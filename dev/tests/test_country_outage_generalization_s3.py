from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_generalization_s3.py"


def load_verifier_module():
    specification = importlib.util.spec_from_file_location(
        "verify_country_outage_generalization_s3",
        VERIFIER_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 S3 verifier：{VERIFIER_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageGeneralizationS3VerifierTest(unittest.TestCase):
    def test_formal_identities_are_frozen(self) -> None:
        module = load_verifier_module()
        self.assertEqual(
            module.EXPECTED["dataset_id"],
            "event_as_path_dataset_v1_027b658b0a3121f9ec41d33da3a01504",
        )
        self.assertEqual(module.EXPECTED["events"], 81)
        self.assertEqual(module.EXPECTED["affected_as"], 2_112)
        self.assertEqual(module.EXPECTED["relations"], 12_447)
        self.assertEqual(module.EXPECTED["concurrent_relations"], 5_011)
        self.assertEqual(module.EXPECTED["evidence_rows"], 5_093_251)

    def test_local_contract_and_record_are_closed(self) -> None:
        module = load_verifier_module()
        errors, checks = module.verify_local()
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(checks), 4)


if __name__ == "__main__":
    unittest.main()
