import json
from datetime import datetime
from pathlib import Path
import unittest


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "api-snapshot.json"


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00").replace("T", " "))


class DevelopmentFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.window = cls.fixture["data_window"]

    def test_window_is_fixed_to_february_and_march(self):
        self.assertEqual(self.window["start_time"], "2026-02-01 00:00:00")
        self.assertEqual(self.window["end_time"], "2026-03-31 23:59:59")
        self.assertEqual(self.window["timezone"], "Asia/Shanghai")

    def test_fixture_hits_both_window_boundaries(self):
        observed = []
        observed.extend(item["start_time"] for item in self.fixture["events"]["data"])
        observed.extend(item["t"] for item in self.fixture["features"])
        for series in self.fixture["outages"].values():
            observed.extend(item["time_slot"] for item in series)

        start = parse_time(self.window["start_time"])
        end = parse_time(self.window["end_time"])
        parsed = [parse_time(value) for value in observed]
        self.assertTrue(all(start <= value <= end for value in parsed))
        self.assertEqual(min(parsed).date().isoformat(), "2026-02-01")
        self.assertEqual(max(parsed).date().isoformat(), "2026-03-31")

    def test_fixture_contains_all_six_event_types(self):
        event_types = {item["event_type"] for item in self.fixture["events"]["data"]}
        self.assertEqual(event_types, {
            "前缀劫持",
            "子前缀劫持",
            "路由泄漏",
            "前缀中断",
            "AS中断",
            "国家中断",
        })

    def test_fixture_has_distinct_contract_shaped_feature_lists(self):
        countries = self.fixture["country_features"]["data"]
        ases = self.fixture["as_features"]["data"]
        self.assertTrue(all(set(item) == {"country", "time_series_data"} for item in countries))
        self.assertTrue(all(
            set(item) == {"asn", "as_name", "country", "org_name", "time_series_data"}
            for item in ases
        ))
        self.assertEqual(set(self.fixture["event_details"]), {
            "hijack", "sub_hijack", "leak",
            "prefix_outage", "as_outage", "country_outage",
        })


if __name__ == "__main__":
    unittest.main()
