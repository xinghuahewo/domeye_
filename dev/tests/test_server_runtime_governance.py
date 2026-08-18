import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "governance" / "audit-server-runtime-governance.py"
SPEC = importlib.util.spec_from_file_location("audit_server_runtime_governance", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class ServerRuntimeGovernanceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.protected = self.root / "old-domeye"
        self.runtime = self.root / "Domeye-Core-runtime"
        self.dev_data = self.root / "Domeye-Core-dev-data"
        self.data = self.root / "Domeye-Core-data"
        self.source = self.root / "Domeye-Core"
        self.artifacts = self.root / "Domeye-Core-artifacts"
        self.governance = self.root / "Domeye-Core-governance"
        for item in (self.protected, self.runtime, self.dev_data, self.data, self.source, self.artifacts, self.governance):
            item.mkdir()

        self.backend_root = self.runtime / "releases"
        self.agent_root = self.runtime / "country-outage-agent" / "releases"
        self.p1_root = self.runtime / "country-outage-p1-chat" / "releases"
        self.backend_active = self.create_release(self.backend_root, "backend-active")
        self.backend_old = self.create_release(self.backend_root, "backend-old")
        self.agent_active = self.create_release(self.agent_root, "agent-active")
        self.p1_active = self.create_release(self.p1_root, "p1-active")
        self.link(self.runtime / "current", self.backend_active)
        self.link(self.runtime / "country-outage-agent" / "current", self.agent_active)
        self.link(self.runtime / "country-outage-p1-chat" / "current", self.p1_active)

        self.research_runs = self.dev_data / "research-runs"
        self.worktrees = self.dev_data / "research-worktrees"
        self.inputs = self.dev_data / "research-inputs"
        self.overlays = self.dev_data / "overlays"
        for item in (self.research_runs, self.worktrees, self.inputs, self.overlays):
            item.mkdir()
        self.run_in_use = self.create_release(self.research_runs, "run-in-use")
        self.run_locked = self.create_release(self.research_runs, "run-locked")
        (self.run_locked / "active.lock").write_text("fixture lock\n", encoding="utf-8")
        self.run_shared = self.create_release(self.research_runs, "run-shared")
        shared = self.run_shared / "shared.bin"
        shared.write_bytes(b"shared")
        outside_link = self.root / "outside-hardlink.bin"
        os.link(shared, outside_link)

        config = self.runtime / "config"
        config.mkdir()
        secret = config / "runtime.env"
        secret.write_text("SECRET_MARKER_MUST_NOT_APPEAR\n", encoding="utf-8")
        secret.chmod(0o600)

        self.process_root = self.root / "proc"
        self.add_process(123, self.backend_active, {"8": self.run_in_use / "manifest.json"})
        self.mount_info = self.root / "mountinfo"
        self.mount_info.write_text("", encoding="utf-8")
        self.policy = {
            "schemaVersion": AUDIT.POLICY_SCHEMA,
            "expectedHost": socket.gethostname(),
            "processRoot": str(self.process_root),
            "configDirectory": str(config),
            "requiredConfigMode": "0600",
            "protectedRoots": [{"path": str(self.protected), "reason": "fixture"}],
            "managedRoots": [
                {"name": "source", "path": str(self.source)},
                {"name": "runtime", "path": str(self.runtime)},
                {"name": "artifacts", "path": str(self.artifacts)},
                {"name": "data", "path": str(self.data)},
                {"name": "dev", "path": str(self.dev_data)},
                {"name": "governance", "path": str(self.governance)},
            ],
            "runtimeGovernance": {
                "mountInfoPath": str(self.mount_info),
                "maxEntriesPerObject": 200,
                "maxManifestBytes": 1024,
                "manifestFileNames": ["manifest.json"],
                "releaseComponents": [
                    {"name": "backend", "activeLinkPath": str(self.runtime / "current"), "releaseRoot": str(self.backend_root)},
                    {"name": "legacy_agent_sidecar", "activeLinkPath": str(self.runtime / "country-outage-agent" / "current"), "releaseRoot": str(self.agent_root)},
                    {"name": "p1_chat_sidecar", "activeLinkPath": str(self.runtime / "country-outage-p1-chat" / "current"), "releaseRoot": str(self.p1_root)},
                ],
                "developmentDataRoots": [
                    {"name": "research_runs", "path": str(self.research_runs)},
                    {"name": "research_worktrees", "path": str(self.worktrees)},
                    {"name": "research_inputs", "path": str(self.inputs)},
                    {"name": "overlays", "path": str(self.overlays)},
                ],
            },
            "mutationPolicy": {
                "auditWritesServer": False,
                "deleteEnabled": False,
                "moveEnabled": False,
                "restartEnabled": False,
                "productionSwitchEnabled": False,
            },
        }

    def tearDown(self):
        self.temporary.cleanup()

    def create_release(self, root, name):
        release = root / name
        release.mkdir(parents=True)
        (release / "manifest.json").write_text('{"fixture":"identity"}\n', encoding="utf-8")
        return release

    def link(self, link, target):
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)

    def add_process(self, pid, cwd, descriptors):
        process = self.process_root / str(pid)
        (process / "fd").mkdir(parents=True)
        (process / "cwd").symlink_to(cwd)
        (process / "exe").symlink_to("/bin/sh")
        (process / "comm").write_text("fixture-service\n", encoding="utf-8")
        for number, target in descriptors.items():
            (process / "fd" / number).symlink_to(target)

    def test_discovers_runtime_identity_and_blocks_mutation(self):
        result = AUDIT.build_discovery(self.policy)

        self.assertEqual(result["schemaVersion"], AUDIT.SCHEMA)
        self.assertFalse(result["serverWrites"])
        self.assertFalse(result["mutationAuthorized"])
        self.assertEqual(result["gate"]["decision"], "BLOCK_MUTATION")
        backend = next(item for item in result["runtimeComponents"] if item["name"] == "backend")
        self.assertTrue(backend["identityEquation"]["activeLinkValid"])
        self.assertTrue(backend["identityEquation"]["actualProcessCwdBound"])
        self.assertTrue(backend["identityEquation"]["releaseManifestDigestBound"])
        p1 = next(item for item in result["runtimeComponents"] if item["name"] == "p1_chat_sidecar")
        self.assertFalse(p1["identityEquation"]["actualProcessCwdBound"])
        old_release = next(item for item in backend["releases"] if item["releaseId"] == "backend-old")
        self.assertEqual(old_release["retentionState"], "future_quarantine_candidate")
        self.assertFalse(old_release["quarantineAuthorized"])
        self.assertFalse(old_release["deleteAuthorized"])

    def test_development_data_requires_reference_and_hardlink_resolution(self):
        result = AUDIT.build_discovery(self.policy)
        research = next(item for item in result["developmentData"] if item["name"] == "research_runs")
        in_use = next(item for item in research["objects"] if item["objectId"] == "run-in-use")
        locked = next(item for item in research["objects"] if item["objectId"] == "run-locked")
        shared = next(item for item in research["objects"] if item["objectId"] == "run-shared")

        self.assertIn("process_referenced", in_use["protectedClasses"])
        self.assertIn("locked", locked["protectedClasses"])
        self.assertIn("unknown", shared["protectedClasses"])
        self.assertEqual(shared["inventory"]["externalHardLinkCount"], 1)
        self.assertEqual(in_use["candidateReferenceInspection"], "metadata_only_not_proven")

    def test_never_emits_config_contents_or_process_arguments(self):
        encoded = json.dumps(AUDIT.build_discovery(self.policy), ensure_ascii=False)

        self.assertNotIn("SECRET_MARKER_MUST_NOT_APPEAR", encoded)
        self.assertEqual(result := AUDIT.build_discovery(self.policy)["credentialSurface"]["processArgumentInspection"], "not_performed_by_contract")
        self.assertNotIn("environment", encoded)

    def test_policy_rejects_mutation_capability(self):
        self.policy["mutationPolicy"]["moveEnabled"] = True

        with self.assertRaisesRegex(AUDIT.DiscoveryError, "moveEnabled=false"):
            AUDIT.validate_policy(self.policy)


if __name__ == "__main__":
    unittest.main()
