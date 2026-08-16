from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = (
    ROOT
    / "contracts/agent/country-outage-p2-s1-implementation/tools/run_w6_certification.py"
)
SPEC = importlib.util.spec_from_file_location("country_outage_p2_s1_w6_certifier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
w6 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w6)


class CountryOutageP2S1W6CertificationTest(unittest.TestCase):
    def source_inputs(self):
        evidence, _ = w6.load_w5_bindings()
        oracle = w6.load_json(w6.ORACLE_PATH)
        seed = w6.load_json(w6.SEED_PATH)
        capability_map = w6.load_json(w6.MAP_PATH)
        return evidence, oracle, seed, capability_map

    def test_frozen_question_and_scenario_population_is_exact_28_by_6(self):
        evidence, oracle, seed, capability_map = self.source_inputs()
        receipts, matrix = w6.build_case_receipts(
            evidence, oracle, seed, capability_map
        )
        self.assertEqual(len(receipts), 168)
        self.assertEqual(len(matrix["cases"]), 168)
        self.assertEqual(
            [item["case_id"] for item in receipts],
            [
                f"{question_id}-{scenario_code}"
                for question_id in w6.QUESTION_IDS
                for scenario_code, _ in w6.SCENARIOS
            ],
        )

    def test_current_candidate_classifies_162_blocked_and_6_deferred_without_answer_claim(self):
        evidence, oracle, seed, capability_map = self.source_inputs()
        receipts, matrix = w6.build_case_receipts(
            evidence, oracle, seed, capability_map
        )
        w6.verify_case_population(receipts, matrix)
        self.assertEqual(
            sum(item["actual_disposition"] == "correctly_blocked" for item in receipts),
            162,
        )
        self.assertEqual(
            sum(item["actual_disposition"] == "correctly_deferred" for item in receipts),
            6,
        )
        self.assertTrue(all(not any(item["overclaim_checks"].values()) for item in receipts))
        self.assertTrue(all(item["runtime_proof"] is None for item in receipts))

    def test_case_schema_and_all_self_digests_are_recomputable(self):
        evidence, oracle, seed, capability_map = self.source_inputs()
        receipts, _ = w6.build_case_receipts(evidence, oracle, seed, capability_map)
        schema = w6.load_json(w6.CASE_SCHEMA_PATH)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for item in receipts:
            self.assertEqual(list(validator.iter_errors(item)), [])
            self.assertEqual(item["content_digest"], w6.self_digest(item))

    def test_jsonl_index_is_canonical_ordered_and_content_addressed(self):
        evidence, oracle, seed, capability_map = self.source_inputs()
        receipts, _ = w6.build_case_receipts(evidence, oracle, seed, capability_map)
        index, payload = w6.build_case_index(receipts)
        lines = payload.splitlines()
        self.assertEqual(len(lines), 168)
        self.assertTrue(payload.endswith(b"\n"))
        for line, item, entry in zip(lines, receipts, index["entries"], strict=True):
            self.assertEqual(line, w6.canonical_json(item))
            self.assertEqual(entry["line_sha256"], "sha256:" + w6.sha256_bytes(line))
            self.assertEqual(entry["case_receipt_digest"], item["content_digest"])

    def test_strict_json_rejects_duplicate_nonfinite_and_bom(self):
        for payload in (
            b'{"a":1,"a":2}',
            b'{"a":NaN}',
            b'{"a":Infinity}',
            b'\xef\xbb\xbf{"a":1}',
        ):
            with self.assertRaises(w6.CertificationError):
                w6.strict_json_bytes(payload, "attack")

    def test_overclaim_population_order_and_cross_candidate_attacks_fail_closed(self):
        evidence, oracle, seed, capability_map = self.source_inputs()
        receipts, _ = w6.build_case_receipts(evidence, oracle, seed, capability_map)
        results = w6.run_attack_probes(receipts)
        self.assertEqual(len(results), 24)
        self.assertTrue(all(set(item) == {
            "attack_id", "rejected", "verifier_entrypoint", "rejection_code",
            "attack_input_digest",
        } for item in results))
        self.assertTrue(all(item["rejected"] is True for item in results))
        self.assertTrue(all(item["rejection_code"] for item in results))

        forged = copy.deepcopy(receipts)
        forged[0]["actual_disposition"] = "executed_supported"
        forged[0]["content_digest"] = w6.self_digest(forged[0])
        with self.assertRaises(w6.CertificationError):
            w6.verify_case_population(forged)

    def test_machine_blocker_facts_bind_registry_planner_source_and_p21(self):
        evidence, oracle, seed, capability_map = self.source_inputs()
        receipts, matrix = w6.build_case_receipts(
            evidence, oracle, seed, capability_map
        )
        facts = matrix["machine_facts"]
        self.assertNotIn("TOOL-01", facts["w5_registry_admitted_unit_ids"])
        self.assertEqual(
            facts["formal_planning_allowlist_unit_ids"],
            sorted(["GATE-01", "GATE-02", "GATE-03", "TOOL-07", "TOOL-11", "OP-29", "OP-37", "BOUNDARY-01"]),
        )
        self.assertEqual(facts["source_population_facts"]["distinct_asn_count"], 1)
        self.assertEqual(facts["source_population_facts"]["row_counts"]["window_path_association_evidence_rows"], 1)
        denial = facts["p2_1_denial_probe"]
        self.assertEqual(
            [item["unit_id"] for item in denial["unit_results"]],
            ["PLAN-CAP-02", "TOOL-13", "OP-34"],
        )
        self.assertEqual(denial["handler_invocation_count"], 0)
        self.assertTrue(all(item["denial_code"] == "p2_1_unit_forbidden" for item in denial["unit_results"]))
        q24 = [item for item in receipts if item["question_id"] == "Q24"]
        self.assertEqual(len(q24), 6)
        self.assertTrue(all(item["deferred_proof"]["dispatch_count"] == 0 for item in q24))

    def test_actual_classifier_is_independent_from_frozen_expected_table(self):
        evidence, oracle, seed, _ = self.source_inputs()
        facts = w6.collect_machine_facts(evidence)
        oracle_questions = w6.question_map(oracle, "oracle")
        seed_questions = w6.question_map(seed, "seed")
        before = w6.classify_candidate(oracle_questions["Q03"], seed_questions["Q03"], facts)
        original = w6.EXPECTED_REASONS["Q03"]
        try:
            w6.EXPECTED_REASONS["Q03"] = ("forged_expected_reason",)
            after = w6.classify_candidate(oracle_questions["Q03"], seed_questions["Q03"], facts)
        finally:
            w6.EXPECTED_REASONS["Q03"] = original
        self.assertEqual(before, after)
        self.assertEqual(before, (
            "correctly_blocked",
            ["legacy_p1_units_not_in_w5_dispatcher", "legacy_fact_to_op29_adapter_missing"],
        ))

    def test_oracle_and_machine_fact_splice_are_rejected_after_full_resign(self):
        evidence, oracle, seed, capability_map = self.source_inputs()
        receipts, matrix = w6.build_case_receipts(evidence, oracle, seed, capability_map)
        oracle_splice = copy.deepcopy(receipts)
        oracle_splice[0]["oracle_binding"]["oracle_digest"] = "sha256:" + "0" * 64
        oracle_splice[0]["content_digest"] = w6.self_digest(oracle_splice[0])
        with self.assertRaisesRegex(w6.CertificationError, "case_oracle_binding_mismatch"):
            w6.verify_case_population(oracle_splice, matrix)

        fact_splice = copy.deepcopy(receipts)
        fact_splice[0]["machine_facts_digest"] = "sha256:" + "1" * 64
        fact_splice[0]["content_digest"] = w6.self_digest(fact_splice[0])
        with self.assertRaisesRegex(w6.CertificationError, "case_machine_facts_binding_mismatch"):
            w6.verify_case_population(fact_splice, matrix)

    def test_review_schema_can_express_revision_and_only_clean_reviews_can_accept(self):
        schema = w6.load_json(w6.REVIEW_SCHEMA_PATH)
        Draft202012Validator.check_schema(schema)
        base = {
            "schema_version": "country_outage_p2_s1_w6_independent_review_v2",
            "review_id": "review-test",
            "reviewer_identity": "independent-test-reviewer",
            "reviewer_role": "product_semantic",
            "read_only": True,
            "implemented_by_reviewer": False,
            "implementation_candidate_id": w6.W5_CANDIDATE_ID,
            "implementation_candidate_digest": w6.W5_CANDIDATE_DIGEST,
            "review_input_digest": "sha256:" + "1" * 64,
            "question_disposition_contract_digest": "sha256:" + "2" * 64,
            "case_matrix_digest": "sha256:" + "3" * 64,
            "certifier_manifest_digest": "sha256:" + "4" * 64,
            "case_index_digest": "sha256:" + "5" * 64,
            "runtime_proof_digest": "sha256:" + "6" * 64,
            "performance_digest": "sha256:" + "7" * 64,
            "attack_digest": "sha256:" + "8" * 64,
            "findings": ["只读发现"],
            "blocking_findings": ["存在阻断"],
            "overclaim_findings": [],
            "review_disposition": "revision_required",
            "full_question_answer_certification_passed": False,
            "actual_provider_model_alignment": False,
            "actual_provider_performance_acceptance": False,
            "actual_provider_cost_acceptance": False,
            "runtime_promotion": False,
            "production_deployed": False,
            "content_digest": "sha256:" + "9" * 64,
        }
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(base)), [])
        forged_acceptance = copy.deepcopy(base)
        forged_acceptance["review_disposition"] = "accepted_for_pre_provider_release_preparation_with_explicit_question_blocks"
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(forged_acceptance)))

    def test_review_input_digest_binds_contract_matrix_and_certifier(self):
        values = [
            {"content_digest": f"sha256:{number:064x}"}
            for number in range(1, 10)
        ]
        baseline = w6.review_input_digest(*values)
        for index in (2, 3, 4):
            mutated = copy.deepcopy(values)
            mutated[index]["content_digest"] = "sha256:" + "f" * 64
            self.assertNotEqual(w6.review_input_digest(*mutated), baseline)

    def test_same_candidate_runtime_proof_uses_actual_w5_14_event_trace(self):
        evidence, stage = w6.load_w5_bindings()
        candidate = w6.build_candidate(evidence, stage)
        _, oracle, seed, capability_map = self.source_inputs()
        receipts, _ = w6.build_case_receipts(
            evidence, oracle, seed, capability_map
        )
        case_index, _ = w6.build_case_index(receipts)
        proof = w6.build_runtime_proof(candidate, case_index)
        trace = proof["actual_execution_trace"]
        self.assertEqual(trace["event_count"], 14)
        self.assertTrue(trace["result_set_committed"])
        self.assertTrue(trace["evidence_graph_committed"])
        self.assertEqual(trace["dynamic_fanout_count"], 0)
        self.assertEqual(trace["arbitrary_callback_count"], 0)
        self.assertFalse(proof["question_answer_certification"])
        self.assertFalse(proof["external_provider_called"])

    def test_acceptance_schema_forbids_provider_promotion_and_production_claims(self):
        schema = w6.load_json(w6.MANIFEST_SCHEMA_PATH)
        Draft202012Validator.check_schema(schema)
        properties = schema["properties"]
        for key in (
            "full_question_answer_certification_passed",
            "actual_provider_model_alignment",
            "actual_provider_performance_acceptance",
            "actual_provider_cost_acceptance",
            "runtime_promotion",
            "production_deployed",
        ):
            self.assertEqual(properties[key]["const"], False)
        self.assertEqual(properties["external_provider_call_count"]["const"], 0)


if __name__ == "__main__":
    unittest.main()
