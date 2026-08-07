from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = (
    REPOSITORY_ROOT
    / "contracts"
    / "data"
    / "country-outage-general-read-model.schema.json"
)
OPENAPI_PATH = REPOSITORY_ROOT / "contracts" / "openapi.json"
BUILDER_PATH = REPOSITORY_ROOT / "tools" / "build_country_outage_general_read_model.py"


class CountryOutageGeneralizationS4ContractTest(unittest.TestCase):
    def test_store_contract_freezes_scope_identity_and_bounded_reads(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        properties = schema["properties"]
        self.assertEqual(
            properties["window_start_utc"]["const"],
            "2026-02-24T00:00:00Z",
        )
        self.assertEqual(
            properties["window_end_exclusive_utc"]["const"],
            "2026-03-11T00:00:00Z",
        )
        self.assertEqual(properties["collector_id"]["const"], "rrc25")
        self.assertEqual(
            properties["pagination_semantics"]["const"],
            "stable_server_side_pages_maximum_60_items",
        )
        self.assertEqual(
            properties["path_evidence_semantics"]["const"],
            "bounded_real_path_samples_full_evidence_remains_in_s3_audit_artifact",
        )
        event = schema["$defs"]["event"]
        self.assertIn("publication_id", event["required"])
        self.assertIn("data_through", event["required"])
        self.assertIn("is_final_in_data_range", event["required"])

    def test_openapi_exposes_stable_pages_and_no_full_path_matrix(self) -> None:
        contract = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
        path = contract["paths"][
            "/api/v2/country-outages/{incident_id}/path-downstreams"
        ]["get"]
        parameters = {item["name"]: item for item in path["parameters"]}
        self.assertEqual(parameters["page_size"]["schema"]["maximum"], 60)
        item = contract["components"]["schemas"][
            "CountryOutageGeneralPathDownstreamItemV1"
        ]
        self.assertEqual(item["properties"]["path_samples"]["maxItems"], 3)
        self.assertNotIn("path_evidence", item["properties"])

    def test_builder_rejects_unbound_implementation_identity(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(BUILDER_PATH),
                "--event-cohorts",
                "/not-used",
                "--event-metrics",
                "/not-used",
                "--event-as-path",
                "/not-used",
                "--output",
                "/not-used",
                "--event-cohort-implementation-id",
                "git:" + "1" * 40,
                "--event-metric-implementation-id",
                "git:" + "2" * 40,
                "--event-as-path-implementation-id",
                "git:" + "3" * 40,
                "--implementation-id",
                "unbound",
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("git:<40位SHA>", payload["error"])


if __name__ == "__main__":
    unittest.main()
