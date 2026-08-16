import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
VERIFY_PATH = ROOT / "dev" / "verify_p0_contracts.py"
SPEC = importlib.util.spec_from_file_location("verify_p0_contracts", VERIFY_PATH)
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class P0ContractVerificationTest(unittest.TestCase):
    def test_all_contracts_accept_positive_and_reject_negative_fixtures(self):
        result = subprocess.run(
            [sys.executable, str(VERIFY_PATH)],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("P0 数据合同验证通过", result.stdout)

    def test_strict_loader_rejects_duplicate_object_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schema_version":"v1","schema_version":"v2"}', encoding="utf-8")
            with self.assertRaisesRegex(VERIFY.ContractVerificationError, "字段重复"):
                VERIFY._load_json_strict(path)

    def test_strict_loader_rejects_non_finite_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nan.json"
            path.write_text('{"value":NaN}', encoding="utf-8")
            with self.assertRaisesRegex(VERIFY.ContractVerificationError, "非有限数值"):
                VERIFY._load_json_strict(path)

    def test_contract_documents_have_unique_json_keys(self):
        self.assertGreater(VERIFY._verify_json_files(), 0)

if __name__ == "__main__":
    unittest.main()
