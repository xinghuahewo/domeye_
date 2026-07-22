from datetime import datetime, timedelta, timezone
import unittest
from unittest import mock

from backend.data_pipeline.route_event import AsPathSegment
from backend.data_pipeline.research.rrc25_country_outage import (
    country_impact as country_impact_module,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    CONFLICT,
    CONFLICT_MAPPING,
    MAPPED,
    RESOLVED,
    UNKNOWN,
    UNKNOWN_MAPPING,
    CountryImpactError,
    MappingAssignment,
    build_country_cohort,
    build_country_mapping_view,
    compute_country_snapshot_impact,
    derive_country_cohort_and_impacts,
    derive_origin_asns,
    mapping_view_from_frozen_snapshot,
    project_snapshot_origins,
    project_snapshot_origins_series,
    snapshot_id_v1,
    snapshot_ids_v1,
)
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    CONTINUOUS,
    UNKNOWN_AFTER_GAP,
    RawRecordRef,
    ReplaySnapshot,
    RouteReplayState,
    RouteStateEntry,
    RouteStateKey,
)


HASH_A = "a" * 64


def path(asn, *upstream):
    return (AsPathSegment("as_sequence", tuple(upstream) + (asn,)),)


def entry(
    ordinal,
    *,
    vp="vp-a",
    prefix="10.0.0.0/24",
    as_path=None,
    afi_safi=None,
):
    if afi_safi is None:
        afi_safi = "ipv6_unicast" if ":" in prefix else "ipv4_unicast"
    if as_path is None:
        as_path = path(65001, 64500)
    raw_ref = RawRecordRef(
        artifact_id=f"art_v1_{ordinal:032x}",
        file_sha256=f"{ordinal:064x}",
        collector_id="rrc25",
        artifact_slot_utc="2026-02-27T16:00:00Z",
        record_ordinal=ordinal,
        element_ordinal=0,
        route_event_id=f"rte_v1_{ordinal:032x}",
    )
    return RouteStateEntry(
        key=RouteStateKey("rrc25", vp, afi_safi, prefix),
        peer_ip="192.0.2.1",
        peer_asn=64500,
        as_path=as_path,
        quality_flags=(),
        last_action="announce",
        last_event_time_utc="2026-02-27T16:00:00Z",
        last_raw_ref=raw_ref,
    )


def seed(*entries, continuity=CONTINUOUS, reasons=()):
    ordered = tuple(sorted(entries, key=lambda value: value.key))
    return RouteReplayState(
        entries=ordered,
        latest_changes=(),
        continuity_state=continuity,
        missing_reasons=tuple(reasons),
        processed_route_event_ids=frozenset(
            value.last_raw_ref.route_event_id for value in ordered
        ),
        last_order_key=None,
    )


def snapshot(
    slot,
    *entries,
    continuity=CONTINUOUS,
    reasons=(),
):
    start_minute = (slot - 1) * 5
    end_minute = slot * 5
    start_hour, start_minute = divmod(start_minute, 60)
    end_hour, end_minute = divmod(end_minute, 60)
    ordered = tuple(sorted(entries, key=lambda value: value.key))
    return ReplaySnapshot(
        slot_start_utc=f"2026-02-27T{16 + start_hour:02d}:{start_minute:02d}:00Z",
        slot_end_exclusive_utc=f"2026-02-27T{16 + end_hour:02d}:{end_minute:02d}:00Z",
        boundary="[start,end)",
        continuity_state=continuity,
        missing_reasons=tuple(reasons),
        route_count=len(ordered) if continuity == CONTINUOUS else None,
        entries=ordered,
        slot_changes=(),
    )


def mapping(*assignments):
    return build_country_mapping_view(
        assignments,
        view="compatible",
        target_country="IR",
        source_sha256=HASH_A,
        source_ref="asmap_v1_test",
    )


