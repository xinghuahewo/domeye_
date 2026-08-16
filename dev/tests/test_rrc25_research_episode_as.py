from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import unittest

from backend.data_pipeline.route_event import (
    AsPathSegment,
    ParsedRouteElement,
    artifact_id_v1,
    route_event_id_v1,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    MAPPED,
    MappingAssignment,
    MeasuredValue,
    build_country_cohort,
    build_country_mapping_view,
    compute_country_snapshot_impact,
)
from backend.data_pipeline.research.rrc25_country_outage.episode_as import (
    EpisodeAsBuildError,
    build_episode_as_records,
)
from backend.data_pipeline.research.rrc25_country_outage.episodes import (
    DurationEstimate,
    EpisodeDetection,
)
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    CONTINUOUS,
    UNKNOWN_AFTER_GAP,
    RawRecordRef,
    ReplaySnapshot,
    RouteReplayState,
    RouteStateEntry,
    RouteStateKey,
    build_research_route_event,
)


ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 2, 27, 16, 0, tzinfo=timezone.utc)
RUN_ID = "research_run_v1_" + "a" * 24
EPISODE_ID = "episode_v1_" + "b" * 24
MAPPING_HASH = "c" * 64
FILE_HASH = "d" * 64


def _time(value):
    return value.isoformat().replace("+00:00", "Z")


def _path(asn):
    return (AsPathSegment("as_sequence", (64500, asn)),)


def _entry(ordinal, asn, prefix, vp=None):
    vp = vp or f"vp-{ordinal}"
    raw_ref = RawRecordRef(
        artifact_id=artifact_id_v1(FILE_HASH),
        file_sha256=FILE_HASH,
        collector_id="rrc25",
        artifact_slot_utc="2026-02-27T16:00:00Z",
        record_ordinal=ordinal,
        element_ordinal=0,
        route_event_id=route_event_id_v1(FILE_HASH, ordinal, 0),
    )
    afi = "ipv6_unicast" if ":" in prefix else "ipv4_unicast"
    return RouteStateEntry(
        key=RouteStateKey("rrc25", vp, afi, prefix),
        peer_ip="192.0.2.1",
        peer_asn=64500,
        as_path=_path(asn),
        quality_flags=(),
        last_action="announce",
        last_event_time_utc="2026-02-27T16:00:00Z",
        last_raw_ref=raw_ref,
    )


def _seed(*entries):
    ordered = tuple(sorted(entries, key=lambda item: item.key))
    return RouteReplayState(
        entries=ordered,
        latest_changes=(),
        continuity_state=CONTINUOUS,
        missing_reasons=(),
        processed_route_event_ids=frozenset(
            item.last_raw_ref.route_event_id for item in ordered
        ),
        last_order_key=None,
    )


def _snapshot(index, *entries):
    start = START + timedelta(minutes=5 * index)
    end = start + timedelta(minutes=5)
    ordered = tuple(sorted(entries, key=lambda item: item.key))
    return ReplaySnapshot(
        slot_start_utc=_time(start),
        slot_end_exclusive_utc=_time(end),
        boundary="[start,end)",
        continuity_state=CONTINUOUS,
        missing_reasons=(),
        route_count=len(ordered),
        entries=ordered,
        slot_changes=(),
    )


def _mapping(*asns):
    return build_country_mapping_view(
        tuple(MappingAssignment(asn, ("IR",), MAPPED) for asn in asns),
        view="compatible",
        target_country="IR",
        source_sha256=MAPPING_HASH,
        source_ref="mapping/as-country.json",
    )


def _samples_and_impacts(snapshots, cohort):
    samples = {}
    impacts = {}
    for index, snapshot in enumerate(snapshots, 1):
        impact = compute_country_snapshot_impact(snapshot, cohort)
        sample_id = f"sample_v1_{index:024x}"
        samples[sample_id] = {
            "sample_id": sample_id,
            "run_id": RUN_ID,
            "snapshot_id": impact.snapshot_id,
            "collector_id": "rrc25",
            "country_code": "IR",
            "cohort_view": "compatible",
            "slot": {
                "start": snapshot.slot_start_utc,
                "end": snapshot.slot_end_exclusive_utc,
                "boundary": "[start,end)",
                "granularity_seconds": 300,
            },
        }
        impacts[sample_id] = impact
    return samples, impacts


