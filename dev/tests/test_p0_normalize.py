import hashlib
import json
from pathlib import Path
import unittest

from backend.data_pipeline.normalize import (
    LocatorError,
    NormalizationError,
    build_collision_group,
    build_quarantine_record,
    business_time_to_utc,
    canonical_json,
    collision_group_id_v1,
    fact_source_primary_key,
    incident_id_v1,
    normalize_asn,
    normalize_collection,
    normalize_country_code,
    normalize_event,
    normalize_event_facts,
    normalize_phase,
    normalize_prefix,
    normalize_risk_level,
    parse_detail_url,
    quarantine_id_v1,
)


WINDOW = {
    "window_start": "2026-02-01T00:00:00+08:00",
    "window_end_exclusive": "2026-04-01T00:00:00+08:00",
}


def event(detail_url, event_type, source="r"):
    return {"detail_url": detail_url, "event_type": event_type, "source": source}


def six_type_fixture():
    events = [
        event("hijack/2026-03-01 00:00:00/10.0.0.0-24/1/r", "前缀劫持"),
        event("sub_hijack/2026-03-02 01:00:00/10.1.0.0-24/2/r", "子前缀劫持"),
        event("leak/2026-03-03 02:00:00/10.2.0.0-24/3/r", "路由泄漏"),
        event("prefix_outage/2026-03-04 03:00:00/10.3.0.0-24/4/r", "前缀中断"),
        event("as_outage/2026-03-05 04:00:00/AS64505/5/r", "AS中断"),
        event("country_outage/2026-03-06 05:00:00/us/6/r", "国家中断"),
    ]
    facts = {
        "hijack": [
            {
                "source_table": "hijack_202603",
                "source": "r",
                "prefix": "10.0.0.0/24",
                "hijack_eventid": 1,
                "hijacked_as": "AS64500",
                "s_time": "2026-03-01 00:00:00",
                "e_time": "2026-03-01 00:10:00",
                "duration": "00:10:00",
                "hijack_level": "high",
                "pre_vp_paths": {"2026-03-01 00:00:00": ["64500 64501"]},
                "eve_vp_paths": ["64502 64500"],
                "next_vp_paths": [],
            }
        ],
        "sub_hijack": [
            {
                "source_table": "sub_hijack_202603",
                "source": "r",
                "prefix": "10.1.0.0/24",
                "sub_hijack_eventid": 2,
                "hijacked_prefix": "10.0.0.0/16",
                "hijacked_as": "['64501', 'AS64502']",
                "s_time": "2026-03-02 01:00:00",
                "e_time": "2026-03-02 01:10:00",
                "duration": "00:10:00",
                "sub_hijack_level": "middle",
            }
        ],
        "leak": [
            {
                "source_table": "leak_event_202603",
                "source": "r",
                "prefix": "10.2.0.0/24",
                "leak_event_id": 3,
                "leak_to": "64503",
                "s_time": "2026-03-03 02:00:00",
                "leak_level": "low",
                "as_path": "['64503 64504 64505']",
            }
        ],
        "prefix_outage": [
            {
                "source_table": "prefix_outage_202603",
                "source": "r",
                "prefix": "10.3.0.0/24",
                "outage_id": 4,
                "asn": "AS64504",
                "s_time": "2026-03-04 03:00:00",
                "e_time": "2026-03-04 03:10:00",
                "duration": "00:10:00",
                "outage_level": "medium",
                "pre_vp_paths": ["64504 64505"],
                "eve_vp_paths": [],
                "next_vp_paths": ["64504 64505"],
            }
        ],
        "as_outage": [
            {
                "source_table": "as_outage_202603",
                "source": "r",
                "asn": "64505",
                "outage_id": 5,
                "s_time": "2026-03-05 04:00:00",
                "e_time": "2026-03-05 04:10:00",
                "duration": "00:10:00",
                "outage_level": "high",
                "outage_prefixes": ["10.5.0.0/24", "2001:db8:5::/48"],
                "pre_vp_paths": ["64505 64506"],
                "eve_vp_paths": [],
                "next_vp_paths": [],
            }
        ],
        "country_outage": [
            {
                "source_table": "country_outage_202603",
                "source": "r",
                "country": "US",
                "outage_id": 6,
                "s_time": "2026-03-06 05:00:00",
                "e_time": "2026-03-06 05:10:00",
                "duration": "00:10:00",
                "outage_level": "low",
                "outage_ases": [],
                "event_info": "国家级可见性观测",
            }
        ],
    }
    return events, facts