class OriginDerivationTests(unittest.TestCase):
    def test_only_sequence_tail_is_a_resolved_origin(self):
        resolution = derive_origin_asns(
            (
                AsPathSegment("as_set", (64500, 64501)),
                AsPathSegment("as_sequence", (65000, 65001)),
            )
        )
        self.assertEqual(resolution.state, RESOLVED)
        self.assertEqual(resolution.origins, (65001,))
        self.assertIsNone(resolution.reason)

    def test_as_set_confederation_and_empty_are_never_fabricated(self):
        as_set = derive_origin_asns((AsPathSegment("as_set", (65002, 65001)),))
        confed = derive_origin_asns(
            (AsPathSegment("confederation_sequence", (65001,)),)
        )
        empty = derive_origin_asns(())

        self.assertEqual((as_set.state, as_set.origins), (CONFLICT, (65001, 65002)))
        self.assertEqual((confed.state, confed.origins), (UNKNOWN, ()))
        self.assertEqual((empty.state, empty.origins), (UNKNOWN, ()))
        self.assertEqual(confed.reason, "origin_confederation_segment")
        self.assertEqual(empty.reason, "empty_as_path")

    def test_invalid_segment_or_asn_fails_closed(self):
        with self.assertRaisesRegex(CountryImpactError, "segment_type"):
            derive_origin_asns((AsPathSegment("sequence", (65001,)),))
        with self.assertRaisesRegex(CountryImpactError, "1..4294967295"):
            derive_origin_asns((AsPathSegment("as_sequence", (0,)),))


