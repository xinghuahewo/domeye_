import dataclasses
import hashlib
import unittest

from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    CONTINUOUS,
    UNKNOWN_AFTER_GAP,
    InputGap,
    StateReplayError,
    apply_catch_up_updates,
    build_research_route_event,
    replay_five_minute_window,
    seed_state_from_rib,
)
from backend.data_pipeline.route_event import (
    AsPathSegment,
    ParsedRouteElement,
    artifact_id_v1,
)


def _path(*asns):
    return (AsPathSegment("as_sequence", tuple(asns)),)


def _event(
    *,
    action,
    event_time,
    artifact_slot,
    prefix="203.0.113.0/24",
    peer_ip="192.0.2.1",
    peer_asn=64500,
    as_path=None,
    quality_flags=(),
    record_ordinal=0,
    element_ordinal=0,
    file_label="artifact-a",
):
    file_sha256 = hashlib.sha256(file_label.encode("ascii")).hexdigest()
    if as_path is None and action != "withdraw":
        as_path = _path(peer_asn, 64496)
    element = ParsedRouteElement(
        event_time_utc=event_time,
        peer_ip=peer_ip,
        peer_asn=peer_asn,
        action=action,
        prefix=prefix,
        afi_safi="ipv6_unicast" if ":" in prefix else "ipv4_unicast",
        as_path=as_path,
        quality_flags=quality_flags,
    )
    return build_research_route_event(
        artifact_id=artifact_id_v1(file_sha256),
        file_sha256=file_sha256,
        collector_id="rrc25",
        artifact_slot_utc=artifact_slot,
        record_ordinal=record_ordinal,
        element_ordinal=element_ordinal,
        element=element,
    )


