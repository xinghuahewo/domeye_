import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "governance" / "normalize-server-checkout.py"
SPEC = importlib.util.spec_from_file_location("normalize_server_checkout", SCRIPT)
NORMALIZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NORMALIZER)


def run(arguments, cwd):
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


class ServerCheckoutNormalizationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_operation_id_and_scope_fail_closed(self):
        self.assertEqual(NORMALIZER.safe_operation_id("20260818T120000Z-main"), "20260818T120000Z-main")
        with self.assertRaisesRegex(NORMALIZER.NormalizationError, "operation-id"):
            NORMALIZER.safe_operation_id("../escape")
        with self.assertRaisesRegex(NORMALIZER.NormalizationError, "公开 HTTPS"):
            NORMALIZER.validate_scope(
                NORMALIZER.EXPECTED_HOST,
                NORMALIZER.EXPECTED_SOURCE,
                NORMALIZER.EXPECTED_ARTIFACT_ROOT,
                "https://token@example.invalid/repo.git",
            )

    def test_process_reference_scan_reads_only_paths(self):
        source = self.root / "Domeye-Core"
        source.mkdir()
        tracked = source / "tracked.txt"
        tracked.write_text("fixture\n", encoding="utf-8")
        process = self.root / "proc" / "321"
        (process / "fd").mkdir(parents=True)
        (process / "cwd").symlink_to(source)
        (process / "exe").symlink_to("/bin/sh")
        (process / "fd" / "7").symlink_to(tracked)
        (process / "comm").write_text("fixture-service\n", encoding="utf-8")

        references = NORMALIZER.source_process_references(source, self.root / "proc")

        self.assertEqual(references[0]["pid"], 321)
        self.assertEqual(references[0]["command"], "fixture-service")
        self.assertIn("cwd", references[0]["references"])
        self.assertIn("fd:7", references[0]["references"])
        self.assertNotIn("fixture\n", str(references))

    def test_mount_and_lock_scan_detects_blockers(self):
        source = self.root / "Domeye-Core"
        (source / ".git").mkdir(parents=True)
        (source / ".git" / "index.lock").write_text("lock", encoding="utf-8")
        mountinfo = self.root / "mountinfo"
        mountinfo.write_text(
            f"42 31 0:42 / {source} rw,relatime - ext4 /dev/fake rw\n",
            encoding="utf-8",
        )

        self.assertEqual(NORMALIZER.source_lock_files(source), [str(source / ".git" / "index.lock")])
        self.assertEqual(NORMALIZER.source_mount_references(source, mountinfo), [str(source)])

    def test_preflight_snapshots_dirty_checkout_without_writing(self):
        source = self.root / "Domeye-Core"
        artifact_root = self.root / "Domeye-Core-artifacts"
        source.mkdir()
        artifact_root.mkdir()
        run(["git", "init", "-b", "main"], source)
        run(["git", "config", "user.email", "fixture@example.invalid"], source)
        run(["git", "config", "user.name", "Fixture"], source)
        (source / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        run(["git", "add", "tracked.txt"], source)
        run(["git", "commit", "-m", "baseline"], source)
        expected_head = run(["git", "rev-parse", "HEAD"], source)
        (source / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        (source / "untracked.txt").write_text("untracked\n", encoding="utf-8")

        original_links = NORMALIZER.ACTIVE_LINKS
        NORMALIZER.ACTIVE_LINKS = ()
        try:
            snapshot = NORMALIZER.preflight(source, artifact_root, expected_head)
        finally:
            NORMALIZER.ACTIVE_LINKS = original_links

        self.assertEqual(snapshot["sourceCheckout"]["changeCount"], 2)
        self.assertEqual(snapshot["sourceCheckout"]["remoteNames"], [])
        self.assertFalse((artifact_root / "quarantine").exists())

    def test_bundle_clone_keeps_public_origin_without_network_fetch(self):
        source_repository = self.root / "source-repository"
        source_repository.mkdir()
        run(["git", "init", "-b", "main"], source_repository)
        run(["git", "config", "user.email", "fixture@example.invalid"], source_repository)
        run(["git", "config", "user.name", "Fixture"], source_repository)
        (source_repository / "tracked.txt").write_text("bundle\n", encoding="utf-8")
        run(["git", "add", "tracked.txt"], source_repository)
        run(["git", "commit", "-m", "bundle"], source_repository)
        expected_head = run(["git", "rev-parse", "HEAD"], source_repository)
        bundle = self.root / "main.bundle"
        run(["git", "bundle", "create", str(bundle), "main"], source_repository)

        checkout = self.root / "checkout"
        result = NORMALIZER.clone_clean_checkout(checkout, expected_head, bundle)

        self.assertTrue(result["clean"])
        self.assertEqual(result["head"], expected_head)
        self.assertEqual(result["remote"], NORMALIZER.EXPECTED_REMOTE)

    def test_bundle_path_must_be_exact_managed_input(self):
        artifact_root = self.root / "artifacts"
        incoming = artifact_root / "incoming"
        incoming.mkdir(parents=True)
        bundle = incoming / "operation.bundle"
        bundle.write_bytes(b"fixture")

        self.assertEqual(
            NORMALIZER.validate_bundle_path("operation", artifact_root, bundle), bundle
        )
        with self.assertRaisesRegex(NORMALIZER.NormalizationError, "受管输入文件"):
            NORMALIZER.validate_bundle_path("operation", artifact_root, self.root / "other.bundle")


if __name__ == "__main__":
    unittest.main()