class IdentifierAndLocatorTest(unittest.TestCase):
    def test_incident_id_is_exactly_compatible_and_order_stable(self):
        identity = {
            "schema": "incident_id_v1",
            "event_type": "hijack",
            "start_time": "2026-03-04 19:35:43",
            "problem": "80.244.11.0-24",
            "event_id": 1,
            "source": "r",
        }
        expected_digest = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:24]
        identifier = incident_id_v1(
            "hijack", "2026-03-04 19:35:43", "80.244.11.0-24", "01", "r"
        )
        self.assertEqual(identifier, "inc_v1_" + expected_digest)
        self.assertEqual(identifier, "inc_v1_d3e1f3107353ce61e09526ec")
        self.assertEqual(canonical_json({"b": 2, "a": 1}), '{"a":1,"b":2}')

    def test_collision_and_quarantine_identities_are_versioned_and_stable(self):
        incident_ids = [
            "inc_v1_c8347970bf39542f28129526",
            "inc_v1_d3e1f3107353ce61e09526ec",
        ]
        primary_key = {
            "source": "r",
            "prefix": "80.244.11.0/24",
            "hijack_eventid": 1,
        }
        self.assertEqual(
            collision_group_id_v1("hijack_202603", primary_key, incident_ids),
            "lcg_v1_e716ada45b5accd6ab7d0db832ca9119",
        )
        self.assertEqual(
            collision_group_id_v1("hijack_202603", primary_key, reversed(incident_ids)),
            "lcg_v1_e716ada45b5accd6ab7d0db832ca9119",
        )
        quarantine_key = {"source": "r", "country": "", "outage_id": 1}
        reasons = ["invalid_identity", "legacy_window_contamination"]
        self.assertEqual(
            quarantine_id_v1("country_outage_202603", quarantine_key, reasons),
            "qr_v1_4067c5a513575065d7db240f4acc5470",
        )
        self.assertEqual(
            quarantine_id_v1("country_outage_202603", quarantine_key, reversed(reasons)),
            "qr_v1_4067c5a513575065d7db240f4acc5470",
        )

    def test_all_six_locator_shapes_are_strictly_parsed(self):
        cases = {
            "hijack": "hijack/2026-03-01 00:00:00/10.0.0.0-24/1/r",
            "sub_hijack": "sub_hijack/2026-03-01 00:00:00/10.0.1.0-24/1/r",
            "leak": "leak/2026-03-01 00:00:00/2001:db8::-32/1/r",
            "prefix_outage": "prefix_outage/2026-03-01 00:00:00/10.0.2.0-24/1/r",
            "as_outage": "as_outage/2026-03-01 00:00:00/AS64512/1/r",
            "country_outage": "country_outage/2026-03-01 00:00:00/cn/1/r",
        }
        for event_type, detail_url in cases.items():
            with self.subTest(event_type=event_type):
                locator = parse_detail_url(detail_url)
                self.assertEqual(locator["event_type"], event_type)
                self.assertEqual(locator["event_time_utc"], "2026-02-28T16:00:00Z")
                self.assertRegex(locator["incident_id"], r"^inc_v1_[0-9a-f]{24}$")
        prefix_outage = parse_detail_url(cases["prefix_outage"])
        self.assertEqual(
            prefix_outage["locator_risks"],
            ["native_key_component_not_in_detail_url:asn"],
        )

    def test_locator_rejects_malformed_or_misleading_values(self):
        invalid = (
            "hijack/2026-03-01 00:00:00/10.0.0.0-24/1",
            "hijack/2026-02-30 00:00:00/10.0.0.0-24/1/r",
            "hijack/2026-03-01 00:00:00/not-a-prefix/1/r",
            "as_outage/2026-03-01 00:00:00/ASx/1/r",
            "country_outage/2026-03-01 00:00:00//1/r",
        )
        for detail_url in invalid:
            with self.subTest(detail_url=detail_url):
                with self.assertRaises(LocatorError):
                    parse_detail_url(detail_url)


