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
        self.stage_test_pins = copy.deepcopy(HOOK.STAGE_TEST_RUN_RECEIPTS)
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
        for relative in [
            HOOK.STAGE_TEST_RUNNER_PATH,
            *[
                HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
                for suite_id in HOOK.STAGE_TEST_RUN_RECEIPTS
            ],
            *[
                HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json"
                for stage in HOOK.REGISTRY_WAVE_SEQUENCE
            ],
        ]:
            source = ROOT / relative
            if not source.is_file():
                continue
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def tearDown(self) -> None:
        HOOK.STAGE_TEST_RUN_RECEIPTS.clear()
        HOOK.STAGE_TEST_RUN_RECEIPTS.update(self.stage_test_pins)
        self.temporary.cleanup()

    def load(self, relative: Path) -> dict:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def load_from_root(self, relative: Path) -> dict:
        return json.loads((ROOT / relative).read_text(encoding="utf-8"))

    def write(self, relative: Path, value: dict) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
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
        runtime_files = [
            "tools/build_country_outage_p2_s1_source_views.py",
            "backend/services/country_outage_p2_s1_source_store.py",
            "backend/web/tests/test_country_outage_p2_s1_source_store.py",
            "dev/tests/test_country_outage_p2_s1_w0_source_governance.py",
            "agent-sidecar/src/chat/p2-s1-registry-runtime.ts",
            "agent-sidecar/src/chat/p2-s1-trusted-receipt-store.ts",
            "agent-sidecar/tests/p2-s1-w0-source-governance.test.ts",
        ]
        for relative in runtime_files:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
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
        test_receipts = []
        for suite_id in ("w0-python", "w0-typescript"):
            stage, category, sha256 = HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id]
            receipt_path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
            receipt = self.load(receipt_path)
            test_receipts.append({
                "suite_id": suite_id,
                "category": category,
                "path": receipt_path.as_posix(),
                "sha256": sha256,
                "receipt_digest": receipt["receipt_digest"],
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
            "test_receipts": test_receipts,
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

    def artifact_ref(self, relative: Path | str, role: str) -> dict:
        relative = Path(relative)
        path = self.root / relative
        return {
            "path": relative.as_posix(),
            "role": role,
            "size_bytes": path.stat().st_size,
            "sha256": HOOK.file_sha256(path),
        }

    def create_w1_w2_fixture(self, stage: str) -> dict:
        self.assertIn(stage, set(HOOK.REGISTRY_WAVE_SEQUENCE))
        wave_index = HOOK.REGISTRY_WAVE_SEQUENCE.index(stage)
        if wave_index > 0:
            previous_stage = HOOK.REGISTRY_WAVE_SEQUENCE[wave_index - 1]
            self.create_w1_w2_fixture(previous_stage)
            previous_receipt = HOOK.run_alignment(self.root, previous_stage)
            self.write(HOOK.WAVE_RECEIPT_ROOT / f"{previous_stage}.json", previous_receipt)
        for receipt_path in (HOOK.P0_RECEIPT_PATH, HOOK.WAVE_RECEIPT_ROOT / "W0.json"):
            target = self.root / receipt_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / receipt_path, target)
        w0_evidence_target = self.root / HOOK.WAVE_EVIDENCE_ROOT / "W0.json"
        w0_evidence_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / HOOK.WAVE_EVIDENCE_ROOT / "W0.json", w0_evidence_target)
        for relative, role in HOOK.atomic_wave_artifact_roles(stage).items():
            source = ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_file():
                shutil.copy2(source, target)
            else:
                target.write_text(f"# fixture {role}\n", encoding="utf-8")
        artifacts = [
            self.artifact_ref(relative, role)
            for relative, role in HOOK.atomic_wave_artifact_roles(stage).items()
        ]
        structural = self.artifact_ref(
            HOOK.STRUCTURAL_BINDING_PATH, "structural_binding_contract"
        )
        frozen_roles = {
            HOOK.TOOL_CATALOG_PATH: "frozen_tool_catalog",
            HOOK.TOOL_CONTRACT_SCHEMA_PATH: "frozen_tool_contract_schema",
            HOOK.OPERATOR_CATALOG_PATH: "frozen_operator_catalog",
            HOOK.OPERATOR_CONTRACT_SCHEMA_PATH: "frozen_operator_contract_schema",
        }
        frozen = [self.artifact_ref(path, role) for path, role in frozen_roles.items()]
        source_manifest = self.load(HOOK.W0_SOURCE_FIXTURE_MANIFEST)
        tool_map, operator_map = HOOK._catalog_units(self.root)
        wave_units = list(HOOK.WAVE_CONTRACT[stage]["unit_ids"])
        populations = [tool_map[unit_id]["source_population"] for unit_id in wave_units if unit_id in tool_map]
        population_schemas = []
        for population in populations:
            item = self.artifact_ref(HOOK.W1_W2_SOURCE_SCHEMA_PATHS[population], "w0_source_schema")
            item = {"population_id": population, **item}
            population_schemas.append(item)
        source_binding = {
            "w0_receipt_digest": self.load(HOOK.WAVE_RECEIPT_ROOT / "W0.json")["receipt_digest"],
            "store_id": source_manifest["store_id"],
            "manifest": self.artifact_ref(HOOK.W0_SOURCE_FIXTURE_MANIFEST, "w0_source_manifest"),
            "source_store": self.artifact_ref(HOOK.W1_W2_SOURCE_STORE_PATH, "w0_source_store"),
            "population_schemas": population_schemas,
        }
        atomic_receipts = []
        for unit_id in wave_units:
            unit_kind = "tool" if unit_id in tool_map else "operator"
            catalog_entry = tool_map.get(unit_id) or operator_map[unit_id]
            implementation_path = (
                HOOK.W1_W2_TOOL_IMPLEMENTATION_PATH
                if unit_kind == "tool"
                else HOOK.W1_W2_OPERATOR_IMPLEMENTATION_PATH
            )
            input_ref, output_ref = HOOK._w1_w2_schema_refs(unit_id, tool_map, operator_map)
            receipt = {
                "schema_version": "country_outage_p2_s1_atomic_unit_receipt_v1",
                "receipt_kind": "atomic_unit_implementation",
                "stage": stage,
                "design_candidate_id": HOOK.DESIGN_CANDIDATE_ID,
                "unit_id": unit_id,
                "unit_kind": unit_kind,
                "catalog_entry_digest": HOOK.sha256_bytes(HOOK.canonical_json(catalog_entry)),
                "implementation_path": implementation_path.as_posix(),
                "implementation_sha256": HOOK.file_sha256(self.root / implementation_path),
                "input_schema_ref": input_ref,
                "output_schema_ref": output_ref,
                "registered_atomic_operation_count": 1,
                "business_transform_count": 0 if unit_kind == "tool" else 1,
                "fact_population_read_count": 1 if unit_kind == "tool" else 0,
                "internal_unit_calls": 0,
                "model_call_count": 0,
                "external_read_count": 0,
                "p2_1_unit_ids": [],
            }
            if unit_kind == "tool":
                receipt["source_population_id"] = catalog_entry["source_population"]
            atomic_receipts.append(self.signed_receipt(receipt))
        test_receipts = []
        for category in ("positive", "boundary", "attack"):
            suite_id = f"{stage.lower()}-{category}"
            _, expected_category, sha256 = HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id]
            receipt_path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
            receipt = self.load(receipt_path)
            test_receipts.append({
                "suite_id": suite_id,
                "category": expected_category,
                "path": receipt_path.as_posix(),
                "sha256": sha256,
                "receipt_digest": receipt["receipt_digest"],
            })
        binding = {
            "binding_manifest_id": None,
            "binding_manifest_digest": None,
            "wave_binding_unit_ids": list(wave_units),
            "binding_kind": "immutable_non_callable_dispatch_binding",
            "artifact_path": HOOK.W1_W2_REGISTRY_RUNTIME_PATH.as_posix(),
            "artifact_sha256": HOOK.file_sha256(self.root / HOOK.W1_W2_REGISTRY_RUNTIME_PATH),
            "structural_binding_contract_sha256": HOOK.file_sha256(self.root / HOOK.STRUCTURAL_BINDING_PATH),
            "callable_handler_refs": [],
            "caller_callback_refs": [],
            "trusted_dispatcher_id": None,
        }
        binding["binding_manifest_digest"] = HOOK.object_digest(
            binding, {"binding_manifest_id", "binding_manifest_digest"}
        )
        binding["binding_manifest_id"] = (
            f"p2-s1-dispatch-binding-manifest-sha256:{binding['binding_manifest_digest']}"
        )
        admitted_binding_units = HOOK.cumulative_registry_units(stage)
        previous_snapshot = (
            None
            if stage == "W1"
            else self.load(
                HOOK.WAVE_EVIDENCE_ROOT
                / f"{HOOK.REGISTRY_WAVE_SEQUENCE[wave_index - 1]}.json"
            )["registry_binding_projection"]["snapshot"]
        )
        snapshot = {
            "schema_version": "country_outage_p2_s1_registry_wave_snapshot_v1",
            "snapshot_id": None,
            "snapshot_digest": None,
            "registry_revision": 4 + wave_index,
            "wave_id": stage,
            "proposal_snapshot_id": f"p2-s1-registry-proposal-sha256:{'d' * 64}",
            "proposal_snapshot_digest": "d" * 64,
            "proposal_registry_revision": 3,
            "previous_snapshot_id": (
                f"p2-s1-registry-proposal-sha256:{'d' * 64}"
                if stage == "W1"
                else previous_snapshot["snapshot_id"]
            ),
            "previous_snapshot_digest": "d" * 64 if stage == "W1" else previous_snapshot["snapshot_digest"],
            "previous_registry_revision": 3 if stage == "W1" else previous_snapshot["registry_revision"],
            "binding_manifest_id": binding["binding_manifest_id"],
            "binding_manifest_digest": binding["binding_manifest_digest"],
            "structural_binding_contract_sha256": HOOK.file_sha256(self.root / HOOK.STRUCTURAL_BINDING_PATH),
            "admitted_wave_binding_unit_ids": list(wave_units),
            "admitted_binding_unit_ids": admitted_binding_units,
            "binding_admission_only": True,
            "artifact_path": HOOK.W1_W2_REGISTRY_RUNTIME_PATH.as_posix(),
            "artifact_sha256": HOOK.file_sha256(self.root / HOOK.W1_W2_REGISTRY_RUNTIME_PATH),
            "production_deployed": False,
        }
        snapshot["snapshot_digest"] = HOOK.object_digest(snapshot, {"snapshot_id", "snapshot_digest"})
        snapshot["snapshot_id"] = f"p2-s1-registry-wave-sha256:{snapshot['snapshot_digest']}"
        admission = self.signed_receipt({
            "schema_version": "country_outage_p2_s1_registry_wave_admission_v1",
            "status": "admitted_complete_atomic_wave_bindings",
            "wave_id": stage,
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_digest": snapshot["snapshot_digest"],
            "registry_revision": snapshot["registry_revision"],
            "previous_snapshot_id": snapshot["previous_snapshot_id"],
            "previous_snapshot_digest": snapshot["previous_snapshot_digest"],
            "previous_registry_revision": snapshot["previous_registry_revision"],
            "binding_manifest_id": binding["binding_manifest_id"],
            "binding_manifest_digest": binding["binding_manifest_digest"],
            "structural_binding_contract_sha256": HOOK.file_sha256(self.root / HOOK.STRUCTURAL_BINDING_PATH),
            "registry_artifact_path": HOOK.W1_W2_REGISTRY_RUNTIME_PATH.as_posix(),
            "registry_artifact_sha256": HOOK.file_sha256(self.root / HOOK.W1_W2_REGISTRY_RUNTIME_PATH),
            "admitted_wave_binding_unit_ids": list(wave_units),
            "admitted_binding_unit_ids": admitted_binding_units,
            "execution_allowed_unit_ids": [],
            "partial_binding_admission": False,
            "trusted_dispatcher_bound": False,
            "execution_started": False,
            "production_deployed": False,
        })
        execution_probe = self.signed_receipt({
            "schema_version": "country_outage_p2_s1_registry_non_execution_probe_v1",
            "wave_id": stage,
            "tested_unit_ids": list(wave_units),
            "test_artifact_path": HOOK.W1_W2_REGISTRY_TEST_PATH.as_posix(),
            "test_artifact_sha256": HOOK.file_sha256(self.root / HOOK.W1_W2_REGISTRY_TEST_PATH),
            "caller_callback_injection_supported": False,
            "caller_callback_spy_count": 0,
            "execution_allowed_unit_ids": [],
            "assert_execution_authorized_error": "registry_dispatch_not_bound",
            "trusted_dispatcher_bound": False,
        })
        p0 = self.load(HOOK.P0_RECEIPT_PATH)
        w0 = self.load(HOOK.WAVE_RECEIPT_ROOT / "W0.json")
        evidence = {
            "schema_version": "country_outage_p2_s1_implementation_wave_evidence_v1",
            "stage": stage,
            "status": "offline_atomic_harness_accepted_for_w5_integration",
            "design_candidate_id": HOOK.DESIGN_CANDIDATE_ID,
            "baseline_content_digest": self.load(HOOK.BASELINE_PATH)["content_digest"],
            "implementation_candidate_id": None,
            "implementation_semantic_digest": None,
            "content_digest": None,
            "effect": HOOK.WAVE_CONTRACT[stage]["effect"],
            "effect_verified": True,
            "implemented_unit_ids": list(wave_units),
            "atomic_split_tests_passed": True,
            "p2_1_units_included": [],
            "production_deployed": False,
            "artifact_manifest": artifacts,
            "structural_binding_contract": structural,
            "frozen_contracts": frozen,
            "w0_source_binding": source_binding,
            "atomic_unit_receipts": atomic_receipts,
            "test_receipts": test_receipts,
            "registry_runtime_evidence": {
                "path": (HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json").as_posix(),
                "sha256": HOOK.W1_W2_REGISTRY_EVIDENCE_SHA256[stage],
                "content_digest": self.load(
                    HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json"
                )["content_digest"],
                "generator_path": HOOK.W1_W2_REGISTRY_EVIDENCE_GENERATOR_PATH.as_posix(),
                "generator_sha256": HOOK.file_sha256(
                    self.root / HOOK.W1_W2_REGISTRY_EVIDENCE_GENERATOR_PATH
                ),
            },
            "registry_binding_projection": {
                "source_runtime_bundle": {
                    "path": (HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json").as_posix(),
                    "sha256": HOOK.file_sha256(
                        self.root / HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json"
                    ),
                    "content_digest": self.load(
                        HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json"
                    )["content_digest"],
                    "handler_manifest_id": self.load(
                        HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json"
                    )["handler_manifest"]["handler_manifest_id"],
                    "handler_manifest_digest": self.load(
                        HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json"
                    )["handler_manifest"]["handler_manifest_digest"],
                    "snapshot_id": self.load(
                        HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json"
                    )["wave_snapshot"]["registry_snapshot_id"],
                    "snapshot_digest": self.load(
                        HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json"
                    )["wave_snapshot"]["snapshot_digest"],
                    "admission_receipt_digest": self.load(
                        HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json"
                    )["wave_admission_receipt"]["receipt_digest"],
                },
                "artifact": self.artifact_ref(HOOK.W1_W2_REGISTRY_RUNTIME_PATH, "registry_runtime"),
                "binding_manifest": binding,
                "snapshot": snapshot,
                "admission_receipt": admission,
                "execution_probe": execution_probe,
                "execution_scope": {
                    "offline_harness_verified": True,
                    "trusted_dispatcher_implemented": False,
                    "registry_execution_authorized": False,
                    "production_deployed": False,
                },
            },
            "capability_scope": {
                "offline_fixture_harness_verified": True,
                "non_callable_registry_binding_verified": True,
                "user_answer_available": False,
                "api_available": False,
                "export_available": False,
                "complete_result_set_freeze_available": False,
                "real_publication_replay_verified": False,
                "runtime_activation": False,
                "trusted_dispatcher_implemented": False,
                "production_deployed": False,
            },
            "performance_baseline": {
                "measurement_status": "not_w6_acceptance",
                "performance_acceptance_passed": False,
            },
            "prior_stage_receipt_digests": {
                "S1I-P0": p0["receipt_digest"],
                **{
                    dependency: self.load(
                        HOOK.WAVE_RECEIPT_ROOT / f"{dependency}.json"
                    )["receipt_digest"]
                    for dependency in HOOK.stage_prior_dependencies(stage)
                },
            },
        }
        evidence["implementation_semantic_digest"] = HOOK.object_digest(
            evidence,
            {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
        )
        evidence["implementation_candidate_id"] = (
            f"country-outage-p2-s1-{stage.lower()}-{evidence['implementation_semantic_digest'][:24]}"
        )
        evidence["content_digest"] = HOOK.object_digest(evidence, {"content_digest"})
        self.write(HOOK.WAVE_EVIDENCE_ROOT / f"{stage}.json", evidence)
        return evidence

    def resign_w1_w2_evidence(self, stage: str, evidence: dict) -> None:
        evidence["implementation_semantic_digest"] = HOOK.object_digest(
            evidence,
            {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
        )
        evidence["implementation_candidate_id"] = (
            f"country-outage-p2-s1-{stage.lower()}-{evidence['implementation_semantic_digest'][:24]}"
        )
        evidence["content_digest"] = HOOK.object_digest(evidence, {"content_digest"})
        self.write(HOOK.WAVE_EVIDENCE_ROOT / f"{stage}.json", evidence)

    def resign_w5_evidence(self, evidence: dict) -> None:
        evidence["implementation_semantic_digest"] = HOOK.object_digest(
            evidence,
            {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
        )
        evidence["implementation_candidate_id"] = (
            f"country-outage-p2-s1-w5-{evidence['implementation_semantic_digest'][:24]}"
        )
        evidence["content_digest"] = HOOK.object_digest(evidence, {"content_digest"})
        self.write(HOOK.WAVE_EVIDENCE_ROOT / "W5.json", evidence)

    def create_w5_fixture(self) -> dict:
        for relative, role in HOOK.W5_ARTIFACT_ROLES.items():
            source = ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_file():
                shutil.copy2(source, target)
            else:
                target.write_text(f"# W5 test fixture: {role}\n", encoding="utf-8")
        if not (ROOT / HOOK.W5_CONTROL_RUNTIME_SCHEMA_PATH).is_file():
            definitions = {}
            for unit_id in HOOK.WAVE_CONTRACT["W5"]["unit_ids"]:
                for reference in HOOK._w5_control_schema_refs(unit_id):
                    name = reference.rsplit("/", 1)[-1]
                    definitions[name] = {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"unit_id": {"const": unit_id}},
                        "required": ["unit_id"],
                    }
            self.write(HOOK.W5_CONTROL_RUNTIME_SCHEMA_PATH, {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$defs": definitions,
            })
        runtime_test_path = self.root / "backend/web/tests/test_country_outage_p2_s1_runtime.py"
        runtime_test_path.write_text(
            runtime_test_path.read_text(encoding="utf-8")
            + """

def test_frozen_design_semantic_validators_replay_actual_runtime_artifacts():
    design_alignment.validate_investigation_plan_instance()
    design_alignment.validate_result_set_instance()
    design_alignment.validate_evidence_graph_instance()

def test_frozen_design_semantic_validators_reject_named_attacks():
    assert "plan_admission_receipt_unresolved"
    assert "result_set_sort_digest_mismatch"
    assert "evidence_graph_plan_digest_mismatch"

def test_tool11_exact_time_route_state_journey_commits_result_graph_and_export():
    pass

def test_op29_to_op37_route_path_consistency_journey_commits_result_graph_and_export():
    pass
""",
            encoding="utf-8",
        )
        artifacts = [
            self.artifact_ref(relative, role)
            for relative, role in HOOK.W5_ARTIFACT_ROLES.items()
        ]
        for relative in {
            path
            for paths in HOOK.W5_SUITE_ARTIFACT_PATHS.values()
            for path in paths
        }:
            target = self.root / relative
            if target.is_file():
                continue
            source = ROOT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_file():
                shutil.copy2(source, target)
            else:
                target.write_text("# W5 suite artifact fixture\n", encoding="utf-8")
        for relative in HOOK.W5_PERSISTED_ARTIFACT_SCHEMAS.values():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        snapshot_digest = "sha256:" + "a" * 64
        admission = {
            "schema_version": "country_outage_p2_s1_w5_execution_admission_v1",
            "status": "admitted_local_isolated_execution",
            "registry_snapshot_id": "registry-snapshot-sha256:" + "a" * 64,
            "snapshot_digest": snapshot_digest,
            "registry_revision": 8,
            "previous_snapshot_id": HOOK.W5_W4_ACTUAL_SNAPSHOT_ID,
            "snapshot_object_digest": "sha256:" + "b" * 64,
            "execution_allowed_unit_ids": list(HOOK.W5_EXECUTION_ALLOWED_UNIT_IDS),
            "deferred_denied_unit_ids": sorted(HOOK.P2_1_UNIT_IDS),
            "control_unit_entries": [
                {
                    "unit_id": unit_id,
                    "input_schema_ref": HOOK._w5_control_schema_refs(unit_id)[0],
                    "output_schema_ref": HOOK._w5_control_schema_refs(unit_id)[1],
                    "schema_path": HOOK.W5_CONTROL_RUNTIME_SCHEMA_PATH.as_posix(),
                    "schema_sha256": HOOK.file_sha256(self.root / HOOK.W5_CONTROL_RUNTIME_SCHEMA_PATH),
                    "handler_id": "python:backend.services.country_outage_p2_s1_investigation_runtime.fixture",
                    "implementation_digest": "sha256:" + "c" * 64,
                }
                for unit_id in HOOK.WAVE_CONTRACT["W5"]["unit_ids"]
            ],
            "arbitrary_callback_supported": False,
            "external_data_allowed": False,
            "production_deployed": False,
        }
        admission["receipt_digest"] = "sha256:" + HOOK.object_digest(admission)
        query_receipt_base = {
            "receipt_kind": "query",
            "tool_run_id": "tool-run-fixture",
            "identity_digest": "5" * 64,
            "query_digest": "6" * 64,
            "source_population_id": "population:fixture",
            "source_population_schema_digest": "7" * 64,
            "source_dataset_digest": "8" * 64,
            "total_count": 1,
            "atomic_tool_query_receipt_digest": "9" * 64,
            "disposition": "passed",
        }
        query_receipt = {
            **query_receipt_base,
            "receipt_digest": HOOK.object_digest(query_receipt_base),
        }
        page_receipt_base = {
            "receipt_kind": "page",
            "page_index": 0,
            "page_content_digest": "a" * 64,
            "member_count": 1,
            "identity_digest": query_receipt["identity_digest"],
            "query_digest": query_receipt["query_digest"],
            "source_population_id": query_receipt["source_population_id"],
            "source_population_schema_digest": query_receipt["source_population_schema_digest"],
            "source_dataset_digest": query_receipt["source_dataset_digest"],
            "atomic_tool_query_receipt_digest": "b" * 64,
            "disposition": "passed",
        }
        page_receipt = {
            **page_receipt_base,
            "receipt_digest": HOOK.object_digest(page_receipt_base),
        }
        closure_identity = {
            "result_set_id": "result-set-sha256:" + "c" * 64,
            "result_set_revision": 1,
            "manifest_digest": "d" * 64,
            "content_digest": "e" * 64,
            "returned_count": 1,
            "total_count": 1,
            "set_completeness": "complete",
            "source_population_id": query_receipt["source_population_id"],
            "source_population_schema_digest": query_receipt["source_population_schema_digest"],
            "source_dataset_digest": query_receipt["source_dataset_digest"],
        }
        freeze_receipt_base = {
            "receipt_kind": "freeze",
            **closure_identity,
            "disposition": "passed",
        }
        freeze_receipt = {
            **freeze_receipt_base,
            "receipt_digest": HOOK.object_digest(freeze_receipt_base),
        }

        def control_execution_record(index: int, unit_id: str) -> dict:
            base = {
                "unit_id": unit_id,
                "input_digest": "sha256:" + format(index, "x") * 64,
                "output_digest": "sha256:" + format(index + 1, "x") * 64,
                "handler_id": "python:backend.services.country_outage_p2_s1_investigation_runtime.fixture",
                "implementation_digest": "sha256:" + "c" * 64,
                "input_schema_ref": HOOK._w5_control_schema_refs(unit_id)[0],
                "output_schema_ref": HOOK._w5_control_schema_refs(unit_id)[1],
                "input_schema_valid": True,
                "output_schema_valid": True,
                "execution_disposition": "completed",
            }
            return {**base, "call_receipt_digest": "sha256:" + HOOK.object_digest(base)}

        def semantic_replay(kind: str, character: str) -> dict:
            path = HOOK.W5_PERSISTED_ARTIFACT_SCHEMAS[kind]
            validator_id, implementation_path, entrypoints = HOOK.W5_DESIGN_SEMANTIC_VALIDATORS[kind]
            artifact_digest = "sha256:" + character * 64
            contract_digest = "sha256:" + HOOK.file_sha256(self.root / path)
            implementation_digest = "sha256:" + HOOK.file_sha256(self.root / implementation_path)
            validator_receipt_base = {
                "schema_version": "country_outage_p2_s1_w5_design_semantic_validator_receipt_v1",
                "artifact_kind": kind,
                "artifact_digest": artifact_digest,
                "validator_id": validator_id,
                "validator_version": "1.0.0",
                "validator_entrypoints": list(entrypoints),
                "validator_contract_digest": contract_digest,
                "validator_implementation_digests": {
                    entrypoint: implementation_digest for entrypoint in entrypoints
                },
                "trusted_store_snapshot_digest": "sha256:" + "f" * 64,
                "draft_schema_error_codes": [],
                "semantic_error_codes": [],
                "disposition": "passed",
            }
            validator_receipt = {
                **validator_receipt_base,
                "receipt_digest": HOOK.object_digest(validator_receipt_base),
            }
            runtime_receipt_kind, runtime_validator_id, runtime_implementation_path, authorizes_dispatcher = HOOK.W5_RUNTIME_ARTIFACT_VALIDATORS[kind]
            runtime_receipt_base = {
                "schema_version": "country_outage_p2_s1_w5_runtime_artifact_admission_receipt_v1",
                "artifact_kind": kind,
                "design_artifact_digest": artifact_digest,
                "runtime_subject_digest": artifact_digest,
                "frozen_design_validator_receipt_digest": validator_receipt["receipt_digest"],
                "runtime_receipt_kind": runtime_receipt_kind,
                "validator_id": runtime_validator_id,
                "validator_version": "1.0.0",
                "validator_contract_digest": contract_digest,
                "validator_implementation_digest": "sha256:" + HOOK.file_sha256(self.root / runtime_implementation_path),
                "registry_snapshot_digest": admission["snapshot_digest"],
                "parameter_bindings_digest": "sha256:" + "d" * 64,
                "trusted_store_snapshot_digest": "sha256:" + "e" * 64,
                "trusted_store_resolved": True,
                "authorizes_dispatcher_execution": authorizes_dispatcher,
                "disposition": "passed",
            }
            runtime_receipt = {
                **runtime_receipt_base,
                "receipt_digest": "sha256:" + HOOK.object_digest(runtime_receipt_base),
            }
            return {
                "artifact_kind": kind,
                "artifact_digest": artifact_digest,
                "schema_path": path,
                "schema_sha256": HOOK.file_sha256(self.root / path),
                "validator_id": validator_id,
                "validator_version": "1.0.0",
                "validator_contract_digest": contract_digest,
                "validator_implementation_digest": implementation_digest,
                "trusted_store_resolved": True,
                "draft_schema_error_count": 0,
                "semantic_error_count": 0,
                "replay_disposition": "passed",
                "validator_receipt": validator_receipt,
                "runtime_admission_receipt": runtime_receipt,
            }

        trace = {
            "schema_version": "country_outage_p2_s1_w5_execution_trace_v1",
            "trace_source": "runtime_execution_spy_and_content_addressed_store",
            "execution_admission_receipt_digest": admission["receipt_digest"],
            "invoked_control_unit_ids": list(HOOK.WAVE_CONTRACT["W5"]["unit_ids"]),
            "control_unit_call_counts": {
                unit_id: 1 for unit_id in HOOK.WAVE_CONTRACT["W5"]["unit_ids"]
            },
            "control_unit_execution_records": [
                control_execution_record(index, unit_id)
                for index, unit_id in enumerate(HOOK.WAVE_CONTRACT["W5"]["unit_ids"], start=1)
            ],
            "schema_validated_control_unit_ids": list(HOOK.WAVE_CONTRACT["W5"]["unit_ids"]),
            "schema_validation_failure_count": 0,
            "persisted_artifact_schema_bindings": [
                {
                    "artifact_kind": kind,
                    "validation_mode": "frozen_schema_valid" if kind == "InvestigationPlan" else "design_envelope_bound",
                    "frozen_schema_path": path,
                    "frozen_schema_sha256": HOOK.file_sha256(self.root / path),
                    "design_artifact_object_digest": "sha256:" + character * 64,
                    "runtime_object_digest": "sha256:" + character * 64,
                    "runtime_envelope_object_digest": None if kind == "InvestigationPlan" else "sha256:" + "e" * 64,
                }
                for kind, path, character in (
                    ("InvestigationPlan", HOOK.W5_PERSISTED_ARTIFACT_SCHEMAS["InvestigationPlan"], "1"),
                    ("ResultSet", HOOK.W5_PERSISTED_ARTIFACT_SCHEMAS["ResultSet"], "2"),
                    ("EvidenceGraph", HOOK.W5_PERSISTED_ARTIFACT_SCHEMAS["EvidenceGraph"], "3"),
                )
            ],
            "runtime_artifact_schema_validation_failure_count": 0,
            "design_semantic_validator_replays": [
                semantic_replay("InvestigationPlan", "1"),
                semantic_replay("ResultSet", "2"),
                semantic_replay("EvidenceGraph", "3"),
            ],
            "result_set_receipt_closure": {
                **closure_identity,
                "query_receipt": query_receipt,
                "page_receipts": [page_receipt],
                "freeze_receipt": freeze_receipt,
            },
            "plan_admission_validator": {},
            "business_unit_invocation_ids": sorted(HOOK.W5_REQUIRED_CORE_BUSINESS_UNIT_IDS),
            "schema_validated_business_unit_ids": sorted(HOOK.W5_REQUIRED_CORE_BUSINESS_UNIT_IDS),
            "business_unit_execution_records": [
                {
                    "unit_id": unit_id,
                    "invocation_count": 1,
                    "schema_validation_count": 2,
                    "schema_validation_failure_count": 0,
                    "registry_snapshot_digest": admission["snapshot_digest"],
                    "input_schema_ref": f"fixture:{unit_id}:input",
                    "output_schema_ref": f"fixture:{unit_id}:output",
                    "input_schema_digest": "sha256:" + format(index, "x") * 64,
                    "output_schema_digest": "sha256:" + format(index + 1, "x") * 64,
                    "handler_id": f"python:backend.services.fixture.{unit_id.lower().replace('-', '_')}",
                    "implementation_digest": "sha256:" + "c" * 64,
                    "call_receipt_digests": ["sha256:" + format(index + 7, "x") * 64],
                }
                for index, unit_id in enumerate(sorted(HOOK.W5_REQUIRED_CORE_BUSINESS_UNIT_IDS), start=1)
            ],
            "business_unit_schema_validation_failure_count": 0,
            "monetary_limit_mode": "unlimited",
            "max_cost_amount_zero_present": False,
            "cas_crash_recovery_replayed_same_outcome": True,
            "planning_grounding_port": {
                "port_kind": "trusted_fixture_sol_planning_host_grounding",
                "request_plan_nodes_rejected": True,
                "constructor_plan_nodes_supported": False,
                "grounded_plan_committed": True,
                "grounded_execution_recipe_schema_version": "country_outage_p2_s1_w5_grounded_execution_recipe_v1",
                "recipe_digest_verified": True,
                "projection_recipe_digest_verified": True,
                "host_grounding_recipe_digest_verified": True,
            },
            "plan_node_unit_ids": ["BOUNDARY-01", *sorted(HOOK.W5_REQUIRED_CORE_BUSINESS_UNIT_IDS)],
            "dynamic_fanout_count": 0,
            "arbitrary_callback_count": 0,
            "p2_1_unit_ids": [],
            "external_model_call_count": 0,
            "result_set_committed": True,
            "evidence_graph_committed": True,
            "export_committed": True,
            "cas_conflict_rejected": True,
            "running_cancel_verified": True,
            "local_fixture_only": True,
            "production_deployed": False,
        }
        plan_runtime_receipt = trace["design_semantic_validator_replays"][0]["runtime_admission_receipt"]
        trace["plan_admission_validator"] = {
            field: plan_runtime_receipt[field]
            for field in (
                "receipt_digest", "validator_id", "validator_version",
                "validator_contract_digest", "validator_implementation_digest",
                "trusted_store_resolved",
            )
        }
        replay_by_kind = {
            item["artifact_kind"]: item for item in trace["design_semantic_validator_replays"]
        }
        artifact_by_kind = {
            item["artifact_kind"]: item for item in trace["persisted_artifact_schema_bindings"]
        }
        investigation_id = "inv_w5_trace_fixture"
        base_investigation_revision = 1
        idempotency_key_digest = "sha256:" + "7" * 64
        registry_digest = admission["snapshot_digest"]
        plan_artifact_digest = artifact_by_kind["InvestigationPlan"]["design_artifact_object_digest"]
        execution_id = "w5-execution-sha256:" + HOOK.object_digest({
            "investigation_id": investigation_id,
            "base_investigation_revision": base_investigation_revision,
            "idempotency_key_digest": idempotency_key_digest,
            "plan_artifact_digest": plan_artifact_digest,
            "registry_snapshot_digest": registry_digest,
        })
        event_specs = [
            ("plan_design_validated", "InvestigationPlan", "a"),
            ("plan_runtime_admitted", "InvestigationPlan", "b"),
            ("running_committed", "InvestigationPlan", "running"),
            ("first_dispatch", "InvestigationPlan", "dispatch"),
            ("result_set_built", "ResultSet", "build"),
            ("result_set_design_validated", "ResultSet", "a"),
            ("result_set_runtime_admitted", "ResultSet", "b"),
            ("result_set_published", "ResultSet", "publish"),
            ("evidence_graph_built", "EvidenceGraph", "build"),
            ("evidence_graph_design_validated", "EvidenceGraph", "a"),
            ("evidence_graph_runtime_admitted", "EvidenceGraph", "b"),
            ("evidence_graph_receipts_published", "EvidenceGraph", "receipts"),
            ("evidence_graph_published", "EvidenceGraph", "publish"),
            ("final_investigation_cas_committed", "InvestigationCommit", "final"),
        ]
        events = []
        previous = None
        first_business_receipt = trace["business_unit_execution_records"][0]["call_receipt_digests"][0]
        for sequence, (event_kind, artifact_kind, phase) in enumerate(event_specs, start=1):
            if artifact_kind in replay_by_kind:
                replay = replay_by_kind[artifact_kind]
                binding = artifact_by_kind[artifact_kind]
                artifact_digest = binding["design_artifact_object_digest"]
                a_digest = replay["validator_receipt"]["receipt_digest"]
                b_digest = replay["runtime_admission_receipt"]["receipt_digest"]
                parameter_digest = replay["runtime_admission_receipt"]["parameter_bindings_digest"]
                a_exists = phase not in {"build"}
                b_exists = phase not in {"build", "a"}
                subject = {
                    "build": artifact_digest,
                    "a": "sha256:" + a_digest,
                    "b": b_digest,
                    "dispatch": first_business_receipt,
                    "running": "sha256:" + "8" * 64,
                    "receipts": "sha256:" + "9" * 64,
                    "publish": binding["runtime_object_digest"],
                }[phase]
            else:
                artifact_digest = "sha256:" + "f" * 64
                a_digest = None
                b_digest = None
                parameter_digest = replay_by_kind["InvestigationPlan"]["runtime_admission_receipt"]["parameter_bindings_digest"]
                a_exists = b_exists = False
                subject = artifact_digest
            event = {
                "schema_version": "country_outage_p2_s1_w5_admission_event_v1",
                "event_kind": event_kind,
                "execution_id": execution_id,
                "investigation_id": investigation_id,
                "sequence": sequence,
                "previous_event_digest": previous,
                "artifact_kind": artifact_kind,
                "artifact_digest": artifact_digest,
                "design_validator_receipt_digest": a_digest if a_exists else None,
                "runtime_admission_receipt_digest": b_digest if b_exists else None,
                "registry_snapshot_digest": registry_digest,
                "parameter_bindings_digest": parameter_digest,
                "action": event_kind,
                "action_subject_digest": subject,
            }
            event["event_digest"] = "sha256:" + HOOK.object_digest(event)
            previous = event["event_digest"]
            events.append(event)
        chain = {
            "schema_version": "country_outage_p2_s1_w5_admission_event_chain_v1",
            "execution_id": execution_id,
            "investigation_id": investigation_id,
            "base_investigation_revision": base_investigation_revision,
            "idempotency_key_digest": idempotency_key_digest,
            "registry_snapshot_digest": registry_digest,
            "events": events,
        }
        chain["chain_digest"] = "sha256:" + HOOK.object_digest(chain)
        trace["admission_event_chain"] = chain
        trace["trace_digest"] = HOOK.object_digest(trace)
        trace_line = HOOK.W5_EXECUTION_TRACE_PREFIX + json.dumps(
            trace,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        marker_outputs = {
            "w5-python": (
                "test_country_outage_p2_s1_runtime\n"
                "test_country_outage_p2_s1_investigation_api\n"
                + "\n".join(sorted(HOOK.W5_PYTHON_REQUIRED_TEST_NAMES))
                + f"\n{trace_line}\nRan 23 tests in <ELAPSED>\nOK\n"
            ),
            "w5-openapi": "........ 8 passed in <ELAPSED>\nOpenAPI 生成类型与契约一致\n",
            "w5-sidecar": (
                "W5 按 Sol planning→Host→DS 顺序发布\n"
                + "\n".join(HOOK.W5_SIDECAR_REQUIRED_OUTPUT_MARKERS)
                + "\nℹ tests 23\nℹ pass 23\n"
            ),
            "w5-frontend": "countryOutageInvestigation.test.ts\nCountryOutageInvestigationPage.test.ts\nEventDetailPage.test.ts\nTests 15 passed\n",
        }
        selected = {
            "w5-python": [
                "backend.web.tests.test_country_outage_p2_s1_runtime",
                "backend.web.tests.test_country_outage_p2_s1_investigation_api",
            ],
            "w5-openapi": ["backend.web.tests.test_openapi_contract"],
            "w5-sidecar": [
                "agent-sidecar/tests/p2-s1-w5-composition-runtime.test.ts",
                "agent-sidecar/tests/p2-s1-w5-planning-grounding-port.test.ts",
            ],
            "w5-frontend": [
                "frontend/src/api/countryOutageInvestigation.test.ts",
                "frontend/src/pages/CountryOutageInvestigationPage.test.ts",
                "frontend/src/pages/EventDetailPage.test.ts",
            ],
        }
        test_receipts = []
        for suite_id in HOOK.W5_TEST_SUITE_IDS:
            unit_ids: list[str] = []
            coverage = [
                {
                    "test_id": test_id,
                    "coverage_kind": "direct_execution",
                    "unit_ids": list(unit_ids),
                    "executed_unit_ids": list(unit_ids),
                }
                for test_id in selected[suite_id]
            ]
            bindings = [
                {
                    "path": HOOK.STAGE_TEST_RUNNER_PATH.as_posix(),
                    "size_bytes": (self.root / HOOK.STAGE_TEST_RUNNER_PATH).stat().st_size,
                    "sha256": HOOK.file_sha256(self.root / HOOK.STAGE_TEST_RUNNER_PATH),
                }
            ]
            for artifact_path in HOOK.W5_SUITE_ARTIFACT_PATHS[suite_id]:
                artifact = self.root / artifact_path
                bindings.append({
                    "path": artifact_path,
                    "size_bytes": artifact.stat().st_size,
                    "sha256": HOOK.file_sha256(artifact),
                })
            output = marker_outputs[suite_id]
            _, category, _ = HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id]
            receipt = {
                "schema_version": "country_outage_p2_s1_stage_test_run_receipt_v1",
                "runner_id": "country_outage_p2_s1_stage_test_runner",
                "runner_version": "1.2.0",
                "suite_id": suite_id,
                "stage": "W5",
                "category": category,
                "started_at_utc": "2026-08-13T00:00:00Z",
                "completed_at_utc": "2026-08-13T00:00:01Z",
                "command": HOOK.W5_SUITE_COMMANDS[suite_id],
                "working_directory": HOOK.W5_SUITE_WORKING_DIRECTORIES[suite_id],
                "selected_test_ids": selected[suite_id],
                "test_case_coverage": coverage,
                "tested_unit_ids": sorted(unit_ids),
                "tested_execution_unit_ids": sorted(unit_ids),
                "artifact_bindings": bindings,
                "exit_code": 0,
                "tests_run": {"w5-python": 23, "w5-openapi": 8, "w5-sidecar": 23, "w5-frontend": 15}[suite_id],
                "failure_count": 0,
                "error_count": 0,
                "skipped_count": 0,
                "passed": True,
                "normalized_output": output,
                "normalized_output_sha256": HOOK.sha256_bytes(output.encode("utf-8")),
            }
            receipt["receipt_digest"] = HOOK.object_digest(receipt)
            path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
            self.write(path, receipt)
            sha256 = HOOK.file_sha256(self.root / path)
            HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id] = ("W5", category, sha256)
            test_receipts.append({
                "suite_id": suite_id,
                "category": category,
                "path": path.as_posix(),
                "sha256": sha256,
                "receipt_digest": receipt["receipt_digest"],
            })
        dispatcher_path = "backend/services/country_outage_p2_s1_registry_dispatcher.py"
        probe = {
            "schema_version": "country_outage_p2_s1_w5_dispatcher_probe_v1",
            "dispatcher_path": dispatcher_path,
            "dispatcher_sha256": HOOK.file_sha256(self.root / dispatcher_path),
            "test_suite_id": "w5-python",
            "execution_admission_receipt_digest": admission["receipt_digest"],
            "caller_callback_injection_supported": False,
            "arbitrary_handler_name_supported": False,
            "dynamic_import_path_supported": False,
            "dynamic_member_fanout_supported": False,
            "p2_1_execution_supported": False,
            "production_deployed": False,
        }
        probe["receipt_digest"] = HOOK.object_digest(probe)
        evidence = {
            "schema_version": "country_outage_p2_s1_implementation_wave_evidence_v1",
            "stage": "W5",
            "status": "local_isolated_composition_runtime_accepted_for_w6_certification",
            "design_candidate_id": HOOK.DESIGN_CANDIDATE_ID,
            "baseline_content_digest": self.load(HOOK.BASELINE_PATH)["content_digest"],
            "implementation_candidate_id": None,
            "implementation_semantic_digest": None,
            "content_digest": None,
            "effect": HOOK.WAVE_CONTRACT["W5"]["effect"],
            "effect_verified": True,
            "implemented_unit_ids": list(HOOK.WAVE_CONTRACT["W5"]["unit_ids"]),
            "atomic_split_tests_passed": True,
            "p2_1_units_included": [],
            "production_deployed": False,
            "artifact_manifest": artifacts,
            "test_receipts": test_receipts,
            "execution_admission_receipt": admission,
            "dispatcher_probe": probe,
            "runtime_capability_evidence": {
                "execution_scope": "local_isolated_fixture_only",
                "local_runtime_implemented": True,
                "investigation_api_available": True,
                "investigation_ui_available": True,
                "complete_result_set_freeze_available": True,
                "stable_result_set_pagination_available": True,
                "evidence_graph_commit_available": True,
                "authorized_export_available": True,
                "revision_and_digest_cas_available": True,
                "explicit_followup_anchor_required": True,
                "cancel_and_rerun_available": True,
                "real_publication_execution_verified": False,
                "proof_suite_ids": ["w5-python", "w5-openapi", "w5-frontend"],
            },
            "model_chain_evidence": {
                "execution_order": ["gpt-5.6-sol-planning", "host-grounding-validation-execution", "gpt-5.6-sol-reference", "ds-first-answer", "ds-revision-at-most-once"],
                "teacher_model_id": "gpt-5.6-sol",
                "student_model_id": "ds",
                "transport": "local_fixture_injection_only",
                "external_model_call_count": 0,
                "hard_gate_ids": ["GATE-01", "GATE-02", "GATE-03", "GATE-04", "GATE-05"],
                "independent_oracle_required": True,
                "successful_student_revision_max": 1,
                "teacher_reference_is_ground_truth": False,
                "model_alignment_passed": False,
                "runtime_model_promotion": False,
                "production_deployed": False,
                "proof_suite_id": "w5-sidecar",
            },
            "atomicity_evidence": {
                "one_tool_one_fact_population_preserved": True,
                "one_operator_one_deterministic_transform_preserved": True,
                "plan_cap_01_static_dag_only": True,
                "hidden_dynamic_fanout": False,
                "host_hidden_business_transform": False,
                "api_hidden_business_transform": False,
                "renderer_hidden_business_transform": False,
                "model_hidden_business_transform": False,
                "arbitrary_callback_seam": False,
                "proof_suite_ids": ["w5-python", "w5-sidecar"],
            },
            "acceptance_scope": {
                "status": "local_isolated_composition_runtime_accepted_for_w6_certification",
                "implementation_wave_accepted": True,
                "w6_same_candidate_certification_passed": False,
                "external_model_execution_verified": False,
                "model_alignment_passed": False,
                "performance_acceptance_passed": False,
                "runtime_promotion_passed": False,
                "release_preparation_accepted": False,
                "production_deployed": False,
            },
            "prior_stage_receipt_digests": {},
        }
        self.resign_w5_evidence(evidence)
        return evidence

    def rewrite_and_repin_w5_execution_trace(self, evidence: dict, mutator) -> None:
        suite_id = "w5-python"
        path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
        receipt = self.load(path)
        lines = receipt["normalized_output"].splitlines()
        index = next(
            position for position, line in enumerate(lines)
            if line.startswith(HOOK.W5_EXECUTION_TRACE_PREFIX)
        )
        trace = json.loads(lines[index].removeprefix(HOOK.W5_EXECUTION_TRACE_PREFIX))
        mutator(trace)
        trace["trace_digest"] = HOOK.object_digest(trace, {"trace_digest"})
        lines[index] = HOOK.W5_EXECUTION_TRACE_PREFIX + json.dumps(
            trace,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        receipt["normalized_output"] = "\n".join(lines) + "\n"
        receipt["normalized_output_sha256"] = HOOK.sha256_bytes(
            receipt["normalized_output"].encode("utf-8")
        )
        receipt["receipt_digest"] = HOOK.object_digest(receipt, {"receipt_digest"})
        self.write(path, receipt)
        sha256 = HOOK.file_sha256(self.root / path)
        _, category, _ = HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id]
        HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id] = ("W5", category, sha256)
        reference = next(item for item in evidence["test_receipts"] if item["suite_id"] == suite_id)
        reference["sha256"] = sha256
        reference["receipt_digest"] = receipt["receipt_digest"]
        self.resign_w5_evidence(evidence)

    @staticmethod
    def resign_w5_event_chain(chain: dict) -> None:
        previous = None
        for sequence, event in enumerate(chain["events"], start=1):
            event["sequence"] = sequence
            event["previous_event_digest"] = previous
            event["event_digest"] = "sha256:" + HOOK.object_digest(event, {"event_digest"})
            previous = event["event_digest"]
        chain["chain_digest"] = "sha256:" + HOOK.object_digest(chain, {"chain_digest"})

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

    def test_w0_rejects_fully_resigned_forged_test_run_receipt(self) -> None:
        evidence = self.create_w0_fixture()
        reference = evidence["test_receipts"][0]
        receipt_path = Path(reference["path"])
        receipt = self.load(receipt_path)
        receipt["normalized_output"] = "[stdout]\nforged success\n[stderr]\n\n"
        receipt["normalized_output_sha256"] = HOOK.sha256_bytes(
            receipt["normalized_output"].encode("utf-8")
        )
        receipt["receipt_digest"] = HOOK.object_digest(receipt, {"receipt_digest"})
        self.write(receipt_path, receipt)
        reference["sha256"] = HOOK.file_sha256(self.root / receipt_path)
        reference["receipt_digest"] = receipt["receipt_digest"]
        evidence["implementation_semantic_digest"] = HOOK.object_digest(
            evidence,
            {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
        )
        evidence["implementation_candidate_id"] = f"country-outage-p2-s1-w0-{evidence['implementation_semantic_digest'][:24]}"
        evidence["content_digest"] = HOOK.object_digest(evidence, {"content_digest"})
        self.write(HOOK.WAVE_EVIDENCE_ROOT / "W0.json", evidence)
        self.assert_alignment_error(
            "wave_test_receipt_untrusted_resign",
            lambda: HOOK.run_alignment(self.root, "W0"),
        )

    def test_w1_rejects_missing_required_w0_dependency(self) -> None:
        self.create_w1_w2_fixture("W1")
        (self.root / HOOK.WAVE_RECEIPT_ROOT / "W0.json").unlink()
        self.assert_alignment_error(
            "无法严格读取 JSON",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_positive_closes_atomic_tools_operators_source_registry_and_tests(self) -> None:
        self.create_w1_w2_fixture("W1")
        receipt = HOOK.run_alignment(self.root, "W1")
        self.assertEqual(receipt["status"], "alignment_passed")
        self.assertIn("w1_atomic_unit_receipts_verified", receipt["checks"])
        self.assertIn("w1_registry_complete_wave_binding_admission_verified", receipt["checks"])
        self.assertIn("w1_trusted_dispatcher_deferred_to_w5_verified", receipt["checks"])
        self.assertFalse(any("activation" in check for check in receipt["checks"]))
        self.assertFalse(receipt["runtime_implemented"])
        self.assertFalse(receipt["production_deployed"])

    def test_w2_positive_closes_atomic_path_projection_set_and_count_wave(self) -> None:
        self.create_w1_w2_fixture("W2")
        receipt = HOOK.run_alignment(self.root, "W2")
        self.assertEqual(receipt["status"], "alignment_passed")
        self.assertIn("w2_w0_source_lineage_verified", receipt["checks"])
        self.assertIn("w2_positive_boundary_attack_tests_verified", receipt["checks"])

    def test_w3_positive_closes_interval_intersection_and_prefix_projection_wave(self) -> None:
        self.create_w1_w2_fixture("W3")
        receipt = HOOK.run_alignment(self.root, "W3")
        self.assertEqual(receipt["status"], "alignment_passed")
        self.assertIn("w3_atomic_unit_receipts_verified", receipt["checks"])
        self.assertIn("w3_registry_complete_wave_binding_admission_verified", receipt["checks"])
        self.assertIn("w3_positive_boundary_attack_tests_verified", receipt["checks"])
        self.assertIn("w3_trusted_dispatcher_deferred_to_w5_verified", receipt["checks"])
        self.assertFalse(receipt["runtime_implemented"])
        self.assertFalse(receipt["production_deployed"])

    def test_w4_positive_closes_exact_time_route_state_and_consistency_wave(self) -> None:
        self.create_w1_w2_fixture("W4")
        receipt = HOOK.run_alignment(self.root, "W4")
        self.assertEqual(receipt["status"], "alignment_passed")
        self.assertIn("w4_atomic_unit_receipts_verified", receipt["checks"])
        self.assertIn("w4_registry_complete_wave_binding_admission_verified", receipt["checks"])
        self.assertIn("w4_positive_boundary_attack_tests_verified", receipt["checks"])
        self.assertIn("w4_trusted_dispatcher_deferred_to_w5_verified", receipt["checks"])
        self.assertFalse(receipt["runtime_implemented"])
        self.assertFalse(receipt["production_deployed"])

    def test_w3_rejects_hidden_second_transform_after_full_resign(self) -> None:
        evidence = self.create_w1_w2_fixture("W3")
        operator = next(
            item for item in evidence["atomic_unit_receipts"]
            if item["unit_id"] == "OP-38"
        )
        operator["business_transform_count"] = 2
        operator["registered_atomic_operation_count"] = 2
        operator["receipt_digest"] = HOOK.object_digest(operator, {"receipt_digest"})
        self.resign_w1_w2_evidence("W3", evidence)
        self.assert_alignment_error(
            "w1_w2_hidden_second_transform",
            lambda: HOOK.run_alignment(self.root, "W3"),
        )

    def test_w4_rejects_hidden_second_population_read_after_full_resign(self) -> None:
        evidence = self.create_w1_w2_fixture("W4")
        tool = next(
            item for item in evidence["atomic_unit_receipts"]
            if item["unit_id"] == "TOOL-11"
        )
        tool["fact_population_read_count"] = 2
        tool["receipt_digest"] = HOOK.object_digest(tool, {"receipt_digest"})
        self.resign_w1_w2_evidence("W4", evidence)
        self.assert_alignment_error(
            "w1_w2_hidden_second_population_read",
            lambda: HOOK.run_alignment(self.root, "W4"),
        )

    def test_w4_rejects_op33_empty_population_contract_regression(self) -> None:
        self.create_w1_w2_fixture("W4")
        schema = self.load(HOOK.OPERATOR_CONTRACT_SCHEMA_PATH)
        schema["$defs"]["op33InputPayload"]["properties"]["new_prefix_state_rows"]["minItems"] = 1
        self.write(HOOK.OPERATOR_CONTRACT_SCHEMA_PATH, schema)
        self.assert_alignment_error(
            "op33_empty_population_contract_open",
            lambda: HOOK._validate_op33_population_evidence_contract(self.root),
        )

    def test_w4_rejects_op33_population_evidence_contract_regression(self) -> None:
        self.create_w1_w2_fixture("W4")
        schema = self.load(HOOK.STRUCTURAL_BINDING_PATH)
        receipt = schema["$defs"]["populationEvidenceBindingReceipt"]
        receipt["properties"]["operator_id"]["enum"].remove("OP-33")
        self.write(HOOK.STRUCTURAL_BINDING_PATH, schema)
        self.assert_alignment_error(
            "op33_population_evidence_contract_open",
            lambda: HOOK._validate_op33_population_evidence_contract(self.root),
        )

    def test_w4_rejects_op33_identity_binding_removal(self) -> None:
        self.create_w1_w2_fixture("W4")
        schema = self.load(HOOK.STRUCTURAL_BINDING_PATH)
        receipt = schema["$defs"]["populationEvidenceBindingReceipt"]
        receipt["allOf"] = []
        self.write(HOOK.STRUCTURAL_BINDING_PATH, schema)
        self.assert_alignment_error(
            "op33_population_evidence_contract_open",
            lambda: HOOK._validate_op33_population_evidence_contract(self.root),
        )

    def test_w4_rejects_op33_second_population_binding_removal(self) -> None:
        self.create_w1_w2_fixture("W4")
        path = self.root / HOOK.W1_W2_OPERATOR_IMPLEMENTATION_PATH
        source = path.read_text(encoding="utf-8")
        source = source.replace(
            "    right_population_evidence = _population_evidence(",
            "    right_population_evidence = _merge_evidence(",
            1,
        )
        path.write_text(source, encoding="utf-8")
        self.assert_alignment_error(
            "op33_population_evidence_contract_open",
            lambda: HOOK._validate_op33_population_evidence_contract(self.root),
        )

    def test_w4_rejects_skipping_w3_governance_receipt_after_full_resign(self) -> None:
        evidence = self.create_w1_w2_fixture("W4")
        evidence["prior_stage_receipt_digests"].pop("W3")
        self.resign_w1_w2_evidence("W4", evidence)
        self.assert_alignment_error(
            "wave_prior_binding_mismatch",
            lambda: HOOK.run_alignment(self.root, "W4"),
        )

    def test_w4_rejects_nonempty_registry_execution_allowlist(self) -> None:
        evidence = self.create_w1_w2_fixture("W4")
        admission = evidence["registry_binding_projection"]["admission_receipt"]
        admission["execution_allowed_unit_ids"] = ["TOOL-11"]
        admission["receipt_digest"] = HOOK.object_digest(admission, {"receipt_digest"})
        self.resign_w1_w2_evidence("W4", evidence)
        self.assert_alignment_error(
            "w1_w2_registry_execution_authorization_overclaim",
            lambda: HOOK.run_alignment(self.root, "W4"),
        )

    def test_w4_rejects_p2_1_unit_smuggling_after_full_resign(self) -> None:
        evidence = self.create_w1_w2_fixture("W4")
        evidence["p2_1_units_included"] = ["OP-34"]
        self.resign_w1_w2_evidence("W4", evidence)
        self.assert_alignment_error(
            "p2_1_unit_smuggled",
            lambda: HOOK.run_alignment(self.root, "W4"),
        )

    def test_w4_rejects_user_effect_overclaim_after_full_resign(self) -> None:
        evidence = self.create_w1_w2_fixture("W4")
        evidence["effect"] = "user_facing_country_outage_investigation_answer_available"
        self.resign_w1_w2_evidence("W4", evidence)
        self.assert_alignment_error(
            "wave_effect_not_verified",
            lambda: HOOK.run_alignment(self.root, "W4"),
        )

    def test_w1_rejects_static_analysis_masquerading_as_execution_coverage(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        suite_id = "w1-attack"
        receipt_path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
        receipt = self.load(receipt_path)
        static_item = next(
            item for item in receipt["test_case_coverage"]
            if item["coverage_kind"] == "static_atomicity_analysis"
        )
        static_item["executed_unit_ids"] = list(static_item["unit_ids"])
        receipt["tested_execution_unit_ids"] = sorted({
            unit_id
            for item in receipt["test_case_coverage"]
            for unit_id in item["executed_unit_ids"]
        })
        receipt["receipt_digest"] = HOOK.object_digest(receipt, {"receipt_digest"})
        self.write(receipt_path, receipt)
        forged_sha = HOOK.file_sha256(self.root / receipt_path)
        old_pin = HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id]
        HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id] = (old_pin[0], old_pin[1], forged_sha)
        reference = next(item for item in evidence["test_receipts"] if item["suite_id"] == suite_id)
        reference["sha256"] = forged_sha
        reference["receipt_digest"] = receipt["receipt_digest"]
        self.resign_w1_w2_evidence("W1", evidence)
        try:
            self.assert_alignment_error(
                "静态检查不得冒充执行覆盖",
                lambda: HOOK.run_alignment(self.root, "W1"),
            )
        finally:
            HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id] = old_pin

    def test_w2_rejects_direct_test_execution_population_overclaim(self) -> None:
        evidence = self.create_w1_w2_fixture("W2")
        suite_id = "w2-attack"
        receipt_path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
        receipt = self.load(receipt_path)
        direct_item = next(
            item for item in receipt["test_case_coverage"]
            if item["coverage_kind"] == "direct_execution"
        )
        direct_item["unit_ids"].append("OP-28")
        receipt["tested_unit_ids"] = sorted({
            unit_id
            for item in receipt["test_case_coverage"]
            for unit_id in item["unit_ids"]
        })
        receipt["receipt_digest"] = HOOK.object_digest(receipt, {"receipt_digest"})
        self.write(receipt_path, receipt)
        forged_sha = HOOK.file_sha256(self.root / receipt_path)
        old_pin = HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id]
        HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id] = (old_pin[0], old_pin[1], forged_sha)
        reference = next(item for item in evidence["test_receipts"] if item["suite_id"] == suite_id)
        reference["sha256"] = forged_sha
        reference["receipt_digest"] = receipt["receipt_digest"]
        self.resign_w1_w2_evidence("W2", evidence)
        try:
            self.assert_alignment_error(
                "直接执行测试必须精确登记实际单元",
                lambda: HOOK.run_alignment(self.root, "W2"),
            )
        finally:
            HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id] = old_pin

    def test_w1_rejects_missing_required_artifact_even_when_outer_evidence_is_resigned(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        evidence["artifact_manifest"].pop()
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_artifact_population_mismatch",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_extra_or_duplicate_artifact(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        evidence["artifact_manifest"].append(copy.deepcopy(evidence["artifact_manifest"][0]))
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_artifact_population_mismatch",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_digest_substitution_with_only_outer_resign(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        evidence["atomic_unit_receipts"][0]["implementation_sha256"] = "0" * 64
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_atomic_receipt_digest_mismatch",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_mixed_wave_unit_population(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        replacement = next(
            item for item in self.create_w1_w2_fixture("W2")["atomic_unit_receipts"]
            if item["unit_id"] == "OP-15"
        )
        evidence["atomic_unit_receipts"][0] = replacement
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_atomic_receipt_population_mismatch",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_hidden_second_business_transform_after_full_resign(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        operator = next(item for item in evidence["atomic_unit_receipts"] if item["unit_kind"] == "operator")
        operator["business_transform_count"] = 2
        operator["registered_atomic_operation_count"] = 2
        operator["receipt_digest"] = HOOK.object_digest(operator, {"receipt_digest"})
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_hidden_second_transform",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w2_rejects_missing_structural_binding_contract(self) -> None:
        evidence = self.create_w1_w2_fixture("W2")
        evidence["structural_binding_contract"] = None
        self.resign_w1_w2_evidence("W2", evidence)
        self.assert_alignment_error(
            "w1_w2_structural_contract_missing",
            lambda: HOOK.run_alignment(self.root, "W2"),
        )

    def test_w2_rejects_registry_population_drift_after_full_resign(self) -> None:
        evidence = self.create_w1_w2_fixture("W2")
        registry = evidence["registry_binding_projection"]
        binding = registry["binding_manifest"]
        binding["wave_binding_unit_ids"].append("OP-34")
        binding["binding_manifest_digest"] = HOOK.object_digest(
            binding, {"binding_manifest_id", "binding_manifest_digest"}
        )
        binding["binding_manifest_id"] = (
            f"p2-s1-dispatch-binding-manifest-sha256:{binding['binding_manifest_digest']}"
        )
        snapshot = registry["snapshot"]
        snapshot["admitted_wave_binding_unit_ids"].append("OP-34")
        snapshot["admitted_binding_unit_ids"].append("OP-34")
        snapshot["binding_manifest_id"] = binding["binding_manifest_id"]
        snapshot["binding_manifest_digest"] = binding["binding_manifest_digest"]
        snapshot["snapshot_digest"] = HOOK.object_digest(snapshot, {"snapshot_id", "snapshot_digest"})
        snapshot["snapshot_id"] = f"p2-s1-registry-wave-sha256:{snapshot['snapshot_digest']}"
        admission = registry["admission_receipt"]
        admission["snapshot_id"] = snapshot["snapshot_id"]
        admission["snapshot_digest"] = snapshot["snapshot_digest"]
        admission["binding_manifest_id"] = binding["binding_manifest_id"]
        admission["binding_manifest_digest"] = binding["binding_manifest_digest"]
        admission["admitted_wave_binding_unit_ids"].append("OP-34")
        admission["admitted_binding_unit_ids"].append("OP-34")
        admission["receipt_digest"] = HOOK.object_digest(admission, {"receipt_digest"})
        self.resign_w1_w2_evidence("W2", evidence)
        self.assert_alignment_error(
            "w1_w2_registry_population_drift",
            lambda: HOOK.run_alignment(self.root, "W2"),
        )

    def test_w1_rejects_forged_test_artifact_sha_after_full_resign(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        reference = evidence["test_receipts"][0]
        receipt_path = Path(reference["path"])
        run_receipt = self.load(receipt_path)
        run_receipt["artifact_bindings"][1]["sha256"] = "f" * 64
        run_receipt["receipt_digest"] = HOOK.object_digest(run_receipt, {"receipt_digest"})
        self.write(receipt_path, run_receipt)
        reference["sha256"] = HOOK.file_sha256(self.root / receipt_path)
        reference["receipt_digest"] = run_receipt["receipt_digest"]
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "wave_test_receipt_untrusted_resign",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_fully_resigned_registry_runtime_bundle(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        reference = evidence["registry_runtime_evidence"]
        bundle_path = Path(reference["path"])
        bundle = self.load(bundle_path)
        admission = bundle["wave_admission_receipt"]
        admission["execution_allowed_unit_ids"] = ["TOOL-07"]
        admission["receipt_digest"] = HOOK._p2s1_governance_digest({
            key: value for key, value in admission.items() if key != "receipt_digest"
        })
        bundle["content_digest"] = HOOK._p2s1_governance_digest({
            key: value for key, value in bundle.items() if key != "content_digest"
        })
        self.write(bundle_path, bundle)
        reference["sha256"] = HOOK.file_sha256(self.root / bundle_path)
        reference["content_digest"] = bundle["content_digest"]
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_registry_runtime_evidence_untrusted_resign",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_user_answer_or_runtime_capability_overclaim(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        evidence["capability_scope"]["user_answer_available"] = True
        evidence["capability_scope"]["runtime_activation"] = True
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_capability_overclaim",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_nonempty_registry_execution_allowlist(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        admission = evidence["registry_binding_projection"]["admission_receipt"]
        admission["execution_allowed_unit_ids"] = ["TOOL-07"]
        admission["receipt_digest"] = HOOK.object_digest(admission, {"receipt_digest"})
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_registry_execution_authorization_overclaim",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_active_unit_population_smuggled_into_binding_snapshot(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        snapshot = evidence["registry_binding_projection"]["snapshot"]
        snapshot["active_unit_ids"] = list(HOOK.WAVE_CONTRACT["W1"]["unit_ids"])
        snapshot["snapshot_digest"] = HOOK.object_digest(snapshot, {"snapshot_id", "snapshot_digest"})
        snapshot["snapshot_id"] = f"p2-s1-registry-wave-sha256:{snapshot['snapshot_digest']}"
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_registry_snapshot_invalid",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_execution_started_after_full_resign(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        admission = evidence["registry_binding_projection"]["admission_receipt"]
        admission["execution_started"] = True
        admission["receipt_digest"] = HOOK.object_digest(admission, {"receipt_digest"})
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_registry_execution_authorization_overclaim",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_callable_caller_callback_seam_even_when_probe_is_resigned(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        probe = evidence["registry_binding_projection"]["execution_probe"]
        probe["caller_callback_injection_supported"] = True
        probe["caller_callback_spy_count"] = 1
        probe["receipt_digest"] = HOOK.object_digest(probe, {"receipt_digest"})
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_registry_callback_seam_open",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_binding_admission_masquerading_as_handler_activation(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        admission = evidence["registry_binding_projection"]["admission_receipt"]
        admission["status"] = "activated_complete_atomic_wave"
        admission["trusted_dispatcher_bound"] = True
        admission["receipt_digest"] = HOOK.object_digest(admission, {"receipt_digest"})
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_registry_handler_activation_overclaim",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_old_w0_receipt_replay_after_full_resign(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        evidence["w0_source_binding"]["w0_receipt_digest"] = "e" * 64
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_old_w0_receipt_replay",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_self_consistent_w0_receipt_with_stale_hook_binding(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        w0 = self.load(HOOK.WAVE_RECEIPT_ROOT / "W0.json")
        w0["hook_sha256"] = "e" * 64
        w0["receipt_digest"] = HOOK.object_digest(w0, {"receipt_digest"})
        self.write(HOOK.WAVE_RECEIPT_ROOT / "W0.json", w0)
        evidence["w0_source_binding"]["w0_receipt_digest"] = w0["receipt_digest"]
        evidence["prior_stage_receipt_digests"]["W0"] = w0["receipt_digest"]
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "wave_prior_artifact_binding_mismatch",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w1_rejects_performance_acceptance_overclaim(self) -> None:
        evidence = self.create_w1_w2_fixture("W1")
        evidence["performance_baseline"] = {
            "measurement_status": "w6_acceptance",
            "performance_acceptance_passed": True,
        }
        self.resign_w1_w2_evidence("W1", evidence)
        self.assert_alignment_error(
            "w1_w2_performance_overclaim",
            lambda: HOOK.run_alignment(self.root, "W1"),
        )

    def test_w5_positive_closes_local_runtime_without_external_model_performance_or_production(self) -> None:
        evidence = self.create_w5_fixture()
        checks = HOOK.validate_w5_evidence(self.root, evidence)
        self.assertIn("w5_api_ui_result_graph_export_and_cas_verified", checks)
        self.assertIn("w5_sol_host_ds_local_fixture_chain_verified", checks)
        self.assertFalse(evidence["acceptance_scope"]["production_deployed"])
        self.assertFalse(evidence["acceptance_scope"]["performance_acceptance_passed"])

    def test_w5_rejects_production_overclaim_after_full_resign(self) -> None:
        evidence = self.create_w5_fixture()
        evidence["acceptance_scope"]["production_deployed"] = True
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "w5_acceptance_overclaim",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_v6_task_requires_pytest_openapi_and_both_sidecar_suites(self) -> None:
        task = self.load(HOOK.TASK_PATH)
        task["taskId"] = HOOK.W5_TASK_ID
        task["targetVersion"] = HOOK.W5_TARGET_VERSION
        task["taskTransition"]["supersedesTaskId"] = "country-outage-agent-p2-s1-w5-composition-runtime-v5-20260813"
        task["requiredChecks"] = [
            {"command": ["uv", "run", "--project", "backend", "pytest", "-q", "backend/web/tests/test_openapi_contract.py"]},
            {"command": ["bash", "-lc", "cd agent-sidecar && npm run test:p2-s1-w5"]},
        ]
        self.write(HOOK.TASK_PATH, task)
        self.assertIn("w5_task_boundary_verified", HOOK.validate_task(self.root))

        bad = copy.deepcopy(task)
        bad["requiredChecks"][0]["command"] = ["python3", "-m", "unittest", "backend.web.tests.test_openapi_contract"]
        self.write(HOOK.TASK_PATH, bad)
        self.assert_alignment_error("w5_openapi_zero_test_command_forbidden", lambda: HOOK.validate_task(self.root))

        bad = copy.deepcopy(task)
        bad["requiredChecks"][1]["command"] = ["bash", "-lc", "node --test dist/tests/p2-s1-w5-composition-runtime.test.js"]
        self.write(HOOK.TASK_PATH, bad)
        self.assert_alignment_error("w5_sidecar_suite_incomplete", lambda: HOOK.validate_task(self.root))

    def test_w5_rejects_external_model_execution_overclaim_after_full_resign(self) -> None:
        evidence = self.create_w5_fixture()
        evidence["model_chain_evidence"]["transport"] = "external_model_api"
        evidence["model_chain_evidence"]["external_model_call_count"] = 2
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "w5_model_chain_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_performance_acceptance_overclaim_after_full_resign(self) -> None:
        evidence = self.create_w5_fixture()
        evidence["acceptance_scope"]["performance_acceptance_passed"] = True
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "w5_acceptance_overclaim",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_hidden_dynamic_fanout_after_full_resign(self) -> None:
        evidence = self.create_w5_fixture()
        evidence["atomicity_evidence"]["hidden_dynamic_fanout"] = True
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "w5_atomicity_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_pinned_trace_missing_one_control_unit(self) -> None:
        evidence = self.create_w5_fixture()

        def mutate(trace: dict) -> None:
            trace["invoked_control_unit_ids"].remove("DELIVERY-01")
            trace["control_unit_call_counts"].pop("DELIVERY-01")

        self.rewrite_and_repin_w5_execution_trace(evidence, mutate)
        self.assert_alignment_error(
            "w5_control_execution_population_incomplete",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_control_id_list_without_exact_call_records(self) -> None:
        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace["control_unit_execution_records"].pop(),
        )
        self.assert_alignment_error(
            "w5_control_execution_population_incomplete",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace["control_unit_execution_records"][0].update({
                "handler_id": "python:backend.services.country_outage_p2_s1_investigation_runtime.ghost",
            }),
        )
        self.assert_alignment_error(
            "w5_control_execution_record_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_pinned_trace_with_dynamic_fanout_or_callback(self) -> None:
        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace.update({"dynamic_fanout_count": 1}),
        )
        self.assert_alignment_error(
            "w5_dynamic_fanout_detected",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace.update({"arbitrary_callback_count": 1}),
        )
        self.assert_alignment_error(
            "w5_dispatcher_callback_seam_open",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_pinned_trace_with_external_model_or_missing_running_cancel(self) -> None:
        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace.update({"external_model_call_count": 1}),
        )
        self.assert_alignment_error(
            "w5_external_model_overclaim",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_persisted_artifact_or_plan_admission_evidence_gap(self) -> None:
        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace["persisted_artifact_schema_bindings"].pop(),
        )
        self.assert_alignment_error(
            "w5_runtime_artifact_schema_evidence_missing",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace["plan_admission_validator"].update({"trusted_store_resolved": False}),
        )
        self.assert_alignment_error(
            "w5_plan_admission_evidence_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_draft_only_or_failed_design_semantic_replay(self) -> None:
        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace["design_semantic_validator_replays"][1].update({
                "semantic_error_count": 1,
                "replay_disposition": "failed",
            }),
        )
        self.assert_alignment_error(
            "w5_design_semantic_replay_failed",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_simplified_validator_self_reporting_zero_errors(self) -> None:
        evidence = self.create_w5_fixture()

        def mutate(trace: dict) -> None:
            replay = trace["design_semantic_validator_replays"][0]
            replay["semantic_error_count"] = 0
            replay["replay_disposition"] = "passed"
            receipt = replay["validator_receipt"]
            receipt["validator_entrypoints"] = ["simplified_parallel_validator"]
            receipt["validator_implementation_digests"] = {
                "simplified_parallel_validator": "sha256:" + "0" * 64,
            }
            receipt["semantic_error_codes"] = []
            receipt["disposition"] = "passed"
            receipt["receipt_digest"] = HOOK.object_digest(receipt, {"receipt_digest"})

        self.rewrite_and_repin_w5_execution_trace(evidence, mutate)
        self.assert_alignment_error(
            "w5_design_semantic_validator_entrypoint_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_frozen_design_placeholder_as_runtime_validator(self) -> None:
        evidence = self.create_w5_fixture()

        def mutate(trace: dict) -> None:
            receipt = trace["design_semantic_validator_replays"][1]["runtime_admission_receipt"]
            receipt["validator_contract_digest"] = "sha256:" + "9" * 64
            receipt["validator_implementation_digest"] = "sha256:" + "a" * 64
            receipt["receipt_digest"] = "sha256:" + HOOK.object_digest(receipt, {"receipt_digest"})

        self.rewrite_and_repin_w5_execution_trace(evidence, mutate)
        self.assert_alignment_error(
            "w5_runtime_artifact_admission_placeholder_masquerade",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_runtime_admission_registry_drift(self) -> None:
        evidence = self.create_w5_fixture()

        def mutate(trace: dict) -> None:
            receipt = trace["design_semantic_validator_replays"][2]["runtime_admission_receipt"]
            prior_receipt_digest = receipt["receipt_digest"]
            receipt["registry_snapshot_digest"] = "sha256:" + "0" * 64
            receipt["receipt_digest"] = "sha256:" + HOOK.object_digest(receipt, {"receipt_digest"})
            chain = trace["admission_event_chain"]
            for event in chain["events"]:
                if event.get("runtime_admission_receipt_digest") == prior_receipt_digest:
                    event["runtime_admission_receipt_digest"] = receipt["receipt_digest"]
                if (
                    event["event_kind"] == "evidence_graph_runtime_admitted"
                    and event["action_subject_digest"] == prior_receipt_digest
                ):
                    event["action_subject_digest"] = receipt["receipt_digest"]
            self.resign_w5_event_chain(chain)

        self.rewrite_and_repin_w5_execution_trace(evidence, mutate)
        self.assert_alignment_error(
            "w5_runtime_artifact_admission_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_runner_missing_required_validator_or_core_journey_test(self) -> None:
        evidence = self.create_w5_fixture()
        suite_id = "w5-python"
        path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
        receipt = self.load(path)
        missing = "test_frozen_design_semantic_validators_replay_actual_runtime_artifacts"
        receipt["normalized_output"] = receipt["normalized_output"].replace(missing + "\n", "")
        receipt["normalized_output_sha256"] = HOOK.sha256_bytes(receipt["normalized_output"].encode("utf-8"))
        receipt["receipt_digest"] = HOOK.object_digest(receipt, {"receipt_digest"})
        self.write(path, receipt)
        sha256 = HOOK.file_sha256(self.root / path)
        _, category, _ = HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id]
        HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id] = ("W5", category, sha256)
        reference = next(item for item in evidence["test_receipts"] if item["suite_id"] == suite_id)
        reference["sha256"] = sha256
        reference["receipt_digest"] = receipt["receipt_digest"]
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "w5_python_required_test_not_run",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_trace_test_that_mints_posthoc_admission(self) -> None:
        evidence = self.create_w5_fixture()
        receipt_path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / "w5-python.json"
        normalized_output = self.load(receipt_path)["normalized_output"]
        source_path = self.root / "backend/web/tests/test_country_outage_p2_s1_runtime.py"
        source = source_path.read_text(encoding="utf-8")
        marker = "    def test_z_runtime_execution_trace_is_derived_from_actual_spy_and_store(self):\n"
        self.assertIn(marker, source)
        source_path.write_text(
            source.replace(
                marker,
                marker + "        self.runtime._runtime_artifact_admission()  # 事后补证攻击\n",
                1,
            ),
            encoding="utf-8",
        )
        self.assert_alignment_error(
            "w5_trace_posthoc_admission_mint_forbidden",
            lambda: HOOK._validate_w5_python_validator_test_source(self.root, normalized_output),
        )

        source_path.write_text(
            source.replace(
                marker,
                marker + "        self.runtime._admission_event()  # 事后补写event攻击\n",
                1,
            ),
            encoding="utf-8",
        )
        self.assert_alignment_error(
            "w5_trace_posthoc_admission_mint_forbidden",
            lambda: HOOK._validate_w5_python_validator_test_source(self.root, normalized_output),
        )

    def test_w5_rejects_trace_test_that_mentions_but_does_not_read_event_store(self) -> None:
        evidence = self.create_w5_fixture()
        receipt_path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / "w5-python.json"
        normalized_output = self.load(receipt_path)["normalized_output"]
        source_path = self.root / "backend/web/tests/test_country_outage_p2_s1_runtime.py"
        source = source_path.read_text(encoding="utf-8")
        actual_read = 'runtime.store.list_json("admission-event")'
        self.assertIn(actual_read, source)
        source_path.write_text(
            source.replace(actual_read, '[]  # runtime.store.list_json("admission-event")', 1),
            encoding="utf-8",
        )
        self.assert_alignment_error(
            "w5_trace_actual_store_population_missing",
            lambda: HOOK._validate_w5_python_validator_test_source(self.root, normalized_output),
        )

    def test_w5_rejects_sidecar_runner_missing_recipe_attack(self) -> None:
        evidence = self.create_w5_fixture()
        suite_id = "w5-sidecar"
        path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
        receipt = self.load(path)
        missing = HOOK.W5_SIDECAR_REQUIRED_OUTPUT_MARKERS[0]
        receipt["normalized_output"] = receipt["normalized_output"].replace(missing + "\n", "")
        receipt["normalized_output_sha256"] = HOOK.sha256_bytes(receipt["normalized_output"].encode("utf-8"))
        receipt["receipt_digest"] = HOOK.object_digest(receipt, {"receipt_digest"})
        self.write(path, receipt)
        sha256 = HOOK.file_sha256(self.root / path)
        _, category, _ = HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id]
        HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id] = ("W5", category, sha256)
        reference = next(item for item in evidence["test_receipts"] if item["suite_id"] == suite_id)
        reference["sha256"] = sha256
        reference["receipt_digest"] = receipt["receipt_digest"]
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "w5_sidecar_required_test_not_run",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_result_set_query_digest_cycle_fields(self) -> None:
        evidence = self.create_w5_fixture()

        def mutate(trace: dict) -> None:
            query = trace["result_set_receipt_closure"]["query_receipt"]
            query["manifest_digest"] = trace["result_set_receipt_closure"]["manifest_digest"]
            query["content_digest"] = trace["result_set_receipt_closure"]["content_digest"]
            query["receipt_digest"] = HOOK.object_digest(query, {"receipt_digest"})

        self.rewrite_and_repin_w5_execution_trace(evidence, mutate)
        self.assert_alignment_error(
            "w5_result_set_receipt_closure_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_tool_query_receipt_reused_as_host_query_or_page(self) -> None:
        evidence = self.create_w5_fixture()

        def reuse_query(trace: dict) -> None:
            query = trace["result_set_receipt_closure"]["query_receipt"]
            query["atomic_tool_query_receipt_digest"] = query["receipt_digest"]

        self.rewrite_and_repin_w5_execution_trace(evidence, reuse_query)
        self.assert_alignment_error(
            "w5_result_set_receipt_reused",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()

        def reuse_page(trace: dict) -> None:
            page = trace["result_set_receipt_closure"]["page_receipts"][0]
            page["atomic_tool_query_receipt_digest"] = page["receipt_digest"]

        self.rewrite_and_repin_w5_execution_trace(evidence, reuse_page)
        self.assert_alignment_error(
            "w5_result_set_receipt_reused",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_result_set_page_population_or_freeze_binding_drift(self) -> None:
        evidence = self.create_w5_fixture()

        def drift_page(trace: dict) -> None:
            page = trace["result_set_receipt_closure"]["page_receipts"][0]
            page["member_count"] = 0
            page["receipt_digest"] = HOOK.object_digest(page, {"receipt_digest"})

        self.rewrite_and_repin_w5_execution_trace(evidence, drift_page)
        self.assert_alignment_error(
            "w5_result_set_receipt_binding_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_resigned_admission_event_reorder_or_posthoc_insert(self) -> None:
        evidence = self.create_w5_fixture()

        def reorder(trace: dict) -> None:
            chain = trace["admission_event_chain"]
            chain["events"][2], chain["events"][3] = chain["events"][3], chain["events"][2]
            self.resign_w5_event_chain(chain)

        self.rewrite_and_repin_w5_execution_trace(evidence, reorder)
        self.assert_alignment_error(
            "w5_admission_event_order_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()

        def posthoc(trace: dict) -> None:
            chain = trace["admission_event_chain"]
            injected = copy.deepcopy(chain["events"][1])
            injected["event_kind"] = "posthoc_runtime_admission_reasserted"
            injected["action"] = injected["event_kind"]
            chain["events"].insert(2, injected)
            self.resign_w5_event_chain(chain)

        self.rewrite_and_repin_w5_execution_trace(evidence, posthoc)
        self.assert_alignment_error(
            "w5_admission_event_order_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_resigned_cross_investigation_event_splice(self) -> None:
        evidence = self.create_w5_fixture()

        def splice(trace: dict) -> None:
            chain = trace["admission_event_chain"]
            chain["events"][8]["investigation_id"] = "inv_other_investigation"
            self.resign_w5_event_chain(chain)

        self.rewrite_and_repin_w5_execution_trace(evidence, splice)
        self.assert_alignment_error(
            "w5_admission_event_cross_investigation",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_resigned_event_schema_action_or_rerun_identity_drift(self) -> None:
        evidence = self.create_w5_fixture()

        def schema_drift(trace: dict) -> None:
            chain = trace["admission_event_chain"]
            chain["events"][0]["schema_version"] = "country_outage_p2_s1_w5_admission_event_v0"
            self.resign_w5_event_chain(chain)

        self.rewrite_and_repin_w5_execution_trace(evidence, schema_drift)
        self.assert_alignment_error(
            "w5_admission_event_chain_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()

        def action_drift(trace: dict) -> None:
            chain = trace["admission_event_chain"]
            chain["events"][7]["action"] = "publish_without_runtime_admission"
            self.resign_w5_event_chain(chain)

        self.rewrite_and_repin_w5_execution_trace(evidence, action_drift)
        self.assert_alignment_error(
            "w5_admission_event_action_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()

        def final_commit_drift(trace: dict) -> None:
            chain = trace["admission_event_chain"]
            final = chain["events"][-1]
            final["action_subject_digest"] = "sha256:" + "5" * 64
            self.resign_w5_event_chain(chain)

        self.rewrite_and_repin_w5_execution_trace(evidence, final_commit_drift)
        self.assert_alignment_error(
            "w5_admission_event_final_cas_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        for identity_field, replacement in (
            ("base_investigation_revision", 2),
            ("idempotency_key_digest", "sha256:" + "6" * 64),
        ):
            evidence = self.create_w5_fixture()

            def rerun_identity_drift(trace: dict, *, field=identity_field, value=replacement) -> None:
                chain = trace["admission_event_chain"]
                chain[field] = value
                self.resign_w5_event_chain(chain)

            self.rewrite_and_repin_w5_execution_trace(evidence, rerun_identity_drift)
            self.assert_alignment_error(
                "w5_admission_event_chain_invalid",
                lambda: HOOK.validate_w5_evidence(self.root, evidence),
            )

    def test_w5_rejects_resigned_unresolved_dispatch_or_parameter_drift_event(self) -> None:
        evidence = self.create_w5_fixture()

        def unresolved_dispatch(trace: dict) -> None:
            chain = trace["admission_event_chain"]
            event = next(item for item in chain["events"] if item["event_kind"] == "first_dispatch")
            event["action_subject_digest"] = "sha256:" + "0" * 64
            self.resign_w5_event_chain(chain)

        self.rewrite_and_repin_w5_execution_trace(evidence, unresolved_dispatch)
        self.assert_alignment_error(
            "w5_admission_event_dispatch_unresolved",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()

        def parameter_drift(trace: dict) -> None:
            chain = trace["admission_event_chain"]
            event = next(item for item in chain["events"] if item["event_kind"] == "result_set_published")
            event["parameter_bindings_digest"] = "sha256:" + "0" * 64
            self.resign_w5_event_chain(chain)

        self.rewrite_and_repin_w5_execution_trace(evidence, parameter_drift)
        self.assert_alignment_error(
            "w5_admission_event_parameter_drift",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()

        def drift_freeze(trace: dict) -> None:
            freeze = trace["result_set_receipt_closure"]["freeze_receipt"]
            freeze["content_digest"] = "f" * 64
            freeze["receipt_digest"] = HOOK.object_digest(freeze, {"receipt_digest"})

        self.rewrite_and_repin_w5_execution_trace(evidence, drift_freeze)
        self.assert_alignment_error(
            "w5_result_set_receipt_binding_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_business_schema_or_unlimited_money_masquerade(self) -> None:
        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace.update({"schema_validated_business_unit_ids": []}),
        )
        self.assert_alignment_error(
            "w5_business_schema_runtime_validation_missing",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace["business_unit_execution_records"].pop(),
        )
        self.assert_alignment_error(
            "w5_business_execution_records_missing",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        for removed in ("TOOL-07", "OP-30"):
            with self.subTest(removed=removed):
                evidence = self.create_w5_fixture()

                def remove_one_core_call_consistently(trace: dict) -> None:
                    trace["business_unit_invocation_ids"].remove(removed)
                    trace["schema_validated_business_unit_ids"].remove(removed)
                    trace["business_unit_execution_records"] = [
                        item for item in trace["business_unit_execution_records"]
                        if item["unit_id"] != removed
                    ]
                    trace["plan_node_unit_ids"].remove(removed)

                self.rewrite_and_repin_w5_execution_trace(evidence, remove_one_core_call_consistently)
                self.assert_alignment_error(
                    "w5_core_business_journey_missing",
                    lambda: HOOK.validate_w5_evidence(self.root, evidence),
                )

        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace.update({"monetary_limit_mode": "finite", "max_cost_amount_zero_present": True}),
        )
        self.assert_alignment_error(
            "w5_monetary_limit_mode_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_missing_crash_recovery_or_trusted_planning_port(self) -> None:
        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace.update({"cas_crash_recovery_replayed_same_outcome": False}),
        )
        self.assert_alignment_error(
            "w5_cas_crash_recovery_missing",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace["planning_grounding_port"].update({"constructor_plan_nodes_supported": True}),
        )
        self.assert_alignment_error(
            "w5_trusted_planning_grounding_missing",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace["planning_grounding_port"].update({
                "grounded_execution_recipe_schema_version":
                    "country_outage_p2_s1_grounded_execution_recipe_v1",
            }),
        )
        self.assert_alignment_error(
            "w5_trusted_planning_grounding_missing",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

        evidence = self.create_w5_fixture()
        self.rewrite_and_repin_w5_execution_trace(
            evidence,
            lambda trace: trace.update({"running_cancel_verified": False}),
        )
        self.assert_alignment_error(
            "w5_execution_trace_incomplete",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_callback_seam_even_with_resigned_probe_and_outer_evidence(self) -> None:
        evidence = self.create_w5_fixture()
        probe = evidence["dispatcher_probe"]
        probe["caller_callback_injection_supported"] = True
        probe["receipt_digest"] = HOOK.object_digest(probe, {"receipt_digest"})
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "w5_dispatcher_callback_seam_open",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_missing_artifact_after_full_resign(self) -> None:
        evidence = self.create_w5_fixture()
        evidence["artifact_manifest"].pop()
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "w5_artifact_population_mismatch",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_control_schema_extra_definition(self) -> None:
        self.create_w5_fixture()
        schema = self.load(HOOK.W5_CONTROL_RUNTIME_SCHEMA_PATH)
        schema["$defs"]["unregisteredControl"] = {
            "type": "object", "additionalProperties": False,
        }
        self.write(HOOK.W5_CONTROL_RUNTIME_SCHEMA_PATH, schema)
        self.assert_alignment_error(
            "w5_control_schema_population_mismatch",
            lambda: HOOK._validate_w5_control_runtime_schema(self.root),
        )

    def test_w5_rejects_control_schema_bad_ref_or_open_object(self) -> None:
        self.create_w5_fixture()
        schema = self.load(HOOK.W5_CONTROL_RUNTIME_SCHEMA_PATH)
        schema["$defs"]["gate01Input"] = {"$ref": "#/$defs/gate01Input"}
        self.write(HOOK.W5_CONTROL_RUNTIME_SCHEMA_PATH, schema)
        self.assert_alignment_error(
            "w5_control_schema_ref_invalid",
            lambda: HOOK._validate_w5_control_runtime_schema(self.root),
        )

    def test_w5_rejects_control_schema_cross_unit_identity(self) -> None:
        self.create_w5_fixture()
        schema = self.load(HOOK.W5_CONTROL_RUNTIME_SCHEMA_PATH)
        schema["$defs"]["gate02Output"]["allOf"][1]["properties"]["gate_id"]["const"] = "GATE-01"
        self.write(HOOK.W5_CONTROL_RUNTIME_SCHEMA_PATH, schema)
        self.assert_alignment_error(
            "w5_control_schema_unit_identity_open",
            lambda: HOOK._validate_w5_control_runtime_schema(self.root),
        )

        self.create_w5_fixture()
        schema = self.load(HOOK.W5_CONTROL_RUNTIME_SCHEMA_PATH)
        schema["$defs"]["gateInput"]["additionalProperties"] = True
        self.write(HOOK.W5_CONTROL_RUNTIME_SCHEMA_PATH, schema)
        self.assert_alignment_error(
            "w5_control_schema_open",
            lambda: HOOK._validate_w5_control_runtime_schema(self.root),
        )

    def test_w5_rejects_admission_control_schema_digest_rebinding(self) -> None:
        evidence = self.create_w5_fixture()
        evidence["execution_admission_receipt"]["control_unit_entries"][0]["schema_sha256"] = "f" * 64
        evidence["execution_admission_receipt"]["receipt_digest"] = "sha256:" + HOOK.object_digest(
            evidence["execution_admission_receipt"], {"receipt_digest"}
        )
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "w5_control_schema_binding_mismatch",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_fully_resigned_forged_runner_receipt(self) -> None:
        evidence = self.create_w5_fixture()
        suite_id = "w5-python"
        path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
        receipt = self.load(path)
        receipt["normalized_output"] += "forged success\n"
        receipt["normalized_output_sha256"] = HOOK.sha256_bytes(
            receipt["normalized_output"].encode("utf-8")
        )
        receipt["receipt_digest"] = HOOK.object_digest(receipt, {"receipt_digest"})
        self.write(path, receipt)
        reference = next(item for item in evidence["test_receipts"] if item["suite_id"] == suite_id)
        reference["sha256"] = HOOK.file_sha256(self.root / path)
        reference["receipt_digest"] = receipt["receipt_digest"]
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "wave_test_receipt_untrusted_resign",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_tests_run_count_not_reported_by_real_subprocess_output(self) -> None:
        evidence = self.create_w5_fixture()
        suite_id = "w5-python"
        path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
        receipt = self.load(path)
        receipt["tests_run"] = 19
        receipt["receipt_digest"] = HOOK.object_digest(receipt, {"receipt_digest"})
        self.write(path, receipt)
        sha256 = HOOK.file_sha256(self.root / path)
        _, category, _ = HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id]
        HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id] = ("W5", category, sha256)
        reference = next(item for item in evidence["test_receipts"] if item["suite_id"] == suite_id)
        reference["sha256"] = sha256
        reference["receipt_digest"] = receipt["receipt_digest"]
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "w5_test_count_output_mismatch",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_p2_1_execution_smuggling_after_full_resign(self) -> None:
        evidence = self.create_w5_fixture()
        admission = evidence["execution_admission_receipt"]
        admission["execution_allowed_unit_ids"].append("PLAN-CAP-02")
        admission["receipt_digest"] = "sha256:" + HOOK.object_digest(
            admission,
            {"receipt_digest"},
        )
        evidence["dispatcher_probe"]["execution_admission_receipt_digest"] = admission["receipt_digest"]
        evidence["dispatcher_probe"]["receipt_digest"] = HOOK.object_digest(
            evidence["dispatcher_probe"],
            {"receipt_digest"},
        )
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "w5_execution_population_invalid",
            lambda: HOOK.validate_w5_evidence(self.root, evidence),
        )

    def test_w5_rejects_stale_prior_receipt_even_when_dependency_digest_is_rebound(self) -> None:
        evidence = self.create_w5_fixture()
        baseline = self.load(HOOK.BASELINE_PATH)
        for path in (HOOK.P0_RECEIPT_PATH,):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, target)
        p0 = self.load(HOOK.P0_RECEIPT_PATH)
        current_receipts: dict[str, dict] = {}
        for stage in ("W0", "W1", "W2", "W3", "W4"):
            wave_evidence_path = HOOK.WAVE_EVIDENCE_ROOT / f"{stage}.json"
            target = self.root / wave_evidence_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / wave_evidence_path, target)
            receipt = self.load_from_root(HOOK.WAVE_RECEIPT_ROOT / f"{stage}.json")
            receipt.update({
                "task_sha256": HOOK.file_sha256(self.root / HOOK.TASK_PATH),
                "target_document_sha256": HOOK.file_sha256(self.root / HOOK.TARGET_PATH),
                "phase_plan_sha256": HOOK.file_sha256(self.root / HOOK.PLAN_PATH),
                "baseline_sha256": HOOK.file_sha256(self.root / HOOK.BASELINE_PATH),
                "baseline_content_digest": baseline["content_digest"],
                "hook_sha256": HOOK.file_sha256(HOOK_PATH),
                "hook_tests_sha256": HOOK.file_sha256(self.root / "dev/tests/test_country_outage_p2_s1_implementation_alignment_hook.py"),
                "wave_evidence_sha256": HOOK.file_sha256(target),
                "implementation_candidate_id": self.load(wave_evidence_path)["implementation_candidate_id"],
                "prior_stage_receipt_digests": [
                    p0["receipt_digest"],
                    *[
                        current_receipts[dependency]["receipt_digest"]
                        for dependency in HOOK.stage_prior_dependencies(stage)
                    ],
                ],
                "production_deployed": False,
            })
            receipt["receipt_digest"] = HOOK.object_digest(receipt, {"receipt_digest"})
            current_receipts[stage] = receipt
            self.write(HOOK.WAVE_RECEIPT_ROOT / f"{stage}.json", receipt)
        stale = current_receipts["W4"]
        stale["hook_sha256"] = "f" * 64
        stale["receipt_digest"] = HOOK.object_digest(stale, {"receipt_digest"})
        self.write(HOOK.WAVE_RECEIPT_ROOT / "W4.json", stale)
        evidence["prior_stage_receipt_digests"] = {
            "S1I-P0": p0["receipt_digest"],
            "W1": current_receipts["W1"]["receipt_digest"],
            "W2": current_receipts["W2"]["receipt_digest"],
            "W3": current_receipts["W3"]["receipt_digest"],
            "W4": stale["receipt_digest"],
        }
        self.resign_w5_evidence(evidence)
        self.assert_alignment_error(
            "wave_prior_artifact_binding_mismatch",
            lambda: HOOK.validate_wave(self.root, "W5", baseline),
        )


if __name__ == "__main__":
    unittest.main()
