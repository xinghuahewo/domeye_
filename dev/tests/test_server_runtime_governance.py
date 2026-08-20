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
        self.interactive_agent_root = self.runtime / "country-outage-interactive-agent" / "releases"
        self.legacy_p1_chat_root = self.runtime / "country-outage-p1-chat" / "releases"
        self.backend_active = self.create_release(self.backend_root, "backend-active")
        self.backend_old = self.create_release(self.backend_root, "backend-old")
        (self.backend_old / "Pipfile.lock").write_text("static dependency lock\n", encoding="utf-8")
        self.agent_active = self.create_release(self.agent_root, "agent-active")
        self.interactive_agent_active = self.create_release(
            self.interactive_agent_root, "interactive-agent-active"
        )
        self.legacy_p1_chat_active = self.create_release(
            self.legacy_p1_chat_root, "legacy-p1-chat-active"
        )
        self.link(self.runtime / "current", self.backend_active)
        self.link(self.runtime / "country-outage-agent" / "current", self.agent_active)
        self.link(
            self.runtime / "country-outage-interactive-agent" / "current",
            self.interactive_agent_active,
        )
        self.link(
            self.runtime / "country-outage-p1-chat" / "current",
            self.legacy_p1_chat_active,
        )

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
        self.add_process(124, self.root, {}, executable="missing-executable")
        self.add_process(125, self.root, {"9": self.root / "outside-disappeared"})
        self.mount_info = self.root / "mountinfo"
        self.mount_info.write_text("", encoding="utf-8")
        active_lock = self.run_locked / "active.lock"
        lock_stat = active_lock.stat()
        self.lock_info = self.process_root / "locks"
        self.lock_info.write_text(
            f"1: POSIX ADVISORY WRITE 123 {os.major(lock_stat.st_dev):02x}:{os.minor(lock_stat.st_dev):02x}:{lock_stat.st_ino} 0 EOF\\n",
            encoding="utf-8",
        )
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
                "rollbackStateRequiredUid": os.getuid(),
                "rollbackStateRequiredGid": os.getgid(),
                "rollbackStateRequiredMode": "0600",
                "manifestFileNames": ["manifest.json"],
                "releaseComponents": [
                    {"name": "backend", "activeLinkPath": str(self.runtime / "current"), "releaseRoot": str(self.backend_root)},
                    {"name": "legacy_agent_sidecar", "activeLinkPath": str(self.runtime / "country-outage-agent" / "current"), "releaseRoot": str(self.agent_root)},
                    {
                        "name": "interactive_agent_sidecar",
                        "activeLinkPath": str(
                            self.runtime / "country-outage-interactive-agent" / "current"
                        ),
                        "releaseRoot": str(self.interactive_agent_root),
                    },
                    {
                        "name": "legacy_p1_chat_sidecar",
                        "activeLinkPath": str(
                            self.runtime / "country-outage-p1-chat" / "current"
                        ),
                        "releaseRoot": str(self.legacy_p1_chat_root),
                        "routingState": "retained_not_routed",
                        "governanceMode": "read_only",
                    },
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

    def add_process(self, pid, cwd, descriptors, executable="/bin/sh"):
        process = self.process_root / str(pid)
        (process / "fd").mkdir(parents=True)
        (process / "cwd").symlink_to(cwd)
        (process / "exe").symlink_to(executable)
        (process / "comm").write_text("fixture-service\n", encoding="utf-8")
        for number, target in descriptors.items():
            (process / "fd" / number).symlink_to(target)

    def test_discovers_runtime_identity_and_blocks_mutation(self):
        result = AUDIT.build_discovery(self.policy)

        self.assertEqual(result["schemaVersion"], AUDIT.SCHEMA)
        self.assertFalse(result["serverWrites"])
        self.assertFalse(result["mutationAuthorized"])
        self.assertEqual(result["gate"]["decision"], "BLOCK_MUTATION")
        self.assertTrue(result["processPathCoverage"]["coverageComplete"])
        self.assertIn(124, result["processPathCoverage"]["executableUnavailablePids"])
        backend = next(item for item in result["runtimeComponents"] if item["name"] == "backend")
        self.assertTrue(backend["identityEquation"]["activeLinkValid"])
        self.assertTrue(backend["identityEquation"]["actualProcessCwdBound"])
        self.assertTrue(backend["identityEquation"]["releaseManifestDigestBound"])
        interactive_agent = next(
            item
            for item in result["runtimeComponents"]
            if item["name"] == "interactive_agent_sidecar"
        )
        self.assertFalse(interactive_agent["identityEquation"]["actualProcessCwdBound"])
        legacy_p1_chat = next(
            item
            for item in result["runtimeComponents"]
            if item["name"] == "legacy_p1_chat_sidecar"
        )
        self.assertEqual(legacy_p1_chat["routingState"], "retained_not_routed")
        self.assertEqual(legacy_p1_chat["governanceMode"], "read_only")
        legacy_active = next(
            item
            for item in legacy_p1_chat["releases"]
            if item["releaseId"] == "legacy-p1-chat-active"
        )
        self.assertEqual(legacy_active["retentionState"], "protected_or_unknown")
        self.assertIn("active", legacy_active["protectedClasses"])
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
        self.assertIn(str(self.run_locked / "active.lock"), locked["inventory"]["activeLockPaths"])
        self.assertIn("unknown", shared["protectedClasses"])
        self.assertEqual(shared["inventory"]["externalHardLinkCount"], 1)
        # 未请求第二次观察时，S5 明确保持 not_requested。
        self.assertEqual(in_use["candidateReferenceInspection"], "not_requested")

    def test_unresolved_fd_inside_managed_root_blocks_coverage(self):
        self.add_process(126, self.root, {"10": self.backend_old / "missing-in-managed-root"})

        snapshot = AUDIT.process_path_snapshot(
            self.process_root,
            [
                self.backend_root,
                self.agent_root,
                self.interactive_agent_root,
                self.legacy_p1_chat_root,
                self.research_runs,
            ],
        )

        self.assertFalse(snapshot["coverageComplete"])
        self.assertIn(126, snapshot["unreadablePids"])

    def test_active_release_manifest_protects_rollback_and_accepted_evidence(self):
        rollback = self.create_release(self.interactive_agent_root, "interactive-agent-rollback")
        (self.interactive_agent_active / "RELEASE-MANIFEST.json").write_text(
            json.dumps(
                {
                    "release_id": "interactive-agent-active",
                    "rollback": {"release_id": "interactive-agent-rollback"},
                    "checks": {"release": "verified"},
                }
            ),
            encoding="utf-8",
        )
        (rollback / "RELEASE-MANIFEST.json").write_text(
            json.dumps(
                {
                    "release_id": "interactive-agent-rollback",
                    "checks": {"release": "passed"},
                }
            ),
            encoding="utf-8",
        )
        self.policy["runtimeGovernance"]["manifestFileNames"].append("RELEASE-MANIFEST.json")

        result = AUDIT.build_discovery(self.policy)
        interactive_agent = next(
            item
            for item in result["runtimeComponents"]
            if item["name"] == "interactive_agent_sidecar"
        )
        rollback_release = next(
            item
            for item in interactive_agent["releases"]
            if item["releaseId"] == "interactive-agent-rollback"
        )

        self.assertEqual(
            interactive_agent["activeRollbackReleaseIds"], ["interactive-agent-rollback"]
        )
        self.assertTrue(interactive_agent["rollbackReferenceCoverageComplete"])
        self.assertEqual(rollback_release["retentionState"], "protected_or_unknown")
        self.assertIn("rollback", rollback_release["protectedClasses"])
        self.assertIn("accepted_evidence", rollback_release["protectedClasses"])

    def test_v2_manifest_protects_nested_previous_and_accepted_evidence(self):
        previous_id = "interactive-agent-v2-previous"
        previous = self.create_release(self.interactive_agent_root, previous_id)
        accepted = {
            "evaluation_phase": "formal",
            "acceptance_state": "accepted",
            "dg1_decision": "GO",
            "record_id": f"acceptance-record-sha256:{'a' * 64}",
            "record_sha256": f"sha256:{'b' * 64}",
        }
        (self.interactive_agent_active / "RELEASE-MANIFEST.json").write_text(
            json.dumps(
                {
                    "schema_version": "domeye_interactive_agent_release_manifest_v2",
                    "release_id": "interactive-agent-active",
                    "acceptance": accepted,
                    "rollback": {
                        "mode": "same_schema_only",
                        "previous_release_id": previous_id,
                    },
                }
            ),
            encoding="utf-8",
        )
        (previous / "RELEASE-MANIFEST.json").write_text(
            json.dumps(
                {
                    "schema_version": "domeye_interactive_agent_release_manifest_v2",
                    "release_id": previous_id,
                    "acceptance": accepted,
                    "rollback": {
                        "mode": "fail_closed",
                        "previous_release_id": None,
                    },
                }
            ),
            encoding="utf-8",
        )
        self.policy["runtimeGovernance"]["manifestFileNames"].append(
            "RELEASE-MANIFEST.json"
        )

        result = AUDIT.build_discovery(self.policy)
        interactive_agent = next(
            item
            for item in result["runtimeComponents"]
            if item["name"] == "interactive_agent_sidecar"
        )
        previous_release = next(
            item
            for item in interactive_agent["releases"]
            if item["releaseId"] == previous_id
        )

        self.assertIn(previous_id, interactive_agent["activeRollbackReleaseIds"])
        self.assertTrue(interactive_agent["rollbackReferenceCoverageComplete"])
        self.assertIn("rollback", previous_release["protectedClasses"])
        self.assertIn("accepted_evidence", previous_release["protectedClasses"])

    def test_v2_state_protects_nested_previous_release_id(self):
        previous_id = "interactive-agent-v2-state-previous"
        previous = self.create_release(self.interactive_agent_root, previous_id)
        state_directory = self.runtime / "country-outage-interactive-agent" / "state"
        state_directory.mkdir(parents=True)
        active_state = state_directory / "active.json"
        rollback_state = state_directory / "rollback.json"
        active_state.write_text(
            json.dumps(
                {
                    "schema_version": "domeye_interactive_agent_active_v1",
                    "component": "domeye_interactive_agent_sidecar",
                    "release_id": "interactive-agent-active",
                    "deployment_state": "deployed",
                    "activated_at_utc": "2026-08-21T00:00:00.000Z",
                    "release_manifest_sha256": f"sha256:{'a' * 64}",
                    "candidate_id": f"manifest:sha256:{'b' * 64}",
                    "runtime": {
                        "screen_name": "domeye_interactive_agent_sidecar",
                        "pid": 123,
                        "entrypoint": "agent-sidecar/dist/src/cli/serve-interactive-agent.js",
                        "host": "127.0.0.1",
                        "port": 28476,
                        "base_path": "/country-outage/chat",
                    },
                    "rollback": {
                        "mode": "same_schema_only",
                        "previous_release_id": previous_id,
                    },
                }
            ),
            encoding="utf-8",
        )
        rollback_state.write_text(
            active_state.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        active_state.chmod(0o600)
        rollback_state.chmod(0o600)
        component = self.policy["runtimeGovernance"]["releaseComponents"][2]
        component["rollbackStatePaths"] = [str(active_state), str(rollback_state)]

        result = AUDIT.build_discovery(self.policy)
        interactive_agent = next(
            item
            for item in result["runtimeComponents"]
            if item["name"] == "interactive_agent_sidecar"
        )
        previous_release = next(
            item
            for item in interactive_agent["releases"]
            if item["releaseId"] == previous.name
        )

        self.assertIn(previous_id, interactive_agent["activeRollbackReleaseIds"])
        self.assertTrue(interactive_agent["rollbackReferenceCoverageComplete"])
        self.assertIn("rollback", previous_release["protectedClasses"])

    def test_known_v2_manifest_or_state_with_invalid_rollback_is_unknown(self):
        self.policy["runtimeGovernance"]["manifestFileNames"].append(
            "RELEASE-MANIFEST.json"
        )
        manifest = self.interactive_agent_active / "RELEASE-MANIFEST.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": "domeye_interactive_agent_release_manifest_v2",
                    "release_id": "interactive-agent-active",
                    "acceptance": {
                        "evaluation_phase": "formal",
                        "acceptance_state": "accepted",
                        "dg1_decision": "GO",
                        "record_id": f"acceptance-record-sha256:{'a' * 64}",
                        "record_sha256": f"sha256:{'b' * 64}",
                    },
                }
            ),
            encoding="utf-8",
        )
        state_directory = self.runtime / "country-outage-interactive-agent" / "state"
        state_directory.mkdir(parents=True)
        active_state = state_directory / "active.json"
        active_state.write_text(
            json.dumps(
                {
                    "schema_version": "domeye_interactive_agent_active_v1",
                    "release_id": "interactive-agent-active",
                    "rollback": "invalid",
                }
            ),
            encoding="utf-8",
        )
        active_state.chmod(0o600)
        component = self.policy["runtimeGovernance"]["releaseComponents"][2]
        component["rollbackStatePaths"] = [str(active_state)]

        result = AUDIT.build_discovery(self.policy)
        interactive_agent = next(
            item
            for item in result["runtimeComponents"]
            if item["name"] == "interactive_agent_sidecar"
        )
        active_release = next(
            item for item in interactive_agent["releases"] if item["active"]
        )

        self.assertFalse(
            active_release["inventory"]["manifestEvidenceCoverageComplete"]
        )
        self.assertFalse(interactive_agent["rollbackReferenceCoverageComplete"])
        self.assertIn("unknown", active_release["protectedClasses"])
        self.assertEqual(active_release["retentionState"], "protected_or_unknown")

        for declared_release_id in (None, "different-release"):
            with self.subTest(declared_release_id=declared_release_id):
                identity_manifest = {
                    "schema_version": "domeye_interactive_agent_release_manifest_v2",
                    "rollback": {
                        "mode": "fail_closed",
                        "previous_release_id": None,
                    },
                }
                if declared_release_id is not None:
                    identity_manifest["release_id"] = declared_release_id
                manifest.write_text(
                    json.dumps(identity_manifest),
                    encoding="utf-8",
                )
                evidence = AUDIT.manifest_evidence(
                    manifest, "interactive-agent-active", 4096
                )
                self.assertFalse(evidence["declaredReleaseMatchesObject"])
                self.assertFalse(evidence["releaseIdentityContractComplete"])

    def test_v2_rejected_or_incomplete_acceptance_is_not_accepted_evidence(self):
        base = {
            "schema_version": "domeye_interactive_agent_release_manifest_v2",
            "release_id": "fixture-v2",
            "rollback": {"mode": "fail_closed", "previous_release_id": None},
            "checks": {"release": "verified"},
        }
        invalid_acceptances = (
            {
                "evaluation_phase": "pilot",
                "acceptance_state": "accepted",
                "dg1_decision": "GO",
                "record_id": f"acceptance-record-sha256:{'a' * 64}",
                "record_sha256": f"sha256:{'b' * 64}",
            },
            {
                "evaluation_phase": "formal",
                "acceptance_state": "rejected",
                "dg1_decision": "REPAIR",
                "record_id": f"acceptance-record-sha256:{'a' * 64}",
                "record_sha256": f"sha256:{'b' * 64}",
            },
            {
                "evaluation_phase": "formal",
                "acceptance_state": "accepted",
                "dg1_decision": "GO",
                "record_id": "acceptance-record-sha256:invalid",
                "record_sha256": f"sha256:{'b' * 64}",
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "RELEASE-MANIFEST.json"
            for acceptance in invalid_acceptances:
                with self.subTest(acceptance=acceptance):
                    manifest.write_text(
                        json.dumps({**base, "acceptance": acceptance}),
                        encoding="utf-8",
                    )
                    evidence = AUDIT.manifest_evidence(manifest, "fixture-v2", 4096)
                    self.assertFalse(evidence["acceptedEvidence"])
            manifest.write_text(json.dumps(base), encoding="utf-8")
            evidence = AUDIT.manifest_evidence(manifest, "fixture-v2", 4096)
            self.assertFalse(evidence["acceptedEvidence"])

    def test_missing_declared_rollback_fails_closed_for_component_candidates(self):
        (self.backend_active / "RELEASE-MANIFEST.json").write_text(
            json.dumps(
                {
                    "release_id": "backend-active",
                    "rollback": {"release_id": "missing-rollback"},
                    "checks": {"release": "verified"},
                }
            ),
            encoding="utf-8",
        )
        self.policy["runtimeGovernance"]["manifestFileNames"].append("RELEASE-MANIFEST.json")

        result = AUDIT.build_discovery(self.policy)
        backend = next(item for item in result["runtimeComponents"] if item["name"] == "backend")
        old_release = next(item for item in backend["releases"] if item["releaseId"] == "backend-old")

        self.assertFalse(backend["rollbackReferenceCoverageComplete"])
        self.assertIn("unknown", old_release["protectedClasses"])
        self.assertEqual(old_release["retentionState"], "protected_or_unknown")

    def test_unparseable_manifest_fails_closed_without_emitting_contents(self):
        (self.backend_old / "manifest.json").write_text("SECRET_MARKER_NOT_EMITTED", encoding="utf-8")

        result = AUDIT.build_discovery(self.policy)
        backend = next(item for item in result["runtimeComponents"] if item["name"] == "backend")
        old_release = next(item for item in backend["releases"] if item["releaseId"] == "backend-old")
        encoded = json.dumps(result, ensure_ascii=False)

        self.assertIn("unknown", old_release["protectedClasses"])
        self.assertNotIn("SECRET_MARKER_NOT_EMITTED", encoded)

    def test_root_only_lifecycle_state_protects_declared_rollback(self):
        rollback = self.create_release(self.agent_root, "agent-rollback")
        state_directory = self.runtime / "country-outage-agent" / "state"
        state_directory.mkdir(parents=True)
        active_state = state_directory / "active.json"
        rollback_state = state_directory / "rollback.json"
        active_state.write_text(json.dumps({"release_id": "agent-active", "previous_release_id": "agent-rollback"}), encoding="utf-8")
        rollback_state.write_text(json.dumps({"release_id": "agent-rollback"}), encoding="utf-8")
        active_state.chmod(0o600)
        rollback_state.chmod(0o600)
        agent_component = self.policy["runtimeGovernance"]["releaseComponents"][1]
        agent_component["rollbackStatePaths"] = [str(active_state), str(rollback_state)]

        result = AUDIT.build_discovery(self.policy)
        agent = next(item for item in result["runtimeComponents"] if item["name"] == "legacy_agent_sidecar")
        rollback_release = next(item for item in agent["releases"] if item["releaseId"] == "agent-rollback")

        self.assertTrue(agent["rollbackStateEvidence"]["coverageComplete"])
        self.assertIn("agent-rollback", agent["activeRollbackReleaseIds"])
        self.assertTrue(agent["rollbackReferenceCoverageComplete"])
        self.assertIn("rollback", rollback_release["protectedClasses"])

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
