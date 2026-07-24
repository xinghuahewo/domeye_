from __future__ import annotations

from dataclasses import replace
import unittest

from backend.data_pipeline.route_event import (
    AsPathSegment,
    ParsedRouteElement,
    artifact_id_v1,
)
from backend.data_pipeline.research.rrc25_country_outage.bounded_state_v2 import (
    AsnMilestoneTracker,
    freeze_ir_cohort,
    normal_band_from_catch_up,
    project_country_snapshot,
)
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    CONTINUOUS,
    UNKNOWN_AFTER_GAP,
    RouteStateEntry,
    build_research_route_event,
)


SHA = "1" * 64
ARTIFACT_ID = artifact_id_v1(SHA)


def entry(
    *,
    ordinal: int,
    peer_ip: str,
    peer_asn: int,
    prefix: str,
    origin: int,
    afi_safi: str,
) -> RouteStateEntry:
    event = build_research_route_event(
        artifact_id=ARTIFACT_ID,
        file_sha256=SHA,
        collector_id="rrc25",
        artifact_slot_utc="2026-02-28T08:00:00Z",
        record_ordinal=ordinal,
        element_ordinal=0,
        element=ParsedRouteElement(
            event_time_utc="2026-02-28T08:00:00Z",
            peer_ip=peer_ip,
            peer_asn=peer_asn,
            action="rib_snapshot",
            prefix=prefix,
            afi_safi=afi_safi,
            as_path=(AsPathSegment("as_sequence", (64500, origin)),),
            quality_flags=(),
        ),
    )
    return RouteStateEntry(
        key=event.key,
        peer_ip=event.peer_ip,
        peer_asn=event.peer_asn,
        as_path=event.as_path or (),
        quality_flags=event.quality_flags,
        last_action=event.action,
        last_event_time_utc=event.event_time_utc,
        last_raw_ref=event.raw_ref,
    )


def membership(asn: int) -> bool | None:
    if asn in {100, 200, 300}:
        return True
    if asn == 999:
        return None
    return False


COUNTS = {
    "announce": 0,
    "withdraw": 0,
    "retained_announce": 0,
    "retained_withdraw": 0,
}


