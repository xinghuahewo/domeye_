from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from dev.tools import manage_country_outage_p2_registry as registry


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPO_ROOT / "contracts/agent/country-outage-p2-s0a-lifecycle"
REGISTRY_PATH = CONTRACT_ROOT / "registry-set.json"
SNAPSHOT_PATH = CONTRACT_ROOT / "registry-snapshot.json"


def evidence_directory():
    value = os.environ.get("P2_S0A_EVIDENCE_DIR")
    if not value:
        return None
    path = Path(value).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


class P2S0ARegistryTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.registry_set = load(REGISTRY_PATH)
        self.snapshot = load(SNAPSHOT_PATH)

    def test_migration_is_complete_and_offline_only(self) -> None:
        result = registry.validate_registry_set(self.registry_set, require_active_snapshot=True)
        self.assertEqual(result["capability_count"], 18)
        self.assertEqual(result["execution_unit_count"], 10)
        self.assertEqual(result["active_capability_count"], 18)
        self.assertEqual(result["active_execution_unit_count"], 10)
        self.assertEqual(result["runtime_integration"], "not_implemented")
        units = self.registry_set["execution_unit_registry"]["entries"]
        self.assertEqual(sum(item["unit_id"].startswith("TOOL-") for item in units), 6)
        self.assertEqual(sum(item["unit_id"].startswith("OP-") for item in units), 4)
        op04 = next(item for item in units if item["unit_id"] == "OP-04")
        self.assertEqual(op04["version"], "1.2.0")
        self.assertEqual(op04["dependencies"], [{"unit_id": "TOOL-03", "version": "1.0.0", "relationship": "validated_series_source"}])

    def _active_plan(self):
        capability = next(item for item in self.registry_set["capability_registry"]["entries"] if item["capability_id"] == "CAP-001")
        unit = next(item for item in self.registry_set["execution_unit_registry"]["entries"] if item["unit_id"] == "TOOL-01")
        return {
            "schema_version": "country_outage_p2_s0a_plan_admission_v1",
            "plan_id": "plan-active-001",
            "plan_kind": "GroundingPlan",
            "registry_snapshot_id": self.snapshot["registry_snapshot_id"],
            "registry_revision": self.snapshot["snapshot_payload"]["registry_revision"],
            "event_identity": {
                "event_type": "country_outage",
                "incident_id": "incident-fixture",
                "publication_id": "publication-fixture",
                "revision": 1,
                "collector_id": "rrc25",
                "cohort_id": "cohort-fixture",
                "window_start_utc": "2026-02-27T00:10:00Z",
                "window_end_utc": "2026-03-11T00:00:00Z",
                "data_through": "2026-03-11T00:00:00Z",
                "is_final_in_data_range": False,
                "lifecycle_state": "event_end_unknown",
            },
            "nodes": [
                {
                    "node_id": "node-1",
                    "capability_id": capability["capability_id"],
                    "capability_version": capability["version"],
                    "capability_contract_digest": capability["contract_digest"],
                    "execution_unit_id": unit["unit_id"],
                    "execution_unit_version": unit["version"],
                    "unit_contract_digest": unit["contract_digest"],
                    "unit_implementation_digest": unit["implementation_digest"],
                    "unit_semantic_digest": unit["semantic_digest"],
                }
            ],
        }

    def test_plan_admission_accepts_only_exact_active_snapshot(self) -> None:
        active_plan = self._active_plan()
        result = registry.check_plan(self.snapshot, active_plan)
        self.assertEqual(result["status"], "admitted")
        self.assertFalse(result["execution_started"])
        output = evidence_directory()
        if output is not None:
            dump(output / "plan-admission-input.json", active_plan)
            dump(output / "plan-admission-receipt.json", result)
        wrong_snapshot = self._active_plan()
        wrong_snapshot["registry_snapshot_id"] = "registry-snapshot-sha256:" + "0" * 64
        with self.assertRaises(registry.GovernanceError) as context:
            registry.check_plan(self.snapshot, wrong_snapshot)
        self.assertEqual(context.exception.code, "registry_snapshot_conflict")
        wrong_digest = self._active_plan()
        wrong_digest["nodes"][0]["unit_contract_digest"] = "sha256:" + "f" * 64
        with self.assertRaises(registry.GovernanceError) as context:
            registry.check_plan(self.snapshot, wrong_digest)
        self.assertEqual(context.exception.code, "digest_mismatch")

    def test_non_active_unit_and_capability_are_rejected_before_execution(self) -> None:
        snapshot = copy.deepcopy(self.snapshot)
        unit = next(item for item in snapshot["snapshot_payload"]["execution_unit_registry"]["entries"] if item["unit_id"] == "TOOL-01")
        unit["state"] = "retired"
        snapshot = registry.build_snapshot(
            {
                "candidate_id": snapshot["snapshot_payload"]["candidate_id"],
                "registry_revision": snapshot["snapshot_payload"]["registry_revision"],
                "activation_scope": snapshot["snapshot_payload"]["activation_scope"],
                "runtime_integration": snapshot["snapshot_payload"]["runtime_integration"],
                "capability_registry": snapshot["snapshot_payload"]["capability_registry"],
                "execution_unit_registry": snapshot["snapshot_payload"]["execution_unit_registry"],
            },
            "2026-08-11T09:00:00Z",
        )
        plan = self._active_plan()
        plan["registry_snapshot_id"] = snapshot["registry_snapshot_id"]
        with self.assertRaises(registry.GovernanceError) as context:
            registry.check_plan(snapshot, plan)
        self.assertEqual(context.exception.code, "execution_unit_not_active")
        snapshot = copy.deepcopy(self.snapshot)
        capability = next(item for item in snapshot["snapshot_payload"]["capability_registry"]["entries"] if item["capability_id"] == "CAP-001")
        capability["state"] = "deprecated"
        snapshot = registry.build_snapshot(
            {
                "candidate_id": snapshot["snapshot_payload"]["candidate_id"],
                "registry_revision": snapshot["snapshot_payload"]["registry_revision"],
                "activation_scope": snapshot["snapshot_payload"]["activation_scope"],
                "runtime_integration": snapshot["snapshot_payload"]["runtime_integration"],
                "capability_registry": snapshot["snapshot_payload"]["capability_registry"],
                "execution_unit_registry": snapshot["snapshot_payload"]["execution_unit_registry"],
            },
            "2026-08-11T09:01:00Z",
        )
        plan = self._active_plan()
        plan["registry_snapshot_id"] = snapshot["registry_snapshot_id"]
        with self.assertRaises(registry.GovernanceError) as context:
            registry.check_plan(snapshot, plan)
        self.assertEqual(context.exception.code, "capability_not_active")

    def test_missing_null_and_tampered_digest_fail_closed(self) -> None:
        missing = copy.deepcopy(self.registry_set)
        del missing["capability_registry"]["entries"][0]["contract_digest"]
        with self.assertRaises(registry.GovernanceError) as context:
            registry.validate_registry_set(missing)
        self.assertEqual(context.exception.code, "schema_invalid")
        null_evidence = copy.deepcopy(self.registry_set)
        null_evidence["execution_unit_registry"]["entries"][0]["certification_evidence"] = None
        with self.assertRaises(registry.GovernanceError) as context:
            registry.validate_registry_set(null_evidence)
        self.assertEqual(context.exception.code, "certification_evidence_missing")
        tampered = copy.deepcopy(self.registry_set)
        tampered["execution_unit_registry"]["entries"][0]["contract_material"]["purpose"] += "（被篡改）"
        with self.assertRaises(registry.GovernanceError) as context:
            registry.validate_registry_set(tampered)
        self.assertEqual(context.exception.code, "digest_mismatch")

    def _new_unit(self, state: str = "discovered"):
        base = copy.deepcopy(next(item for item in self.registry_set["execution_unit_registry"]["entries"] if item["unit_id"] == "OP-01"))
        base["unit_id"] = "OP-99"
        base["version"] = "0.1.0"
        base["state"] = state
        base["name"] = "offline_fixture_operator"
        base["purpose"] = "仅用于离线治理生命周期测试"
        base["capability_ids"] = ["CAP-001"]
        base["contract_material"]["unit_id"] = "OP-99"
        base["contract_material"]["name"] = base["name"]
        base["contract_material"]["purpose"] = base["purpose"]
        base["contract_material"]["capability_ids"] = ["CAP-001"]
        base["semantic_material"]["unit_id"] = "OP-99"
        base["semantic_material"]["name"] = base["name"]
        base["semantic_material"]["purpose"] = base["purpose"]
        base["semantic_material"]["capability_ids"] = ["CAP-001"]
        base["contract_digest"] = registry.digest_value(base["contract_material"])
        base["semantic_digest"] = registry.digest_value(base["semantic_material"])
        base["implementation_digest"] = registry.digest_value(base["implementation_files"])
        base["certification_evidence"] = None
        base["replacement"] = None
        base["migration_deadline"] = None
        base["tombstone"] = None
        base["lifecycle_history"] = registry.migration_history("2026-08-11T09:10:00Z", state)
        return base

    def test_create_update_impact_and_revision_cas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "registry-set.json"
            entry_path = root / "entry.json"
            dump(state_path, self.registry_set)
            dump(entry_path, self._new_unit())
            receipt = registry.operate_create(
                state_path, entry_path, "execution_unit", 1, "builder", "registry:propose",
                "创建离线治理测试单元", "req-create", "2026-08-11T09:11:00Z", root / "create-receipt.json",
            )
            self.assertEqual(receipt["after_revision"], 2)
            state = load(state_path)
            self.assertEqual(registry._find_entry(state, "execution_unit", "OP-99", "0.1.0")["state"], "discovered")
            with self.assertRaises(registry.GovernanceError) as context:
                registry.operate_create(
                    state_path, entry_path, "execution_unit", 1, "builder", "registry:propose",
                    "重复创建应失败", "req-conflict", "2026-08-11T09:12:00Z", None,
                )
            self.assertEqual(context.exception.code, "registry_revision_conflict")
            proposed = self._new_unit("proposed")
            proposed["version"] = "0.1.1"
            proposed["implementation_files"] = copy.deepcopy(proposed["implementation_files"]) + [{"path": "fixture/new.py", "sha256": "sha256:" + "1" * 64}]
            proposed["implementation_digest"] = registry.digest_value(proposed["implementation_files"])
            dump(entry_path, proposed)
            receipt = registry.operate_update(
                state_path, entry_path, "execution_unit", 2, "builder", "registry:propose",
                "提交向后兼容实现修订", "req-update", "2026-08-11T09:13:00Z", root / "update-receipt.json",
            )
            self.assertEqual(receipt["after_revision"], 3)
            self.assertEqual(receipt["impact"]["compatibility"]["declared_semver_class"], "patch")

    def test_full_state_path_delete_and_id_reuse_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = copy.deepcopy(self.registry_set)
            new_unit = self._new_unit()
            state["execution_unit_registry"]["entries"].append(new_unit)
            state["registry_revision"] = 2
            state["previous_snapshot_id"] = state["active_snapshot_id"]
            state["active_snapshot_id"] = None
            state_path = root / "registry-set.json"
            dump(state_path, state)
            transitions = [
                ("proposed", "registry:propose"),
                ("oracle_ready", "registry:certify"),
                ("certified", "registry:certify"),
                ("active", "registry:activate"),
                ("deprecated", "registry:activate"),
                ("retired", "registry:retire"),
            ]
            certification_path = root / "certification.json"
            evidence = copy.deepcopy(self.registry_set["execution_unit_registry"]["entries"][0]["certification_evidence"])
            dump(certification_path, evidence)
            revision = 2
            for index, (target, role) in enumerate(transitions, 1):
                kwargs = {}
                if target == "certified":
                    kwargs["certification_path"] = certification_path
                if target == "deprecated":
                    kwargs["replacement"] = "no_replacement:test_fixture"
                    kwargs["migration_deadline"] = "2026-08-12T00:00:00Z"
                snapshot_out = root / f"snapshot-{index}.json" if target == "active" else None
                registry.operate_transition(
                    state_path, "execution_unit", "OP-99", "0.1.0", target, revision,
                    "governor", role, f"测试迁移到 {target}", f"req-{target}",
                    f"2026-08-11T09:{20 + index:02d}:00Z", None,
                    kwargs.get("certification_path"), kwargs.get("replacement"),
                    kwargs.get("migration_deadline"), snapshot_out,
                )
                revision += 1
            registry.operate_delete(
                state_path, "execution_unit", "OP-99", "0.1.0", revision,
                "governor", "registry:tombstone", "完成离线 fixture 退役后的载荷清理",
                "req-delete", "2026-08-11T09:40:00Z", root / "delete-receipt.json",
            )
            final_state = load(state_path)
            entry = registry._find_entry(final_state, "execution_unit", "OP-99", "0.1.0")
            self.assertEqual(entry["state"], "tombstoned")
            self.assertTrue(entry["tombstone"]["id_reuse_forbidden"])
            self.assertIsNone(entry["contract_material"])
            output = evidence_directory()
            if output is not None:
                dump(
                    output / "tombstone-receipt.json",
                    {
                        "schema_version": "country_outage_p2_s0a_tombstone_evidence_v1",
                        "status": "applied",
                        "stable_id": "OP-99",
                        "version": "0.1.0",
                        "tombstone": entry["tombstone"],
                        "governance_receipt": load(root / "delete-receipt.json"),
                    },
                )
            dump(root / "reuse.json", self._new_unit())
            with self.assertRaises(registry.GovernanceError) as context:
                registry.operate_create(
                    state_path, root / "reuse.json", "execution_unit", final_state["registry_revision"],
                    "builder", "registry:propose", "尝试复用 Tombstone ID 应失败",
                    "req-reuse", "2026-08-11T09:41:00Z", None,
                )
            self.assertEqual(context.exception.code, "stable_id_reused")

    def _registry_with_new_op04(self):
        value = copy.deepcopy(self.registry_set)
        old_unit = registry._find_entry(value, "execution_unit", "OP-04", "1.2.0")
        old_cap = registry._find_entry(value, "capability", "CAP-TREND-001", "1.0.0")
        registry._append_transition(old_unit, "deprecated", "fixture", "准备单单元回滚 fixture", "2026-08-11T10:00:00Z")
        old_unit["replacement"] = {"target": "OP-04@1.2.1"}
        old_unit["migration_deadline"] = "2026-08-12T00:00:00Z"
        new_unit = copy.deepcopy(old_unit)
        new_unit["version"] = "1.2.1"
        new_unit["state"] = "active"
        new_unit["contract_material"]["operator_contract"]["operator_version"] = "1.2.1"
        new_unit["contract_material"]["integration_contract"]["operator"]["operator_version"] = "1.2.1"
        new_unit["semantic_material"]["operator_version"] = "1.2.1"
        new_unit["contract_digest"] = registry.digest_value(new_unit["contract_material"])
        new_unit["semantic_digest"] = registry.digest_value(new_unit["semantic_material"])
        new_unit["replacement"] = None
        new_unit["migration_deadline"] = None
        new_unit["lifecycle_history"] = registry.migration_history("2026-08-11T10:01:00Z", "active")
        value["execution_unit_registry"]["entries"].append(new_unit)
        registry._append_transition(old_cap, "deprecated", "fixture", "准备单单元回滚 fixture", "2026-08-11T10:00:00Z")
        old_cap["replacement"] = {"target": "CAP-TREND-001@1.0.1"}
        old_cap["migration_deadline"] = "2026-08-12T00:00:00Z"
        new_cap = copy.deepcopy(old_cap)
        new_cap["version"] = "1.0.1"
        new_cap["state"] = "active"
        new_cap["execution_units"] = [{
            "unit_id": "OP-04",
            "version": "1.2.1",
            "contract_digest": new_unit["contract_digest"],
            "implementation_digest": new_unit["implementation_digest"],
            "semantic_digest": new_unit["semantic_digest"],
        }]
        new_cap["implementation_digest"] = registry.digest_value(new_cap["execution_units"])
        new_cap["replacement"] = None
        new_cap["migration_deadline"] = None
        new_cap["lifecycle_history"] = registry.migration_history("2026-08-11T10:01:00Z", "active")
        value["capability_registry"]["entries"].append(new_cap)
        value["registry_revision"] = 2
        value["previous_snapshot_id"] = value["active_snapshot_id"]
        snapshot = registry.build_snapshot(value, "2026-08-11T10:02:00Z")
        value["active_snapshot_id"] = snapshot["registry_snapshot_id"]
        value["snapshot_history"].append(snapshot["registry_snapshot_id"])
        registry.validate_registry_set(value, require_active_snapshot=True)
        return value, snapshot

    def test_single_unit_and_whole_edition_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state, current_snapshot = self._registry_with_new_op04()
            state_path = root / "registry-set.json"
            dump(state_path, state)
            dump(root / "current-snapshot.json", current_snapshot)
            dump(root / "original-snapshot.json", self.snapshot)
            unit_receipt = registry.operate_rollback_unit(
                state_path, "OP-04", "1.2.0", 2, "rollback-operator",
                "registry:rollback", "OP-04 新版本受控回退到已认证 1.2.0",
                "req-rollback-unit", "2026-08-11T10:10:00Z",
                root / "unit-rollback-snapshot.json", root / "unit-rollback-receipt.json",
            )
            self.assertEqual(unit_receipt["status"], "applied")
            after_unit = load(state_path)
            self.assertEqual(after_unit["registry_revision"], 3)
            self.assertEqual(registry._find_entry(after_unit, "execution_unit", "OP-04", "1.2.0")["state"], "active")
            active_caps = [entry for entry in after_unit["capability_registry"]["entries"] if entry["capability_id"] == "CAP-TREND-001" and entry["state"] == "active"]
            self.assertEqual(len(active_caps), 1)
            self.assertEqual(active_caps[0]["execution_units"][0]["version"], "1.2.0")
            edition_receipt = registry.operate_rollback_edition(
                state_path, root / "original-snapshot.json", 3, "rollback-operator",
                "registry:rollback", "恢复完整的初始离线候选快照",
                "req-rollback-edition", "2026-08-11T10:20:00Z",
                root / "edition-rollback-snapshot.json", root / "edition-rollback-receipt.json",
            )
            self.assertEqual(edition_receipt["impact"]["snapshot_mode"], "whole_edition")
            after_edition = load(state_path)
            self.assertEqual(after_edition["registry_revision"], 4)
            self.assertEqual(len([entry for entry in after_edition["execution_unit_registry"]["entries"] if entry["unit_id"] == "OP-04"]), 1)
            registry.validate_registry_set(after_edition, require_active_snapshot=True)
            output = evidence_directory()
            if output is not None:
                dump(output / "single-unit-rollback-receipt.json", unit_receipt)
                dump(output / "whole-edition-rollback-receipt.json", edition_receipt)
                dump(output / "single-unit-rollback-snapshot.json", load(root / "unit-rollback-snapshot.json"))
                dump(output / "whole-edition-rollback-snapshot.json", load(root / "edition-rollback-snapshot.json"))

    def test_boundary_rejects_non_rrc25_capability(self) -> None:
        value = copy.deepcopy(self.registry_set)
        value["capability_registry"]["entries"][0]["identity_constraints"]["collector_id"] = "external"
        with self.assertRaises(registry.GovernanceError) as context:
            registry.validate_registry_set(value)
        self.assertEqual(context.exception.code, "boundary_violation")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(P2S0ARegistryTest)
    outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt_path = os.environ.get("P2_S0A_TEST_RECEIPT")
    if receipt_path:
        dump(
            Path(receipt_path).resolve(),
            {
                "schema_version": "country_outage_p2_s0a_registry_test_receipt_v1",
                "status": "passed" if outcome.wasSuccessful() else "failed",
                "candidate_id": load(CONTRACT_ROOT / "candidate.json")["candidate_id"],
                "test_count": outcome.testsRun,
                "failure_count": len(outcome.failures),
                "error_count": len(outcome.errors),
                "oracle_categories": [
                    "normal",
                    "missing",
                    "null",
                    "wrong_identity",
                    "unavailable",
                    "boundary",
                    "migration",
                    "tamper",
                    "rollback",
                    "plan_admission",
                ],
                "runtime_integration": "not_implemented",
                "production_deployed": False,
                "at_utc": os.environ.get("P2_S0A_TEST_AT", "not_recorded"),
            },
        )
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
