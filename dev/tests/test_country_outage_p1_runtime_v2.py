from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPOSITORY_ROOT / "dev" / "tools" / "validate_country_outage_p1_runtime_v2.py"
CONTRACT_ROOT = REPOSITORY_ROOT / "contracts" / "agent" / "country-outage-p1-runtime-v2"


def load_validator():
    specification = importlib.util.spec_from_file_location(
        "validate_country_outage_p1_runtime_v2", VALIDATOR_PATH
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("无法加载 P1 runtime-v2 校验器")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageP1RuntimeV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = load_validator()
        cls.catalog = cls.validator.read_json(CONTRACT_ROOT / "capability-catalog.json")
        cls.tools = cls.validator.read_json(CONTRACT_ROOT / "tool-contracts.json")
        cls.oracle = cls.validator.read_json(CONTRACT_ROOT / "oracle.json")
        cls.schema = cls.validator.read_json(CONTRACT_ROOT / "semantic-plan.schema.json")
        cls.policy = cls.validator.read_json(CONTRACT_ROOT / "policy.json")

    def validate(self, catalog=None, tools=None, oracle=None, schema=None, policy=None):
        return self.validator.validate_s0(
            catalog or self.catalog,
            tools or self.tools,
            oracle or self.oracle,
            schema or self.schema,
            policy or self.policy,
        )

    def test_s0_contract_is_closed_after_authorized_p0_truth_correction(self) -> None:
        self.assertEqual(self.validate(), [])

    def test_selected_capability_cannot_silently_disappear(self) -> None:
        changed = copy.deepcopy(self.catalog)
        changed["selected"].pop()
        errors = self.validate(catalog=changed)
        self.assertTrue(any("selected" in error for error in errors))

    def test_p0_defer_cannot_be_promoted(self) -> None:
        changed = copy.deepcopy(self.catalog)
        promoted = changed["deferred"].pop(0)
        promoted.update({
            "user_outcome": "非法提升",
            "execution_unit": "TOOL-01",
            "goal_kinds": ["trend_analysis"],
            "answer_modes": ["supported"],
            "required_event_capabilities": ["general_read_model"],
            "evidence_sources": ["resolution"],
        })
        changed["selected"].append(promoted)
        errors = self.validate(catalog=changed)
        self.assertTrue(any("selected" in error or "P0 v1.3" in error for error in errors))

    def test_tool_contract_requires_null_and_forbidden_semantics(self) -> None:
        changed = copy.deepcopy(self.tools)
        changed["execution_units"][0].pop("null_semantics")
        changed["execution_units"][0]["forbidden_uses"] = []
        errors = self.validate(tools=changed)
        self.assertTrue(any("null_semantics" in error for error in errors))
        self.assertTrue(any("forbidden_uses" in error for error in errors))

    def test_oracle_requires_all_six_categories(self) -> None:
        changed = copy.deepcopy(self.oracle)
        changed["capability_coverage"][0]["cases"].pop("wrong_identity")
        errors = self.validate(oracle=changed)
        self.assertTrue(any("六类 Oracle" in error for error in errors))

    def test_oracle_case_ids_must_be_unique(self) -> None:
        changed = copy.deepcopy(self.oracle)
        duplicate = changed["capability_coverage"][0]["cases"]["normal"]["case_id"]
        changed["capability_coverage"][1]["cases"]["normal"]["case_id"] = duplicate
        errors = self.validate(oracle=changed)
        self.assertTrue(any("102 个唯一" in error for error in errors))

    def test_oracle_adapter_must_point_to_real_fixture_path(self) -> None:
        changed = copy.deepcopy(self.oracle)
        changed["capability_coverage"][0]["cases"]["missing"]["adapter_args"]["path"] = "missing.path"
        errors = self.validate(oracle=changed)
        self.assertTrue(any("adapter 不可执行" in error for error in errors))

    def test_grounding_adapter_must_cover_every_selected_capability(self) -> None:
        changed = copy.deepcopy(self.oracle)
        changed["grounding_adapter_registry"].pop("CAP-005")
        errors = self.validate(oracle=changed)
        self.assertTrue(any("capability_input" in error for error in errors))

    def test_raw_series_capability_cannot_claim_extrema(self) -> None:
        changed = copy.deepcopy(self.oracle)
        normal = changed["capability_coverage"][5]["cases"]["normal"]
        normal["expected"]["facts"]["maximum"] = 10156800
        errors = self.validate(oracle=changed)
        self.assertTrue(any("原始时序" in error for error in errors))

    def test_operator_metric_unit_pairs_are_closed(self) -> None:
        changed = copy.deepcopy(self.tools)
        changed["$defs"]["registeredMetricUnitPair"]["oneOf"].pop()
        errors = self.validate(tools=changed)
        self.assertTrue(any("metric 与 unit" in error for error in errors))

    def test_open_goal_text_cannot_become_tool_enum(self) -> None:
        changed = copy.deepcopy(self.schema)
        changed["$defs"]["goal"]["properties"]["requested_goal"]["enum"] = ["event_summary"]
        errors = self.validate(schema=changed)
        self.assertTrue(any("requested_goal" in error for error in errors))

    def test_grounding_execution_unit_is_closed(self) -> None:
        changed = copy.deepcopy(self.schema)
        changed["$defs"]["node"]["oneOf"].pop()
        errors = self.validate(schema=changed)
        self.assertTrue(any("执行单元" in error for error in errors))

    def test_grounding_identity_requires_finality(self) -> None:
        changed = copy.deepcopy(self.schema)
        changed["$defs"]["identity"]["required"].remove("is_final_in_data_range")
        errors = self.validate(schema=changed)
        self.assertTrue(any("finality" in error for error in errors))

    def test_parameter_schema_is_a_host_hard_gate(self) -> None:
        changed = copy.deepcopy(self.policy)
        changed["grounding_validator"]["required_invariants"].remove("GND-12")
        changed["grounding_validator"]["validation_order"].remove("parameter_schema_valid")
        errors = self.validate(policy=changed)
        self.assertTrue(any("宿主不变量" in error or "parameter_schema_valid" in error for error in errors))

    def test_cap006_cannot_ground_ipv6_metric(self) -> None:
        changed = copy.deepcopy(self.schema)
        changed["$defs"]["seriesInputsCap006"]["allOf"][1]["properties"]["metrics"]["const"] = [
            "fixed_visible_ipv6_slash48_count"
        ]
        errors = self.validate(schema=changed)
        self.assertTrue(any("CAP-006/CAP-007" in error for error in errors))

    def test_cap011_requires_explicit_asn(self) -> None:
        changed = copy.deepcopy(self.schema)
        changed["$defs"]["asnDetailInputs"]["allOf"][1]["required"] = []
        errors = self.validate(schema=changed)
        self.assertTrue(any("CAP-011" in error for error in errors))

    def test_supported_and_partial_capability_conditions_are_distinct(self) -> None:
        changed = copy.deepcopy(self.catalog)
        cap018 = next(item for item in changed["selected"] if item["capability_id"] == "CAP-018")
        cap018["sufficient_for_partial"] = ["overview=available", "event_series=available"]
        errors = self.validate(catalog=changed)
        self.assertTrue(any("CAP-018 partial" in error for error in errors))

    def test_operator_oracle_executes_and_compares_exact_result(self) -> None:
        changed = copy.deepcopy(self.oracle)
        cap016 = next(item for item in changed["capability_coverage"] if item["capability_id"] == "CAP-016")
        cap016["cases"]["normal"]["expected"]["facts"]["maximum"] = 95
        errors = self.validate(oracle=changed)
        self.assertTrue(any("算子实际结果与 expected 不一致" in error for error in errors))

    def test_verified_fact_kind_unit_contract_cannot_be_opened(self) -> None:
        changed = copy.deepcopy(self.tools)
        changed["$defs"]["verifiedFact"]["oneOf"].pop()
        errors = self.validate(tools=changed)
        self.assertTrue(any("verifiedFact" in error for error in errors))

    def test_unresolvable_timeline_evidence_is_rejected(self) -> None:
        fixtures = self.validator.read_json(
            REPOSITORY_ROOT / "evaluation" / "country-outage" / "p1-runtime-v2" / "oracle-fixtures.json"
        )
        fixture = copy.deepcopy(fixtures["fixtures"]["FX-OP03-TIMELINE"])
        fixture["execution_request"]["peak_nodes"][0]["evidence_ref"] = "model.guessed.value"
        result = self.validator.execute_operator_oracle("CAP-018", fixture)
        self.assertEqual(result, {"status": "invalid_data", "error": "evidence_reference_not_resolved"})

    def test_resolvable_reference_with_fabricated_value_is_rejected(self) -> None:
        fixtures = self.validator.read_json(
            REPOSITORY_ROOT / "evaluation" / "country-outage" / "p1-runtime-v2" / "oracle-fixtures.json"
        )
        fixture = copy.deepcopy(fixtures["fixtures"]["FX-OP03-TIMELINE"])
        fixture["execution_request"]["peak_nodes"][0]["value"] = 999999
        result = self.validator.execute_operator_oracle("CAP-018", fixture)
        self.assertEqual(result, {"status": "invalid_data", "error": "evidence_value_conflict"})

    def test_resolvable_reference_with_fabricated_timestamp_is_rejected(self) -> None:
        fixtures = self.validator.read_json(
            REPOSITORY_ROOT / "evaluation" / "country-outage" / "p1-runtime-v2" / "oracle-fixtures.json"
        )
        fixture = copy.deepcopy(fixtures["fixtures"]["FX-OP03-TIMELINE"])
        fixture["execution_request"]["identity_nodes"][0]["at_utc"] = "2099-01-01T00:00:00Z"
        fixture["execution_request"]["identity_nodes"][0]["value"] = "2099-01-01T00:00:00Z"
        result = self.validator.execute_operator_oracle("CAP-018", fixture)
        self.assertEqual(result, {"status": "invalid_data", "error": "evidence_value_conflict"})

    def test_live_series_micro_sample_is_not_self_consistent_only(self) -> None:
        fixtures = self.validator.read_json(
            REPOSITORY_ROOT / "evaluation" / "country-outage" / "p1-runtime-v2" / "oracle-fixtures.json"
        )
        request = fixtures["fixtures"]["FX-OP01-ROUTE"]["execution_request"]
        self.assertEqual(request["timestamps"][1:3], ["2026-02-28T13:45:00Z", "2026-02-28T13:50:00Z"])
        self.assertEqual(request["values"][1:3], [92, 94])
        self.assertEqual(request["timestamps"][0], "2026-02-27T00:10:00Z")
        self.assertEqual(request["values"][0], 0)

    def test_model_generated_facts_must_remain_forbidden(self) -> None:
        changed = copy.deepcopy(self.policy)
        changed["fact_publication"]["model_generated_values"] = "allowed"
        errors = self.validate(policy=changed)
        self.assertTrue(any("模型生成事实" in error for error in errors))

    def test_identity_conflict_must_rollback_entire_turn(self) -> None:
        changed = copy.deepcopy(self.policy)
        changed["state_transaction"]["shared_identity_conflict"] = "partial_commit"
        errors = self.validate(policy=changed)
        self.assertTrue(any("身份冲突" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
