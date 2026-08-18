import importlib.util
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "governance" / "install-server-directory-audit-schedule.py"
SPEC = importlib.util.spec_from_file_location("install_server_directory_audit_schedule", SCRIPT)
SCHEDULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCHEDULE)


class ServerDirectoryAuditScheduleTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.governance = self.root / "Domeye-Core-governance"
        self.systemd = self.root / "systemd"
        self.source = self.root / "source"
        self.source.mkdir()
        for name in SCHEDULE.SOURCE_FILES:
            (self.source / name).write_text(f"fixture {name}\n", encoding="utf-8")
        self.calls = []
        self.original = {
            "GOVERNANCE_ROOT": SCHEDULE.GOVERNANCE_ROOT,
            "SYSTEMD_ROOT": SCHEDULE.SYSTEMD_ROOT,
            "RELEASES_ROOT": SCHEDULE.RELEASES_ROOT,
            "CURRENT_LINK": SCHEDULE.CURRENT_LINK,
            "WRAPPER": SCHEDULE.WRAPPER,
            "INSTALLATIONS": SCHEDULE.INSTALLATIONS,
            "ROLLBACKS": SCHEDULE.ROLLBACKS,
            "EXPECTED_HOST": SCHEDULE.EXPECTED_HOST,
            "validate_source": SCHEDULE.validate_source,
            "run": SCHEDULE.run,
            "geteuid": SCHEDULE.os.geteuid,
        }
        SCHEDULE.GOVERNANCE_ROOT = self.governance
        SCHEDULE.SYSTEMD_ROOT = self.systemd
        SCHEDULE.RELEASES_ROOT = self.governance / "directory-audit" / "releases"
        SCHEDULE.CURRENT_LINK = self.governance / "directory-audit" / "current"
        SCHEDULE.WRAPPER = self.governance / "bin" / "run-server-directory-audit"
        SCHEDULE.INSTALLATIONS = self.governance / "installations"
        SCHEDULE.ROLLBACKS = self.governance / "directory-audit" / "rollbacks"
        SCHEDULE.EXPECTED_HOST = socket.gethostname()
        SCHEDULE.validate_source = lambda source_dir, expected_main: {
            "repository": "fixture",
            "head": expected_main,
            "originMain": expected_main,
            "sources": {},
        }
        SCHEDULE.run = self.fake_run
        SCHEDULE.os.geteuid = lambda: 0

    def tearDown(self):
        for name, value in self.original.items():
            if name == "geteuid":
                SCHEDULE.os.geteuid = value
            else:
                setattr(SCHEDULE, name, value)
        self.temporary.cleanup()

    def fake_run(self, arguments, cwd=None):
        self.calls.append(tuple(arguments))
        if arguments[:2] == ["systemctl", "is-enabled"]:
            return subprocess.CompletedProcess(arguments, 0, "enabled\n", "")
        if arguments[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(arguments, 0, "active\n", "")
        if arguments[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(arguments, 0, "Tue 2026-08-19 03:15:00 UTC\n", "")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def seed_previous_installation(self):
        SCHEDULE.WRAPPER.parent.mkdir(parents=True)
        SCHEDULE.WRAPPER.write_text("old wrapper\n", encoding="utf-8")
        SCHEDULE.WRAPPER.chmod(0o740)
        previous = self.governance / "directory-audit" / "releases" / "previous"
        previous.mkdir(parents=True)
        SCHEDULE.CURRENT_LINK.parent.mkdir(parents=True, exist_ok=True)
        SCHEDULE.CURRENT_LINK.symlink_to(previous)
        for path in SCHEDULE.unit_payloads():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"old {path.name}\n", encoding="utf-8")
            path.chmod(0o640)
        return previous

    def test_install_copies_versioned_sources_and_activates_all_timers(self):
        previous = self.seed_previous_installation()

        result = SCHEDULE.install("fixture-s6", "a" * 40, self.source)

        release = SCHEDULE.RELEASES_ROOT / "fixture-s6"
        self.assertEqual(SCHEDULE.CURRENT_LINK.resolve(), release.resolve())
        self.assertEqual((release / "audit-server-layout.py").read_text(encoding="utf-8"), "fixture audit-server-layout.py\n")
        self.assertTrue(Path(result["receiptPath"]).is_file())
        self.assertEqual(oct(Path(result["receiptPath"]).stat().st_mode & 0o777), "0o600")
        self.assertTrue(any(call[:3] == ("systemctl", "enable", "--now") for call in self.calls))
        self.assertTrue(all(item["active"] == "active" for item in result["readback"]["timers"].values()))
        self.assertEqual(len(result["snapshots"]), 6)
        self.assertEqual(previous.name, "previous")

    def test_rollback_restores_previous_files_links_and_modes(self):
        previous = self.seed_previous_installation()
        result = SCHEDULE.install("fixture-s6", "b" * 40, self.source)

        rollback = SCHEDULE.rollback("fixture-s6")

        self.assertEqual(SCHEDULE.CURRENT_LINK.resolve(), previous.resolve())
        self.assertEqual(SCHEDULE.WRAPPER.read_text(encoding="utf-8"), "old wrapper\n")
        self.assertEqual(oct(SCHEDULE.WRAPPER.stat().st_mode & 0o777), "0o740")
        for path in SCHEDULE.unit_payloads():
            self.assertEqual(path.read_text(encoding="utf-8"), f"old {path.name}\n")
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o640")
        self.assertTrue(Path(rollback["rollbackReceiptPath"]).is_file())
        self.assertIn(("systemctl", "disable", "--now", *SCHEDULE.TIMER_NAMES), self.calls)
        self.assertEqual(result["operationId"], rollback["operationId"])

    def test_rollback_refuses_to_overwrite_newer_wrapper(self):
        self.seed_previous_installation()
        SCHEDULE.install("fixture-s6", "c" * 40, self.source)
        SCHEDULE.WRAPPER.write_text("newer wrapper\n", encoding="utf-8")

        with self.assertRaisesRegex(SCHEDULE.ScheduleError, "wrapper 已变化"):
            SCHEDULE.rollback("fixture-s6")

    def test_operation_id_rejects_path_escape(self):
        with self.assertRaisesRegex(SCHEDULE.ScheduleError, "operation-id"):
            SCHEDULE.safe_operation_id("../escape")

    def test_expected_main_rejects_abbreviated_or_non_hex_sha(self):
        with self.assertRaisesRegex(SCHEDULE.ScheduleError, "40 位"):
            SCHEDULE.safe_commit("a" * 12)
        with self.assertRaisesRegex(SCHEDULE.ScheduleError, "40 位"):
            SCHEDULE.safe_commit("G" * 40)


if __name__ == "__main__":
    unittest.main()
