from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
for path in (REPOSITORY_ROOT, BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from dev.tests.build_country_outage_trend_product import (  # noqa: E402
    build_contemporaneous_reference_input,
    build_country_outage_trend_product,
    build_country_outage_trend_resources,
)
from services.country_outage_trend_product import (  # noqa: E402
    answer_trend_question_v1,
    compile_country_outage_trend_product_from_resources,
    get_country_outage_trend_product,
)


class CountryOutageTrendS5WebContractTest(unittest.TestCase):
    def test_serialized_product_keeps_reference_claim_for_all_surfaces(
        self,
    ) -> None:
        product = build_country_outage_trend_product()
        context = product["contexts"]["contemporaneous_reference"]
        self.assertEqual(context["status"], "complete")
        self.assertEqual(product["snapshot"]["collector_id"], "rrc25")
        answer = answer_trend_question_v1(product, "查看同期分布位置")
        self.assertEqual(answer["status"], "answered")
        self.assertEqual(answer["operator"], "contemporaneous_reference")

    def test_resource_adapter_attaches_reference_before_serialization(
        self,
    ) -> None:
        overview, series, pages = build_country_outage_trend_resources()
        without_reference = compile_country_outage_trend_product_from_resources(
            overview, series, pages
        )
        reference = build_contemporaneous_reference_input(
            without_reference["profile"]
        )
        product = compile_country_outage_trend_product_from_resources(
            overview,
            series,
            pages,
            contemporaneous_reference=reference,
        )
        self.assertEqual(
            product["contexts"]["contemporaneous_reference"]["status"],
            "complete",
        )
        self.assertIn(
            "contemporaneous_reference",
            {
                node.get("claim_kind")
                for node in product["evidence_graph"]["nodes"]
            },
        )

    @unittest.skipUnless(
        importlib.util.find_spec("dateutil") is not None,
        "当前 Python 环境未安装后端依赖；由 backend uv 环境执行",
    )
    def test_configured_frozen_reference_is_attached_by_read_service(
        self,
    ) -> None:
        overview, series, pages = build_country_outage_trend_resources()
        base = compile_country_outage_trend_product_from_resources(
            overview, series, pages
        )
        reference = build_contemporaneous_reference_input(base["profile"])
        reference.pop("target_country_code")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.json"
            path.write_text(json.dumps(reference), encoding="utf-8")
            with (
                patch.dict(
                    os.environ,
                    {"DOMEYE_RRC25_CONTEMPORANEOUS_REFERENCE": str(path)},
                ),
                patch(
                    "services.country_outage_service."
                    "get_country_outage_overview",
                    return_value=overview,
                ),
                patch(
                    "services.country_outage_service."
                    "get_country_outage_series",
                    return_value=series,
                ),
                patch(
                    "services.country_outage_service."
                    "get_country_outage_asns",
                    return_value=pages[0],
                ),
            ):
                product = get_country_outage_trend_product(
                    overview["incident_id"],
                    publication_id=overview["publication_id"],
                )
        self.assertEqual(
            product["contexts"]["contemporaneous_reference"][
                "target_country_code"
            ],
            overview["event_identity"]["country_code"],
        )


if __name__ == "__main__":
    unittest.main()
