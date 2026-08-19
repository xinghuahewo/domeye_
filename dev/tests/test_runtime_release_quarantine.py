import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "governance" / "quarantine-runtime-releases.py"
SPEC = importlib.util.spec_from_file_location("runtime_release_quarantine", SCRIPT)
EXECUTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXECUTOR)


class RuntimeReleaseQuarantineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.protected = self.root / "old-domeye"
        self.runtime = self.root / "Domeye-Core-runtime"
        self.artifacts = self.root / "Domeye-Core-artifacts"
        self.source = self.root / "Domeye-Core"
        self.data = self.root / "Domeye-Core-data"
        self.dev_data = self.root / "Domeye-Core-dev-data"
        self.governance = self.root / "Domeye-Core-governance"
        for item in (self.protected, self.runtime, self.artifacts, self.source, self.data, self.dev_data, self.governance):
            item.mkdir()
        self.process_root = self.root / "proc"
        self.process_root.mkdir()
        (self.process_root / "locks").write_text("", encoding="utf-8")
        self.mount_info = self.root / "mountinfo"
        self.mount_info.write_text("", encoding="utf-8")
        self.config = self.runtime / "config"
        self.config.mkdir()
        (self.config / "runtime.env").write_text("fixture-only\n", encoding="utf-8")
        (self.config / "runtime.env").chmod(0o600)

        self.backend_root = self.runtime / "releases"
        self.agent_root = self.runtime / "country-outage-agent" / "releases"
        self.interactive_agent_root = self.runtime / "country-outage-interactive-agent" / "releases"
        self.legacy_p1_chat_root = self.runtime / "country-outage-p1-chat" / "releases"
        self.backend_active = self.create_release(self.backend_root, "backend-active", accepted=True)
        self.backend_old = self.create_release(self.backend_root, "backend-old")
        self.backend_old_second = self.create_release(self.backend_root, "backend-old-second")
        self.agent_active = self.create_release(self.agent_root, "agent-active", accepted=True)
        self.interactive_agent_active = self.create_release(
            self.interactive_agent_root, "interactive-agent-active", accepted=True
        )
        self.legacy_p1_chat_active = self.create_release(
            self.legacy_p1_chat_root, "legacy-p1-chat-active", accepted=True
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

        development_roots = []
        for name in ("research-runs", "research-worktrees", "research-inputs", "overlays"):
            path = self.dev_data / name
            path.mkdir()
            development_roots.append({"name": name.replace("-", "_"), "path": str(path)})
        self.policy = {
            "schemaVersion": EXECUTOR.AUDIT.POLICY_SCHEMA,
            "expectedHost": socket.gethostname(),
            "processRoot": str(self.process_root),
            "configDirectory": str(self.config),
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
                "maxEntriesPerObject": 1000,
                "maxManifestBytes": 4096,
                "rollbackStateRequiredUid": os.getuid(),
                "rollbackStateRequiredGid": os.getgid(),
                "rollbackStateRequiredMode": "0600",
                "manifestFileNames": ["manifest.json"],
                "releaseComponents": [
                    {"name": "backend", "activeLinkPath": str(self.runtime / "current"), "releaseRoot": str(self.backend_root), "rollbackStatePaths": []},
                    {"name": "legacy_agent_sidecar", "activeLinkPath": str(self.runtime / "country-outage-agent" / "current"), "releaseRoot": str(self.agent_root), "rollbackStatePaths": []},
                    {
                        "name": "interactive_agent_sidecar",
                        "activeLinkPath": str(
                            self.runtime / "country-outage-interactive-agent" / "current"
                        ),
                        "releaseRoot": str(self.interactive_agent_root),
                        "rollbackStatePaths": [],
                    },
                    {
                        "name": "legacy_p1_chat_sidecar",
                        "activeLinkPath": str(
                            self.runtime / "country-outage-p1-chat" / "current"
                        ),
                        "releaseRoot": str(self.legacy_p1_chat_root),
                        "rollbackStatePaths": [],
                        "routingState": "retained_not_routed",
                        "governanceMode": "read_only",
                    },
                ],
                "developmentDataRoots": development_roots,
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

    def create_release(self, root, release_id, *, accepted=False):
        release = root / release_id
        release.mkdir(parents=True)
        payload = {"release_id": release_id}
        if accepted:
            payload["checks"] = {"fixture": "verified"}
        (release / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
        (release / "payload.bin").write_bytes((release_id + "\n").encode("utf-8"))
        return release

    def link(self, link, target):
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)

    def batch(self, operation_id="s4-fixture-batch-001", releases=None):
        return EXECUTOR.build_batch_plan(
            self.policy,
            "backend",
            releases or ["backend-old"],
            operation_id,
            "用户已明确授权本 fixture 精确批次隔离，不删除。",
        )

    def test_plan_and_read_only_preflight_leave_paths_unchanged(self):
        batch = self.batch()
        result = EXECUTOR.execute({**self.policy}, {**batch, "batchManifestSha256": "a" * 64}, apply=False)

        self.assertEqual(result["mode"], "read_only")
        self.assertEqual(result["gate"]["decision"], "READ_ONLY_READY")
        self.assertTrue(self.backend_old.is_dir())
        self.assertFalse((self.artifacts / "quarantine").exists())
        self.assertFalse(result["oldDomeyeTouched"])
        self.assertFalse(result["productionSwitchPerformed"])

    def test_apply_moves_exact_candidate_and_reads_back_without_delete(self):
        batch = self.batch()
        batch["batchManifestSha256"] = "b" * 64
        result = EXECUTOR.execute(self.policy, batch, apply=True)
        destination = self.artifacts / "quarantine" / "runtime-releases" / batch["operationId"] / "backend" / "backend-old"
        receipt = destination.parents[1] / "quarantine-receipt.json"

        self.assertEqual(result["gate"]["decision"], "QUARANTINED")
        self.assertFalse(self.backend_old.exists())
        self.assertTrue(destination.is_dir())
        self.assertTrue(receipt.is_file())
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["state"], "quarantined")
        self.assertEqual(os.stat(receipt).st_mode & 0o777, 0o600)
        self.assertEqual((self.runtime / "current").resolve(), self.backend_active.resolve())
        self.assertTrue(self.protected.exists())

    def test_preflight_rejects_inventory_drift_without_moving(self):
        batch = self.batch()
        batch["batchManifestSha256"] = "c" * 64
        (self.backend_old / "after-plan.txt").write_text("drift\n", encoding="utf-8")

        with self.assertRaisesRegex(EXECUTOR.QuarantineError, "清单摘要漂移"):
            EXECUTOR.execute(self.policy, batch, apply=True)

        self.assertTrue(self.backend_old.is_dir())
        self.assertFalse((self.artifacts / "quarantine").exists())

    def test_plan_refuses_active_or_protected_release(self):
        with self.assertRaisesRegex(EXECUTOR.QuarantineError, "不是当前可隔离候选"):
            self.batch(releases=["backend-active"])
        with self.assertRaisesRegex(EXECUTOR.QuarantineError, "不是当前可隔离候选"):
            EXECUTOR.build_batch_plan(
                self.policy,
                "legacy_p1_chat_sidecar",
                ["legacy-p1-chat-active"],
                "s4-legacy-p1-read-only-001",
                "只验证旧 P1 Chat 活动 release 保持只读保护，不执行隔离。",
            )

    def test_apply_automatically_restores_earlier_moves_on_later_move_error(self):
        batch = self.batch("s4-fixture-batch-002", ["backend-old", "backend-old-second"])
        batch["batchManifestSha256"] = "d" * 64
        real_replace = os.replace

        def fail_second_move(source, destination):
            if Path(source).resolve() == self.backend_old_second.resolve():
                raise OSError("fixture second move failure")
            return real_replace(source, destination)

        with mock.patch.object(EXECUTOR.os, "replace", side_effect=fail_second_move):
            with self.assertRaisesRegex(EXECUTOR.QuarantineError, "rollback_complete"):
                EXECUTOR.execute(self.policy, batch, apply=True)

        operation = self.artifacts / "quarantine" / "runtime-releases" / batch["operationId"]
        receipt = json.loads((operation / "quarantine-receipt.json").read_text(encoding="utf-8"))
        self.assertTrue(self.backend_old.is_dir())
        self.assertTrue(self.backend_old_second.is_dir())
        self.assertEqual(receipt["state"], "rollback_complete")
        self.assertEqual(receipt["items"][0]["state"], "restored")

    def test_apply_automatically_restores_when_post_move_readback_fails(self):
        batch = self.batch("s4-fixture-batch-003")
        batch["batchManifestSha256"] = "e" * 64

        with mock.patch.object(EXECUTOR, "post_move_readback", side_effect=EXECUTOR.QuarantineError("fixture readback failure")):
            with self.assertRaisesRegex(EXECUTOR.QuarantineError, "rollback_complete"):
                EXECUTOR.execute(self.policy, batch, apply=True)

        operation = self.artifacts / "quarantine" / "runtime-releases" / batch["operationId"]
        receipt = json.loads((operation / "quarantine-receipt.json").read_text(encoding="utf-8"))
        self.assertTrue(self.backend_old.is_dir())
        self.assertEqual(receipt["state"], "rollback_complete")
        self.assertEqual(receipt["items"][0]["state"], "restored")

    def test_batch_requires_nonempty_user_authorization(self):
        batch = self.batch()
        batch["userAuthorization"] = ""
        with self.assertRaisesRegex(EXECUTOR.QuarantineError, "userAuthorization"):
            EXECUTOR.validate_batch(batch)

    def test_apply_manifest_requires_regular_root_only_metadata(self):
        manifest = self.root / "batch.json"
        manifest.write_text("{}", encoding="utf-8")
        manifest.chmod(0o644)

        with self.assertRaisesRegex(EXECUTOR.QuarantineError, "root:root 0600"):
            EXECUTOR.validate_batch_manifest_metadata(manifest, expected_uid=os.getuid(), expected_gid=os.getgid())

        manifest.chmod(0o600)
        EXECUTOR.validate_batch_manifest_metadata(manifest, expected_uid=os.getuid(), expected_gid=os.getgid())


if __name__ == "__main__":
    unittest.main()
