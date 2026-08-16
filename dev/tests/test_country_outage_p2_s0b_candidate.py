from __future__ import annotations

import json
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

from dev.tools import build_country_outage_p2_s0b_candidate as builder


REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_SNAPSHOT = REPO_ROOT / builder.BASE_SNAPSHOT
CONTRACT_ROOT = REPO_ROOT / builder.RUNTIME_ROOT
SNAPSHOT_PATH = CONTRACT_ROOT / "registry-snapshot.json"
CANDIDATE_PATH = CONTRACT_ROOT / "candidate.json"


HOOK_PATH = REPO_ROOT / ".codex/hooks/country_outage_agent_p2_s0b_alignment.py"
HOOK_SPEC = importlib.util.spec_from_file_location("p2_s0b_alignment_for_test", HOOK_PATH)
if HOOK_SPEC is None or HOOK_SPEC.loader is None:
    raise RuntimeError("无法装载 P2-S0B Alignment Hook")
hook = importlib.util.module_from_spec(HOOK_SPEC)
HOOK_SPEC.loader.exec_module(hook)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class P2S0BCandidateTest(unittest.TestCase):
    maxDiff = None

    def test_candidate_is_complete_local_and_not_deployed(self) -> None:
        snapshot = load(SNAPSHOT_PATH)
        candidate = load(CANDIDATE_PATH)
        payload = snapshot["snapshot_payload"]
        capabilities = payload["capability_registry"]["entries"]
        units = payload["execution_unit_registry"]["entries"]
        self.assertEqual(len(capabilities), 18)
        self.assertEqual(len(units), 10)
        self.assertEqual(sum(item["unit_id"].startswith("TOOL-") for item in units), 6)
        self.assertEqual(sum(item["unit_id"].startswith("OP-") for item in units), 4)
        self.assertEqual(candidate["runtime_integration"], "implemented_not_deployed")
        self.assertEqual(candidate["activation_scope"], "runtime_candidate_shadow_only")
        self.assertFalse(candidate["production_deployed"])
        self.assertFalse(candidate["prod32_switched"])

    def test_snapshot_content_address_uses_cross_language_canonical_numbers(self) -> None:
        snapshot = load(SNAPSHOT_PATH)
        expected = builder.digest_value(snapshot["snapshot_payload"])
        self.assertEqual(snapshot["snapshot_digest"], expected)
        self.assertEqual(
            snapshot["registry_snapshot_id"],
            "registry-snapshot-sha256:" + expected.split(":", 1)[1],
        )
        self.assertEqual(
            builder.canonical_text({"v": 0.000003, "i": 10}),
            '{"i":1e1,"v":3e-6}',
        )

    def test_build_is_deterministic_and_does_not_modify_s0a(self) -> None:
        before = builder.digest_file(BASE_SNAPSHOT)
        first = builder.build(REPO_ROOT, "2026-08-11T08:00:00Z")
        second = builder.build(REPO_ROOT, "2026-08-11T08:00:00Z")
        self.assertEqual(first, second)
        self.assertEqual(builder.digest_file(BASE_SNAPSHOT), before)

    def test_candidate_identity_binds_all_runtime_sources(self) -> None:
        candidate = load(CANDIDATE_PATH)
        material = candidate["source_identity"]
        expected_paths = [
            str(builder.RUNTIME_ROOT / "runtime-contract.json"),
            str(builder.RUNTIME_ROOT / "runtime-plan-admission.schema.json"),
            str(builder.RUNTIME_ROOT / "shadow-oracle.json"),
            *builder.RUNTIME_SOURCES,
        ]
        self.assertEqual([item["path"] for item in material["runtime_material"]], expected_paths)
        for item in material["runtime_material"]:
            self.assertEqual(item["sha256"], builder.digest_file(REPO_ROOT / item["path"]))
        identity_digest = builder.digest_value(material)
        self.assertEqual(candidate["source_identity_digest"], identity_digest)
        self.assertEqual(candidate["candidate_id"], "p2-s0b-" + identity_digest.split(":", 1)[1][:16])

    def test_candidate_artifact_digests_are_current(self) -> None:
        candidate = load(CANDIDATE_PATH)
        for relative, expected in candidate["artifact_digests"].items():
            self.assertEqual(builder.digest_file(CONTRACT_ROOT / relative), expected)

    def test_versions_contracts_and_semantics_match_s0a(self) -> None:
        base = load(BASE_SNAPSHOT)["snapshot_payload"]
        current = load(SNAPSHOT_PATH)["snapshot_payload"]
        for registry_name, id_field in (
            ("capability_registry", "capability_id"),
            ("execution_unit_registry", "unit_id"),
        ):
            base_entries = {
                (item[id_field], item["version"]): item
                for item in base[registry_name]["entries"]
            }
            current_entries = {
                (item[id_field], item["version"]): item
                for item in current[registry_name]["entries"]
            }
            self.assertEqual(set(current_entries), set(base_entries))
            for identity, item in current_entries.items():
                self.assertEqual(item["contract_digest"], base_entries[identity]["contract_digest"])
                self.assertEqual(item["semantic_digest"], base_entries[identity]["semantic_digest"])

    def test_implementation_manifests_match_current_files(self) -> None:
        units = load(SNAPSHOT_PATH)["snapshot_payload"]["execution_unit_registry"]["entries"]
        for unit in units:
            manifest = unit["implementation_files"]
            self.assertTrue(manifest)
            for item in manifest:
                self.assertEqual(item["sha256"], builder.digest_file(REPO_ROOT / item["path"]))
            self.assertEqual(unit["implementation_digest"], builder.digest_value(manifest))

    def test_unsafe_output_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            actual = root / "actual.json"
            actual.write_text("{}\n", encoding="utf-8")
            unsafe = root / "unsafe.json"
            unsafe.symlink_to(actual)
            with self.assertRaises(builder.CandidateBuildError):
                builder.write_atomic(unsafe, {"status": "must_not_write"})
            self.assertEqual(actual.read_text(encoding="utf-8"), "{}\n")

    def test_alignment_hook_detects_stage_receipt_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stage.json"
            payload = {
                "stage": "S0B-1",
                "status": "alignment_passed",
                "candidate_id": "p2-s0b-fixture",
                "task_spec_digest": "sha256:" + "1" * 64,
                "plan_digest": "sha256:" + "2" * 64,
            }
            hook.write_receipt(path, payload, "2026-08-11T08:00:00Z")
            value = load(path)
            value["status"] = "alignment_blocked"
            path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(hook.AlignmentError) as context:
                hook.verify_stage_receipt(
                    path,
                    "S0B-1",
                    payload["task_spec_digest"],
                    payload["plan_digest"],
                    payload["candidate_id"],
                )
            self.assertEqual(context.exception.code, "stage_receipt_digest_mismatch")

    def test_alignment_hook_detects_candidate_source_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(CONTRACT_ROOT, root / builder.RUNTIME_ROOT)
            candidate = load(CANDIDATE_PATH)
            for item in candidate["source_identity"]["runtime_material"]:
                source = REPO_ROOT / item["path"]
                target = root / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copy2(source, target)
            drifted = root / builder.RUNTIME_SOURCES[0]
            drifted.write_text(drifted.read_text(encoding="utf-8") + "\n// tampered\n", encoding="utf-8")
            with self.assertRaises(hook.AlignmentError) as context:
                hook.check_candidate(root, [])
            self.assertEqual(context.exception.code, "candidate_source_drift")


if __name__ == "__main__":
    unittest.main()
