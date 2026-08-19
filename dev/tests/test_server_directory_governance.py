import base64
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "governance" / "audit-server-layout.py"
SPEC = importlib.util.spec_from_file_location("audit_server_layout", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def run(arguments, cwd):
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


class ServerDirectoryGovernanceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.protected = self.root / "old-domeye"
        self.source = self.root / "Domeye-Core"
        self.runtime = self.root / "Domeye-Core-runtime"
        self.artifacts = self.root / "Domeye-Core-artifacts"
        self.data = self.root / "Domeye-Core-data"
        self.dev_data = self.root / "Domeye-Core-dev-data"
        self.governance = self.root / "Domeye-Core-governance"
        for path in (
            self.protected,
            self.source,
            self.runtime,
            self.artifacts,
            self.data,
            self.dev_data,
            self.governance,
        ):
            path.mkdir()

        run(["git", "init", "-b", "main"], self.source)
        run(["git", "config", "user.email", "audit@example.invalid"], self.source)
        run(["git", "config", "user.name", "Audit Fixture"], self.source)
        (self.source / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        run(["git", "add", "tracked.txt"], self.source)
        run(["git", "commit", "-m", "baseline"], self.source)
        (self.source / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        (self.source / "untracked.txt").write_text("untracked\n", encoding="utf-8")

        release_specs = (
            ("releases", "prod-backend", "current"),
            ("country-outage-agent/releases", "prod-agent", "country-outage-agent/current"),
            (
                "country-outage-interactive-agent/releases",
                "prod-interactive-agent",
                "country-outage-interactive-agent/current",
            ),
            (
                "country-outage-p1-chat/releases",
                "legacy-p1-chat-retained-not-routed",
                "country-outage-p1-chat/current",
            ),
        )
        self.active_links = []
        for release_root, release_id, link_path in release_specs:
            release_directory = self.runtime / release_root / release_id
            release_directory.mkdir(parents=True)
            link = self.runtime / link_path
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(release_directory)
            self.active_links.append(
                {
                    "name": release_id,
                    "path": str(link),
                    "allowedTargetPrefix": str(self.runtime / release_root),
                }
            )
        hidden = self.runtime / "releases" / ".source-interrupted"
        hidden.mkdir()
        (self.runtime / "unified-releases").mkdir()

        config = self.runtime / "config"
        config.mkdir()
        secret = config / "runtime.env"
        secret.write_text("SECRET_MARKER_SHOULD_NOT_LEAK\n", encoding="utf-8")
        secret.chmod(0o600)

        self.process_root = self.root / "proc"
        process = self.process_root / "123"
        process.mkdir(parents=True)
        (process / "cwd").symlink_to(self.protected)
        (process / "exe").symlink_to("/bin/sh")
        (process / "comm").write_text("legacy-service\n", encoding="utf-8")

        self.policy = {
            "schemaVersion": AUDIT.POLICY_SCHEMA,
            "expectedHost": socket.gethostname(),
            "filesystemPath": str(self.root),
            "processRoot": str(self.process_root),
            "screenSessionsEnabled": False,
            "diskThresholdPercent": {"warning": 98, "critical": 99, "stopNewBuilds": 100},
            "protectedRoots": [
                {"path": str(self.protected), "reason": "fixture protected"}
            ],
            "managedRoots": [
                {"name": "source", "path": str(self.source), "mode": "normalize"},
                {"name": "runtime", "path": str(self.runtime), "mode": "audit"},
                {"name": "artifacts", "path": str(self.artifacts), "mode": "audit"},
                {"name": "data", "path": str(self.data), "mode": "audit_only"},
                {"name": "dev_data", "path": str(self.dev_data), "mode": "audit"},
                {"name": "governance", "path": str(self.governance), "mode": "audit"},
            ],
            "sourceCheckout": str(self.source),
            "runtimeRoot": str(self.runtime),
            "configDirectory": str(config),
            "requiredConfigMode": "0600",
            "activeLinks": self.active_links,
            "releaseRoots": [
                {"name": "core", "path": str(self.runtime / "releases")},
                {"name": "unified", "path": str(self.runtime / "unified-releases")},
                {
                    "name": "agent",
                    "path": str(self.runtime / "country-outage-agent/releases"),
                },
                {
                    "name": "interactive_agent",
                    "path": str(
                        self.runtime / "country-outage-interactive-agent/releases"
                    ),
                },
                {
                    "name": "legacy_p1_chat",
                    "path": str(self.runtime / "country-outage-p1-chat/releases"),
                    "routingState": "retained_not_routed",
                    "governanceMode": "read_only",
                },
            ],
            "retention": {
                "maximumRetainedPerComponent": 5,
                "quarantineDays": 14,
                "requiredProtectedClasses": ["active", "unknown"],
            },
            "mutationPolicy": {
                "auditWritesServer": False,
                "deleteEnabled": False,
                "moveEnabled": False,
                "restartEnabled": False,
                "productionSwitchEnabled": False,
                "requiresSeparateTaskForMutation": True,
            },
        }

    def tearDown(self):
        self.directory.cleanup()

    def test_audit_is_read_only_and_fails_closed_for_dirty_source(self):
        audit = AUDIT.build_audit(self.policy)
        self.assertEqual(audit["mode"], "read_only")
        self.assertFalse(audit["mutationAuthorized"])
        self.assertEqual(audit["gate"]["decision"], "BLOCK_MUTATION")
        self.assertFalse(audit["sourceCheckout"]["clean"])
        self.assertEqual(audit["sourceCheckout"]["modifiedCount"], 1)
        self.assertEqual(audit["sourceCheckout"]["untrackedCount"], 1)
        self.assertTrue(all(item["valid"] for item in audit["activeLinks"]))
        self.assertIn(
            "legacy-p1-chat-retained-not-routed",
            {item["name"] for item in audit["activeLinks"]},
        )
        self.assertTrue(audit["configPermissions"]["allCompliant"])
        self.assertEqual(len(audit["protectedProcessReferences"]), 1)
        self.assertEqual(
            sum(item["hiddenDirectoryCount"] for item in audit["releaseRoots"]), 1
        )
        self.assertTrue(
            any(item["classification"] == "unclassified" for item in audit["topLevelEntries"])
        )

    def test_audit_never_emits_config_contents_or_process_arguments(self):
        encoded = json.dumps(AUDIT.build_audit(self.policy), ensure_ascii=False)
        self.assertNotIn("SECRET_MARKER_SHOULD_NOT_LEAK", encoded)
        process = AUDIT.build_audit(self.policy)["protectedProcessReferences"][0]
        self.assertEqual(set(process), {"pid", "command", "cwd", "executable", "matchedRoots"})

    def test_policy_rejects_managed_path_inside_protected_tree(self):
        self.policy["managedRoots"][0]["path"] = str(self.protected / "child")
        with self.assertRaisesRegex(AUDIT.AuditError, "受管路径不得进入保护树"):
            AUDIT.validate_policy(self.policy)

    def test_policy_rejects_any_mutation_capability(self):
        self.policy["mutationPolicy"]["deleteEnabled"] = True
        with self.assertRaisesRegex(AUDIT.AuditError, "deleteEnabled=false"):
            AUDIT.validate_policy(self.policy)

    def test_policy_rejects_missing_active_link_target_boundary(self):
        del self.policy["activeLinks"][0]["allowedTargetPrefix"]
        with self.assertRaisesRegex(AUDIT.AuditError, "allowedTargetPrefix"):
            AUDIT.validate_policy(self.policy)

    def test_policy_can_be_supplied_without_writing_server_files(self):
        encoded = base64.b64encode(
            json.dumps(self.policy, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        original = os.environ.get(AUDIT.POLICY_ENV)
        try:
            os.environ[AUDIT.POLICY_ENV] = encoded
            loaded = AUDIT.load_policy(None)
        finally:
            if original is None:
                os.environ.pop(AUDIT.POLICY_ENV, None)
            else:
                os.environ[AUDIT.POLICY_ENV] = original
        self.assertEqual(loaded["schemaVersion"], AUDIT.POLICY_SCHEMA)


if __name__ == "__main__":
    unittest.main()
