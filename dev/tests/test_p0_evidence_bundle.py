import copy
import json
from pathlib import Path
import subprocess
import unittest

from backend.data_pipeline.evidence import (
    EvidenceBundleError,
    build_evidence_bundle_v2,
    canonical_evidence_bundle_bytes,
    evidence_id_v2,
    validate_reference_closure,
)
from backend.data_pipeline.metrics import build_metric_series
from backend.data_pipeline.route_event import artifact_id_v1, route_event_id_v1, vp_id_v1


ROOT = Path(__file__).resolve().parents[2]
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64

EVENT_DATA = {
    "hijack": {
        "source_table": "hijack_202603",
        "primary_key": {"source": "r", "prefix": "203.0.113.0/24", "hijack_eventid": 1},
        "problem": "203.0.113.0-24",
        "event_id": 1,
        "object_type": "prefix",
        "object_id": "203.0.113.0/24",
        "phases": ("observed_paths", "observed_paths", "not_retained"),
    },
    "sub_hijack": {
        "source_table": "sub_hijack_202603",
        "primary_key": {"source": "r", "prefix": "203.0.113.0/24", "sub_hijack_eventid": 2},
        "problem": "203.0.113.0-24",
        "event_id": 2,
        "object_type": "prefix",
        "object_id": "203.0.113.0/24",
        "phases": ("not_applicable", "not_applicable", "not_applicable"),
    },
    "leak": {
        "source_table": "leak_event_202603",
        "primary_key": {"source": "r", "prefix": "198.51.100.0/24", "leak_event_id": 3},
        "problem": "198.51.100.0-24",
        "event_id": 3,
        "object_type": "prefix",
        "object_id": "198.51.100.0/24",
        "phases": ("not_retained", "observed_paths", "not_retained"),
    },
    "prefix_outage": {
        "source_table": "prefix_outage_202603",
        "primary_key": {"source": "r", "prefix": "192.0.2.0/24", "outage_id": 4, "asn": "64504"},
        "problem": "192.0.2.0-24",
        "event_id": 4,
        "object_type": "prefix",
        "object_id": "192.0.2.0/24",
        "phases": ("observed_paths", "observed_no_path_in_snapshot", "not_retained"),
    },
    "as_outage": {
        "source_table": "as_outage_202603",
        "primary_key": {"source": "r", "asn": "64505", "outage_id": 5},
        "problem": "64505",
        "event_id": 5,
        "object_type": "asn",
        "object_id": "64505",
        "phases": ("observed_paths", "observed_no_path_in_snapshot", "not_retained"),
    },
    "country_outage": {
        "source_table": "country_outage_202603",
        "primary_key": {"source": "r", "country": "CN", "outage_id": 6},
        "problem": "CN",
        "event_id": 6,
        "object_type": "country",
        "object_id": "CN",
        "phases": ("not_applicable", "not_applicable", "not_applicable"),
    },
}


def program(name, version="1.0.0", digest=HASH_C):
    return {
        "name": name,
        "version": version,
        "code_sha256": digest,
        "config_sha256": HASH_A,
    }


def processing_lineage(raw=False):
    return {
        "parser": program("mrt-parser") if raw else None,
        "importer": program("route-event-importer") if raw else None,
        "detector": None,
        "normalizer": program("p0-incident-normalizer"),
        "bundle_generator": program("p0-evidence-bundle-generator", "2.0.0", HASH_D),
        "import_run_id": "run_v1_0123456789abcdef0123456789abcdef" if raw else None,
    }


def data_snapshot(raw_status="partial"):
    return {
        "profile_id": "fixed-feb-mar-2026",
        "profile_sha256": HASH_A,
        "window_start": "2026-02-01T00:00:00+08:00",
        "window_end_exclusive": "2026-04-01T00:00:00+08:00",
        "snapshot_time": "2026-03-31T23:59:59+08:00",
        "business_timezone": "Asia/Shanghai",
        "database_release_id": "p0-fixed-overlay-test",
        "overlay_inventory_sha256": HASH_B,
        "raw_source_status": raw_status,
    }


def phase(status):
    if status == "observed_paths":
        observations = [[64500, 64501], [64500, 64502]]
    elif status == "observed_no_path_in_snapshot":
        observations = []
    else:
        observations = None
    return {
        "source_field": "fixture_phase",
        "semantics": "route_observation_not_causal_trace",
        "supports_recovery": False,
        "status": status,
        "missing_reason": None if status.startswith("observed") else "fixture_missing",
        "observations": observations,
    }