class MappingAndCohortTests(unittest.TestCase):
    def test_shared_population_batch_is_semantically_equal_and_scanned_once(self):
        shared_entries = tuple(
            entry(
                index + 1,
                prefix=f"10.{index // 256}.{index % 256}.0/24",
                as_path=path(65001),
            )
            for index in range(512)
        )
        start = datetime(2026, 2, 27, 16, tzinfo=timezone.utc)
        snapshots = tuple(
            ReplaySnapshot(
                slot_start_utc=(start + timedelta(minutes=5 * index))
                .isoformat()
                .replace("+00:00", "Z"),
                slot_end_exclusive_utc=(start + timedelta(minutes=5 * (index + 1)))
                .isoformat()
                .replace("+00:00", "Z"),
                boundary="[start,end)",
                continuity_state=UNKNOWN_AFTER_GAP,
                missing_reasons=("sparse_gap",),
                route_count=None,
                entries=shared_entries,
                slot_changes=(),
            )
            for index in range(216)
        )
        baseline = RouteReplayState(
            entries=shared_entries,
            latest_changes=(),
            continuity_state=UNKNOWN_AFTER_GAP,
            missing_reasons=("seed_gap",),
            processed_route_event_ids=frozenset(),
            last_order_key=None,
        )
        country_mapping = mapping(MappingAssignment(65001, ("IR",), MAPPED))

        self.assertEqual(
            snapshot_ids_v1(snapshots[:3]),
            tuple(snapshot_id_v1(row) for row in snapshots[:3]),
        )
        self.assertEqual(
            project_snapshot_origins_series(snapshots[:3]),
            tuple(project_snapshot_origins(row) for row in snapshots[:3]),
        )

        with mock.patch.object(
            country_impact_module,
            "_entry_identity_rows",
            wraps=country_impact_module._entry_identity_rows,
        ) as identities, mock.patch.object(
            country_impact_module,
            "_project_snapshot_origins_with_id",
            wraps=country_impact_module._project_snapshot_origins_with_id,
        ) as projections, mock.patch.object(
            country_impact_module,
            "_projection_target_relations",
            wraps=country_impact_module._projection_target_relations,
        ) as target_relations:
            cohort, impacts = derive_country_cohort_and_impacts(
                baseline, snapshots, country_mapping
            )

        self.assertEqual(len(cohort.covered_snapshots), 216)
        self.assertEqual(len(impacts), 216)
        self.assertEqual(identities.call_count, 1)
        self.assertEqual(projections.call_count, 1)
        # seed 与第一个窗口槽各计算一次；后续 215 个共享人口槽复用结果。
        self.assertEqual(target_relations.call_count, 2)

    def test_large_mapping_lookup_preserves_first_last_and_unknown(self):
        assignments = tuple(
            MappingAssignment(asn, ("IR",), MAPPED)
            for asn in range(1, 10_001)
        )
        view = build_country_mapping_view(
            assignments,
            view="compatible",
            target_country="IR",
            source_sha256=HASH_A,
            source_ref="large-mapping",
        )

        self.assertEqual(view.assignment_for(1).countries, ("IR",))
        self.assertEqual(view.assignment_for(10_000).countries, ("IR",))
        self.assertEqual(view.assignment_for(10_001).mapping_state, UNKNOWN_MAPPING)

    def test_frozen_mapping_adapter_preserves_conflict_and_missing(self):
        frozen = {
            "snapshot_id": "asmap_v1_frozen",
            "target_country": "IR",
            "source_file_sha256": HASH_A,
            "rows": [
                {"asn": 65001, "country_code": "IR"},
                {"asn": 65002, "country_code": None},
                {"asn": 65003, "country_code": "IR"},
            ],
            "conflicts": [
                {
                    "asn": 65003,
                    "kept_country": "IR",
                    "conflicting_country": "US",
                }
            ],
        }
        view = mapping_view_from_frozen_snapshot(frozen)

        self.assertEqual(view.assignment_for(65001).mapping_state, MAPPED)
        self.assertEqual(view.assignment_for(65002).mapping_state, UNKNOWN_MAPPING)
        self.assertEqual(view.assignment_for(65003).mapping_state, CONFLICT_MAPPING)
        self.assertIsNone(view.target_membership(65002))
        self.assertIsNone(view.target_membership(65003))

    def test_static_and_dynamic_members_activate_without_future_leakage(self):
        country_mapping = mapping(
            MappingAssignment(65001, ("IR",), MAPPED),
            MappingAssignment(65004, ("IR",), MAPPED),
        )
        baseline = seed(entry(1, as_path=path(65001)))
        first = snapshot(1, entry(2, as_path=path(65001)))
        second = snapshot(
            2,
            entry(3, as_path=path(65001)),
            entry(4, vp="vp-b", prefix="203.0.113.0/24", as_path=path(65004)),
        )
        cohort = build_country_cohort(baseline, (first, second), country_mapping)

        self.assertEqual(cohort.baseline_asns, (65001,))
        self.assertEqual(cohort.dynamic_asns, (65004,))
        first_impact = compute_country_snapshot_impact(first, cohort)
        second_impact = compute_country_snapshot_impact(second, cohort)
        self.assertEqual(first_impact.baseline_asns.value, (65001,))
        self.assertEqual(first_impact.metrics.baseline_asn_count.value, 1)
        self.assertEqual(second_impact.baseline_asns.value, (65001, 65004))
        self.assertEqual(second_impact.metrics.baseline_asn_count.value, 2)
        self.assertEqual(second_impact.damaged_asns.value, ())

    def test_mapping_conflict_that_may_be_ir_makes_result_unknown_not_zero(self):
        country_mapping = mapping(
            MappingAssignment(65001, ("IR",), MAPPED),
            MappingAssignment(65005, ("IR", "US"), CONFLICT_MAPPING),
        )
        baseline = seed(
            entry(1, as_path=path(65001)),
            entry(2, vp="vp-b", prefix="198.51.100.0/24", as_path=path(65005)),
        )
        current = snapshot(1, *baseline.entries)
        cohort = build_country_cohort(baseline, (current,), country_mapping)
        impact = compute_country_snapshot_impact(current, cohort)

        self.assertEqual(impact.metrics.visible_asn_count.value_state, "unknown_mapping")
        self.assertIsNone(impact.metrics.visible_asn_count.value)
        self.assertIsNone(impact.visible_asns.value)
        self.assertNotEqual(impact.metrics.visible_asn_count.value, 0)


