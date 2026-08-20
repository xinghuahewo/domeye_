import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "governance" / "refresh-server-checkout.py"
SPEC = importlib.util.spec_from_file_location("refresh_server_checkout", SCRIPT)
REFRESH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REFRESH)


def run(arguments, cwd):
    return subprocess.run(arguments, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()


class ServerCheckoutRefreshTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.publisher = self.root / "publisher"
        self.publisher.mkdir()
        run(["git", "init", "-b", "main"], self.publisher)
        run(["git", "config", "user.email", "fixture@example.invalid"], self.publisher)
        run(["git", "config", "user.name", "Fixture"], self.publisher)
        (self.publisher / "tracked.txt").write_text("old\n", encoding="utf-8")
        run(["git", "add", "tracked.txt"], self.publisher)
        run(["git", "commit", "-m", "old"], self.publisher)
        self.old = run(["git", "rev-parse", "HEAD"], self.publisher)
        (self.publisher / "tracked.txt").write_text("new\n", encoding="utf-8")
        run(["git", "commit", "-am", "new"], self.publisher)
        self.new = run(["git", "rev-parse", "HEAD"], self.publisher)

        self.source = self.root / "Domeye-Core"
        run(["git", "clone", str(self.publisher), str(self.source)], self.root)
        run(["git", "reset", "--hard", self.old], self.source)
        run(["git", "remote", "set-url", "origin", REFRESH.EXPECTED_REMOTE], self.source)
        run(["git", "update-ref", "refs/remotes/origin/main", self.old], self.source)
        self.artifacts = self.root / "Domeye-Core-artifacts"
        incoming = self.artifacts / "incoming"
        incoming.mkdir(parents=True)
        self.operation = "fixture-refresh"
        self.bundle = incoming / f"{self.operation}.bundle"
        source_bundle = self.root / "main.bundle"
        run(["git", "bundle", "create", str(source_bundle), "main"], self.publisher)
        shutil.copyfile(source_bundle, self.bundle)

        self.required_link = self.root / "runtime" / "current"
        self.bootstrap_link = self.root / "runtime" / "country-outage-interactive-agent" / "current"
        self.required_target = self.root / "runtime" / "releases" / "current-release"
        self.required_target.mkdir(parents=True)
        self.required_link.parent.mkdir(parents=True, exist_ok=True)
        self.bootstrap_link.parent.mkdir(parents=True)
        self.required_link.symlink_to(self.required_target)

        self.original = (
            REFRESH.EXPECTED_HOST,
            REFRESH.EXPECTED_SOURCE,
            REFRESH.EXPECTED_ARTIFACT_ROOT,
            REFRESH.PROTECTED_ROOTS,
            REFRESH.ACTIVE_LINKS,
            REFRESH.BOOTSTRAP_ABSENT_ACTIVE_LINK,
        )
        self.original_mount_references = REFRESH.mount_references
        REFRESH.EXPECTED_HOST = socket.gethostname()
        REFRESH.EXPECTED_SOURCE = self.source
        REFRESH.EXPECTED_ARTIFACT_ROOT = self.artifacts
        REFRESH.PROTECTED_ROOTS = ()
        REFRESH.ACTIVE_LINKS = (self.required_link, self.bootstrap_link)
        REFRESH.BOOTSTRAP_ABSENT_ACTIVE_LINK = self.bootstrap_link
        REFRESH.mount_references = lambda source: []

    def tearDown(self):
        (
            REFRESH.EXPECTED_HOST,
            REFRESH.EXPECTED_SOURCE,
            REFRESH.EXPECTED_ARTIFACT_ROOT,
            REFRESH.PROTECTED_ROOTS,
            REFRESH.ACTIVE_LINKS,
            REFRESH.BOOTSTRAP_ABSENT_ACTIVE_LINK,
        ) = self.original
        REFRESH.mount_references = self.original_mount_references
        self.temporary.cleanup()

    def refresh_with_second_active_link_snapshot_mutation(self, mutation):
        original_active_links = REFRESH.active_links
        call_count = 0

        def observe_active_links():
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                mutation()
            return original_active_links()

        with mock.patch.object(REFRESH, "active_links", side_effect=observe_active_links):
            return REFRESH.refresh(self.operation, self.old, self.new, self.bundle, REFRESH.sha256_file(self.bundle))

    def test_refreshes_with_bootstrap_link_missing_before_and_after(self):
        result = REFRESH.refresh(self.operation, self.old, self.new, self.bundle, REFRESH.sha256_file(self.bundle))

        self.assertEqual(result["sourceBefore"]["head"], self.old)
        self.assertEqual(result["sourceAfter"]["head"], self.new)
        self.assertEqual(
            result["activeLinksBefore"],
            [
                {
                    "path": str(self.required_link),
                    "state": "symlink",
                    "rawTarget": str(self.required_target),
                    "resolvedTarget": str(self.required_target.resolve()),
                },
                {"path": str(self.bootstrap_link), "state": "absent"},
            ],
        )
        self.assertEqual(result["activeLinksAfter"], result["activeLinksBefore"])
        self.assertEqual(run(["git", "rev-parse", "origin/main"], self.source), self.new)
        self.assertEqual(run(["git", "rev-parse", result["rollbackRef"]["ref"]], self.source), self.old)
        self.assertTrue(Path(result["inputBundle"]["path"]).is_file())
        self.assertTrue(Path(result["receiptPath"]).is_file())
        receipt = json.loads(Path(result["receiptPath"]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["activeLinksBefore"], result["activeLinksBefore"])
        self.assertEqual(receipt["activeLinksAfter"], result["activeLinksAfter"])
        self.assertFalse(self.bundle.exists())

    def test_refresh_rejects_bootstrap_link_created_between_snapshots(self):
        bootstrap_target = self.root / "runtime" / "releases" / "interactive-release"
        bootstrap_target.mkdir()

        with self.assertRaisesRegex(REFRESH.RefreshError, "活动指针身份不一致"):
            self.refresh_with_second_active_link_snapshot_mutation(
                lambda: self.bootstrap_link.symlink_to(bootstrap_target)
            )

        self.assertEqual(run(["git", "rev-parse", "HEAD"], self.source), self.old)

    def test_refresh_rejects_active_link_target_changed_between_snapshots(self):
        changed_target = self.root / "runtime" / "releases" / "changed-release"
        changed_target.mkdir()

        def change_target():
            self.required_link.unlink()
            self.required_link.symlink_to(changed_target)

        with self.assertRaisesRegex(REFRESH.RefreshError, "活动指针身份不一致"):
            self.refresh_with_second_active_link_snapshot_mutation(change_target)

        self.assertEqual(run(["git", "rev-parse", "HEAD"], self.source), self.old)

    def test_active_links_rejects_other_missing_link(self):
        self.required_link.unlink()

        with self.assertRaisesRegex(REFRESH.RefreshError, "活动指针不存在"):
            REFRESH.active_links()

    def test_active_links_rejects_existing_non_symlink_at_bootstrap_path(self):
        self.bootstrap_link.write_text("not-a-symlink\n", encoding="utf-8")

        with self.assertRaisesRegex(REFRESH.RefreshError, "活动指针不是软链接"):
            REFRESH.active_links()

    def test_preflight_rejects_bundle_digest_drift_without_writing(self):
        with self.assertRaisesRegex(REFRESH.RefreshError, "SHA-256"):
            REFRESH.preflight(self.operation, self.old, self.new, self.bundle, "0" * 64)
        self.assertTrue(self.bundle.is_file())
        self.assertFalse((self.artifacts / "quarantine").exists())

    def test_operation_id_and_scope_fail_closed(self):
        with self.assertRaisesRegex(REFRESH.RefreshError, "operation-id"):
            REFRESH.safe_operation_id("../escape")
        with self.assertRaisesRegex(REFRESH.RefreshError, "指定源码 checkout"):
            REFRESH.validate_scope(socket.gethostname(), self.root / "other", self.artifacts)


if __name__ == "__main__":
    unittest.main()
