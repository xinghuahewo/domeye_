from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.country_outage_trend_product import (
    TrendProductValidationError,
    compile_country_outage_trend_product_from_resources,
)


VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s4.py"


def load_verifier():
    specification = importlib.util.spec_from_file_location(
        "s4_verifier_for_api_test", VERIFIER_PATH
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("无法加载 S4 校验器")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def resources():
    candidate = load_verifier().build_candidate()
    profile = candidate["profile"]
    snapshot = profile["snapshot"]
    common = {
        "incident_id": snapshot["incident_id"],
        "publication_id": snapshot["publication_id"],
        "revision": snapshot["revision"],
        "data_through": snapshot["data_through"],
        "cohort_id": "cohort-s4-api",
        "window_start_utc": snapshot["window_start_utc"],
        "window_end_utc": snapshot["window_end_utc"],
        "is_final": snapshot["is_final"],
    }
    overview = {
        "schema_version": "country_outage_overview_v2",
        **common,
        "event_identity": {
            "incident_id": snapshot["incident_id"],
            "legacy_reference": snapshot["event_reference"],
            "event_type": "country_outage",
            "country_code": snapshot["country_code"],
            "country_name": "验收国家",
            "display_name": "验收国家国家中断",
        },
        "observation_scope": {
            "collector_id": "rrc25",
            "collector_ids": ["rrc25"],
            "collector_count": 1,
            "window_start_utc": snapshot["window_start_utc"],
            "window_end_utc": snapshot["window_end_utc"],
            "timezone": snapshot["timezone"],
            "interval_seconds": profile["time_grid"]["slot_seconds"],
        },
        "cohort": {
            "cohort_id": "cohort-s4-api",
            "prefix_vp_count": profile["metric"]["denominator"]["value"],
        },
        "capabilities": {"trend_analysis": {"state": "available"}},
    }
    series = {
        "schema_version": "country_outage_series_v2",
        **common,
        "interval_seconds": profile["time_grid"]["slot_seconds"],
        "series": [
            {
                "observed_at_utc": slot["observed_at_utc"],
                "slot_state": slot["state"],
                "visible_prefix_vp_count": slot["value"],
                "update_total": index * 10,
                "announce_count": index * 7,
                "withdraw_count": index * 3,
            }
            for index, slot in enumerate(profile["slots"])
        ],
    }
    slot_count = len(profile["slots"])
    asn_page = {
        "schema_version": "country_outage_asn_page_v2",
        **common,
        "page": 1,
        "page_count": 1,
        "items": [
            {
                "asn": "64500",
                "address_families": [4, 6],
                "baseline_prefix_count": 2,
                "baseline_prefix_vp_count": 8,
                "states": [0, *([1] * max(0, slot_count - 2)), 0],
            }
        ],
    }
    return overview, series, [asn_page]


class CountryOutageTrendS4ApiTest(unittest.TestCase):
    def test_resource_adapter_compiles_one_authoritative_product(self):
        overview, series, pages = resources()
        product = compile_country_outage_trend_product_from_resources(
            overview, series, pages
        )
        self.assertEqual(
            product["schema_version"], "country_outage_trend_product_v1"
        )
        self.assertEqual(
            product["snapshot"]["publication_id"], overview["publication_id"]
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
            product["contexts"]["asn"]["asns"][0]["per_family_state_status"],
            "unavailable_in_current_observation_contract",
        )

    def test_resource_adapter_rejects_cross_publication_mix(self):
        overview, series, pages = resources()
        series["publication_id"] = "publication-conflict"
        with self.assertRaises(TrendProductValidationError) as captured:
            compile_country_outage_trend_product_from_resources(
                overview, series, pages
            )
        self.assertEqual(captured.exception.code, "resource_identity_conflict")

    @unittest.skipUnless(
        importlib.util.find_spec("flask") is not None,
        "当前 Python 环境未安装 Flask；由 backend uv 环境执行",
    )
    def test_trend_endpoint_returns_etag_from_same_product(self):
        from run import create_app

        app = create_app("testing")
        product = load_verifier().build_candidate()
        with patch(
            "web.api.v2.country_outages.get_country_outage_trend_product",
            return_value=product,
        ):
            response = app.test_client().get(
                "/api/v2/country-outages/incident-s4/trend",
                query_string={"publication_id": "publication-s4"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["product_id"], product["product_id"])
        self.assertTrue(response.headers["ETag"])


if __name__ == "__main__":
    unittest.main()
