import importlib.util
import json
import os
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
            result["sourceBefore"]["remote"],
            "git@github.com:xinghuahewo/domeye_.git",
        )
        self.assertEqual(result["sourceAfter"]["remote"], result["sourceBefore"]["remote"])
        self.assertEqual(result["authorityRemote"], result["sourceBefore"]["remote"])
        self.assertEqual(result["checkoutAcquisition"], "local_immutable_git_bundle")
        self.assertEqual(result["schemaVersion"], "domeye.server-checkout-refresh/v2")
        self.assertEqual(result["repositoryAccess"]["policy"], "official_ssh_first_v1")
        self.assertFalse(result["repositoryAccess"]["networkGitHubAccessPerformed"])
        self.assertEqual(
            result["repositoryAccess"]["sshPreflight"]["status"],
            "not_required_bundle_only",
        )
        self.assertFalse(result["repositoryAccess"]["httpsFallback"]["performed"])
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

    def test_preflight_does_not_refresh_git_index(self):
        tracked = self.source / "tracked.txt"
        current = tracked.stat()
        os.utime(
            tracked,
            ns=(current.st_atime_ns, current.st_mtime_ns + 2_000_000_000),
        )
        index = self.source / ".git" / "index"
        before = index.read_bytes()

        snapshot = REFRESH.preflight(
            self.operation,
            self.old,
            self.new,
            self.bundle,
            REFRESH.sha256_file(self.bundle),
        )

        self.assertEqual(snapshot["source"]["head"], self.old)
        self.assertEqual(index.read_bytes(), before)
        self.assertTrue(self.bundle.is_file())
        self.assertFalse((self.artifacts / "quarantine").exists())

    def test_operation_id_and_scope_fail_closed(self):
        self.assertEqual(
            REFRESH.EXPECTED_REMOTE,
            "git@github.com:xinghuahewo/domeye_.git",
        )
        with self.assertRaisesRegex(REFRESH.RefreshError, "operation-id"):
            REFRESH.safe_operation_id("../escape")
        with self.assertRaisesRegex(REFRESH.RefreshError, "指定源码 checkout"):
            REFRESH.validate_scope(socket.gethostname(), self.root / "other", self.artifacts)

    def test_source_snapshot_rejects_https_origin(self):
        run(
            [
                "git",
                "remote",
                "set-url",
                "origin",
                "https://github.com/xinghuahewo/domeye_.git",
            ],
            self.source,
        )

        with self.assertRaisesRegex(REFRESH.RefreshError, "官方 GitHub SSH"):
            REFRESH.refresh(
                self.operation,
                self.old,
                self.new,
                self.bundle,
                REFRESH.sha256_file(self.bundle),
            )

        self.assertTrue(self.bundle.is_file())
        self.assertEqual(run(["git", "rev-parse", "HEAD"], self.source), self.old)
        self.assertEqual(
            run(["git", "rev-parse", "origin/main"], self.source), self.old
        )
        self.assertFalse((self.artifacts / "quarantine").exists())

    def test_source_snapshot_rejects_separate_https_pushurl(self):
        run(
            [
                "git",
                "config",
                "--local",
                "--add",
                "remote.origin.pushurl",
                "https://github.com/xinghuahewo/domeye_.git",
            ],
            self.source,
        )

        with self.assertRaisesRegex(REFRESH.RefreshError, "不可改写的官方 GitHub SSH"):
            REFRESH.source_snapshot(self.source)

        self.assertTrue(self.bundle.is_file())
        self.assertFalse((self.artifacts / "quarantine").exists())

    def test_source_snapshot_rejects_extra_fetch_url(self):
        run(
            [
                "git",
                "config",
                "--local",
                "--add",
                "remote.origin.url",
                "https://github.com/xinghuahewo/domeye_.git",
            ],
            self.source,
        )

        with self.assertRaisesRegex(REFRESH.RefreshError, "不可改写的官方 GitHub SSH"):
            REFRESH.source_snapshot(self.source)

        self.assertTrue(self.bundle.is_file())
        self.assertEqual(run(["git", "rev-parse", "HEAD"], self.source), self.old)
        self.assertEqual(
            run(["git", "rev-parse", "origin/main"], self.source), self.old
        )
        self.assertFalse((self.artifacts / "quarantine").exists())

    def test_refresh_unbundle_ignores_local_url_rewrite(self):
        run(
            [
                "git",
                "config",
                "--local",
                f"url.file://{self.root}/missing-bundle.insteadOf",
                str(self.bundle),
            ],
            self.source,
        )

        result = REFRESH.refresh(
            self.operation,
            self.old,
            self.new,
            self.bundle,
            REFRESH.sha256_file(self.bundle),
        )

        self.assertEqual(result["sourceAfter"]["head"], self.new)
        self.assertEqual(
            result["repositoryAccess"]["acquisition"],
            "local_immutable_git_bundle",
        )
        self.assertFalse(result["repositoryAccess"]["networkGitHubAccessPerformed"])

    def test_source_snapshot_ignores_ambient_decoy_repository(self):
        decoy = self.root / "decoy"
        decoy.mkdir()
        run(["git", "init", "-b", "main"], decoy)
        run(["git", "config", "user.email", "fixture@example.invalid"], decoy)
        run(["git", "config", "user.name", "Fixture"], decoy)
        (decoy / "decoy.txt").write_text("decoy\n", encoding="utf-8")
        run(["git", "add", "decoy.txt"], decoy)
        run(["git", "commit", "-m", "decoy"], decoy)
        decoy_head = run(["git", "rev-parse", "HEAD"], decoy)
        self.assertNotEqual(decoy_head, self.old)

        with mock.patch.dict(
            os.environ,
            {
                "GIT_DIR": str(decoy / ".git"),
                "GIT_WORK_TREE": str(decoy),
                "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
            },
            clear=False,
        ):
            snapshot = REFRESH.source_snapshot(self.source)

        self.assertEqual(snapshot["head"], self.old)
        self.assertEqual(snapshot["originMain"], self.old)


if __name__ == "__main__":
    unittest.main()
