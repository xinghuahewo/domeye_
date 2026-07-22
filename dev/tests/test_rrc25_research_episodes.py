from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import unittest

from backend.data_pipeline.research.rrc25_country_outage.episodes import (
    EpisodeInputError,
    detect_country_outage_episodes,
)


EPISODE = {
    "version": "country_outage_episode_v1",
    "combine_rule": "any",
    "confirm_consecutive_slots": 2,
    "damaged_as_ratio_above": 0.03,
    "ipv4_visible_ratio_below": 0.99,
}
RECOVERY = {
    "partial_confirm_consecutive_slots": 6,
    "partial_visible_ratio_at_least": 0.99,
    "full_confirm_consecutive_slots": 6,
}
WAVE = {
    "baseline_ratio_floor": 0.005,
    "mad_multiplier": 3,
}
BASELINE = {
    "median": 1000,
    "mad": 1,
    "normal_band": {
        "mad_multiplier": 3,
        "absolute_floor_ratio": 0.001,
    },
}
START = datetime(2026, 2, 27, 16, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


def _time(value):
    return value.isoformat().replace("+00:00", "Z")


def _measure(value):
    if value is None:
        return {
            "value": None,
            "value_state": "unknown_source_gap",
            "missing_reason": "bounded-test-gap",
        }
    return {
        "value": value,
        "value_state": "observed_zero" if value == 0 else "observed",
        "missing_reason": None,
    }


def _sample(index, visible, damaged=0, continuity="continuous"):
    start = START + timedelta(minutes=5 * index)
    end = start + timedelta(minutes=5)
    sample_id = "sample_v1_{:024x}".format(index + 1)
    snapshot_id = "snapshot_v1_{:024x}".format(index + 1)
    visible_measure = _measure(visible)
    visible_measure.update({"sample_id": sample_id, "snapshot_id": snapshot_id})
    bound = {"sample_id": sample_id, "snapshot_id": snapshot_id}

    def count(value):
        return {
            **bound,
            "value": value,
            "value_state": "observed_zero" if value == 0 else "observed",
            "missing_reason": None,
        }

    ratio_denominator = 1_000_000
    ratio_numerator = int(damaged * ratio_denominator)
    damaged_measure = {
        **bound,
        "numerator": {**bound, "value": ratio_numerator},
        "denominator": {**bound, "value": ratio_denominator},
        "value": damaged,
        "value_state": "observed_zero" if damaged == 0 else "observed",
        "missing_reason": None,
    }
    return {
        "schema_version": "country-outage-sample/v1",
        "sample_id": sample_id,
        "run_id": "research_run_v1_" + "a" * 24,
        "snapshot_id": snapshot_id,
        "collector_id": "rrc25",
        "country_code": "IR",
        "cohort_view": "compatible",
        "slot": {
            "start": _time(start),
            "end": _time(end),
            "boundary": "[start,end)",
            "granularity_seconds": 300,
        },
        "continuity_state": continuity,
        "metrics": {
            "visible_asn_count": count(100),
            "damaged_asn_count": count(ratio_numerator),
            "baseline_asn_count": count(ratio_denominator),
            "visible_ipv4_prefix_count": count(100),
            "visible_ipv6_prefix_count": count(50),
            "visible_ipv4_address_union": visible_measure,
            "visible_ipv4_24_equivalent": count(100),
            "visible_ipv6_48_equivalent": count(50),
            "announce_count": count(10),
            "withdraw_count": count(0),
            "vp_expected_count": count(20),
            "vp_observed_count": count(20),
            "damaged_asn_ratio": damaged_measure,
        },
        "asn_sets": {
            "visible": {**bound, "value": [1], "value_state": "observed", "missing_reason": None},
            "damaged": {**bound, "value": [], "value_state": "observed_empty", "missing_reason": None},
            "baseline": {**bound, "value": [1], "value_state": "observed", "missing_reason": None},
        },
        "source_refs": [
            {
                "ref_type": "state_shard",
                "ref_id": "bounded-test/{:04d}.jsonl.gz".format(index),
                "sha256": "1" * 64,
            }
        ],
    }


def _detect(values, sample_overrides=None):
    sample_overrides = sample_overrides or {}
    samples = []
    for index, value in enumerate(values):
        override = sample_overrides.get(index, {})
        samples.append(_sample(index, value, **override))
    return detect_country_outage_episodes(
        samples,
        episode=EPISODE,
        recovery=RECOVERY,
        wave=WAVE,
        baseline=BASELINE,
    )


def _validate_with_contract(payload, schema_name):
    schema_path = ROOT / "contracts/research" / schema_name
    script = r"""
const fs = require('fs')
const path = require('path')
const root = process.argv[1]
const schemaPath = process.argv[2]
const Ajv2020 = require(path.join(root, 'frontend', 'node_modules', '@redocly', 'ajv', 'dist', '2020')).default
const schema = JSON.parse(fs.readFileSync(schemaPath, 'utf8'))
const payload = JSON.parse(fs.readFileSync(0, 'utf8'))
const ajv = new Ajv2020({allErrors: true, allowUnionTypes: true, strict: true})
ajv.addFormat('date-time', {
  type: 'string',
  validate: (value) => {
    const timestamp = Date.parse(value)
    return Number.isFinite(timestamp) && new Date(timestamp).toISOString().replace('.000Z', 'Z') === value
  },
})
const validate = ajv.compile(schema)
if (!validate(payload)) {
  process.stderr.write(ajv.errorsText(validate.errors, {separator: '; '}))
  process.exit(1)
}
"""
    result = subprocess.run(
        ["node", "-e", script, str(ROOT), str(schema_path)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


class CountryOutageEpisodeDetectionTest(unittest.TestCase):
    def test_short_crossing_above_99_percent_is_not_partial_recovery(self):
        result = _detect([900, 880, 991, 980])

        self.assertEqual(len(result.episodes), 1)
        episode = result.episodes[0]
        self.assertIsNone(episode.partial_recovery_at)
        self.assertEqual(episode.recovery_state, "recovering")
        rejected = [
            item
            for item in episode.recovery_candidates
            if item.kind == "partial" and not item.confirmed
        ]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0].supporting_sample_ids, ("sample_v1_" + "0" * 23 + "3",))
        self.assertEqual(rejected[0].reason_code, "threshold_not_sustained")

    def test_two_declines_without_full_recovery_stay_in_one_episode(self):
        result = _detect([900, 880, 920, 930, 920, 910])

        self.assertEqual(len(result.episodes), 1)
        self.assertEqual(len(result.waves), 2)
        self.assertEqual(result.episodes[0].wave_ids, tuple(w.wave_id for w in result.waves))
        split = result.episodes[0].split_evidence[0]
        self.assertEqual(split.decision, "same_episode_new_wave")
        self.assertFalse(split.full_recovery_confirmed)
        self.assertEqual(split.reason_code, "partial_rebound_only")

    def test_six_normal_slots_close_episode_and_later_anomaly_splits(self):
        result = _detect([900, 880] + [1000] * 6 + [900, 880])

        self.assertEqual(len(result.episodes), 2)
        first, second = result.episodes
        self.assertEqual(first.recovery_state, "fully_recovered")
        self.assertEqual(first.duration.duration_state, "exact")
        self.assertEqual(first.duration.seconds, 2400)
        self.assertEqual(first.full_recovery_at, _time(START + timedelta(minutes=40)))
        self.assertEqual(second.recovery_state, "ongoing")
        self.assertEqual(len(second.split_evidence), 1)
        self.assertEqual(second.split_evidence[0].decision, "new_episode")
        self.assertTrue(second.split_evidence[0].full_recovery_confirmed)
        self.assertEqual(
            second.split_evidence[0].reason_code, "full_recovery_six_slots"
        )

    def test_damaged_asn_trigger_blocks_full_recovery_and_episode_split(self):
        result = _detect(
            [1000] * 10,
            sample_overrides={index: {"damaged": 0.04} for index in range(10)},
        )

        self.assertEqual(len(result.episodes), 1)
        self.assertEqual(len(result.waves), 1)
        self.assertEqual(result.episodes[0].recovery_state, "ongoing")
        self.assertIsNone(result.episodes[0].full_recovery_at)
        self.assertEqual(result.episodes[0].duration.duration_state, "lower_bound")

    def test_historical_partial_recovery_does_not_mask_later_relapse(self):
        result = _detect([900, 880] + [991] * 6 + [900, 880])

        episode = result.episodes[0]
        self.assertEqual(
            episode.partial_recovery_at, _time(START + timedelta(minutes=40))
        )
        self.assertEqual(episode.recovery_state, "ongoing")
        self.assertEqual(len(result.waves), 2)

    def test_second_decline_requires_two_slots_and_writes_amplitudes(self):
        one_decline = _detect([900, 880, 930, 920])
        two_declines = _detect([900, 880, 930, 920, 910])

        self.assertEqual(len(one_decline.waves), 1)
        self.assertEqual(len(two_declines.waves), 2)
        evidence = two_declines.waves[1].split_evidence
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence.rebound_amplitude, 50)
        self.assertEqual(evidence.new_decline_amplitude, 20)
        self.assertEqual(evidence.significance_threshold, 5)

    def test_new_wave_is_observational_and_never_labeled_causal(self):
        result = _detect([900, 880, 930, 920, 910])

        second = result.waves[1]
        self.assertEqual(second.relation_to_previous_wave, "same_episode_after_partial_rebound")
        self.assertEqual(second.causal_relation, "not_assessed")
        record = second.to_contract_record()
        self.assertNotIn("precursor", str(record).lower())
        self.assertFalse(record["split_evidence"]["full_recovery_between_waves"])

    def test_window_end_without_full_recovery_is_lower_bound(self):
        result = _detect([900, 880])

        episode = result.episodes[0]
        self.assertEqual(episode.recovery_state, "ongoing")
        self.assertIsNone(episode.full_recovery_at)
        self.assertEqual(episode.duration.duration_state, "lower_bound")
        self.assertIsNone(episode.duration.seconds)
        self.assertEqual(episode.duration.minimum_seconds, 600)
        self.assertEqual(episode.duration.measured_to, _time(START + timedelta(minutes=10)))

    def test_unknown_gap_cannot_complete_two_slot_onset(self):
        result = _detect(
            [900, 0, 880],
            sample_overrides={1: {"continuity": "unknown_after_gap"}},
        )

        self.assertEqual(result.episodes, ())
        self.assertEqual(result.ignored_unknown_sample_ids, ("sample_v1_" + "0" * 23 + "2",))

    def test_gap_during_episode_yields_unknown_duration_and_no_cross_gap_wave(self):
        result = _detect(
            [900, 880, 930, 920, 910],
            sample_overrides={3: {"continuity": "unknown_after_gap"}},
        )

        self.assertEqual(len(result.episodes), 1)
        episode = result.episodes[0]
        self.assertEqual(episode.recovery_state, "unknown")
        self.assertEqual(episode.duration.duration_state, "unknown")
        self.assertEqual(len(result.waves), 1)
        self.assertEqual(result.episode_count_state, "unknown")

    def test_gap_followed_by_full_normal_window_bounds_duration_as_interval(self):
        result = _detect(
            [900, 880, 0] + [1000] * 6,
            sample_overrides={2: {"continuity": "unknown_after_gap"}},
        )

        self.assertEqual(len(result.episodes), 1)
        episode = result.episodes[0]
        self.assertEqual(episode.recovery_state, "unknown")
        self.assertEqual(episode.duration.duration_state, "interval")
        self.assertEqual(episode.duration.minimum_seconds, 2400)
        self.assertEqual(episode.duration.maximum_seconds, 2700)
        self.assertEqual(
            episode.duration.measured_to,
            _time(START + timedelta(minutes=45)),
        )
        bounded = [
            item
            for item in episode.recovery_candidates
            if item.reason_code == "continuity_gap_bounds_recovery"
        ]
        self.assertEqual(len(bounded), 1)
        self.assertFalse(bounded[0].confirmed)
        record = episode.to_contract_record(
            [
                {
                    "incident_ref": "country_outage/legacy",
                    "relation": "possible_correspondence",
                    "causal": False,
                    "evidence_sample_ids": list(episode.supporting_sample_ids),
                }
            ]
        )
        _validate_with_contract(record, "country-outage-episode.schema.json")

    def test_gap_without_six_reliable_normal_slots_keeps_duration_unknown(self):
        result = _detect(
            [900, 880, 0, 1000, 1000, 1000],
            sample_overrides={2: {"continuity": "unknown_after_gap"}},
        )

        episode = result.episodes[0]
        self.assertEqual(episode.duration.duration_state, "unknown")
        self.assertIsNone(episode.duration.minimum_seconds)
        self.assertIsNone(episode.duration.maximum_seconds)

    def test_gap_between_two_anomaly_segments_records_unknown_split(self):
        result = _detect(
            [900, 880, 0, 900, 880],
            sample_overrides={2: {"continuity": "unknown_after_gap"}},
        )

        self.assertEqual(len(result.episodes), 2)
        self.assertEqual(result.episode_count_state, "unknown")
        split = result.episodes[1].split_evidence[0]
        self.assertEqual(split.decision, "same_episode_new_wave")
        self.assertFalse(split.full_recovery_confirmed)
        self.assertEqual(split.reason_code, "continuity_unknown")

    def test_unknown_metric_resets_candidates_and_blocks_cross_unknown_wave(self):
        result = _detect([900, 880, 930, None, 940, 920, 910])

        self.assertEqual(len(result.episodes), 1)
        self.assertEqual(len(result.waves), 1)
        self.assertIn("sample_v1_" + "0" * 23 + "4", result.ignored_unknown_sample_ids)
        self.assertIsNone(result.episodes[0].partial_recovery_at)

    def test_same_input_is_fully_deterministic(self):
        values = [900, 880, 930, 920, 910, 950]

        first = _detect(values)
        second = _detect(values)

        self.assertEqual(first, second)
        self.assertEqual(first.episodes[0].episode_id, second.episodes[0].episode_id)
        self.assertEqual(first.waves[1].wave_id, second.waves[1].wave_id)
        self.assertEqual(first.episode_count_state, "exact")

    def test_thresholds_are_derived_from_explicit_baseline_and_profile(self):
        result = _detect([])
        self.assertEqual(result.normal_band_lower, 997)
        self.assertEqual(result.normal_band_upper, 1003)
        self.assertEqual(result.wave_significance_threshold, 5)

        incomplete = dict(EPISODE)
        del incomplete["confirm_consecutive_slots"]
        with self.assertRaisesRegex(EpisodeInputError, "缺少显式算法"):
            detect_country_outage_episodes(
                [],
                episode=incomplete,
                recovery=RECOVERY,
                wave=WAVE,
                baseline=BASELINE,
            )

        non_v1_window = dict(RECOVERY)
        non_v1_window["full_confirm_consecutive_slots"] = 3
        with self.assertRaisesRegex(EpisodeInputError, "固定为六槽"):
            detect_country_outage_episodes(
                [],
                episode=EPISODE,
                recovery=non_v1_window,
                wave=WAVE,
                baseline=BASELINE,
            )

    def test_episode_contract_adapter_requires_explicit_non_causal_mapping(self):
        episode = _detect([900, 880]).episodes[0]
        with self.assertRaisesRegex(EpisodeInputError, "Incident"):
            episode.to_contract_record([])

        record = episode.to_contract_record(
            [
                {
                    "incident_ref": "country_outage/legacy",
                    "relation": "legacy_reconciliation",
                    "causal": False,
                    "evidence_sample_ids": list(episode.supporting_sample_ids),
                }
            ]
        )
        self.assertEqual(record["schema_version"], "country-outage-episode/v1")
        self.assertFalse(record["incident_mappings"][0]["causal"])
        _validate_with_contract(record, "country-outage-episode.schema.json")

        result = _detect([900, 880, 930, 920, 910])
        for wave in result.waves:
            _validate_with_contract(
                wave.to_contract_record(), "country-outage-wave.schema.json"
            )


if __name__ == "__main__":
    unittest.main()