def _episode(sample_count, peak_index=1):
    sample_ids = tuple(f"sample_v1_{index:024x}" for index in range(1, sample_count + 1))
    end = START + timedelta(minutes=5 * sample_count)
    return EpisodeDetection(
        episode_id=EPISODE_ID,
        run_id=RUN_ID,
        collector_id="rrc25",
        country_code="IR",
        cohort_view="compatible",
        algorithm_version="country_outage_episode_v1",
        onset_at=_time(START),
        detected_at=_time(START + timedelta(minutes=5)),
        peak_at=_time(START + timedelta(minutes=5 * peak_index)),
        trough_at=_time(START + timedelta(minutes=5 * peak_index)),
        partial_recovery_at=None,
        full_recovery_at=None,
        observation_end_at=_time(end),
        recovery_state="ongoing",
        duration=DurationEstimate(
            duration_state="lower_bound",
            seconds=None,
            minimum_seconds=sample_count * 300,
            maximum_seconds=None,
            measured_to=_time(end),
        ),
        supporting_sample_ids=sample_ids,
        wave_ids=(),
        split_evidence=(),
        recovery_candidates=(),
    )


def _withdraw_event(ordinal, prefix, when):
    return build_research_route_event(
        artifact_id=artifact_id_v1(FILE_HASH),
        file_sha256=FILE_HASH,
        collector_id="rrc25",
        artifact_slot_utc="2026-02-27T16:00:00Z",
        record_ordinal=ordinal,
        element_ordinal=0,
        element=ParsedRouteElement(
            event_time_utc=when,
            peer_ip="192.0.2.1",
            peer_asn=64500,
            action="withdraw",
            prefix=prefix,
            afi_safi="ipv6_unicast" if ":" in prefix else "ipv4_unicast",
            as_path=None,
            quality_flags=(),
        ),
    )


