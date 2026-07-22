from datetime import datetime, timedelta, timezone
import unittest

from backend.data_pipeline.research.rrc25_country_outage.baseline import (
    BaselineInputError,
    BaselineObservation,
    derive_numeric_baseline,
)


START = datetime(2026, 2, 27, 16, 0, tzinfo=timezone.utc)
NUMERIC = {
    "statistic": "median",
    "dispersion": "median_absolute_deviation",
    "extension_direction": "forward",
    "initial_duration_seconds": 21_600,
    "extension_step_seconds": 21_600,
    "max_duration_seconds": 86_400,
    "max_relative_mad": 0.001,
    "stop_before_confirmed_onset": True,
    "unstable_exhausted_state": "incomplete",
}
NORMAL_BAND = {
    "method": "median_plus_minus_max_scaled_mad_and_absolute_floor",
    "mad_multiplier": 3,
    "absolute_floor_ratio": 0.001,
}


def _utc(value):
    return value.isoformat().replace("+00:00", "Z")


def _observations(values, *, gap_at=None):
    result = []
    for index, value in enumerate(values):
        start = START + timedelta(minutes=5 * index)
        gap = index == gap_at
        result.append(
            BaselineObservation(
                sample_id=f"sample-{index:04d}",
                snapshot_id=f"snapshot-{index:04d}",
                slot_start_utc=_utc(start),
                slot_end_exclusive_utc=_utc(start + timedelta(minutes=5)),
                continuity_state="unknown_after_gap" if gap else "continuous",
                value=None if gap else value,
                value_state="unknown_source_gap"
                if gap
                else ("observed_zero" if value == 0 else "observed"),
                missing_reason="artifact_slot_missing" if gap else None,
            )
        )
    return tuple(result)


def _derive(values, **kwargs):
    return derive_numeric_baseline(
        _observations(values, gap_at=kwargs.pop("gap_at", None)),
        candidate_start_utc="2026-02-27T16:00:00Z",
        numeric_policy=kwargs.pop("numeric_policy", NUMERIC),
        normal_band_policy=kwargs.pop("normal_band_policy", NORMAL_BAND),
        **kwargs,
    )


class Rrc25NumericBaselineTests(unittest.TestCase):
    def test_stable_initial_six_hours_use_median_mad_and_absolute_floor(self):
        result = _derive([10_000_000] * 72)

        self.assertTrue(result.resolved)
        self.assertEqual(result.duration_seconds, 21_600)
        self.assertEqual(result.observation_count, 72)
        self.assertEqual(result.extension_count, 0)
        self.assertEqual(result.median, 10_000_000)
        self.assertEqual(result.mad, 0)
        self.assertEqual(result.normal_band_lower, 9_990_000)
        self.assertEqual(result.normal_band_upper, 10_010_000)
        self.assertEqual(result.actual_end_exclusive_utc, "2026-02-27T22:00:00Z")

    def test_unstable_candidate_extends_by_six_hours_until_stable(self):
        first = [900.0, 1100.0] * 36
        result = _derive(first + [1000.0] * 144)

        self.assertTrue(result.resolved)
        self.assertEqual(result.duration_seconds, 64_800)
        self.assertEqual(result.extension_count, 2)
        self.assertEqual(result.observation_count, 216)
        self.assertEqual(result.median, 1000)
        self.assertEqual(result.mad, 0)

    def test_confirmed_onset_prevents_extension_from_polluting_baseline(self):
        result = _derive(
            [900.0, 1100.0] * 72,
            confirmed_onset_at="2026-02-27T23:00:00Z",
        )

        self.assertFalse(result.resolved)
        self.assertEqual(
            result.unresolved_reason, "confirmed_onset_before_stable_extension"
        )
        self.assertEqual(result.duration_seconds, 25_200)
        self.assertEqual(result.observation_count, 84)

    def test_gap_and_insufficient_window_remain_unknown_not_zero(self):
        gap = _derive([1000.0] * 72, gap_at=10)
        short = _derive([1000.0] * 71)

        self.assertEqual(gap.resolution_state, "baseline_unresolved")
        self.assertEqual(gap.unresolved_reason, "artifact_slot_missing")
        self.assertIsNone(gap.median)
        self.assertEqual(short.unresolved_reason, "insufficient_contiguous_observations")
        self.assertIsNone(short.normal_band_lower)

    def test_max_duration_still_unstable_stops_episode_prerequisite(self):
        result = _derive([900.0, 1100.0] * 144)

        self.assertFalse(result.resolved)
        self.assertEqual(result.unresolved_reason, "max_duration_still_unstable")
        self.assertEqual(result.duration_seconds, 86_400)
        self.assertEqual(result.extension_count, 3)

    def test_unordered_or_noncanonical_slots_fail_closed(self):
        values = list(_observations([1000.0] * 72))
        values[1] = BaselineObservation(
            **{
                **values[1].__dict__,
                "slot_start_utc": values[2].slot_start_utc,
            }
        )
        with self.assertRaisesRegex(BaselineInputError, "连续覆盖"):
            derive_numeric_baseline(
                values,
                candidate_start_utc="2026-02-27T16:00:00Z",
                numeric_policy=NUMERIC,
                normal_band_policy=NORMAL_BAND,
            )


if __name__ == "__main__":
    unittest.main()
