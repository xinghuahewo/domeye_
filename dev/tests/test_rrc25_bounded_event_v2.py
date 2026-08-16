from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from backend.data_pipeline.research.rrc25_country_outage.bounded_event_v2 import (
    BoundedEventModelError,
    OBSERVATION_SCHEMA_VERSION,
    derive_incident_episode_v2,
    stable_id,
    validate_observation,
)


UTC = timezone.utc
START = datetime(2026, 2, 28, 10, 5, tzinfo=UTC)
BASELINE_ASNS = list(range(1, 101))
COHORT_ID = stable_id(
    "cohort_v2_",
    {"collector_id": "rrc25", "country_code": "IR", "seed": "fixture"},
)


def _time(index: int) -> str:
    return (START + timedelta(minutes=5 * index)).strftime("%Y-%m-%dT%H:%M:%SZ")


def observation(
    index: int,
    *,
    affected: int,
    prefix_visible: int,
    baseline_prefix: int = 1_000,
    continuity: str = "continuous",
    ipv4_invisible: tuple[int, ...] = (),
    ipv6_visible: tuple[int, ...] = (),
) -> dict:
    observed_at = _time(index)
    snapshot_id = stable_id(
        "snapshot_v2_",
        {"cohort_id": COHORT_ID, "observed_at": observed_at},
    )
    affected_members = BASELINE_ASNS[-affected:] if affected else []
    visible_members = sorted(set(BASELINE_ASNS) - set(affected_members))
    ipv4_invisible_set = set(ipv4_invisible)
    ipv4_full = sorted(set(BASELINE_ASNS) - ipv4_invisible_set)
    ipv6_baseline = sorted(set(ipv6_visible))
    if index == 0:
        slot_start = observed_at
        slot_end = observed_at
        role = "window_start"
    else:
        slot_start = _time(index - 1)
        slot_end = observed_at
        role = "slot_end"
    if continuity == "continuous":
        affected_count = affected
        affected_ratio = affected / len(BASELINE_ASNS)
        visible_count = len(visible_members)
        visible_ratio = visible_count / len(BASELINE_ASNS)
        pv_count = prefix_visible
        pv_lost = baseline_prefix - prefix_visible
        pv_ratio = prefix_visible / baseline_prefix
    else:
        affected_count = affected_ratio = visible_count = visible_ratio = None
        pv_count = pv_lost = pv_ratio = None
        affected_members = []
        visible_members = []
    dual_partial = affected_members
    dual_fully_visible = visible_members
    ipv4_partial = sorted(ipv4_invisible_set - set(affected_members))
    ipv4_invisible_members = sorted(ipv4_invisible_set & set(affected_members))
    ipv4_classified_full = sorted(
        set(ipv4_full) - set(affected_members)
    )
    ipv4_other_partial = sorted(
        set(affected_members) - set(ipv4_invisible_members)
    )
    payload = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "observed_at": observed_at,
        "slot": {
            "start_utc": slot_start,
            "end_exclusive_utc": slot_end,
            "boundary": "[start,end)",
            "role": role,
        },
        "continuity_state": continuity,
        "cohort": {
            "cohort_id": COHORT_ID,
            "baseline_asn_count": len(BASELINE_ASNS),
            "baseline_prefix_vp_count": baseline_prefix,
            "mapping_version": "asmap-fixture",
            "completeness_state": (
                "known_population_with_explicit_unknown_exclusions"
            ),
        },
        "address_families": {
            "ipv4": {
                "baseline_origin_asns": BASELINE_ASNS,
                "visible_origin_asns": ipv4_full,
                "visible_prefixes_ref": {
                    "path": "route-states.jsonl.gz",
                    "snapshot_id": snapshot_id,
                    "afi": "ipv4",
                },
                "classifications": {
                    "fully_visible": ipv4_classified_full,
                    "partially_visible": sorted(
                        set(ipv4_partial) | set(ipv4_other_partial)
                    ),
                    "fully_invisible": ipv4_invisible_members,
                    "unknown": [],
                },
            },
            "ipv6": {
                "baseline_origin_asns": ipv6_baseline,
                "visible_origin_asns": ipv6_baseline,
                "visible_prefixes_ref": {
                    "path": "route-states.jsonl.gz",
                    "snapshot_id": snapshot_id,
                    "afi": "ipv6",
                },
                "classifications": {
                    "fully_visible": ipv6_baseline,
                    "partially_visible": [],
                    "fully_invisible": [],
                    "unknown": [],
                },
            },
        },
        "dual_stack": {
            "baseline_origin_asns": BASELINE_ASNS,
            "visible_origin_asns": visible_members,
            "affected_asns": affected_members,
            "classifications": {
                "fully_visible": dual_fully_visible,
                "partially_visible": dual_partial,
                "fully_invisible": [],
                "ipv4_invisible_ipv6_visible": sorted(
                    ipv4_invisible_set & set(ipv6_visible)
                ),
                "unknown": [],
            },
        },
        "dynamic": {
            "denominator_policy": "reported_separately",
            "ipv4_visible_origin_asns": [],
            "ipv6_visible_origin_asns": [],
            "dual_stack_visible_origin_asns": [],
            "visible_prefixes_ref": {
                "path": "route-states.jsonl.gz",
                "snapshot_id": snapshot_id,
            },
        },
        "prefix_vp": {
            "baseline_count": baseline_prefix,
            "visible_count": pv_count,
            "lost_count": pv_lost,
            "visible_ratio": pv_ratio,
        },
        "metrics": {
            "affected_asn_count": affected_count,
            "affected_asn_ratio": affected_ratio,
            "visible_origin_asn_count": visible_count,
            "visible_origin_asn_ratio": visible_ratio,
        },
        "update_counts": {
            "announce": 0,
            "withdraw": 0,
            "retained_announce": 0,
            "retained_withdraw": 0,
        },
        "state_result_ref": {
            "path": "route-states.jsonl.gz",
            "format": "route_state_snapshots_jsonl_gzip",
            "snapshot_id": snapshot_id,
        },
    }
    if continuity != "continuous":
        for family in payload["address_families"].values():
            family["visible_origin_asns"] = []
            family["classifications"] = {
                "fully_visible": [],
                "partially_visible": [],
                "fully_invisible": [],
                "unknown": family["baseline_origin_asns"],
            }
        payload["dual_stack"]["classifications"] = {
            "fully_visible": [],
            "partially_visible": [],
            "fully_invisible": [],
            "ipv4_invisible_ipv6_visible": [],
            "unknown": BASELINE_ASNS,
        }
    return payload


