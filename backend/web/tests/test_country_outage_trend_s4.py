from __future__ import annotations

import importlib.util
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
    build_country_outage_trend_resources,
)
from services.country_outage_trend_product import (  # noqa: E402
    TrendProductValidationError,
    compile_country_outage_trend_product_from_resources,
)


class CountryOutageTrendS4ApiTest(unittest.TestCase):
    def test_resource_adapter_compiles_one_authoritative_product(self):
        overview, series, pages = build_country_outage_trend_resources()
        product = compile_country_outage_trend_product_from_resources(
            overview, series, pages
        )
        self.assertEqual(
            product["schema_version"], "country_outage_trend_product_v1"
        )
        self.assertEqual(
            product["snapshot"]["publication_id"],
            overview["publication_id"],
        )
        self.assertEqual(
            product["render_contract"]["source_product_id"],
            product["product_id"],
        )
        self.assertEqual(
            product["contexts"]["asn"]["asns"][0]["end_state"],
            "fully_visible",
        )
        self.assertEqual(
            product["contexts"]["asn"]["asns"][0][
                "per_family_state_status"
            ],
            "unavailable_in_current_observation_contract",
        )

    def test_resource_adapter_rejects_cross_publication_mix(self):
        overview, series, pages = build_country_outage_trend_resources()
        series["publication_id"] = "publication-conflict"
        with self.assertRaises(TrendProductValidationError) as captured:
            compile_country_outage_trend_product_from_resources(
                overview, series, pages
            )
        self.assertEqual(
            captured.exception.code, "resource_identity_conflict"
        )

    def test_asn_slot_population_indexes_rows_once(self):
        class CountingAsn:
            calls = 0

            def __init__(self, value):
                self.value = value

            def __int__(self):
                type(self).calls += 1
                return self.value

        overview, series, pages = build_country_outage_trend_resources()
        template = pages[0]["items"][0]
        pages[0]["items"] = [
            {**template, "asn": CountingAsn(64500 + index)}
            for index in range(24)
        ]

        product = compile_country_outage_trend_product_from_resources(
            overview, series, pages
        )

        self.assertEqual(product["contexts"]["asn"]["asn_count"], 24)
        self.assertLessEqual(CountingAsn.calls, 24)

    @unittest.skipUnless(
        importlib.util.find_spec("flask") is not None,
        "当前 Python 环境未安装 Flask；由 backend uv 环境执行",
    )
    def test_trend_endpoint_returns_etag_from_same_product(self):
        from run import create_app

        app = create_app("testing")
        product = build_country_outage_trend_product()
        with patch(
            "web.api.v2.country_outages.get_country_outage_trend_product",
            return_value=product,
        ):
            response = app.test_client().get(
                "/api/v2/country-outages/incident-current-trend/trend",
                query_string={"publication_id": "publication-current-trend"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["product_id"], product["product_id"]
        )
        self.assertTrue(response.headers["ETag"])


if __name__ == "__main__":
    unittest.main()
