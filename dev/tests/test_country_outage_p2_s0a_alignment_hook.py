from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / ".codex/hooks/country_outage_agent_p2_s0a_alignment.py"
CONTRACT_REL = Path("contracts/agent/country-outage-p2-s0a-lifecycle")
DOC_REL = Path("docs/agent/P2-组合式调查/Tool与Operator生命周期治理")
EVIDENCE_REL = Path("evaluation/country-outage/p2-s0a-lifecycle")


def load_hook():
    spec = importlib.util.spec_from_file_location("p2_s0a_alignment_hook_under_test", HOOK_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法装载 Alignment Hook")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = load_hook()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


class P2S0AAlignmentHookTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(REPO_ROOT / CONTRACT_REL, self.root / CONTRACT_REL)
        shutil.copytree(REPO_ROOT / DOC_REL, self.root / DOC_REL)
        shutil.copytree(REPO_ROOT / EVIDENCE_REL, self.root / EVIDENCE_REL)
        for relative in (
            Path(".codex/hooks/country_outage_agent_p2_s0a_alignment.py"),
            Path("dev/tools/manage_country_outage_p2_registry.py"),
        ):
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / relative, destination)

    def assert_alignment_error(self, expected_code: str) -> hook.AlignmentError:
        with self.assertRaises(hook.AlignmentError) as context:
            hook.run_alignment(self.root, "S0A-4")
        self.assertEqual(context.exception.code, expected_code)
        return context.exception

    def test_untampered_stage_passes_with_same_candidate(self) -> None:
        result = hook.run_alignment(self.root, "S0A-4")
        candidate = load(self.root / CONTRACT_REL / "candidate.json")
        self.assertEqual(result["status"], "alignment_passed")
        self.assertEqual(result["candidate_id"], candidate["candidate_id"])
        self.assertEqual(result["runtime_integration"], "not_implemented")
        self.assertFalse(result["production_deployed"])

    def test_task_spec_marker_tamper_is_rejected(self) -> None:
        path = self.root / DOC_REL / "Task-Spec-最终验收文档.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "country-outage-agent-p2-s0a-lifecycle-governance-v1",
                "tampered-spec-version",
                1,
            ),
            encoding="utf-8",
        )
        self.assert_alignment_error("task_spec_marker_missing")

    def test_candidate_bound_artifact_tamper_is_rejected(self) -> None:
        path = self.root / CONTRACT_REL / "migration-map.json"
        value = load(path)
        value["tampered"] = True
        dump(path, value)
        self.assert_alignment_error("candidate_artifact_digest_mismatch")

    def test_registry_contract_material_tamper_is_rejected(self) -> None:
        path = self.root / CONTRACT_REL / "registry-set.json"
        value = load(path)
        value["capability_registry"]["entries"][0]["contract_material"]["user_outcome"] += "（篡改）"
        dump(path, value)
        self.assert_alignment_error("digest_mismatch")

    def test_offline_boundary_tamper_is_rejected(self) -> None:
        path = self.root / CONTRACT_REL / "lifecycle-policy.json"
        value = load(path)
        value["runtime_integration"] = "production"
        dump(path, value)
        self.assert_alignment_error("boundary_violation")

    def test_reviewer_role_collision_is_rejected_after_valid_reseal(self) -> None:
        path = self.root / EVIDENCE_REL / "product-semantic-final-review.json"
        value = load(path)
        value["reviewer_role_id"] = value["builder_role_id"]
        value.pop("receipt_digest", None)
        value["receipt_digest"] = hook.digest_value(value)
        dump(path, value)
        self.assert_alignment_error("reviewer_not_independent")

    def test_previous_stage_candidate_rebinding_is_rejected(self) -> None:
        path = self.root / EVIDENCE_REL / "stages/S0A-2.json"
        value = load(path)
        value["candidate_id"] = "p2-s0a-0000000000000000"
        value.pop("receipt_digest", None)
        value["receipt_digest"] = hook.digest_value(value)
        dump(path, value)
        self.assert_alignment_error("stage_receipt_candidate_drift")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(P2S0AAlignmentHookTest)
    outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt_path = os.environ.get("P2_S0A_ALIGNMENT_TEST_RECEIPT")
    if receipt_path:
        dump(
            Path(receipt_path).resolve(),
            {
                "schema_version": "country_outage_p2_s0a_alignment_test_receipt_v1",
                "status": "passed" if outcome.wasSuccessful() else "failed",
                "candidate_id": load(REPO_ROOT / CONTRACT_REL / "candidate.json")["candidate_id"],
                "test_count": outcome.testsRun,
                "failure_count": len(outcome.failures),
                "error_count": len(outcome.errors),
                "negative_cases": [
                    "task_spec_marker_tamper",
                    "candidate_artifact_tamper",
                    "registry_contract_material_tamper",
                    "offline_boundary_tamper",
                    "reviewer_role_collision",
                    "previous_stage_candidate_rebinding",
                ],
                "runtime_integration": "not_implemented",
                "production_deployed": False,
                "at_utc": os.environ.get("P2_S0A_ALIGNMENT_TEST_AT", "not_recorded"),
            },
        )
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
