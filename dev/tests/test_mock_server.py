import importlib.util
from pathlib import Path
import unittest


MOCK_SERVER_PATH = Path(__file__).resolve().parents[1] / "mock_server.py"
SPEC = importlib.util.spec_from_file_location("domeye_mock_server", MOCK_SERVER_PATH)
MOCK_SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOCK_SERVER)


class MockFilteringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = MOCK_SERVER.load_fixture()

    def test_event_query_uses_requested_date_range(self):
        payload = MOCK_SERVER.payload_for(
            "/api/v1/events",
            {"date": ["2026-03-24_2026-03-31"], "page_num": ["1"], "page_size": ["10"]},
            self.fixture,
        )
        self.assertEqual(payload["record_count"], "6")
        self.assertTrue(all(item["start_time"].startswith("2026-03-31") for item in payload["data"]))

    def test_event_query_applies_frontend_filters_and_sorting(self):
        payload = MOCK_SERVER.payload_for(
            "/api/v1/events",
            {
                "date": ["2026-03-24_2026-03-31"],
                "event_type": ["前缀劫持"],
                "level": ["high"],
                "country": ["domestic"],
                "event_info": ["64513"],
                "sort_mode": ["start_timeB"],
            },
            self.fixture,
        )
        self.assertEqual(payload["record_count"], "1")
        self.assertEqual(payload["data"][0]["event_type"], "前缀劫持")

        foreign = MOCK_SERVER.payload_for(
            "/api/v1/events",
            {"date": ["2026-03-31_2026-03-31"], "country": ["foreign"]},
            self.fixture,
        )
        self.assertEqual(foreign["record_count"], "2")

    def test_feature_query_excludes_data_outside_requested_range(self):
        payload = MOCK_SERVER.payload_for(
            "/api/v1/features/top",
            {
                "start_time": ["2026-03-30 23:59:00"],
                "end_time": ["2026-03-31 23:59:59"],
            },
            self.fixture,
        )
        self.assertEqual(len(payload), 4)
        self.assertTrue(all(item["t"].startswith("2026-03-31") for item in payload))

    def test_country_and_as_feature_pages_match_real_nested_contracts(self):
        countries = MOCK_SERVER.payload_for(
            "/api/v1/features/countries",
            {
                "country": ["中"],
                "start_time": ["2026-02-01 00:00:00"],
                "end_time": ["2026-03-31 23:59:59"],
                "page_num": ["1"],
                "page_size": ["5"],
            },
            self.fixture,
        )
        self.assertEqual(countries["record_count"], 1)
        self.assertEqual(countries["data"][0]["country"], "中国")
        self.assertIn("time", countries["data"][0]["time_series_data"][0])

        ases = MOCK_SERVER.payload_for(
            "/api/v1/features/ases",
            {
                "asn": ["AS4134"],
                "country": ["中国"],
                "start_time": ["2026-02-01 00:00:00"],
                "end_time": ["2026-03-31 23:59:59"],
            },
            self.fixture,
        )
        self.assertEqual(ases["record_count"], 1)
        self.assertEqual(ases["data"][0]["asn"], "4134")
        self.assertEqual(ases["data"][0]["org_name"], "中国电信集团")

    def test_country_workbench_and_event_country_filter_share_target(self):
        query = {
            "country": ["中国"],
            "start_time": ["2026-03-30 23:59:59"],
            "end_time": ["2026-03-31 23:59:59"],
        }
        overview = MOCK_SERVER.payload_for(
            "/api/v1/features/countries/overview",
            query,
            self.fixture,
        )
        self.assertEqual(overview["selected_country"]["country"], "中国")
        self.assertGreater(overview["selected_country"]["update_total"], 0)
        self.assertTrue(overview["update_rankings"])

        events = MOCK_SERVER.payload_for(
            "/api/v1/events",
            {"date": ["2026-03-31_2026-03-31"], "attacked_country": ["中国"]},
            self.fixture,
        )
        self.assertTrue(events["data"])
        self.assertTrue(all(row["attacked_country"] == "中国" for row in events["data"]))

    def test_asn_workbench_and_event_asn_filter_share_target(self):
        overview = MOCK_SERVER.payload_for(
            "/api/v1/features/ases/overview",
            {
                "asn": ["AS4134"],
                "start_time": ["2026-03-30 23:59:59"],
                "end_time": ["2026-03-31 23:59:59"],
            },
            self.fixture,
        )
        self.assertEqual(overview["scope_kind"], "operational_asn_cohort")
        self.assertEqual(overview["candidate_pool_size"], 1000)
        self.assertEqual(overview["selected_asn"]["asn"], "4134")

        events = MOCK_SERVER.payload_for(
            "/api/v1/events",
            {"date": ["2026-03-31_2026-03-31"], "attacked_as": ["AS64512"]},
            self.fixture,
        )
        self.assertTrue(events["data"])
        self.assertTrue(all(row["attacked_as"] == "64512" for row in events["data"]))
        self.assertTrue(all("semantic_guardrails" in row for row in events["data"]))

    def test_each_event_kind_returns_its_own_detail_shape(self):
        expected_fields = {
            "hijack": "hijacked_prefix",
            "sub_hijack": "hijacker_prefix",
            "leak": "leak_prefix",
            "prefix_outage": "outage_prefix",
            "as_outage": "outage_as",
            "country_outage": "outage_country",
        }
        events = {row["detail_url"].split("/", 1)[0]: row for row in self.fixture["events"]["data"]}
        for event_kind, field in expected_fields.items():
            detail = MOCK_SERVER.payload_for(
                "/api/v1/{}".format(events[event_kind]["detail_url"]),
                {},
                self.fixture,
            )
            self.assertIn(field, detail)
            self.assertEqual(detail["start_time"], events[event_kind]["start_time"])
            self.assertEqual(
                detail["semantic_guardrails"]["contract_version"],
                "legacy_event_semantic_guardrails_v1",
            )
        self.assertIsInstance(
            self.fixture["event_details"]["hijack"]["pre_vp_paths"],
            dict,
        )
        self.assertIsInstance(self.fixture["event_details"]["leak"]["as_path"], str)

    def test_event_evidence_bundle_preserves_phase_and_non_causal_semantics(self):
        event = next(row for row in self.fixture["events"]["data"] if row["event_type"] == "前缀劫持")
        path = "/api/v1/events/evidence-bundle/{}".format(event["detail_url"])

        first = MOCK_SERVER.payload_for(path, {}, self.fixture)
        second = MOCK_SERVER.payload_for(path, {}, self.fixture)

        self.assertEqual(first["bundle_version"], "evidence_bundle_v1")
        self.assertEqual(first["incident_id"], second["incident_id"])
        self.assertEqual(first["incident_id_schema"], "incident_id_v1")
        self.assertIn(first["phase_coverage"]["before"]["status"], ("observed_paths", "not_available"))
        self.assertEqual(first["assessment"]["classification"], "observation_only")
        self.assertIsNone(first["assessment"]["causal_conclusion"])
        self.assertEqual(
            first["semantic_guardrails"],
            first["fact_record"]["semantic_guardrails"],
        )
        fact_record_identity = dict(first["fact_record"])
        fact_record_identity.pop("semantic_guardrails")
        fact_item = next(
            item for item in first["evidence_items"]
            if item["kind"] == "fact_record"
        )
        self.assertEqual(
            fact_item["evidence_id"],
            MOCK_SERVER._stable_identifier("ev_v1_", {
                "incident_id": first["incident_id"],
                "source_record": first["source_record"],
                "fact_record": fact_record_identity,
            }),
        )
        self.assertEqual(fact_item["field_count"], len(fact_record_identity))
        route_items = [item for item in first["evidence_items"] if item["kind"] == "route_observation"]
        self.assertTrue(route_items)
        self.assertTrue(all(
            item["semantics"] == "route_observation_not_causal_trace"
            for item in route_items
        ))
        self.assertFalse(first["data_quality"]["vantage_point_identity_available"])
        self.assertFalse(first["data_quality"]["raw_bgp_message_available"])

    def test_legacy_leak_list_does_not_publish_lifecycle(self):
        events = MOCK_SERVER.payload_for("/api/v1/events", {}, self.fixture)
        leak = next(row for row in events["data"] if row["event_type"] == "路由泄漏")

        self.assertEqual(leak["end_time"], "-")
        self.assertEqual(
            leak["semantic_guardrails"]["lifecycle_state"],
            "unavailable",
        )
        self.assertIn(
            "ongoing_state",
            leak["semantic_guardrails"]["blocked_claims"],
        )

    def test_query_after_development_window_is_empty(self):
        payload = MOCK_SERVER.payload_for(
            "/api/v1/features/top",
            {
                "start_time": ["2026-04-01 00:00:00"],
                "end_time": ["2026-04-02 00:00:00"],
            },
            self.fixture,
        )
        self.assertEqual(payload, [])

    def test_time_parser_rejects_invalid_or_imprecise_values(self):
        self.assertIsNone(MOCK_SERVER._parse_time("2026-02-30 00:00:00"))
        self.assertIsNone(MOCK_SERVER._parse_time("2026-03-31 23:59"))
        self.assertIsNone(MOCK_SERVER._parse_time("2026-03-31 24:00:00"))
        self.assertEqual(
            MOCK_SERVER._parse_time("2026-03-31T23:59:59"),
            MOCK_SERVER._parse_time("2026-03-31 23:59:59"),
        )
        payload = MOCK_SERVER.payload_for(
            "/api/v1/features/top",
            {
                "start_time": ["2026-02-30 00:00:00"],
                "end_time": ["2026-03-31 23:59:59"],
            },
            self.fixture,
        )
        self.assertEqual(payload, [])

    def test_empty_scenario_keeps_health_contract_and_event_count_type(self):
        self.assertIsNone(MOCK_SERVER.empty_payload("/api/v1/healthz"))
        health = MOCK_SERVER.payload_for("/api/v1/healthz", {}, self.fixture)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(
            MOCK_SERVER.empty_payload("/api/v1/events"),
            {"data": [], "total_page": 0, "record_count": "0"},
        )


if __name__ == "__main__":
    unittest.main()
