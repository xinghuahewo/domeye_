import unittest
from unittest import mock

from backend.data_pipeline.route_event import AsPathSegment
from backend.data_pipeline.research.rrc25_country_outage import (
    mapped_compatible_projection as projection_module,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    CONFLICT_MAPPING,
    MAPPED,
    UNKNOWN_MAPPING,
    MappingAssignment,
    build_country_mapping_view,
)
from backend.data_pipeline.research.rrc25_country_outage.mapped_compatible_projection import (
    MappedCompatibleProjectionError,
    build_mapped_compatible_projection,
    build_mapped_compatible_projection_series,
)
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    CONTINUOUS,
    UNKNOWN_AFTER_GAP,
    RawRecordRef,
    ReplaySnapshot,
    RouteLastChange,
    RouteReplayState,
    RouteStateEntry,
    RouteStateKey,
)


HASH = "a" * 64


def _path(asn):
    return (AsPathSegment("as_sequence", (64500, asn)),)


def _raw(ordinal):
    return RawRecordRef(
        artifact_id=f"art_v1_{ordinal:032x}",
        file_sha256=f"{ordinal:064x}",
        collector_id="rrc25",
        artifact_slot_utc="2026-02-27T16:00:00Z",
        record_ordinal=ordinal,
        element_ordinal=0,
        route_event_id=f"rte_v1_{ordinal:032x}",
    )


def _entry(ordinal, *, vp, prefix, as_path):
    raw = _raw(ordinal)
    return RouteStateEntry(
        key=RouteStateKey("rrc25", vp, "ipv4_unicast", prefix),
        peer_ip=f"192.0.2.{ordinal}",
        peer_asn=64500,
        as_path=as_path,
        quality_flags=(),
        last_action="announce",
        last_event_time_utc="2026-02-27T16:00:00Z",
        last_raw_ref=raw,
    )


def _change(ordinal, *, vp, prefix, as_path, action="announce"):
    return RouteLastChange(
        key=RouteStateKey("rrc25", vp, "ipv4_unicast", prefix),
        action=action,
        event_time_utc="2026-02-27T16:01:00Z",
        as_path=as_path,
        quality_flags=(),
        raw_ref=_raw(ordinal),
    )


def _mapping(view="compatible"):
    return build_country_mapping_view(
        (
            MappingAssignment(65001, ("IR",), MAPPED),
            MappingAssignment(65002, ("US",), MAPPED),
            MappingAssignment(65004, (), UNKNOWN_MAPPING),
            MappingAssignment(65005, ("IR", "US"), CONFLICT_MAPPING),
        ),
        view=view,
        target_country="IR",
        source_sha256=HASH,
        source_ref="asmap_v1_synthetic",
    )


def _state(entries, changes=(), *, continuity=CONTINUOUS, reasons=()):
    return RouteReplayState(
        entries=tuple(sorted(entries, key=lambda item: item.key)),
        latest_changes=tuple(changes),
        continuity_state=continuity,
        missing_reasons=tuple(reasons),
        processed_route_event_ids=frozenset(
            item.last_raw_ref.route_event_id for item in entries
        ),
        last_order_key=None,
    )


