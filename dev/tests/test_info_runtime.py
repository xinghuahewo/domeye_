import unittest

from backend.info_pipeline.runtime import (
    ExactLookupMapping,
    FullTableLoadRejected,
    QueryTelemetry,
)
from backend.info_pipeline.s4 import (
    S4AcceptanceError,
    _ensure_safe_report,
    _percentile,
    _regression_percent,
    _result_differences,
)


class ExactLookupMappingTests(unittest.TestCase):
    def test_exact_lookup_caches_and_rejects_full_iteration(self):
        calls = []
        telemetry = QueryTelemetry()
        mapping = ExactLookupMapping(
            name="fixture",
            fetch=lambda key: calls.append(key) or {"key": key},
            count=lambda: 7,
            telemetry=telemetry,
            normalize_key=str,
        )

        self.assertEqual(mapping["1"], {"key": "1"})
        self.assertEqual(mapping["1"], {"key": "1"})
        self.assertIn("1", mapping.keys())
        self.assertEqual(calls, ["1"])
        self.assertEqual(len(mapping), 7)
        with self.assertRaises(FullTableLoadRejected):
            list(mapping)
        self.assertEqual(telemetry.full_table_load_count, 1)

    def test_missing_exact_key_is_cached(self):
        calls = []
        telemetry = QueryTelemetry()
        mapping = ExactLookupMapping(
            name="fixture",
            fetch=lambda key: calls.append(key) or None,
            count=lambda: 0,
            telemetry=telemetry,
            normalize_key=str,
        )
        self.assertNotIn("missing", mapping)
        self.assertIsNone(mapping.get("missing"))
        self.assertEqual(calls, ["missing"])


class S4EvidenceUtilityTests(unittest.TestCase):
    def test_percentiles_and_regression_are_deterministic(self):
        self.assertEqual(_percentile([4, 1, 2, 3], 0.95), 4)
        self.assertEqual(_percentile([4, 1, 2, 3], 0.5), 2)
        self.assertEqual(_regression_percent(9, 10), -10)

    def test_event_differences_are_per_event_type(self):
        left = {
            name: {"event_count": 1}
            for name in (
                "hijack",
                "sub_hijack",
                "leak",
                "prefix_outage",
                "as_outage",
                "country_outage",
            )
        }
        right = {name: dict(value) for name, value in left.items()}
        self.assertEqual(_result_differences(left, right), [])
        right["leak"]["event_count"] = 0
        differences = _result_differences(left, right)
        self.assertEqual(
            [item["event_type"] for item in differences],
            ["leak"],
        )

    def test_evidence_rejects_contact_fields(self):
        _ensure_safe_report({"status": "pass"})
        with self.assertRaises(S4AcceptanceError):
            _ensure_safe_report(
                {"status": "pass", "admin_info": "secret"}
            )


if __name__ == "__main__":
    unittest.main()
