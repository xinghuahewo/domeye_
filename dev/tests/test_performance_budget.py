import json
import tempfile
import unittest
from pathlib import Path

from dev.performance_budget import DEFAULT_BUDGET, load_budget, nearest_rank_percentile


class PerformanceBudgetContractTest(unittest.TestCase):
    def test_fixed_profile_budget_covers_productized_readonly_surfaces(self):
        budget = load_budget(DEFAULT_BUDGET)

        self.assertEqual(budget["data_profile"], "feb-mar-2026")
        self.assertLessEqual(budget["manager_start_max_seconds"], 60)
        self.assertEqual(
            {endpoint["id"] for endpoint in budget["endpoints"]},
            {
                "dashboard_overview",
                "country_overview",
                "asn_overview",
                "event_evidence_bundle",
            },
        )
        self.assertTrue(all(endpoint["sample_count"] >= 20 for endpoint in budget["endpoints"]))

    def test_nearest_rank_percentile_does_not_interpolate_away_slow_samples(self):
        self.assertEqual(nearest_rank_percentile([1, 2, 3, 4, 100], 0.95), 100)

    def test_duplicate_endpoint_ids_fail_closed(self):
        invalid = {
            "schema_version": 1,
            "endpoints": [
                {
                    "id": "same",
                    "path": "/api/v1/one",
                    "sample_count": 2,
                    "first_sample_max_ms": 1,
                    "warm_p95_max_ms": 1,
                },
                {
                    "id": "same",
                    "path": "/api/v1/two",
                    "sample_count": 2,
                    "first_sample_max_ms": 1,
                    "warm_p95_max_ms": 1,
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "id 重复"):
                load_budget(path)


if __name__ == "__main__":
    unittest.main()