def derive(rows: list[dict], normal_band: dict | None = None) -> dict:
    return derive_incident_episode_v2(
        rows,
        legacy_ref="country_outage/2026-02-27 09:12:32/IR/1/r",
        detected_at="2026-02-27T01:12:32Z",
        source="legacy_country_outage",
        country_code="IR",
        collector_id="rrc25",
        source_context={"bview": "bview.20260228.0800.gz"},
        normal_band=normal_band,
    )


class BoundedEventV2Test(unittest.TestCase):
    def test_two_slots_confirm_onset_and_peak_trough_do_not_overwrite(self) -> None:
        rows = [
            observation(0, affected=0, prefix_visible=1_000),
            observation(1, affected=4, prefix_visible=950),
            observation(2, affected=5, prefix_visible=930),
            observation(3, affected=8, prefix_visible=940),
            observation(4, affected=7, prefix_visible=900),
        ]
        result = derive(rows)
        episode = result["episodes"][0]
        self.assertEqual(episode["onset_at"], _time(1))
        self.assertEqual(episode["peak_at"], _time(3))
        self.assertEqual(episode["trough_at"], _time(4))
        self.assertEqual(episode["duration_state"], "lower_bound")

    def test_single_slot_recovery_does_not_close_episode(self) -> None:
        rows = [
            observation(0, affected=0, prefix_visible=1_000),
            observation(1, affected=5, prefix_visible=940),
            observation(2, affected=5, prefix_visible=940),
            observation(3, affected=0, prefix_visible=1_000),
            observation(4, affected=6, prefix_visible=930),
            observation(5, affected=6, prefix_visible=930),
        ]
        result = derive(
            rows,
            {
                "visible_origin_asn_ratio": {"lower": 0.999, "upper": 1.0},
                "visible_prefix_vp_ratio": {"lower": 0.999, "upper": 1.0},
            },
        )
        self.assertEqual(len(result["episodes"]), 1)
        self.assertIsNone(result["episodes"][0]["full_recovery_at"])

    def test_six_slots_partial_and_full_recovery_then_second_episode(self) -> None:
        rows = [
            observation(0, affected=0, prefix_visible=1_000),
            observation(1, affected=5, prefix_visible=940),
            observation(2, affected=5, prefix_visible=940),
        ]
        rows.extend(
            observation(index, affected=0, prefix_visible=1_000)
            for index in range(3, 9)
        )
        rows.extend(
            [
                observation(9, affected=6, prefix_visible=930),
                observation(10, affected=6, prefix_visible=930),
            ]
        )
        result = derive(
            rows,
            {
                "visible_origin_asn_ratio": {"lower": 0.999, "upper": 1.0},
                "visible_prefix_vp_ratio": {"lower": 0.999, "upper": 1.0},
            },
        )
        self.assertEqual(len(result["episodes"]), 2)
        self.assertEqual(result["episodes"][0]["partial_recovery_at"], _time(3))
        self.assertEqual(result["episodes"][0]["full_recovery_at"], _time(3))
        self.assertEqual(result["episodes"][0]["duration_state"], "exact")
        self.assertEqual(result["episodes"][1]["duration_state"], "lower_bound")
        self.assertEqual(result["incident"]["recovery_state"], "ongoing")

    def test_left_censored_onset_is_interval(self) -> None:
        result = derive(
            [
                observation(0, affected=5, prefix_visible=940),
                observation(1, affected=5, prefix_visible=940),
            ]
        )
        self.assertEqual(result["episodes"][0]["duration_state"], "interval")
        self.assertEqual(
            result["episodes"][0]["milestones"]["onset"]["time_precision"],
            "left_censored_at_window_start",
        )
        self.assertEqual(result["incident"]["duration_state"], "interval")

    def test_second_decline_before_full_recovery_is_new_wave(self) -> None:
        rows = [
            observation(0, affected=0, prefix_visible=1_000),
            observation(1, affected=5, prefix_visible=950),
            observation(2, affected=8, prefix_visible=920),
            observation(3, affected=4, prefix_visible=960),
            observation(4, affected=7, prefix_visible=930),
            observation(5, affected=8, prefix_visible=920),
        ]
        result = derive(rows)
        self.assertEqual(len(result["episodes"]), 1)
        self.assertGreaterEqual(len(result["waves"]), 2)
        self.assertTrue(
            all(
                wave["episode_id"] == result["episodes"][0]["episode_id"]
                for wave in result["waves"]
            )
        )

    def test_gap_values_must_be_unknown(self) -> None:
        row = observation(
            0,
            affected=0,
            prefix_visible=1_000,
            continuity="unknown_after_gap",
        )
        validate_observation(row)
        invalid = deepcopy(row)
        invalid["metrics"]["affected_asn_count"] = 0
        with self.assertRaises(BoundedEventModelError):
            validate_observation(invalid)

    def test_ipv4_invisible_ipv6_visible_label_is_validated(self) -> None:
        row = observation(
            0,
            affected=1,
            prefix_visible=990,
            ipv4_invisible=(100,),
            ipv6_visible=(100,),
        )
        validate_observation(row)
        invalid = deepcopy(row)
        invalid["dual_stack"]["classifications"][
            "ipv4_invisible_ipv6_visible"
        ] = [99]
        with self.assertRaises(BoundedEventModelError):
            validate_observation(invalid)


if __name__ == "__main__":
    unittest.main()
