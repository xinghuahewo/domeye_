from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = ROOT / ".codex/hooks/country_outage_agent_p2_s1_implementation_alignment.py"
SPEC = importlib.util.spec_from_file_location("p2_s1_implementation_alignment", HOOK_PATH)
assert SPEC is not None and SPEC.loader is not None
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)


class ImplementationAlignmentHookTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = [
            HOOK.TASK_PATH,
            HOOK.TARGET_PATH,
            HOOK.PLAN_PATH,
            HOOK.BASELINE_PATH,
            HOOK.DESIGN_CANDIDATE_PATH,
            HOOK.DESIGN_MANIFEST_PATH,
            HOOK.S1D6_PATH,
            HOOK.FINAL_PATH,
            Path("dev/tests/test_country_outage_p2_s1_implementation_alignment_hook.py"),
        ]
        for relative in self.paths:
            source = ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def load(self, relative: Path) -> dict:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def write(self, relative: Path, value: dict) -> None:
        (self.root / relative).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def refresh_baseline_digest(self, baseline: dict) -> None:
        baseline["content_digest"] = HOOK.object_digest(baseline, {"content_digest"})

    def signed_receipt(self, value: dict) -> dict:
        value = copy.deepcopy(value)
        value["receipt_digest"] = HOOK.object_digest(value, {"receipt_digest"})
        return value

    def create_w0_fixture(self) -> dict:
        p0_target = self.root / HOOK.P0_RECEIPT_PATH
        p0_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / HOOK.P0_RECEIPT_PATH, p0_target)

        contract_source = ROOT / "contracts/data/country-outage-p2-s1"
        contract_target = self.root / "contracts/data/country-outage-p2-s1"
        shutil.copytree(contract_source, contract_target, dirs_exist_ok=True)
        schema_paths = {
            population: f"contracts/data/country-outage-p2-s1/{schema_name}"
            for population, schema_name in HOOK.W0_SOURCE_SCHEMA_BY_POPULATION.items()
        }
        runtime_files = {
            "tools/build_country_outage_p2_s1_source_views.py": "# materializer\n",
            "backend/services/country_outage_p2_s1_source_store.py": "# store\n",
            "backend/web/tests/test_country_outage_p2_s1_source_store.py": "# test\n",
            "dev/tests/test_country_outage_p2_s1_w0_source_governance.py": "# test\n",
            "agent-sidecar/src/chat/p2-s1-registry-runtime.ts": "// registry\n",
            "agent-sidecar/src/chat/p2-s1-trusted-receipt-store.ts": "// store\n",
            "agent-sidecar/tests/p2-s1-w0-source-governance.test.ts": "// test\n",
        }
        for relative, text in runtime_files.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        artifacts = []
        for population, relative in schema_paths.items():
            path = self.root / relative
            artifacts.append({
                "path": relative,
                "role": "source_schema",
                "size_bytes": path.stat().st_size,
                "sha256": HOOK.file_sha256(path),
            })
        fixture_root = contract_target / "test-fixture/source-store"
        for path in sorted(item for item in fixture_root.rglob("*") if item.is_file()):
            relative = path.relative_to(self.root).as_posix()
            artifacts.append({
                "path": relative,
                "role": "source_fixture_manifest" if path.name == "manifest.json" else "source_fixture_data",
                "size_bytes": path.stat().st_size,
                "sha256": HOOK.file_sha256(path),
            })
        roles = {
            "tools/build_country_outage_p2_s1_source_views.py": "source_materializer",
            "backend/services/country_outage_p2_s1_source_store.py": "source_store",
            "backend/web/tests/test_country_outage_p2_s1_source_store.py": "test",
            "dev/tests/test_country_outage_p2_s1_w0_source_governance.py": "test",
            "agent-sidecar/src/chat/p2-s1-registry-runtime.ts": "registry_runtime",
            "agent-sidecar/src/chat/p2-s1-trusted-receipt-store.ts": "trusted_receipt_store",
            "agent-sidecar/tests/p2-s1-w0-source-governance.test.ts": "test",
        }
        governance_contracts = {
            "contracts/agent/country-outage-p2-s1-implementation/w0-registry-proposal.schema.json": "registry_contract",
            "contracts/agent/country-outage-p2-s1-implementation/w0-trusted-receipt.schema.json": "trusted_receipt_contract",
        }
        for relative, role in governance_contracts.items():
            source = ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            roles[relative] = role
        for relative, role in roles.items():
            path = self.root / relative
            artifacts.append({
                "path": relative,
                "role": role,
                "size_bytes": path.stat().st_size,
                "sha256": HOOK.file_sha256(path),
            })

        fixture_manifest_path = self.root / HOOK.W0_SOURCE_FIXTURE_MANIFEST
        fixture_manifest = json.loads(fixture_manifest_path.read_text(encoding="utf-8"))
        fixture_by_population = {
            item["population_id"]: item for item in fixture_manifest["population_manifests"]
        }
        source_receipts = []
        for population in HOOK.W0_SOURCE_POPULATIONS:
            fixture_entry = fixture_by_population[population]
            item = {
                "receipt_kind": "source_population_contract",
                "population_id": population,
                "schema_path": schema_paths[population],
                "schema_sha256": HOOK.file_sha256(self.root / schema_paths[population]),
                "readiness": "fixture_materialization_verified_authoritative_run_pending",
                "atomic_fact_population_count": 1,
                "query_time_replay": False,
                "query_time_path_parsing": False,
                "query_time_business_transform": False,
                "authoritative_source_refs": ["source:fixture:authoritative"],
                "fixture_manifest_path": HOOK.W0_SOURCE_FIXTURE_MANIFEST.as_posix(),
                "fixture_manifest_sha256": HOOK.file_sha256(fixture_manifest_path),
                "fixture_store_id": fixture_manifest["store_id"],
                "fixture_population_readiness": fixture_entry["readiness"],
                "fixture_row_count": fixture_entry["row_count"],
                "fixture_member_keys_digest": fixture_entry["member_keys_digest"],
                "fixture_materialization_receipt_digest": fixture_entry["materialization_receipt_digest"],
            }
            if population == "new_prefix_state_rows":
                item["projection_profile_id"] = "PROFILE-NEW-PREFIX-FIXED-FIRST-OBSERVED-DIRECTIONS-1.0.0"
            if population == "materialized_route_state_rows_at_exact_time":
                item["state_semantics"] = "all_events_with_event_time_strictly_before_state_point"
                item["left_checkpoint_only"] = True
            if population == "window_path_association_evidence_rows":
                item["known_origin_must_equal_collapsed_path_tail"] = True
                item["eligible_anchor_population_complete"] = True
            source_receipts.append(self.signed_receipt(item))
        registry_contract_path = "contracts/agent/country-outage-p2-s1-implementation/w0-registry-proposal.schema.json"
        receipt_contract_path = "contracts/agent/country-outage-p2-s1-implementation/w0-trusted-receipt.schema.json"
        registry_implementation_path = "agent-sidecar/src/chat/p2-s1-registry-runtime.ts"
        store_implementation_path = "agent-sidecar/src/chat/p2-s1-trusted-receipt-store.ts"
        governance_test_path = "agent-sidecar/tests/p2-s1-w0-source-governance.test.ts"
        source_receipts.append(self.signed_receipt({
            "receipt_kind": "registry_governance_contract",
            "new_unit_lifecycle_state": "proposed",
            "active_new_unit_ids": [],
            "inactive_execution_call_count": 0,
            "p2_1_unit_ids": ["PLAN-CAP-02", "TOOL-13", "OP-34"],
            "p2_1_admission": "denied",
            "publication_cardinality": 1,
            "collector_id": "rrc25",
            "contract_path": registry_contract_path,
            "contract_sha256": HOOK.file_sha256(self.root / registry_contract_path),
            "implementation_path": registry_implementation_path,
            "implementation_sha256": HOOK.file_sha256(self.root / registry_implementation_path),
            "attack_test_artifact_path": governance_test_path,
            "attack_test_artifact_sha256": HOOK.file_sha256(self.root / governance_test_path),
            "attack_test_receipt_digest": HOOK.file_sha256(self.root / governance_test_path),
        }))
        source_receipts.append(self.signed_receipt({
            "receipt_kind": "trusted_receipt_store_contract",
            "content_addressed": True,
            "atomic_write_and_recovery": True,
            "caller_forged_receipt_rejected": True,
            "cross_binding_replay_rejected": True,
            "contract_path": receipt_contract_path,
            "contract_sha256": HOOK.file_sha256(self.root / receipt_contract_path),
            "implementation_path": store_implementation_path,
            "implementation_sha256": HOOK.file_sha256(self.root / store_implementation_path),
            "attack_test_artifact_path": governance_test_path,
            "attack_test_artifact_sha256": HOOK.file_sha256(self.root / governance_test_path),
            "attack_test_receipt_digest": HOOK.file_sha256(self.root / governance_test_path),
        }))
        test_receipt = self.signed_receipt({
            "case_id": "W0-PYTHON-AND-TYPESCRIPT",
            "category": "positive_and_attack",
            "runner": "unittest_and_node_test",
            "passed": True,
            "output_digest": "7" * 64,
        })
        measurement = self.signed_receipt({
            "measurement_id": "fixture-source-build",
            "duration_ms": 1.5,
            "row_count": 6,
            "bytes": 512,
            "peak_rss_bytes": 1024,
        })
        baseline = self.load(HOOK.BASELINE_PATH)
        p0 = self.load(HOOK.P0_RECEIPT_PATH)
        evidence = {
            "schema_version": "country_outage_p2_s1_implementation_wave_evidence_v1",
            "stage": "W0",
            "status": "implementation_wave_accepted",
            "design_candidate_id": HOOK.DESIGN_CANDIDATE_ID,
            "baseline_content_digest": baseline["content_digest"],
            "implementation_candidate_id": None,
            "implementation_semantic_digest": None,
            "content_digest": None,
            "effect": HOOK.WAVE_CONTRACT["W0"]["effect"],
            "effect_verified": True,
            "implemented_unit_ids": [],
            "atomic_split_tests_passed": True,
            "p2_1_units_included": [],
            "production_deployed": False,
            "artifact_manifest": artifacts,
            "source_and_governance_receipts": source_receipts,
            "test_receipts": [test_receipt],
            "performance_baseline": {
                "measurement_status": "fixture_baseline_not_w6_acceptance",
                "performance_acceptance_passed": False,
                "measurements": [measurement],
            },
            "prior_stage_receipt_digests": {"S1I-P0": p0["receipt_digest"]},
        }
        evidence["implementation_semantic_digest"] = HOOK.object_digest(
            evidence,
            {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
        )
        evidence["implementation_candidate_id"] = f"country-outage-p2-s1-w0-{evidence['implementation_semantic_digest'][:24]}"
        evidence["content_digest"] = HOOK.object_digest(evidence, {"content_digest"})
        target = self.root / HOOK.WAVE_EVIDENCE_ROOT / "W0.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        self.write(HOOK.WAVE_EVIDENCE_ROOT / "W0.json", evidence)
        return evidence

    def assert_alignment_error(self, code: str, action) -> None:
        with self.assertRaises(HOOK.AlignmentError) as caught:
            action()
        self.assertIn(code, str(caught.exception))

    def test_s1ip0_positive_closes_effect_first_implementation_plan(self) -> None:
        receipt = HOOK.run_alignment(self.root, "S1I-P0")
        self.assertEqual(receipt["status"], "alignment_passed")
        self.assertEqual(receipt["stage"], "S1I-P0")
        self.assertEqual(receipt["design_candidate_id"], HOOK.DESIGN_CANDIDATE_ID)
        self.assertTrue(receipt["implementation_planning"])
        self.assertFalse(receipt["runtime_implemented"])
        self.assertFalse(receipt["production_deployed"])
        self.assertEqual(
            receipt["receipt_digest"],
            HOOK.object_digest(receipt, {"receipt_digest"}),
        )

    def test_rejects_target_without_user_effect_section(self) -> None:
        path = self.root / HOOK.TARGET_PATH
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("### 4.1 用户调查效果", "### 4.1 临时章节", 1), encoding="utf-8")
        self.assert_alignment_error(
            "target_effect_section_missing",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_wave_plan_without_effect_stage(self) -> None:
        path = self.root / HOOK.PLAN_PATH
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("## 十、W5：组合调查运行时、接口和页面", "## 十、临时阶段", 1), encoding="utf-8")
        self.assert_alignment_error(
            "implementation_wave_section_missing",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_baseline_content_tamper(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["effect_contract"]["user_effects"].append("ghost_effect")
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "baseline_digest_mismatch",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_tool_population_expansion_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["implementation_population"]["tool_ids"].append("TOOL-13")
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "tool_population_drift",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_operator_population_merge_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["implementation_population"]["operator_ids"].remove("OP-16")
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "operator_population_drift",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_atomicity_relaxation_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["atomicity_contract"]["one_tool_one_fact_population"] = False
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "atomicity_contract_open",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_p2_1_fanout_smuggling_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["implementation_population"]["plan_capability_ids"].append("PLAN-CAP-02")
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "plan_population_drift",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_runtime_overclaim_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["acceptance_layers"]["runtime_implemented"] = True
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "implementation_status_overclaim",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_production_overclaim_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["acceptance_layers"]["production_deployed"] = True
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "implementation_status_overclaim",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_sol_ds_order_drift_even_when_resigned(self) -> None:
        baseline = self.load(HOOK.BASELINE_PATH)
        baseline["model_flow_contract"]["execution_order"] = [
            "ds_student", "gpt-5.6-sol", "host_grounding_and_validation"
        ]
        self.refresh_baseline_digest(baseline)
        self.write(HOOK.BASELINE_PATH, baseline)
        self.assert_alignment_error(
            "model_order_drift",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_frozen_design_candidate_tamper(self) -> None:
        candidate = self.load(HOOK.DESIGN_CANDIDATE_PATH)
        candidate["runtime_implemented"] = True
        self.write(HOOK.DESIGN_CANDIDATE_PATH, candidate)
        self.assert_alignment_error(
            "design_candidate_stale",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_duplicate_json_key(self) -> None:
        path = self.root / HOOK.BASELINE_PATH
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("{", '{"schema_version":"duplicate",', 1), encoding="utf-8")
        self.assert_alignment_error(
            "重复 JSON key",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_rejects_nan(self) -> None:
        path = self.root / HOOK.BASELINE_PATH
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace('"status": "implementation_planning_draft"', '"non_finite": NaN,\n  "status": "implementation_planning_draft"', 1), encoding="utf-8")
        self.assert_alignment_error(
            "禁止非有限 JSON 数值",
            lambda: HOOK.run_alignment(self.root, "S1I-P0"),
        )

    def test_w0_fails_closed_without_real_wave_evidence(self) -> None:
        self.assert_alignment_error(
            "无法严格读取 JSON",
            lambda: HOOK.run_alignment(self.root, "W0"),
        )

    def test_w0_positive_closes_real_artifact_source_registry_and_store_evidence(self) -> None:
        self.create_w0_fixture()
        receipt = HOOK.run_alignment(self.root, "W0")
        self.assertEqual(receipt["status"], "alignment_passed")
        self.assertIn("w0_six_atomic_source_population_contracts_verified", receipt["checks"])
        self.assertFalse(receipt["runtime_implemented"])
        self.assertFalse(receipt["production_deployed"])

    def test_w0_rejects_arbitrary_nonempty_governance_receipt(self) -> None:
        evidence = self.create_w0_fixture()
        evidence["source_and_governance_receipts"] = [{}]
        evidence["implementation_semantic_digest"] = HOOK.object_digest(
            evidence,
            {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
        )
        evidence["implementation_candidate_id"] = f"country-outage-p2-s1-w0-{evidence['implementation_semantic_digest'][:24]}"
        evidence["content_digest"] = HOOK.object_digest(evidence, {"content_digest"})
        self.write(HOOK.WAVE_EVIDENCE_ROOT / "W0.json", evidence)
        self.assert_alignment_error(
            "w0_source_population_receipt_count_mismatch",
            lambda: HOOK.run_alignment(self.root, "W0"),
        )

    def test_w0_rejects_source_schema_tamper_after_manifest(self) -> None:
        evidence = self.create_w0_fixture()
        path = self.root / evidence["source_and_governance_receipts"][0]["schema_path"]
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        self.assert_alignment_error(
            "w0_artifact_size_mismatch",
            lambda: HOOK.run_alignment(self.root, "W0"),
        )

    def test_w0_rejects_fixture_member_digest_rebinding(self) -> None:
        evidence = self.create_w0_fixture()
        evidence["source_and_governance_receipts"][0]["fixture_member_keys_digest"] = "0" * 64
        evidence["source_and_governance_receipts"][0]["receipt_digest"] = HOOK.object_digest(
            evidence["source_and_governance_receipts"][0], {"receipt_digest"}
        )
        evidence["implementation_semantic_digest"] = HOOK.object_digest(
            evidence,
            {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
        )
        evidence["implementation_candidate_id"] = f"country-outage-p2-s1-w0-{evidence['implementation_semantic_digest'][:24]}"
        evidence["content_digest"] = HOOK.object_digest(evidence, {"content_digest"})
        self.write(HOOK.WAVE_EVIDENCE_ROOT / "W0.json", evidence)
        self.assert_alignment_error(
            "w0_fixture_receipt_binding_mismatch",
            lambda: HOOK.run_alignment(self.root, "W0"),
        )

    def test_w0_rejects_tool_activation_overclaim(self) -> None:
        evidence = self.create_w0_fixture()
        registry = evidence["source_and_governance_receipts"][6]
        registry["new_unit_lifecycle_state"] = "active"
        registry["active_new_unit_ids"] = ["TOOL-07"]
        registry["receipt_digest"] = HOOK.object_digest(registry, {"receipt_digest"})
        evidence["implementation_semantic_digest"] = HOOK.object_digest(
            evidence,
            {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
        )
        evidence["implementation_candidate_id"] = f"country-outage-p2-s1-w0-{evidence['implementation_semantic_digest'][:24]}"
        evidence["content_digest"] = HOOK.object_digest(evidence, {"content_digest"})
        self.write(HOOK.WAVE_EVIDENCE_ROOT / "W0.json", evidence)
        self.assert_alignment_error(
            "w0_unit_activation_overclaim",
            lambda: HOOK.run_alignment(self.root, "W0"),
        )

    def test_w0_rejects_non_recomputable_test_receipt(self) -> None:
        evidence = self.create_w0_fixture()
        evidence["test_receipts"][0]["case_id"] = "TAMPERED"
        evidence["implementation_semantic_digest"] = HOOK.object_digest(
            evidence,
            {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
        )
        evidence["implementation_candidate_id"] = f"country-outage-p2-s1-w0-{evidence['implementation_semantic_digest'][:24]}"
        evidence["content_digest"] = HOOK.object_digest(evidence, {"content_digest"})
        self.write(HOOK.WAVE_EVIDENCE_ROOT / "W0.json", evidence)
        self.assert_alignment_error(
            "wave_test_digest_invalid",
            lambda: HOOK.run_alignment(self.root, "W0"),
        )

    def test_w1_rejects_missing_required_w0_dependency(self) -> None:
        evidence_path = self.root / HOOK.WAVE_EVIDENCE_ROOT / "W1.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence = {
            "schema_version": "country_outage_p2_s1_implementation_wave_evidence_v1",
            "stage": "W1",
            "status": "implementation_wave_accepted",
            "design_candidate_id": HOOK.DESIGN_CANDIDATE_ID,
            "baseline_content_digest": self.load(HOOK.BASELINE_PATH)["content_digest"],
            "implementation_candidate_id": "impl-candidate",
            "effect": HOOK.WAVE_CONTRACT["W1"]["effect"],
            "effect_verified": True,
            "implemented_unit_ids": HOOK.WAVE_CONTRACT["W1"]["unit_ids"],
            "atomic_split_tests_passed": True,
            "p2_1_units_included": [],
            "production_deployed": False,
            "test_receipts": [self.signed_receipt({
                "case_id": "W1-fixture",
                "category": "positive",
                "runner": "unittest",
                "passed": True,
                "output_digest": "a" * 64,
            })],
            "registry_snapshot": {"digest": "b" * 64},
            "prior_stage_receipt_digests": {},
        }
        self.write(HOOK.WAVE_EVIDENCE_ROOT / "W1.json", evidence)
        self.assert_alignment_error(
            "无法严格读取 JSON",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )


if __name__ == "__main__":
    unittest.main()
