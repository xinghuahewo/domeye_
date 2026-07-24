from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import unittest

from backend.core.country_outage_v2 import (
    CountryOutageV2Error,
    build_live_observation,
    legacy_peak_projection,
    new_runtime_state,
    reduce_live_observation,
)


def local_time(index: int) -> str:
    return (
        datetime(2026, 2, 28, 18, 0, 0) + timedelta(minutes=5 * index)
    ).strftime("%Y-%m-%d %H:%M:%S")


def observation(index: int, affected: int, baseline=range(1, 101)) -> dict:
    baseline_values = list(baseline)
    outage = baseline_values[-affected:] if affected else []
    normal = sorted(set(baseline_values) - set(outage))
    return build_live_observation(
        source="r",
        country_code="IR",
        observed_at_local=local_time(index),
        outage_asns=outage,
        normal_asns=normal,
        baseline_asns=baseline_values,
    )


class CountryOutageLiveV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.state = new_runtime_state(
            source="r",
            country_code="IR",
            collector_id="legacy_live",
            baseline_asns=range(1, 101),
        )

    def consume(self, row: dict) -> dict:
        result = reduce_live_observation(self.state, row)
        self.state = result["state"]
        return result

    def test_two_slots_confirm_and_detected_differs_from_onset(self) -> None:
        first = self.consume(observation(0, 4))
        self.assertEqual(first["lifecycle_action"], "none")
        second = self.consume(observation(1, 5))
        self.assertEqual(second["lifecycle_action"], "started")
        incident = self.state["incident"]
        self.assertEqual(incident["onset_at"], "2026-02-28T10:00:00Z")
        self.assertEqual(incident["detected_at"], "2026-02-28T10:05:00Z")
        self.assertEqual(len(second["persist_observations"]), 2)

    def test_single_asn_recovery_does_not_close_without_prefix_vp(self) -> None:
        self.consume(observation(0, 5))
        self.consume(observation(1, 5))
        for index in range(2, 9):
            result = self.consume(observation(index, 0))
        self.assertNotEqual(result["lifecycle_action"], "fully_recovered")
        self.assertEqual(self.state["incident"]["recovery_state"], "unknown")
        self.assertIsNone(self.state["incident"]["full_recovery_at"])

    def test_peak_members_count_ratio_and_total_share_snapshot(self) -> None:
        self.consume(observation(0, 4))
        self.consume(observation(1, 5))
        self.consume(observation(2, 8))
        projection = legacy_peak_projection(
            incident=self.state["incident"],
            peak_observation=self.state["peak_observation"],
            country_chinese_name="伊朗",
            outage_level="high",
            outage_level_descr="测试",
            outage_id=1,
        )
        self.assertEqual(projection["max_outage_as_num"], 8)
        self.assertEqual(projection["total_as_num"], 100)
        self.assertEqual(projection["outage_ases"], list(range(93, 101)))
        self.assertEqual(
            projection["peak_snapshot_id"],
            self.state["incident"]["peak_snapshot_id"],
        )

    def test_projection_rejects_wrong_peak_observation(self) -> None:
        self.consume(observation(0, 4))
        self.consume(observation(1, 5))
        wrong = observation(2, 7)
        with self.assertRaises(CountryOutageV2Error):
            legacy_peak_projection(
                incident=self.state["incident"],
                peak_observation=wrong,
                country_chinese_name="伊朗",
                outage_level="high",
                outage_level_descr="测试",
                outage_id=1,
            )

    def test_total_population_change_becomes_dynamic_not_denominator(self) -> None:
        row = build_live_observation(
            source="r",
            country_code="IR",
            observed_at_local=local_time(0),
            outage_asns=[99, 100, 101],
            normal_asns=range(1, 99),
            baseline_asns=range(1, 101),
        )
        self.assertEqual(row["cohort"]["baseline_asn_count"], 100)
        self.assertEqual(row["cohort"]["dynamic_asns"], [101])
        self.assertEqual(row["asn_state"]["affected_asn_count"], 2)

    def test_unknown_population_does_not_become_zero(self) -> None:
        row = build_live_observation(
            source="r",
            country_code="IR",
            observed_at_local=local_time(0),
            outage_asns=[],
            normal_asns=range(1, 100),
            baseline_asns=range(1, 101),
        )
        self.assertEqual(row["asn_state"]["unknown_asns"], [100])
        self.assertIsNone(row["asn_state"]["affected_asn_count"])
        self.assertIsNone(row["asn_state"]["affected_asn_ratio"])


if __name__ == "__main__":
    unittest.main()
