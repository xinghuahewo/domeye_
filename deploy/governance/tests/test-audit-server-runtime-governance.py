#!/usr/bin/env python3

"""S5 开发数据连续只读取证的最小单元测试。"""

from __future__ import annotations

import importlib.util
import argparse
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "audit-server-runtime-governance.py"
SPEC = importlib.util.spec_from_file_location("runtime_governance_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def inventory(*, active_locks: list[str] | None = None, bytes_count: int = 1) -> dict[str, object]:
    return {
        "path": "/managed/research-run",
        "coverageComplete": True,
        "manifestEvidenceCoverageComplete": True,
        "activeLockPaths": active_locks or [],
        "allocatedBytesApproximate": bytes_count,
    }


class ReferenceProofTests(unittest.TestCase):
    def test_two_clean_snapshots_only_report_observed_no_live_reference(self) -> None:
        result = AUDIT.reference_proof(
            inventory(),
            inventory(),
            [],
            [],
            [],
            [],
            observation_seconds=60,
            coverage_complete=True,
        )

        self.assertEqual("observed_no_live_reference", result["state"])
        self.assertTrue(result["inventoryStable"])
        self.assertFalse(result["coverageComplete"] is False)

    def test_change_or_lock_fails_closed(self) -> None:
        changed = AUDIT.reference_proof(
            inventory(),
            inventory(bytes_count=2),
            [],
            [],
            [],
            [],
            observation_seconds=60,
            coverage_complete=True,
        )
        locked = AUDIT.reference_proof(
            inventory(),
            inventory(active_locks=["/managed/research-run/data.lock"]),
            [],
            [],
            [],
            [],
            observation_seconds=60,
            coverage_complete=True,
        )

        self.assertEqual("reference_or_change_observed", changed["state"])
        self.assertEqual("reference_or_change_observed", locked["state"])

    def test_one_snapshot_is_not_proof(self) -> None:
        result = AUDIT.reference_proof(
            inventory(),
            None,
            [],
            None,
            [],
            None,
            observation_seconds=0,
            coverage_complete=True,
        )

        self.assertEqual("not_requested", result["state"])
        self.assertIsNone(result["finalInventorySha256"])

    def test_incomplete_scan_fails_closed(self) -> None:
        result = AUDIT.reference_proof(
            inventory(),
            inventory(),
            [],
            [],
            [],
            [],
            observation_seconds=60,
            coverage_complete=False,
        )

        self.assertEqual("coverage_incomplete", result["state"])

    def test_observation_seconds_rejects_out_of_range_values(self) -> None:
        self.assertEqual(0, AUDIT.observation_seconds("0"))
        self.assertEqual(3600, AUDIT.observation_seconds("3600"))
        with self.assertRaises(argparse.ArgumentTypeError):
            AUDIT.observation_seconds("-1")

    def test_root_level_files_are_listed_and_never_made_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "experiment").mkdir()
            (root / "selection.json").write_text("{}", encoding="utf-8")
            policy = {
                "runtimeGovernance": {
                    "manifestFileNames": ["RELEASE-MANIFEST.json"],
                    "maxEntriesPerObject": 100,
                    "maxManifestBytes": 1024,
                }
            }
            snapshot = {"coverageComplete": True, "processes": [], "mountPoints": [], "_lockedInodes": {}}
            result = AUDIT.development_data_discovery(
                {"name": "fixture", "path": str(root)},
                policy,
                snapshot,
                snapshot,
                snapshot,
            )

        self.assertEqual(["selection.json"], [Path(item["path"]).name for item in result["rootNonDirectoryEntries"]])
        self.assertEqual("protected_or_unknown", result["objects"][0]["retentionState"])


if __name__ == "__main__":
    unittest.main()