class PrimitiveNormalizationTest(unittest.TestCase):
    def test_time_entity_risk_and_native_key_normalization(self):
        self.assertEqual(business_time_to_utc("2026-03-01 08:00:00"), "2026-03-01T00:00:00Z")
        self.assertEqual(normalize_asn("AS00123"), "123")
        self.assertEqual(normalize_prefix("2001:0DB8::1-32"), "2001:db8::/32")
        self.assertEqual(normalize_country_code(" cn "), "CN")
        self.assertEqual(normalize_risk_level("medium"), "middle")
        self.assertIsNone(normalize_risk_level(""))
        key = fact_source_primary_key(
            "prefix_outage",
            {"source": "r", "prefix": "10.0.0.1/24", "outage_id": "7", "asn": "AS64512"},
        )
        self.assertEqual(
            key,
            {"source": "r", "prefix": "10.0.0.0/24", "outage_id": 7, "asn": "64512"},
        )
        with self.assertRaises(NormalizationError):
            normalize_country_code("")

    def test_native_asn_set_key_expands_to_real_affected_asns(self):
        detail_url = "as_outage/2026-03-14 01:02:26/{36040,211612}/1/r"
        locator = parse_detail_url(detail_url)
        self.assertEqual(locator["normalized_problem"], "{36040,211612}")
        fact = {
            "source_table": "as_outage_202603",
            "source": "r",
            "asn": "{36040,211612}",
            "outage_id": 1,
            "s_time": "2026-03-14 01:02:26",
            "e_time": "2026-03-14 01:05:26",
            "duration": "00:03:00",
            "outage_level": "high",
            "outage_prefixes": ["190.129.0.0/16"],
            "pre_vp_paths": [],
            "eve_vp_paths": [],
            "next_vp_paths": [],
        }
        self.assertEqual(
            fact_source_primary_key("as_outage", fact),
            {"source": "r", "asn": "{36040,211612}", "outage_id": 1},
        )
        incident = normalize_event(
            event(detail_url, "AS中断"),
            fact,
            {"source_table": "as_outage_202603"},
        )
        asn_ids = {
            item["object_id"]
            for item in incident["affected_objects"]
            if item["object_type"] == "asn"
        }
        self.assertEqual(asn_ids, {"36040", "211612"})
        self.assertNotIn("{36040,211612}", asn_ids)

        with self.assertRaises(NormalizationError):
            fact_source_primary_key("as_outage", {**fact, "asn": "{36040,bad}"})

    def test_missing_and_empty_collections_never_become_zero_or_recovery(self):
        missing = normalize_collection(None, "asn")
        empty = normalize_collection("[]", "asn")
        invalid = normalize_collection(["64512", "bad"], "asn")
        self.assertIsNone(missing["values"])
        self.assertEqual(missing["status"], "not_retained")
        self.assertEqual(empty["values"], [])
        self.assertEqual(empty["status"], "observed_empty")
        self.assertFalse(empty["supports_recovery"])
        self.assertEqual(invalid["status"], "legacy_unknown")
        self.assertEqual(invalid["values"], ["64512"])
        self.assertEqual(invalid["rejected_values"], ["bad"])

    def test_three_phase_statuses_keep_empty_snapshot_semantics(self):
        empty = normalize_phase(
            [], source_field="next_vp_paths", applicable=True, retained=True
        )
        missing = normalize_phase(
            None, source_field="pre_vp_paths", applicable=True, retained=False
        )
        not_applicable = normalize_phase(
            None, source_field=None, applicable=False, retained=False
        )
        collided = normalize_phase(
            ["64512"],
            source_field="eve_vp_paths",
            applicable=True,
            retained=True,
            collision=True,
        )
        self.assertEqual(empty["status"], "observed_no_path_in_snapshot")
        self.assertIsNone(empty["missing_reason"])
        self.assertFalse(empty["supports_recovery"])
        self.assertEqual(missing["status"], "not_retained")
        self.assertEqual(not_applicable["status"], "not_applicable")
        self.assertEqual(collided["status"], "source_fact_collision")
        self.assertTrue(collided["missing_reason"])