def normalized_incident(event_type, *, suffix=None):
    config = EVENT_DATA[event_type]
    suffix = suffix or format(config["event_id"], "x") * 24
    start = "2026-03-12 10:0{}:00".format(config["event_id"])
    detail = "{}/{}/{}/{}/r".format(
        event_type, start, config["problem"], config["event_id"]
    )
    return {
        "schema_version": "p0_incident_normalization_v1",
        "incident_id": "inc_v1_" + suffix[:24],
        "incident_id_schema": "incident_id_v1",
        "event_type": event_type,
        "source_code": "r",
        "source_table": config["source_table"],
        "source_primary_key": copy.deepcopy(config["primary_key"]),
        "detail_reference": detail,
        "event_time_utc": "2026-03-12T02:0{}:00Z".format(config["event_id"]),
        "end_time_utc": None,
        "duration_seconds": None,
        "risk_level": None,
        "affected_objects": [
            {
                "object_type": config["object_type"],
                "object_id": config["object_id"],
                "role": "affected",
                "source_field": "detail_url.problem",
            }
        ],
        "collection_quality": [],
        "phase_coverage": {
            name: phase(status)
            for name, status in zip(("before", "during", "after"), config["phases"])
        },
        "fact_link_status": "matched",
        "field_quality": [
            {
                "field": "detector_version",
                "status": "not_retained",
                "missing_reason": "legacy_field_not_retained",
            }
        ],
        "collision_group_id": None,
        "quarantine_id": None,
        "detector_version": None,
        "classification": "observation_only",
        "causal_conclusion": None,
    }


def legacy_kwargs(**overrides):
    values = {
        "data_snapshot": data_snapshot(),
        "processing_lineage": processing_lineage(False),
        "raw_source_coverage": {"expected_count": 16992, "observed_count": 10272},
        "generated_at": "2026-07-20T09:00:00Z",
        "input_snapshot_sha256": HASH_B,
        "query_fingerprint_sha256": HASH_C,
        "source_hash_verification_status": "partial",
    }
    values.update(overrides)
    return values


def raw_record_ref():
    return {
        "raw_record_ref_id": "raw_v1_" + "1" * 32,
        "artifact_id": artifact_id_v1(HASH_A),
        "file_sha256": HASH_A,
        "record_offset": 4096,
        "record_length": 128,
        "record_hash": HASH_D,
        "record_ordinal": 42,
        "element_ordinal": 0,
        "collector_id": "rrc25",
        "vp_id": vp_id_v1("rrc25", "192.0.2.1", 64500),
        "vp_asn": 64500,
        "verification_status": "verified",
    }


def route_event_ref():
    return {
        "route_event_id": route_event_id_v1(HASH_A, 42, 0),
        "route_event_id_schema": "route_event_id_v1",
        "schema_version": "route_event_v1",
        "relation": "supports_observation",
        "semantics": "route_observation",
        "lineage_status": "raw_traceable",
        "observed_at": "2026-03-12T02:01:00Z",
        "collector_id": "rrc25",
        "vp_id": vp_id_v1("rrc25", "192.0.2.1", 64500),
        "vp_asn": 64500,
        "raw_record_ref_ids": ["raw_v1_" + "1" * 32],
        "phase": "during",
    }


def complete_route_event():
    return {
        "schema_version": "route_event_v1",
        "record_kind": "route_event",
        "route_event_id_schema": "route_event_id_v1",
        "route_event_id": route_event_id_v1(HASH_A, 42, 0),
        "source": "ripe_ris",
        "collector_id": "rrc25",
        "source_timezone": "UTC",
        "event_time_utc": "2026-03-12T02:01:00Z",
        "ingest_time_utc": "2026-07-20T08:00:00Z",
        "parse_time_utc": "2026-07-20T08:00:00Z",
        "vp_id": vp_id_v1("rrc25", "192.0.2.1", 64500),
        "vp_peer_ip": "192.0.2.1",
        "vp_asn": 64500,
        "action": "announce",
        "afi_safi": "ipv4_unicast",
        "prefix": "203.0.113.0/24",
        "as_path": {
            "semantics": "route_observation_path_snapshot",
            "causal_conclusion": None,
            "canonical": "64500 64496",
            "segments": [
                {"segment_type": "as_sequence", "asns": [64500, 64496]}
            ],
        },
        "origin_asn": 64496,
        "raw_ref": {
            "artifact_id": artifact_id_v1(HASH_A),
            "file_sha256": HASH_A,
            "record_ordinal": 42,
            "element_ordinal": 0,
            "record_offset": 4096,
            "record_length": 128,
            "record_hash": HASH_D,
        },
        "parser_name": "mrt-parser",
        "parser_version": "1.0.0",
        "importer_name": "route-event-importer",
        "importer_version": "1.0.0",
        "import_run_id": "run_v1_0123456789abcdef0123456789abcdef",
        "lineage_status": "raw_traceable",
        "quality_flags": [],
        "missing_reasons": [],
    }


