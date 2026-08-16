from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import unittest
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s6.py"


def load_verifier():
    specification = importlib.util.spec_from_file_location(
        "s6_verifier_for_api_test", VERIFIER_PATH
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("无法加载 S6 校验器")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@unittest.skipUnless(
    importlib.util.find_spec("flask") is not None,
    "当前 Python 环境未安装 Flask；由 backend uv 环境执行",
)
class CountryOutageTrendS6ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verifier = load_verifier()
        cls.product = cls.verifier.build_candidate()

    def test_endpoint_returns_exact_candidate_and_stable_etag(self):
        from run import create_app

        app = create_app("testing")
        with patch(
            "web.api.v2.country_outages.get_country_outage_trend_product",
            return_value=self.product,
        ):
            first = app.test_client().get(
                "/api/v2/country-outages/incident-trend-s6/trend",
                query_string={"publication_id": "publication-trend-s6"},
            )
            second = app.test_client().get(
                "/api/v2/country-outages/incident-trend-s6/trend",
                query_string={"publication_id": "publication-trend-s6"},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json(), self.product)
        self.assertEqual(second.get_json(), self.product)
        self.assertEqual(first.headers["ETag"], second.headers["ETag"])
        self.assertIn(
            self.verifier.canonical_sha256(self.product)[:12],
            first.headers["ETag"],
        )
        self.assertEqual(
            self.verifier.canonical_sha256(first.get_json()),
            self.verifier.canonical_sha256(self.product),
        )

    def test_api_candidate_keeps_one_snapshot_across_graph_and_contexts(self):
        snapshot = self.product["snapshot"]
        self.assertEqual(snapshot["collector_id"], "rrc25")
        self.assertEqual(self.product["evidence_graph"]["snapshot"], snapshot)
        self.assertEqual(
            self.product["contexts"]["contemporaneous_reference"]["reference_identity"]["collector_id"],
            snapshot["collector_id"],
        )
        self.assertEqual(
            self.product["render_contract"]["source_product_id"],
            self.product["product_id"],
        )

    def test_canonical_json_download_is_replayable(self):
        downloaded = json.loads(self.verifier.canonical_json(self.product))
        self.assertEqual(downloaded, self.product)
        self.assertEqual(
            self.verifier.canonical_sha256(downloaded),
            "721da778b9aabac9c275012b858e42db637b65efd9a8ad09195b9dc4c152da22",
        )


if __name__ == "__main__":
    unittest.main()