class SixTypeNormalizationTest(unittest.TestCase):
    def test_six_types_produce_json_serializable_matched_incidents(self):
        events, facts = six_type_fixture()
        result = normalize_event_facts(events, facts, **WINDOW)
        self.assertEqual(result["summary"]["incident_count"], 6)
        self.assertEqual(result["summary"]["matched_count"], 6)
        self.assertEqual(result["summary"]["quarantine_count"], 0)
        self.assertEqual(result["classification"], "observation_only")
        self.assertIsNone(result["causal_conclusion"])
        json.dumps(result, ensure_ascii=False, allow_nan=False)

        by_type = {item["event_type"]: item for item in result["incidents"]}
        self.assertEqual(by_type["hijack"]["duration_seconds"], 600)
        self.assertEqual(
            by_type["hijack"]["phase_coverage"]["after"]["status"],
            "observed_no_path_in_snapshot",
        )
        self.assertFalse(
            by_type["hijack"]["phase_coverage"]["after"]["supports_recovery"]
        )
        self.assertEqual(
            by_type["sub_hijack"]["phase_coverage"]["during"]["status"],
            "not_applicable",
        )
        self.assertEqual(
            by_type["leak"]["phase_coverage"]["before"]["status"], "not_retained"
        )
        self.assertEqual(
            by_type["leak"]["phase_coverage"]["during"]["status"], "observed_paths"
        )
        self.assertEqual(
            by_type["country_outage"]["collection_quality"][0]["status"],
            "observed_empty",
        )
        prefix_link = next(item for item in result["links"] if item["event_type"] == "prefix_outage")
        self.assertEqual(
            prefix_link["locator_risks"],
            ["native_key_component_not_in_detail_url:asn"],
        )

    def test_batch_rerun_is_byte_deterministic(self):
        events, facts = six_type_fixture()
        first = normalize_event_facts(events, facts, **WINDOW)
        second = normalize_event_facts(reversed(events), facts, **WINDOW)
        self.assertEqual(canonical_json(first), canonical_json(second))

    def test_streaming_normalize_event_uses_explicit_context(self):
        events, facts = six_type_fixture()
        incident = normalize_event(
            events[0], facts["hijack"][0], {"source_table": "hijack_202603"}
        )
        self.assertEqual(incident["fact_link_status"], "matched")
        self.assertEqual(incident["classification"], "observation_only")
        self.assertIsNone(incident["causal_conclusion"])
        with self.assertRaisesRegex(NormalizationError, "事实主键"):
            normalize_event(
                events[0],
                {**facts["hijack"][0], "prefix": "10.9.0.0/24"},
                {"source_table": "hijack_202603"},
            )


