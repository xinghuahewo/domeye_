import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "contracts" / "research"
VALIDATOR = ROOT / "dev" / "data_quality" / "validate_research_contracts.cjs"


class Rrc25ResearchContractTest(unittest.TestCase):
    def test_all_research_contract_fixtures(self):
        result = subprocess.run(
            ["node", str(VALIDATOR)],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("研究数据合同验证通过", result.stdout)

    def test_output_contracts_are_strict_json_schema_2020_12(self):
        names = {
            "research-run",
            "country-outage-sample",
            "country-outage-episode",
            "country-outage-wave",
            "country-outage-episode-as",
            "reconciliation-result",
            "incident-episode-mapping",
        }
        for name in names:
            schema = json.loads((CONTRACT_ROOT / f"{name}.schema.json").read_text(encoding="utf-8"))
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertTrue(schema["$id"])
            self.assertIs(schema["additionalProperties"], False)

    def test_required_boundary_fixtures_exist(self):
        expected = [
            "fixtures/country-outage-sample/invalid-unknown-filled-with-zero.json",
            "fixtures/country-outage-sample/invalid-ratio-components-from-different-snapshots.json",
            "fixtures/country-outage-episode/invalid-partial-recovery-with-fabricated-end.json",
            "fixtures/country-outage-wave/invalid-causal-precursor-label.json",
            "fixtures/country-outage-episode-as/invalid-dual-stack-label-when-ipv6-visible.json",
            "fixtures/reconciliation-result/invalid-rrc25-only-causal-overreach.json",
            "fixtures/incident-episode-mapping/invalid-content-id.json",
        ]
        for relative in expected:
            self.assertTrue((CONTRACT_ROOT / relative).is_file(), relative)

    def test_sample_ratio_carries_component_snapshot_identity(self):
        schema = json.loads(
            (CONTRACT_ROOT / "country-outage-sample.schema.json").read_text(encoding="utf-8")
        )
        component = schema["$defs"]["ratioComponent"]
        self.assertIn("sample_id", component["required"])
        self.assertIn("snapshot_id", component["required"])
        self.assertIn("value", component["required"])

    def test_run_resource_contract_uses_decimal_approval_boundaries(self):
        schema = json.loads(
            (CONTRACT_ROOT / "research-run.schema.json").read_text(encoding="utf-8")
        )
        execution = schema["$defs"]["execution"]["properties"]
        self.assertEqual(
            execution["new_raw_bytes_read"]["exclusiveMaximum"], 50_000_000_000
        )
        self.assertEqual(
            execution["peak_temporary_bytes"]["exclusiveMaximum"], 5_000_000_000
        )
        self.assertEqual(execution["max_worker_seconds"]["exclusiveMaximum"], 600)


if __name__ == "__main__":
    unittest.main()