class VisibilityAndMetricTests(unittest.TestCase):
    def setUp(self):
        self.mapping = mapping(
            MappingAssignment(65001, ("IR",), MAPPED),
            MappingAssignment(65002, ("IR",), MAPPED),
            MappingAssignment(65004, ("IR",), MAPPED),
            MappingAssignment(65100, ("US",), MAPPED),
        )

    def test_one_remaining_vp_keeps_prefix_visible(self):
        baseline = seed(
            entry(1, vp="vp-a", as_path=path(65001)),
            entry(2, vp="vp-b", as_path=path(65001)),
        )
        current = snapshot(1, entry(3, vp="vp-b", as_path=path(65001)))
        cohort = build_country_cohort(baseline, (current,), self.mapping)
        impact = compute_country_snapshot_impact(current, cohort)

        self.assertEqual(impact.metrics.visible_ipv4_prefix_count.value, 1)
        self.assertEqual(impact.metrics.visible_ipv4_address_union.value, 256)
        self.assertEqual(impact.damaged_asns.value, ())
        self.assertEqual(impact.visible_asns.value, (65001,))

    def test_overlapping_ipv4_prefixes_are_union_counted_once(self):
        baseline = seed(
            entry(1, prefix="10.0.0.0/23", as_path=path(65001)),
            entry(2, vp="vp-b", prefix="10.0.0.0/24", as_path=path(65001)),
        )
        current = snapshot(1, *baseline.entries)
        cohort = build_country_cohort(baseline, (current,), self.mapping)
        impact = compute_country_snapshot_impact(current, cohort)

        self.assertEqual(impact.metrics.visible_ipv4_prefix_count.value, 2)
        self.assertEqual(impact.metrics.visible_ipv4_address_union.value, 512)
        self.assertEqual(impact.metrics.visible_ipv4_24_equivalent.value, 2)

    def test_ipv6_48_equivalent_is_separate_from_ipv4_address_count(self):
        baseline = seed(
            entry(1, prefix="2001:db8::/47", as_path=path(65001)),
            entry(2, vp="vp-b", prefix="2001:db8::/48", as_path=path(65001)),
        )
        current = snapshot(1, *baseline.entries)
        cohort = build_country_cohort(baseline, (current,), self.mapping)
        impact = compute_country_snapshot_impact(current, cohort)

        self.assertEqual(impact.metrics.visible_ipv4_address_union.value, 0)
        self.assertEqual(impact.metrics.visible_ipv6_prefix_count.value, 2)
        self.assertEqual(impact.metrics.visible_ipv6_48_equivalent.value, 2)

    def test_moas_relations_are_retained_but_country_union_is_not_additive(self):
        baseline = seed(
            entry(1, vp="vp-a", prefix="10.1.0.0/24", as_path=path(65001)),
            entry(2, vp="vp-b", prefix="10.1.0.0/24", as_path=path(65002)),
        )
        current = snapshot(1, *baseline.entries)
        projection = project_snapshot_origins(current)
        cohort = build_country_cohort(baseline, (current,), self.mapping)
        impact = compute_country_snapshot_impact(current, cohort)

        self.assertEqual(len(projection.relations), 1)
        self.assertEqual(projection.relations[0].origins, (65001, 65002))
        self.assertTrue(projection.relations[0].moas)
        self.assertEqual(
            tuple(
                (observation.vp_id, observation.origins)
                for observation in projection.relations[0].observations
            ),
            (("vp-a", (65001,)), ("vp-b", (65002,))),
        )
        self.assertEqual(impact.metrics.visible_asn_count.value, 2)
        self.assertEqual(impact.metrics.visible_ipv4_prefix_count.value, 1)
        self.assertEqual(impact.metrics.visible_ipv4_address_union.value, 256)
        for asn_impact in impact.asn_impacts:
            self.assertEqual(asn_impact.address_families[0].moas_prefixes, ("10.1.0.0/24",))

    def test_per_as_damage_uses_baseline_and_current_from_same_snapshot(self):
        baseline = seed(
            entry(1, prefix="10.0.0.0/24", as_path=path(65001)),
            entry(2, vp="vp-b", prefix="10.0.1.0/24", as_path=path(65001)),
            entry(3, vp="vp-c", prefix="2001:db8::/48", as_path=path(65002)),
        )
        current = snapshot(
            1,
            entry(4, prefix="10.0.0.0/24", as_path=path(65001)),
        )
        cohort = build_country_cohort(baseline, (current,), self.mapping)
        impact = compute_country_snapshot_impact(current, cohort)

        self.assertEqual(impact.visible_asns.value, (65001,))
        self.assertEqual(impact.damaged_asns.value, (65001, 65002))
        self.assertEqual(impact.metrics.damaged_asn_ratio.value, 1.0)
        by_asn = {value.asn: value for value in impact.asn_impacts}
        self.assertEqual(
            by_asn[65001].address_families[0].lost_prefixes,
            ("10.0.1.0/24",),
        )
        self.assertEqual(by_asn[65001].overall_classification, "partially_visible")
        self.assertEqual(by_asn[65002].overall_classification, "ipv6_only_fully_invisible")

    def test_dynamic_prefix_can_be_damaged_after_its_first_observation(self):
        baseline = seed(entry(1, as_path=path(65001)))
        first = snapshot(
            1,
            entry(2, as_path=path(65001)),
            entry(3, vp="vp-b", prefix="203.0.113.0/24", as_path=path(65004)),
        )
        second = snapshot(2, entry(4, as_path=path(65001)))
        cohort = build_country_cohort(baseline, (first, second), self.mapping)

        first_impact = compute_country_snapshot_impact(first, cohort)
        second_impact = compute_country_snapshot_impact(second, cohort)
        self.assertEqual(first_impact.damaged_asns.value, ())
        self.assertEqual(second_impact.damaged_asns.value, (65004,))
        dynamic = next(value for value in second_impact.asn_impacts if value.asn == 65004)
        self.assertTrue(dynamic.dynamic_member)
        self.assertEqual(dynamic.overall_classification, "ipv4_only_fully_invisible")

    def test_state_gap_returns_unknown_values_and_sets_not_zero(self):
        baseline = seed(entry(1, as_path=path(65001)))
        current = snapshot(
            1,
            entry(2, as_path=path(65001)),
            continuity=UNKNOWN_AFTER_GAP,
            reasons=("update-slot-missing",),
        )
        cohort = build_country_cohort(baseline, (current,), self.mapping)
        impact = compute_country_snapshot_impact(current, cohort)

        self.assertEqual(impact.metrics.visible_ipv4_prefix_count.value_state, "unknown_state_gap")
        self.assertIsNone(impact.metrics.visible_ipv4_prefix_count.value)
        self.assertIsNone(impact.damaged_asns.value)
        self.assertEqual(impact.asn_impacts, ())

    def test_all_country_values_and_ratio_components_share_snapshot_identity(self):
        baseline = seed(entry(1, as_path=path(65001)))
        first = snapshot(1, entry(2, as_path=path(65001)))
        second = snapshot(2, entry(3, as_path=path(65001)))
        cohort = build_country_cohort(baseline, (first, second), self.mapping)
        impact = compute_country_snapshot_impact(first, cohort)

        metric_ids = {
            value.snapshot_id
            for value in (
                impact.metrics.visible_asn_count,
                impact.metrics.damaged_asn_count,
                impact.metrics.baseline_asn_count,
                impact.metrics.visible_ipv4_prefix_count,
                impact.metrics.visible_ipv6_prefix_count,
                impact.metrics.visible_ipv4_address_union,
                impact.metrics.visible_ipv4_24_equivalent,
                impact.metrics.visible_ipv6_48_equivalent,
            )
        }
        self.assertEqual(metric_ids, {impact.snapshot_id})
        self.assertEqual(impact.metrics.damaged_asn_ratio.snapshot_id, impact.snapshot_id)
        self.assertEqual(impact.visible_asns.snapshot_id, impact.snapshot_id)
        self.assertEqual(impact.damaged_asns.snapshot_id, impact.snapshot_id)
        self.assertEqual(impact.baseline_asns.snapshot_id, impact.snapshot_id)
        self.assertNotEqual(snapshot_id_v1(first), snapshot_id_v1(second))

    def test_snapshot_not_used_for_cohort_is_rejected(self):
        baseline = seed(entry(1, as_path=path(65001)))
        frozen = snapshot(1, entry(2, as_path=path(65001)))
        other = snapshot(2, entry(3, as_path=path(65001)))
        cohort = build_country_cohort(baseline, (frozen,), self.mapping)

        with self.assertRaisesRegex(CountryImpactError, "不属于"):
            compute_country_snapshot_impact(other, cohort)


if __name__ == "__main__":
    unittest.main()