class BoundedStateV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.v4a = entry(
            ordinal=1,
            peer_ip="192.0.2.1",
            peer_asn=64501,
            prefix="203.0.113.0/24",
            origin=100,
            afi_safi="ipv4_unicast",
        )
        self.v4b = entry(
            ordinal=2,
            peer_ip="192.0.2.2",
            peer_asn=64502,
            prefix="203.0.113.0/24",
            origin=100,
            afi_safi="ipv4_unicast",
        )
        self.v6 = entry(
            ordinal=3,
            peer_ip="2001:db8::1",
            peer_asn=64503,
            prefix="2001:db8:100::/48",
            origin=100,
            afi_safi="ipv6_unicast",
        )
        self.other = entry(
            ordinal=4,
            peer_ip="192.0.2.3",
            peer_asn=64504,
            prefix="198.51.100.0/24",
            origin=400,
            afi_safi="ipv4_unicast",
        )
        self.cohort = freeze_ir_cohort(
            [self.v4a, self.v4b, self.v6, self.other],
            target_membership=membership,
            mapping_version="mapping-fixture",
            seed_observed_at="2026-02-28T08:00:00Z",
        )

    def project(self, entries, *, continuity=CONTINUOUS, tracker=None):
        return project_country_snapshot(
            entries,
            cohort=self.cohort,
            target_membership=membership,
            observed_at="2026-02-28T10:05:00Z",
            slot_start_utc="2026-02-28T10:05:00Z",
            slot_end_exclusive_utc="2026-02-28T10:05:00Z",
            slot_role="window_start",
            continuity_state=continuity,
            update_counts=COUNTS,
            milestone_tracker=tracker,
        )

    def test_prefix_vp_and_dual_stack_classification(self) -> None:
        observation, asn_rows, route_rows = self.project(
            [self.v4a, self.v6, self.other]
        )
        self.assertEqual(observation["prefix_vp"]["baseline_count"], 3)
        self.assertEqual(observation["prefix_vp"]["visible_count"], 2)
        self.assertEqual(
            observation["address_families"]["ipv4"]["classifications"][
                "partially_visible"
            ],
            [100],
        )
        self.assertEqual(
            observation["dual_stack"]["classifications"]["partially_visible"],
            [100],
        )
        self.assertEqual(len(asn_rows), 2)
        self.assertEqual(len(route_rows), 3)

    def test_ipv4_invisible_ipv6_visible_label(self) -> None:
        observation, _asn, _routes = self.project([self.v6])
        self.assertEqual(
            observation["dual_stack"]["classifications"][
                "ipv4_invisible_ipv6_visible"
            ],
            [100],
        )

    def test_dynamic_ir_origin_is_separate_from_denominator(self) -> None:
        dynamic = entry(
            ordinal=5,
            peer_ip="192.0.2.9",
            peer_asn=64509,
            prefix="192.0.2.0/24",
            origin=300,
            afi_safi="ipv4_unicast",
        )
        observation, _asn, routes = self.project(
            [self.v4a, self.v4b, self.v6, dynamic]
        )
        self.assertEqual(observation["cohort"]["baseline_asn_count"], 1)
        self.assertEqual(
            observation["dynamic"]["ipv4_visible_origin_asns"], [300]
        )
        self.assertTrue(any(row["population_role"] == "dynamic" for row in routes))

    def test_gap_does_not_become_zero(self) -> None:
        observation, asn_rows, _routes = self.project(
            [], continuity=UNKNOWN_AFTER_GAP
        )
        self.assertIsNone(observation["metrics"]["affected_asn_count"])
        self.assertIsNone(observation["prefix_vp"]["visible_count"])
        self.assertTrue(all(row["classification"] == "unknown" for row in asn_rows))

    def test_tracker_records_damage_invisibility_and_recovery(self) -> None:
        tracker = AsnMilestoneTracker()
        _first, rows, _route = self.project([self.v4a, self.v6], tracker=tracker)
        v4 = next(row for row in rows if row["afi"] == "ipv4")
        self.assertEqual(v4["first_damaged_at"], "2026-02-28T10:05:00Z")
        self.assertIsNone(v4["first_fully_invisible_at"])
        _second, rows2, _route2 = self.project(
            [self.v4a, self.v4b, self.v6], tracker=tracker
        )
        v4_second = next(row for row in rows2 if row["afi"] == "ipv4")
        self.assertEqual(
            v4_second["first_recovered_at"], "2026-02-28T10:05:00Z"
        )

    def test_normal_band_is_median_plus_three_mad_floor(self) -> None:
        band = normal_band_from_catch_up(
            [
                {
                    "visible_origin_asn_ratio": 1.0,
                    "visible_prefix_vp_ratio": 0.998,
                },
                {
                    "visible_origin_asn_ratio": 0.999,
                    "visible_prefix_vp_ratio": 1.0,
                },
                {
                    "visible_origin_asn_ratio": 1.0,
                    "visible_prefix_vp_ratio": 0.999,
                },
            ]
        )
        assert band is not None
        self.assertEqual(
            band["visible_origin_asn_ratio"]["sample_count"], 3
        )
        self.assertLessEqual(
            band["visible_prefix_vp_ratio"]["lower"], 0.998
        )

    def test_as_set_and_mapping_unknown_are_explicitly_excluded(self) -> None:
        as_set = replace(
            self.v4a,
            key=replace(self.v4a.key, prefix="203.0.114.0/24"),
            as_path=(AsPathSegment("as_set", (100, 200)),),
        )
        mapping_unknown = entry(
            ordinal=6,
            peer_ip="192.0.2.6",
            peer_asn=64506,
            prefix="203.0.115.0/24",
            origin=999,
            afi_safi="ipv4_unicast",
        )
        cohort = freeze_ir_cohort(
            [self.v4a, as_set, mapping_unknown],
            target_membership=membership,
            mapping_version="mapping-fixture",
            seed_observed_at="2026-02-28T08:00:00Z",
        )
        self.assertEqual(cohort.baseline_asns, (100,))
        self.assertEqual(cohort.quality["conflict_origin_route_count"], 1)
        self.assertEqual(
            cohort.quality["mapping_unknown_or_conflict_route_count"], 1
        )
        self.assertEqual(
            cohort.quality["mapping_unknown_or_conflict_asns"], [999]
        )


if __name__ == "__main__":
    unittest.main()