class Rrc25ResearchStateReplayTests(unittest.TestCase):
    def _seed(self):
        return seed_state_from_rib(
            (
                _event(
                    action="rib_snapshot",
                    event_time="2026-02-27T23:55:00Z",
                    artifact_slot="2026-02-27T23:55:00Z",
                    file_label="seed",
                ),
            )
        )

    def test_withdraw_only_deletes_same_vp_prefix(self):
        seed = seed_state_from_rib(
            (
                _event(
                    action="rib_snapshot",
                    event_time="2026-02-27T23:55:00Z",
                    artifact_slot="2026-02-27T23:55:00Z",
                    peer_ip="192.0.2.1",
                    peer_asn=64500,
                    file_label="seed-multi-vp",
                    element_ordinal=0,
                ),
                _event(
                    action="rib_snapshot",
                    event_time="2026-02-27T23:55:00Z",
                    artifact_slot="2026-02-27T23:55:00Z",
                    peer_ip="192.0.2.2",
                    peer_asn=64501,
                    file_label="seed-multi-vp",
                    element_ordinal=1,
                ),
            )
        )
        withdrawn = _event(
            action="withdraw",
            event_time="2026-02-28T00:00:01Z",
            artifact_slot="2026-02-28T00:00:00Z",
            peer_ip="192.0.2.1",
            peer_asn=64500,
            file_label="withdraw-vp-one",
        )
        result = apply_catch_up_updates(seed, (withdrawn,))

        self.assertEqual(result.route_count, 1)
        self.assertEqual(result.entries[0].peer_ip, "192.0.2.2")
        by_key = {change.key: change for change in result.latest_changes}
        self.assertEqual(by_key[withdrawn.key].action, "withdraw")
        self.assertEqual(
            by_key[withdrawn.key].raw_ref.route_event_id,
            withdrawn.route_event_id,
        )

    def test_ipv4_and_ipv6_are_separate_state_keys(self):
        state = seed_state_from_rib(
            (
                _event(
                    action="rib_snapshot",
                    event_time="2026-02-27T23:55:00Z",
                    artifact_slot="2026-02-27T23:55:00Z",
                    prefix="203.0.113.7/24",
                    file_label="dual-stack-seed",
                    element_ordinal=0,
                ),
                _event(
                    action="rib_snapshot",
                    event_time="2026-02-27T23:55:00Z",
                    artifact_slot="2026-02-27T23:55:00Z",
                    prefix="2001:db8:1::99/48",
                    file_label="dual-stack-seed",
                    element_ordinal=1,
                ),
            )
        )
        self.assertEqual(state.route_count, 2)
        self.assertEqual(
            {(entry.key.afi_safi, entry.key.prefix) for entry in state.entries},
            {
                ("ipv4_unicast", "203.0.113.0/24"),
                ("ipv6_unicast", "2001:db8:1::/48"),
            },
        )

    def test_announce_replaces_path_flags_and_latest_raw_ref(self):
        seed = self._seed()
        replacement = _event(
            action="announce",
            event_time="2026-02-28T00:00:02Z",
            artifact_slot="2026-02-28T00:00:00Z",
            as_path=(
                AsPathSegment("as_sequence", (64500, 64497)),
                AsPathSegment("as_set", (64498, 64499)),
            ),
            quality_flags=("parser_warning", "as_set_present"),
            file_label="replacement-update",
        )
        result = apply_catch_up_updates(seed, (replacement,))

        self.assertEqual(result.entries[0].as_path, replacement.as_path)
        self.assertEqual(
            result.entries[0].quality_flags,
            ("as_set_present", "parser_warning"),
        )
        self.assertEqual(
            result.entries[0].last_raw_ref.route_event_id,
            replacement.route_event_id,
        )

    def test_seed_catchup_and_window_are_distinct_phases(self):
        seed = self._seed()
        catch_up = _event(
            action="announce",
            event_time="2026-02-28T00:05:00Z",
            artifact_slot="2026-02-28T00:05:00Z",
            as_path=_path(64500, 64497),
            file_label="catch-up",
        )
        at_window_start = _event(
            action="announce",
            event_time="2026-02-28T00:10:00Z",
            artifact_slot="2026-02-28T00:10:00Z",
            as_path=_path(64500, 64498),
            file_label="window-start",
        )
        at_second_slot = _event(
            action="withdraw",
            event_time="2026-02-28T00:15:00Z",
            artifact_slot="2026-02-28T00:15:00Z",
            file_label="window-second",
        )

        start_state = apply_catch_up_updates(seed, (catch_up,))
        result = replay_five_minute_window(
            start_state,
            (at_window_start, at_second_slot),
            window_start_utc="2026-02-28T00:10:00Z",
            window_end_exclusive_utc="2026-02-28T00:20:00Z",
        )

        self.assertEqual(len(result.snapshots), 2)
        self.assertEqual(result.snapshots[0].route_count, 1)
        self.assertEqual(
            result.snapshots[0].entries[0].as_path,
            at_window_start.as_path,
        )
        self.assertEqual(result.snapshots[1].route_count, 0)
        self.assertEqual(result.final_state.route_count, 0)

    def test_unordered_input_replays_deterministically(self):
        seed = self._seed()
        first = _event(
            action="announce",
            event_time="2026-02-28T00:00:01Z",
            artifact_slot="2026-02-28T00:00:00Z",
            as_path=_path(64500, 64497),
            file_label="ordered-artifact",
            record_ordinal=0,
        )
        second = _event(
            action="announce",
            event_time="2026-02-28T00:00:02Z",
            artifact_slot="2026-02-28T00:00:00Z",
            as_path=_path(64500, 64498),
            file_label="ordered-artifact",
            record_ordinal=1,
        )

        ordered = apply_catch_up_updates(seed, (first, second))
        reversed_input = apply_catch_up_updates(seed, (second, first))
        self.assertEqual(ordered, reversed_input)
        self.assertEqual(ordered.entries[0].as_path, second.as_path)

    def test_same_sort_key_and_conflicting_stable_identity_fail_closed(self):
        seed = self._seed()
        first = _event(
            action="announce",
            event_time="2026-02-28T00:00:01Z",
            artifact_slot="2026-02-28T00:00:00Z",
            file_label="sort-conflict-a",
        )
        second = _event(
            action="announce",
            event_time="2026-02-28T00:00:01Z",
            artifact_slot="2026-02-28T00:00:00Z",
            file_label="sort-conflict-b",
        )
        with self.assertRaisesRegex(StateReplayError, "相同确定性排序键"):
            apply_catch_up_updates(seed, (first, second))

        tampered = dataclasses.replace(first, route_event_id=second.route_event_id)
        with self.assertRaisesRegex(StateReplayError, "稳定身份"):
            apply_catch_up_updates(seed, (tampered,))

    def test_five_minute_slots_use_half_open_boundary(self):
        seed = self._seed()
        at_boundary = _event(
            action="withdraw",
            event_time="2026-02-28T00:05:00Z",
            artifact_slot="2026-02-28T00:05:00Z",
            file_label="half-open-boundary",
        )
        result = replay_five_minute_window(
            seed,
            (at_boundary,),
            window_start_utc="2026-02-28T00:00:00Z",
            window_end_exclusive_utc="2026-02-28T00:10:00Z",
        )

        self.assertEqual(result.snapshots[0].route_count, 1)
        self.assertEqual(result.snapshots[0].slot_changes, ())
        self.assertEqual(result.snapshots[1].route_count, 0)
        self.assertEqual(
            result.snapshots[1].slot_changes[0].raw_ref.route_event_id,
            at_boundary.route_event_id,
        )

        at_window_end = _event(
            action="announce",
            event_time="2026-02-28T00:10:00Z",
            artifact_slot="2026-02-28T00:10:00Z",
            file_label="outside-half-open-window",
        )
        with self.assertRaisesRegex(StateReplayError, "半开区间"):
            replay_five_minute_window(
                seed,
                (at_window_end,),
                window_start_utc="2026-02-28T00:00:00Z",
                window_end_exclusive_utc="2026-02-28T00:10:00Z",
            )

    def test_gap_turns_later_snapshots_unknown_without_zero_fill(self):
        seed = self._seed()
        result = replay_five_minute_window(
            seed,
            (),
            window_start_utc="2026-02-28T00:00:00Z",
            window_end_exclusive_utc="2026-02-28T00:15:00Z",
            input_gaps=(
                InputGap(
                    start_utc="2026-02-28T00:05:00Z",
                    end_exclusive_utc="2026-02-28T00:10:00Z",
                    missing_reason="artifact_slot_missing",
                ),
            ),
        )

        self.assertEqual(result.snapshots[0].continuity_state, CONTINUOUS)
        self.assertEqual(result.snapshots[0].route_count, 1)
        self.assertEqual(
            [snapshot.continuity_state for snapshot in result.snapshots[1:]],
            [UNKNOWN_AFTER_GAP, UNKNOWN_AFTER_GAP],
        )
        self.assertIsNone(result.snapshots[1].route_count)
        self.assertIsNone(result.snapshots[2].route_count)
        self.assertEqual(
            result.snapshots[2].missing_reasons,
            ("artifact_slot_missing",),
        )
        self.assertEqual(len(result.snapshots[2].entries), 1)
        self.assertIsNone(result.final_state.route_count)

    def test_seed_or_catchup_gap_is_never_recovered_by_later_updates(self):
        seed = seed_state_from_rib(
            (
                _event(
                    action="rib_snapshot",
                    event_time="2026-02-27T23:55:00Z",
                    artifact_slot="2026-02-27T23:55:00Z",
                    file_label="gapped-seed",
                ),
            ),
            input_gaps=(
                InputGap(
                    "2026-02-27T23:50:00Z",
                    "2026-02-27T23:55:00Z",
                    "rib_parse_failure",
                ),
            ),
        )
        later = _event(
            action="announce",
            event_time="2026-02-28T00:00:01Z",
            artifact_slot="2026-02-28T00:00:00Z",
            file_label="later-after-gap",
        )
        result = apply_catch_up_updates(seed, (later,))
        self.assertEqual(result.continuity_state, UNKNOWN_AFTER_GAP)
        self.assertIsNone(result.route_count)
        self.assertEqual(result.missing_reasons, ("rib_parse_failure",))


if __name__ == "__main__":
    unittest.main()
