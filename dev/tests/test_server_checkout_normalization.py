import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


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
        self.assertEqual(
            NORMALIZER.EXPECTED_REMOTE,
            "git@github.com:xinghuahewo/domeye_.git",
        )
        self.assertEqual(NORMALIZER.safe_operation_id("20260818T120000Z-main"), "20260818T120000Z-main")
        with self.assertRaisesRegex(NORMALIZER.NormalizationError, "operation-id"):
            NORMALIZER.safe_operation_id("../escape")
        with self.assertRaisesRegex(NORMALIZER.NormalizationError, "官方 GitHub SSH"):
            NORMALIZER.validate_scope(
                NORMALIZER.EXPECTED_HOST,
                NORMALIZER.EXPECTED_SOURCE,
                NORMALIZER.EXPECTED_ARTIFACT_ROOT,
                "https://github.com/xinghuahewo/domeye_.git",
            )

    def test_official_ssh_probe_binds_exact_main_without_disclosing_output(self):
        expected_main = "a" * 40
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=f"{expected_main}\trefs/heads/main\n",
            stderr="",
        )

        injected_environment = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "url.https://example.invalid/.insteadOf",
            "GIT_CONFIG_VALUE_0": "git@github.com:",
            "GIT_CONFIG_PARAMETERS": "'credential.helper=leak'",
        }
        with (
            mock.patch.dict(os.environ, injected_environment, clear=False),
            mock.patch.object(NORMALIZER.subprocess, "run", return_value=completed) as runner,
        ):
            result = NORMALIZER.probe_official_ssh_main(expected_main)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["expectedCommit"], expected_main)
        self.assertEqual(result["observedCommit"], expected_main)
        arguments = runner.call_args.args[0]
        options = runner.call_args.kwargs
        self.assertEqual(arguments[:3], ["git", "ls-remote", "--exit-code"])
        self.assertEqual(arguments[3:], [NORMALIZER.EXPECTED_REMOTE, "refs/heads/main"])
        self.assertEqual(options["timeout"], NORMALIZER.SSH_PROBE_TIMEOUT_SECONDS)
        self.assertIn("BatchMode=yes", options["env"]["GIT_SSH_COMMAND"])
        self.assertIn("StrictHostKeyChecking=yes", options["env"]["GIT_SSH_COMMAND"])
        self.assertEqual(options["env"]["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(options["env"]["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(options["env"]["GIT_CONFIG"], os.devnull)
        self.assertEqual(options["cwd"], Path("/"))
        self.assertNotIn("GIT_CONFIG_COUNT", options["env"])
        self.assertNotIn("GIT_CONFIG_KEY_0", options["env"])
        self.assertNotIn("GIT_CONFIG_VALUE_0", options["env"])
        self.assertNotIn("GIT_CONFIG_PARAMETERS", options["env"])
        self.assertNotIn("GIT_DIR", options["env"])
        self.assertNotIn("GIT_WORK_TREE", options["env"])
        self.assertNotIn("GIT_OBJECT_DIRECTORY", options["env"])
        self.assertEqual(options["env"]["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(options["env"]["GIT_OPTIONAL_LOCKS"], "0")

    def test_official_ssh_probe_fails_closed_and_redacts_stderr(self):
        secret_marker = "secret-should-not-be-reported"
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=128,
            stdout="",
            stderr=f"Permission denied (publickey). {secret_marker}",
        )

        with mock.patch.object(NORMALIZER.subprocess, "run", return_value=completed):
            with self.assertRaises(NORMALIZER.NormalizationError) as captured:
                NORMALIZER.probe_official_ssh_main("b" * 40)

        self.assertIn("authentication_failed", str(captured.exception))
        self.assertNotIn(secret_marker, str(captured.exception))

    def test_official_ssh_probe_rejects_main_identity_drift(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=f"{'c' * 40}\trefs/heads/main\n",
            stderr="",
        )

        with mock.patch.object(NORMALIZER.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(
                NORMALIZER.NormalizationError, "main_identity_mismatch"
            ):
                NORMALIZER.probe_official_ssh_main("d" * 40)

    def test_official_ssh_probe_failure_precedes_any_mutation(self):
        source = self.root / "Domeye-Core"
        artifacts = self.root / "Domeye-Core-artifacts"
        source.mkdir()
        artifacts.mkdir()
        expected_source = "e" * 40
        expected_main = "f" * 40

        with (
            mock.patch.object(NORMALIZER, "EXPECTED_SOURCE", source),
            mock.patch.object(NORMALIZER, "EXPECTED_ARTIFACT_ROOT", artifacts),
            mock.patch.object(NORMALIZER, "PROTECTED_ROOTS", ()),
            mock.patch.object(NORMALIZER.socket, "gethostname", return_value=NORMALIZER.EXPECTED_HOST),
            mock.patch.object(NORMALIZER, "preflight", return_value={"sourceCheckout": {}}),
            mock.patch.object(
                NORMALIZER,
                "probe_official_ssh_main",
                side_effect=NORMALIZER.NormalizationError(
                    "官方 SSH 预检失败：transport_failed"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                NORMALIZER.NormalizationError, "transport_failed"
            ):
                NORMALIZER.normalize(
                    "fixture-no-mutation", expected_source, expected_main
                )

        self.assertTrue(source.is_dir())
        self.assertFalse((artifacts / "quarantine").exists())

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
        index_before = (source / ".git" / "index").read_bytes()

        original_links = NORMALIZER.ACTIVE_LINKS
        NORMALIZER.ACTIVE_LINKS = ()
        try:
            snapshot = NORMALIZER.preflight(source, artifact_root, expected_head)
        finally:
            NORMALIZER.ACTIVE_LINKS = original_links

        self.assertEqual(snapshot["sourceCheckout"]["changeCount"], 2)
        self.assertEqual(snapshot["sourceCheckout"]["remoteNames"], [])
        self.assertEqual((source / ".git" / "index").read_bytes(), index_before)
        self.assertFalse((artifact_root / "quarantine").exists())

    def test_git_snapshot_ignores_ambient_decoy_repository(self):
        source = self.root / "source"
        decoy = self.root / "decoy"
        for repository, filename in ((source, "source.txt"), (decoy, "decoy.txt")):
            repository.mkdir()
            run(["git", "init", "-b", "main"], repository)
            run(["git", "config", "user.email", "fixture@example.invalid"], repository)
            run(["git", "config", "user.name", "Fixture"], repository)
            (repository / filename).write_text(filename + "\n", encoding="utf-8")
            run(["git", "add", filename], repository)
            run(["git", "commit", "-m", filename], repository)
        source_head = run(["git", "rev-parse", "HEAD"], source)
        decoy_head = run(["git", "rev-parse", "HEAD"], decoy)
        self.assertNotEqual(source_head, decoy_head)

        with mock.patch.dict(
            os.environ,
            {
                "GIT_DIR": str(decoy / ".git"),
                "GIT_WORK_TREE": str(decoy),
                "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
            },
            clear=False,
        ):
            snapshot = NORMALIZER.git_snapshot(source)

        self.assertEqual(snapshot["head"], source_head)

    def test_bundle_clone_keeps_official_ssh_origin_without_network_fetch(self):
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

    def test_bundle_normalization_writes_v2_ssh_first_receipt(self):
        source = self.root / "Domeye-Core"
        artifacts = self.root / "Domeye-Core-artifacts"
        incoming = artifacts / "incoming"
        source.mkdir()
        incoming.mkdir(parents=True)
        run(["git", "init", "-b", "main"], source)
        run(["git", "config", "user.email", "fixture@example.invalid"], source)
        run(["git", "config", "user.name", "Fixture"], source)
        (source / "old.txt").write_text("old\n", encoding="utf-8")
        run(["git", "add", "old.txt"], source)
        run(["git", "commit", "-m", "old"], source)
        expected_source = run(["git", "rev-parse", "HEAD"], source)

        publisher = self.root / "publisher"
        publisher.mkdir()
        run(["git", "init", "-b", "main"], publisher)
        run(["git", "config", "user.email", "fixture@example.invalid"], publisher)
        run(["git", "config", "user.name", "Fixture"], publisher)
        (publisher / "new.txt").write_text("new\n", encoding="utf-8")
        run(["git", "add", "new.txt"], publisher)
        run(["git", "commit", "-m", "new"], publisher)
        expected_main = run(["git", "rev-parse", "HEAD"], publisher)
        operation = "fixture-bundle-normalization"
        bundle = incoming / f"{operation}.bundle"
        run(["git", "bundle", "create", str(bundle), "main"], publisher)

        with (
            mock.patch.object(NORMALIZER, "EXPECTED_SOURCE", source),
            mock.patch.object(NORMALIZER, "EXPECTED_ARTIFACT_ROOT", artifacts),
            mock.patch.object(NORMALIZER, "PROTECTED_ROOTS", ()),
            mock.patch.object(NORMALIZER, "ACTIVE_LINKS", ()),
            mock.patch.object(
                NORMALIZER.socket,
                "gethostname",
                return_value=NORMALIZER.EXPECTED_HOST,
            ),
        ):
            result = NORMALIZER.normalize(
                operation, expected_source, expected_main, bundle
            )

        self.assertEqual(
            result["schemaVersion"], "domeye.server-checkout-normalization/v2"
        )
        self.assertEqual(result["newCheckout"]["head"], expected_main)
        access = result["repositoryAccess"]
        self.assertEqual(access["policy"], "official_ssh_first_v1")
        self.assertEqual(access["origin"], NORMALIZER.EXPECTED_REMOTE)
        self.assertEqual(access["acquisition"], "local_immutable_git_bundle")
        self.assertFalse(access["networkGitHubAccessPerformed"])
        self.assertEqual(
            access["sshPreflight"]["status"], "not_required_bundle_input"
        )
        self.assertFalse(access["httpsFallback"]["performed"])
        receipt = Path(result["receiptPath"])
        self.assertTrue(receipt.is_file())
        self.assertEqual(
            receipt.stat().st_mode & 0o777,
            0o600,
        )

    def test_official_origin_rejects_pushurl_and_local_rewrite(self):
        checkout = self.root / "checkout"
        checkout.mkdir()
        run(["git", "init", "-b", "main"], checkout)
        run(
            ["git", "remote", "add", "origin", NORMALIZER.EXPECTED_REMOTE],
            checkout,
        )
        self.assertEqual(
            NORMALIZER.validate_official_origin(checkout),
            NORMALIZER.EXPECTED_REMOTE,
        )

        run(
            [
                "git",
                "config",
                "--local",
                "--add",
                "remote.origin.pushurl",
                "https://github.com/xinghuahewo/domeye_.git",
            ],
            checkout,
        )
        with self.assertRaisesRegex(
            NORMALIZER.NormalizationError, "不可改写的官方 GitHub SSH"
        ):
            NORMALIZER.validate_official_origin(checkout)

        run(
            ["git", "config", "--local", "--unset-all", "remote.origin.pushurl"],
            checkout,
        )
        run(
            [
                "git",
                "config",
                "--local",
                "url.https://github.com/xinghuahewo/domeye_.git.insteadOf",
                NORMALIZER.EXPECTED_REMOTE,
            ],
            checkout,
        )
        with self.assertRaisesRegex(
            NORMALIZER.NormalizationError, "不可改写的官方 GitHub SSH"
        ):
            NORMALIZER.validate_official_origin(checkout)

    def test_official_origin_rejects_extra_fetch_url_and_remote(self):
        checkout = self.root / "checkout-extra"
        checkout.mkdir()
        run(["git", "init", "-b", "main"], checkout)
        run(
            ["git", "remote", "add", "origin", NORMALIZER.EXPECTED_REMOTE],
            checkout,
        )
        run(
            [
                "git",
                "config",
                "--local",
                "--add",
                "remote.origin.url",
                "https://github.com/xinghuahewo/domeye_.git",
            ],
            checkout,
        )
        with self.assertRaisesRegex(
            NORMALIZER.NormalizationError, "不可改写的官方 GitHub SSH"
        ):
            NORMALIZER.validate_official_origin(checkout)

        run(
            [
                "git",
                "config",
                "--local",
                "--unset-all",
                "remote.origin.url",
            ],
            checkout,
        )
        run(
            [
                "git",
                "config",
                "--local",
                "remote.origin.url",
                NORMALIZER.EXPECTED_REMOTE,
            ],
            checkout,
        )
        run(
            ["git", "remote", "add", "backup", NORMALIZER.EXPECTED_REMOTE],
            checkout,
        )
        with self.assertRaisesRegex(
            NORMALIZER.NormalizationError, "不可改写的官方 GitHub SSH"
        ):
            NORMALIZER.validate_official_origin(checkout)

    def test_direct_clone_failure_does_not_retry_https_or_disclose_stderr(self):
        secret_marker = "secret-clone-stderr"
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=128,
            stdout="",
            stderr=f"Permission denied (publickey). {secret_marker}",
        )
        checkout = self.root / "checkout"

        with mock.patch.object(
            NORMALIZER.subprocess, "run", return_value=completed
        ) as runner:
            with self.assertRaises(NORMALIZER.NormalizationError) as captured:
                NORMALIZER.clone_clean_checkout(checkout, "1" * 40)

        self.assertEqual(runner.call_count, 1)
        arguments = runner.call_args.args[0]
        options = runner.call_args.kwargs
        self.assertIn(NORMALIZER.EXPECTED_REMOTE, arguments)
        self.assertNotIn("https://github.com", " ".join(arguments))
        self.assertEqual(options["env"]["GIT_CONFIG"], os.devnull)
        self.assertEqual(options["cwd"], Path("/"))
        self.assertIn("authentication_failed", str(captured.exception))
        self.assertNotIn(secret_marker, str(captured.exception))

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
