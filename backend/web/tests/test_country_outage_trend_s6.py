from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
for path in (REPOSITORY_ROOT, BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from dev.tests.build_country_outage_trend_product import (  # noqa: E402
    build_country_outage_trend_product,
    canonical_json,
    canonical_sha256,
)


@unittest.skipUnless(
    importlib.util.find_spec("flask") is not None,
    "当前 Python 环境未安装 Flask；由 backend uv 环境执行",
)
class CountryOutageTrendS6ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.product = build_country_outage_trend_product()

    def test_endpoint_returns_exact_candidate_and_stable_etag(self):
        from run import create_app

        app = create_app("testing")
        with patch(
            "web.api.v2.country_outages.get_country_outage_trend_product",
            return_value=self.product,
        ):
            first = app.test_client().get(
                "/api/v2/country-outages/incident-current-trend/trend",
                query_string={"publication_id": "publication-current-trend"},
            )
            second = app.test_client().get(
                "/api/v2/country-outages/incident-current-trend/trend",
                query_string={"publication_id": "publication-current-trend"},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json(), self.product)
        self.assertEqual(second.get_json(), self.product)
        self.assertEqual(first.headers["ETag"], second.headers["ETag"])
        self.assertIn(
            canonical_sha256(self.product)[:12], first.headers["ETag"]
        )
        self.assertEqual(
            canonical_sha256(first.get_json()),
            canonical_sha256(self.product),
        )

    def test_api_candidate_keeps_one_snapshot_across_graph_and_contexts(self):
        snapshot = self.product["snapshot"]
        self.assertEqual(snapshot["collector_id"], "rrc25")
        self.assertEqual(self.product["evidence_graph"]["snapshot"], snapshot)
        self.assertEqual(
            self.product["contexts"]["contemporaneous_reference"][
                "reference_identity"
            ]["collector_id"],
            snapshot["collector_id"],
        )
        self.assertEqual(
            self.product["render_contract"]["source_product_id"],
            self.product["product_id"],
        )

    def test_canonical_json_download_is_replayable(self):
        downloaded = json.loads(canonical_json(self.product))
        self.assertEqual(downloaded, self.product)
        self.assertEqual(
            canonical_sha256(downloaded),
            "721da778b9aabac9c275012b858e42db637b65efd9a8ad09195b9dc4c152da22",
        )


if __name__ == "__main__":
    unittest.main()