class DefectRoutingTest(unittest.TestCase):
    def test_generic_fact_reuse_builds_march_hijack_collision_group(self):
        events = [
            event(
                "hijack/2026-03-04 19:35:43/80.244.11.0-24/1/r", "前缀劫持"
            ),
            event(
                "hijack/2026-03-20 14:11:03/80.244.11.0-24/1/r", "前缀劫持"
            ),
        ]
        facts = {
            "hijack": [
                {
                    "source_table": "hijack_202603",
                    "source": "r",
                    "prefix": "80.244.11.0/24",
                    "hijack_eventid": 1,
                    "hijacked_as": "64512",
                    "s_time": "2026-03-04 19:35:43",
                    "e_time": "2026-03-20 14:11:03",
                    "duration": "15 days, 18:35:20",
                    "pre_vp_paths": ["64512 64513"],
                    "eve_vp_paths": [],
                    "next_vp_paths": ["64512 64513"],
                    "hijack_level": "high",
                }
            ]
        }
        result = normalize_event_facts(events, facts, **WINDOW)
        self.assertEqual(result["summary"]["collision_group_count"], 1)
        group = result["collision_groups"][0]
        self.assertRegex(group["collision_group_id"], r"^lcg_v1_[0-9a-f]{32}$")
        self.assertEqual(len(group["incident_ids"]), 2)
        self.assertEqual(
            {item["fact_link_status"] for item in result["incidents"]},
            {"legacy_collision"},
        )
        for incident in result["incidents"]:
            self.assertIsNone(incident["duration_seconds"])
            self.assertEqual(
                incident["phase_coverage"]["during"]["status"],
                "source_fact_collision",
            )
            self.assertIsNone(incident["causal_conclusion"])
        rebuilt = build_collision_group(
            source_table=group["source_table"],
            source_primary_key=group["source_primary_key"],
            incident_ids=reversed(group["incident_ids"]),
        )
        self.assertEqual(rebuilt, group)

    def test_empty_country_and_embedded_out_of_window_time_are_quarantined(self):
        fact = {
            "source_table": "country_outage_202603",
            "source": "r",
            "country": "",
            "outage_id": 1,
            "s_time": "2026-03-10 16:24:31",
            "event_info": "历史描述时间 2026-04-26 12:00:00",
        }
        first = normalize_event_facts([], {"country_outage": [fact]}, **WINDOW)
        second = normalize_event_facts([], {"country_outage": [fact]}, **WINDOW)
        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["quarantine_count"], 1)
        quarantined = first["quarantine"][0]
        self.assertRegex(quarantined["quarantine_id"], r"^qr_v1_[0-9a-f]{32}$")
        self.assertEqual(
            quarantined["reason_codes"],
            ["invalid_identity", "legacy_window_contamination"],
        )
        rebuilt = build_quarantine_record(
            source_table=quarantined["source_table"],
            source_primary_key=quarantined["source_primary_key"],
            reasons=reversed(quarantined["reason_codes"]),
            record_kind="fact_record",
            legacy_payload=fact,
            evidence=quarantined["evidence"],
        )
        self.assertEqual(rebuilt["quarantine_id"], quarantined["quarantine_id"])

    def test_prefix_outage_native_key_ambiguity_is_unresolved_not_first_row_wins(self):
        events = [
            event(
                "prefix_outage/2026-03-11 10:00:00/203.0.113.0-24/9/r",
                "前缀中断",
            )
        ]
        base = {
            "source_table": "prefix_outage_202603",
            "source": "r",
            "prefix": "203.0.113.0/24",
            "outage_id": 9,
            "s_time": "2026-03-11 10:00:00",
            "outage_level": "high",
        }
        facts = {"prefix_outage": [{**base, "asn": "64520"}, {**base, "asn": "64521"}]}
        result = normalize_event_facts(events, facts, **WINDOW)
        self.assertEqual(result["summary"]["unresolved_count"], 1)
        self.assertEqual(result["summary"]["quarantine_count"], 0)
        link = result["links"][0]
        self.assertEqual(link["status"], "unresolved")
        self.assertIsNone(link["matched_source_primary_key"])
        self.assertEqual(len(link["candidate_source_primary_keys"]), 2)
        self.assertEqual(
            link["locator_risks"], ["native_key_component_not_in_detail_url:asn"]
        )

    def test_single_fact_with_wrong_start_time_stays_unresolved(self):
        events, facts = six_type_fixture()
        facts["hijack"][0]["s_time"] = "2026-03-01 00:00:01"
        result = normalize_event_facts(events[:1], {"hijack": facts["hijack"]}, **WINDOW)
        self.assertEqual(result["summary"]["unresolved_count"], 1)
        self.assertEqual(result["links"][0]["issues"], ["fact_start_time_mismatch"])
        self.assertIsNone(result["links"][0]["matched_source_primary_key"])
        self.assertIsNone(result["incidents"][0]["duration_seconds"])

    def test_fact_end_before_incident_start_is_not_exposed_as_valid_time(self):
        events, facts = six_type_fixture()
        facts["prefix_outage"][0]["e_time"] = "2026-03-03 03:10:00"
        facts["prefix_outage"][0]["duration"] = "23:50:00"
        result = normalize_event_facts(
            events[3:4], {"prefix_outage": facts["prefix_outage"]}, **WINDOW
        )
        incident = result["incidents"][0]
        self.assertIsNone(incident["end_time_utc"])
        self.assertIsNone(incident["duration_seconds"])
        quality = {
            row["field"]: (row["status"], row["missing_reason"])
            for row in incident["field_quality"]
            if row["field"] in {"end_time_utc", "duration_seconds"}
        }
        self.assertEqual(
            quality,
            {
                "end_time_utc": (
                    "legacy_unknown",
                    "end_before_start",
                ),
                "duration_seconds": (
                    "legacy_unknown",
                    "end_before_start",
                ),
            },
        )

    def test_as_outage_end_before_start_uses_the_same_missing_semantics(self):
        events, facts = six_type_fixture()
        facts["as_outage"][0]["e_time"] = "2026-03-04 04:10:00"
        facts["as_outage"][0]["duration"] = "23:50:00"
        result = normalize_event_facts(
            events[4:5], {"as_outage": facts["as_outage"]}, **WINDOW
        )
        incident = result["incidents"][0]
        self.assertIsNone(incident["end_time_utc"])
        self.assertIsNone(incident["duration_seconds"])
        self.assertEqual(
            {
                row["field"]: (row["status"], row["missing_reason"])
                for row in incident["field_quality"]
                if row["field"] in {"end_time_utc", "duration_seconds"}
            },
            {
                "end_time_utc": ("legacy_unknown", "end_before_start"),
                "duration_seconds": ("legacy_unknown", "end_before_start"),
            },
        )

    def test_event_row_time_mismatch_is_quarantined(self):
        events, facts = six_type_fixture()
        events[0]["s_time"] = "2026-03-01 00:00:01"
        result = normalize_event_facts(events[:1], {"hijack": facts["hijack"]}, **WINDOW)
        self.assertEqual(result["summary"]["incident_count"], 0)
        self.assertEqual(result["summary"]["quarantine_count"], 2)
        event_quarantine = next(
            item for item in result["quarantine"] if item["record_kind"] == "event_reference"
        )
        self.assertEqual(event_quarantine["reason_codes"], ["reference_time_mismatch"])


class BoundaryTest(unittest.TestCase):
    def test_module_has_no_database_or_core_dependency(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "backend"
            / "data_pipeline"
            / "normalize"
            / "facts.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("psycopg2", source)
        self.assertNotIn("backend.core", source)
        self.assertNotIn("config.database", source)


if __name__ == "__main__":
    unittest.main()
