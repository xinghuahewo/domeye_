from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_generalization_s0.py"


def load_verifier_module():
    specification = importlib.util.spec_from_file_location(
        "verify_country_outage_generalization_s0",
        VERIFIER_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 S0 verifier：{VERIFIER_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageGeneralizationS0Test(unittest.TestCase):
    def test_current_s0_baseline_passes(self) -> None:
        module = load_verifier_module()
        result = module.verify()
        self.assertEqual(result["status"], "pass", result["errors"])
        self.assertEqual(result["stage"], "S0")

    def test_cli_emits_machine_readable_pass(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VERIFIER_PATH)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["schema_version"], "country_outage_generalization_s0_verification_v1")

    def test_missing_targeted_session_scan_boundary_fails(self) -> None:
        module = load_verifier_module()
        original = module.BASELINE_PATH.read_text(encoding="utf-8")
        changed = original.replace(
            "4,320 个 RRC25 UPDATE MRT",
            "若干输入文件",
        )
        self.assertNotEqual(changed, original)
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / module.BASELINE_PATH.name
            candidate.write_text(changed, encoding="utf-8")
            module.BASELINE_PATH = candidate
            result = module.verify()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(
            any("4,320 个 RRC25 UPDATE MRT" in error for error in result["errors"]),
            result["errors"],
        )

    def test_s0_cannot_claim_all_gfas_passed(self) -> None:
        module = load_verifier_module()
        original = module.BASELINE_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / module.BASELINE_PATH.name
            candidate.write_text(
                original + "\nGFA-01 至 GFA-16 全部通过\n",
                encoding="utf-8",
            )
            module.BASELINE_PATH = candidate
            result = module.verify()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(
            any("越级" in error for error in result["errors"]),
            result["errors"],
        )


if __name__ == "__main__":
    unittest.main()