def metric_series():
    return build_metric_series(
        "bgp_announce_record_count",
        subject={"subject_type": "global", "subject_id": "global", "display_name": None},
        collector_scope={
            "scope_kind": "collector_set",
            "collector_ids": ["rrc25"],
            "limitation_reason": None,
        },
        window_start="2026-03-12T02:00:00Z",
        window_end_exclusive="2026-03-12T02:05:00Z",
        source_available_slots=["2026-03-12T02:00:00Z"],
        processing_gap_slots=[],
        subject_rows=[{"time": "2026-03-12T02:00:00Z", "announ_num": 7}],
        source_refs=[
            {
                "source_layer": "derived_metric",
                "ref_id": "table:feature_country",
                "locator": "feature_country/source=r",
                "sha256": None,
            }
        ],
        generated_at="2026-07-20T09:00:00Z",
    )


def assert_schema_valid(test_case, payloads):
    ajv_module = ROOT / "frontend" / "node_modules" / "@redocly" / "ajv" / "dist" / "2020"
    schema_path = ROOT / "contracts" / "data" / "evidence-bundle-v2.schema.json"
    script = r"""
const fs = require('fs')
const Ajv2020 = require(process.argv[1]).default
const schema = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const ajv = new Ajv2020({allErrors: true, allowUnionTypes: true, strict: true, validateFormats: true})
ajv.addFormat('date-time', {
  type: 'string',
  validate: (value) => {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) return false
    const timestamp = Date.parse(value)
    return Number.isFinite(timestamp) && new Date(timestamp).toISOString().replace('.000Z', 'Z') === value
  },
})
const validate = ajv.compile(schema)
for (const payload of JSON.parse(fs.readFileSync(0, 'utf8'))) {
  if (!validate(payload)) {
    process.stderr.write(ajv.errorsText(validate.errors, {separator: '; '}))
    process.exit(1)
  }
}
"""
    result = subprocess.run(
        ["node", "-e", script, str(ajv_module), str(schema_path)],
        cwd=str(ROOT),
        input=json.dumps(payloads, ensure_ascii=False, allow_nan=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    test_case.assertEqual(result.returncode, 0, result.stderr)


class SixEventEvidenceBundleTest(unittest.TestCase):
    def test_all_six_incident_types_are_schema_valid_and_honest_legacy(self):
        bundles = [
            build_evidence_bundle_v2(normalized_incident(event_type), **legacy_kwargs())
            for event_type in EVENT_DATA
        ]
        self.assertEqual({item["incident"]["event_type"] for item in bundles}, set(EVENT_DATA))
        assert_schema_valid(self, bundles)
        for bundle in bundles:
            self.assertEqual(bundle["coverage_summary"]["admission_level"], "legacy_compatible")
            self.assertEqual(bundle["processing_lineage"]["lineage_status"], "legacy_untraceable")
            self.assertEqual(bundle["route_event_refs"], [])
            self.assertEqual(bundle["raw_record_refs"], [])
            self.assertEqual(bundle["conclusion"]["classification"], "observation_only")
            self.assertIsNone(bundle["conclusion"]["causal_conclusion"])
            validate_reference_closure(bundle)

    def test_leak_evidence_keeps_the_real_source_table_name(self):
        bundle = build_evidence_bundle_v2(normalized_incident("leak"), **legacy_kwargs())
        self.assertEqual(
            bundle["source_fact_mapping"]["source_facts"][0]["table_name"],
            "leak_event_202603",
        )

    def test_explicit_legacy_unknown_is_not_an_unknown_missing_reason(self):
        incident = normalized_incident("hijack")
        incident["phase_coverage"]["after"] = phase("legacy_unknown")
        bundle = build_evidence_bundle_v2(
            incident, **legacy_kwargs()
        )
        legacy_count = sum(
            item["count"]
            for item in bundle["coverage_summary"]["missing_counts"]
            if item["reason"] == "legacy_unknown"
        )
        self.assertGreater(legacy_count, 0)
        self.assertEqual(
            bundle["coverage_summary"]["unknown_missing_reason_count"], 0
        )


class MissingAndRegistrySemanticsTest(unittest.TestCase):
    def test_source_fact_collision_remains_visible_but_not_accepted(self):
        incident = normalized_incident("hijack")
        incident["fact_link_status"] = "legacy_collision"
        incident["collision_group_id"] = "lcg_v1_" + "6" * 32
        incident["phase_coverage"] = {
            name: phase("source_fact_collision")
            for name in ("before", "during", "after")
        }
        incident["field_quality"].append(
            {
                "field": "end_time_utc",
                "status": "source_fact_collision",
                "missing_reason": "fact_record_reused_by_multiple_incidents",
            }
        )
        other_incident = "inc_v1_" + "e" * 24
        bundle = build_evidence_bundle_v2(
            incident,
            **legacy_kwargs(
                collision_group={
                    "collision_group_id": incident["collision_group_id"],
                    "incident_ids": [other_incident, incident["incident_id"]],
                    "conflicted_fields": [
                        "/incident/end_time",
                        "/phase_coverage/after",
                    ],
                    "resolution_state": "unresolved",
                }
            ),
        )
        self.assertEqual(bundle["disposition"]["status"], "legacy_collision")
        self.assertEqual(bundle["coverage_summary"]["admission_level"], "not_accepted")
        self.assertEqual(bundle["coverage_summary"]["collision_group_count"], 1)
        self.assertTrue(
            all(
                value["status"] == "compromised"
                for value in bundle["phase_coverage"].values()
            )
        )
        self.assertIn("source_fact_collision", [item["code"] for item in bundle["limitations"]])
        assert_schema_valid(self, [bundle])
        validate_reference_closure(bundle)

    def test_unavailable_phases_are_not_available_with_reason_and_never_zero_filled(self):
        bundle = build_evidence_bundle_v2(
            normalized_incident("country_outage"), **legacy_kwargs()
        )
        for phase_name in ("before", "during", "after"):
            phase_value = bundle["phase_coverage"][phase_name]
            self.assertEqual(phase_value["status"], "not_available")
            self.assertEqual(phase_value["snapshot_count"], 0)
            self.assertEqual(phase_value["path_count"], 0)
            self.assertEqual(phase_value["missing_reasons"], ["not_applicable"])
        encoded = canonical_evidence_bundle_bytes(bundle).decode("utf-8")
        self.assertNotIn("supports_recovery", encoded)
        self.assertNotIn("phase_not_retained", bundle["processing_lineage"]["quality_flags"])
        self.assertNotIn("phase_not_retained", [item["code"] for item in bundle["limitations"]])

    def test_every_registry_source_and_cross_reference_resolves(self):
        bundle = build_evidence_bundle_v2(
            normalized_incident("hijack"),
            **legacy_kwargs(metric_series=[metric_series()]),
        )
        validate_reference_closure(bundle)
        self.assertEqual(len(bundle["metric_windows"]), 1)
        metric_id = bundle["metric_windows"][0]["metric_series_id"]
        self.assertTrue(
            any(metric_id in item["source_ref_ids"] for item in bundle["evidence_registry"])
        )
        broken = copy.deepcopy(bundle)
        broken["phase_coverage"]["before"]["evidence_ids"].append(
            "ev_v2_" + "f" * 32
        )
        with self.assertRaisesRegex(EvidenceBundleError, "Evidence ID"):
            validate_reference_closure(broken)

    def test_evidence_id_is_deterministic_and_phase_sensitive(self):
        first = evidence_id_v2(
            "inc_v1_" + "1" * 24,
            "path_snapshot",
            ["sf_v1_b", "sf_v1_a"],
            phase="before",
            field_paths=["/phase_coverage/before"],
        )
        repeated = evidence_id_v2(
            "inc_v1_" + "1" * 24,
            "path_snapshot",
            ["sf_v1_a", "sf_v1_b"],
            phase="before",
            field_paths=["/phase_coverage/before"],
        )
        after = evidence_id_v2(
            "inc_v1_" + "1" * 24,
            "path_snapshot",
            ["sf_v1_a", "sf_v1_b"],
            phase="after",
            field_paths=["/phase_coverage/after"],
        )
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, after)


class RawTraceabilityAndDeterminismTest(unittest.TestCase):
    def test_direct_d3_route_event_projection_is_schema_valid_and_closed(self):
        bundle = build_evidence_bundle_v2(
            normalized_incident("hijack"),
            **legacy_kwargs(
                processing_lineage=processing_lineage(True),
                raw_source_coverage={"expected_count": 1, "observed_count": 1},
                source_hash_verification_status="verified",
                route_event_records=[
                    {
                        "phase": "during",
                        "relation": "supports_observation",
                        "verification_status": "verified",
                        "route_event": complete_route_event(),
                    }
                ],
            ),
        )
        self.assertEqual(bundle["coverage_summary"]["admission_level"], "raw_traceable")
        self.assertEqual(
            bundle["route_event_refs"][0]["route_event_id"],
            route_event_id_v1(HASH_A, 42, 0),
        )
        self.assertRegex(bundle["raw_record_refs"][0]["raw_record_ref_id"], r"^raw_v1_[0-9a-f]{32}$")
        self.assertEqual(
            bundle["route_event_refs"][0]["raw_record_ref_ids"],
            [bundle["raw_record_refs"][0]["raw_record_ref_id"]],
        )
        assert_schema_valid(self, [bundle])
        validate_reference_closure(bundle)

    def test_raw_traceable_requires_verified_resolvable_raw_ref_and_versions(self):
        bundle = build_evidence_bundle_v2(
            normalized_incident("hijack"),
            **legacy_kwargs(
                processing_lineage=processing_lineage(True),
                raw_source_coverage={"expected_count": 1, "observed_count": 1},
                source_hash_verification_status="verified",
                route_event_refs=[route_event_ref()],
                raw_record_refs=[raw_record_ref()],
            ),
        )
        self.assertEqual(bundle["coverage_summary"]["admission_level"], "raw_traceable")
        self.assertEqual(bundle["processing_lineage"]["lineage_status"], "raw_traceable")
        self.assertEqual(
            bundle["phase_coverage"]["during"]["route_event_ref_ids"],
            [route_event_id_v1(HASH_A, 42, 0)],
        )
        assert_schema_valid(self, [bundle])
        validate_reference_closure(bundle)

        unresolved = route_event_ref()
        unresolved["raw_record_ref_ids"] = ["raw_v1_" + "f" * 32]
        with self.assertRaisesRegex(EvidenceBundleError, "\u672a\u89e3\u6790"):
            build_evidence_bundle_v2(
                normalized_incident("hijack"),
                **legacy_kwargs(
                    processing_lineage=processing_lineage(True),
                    source_hash_verification_status="verified",
                    route_event_refs=[unresolved],
                    raw_record_refs=[raw_record_ref()],
                ),
            )

    def test_legacy_input_downgrades_without_fabricating_raw_coordinates(self):
        bundle = build_evidence_bundle_v2(normalized_incident("as_outage"), **legacy_kwargs())
        self.assertEqual(bundle["coverage_summary"]["admission_level"], "legacy_compatible")
        self.assertEqual(bundle["route_event_refs"], [])
        self.assertEqual(bundle["raw_record_refs"], [])
        self.assertEqual(bundle["reproducibility"]["source_hash_verification_status"], "partial")

    def test_same_semantic_input_is_byte_identical_after_reordering(self):
        first = build_evidence_bundle_v2(
            normalized_incident("hijack"),
            **legacy_kwargs(metric_series=[metric_series()]),
        )
        second_incident = normalized_incident("hijack")
        second_incident["affected_objects"] = list(reversed(second_incident["affected_objects"]))
        repeated = build_evidence_bundle_v2(
            second_incident,
            **legacy_kwargs(metric_series=[metric_series()]),
        )
        self.assertEqual(canonical_evidence_bundle_bytes(first), canonical_evidence_bundle_bytes(repeated))
        self.assertEqual(first["bundle_id"], repeated["bundle_id"])
        self.assertEqual(
            first["reproducibility"]["output_sha256"],
            repeated["reproducibility"]["output_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