def _validate_contract(record):
    schema = ROOT / "contracts/research/country-outage-episode-as.schema.json"
    script = r"""
const fs = require('fs')
const path = require('path')
const root = process.argv[1]
const schemaPath = process.argv[2]
const Ajv2020 = require(path.join(root, 'frontend', 'node_modules', '@redocly', 'ajv', 'dist', '2020')).default
const contract = JSON.parse(fs.readFileSync(schemaPath, 'utf8'))
const payload = JSON.parse(fs.readFileSync(0, 'utf8'))
const ajv = new Ajv2020({allErrors: true, allowUnionTypes: true, strict: true, validateFormats: true})
ajv.addFormat('date-time', {
  type: 'string',
  validate: (value) => /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value) && Number.isFinite(Date.parse(value)),
})
const validate = ajv.compile(contract)
if (!validate(payload)) {
  process.stderr.write(ajv.errorsText(validate.errors, {separator: '; '}))
  process.exit(1)
}
"""
    completed = subprocess.run(
        ["node", "-e", script, str(ROOT), str(schema)],
        input=json.dumps(record, ensure_ascii=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)


class EpisodeAsAggregationTests(unittest.TestCase):
    def _member_swap_fixture(self):
        mapping = _mapping(65001, 65002)
        baseline = _seed(
            _entry(1, 65001, "10.0.1.0/24", "vp-1-v4"),
            _entry(2, 65001, "2001:db8:1::/48", "vp-1-v6"),
            _entry(3, 65002, "10.0.2.0/24", "vp-2-v4"),
            _entry(4, 65002, "2001:db8:2::/48", "vp-2-v6"),
        )
        trigger = _snapshot(
            0,
            _entry(12, 65001, "2001:db8:1::/48", "vp-1-v6"),
            _entry(13, 65002, "10.0.2.0/24", "vp-2-v4"),
            _entry(14, 65002, "2001:db8:2::/48", "vp-2-v6"),
        )
        peak = _snapshot(
            1,
            _entry(21, 65001, "10.0.1.0/24", "vp-1-v4"),
            _entry(22, 65001, "2001:db8:1::/48", "vp-1-v6"),
            _entry(24, 65002, "2001:db8:2::/48", "vp-2-v6"),
        )
        end = _snapshot(
            2,
            _entry(31, 65001, "10.0.1.0/24", "vp-1-v4"),
            _entry(32, 65001, "2001:db8:1::/48", "vp-1-v6"),
            _entry(34, 65002, "2001:db8:2::/48", "vp-2-v6"),
        )
        cohort = build_country_cohort(baseline, (trigger, peak, end), mapping)
        samples, impacts = _samples_and_impacts((trigger, peak, end), cohort)
        return mapping, cohort, samples, impacts

    def test_same_count_member_swap_preserves_both_asn_histories(self):
        mapping, cohort, samples, impacts = self._member_swap_fixture()
        records = build_episode_as_records(
            _episode(3), samples, impacts, cohort=cohort, mapping=mapping
        )
        by_asn = {record["asn"]: record for record in records}

        self.assertEqual(
            impacts["sample_v1_" + f"{1:024x}"].damaged_asns.value,
            (65001,),
        )
        self.assertEqual(
            impacts["sample_v1_" + f"{2:024x}"].damaged_asns.value,
            (65002,),
        )
        self.assertTrue(by_asn[65001]["trigger_member"])
        self.assertFalse(by_asn[65001]["peak_member"])
        self.assertFalse(by_asn[65001]["observation_end_member"])
        self.assertEqual(by_asn[65001]["recovered_at"], "2026-02-27T16:10:00Z")
        self.assertFalse(by_asn[65002]["trigger_member"])
        self.assertTrue(by_asn[65002]["peak_member"])
        self.assertTrue(by_asn[65002]["observation_end_member"])
        self.assertIsNone(by_asn[65002]["recovered_at"])
        self.assertEqual(
            by_asn[65002]["address_families"]["ipv4"]["cumulative_prefixes"]["value"],
            ["10.0.2.0/24"],
        )
        for record in records:
            _validate_contract(record)

    def test_ipv4_fully_invisible_ipv6_visible_and_actual_address_equivalent(self):
        mapping, cohort, samples, impacts = self._member_swap_fixture()
        record = next(
            item
            for item in build_episode_as_records(
                _episode(3), samples, impacts, cohort=cohort, mapping=mapping
            )
            if item["asn"] == 65002
        )

        self.assertEqual(record["overall_classification"], "ipv4_only_fully_invisible")
        self.assertTrue(
            record["address_families"]["ipv4"]["visibility"]["fully_invisible"]
        )
        self.assertFalse(
            record["address_families"]["ipv6"]["visibility"]["fully_invisible"]
        )
        self.assertEqual(
            record["address_families"]["ipv4"]["lost_equivalent_at_peak"]["value"],
            256,
        )
        self.assertEqual(
            record["address_families"]["ipv6"]["lost_equivalent_at_peak"]["value"],
            0,
        )
        self.assertEqual(
            record["address_families"]["ipv4"]["moas_semantics"],
            "origin_relationship_retained_not_additive",
        )
        _validate_contract(record)

    def test_dynamic_asn_is_kept_and_has_zero_static_baseline(self):
        mapping = _mapping(65001, 65003)
        baseline = _seed(_entry(1, 65001, "10.0.1.0/24"))
        first = _snapshot(
            0,
            _entry(11, 65001, "10.0.1.0/24"),
            _entry(12, 65003, "10.0.3.0/24"),
            _entry(13, 65003, "2001:db8:3::/48"),
        )
        second = _snapshot(
            1,
            _entry(21, 65001, "10.0.1.0/24"),
            _entry(23, 65003, "2001:db8:3::/48"),
        )
        cohort = build_country_cohort(baseline, (first, second), mapping)
        samples, impacts = _samples_and_impacts((first, second), cohort)
        record = next(
            item
            for item in build_episode_as_records(
                _episode(2), samples, impacts, cohort=cohort, mapping=mapping
            )
            if item["asn"] == 65003
        )

        self.assertEqual(record["address_families"]["ipv4"]["baseline_prefix_count"]["value"], 0)
        self.assertFalse(record["trigger_member"])
        self.assertTrue(record["peak_member"])
        self.assertTrue(record["cumulative_member"])
        _validate_contract(record)

    def test_unknown_end_state_is_null_not_zero_and_does_not_claim_recovery(self):
        mapping, cohort, samples, impacts = self._member_swap_fixture()
        end_id = "sample_v1_" + f"{3:024x}"
        observed = impacts[end_id]
        unknown_metric = MeasuredValue(
            observed.snapshot_id,
            None,
            "unknown_state_gap",
            "bounded-test-gap",
        )
        impacts[end_id] = replace(
            observed,
            continuity_state=UNKNOWN_AFTER_GAP,
            metrics=replace(observed.metrics, damaged_asn_count=unknown_metric),
            asn_impacts=(),
        )
        record = next(
            item
            for item in build_episode_as_records(
                _episode(3), samples, impacts, cohort=cohort, mapping=mapping
            )
            if item["asn"] == 65002
        )

        end_prefixes = record["address_families"]["ipv4"]["observation_end_prefixes"]
        cumulative = record["address_families"]["ipv4"]["cumulative_prefixes"]
        self.assertIsNone(end_prefixes["value"])
        self.assertEqual(end_prefixes["value_state"], "unknown_state_gap")
        self.assertIsNone(cumulative["value"])
        self.assertFalse(record["observation_end_member"])
        self.assertIsNone(record["recovered_at"])
        _validate_contract(record)

        peak_id = "sample_v1_" + f"{2:024x}"
        peak_observed = impacts[peak_id]
        peak_unknown_metric = replace(
            unknown_metric, snapshot_id=peak_observed.snapshot_id
        )
        impacts[peak_id] = replace(
            peak_observed,
            continuity_state=UNKNOWN_AFTER_GAP,
            metrics=replace(
                peak_observed.metrics, damaged_asn_count=peak_unknown_metric
            ),
            asn_impacts=(),
        )
        peak_unknown = next(
            item
            for item in build_episode_as_records(
                _episode(3), samples, impacts, cohort=cohort, mapping=mapping
            )
            if item["asn"] == 65002
        )
        self.assertEqual(peak_unknown["overall_classification"], "unknown")
        self.assertIsNone(
            peak_unknown["address_families"]["ipv4"]["lost_prefix_count_at_peak"]["value"]
        )
        self.assertIsNone(
            peak_unknown["address_families"]["ipv4"]["visibility"]["fully_invisible"]
        )
        _validate_contract(peak_unknown)

    def test_real_route_event_closes_raw_reference_and_missing_explicit_id_fails(self):
        mapping, cohort, samples, impacts = self._member_swap_fixture()
        event = _withdraw_event(100, "10.0.2.0/24", "2026-02-27T16:05:01Z")
        index = {event.route_event_id: event}
        changes = {(65002, "ipv4", "10.0.2.0/24"): (event.route_event_id,)}
        records = build_episode_as_records(
            _episode(3),
            samples,
            impacts,
            cohort=cohort,
            mapping=mapping,
            route_events_by_id=index,
            prefix_change_event_ids=changes,
        )
        record = next(item for item in records if item["asn"] == 65002)

        self.assertEqual(len(record["evidence_links"]), 1)
        link = record["evidence_links"][0]
        self.assertEqual(link["route_event_id"], event.route_event_id)
        self.assertEqual(link["artifact_id"], event.artifact_id)
        self.assertEqual(link["artifact_sha256"], event.file_sha256)
        self.assertRegex(link["raw_record_ref_id"], r"^raw_v1_[0-9a-f]{32}$")
        _validate_contract(record)

        with self.assertRaisesRegex(EpisodeAsBuildError, "缺少 RouteEvent/raw ref"):
            build_episode_as_records(
                _episode(3),
                samples,
                impacts,
                cohort=cohort,
                mapping=mapping,
                prefix_change_event_ids=changes,
            )

    def test_empty_evidence_is_allowed_and_stable_id_is_deterministic(self):
        mapping, cohort, samples, impacts = self._member_swap_fixture()
        first = build_episode_as_records(
            _episode(3), samples, impacts, cohort=cohort, mapping=mapping
        )
        second = build_episode_as_records(
            _episode(3), samples, impacts, cohort=cohort, mapping=mapping
        )

        self.assertEqual(first, second)
        self.assertTrue(all(record["evidence_links"] == [] for record in first))
        self.assertEqual(len({record["episode_as_id"] for record in first}), len(first))

    def test_moas_prefix_is_retained_for_each_origin_but_declared_non_additive(self):
        mapping = _mapping(65001, 65002)
        baseline = _seed(
            _entry(1, 65001, "10.0.9.0/24", "vp-moas-a"),
            _entry(2, 65002, "10.0.9.0/24", "vp-moas-b"),
        )
        first = _snapshot(0)
        second = _snapshot(1)
        cohort = build_country_cohort(baseline, (first, second), mapping)
        samples, impacts = _samples_and_impacts((first, second), cohort)
        records = build_episode_as_records(
            _episode(2), samples, impacts, cohort=cohort, mapping=mapping
        )

        self.assertEqual({record["asn"] for record in records}, {65001, 65002})
        for record in records:
            family = record["address_families"]["ipv4"]
            self.assertEqual(family["cumulative_prefixes"]["value"], ["10.0.9.0/24"])
            self.assertEqual(
                family["moas_semantics"],
                "origin_relationship_retained_not_additive",
            )
            _validate_contract(record)


if __name__ == "__main__":
    unittest.main()