class MappedCompatibleProjectionTests(unittest.TestCase):
    def test_series_reuses_unchanged_population_without_repeated_full_scan(self):
        entries = (
            _entry(
                90,
                vp="vp-series-ir",
                prefix="203.0.113.0/24",
                as_path=_path(65001),
            ),
            _entry(
                91,
                vp="vp-series-us",
                prefix="198.51.100.0/24",
                as_path=_path(65002),
            ),
        )
        shared_entries = tuple(sorted(entries, key=lambda item: item.key))
        snapshots = tuple(
            ReplaySnapshot(
                slot_start_utc=f"2026-02-27T16:{index:02d}:00Z",
                slot_end_exclusive_utc=f"2026-02-27T16:{index + 1:02d}:00Z",
                boundary="[start,end)",
                continuity_state=UNKNOWN_AFTER_GAP,
                missing_reasons=("sparse_slot_gap",),
                route_count=None,
                entries=shared_entries,
                slot_changes=(),
            )
            for index in range(20)
        )
        mapping = _mapping()
        expected = tuple(
            build_mapped_compatible_projection(snapshot, mapping)
            for snapshot in snapshots
        )

        with mock.patch.object(
            projection_module,
            "_validated_entry_classification",
            wraps=projection_module._validated_entry_classification,
        ) as classify, mock.patch.object(
            projection_module,
            "_projection_id",
            wraps=projection_module._projection_id,
        ) as projection_id:
            actual = build_mapped_compatible_projection_series(snapshots, mapping)

        self.assertEqual(actual, expected)
        self.assertEqual(classify.call_count, 1)
        self.assertEqual(projection_id.call_count, 1)
        self.assertEqual(
            tuple(row.projected.slot_start_utc for row in actual),
            tuple(snapshot.slot_start_utc for snapshot in snapshots),
        )

    def test_retains_target_and_non_target_moas_context(self):
        iran = _entry(
            1,
            vp="vp-ir",
            prefix="203.0.113.0/24",
            as_path=_path(65001),
        )
        non_iran = _entry(
            2,
            vp="vp-us",
            prefix="203.0.113.0/24",
            as_path=_path(65002),
        )

        result = build_mapped_compatible_projection(
            _state((non_iran, iran)), _mapping()
        )

        self.assertEqual(result.projection_kind, "mapped_compatible_projection")
        self.assertEqual(result.audit.retained_entry_count, 2)
        self.assertEqual(result.audit.excluded_entry_count, 0)
        self.assertEqual(
            result.projected.entries,
            tuple(sorted((iran, non_iran), key=lambda item: item.key)),
        )
        context = result.audit.prefix_contexts[0]
        self.assertTrue(context.moas)
        self.assertEqual(context.origin_asns, (65001, 65002))
        self.assertEqual(context.country_codes, ("IR", "US"))
        self.assertEqual(context.target_origin_asns, (65001,))
        self.assertEqual(context.non_target_origin_asns, (65002,))
        self.assertIn("not_a_strict_full_population", result.limitations)
        self.assertIn("strict_population_completeness_not_proven", result.blockers)

    def test_ambiguous_and_unmapped_entries_are_excluded_without_guessing(self):
        entries = (
            _entry(
                10,
                vp="vp-as-set",
                prefix="10.0.0.0/24",
                as_path=(AsPathSegment("as_set", (65001, 65002)),),
            ),
            _entry(
                11,
                vp="vp-confed",
                prefix="10.0.1.0/24",
                as_path=(AsPathSegment("confederation_sequence", (65001,)),),
            ),
            _entry(12, vp="vp-empty", prefix="10.0.2.0/24", as_path=()),
            _entry(13, vp="vp-unknown", prefix="10.0.3.0/24", as_path=_path(65004)),
            _entry(14, vp="vp-conflict", prefix="10.0.4.0/24", as_path=_path(65005)),
        )

        result = build_mapped_compatible_projection(_state(entries), _mapping())

        self.assertEqual(result.route_count, 0)
        self.assertEqual(result.audit.retained_entry_count, 0)
        self.assertEqual(result.audit.excluded_entry_count, 5)
        self.assertEqual(
            dict(result.audit.excluded_reason_counts),
            {
                "country_mapping_conflict": 1,
                "country_mapping_unknown": 1,
                "empty_as_path": 1,
                "origin_as_set": 1,
                "origin_confederation_segment": 1,
            },
        )
        self.assertEqual(len(result.audit.excluded_prefixes), 5)
        self.assertEqual(len(result.audit.excluded_vp_ids), 5)
        self.assertEqual(len(result.audit.excluded_route_event_ids), 5)
        as_set_ref = next(
            ref for ref in result.audit.excluded_refs if ref.reason == "origin_as_set"
        )
        self.assertEqual(as_set_ref.candidate_origin_asns, (65001, 65002))
        self.assertIn("strict_population_blocked_by_unresolved_origin", result.blockers)
        self.assertIn("strict_population_blocked_by_unresolved_mapping", result.blockers)

    def test_changes_follow_the_same_resolved_and_mapped_rule(self):
        mapped = _change(
            20,
            vp="vp-mapped",
            prefix="10.1.0.0/24",
            as_path=_path(65001),
        )
        withdrawal = _change(
            21,
            vp="vp-withdraw",
            prefix="10.1.1.0/24",
            as_path=None,
            action="withdraw",
        )
        unknown_mapping = _change(
            22,
            vp="vp-unknown",
            prefix="10.1.2.0/24",
            as_path=_path(65004),
        )
        result = build_mapped_compatible_projection(
            _state((), (unknown_mapping, withdrawal, mapped)), _mapping()
        )

        self.assertEqual(result.projected.latest_changes, (mapped,))
        self.assertEqual(result.audit.input_change_count, 3)
        self.assertEqual(result.audit.retained_change_count, 1)
        self.assertEqual(result.audit.excluded_change_count, 2)
        self.assertEqual(
            dict(result.audit.excluded_reason_counts),
            {"country_mapping_unknown": 1, "missing_as_path": 1},
        )

    def test_unknown_continuity_stays_unknown_instead_of_becoming_zero(self):
        source = ReplaySnapshot(
            slot_start_utc="2026-02-27T16:00:00Z",
            slot_end_exclusive_utc="2026-02-27T16:05:00Z",
            boundary="[start,end)",
            continuity_state=UNKNOWN_AFTER_GAP,
            missing_reasons=("missing_update_slot",),
            route_count=None,
            entries=(
                _entry(
                    30,
                    vp="vp-unresolved",
                    prefix="10.2.0.0/24",
                    as_path=(),
                ),
            ),
            slot_changes=(),
        )

        result = build_mapped_compatible_projection(source, _mapping())

        self.assertEqual(result.continuity_state, UNKNOWN_AFTER_GAP)
        self.assertEqual(result.missing_reasons, ("missing_update_slot",))
        self.assertIsNone(result.route_count)
        self.assertIsNone(result.projected.route_count)
        self.assertNotEqual(result.route_count, 0)
        self.assertIn("continuity_unknown_after_input_gap", result.blockers)

    def test_projection_is_stable_and_does_not_mutate_input(self):
        first = _entry(
            40, vp="vp-b", prefix="10.3.1.0/24", as_path=_path(65002)
        )
        second = _entry(
            41, vp="vp-a", prefix="10.3.0.0/24", as_path=_path(65001)
        )
        source = _state((first, second))
        before = source

        one = build_mapped_compatible_projection(source, _mapping())
        two = build_mapped_compatible_projection(source, _mapping())

        self.assertEqual(source, before)
        self.assertIsNot(one.projected, source)
        self.assertEqual(one, two)
        self.assertEqual(one.projection_id, two.projection_id)
        self.assertRegex(one.projection_id, r"^mcp_v1_[0-9a-f]{32}$")

    def test_revised_mapping_cannot_be_mislabeled_as_compatible(self):
        with self.assertRaisesRegex(MappedCompatibleProjectionError, "compatible"):
            build_mapped_compatible_projection(_state(()), _mapping("revised"))


if __name__ == "__main__":
    unittest.main()
