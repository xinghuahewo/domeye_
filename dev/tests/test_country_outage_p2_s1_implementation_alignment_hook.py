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
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def load(self, relative: Path) -> dict:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

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

    def test_w4_rejects_op33_empty_population_contract_regression(self) -> None:
        schema_path = HOOK.OPERATOR_CONTRACT_SCHEMA_PATH
        target = self.root / schema_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / schema_path, target)
        schema = self.load(schema_path)
        schema["$defs"]["op33InputPayload"]["properties"]["new_prefix_state_rows"]["minItems"] = 1
        self.write(schema_path, schema)
        self.assert_alignment_error(
            "op33_empty_population_contract_open",
            lambda: HOOK._validate_op33_empty_population_contract(self.root),
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


if __name__ == "__main__":
    unittest.main()
