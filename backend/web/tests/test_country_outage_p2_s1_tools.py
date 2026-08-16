from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from typing import Any, Mapping

from backend.services.country_outage_p2_s1_source_store import CountryOutageP2S1SourceStore, digest_json
from backend.services.country_outage_p2_s1_tools import CountryOutageP2S1Tools, ToolQueryError


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = REPO_ROOT / "contracts/data/country-outage-p2-s1"
STORE_ROOT = CONTRACT_ROOT / "test-fixture/source-store"
TOKEN_KEY = b"page-token-key-for-p2-s1-tests!!" * 2
RECEIPT_KEY = b"query-receipt-key-for-p2-s1-tests" * 2


def identity() -> dict[str, Any]:
    return {
        "incident_id": "incident_fixture_ir_1",
        "publication_id": "publication_fixture_ir_1",
        "publication_revision": 1,
        "publication_digest": "1" * 64,
        "collector_id": "rrc25",
        "cohort_id": "cohort_fixture_ir_1",
        "cohort_digest": "2" * 64,
        "window_start_utc": "2026-02-27T00:00:00Z",
        "window_end_utc": "2026-02-27T00:05:00Z",
        "data_through_utc": "2026-02-27T00:05:00Z",
        "finality": "event_end_unknown",
        "registry_snapshot_id": "registry-fixture-1",
        "registry_snapshot_digest": "3" * 64,
        "binding_generation": 1,
    }


class SpyStore(CountryOutageP2S1SourceStore):
    def __init__(self) -> None:
        super().__init__(STORE_ROOT, contract_root=CONTRACT_ROOT)
        self.population_calls: list[str] = []
        self.index_calls: list[str] = []

    def load_population(self, population_id: str) -> tuple[Mapping[str, Any], ...]:
        self.population_calls.append(population_id)
        return super().load_population(population_id)

    def load_index(self, population_id: str) -> Mapping[str, Any]:
        self.index_calls.append(population_id)
        return super().load_index(population_id)


class CountryOutageP2S1ToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SpyStore()
        self.tools = CountryOutageP2S1Tools(
            self.store,
            page_token_key=TOKEN_KEY,
            query_receipt_key=RECEIPT_KEY,
        )

    def request(self, **values: Any) -> dict[str, Any]:
        return {"identity": identity(), "page_size": 100, **values}

    def assert_error(self, code: str, call: Any) -> ToolQueryError:
        with self.assertRaises(ToolQueryError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_actual_request_result_and_failure_envelopes_validate_draft202012_schema(self) -> None:
        schema_path = (
            "contracts/agent/country-outage-p2-s1-implementation/"
            "w1-w2-tool-runtime.schema.json"
        )
        requests = {
            "tool07Request": self.request(asn=58224),
            "tool08Request": self.request(state_point_utc="2026-02-27T00:05:00Z"),
            "tool09Request": self.request(asn=58224),
            "tool10Request": self.request(
                first_observed_range_half_open={
                    "start_utc": "2026-02-27T00:00:00Z",
                    "end_utc": "2026-02-27T00:05:00Z",
                }
            ),
            "tool11Request": self.request(
                state_point_utc="2026-02-27T00:00:00Z", contains_asn=49666
            ),
            "tool12Request": self.request(contains_asn=49666),
        }
        results = {
            "tool07ResultPage": self.tools.query_fixed_cohort_members(requests["tool07Request"]),
            "tool08ResultPage": self.tools.query_prefix_states(requests["tool08Request"]),
            "tool09ResultPage": self.tools.query_as_states(requests["tool09Request"]),
            "tool10ResultPage": self.tools.query_new_prefix_states(requests["tool10Request"]),
            "tool11ResultPage": self.tools.query_materialized_route_states_at_time(
                requests["tool11Request"]
            ),
            "tool12ResultPage": self.tools.query_window_path_associations(requests["tool12Request"]),
        }
        failure = self.assert_error(
            "unsupported_filter",
            lambda: self.tools.query_window_path_associations(
                self.request(anchor_asn=64496, anchor_before_known_origin=True)
            ),
        ).as_dict()
        invalid_request = copy.deepcopy(requests["tool07Request"])
        invalid_request["hidden_join"] = True
        invalid_result = copy.deepcopy(results["tool07ResultPage"])
        invalid_result.pop("content_digest")
        invalid_path = copy.deepcopy(results["tool12ResultPage"])
        invalid_path["members"][0]["ordered_sequence_eligible"] = False
        invalid_exact_time = copy.deepcopy(results["tool11ResultPage"])
        invalid_exact_time["query_receipt"]["query_time_route_event_replay"] = True
        payload = {
            "requests": requests,
            "results": results,
            "failure": failure,
            "invalid": [
                ["tool07Request", invalid_request],
                ["tool07ResultPage", invalid_result],
                ["tool12ResultPage", invalid_path],
                ["tool11ResultPage", invalid_exact_time],
            ],
        }
        script = r'''
import json, sys
from jsonschema import Draft202012Validator, FormatChecker
schema=json.load(open(sys.argv[1]))
Draft202012Validator.check_schema(schema)
checker=FormatChecker()
root=Draft202012Validator(schema, format_checker=checker)
payload=json.load(sys.stdin)
errors=[]
for name, value in payload["requests"].items():
    validator=root.evolve(schema=schema["$defs"][name])
    errors.extend(f"{name}:{error.message}" for error in validator.iter_errors(value))
for name, value in payload["results"].items():
    validator=root.evolve(schema=schema["$defs"][name])
    errors.extend(f"{name}:{error.message}" for error in validator.iter_errors(value))
failure=root.evolve(schema=schema["$defs"]["toolFailureEnvelope"])
errors.extend(f"failure:{error.message}" for error in failure.iter_errors(payload["failure"]))
for name, value in payload["invalid"]:
    validator=root.evolve(schema=schema["$defs"][name])
    if not list(validator.iter_errors(value)):
        errors.append(f"invalid fixture accepted by {name}")
if errors:
    raise SystemExit("\n".join(errors))
'''
        completed = subprocess.run(
            [
                "uv", "run", "--with", "jsonschema[format-nongpl]==4.25.1",
                "python", "-c", script, schema_path,
            ],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)

    def test_tool07_reads_only_fixed_cohort_population_and_filters_materialized_fields(self) -> None:
        result = self.tools.query_fixed_cohort_members(self.request(asn=58224, prefix="109.74.224.0/20", afi=4))
        self.assertEqual(result["tool_id"], "TOOL-07")
        self.assertEqual(result["source_population_id"], "fixed_cohort_member_rows")
        self.assertEqual(result["returned_count"], 1)
        self.assertEqual(result["members"][0]["afi"], 4)
        self.assertEqual(result["members"][0]["country_origin_asns"], [58224])
        self.assertNotIn("classification", result["members"][0])
        self.assertEqual(self.store.population_calls, ["fixed_cohort_member_rows"])
        self.assertEqual(self.store.index_calls, ["fixed_cohort_member_rows"])
        self.assertEqual(
            result["query_receipt"]["filter_mode"],
            "verified_single_population_predicate_scan",
        )

    def test_tool08_exact_state_and_half_open_range_are_atomic_row_predicates(self) -> None:
        exact = self.tools.query_prefix_states(
            self.request(state_point_utc="2026-02-27T00:05:00Z", classification="partial")
        )
        self.assertEqual(exact["total_count"], 1)
        self.assertEqual(exact["members"][0]["classification"], "partial")
        ranged = self.tools.query_prefix_states(
            self.request(
                time_range_half_open={
                    "start_utc": "2026-02-27T00:00:00Z",
                    "end_utc": "2026-02-27T00:05:00Z",
                }
            )
        )
        self.assertEqual(ranged["total_count"], 1)
        self.assertEqual(ranged["members"][0]["state_point_utc"], "2026-02-27T00:00:00Z")

    def test_tool09_and_tool10_keep_distinct_fact_populations(self) -> None:
        as_state = self.tools.query_as_states(self.request(asn=58224, classification="affected"))
        self.assertEqual(as_state["total_count"], 1)
        self.assertIn("fixed_prefix_count", as_state["members"][0])
        self.assertNotIn("prefix", as_state["members"][0])
        new_prefix = self.tools.query_new_prefix_states(
            self.request(
                first_observed_range_half_open={
                    "start_utc": "2026-02-27T00:00:00Z",
                    "end_utc": "2026-02-27T00:05:00Z",
                },
                classification="partial",
            )
        )
        self.assertEqual(new_prefix["total_count"], 1)
        self.assertEqual(new_prefix["members"][0]["first_observed_at_utc"], "2026-02-27T00:00:00Z")
        self.assertNotIn("path_id", new_prefix["members"][0])

    def test_tool11_reads_one_exact_state_population_without_replay_or_second_population(self) -> None:
        result = self.tools.query_materialized_route_states_at_time(
            self.request(
                state_point_utc="2026-02-27T00:05:00Z",
                prefix="109.74.224.0/20",
                afi=4,
                route_observation_key="rrc25:vp-a:peer-a:109.74.224.0/20:ipv4",
                peer_asn_direction_id="rrc25:64500",
                vp_id="vp-a",
                peer_id="peer-a",
                visibility="visible",
                origin_asn=58224,
            )
        )
        self.assertEqual(result["tool_id"], "TOOL-11")
        self.assertEqual(result["source_population_id"], "materialized_route_state_rows_at_exact_time")
        self.assertEqual(result["total_count"], 1)
        member = result["members"][0]
        self.assertEqual(member["state_point_utc"], "2026-02-27T00:05:00Z")
        self.assertEqual(member["last_update_utc"], "2026-02-27T00:05:00Z")
        self.assertEqual(member["checkpoint_id"], "checkpoint_fixture_1")
        self.assertEqual(member["origin_asns"], [58224])
        self.assertEqual(member["origin_status"], "known_single")
        self.assertEqual(member["path_status"], "known_ordered")
        self.assertEqual(member["common_path_status"], "ordered")
        self.assertEqual(
            self.store.population_calls,
            ["materialized_route_state_rows_at_exact_time"],
        )
        self.assertEqual(
            self.store.index_calls,
            ["materialized_route_state_rows_at_exact_time"],
        )
        receipt = result["query_receipt"]
        self.assertEqual(receipt["exact_state_point_utc"], "2026-02-27T00:05:00Z")
        self.assertFalse(receipt["query_time_route_event_replay"])
        self.assertFalse(receipt["nearest_state_fill"])
        self.assertFalse(receipt["query_time_path_parsing"])
        self.assertTrue(self.tools.verify_query_receipt(receipt))

    def test_tool11_contains_asn_uses_same_population_native_index_and_closes_empty(self) -> None:
        matched = self.tools.query_materialized_route_states_at_time(
            self.request(state_point_utc="2026-02-27T00:00:00Z", contains_asn=49666)
        )
        self.assertEqual(matched["total_count"], 1)
        receipt = matched["query_receipt"]
        self.assertEqual(receipt["filter_mode"], "pre_materialized_native_index")
        self.assertEqual(receipt["tool_run_id"], matched["tool_run_id"])
        self.assertEqual(receipt["state_point_utc"], "2026-02-27T00:00:00Z")
        self.assertEqual(receipt["contains_asn"], 49666)
        self.assertEqual(receipt["total_count"], matched["total_count"])
        self.assertEqual(receipt["target_contains_asn"], 49666)
        self.assertEqual(
            receipt["path_asn_membership_profile_digest"],
            "28acec6edd232fd9aa38885175bcd715b9ea72f240efca6b3c5b7080394655e2",
        )
        self.assertEqual(receipt["path_asn_membership_index_digest"], receipt["source_index_digest"])
        self.assertEqual(
            receipt["path_asn_membership_materialization_receipt_digest"],
            receipt["source_materialization_receipt_digest"],
        )
        empty = self.tools.query_materialized_route_states_at_time(
            self.request(state_point_utc="2026-02-27T00:00:00Z", contains_asn=64496)
        )
        self.assertEqual(empty["members"], [])
        self.assertEqual(empty["set_completeness"], "complete")
        self.assertEqual(empty["query_receipt"]["disposition"], "complete_empty")

    def test_tool11_requires_exact_grid_point_and_never_fills_nearest_or_future_state(self) -> None:
        self.assert_error(
            "invalid_input",
            lambda: self.tools.query_materialized_route_states_at_time(self.request()),
        )
        self.assert_error(
            "invalid_input",
            lambda: self.tools.query_materialized_route_states_at_time(
                self.request(state_point_utc="2026-02-27T00:01:00Z")
            ),
        )
        before = self.tools.query_materialized_route_states_at_time(
            self.request(state_point_utc="2026-02-27T00:00:00Z")
        )
        self.assertEqual(before["total_count"], 1)
        self.assertEqual(before["members"][0]["checkpoint_id"], "checkpoint_fixture_0")
        self.assertNotEqual(before["members"][0]["checkpoint_id"], "checkpoint_fixture_1")
        self.assert_error(
            "invalid_input",
            lambda: self.tools.query_materialized_route_states_at_time(
                {
                    "identity": identity(),
                    "page_size": 100,
                    "state_point_utc": "2026-02-27T00:10:00Z",
                }
            ),
        )

    def test_tool11_query_receipt_hmac_binds_target_time_index_and_members(self) -> None:
        result = self.tools.query_materialized_route_states_at_time(
            self.request(state_point_utc="2026-02-27T00:00:00Z", contains_asn=49666)
        )
        receipt = result["query_receipt"]
        self.assertTrue(self.tools.verify_query_receipt(receipt))
        for field, forged in (
            ("exact_state_point_utc", "2026-02-27T00:05:00Z"),
            ("target_contains_asn", 58224),
            ("path_asn_membership_index_digest", "0" * 64),
            ("matched_member_keys_digest", "0" * 64),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(receipt)
                tampered[field] = forged
                self.assertFalse(self.tools.verify_query_receipt(tampered))

    def test_tool11_rejects_query_time_join_replay_and_nearest_state_controls(self) -> None:
        for forbidden in (
            "time_range_half_open",
            "checkpoint_id",
            "replay_route_events",
            "nearest_state",
            "window_path_association",
        ):
            with self.subTest(forbidden=forbidden):
                self.assert_error(
                    "unsupported_filter",
                    lambda forbidden=forbidden: self.tools.query_materialized_route_states_at_time(
                        self.request(state_point_utc="2026-02-27T00:00:00Z", **{forbidden: True})
                    ),
                )

    def test_tool11_index_content_profile_and_row_tamper_fail_before_receipt(self) -> None:
        attacks = (
            lambda store: store.load_index("materialized_route_state_rows_at_exact_time")[
                "secondary_indexes"
            ]["path_asn_membership"].__setitem__("profile_id", "FORGED"),
            lambda store: store.load_index("materialized_route_state_rows_at_exact_time")[
                "secondary_indexes"
            ]["path_asn_membership"]["members_by_asn"].__setitem__("49666", []),
            lambda store: store.load_population("materialized_route_state_rows_at_exact_time")[0].__setitem__(
                "path_canonicalization_profile_digest", "0" * 64
            ),
            lambda store: store.load_population("materialized_route_state_rows_at_exact_time")[0].__setitem__(
                "last_update_utc", "2026-02-27T00:05:00Z"
            ),
        )
        for attack in attacks:
            with self.subTest(attack=repr(attack)):
                store = SpyStore()
                tools = CountryOutageP2S1Tools(
                    store,
                    page_token_key=TOKEN_KEY,
                    query_receipt_key=RECEIPT_KEY,
                )
                store.verify()
                attack(store)
                error = self.assert_error(
                    "evidence_unclosed",
                    lambda: tools.query_materialized_route_states_at_time(
                        self.request(state_point_utc="2026-02-27T00:00:00Z", contains_asn=49666)
                    ),
                )
                self.assertIsNone(error.receipt)

    def test_tool11_rejects_native_index_member_without_target_asn_or_eligible_path(self) -> None:
        attacks = (
            lambda rows: rows[0].__setitem__(
                "path_segments", [{"segment_type": "as_sequence", "asns": [3257, 58224]}]
            ),
            lambda rows: rows[0].__setitem__("visibility", "invisible"),
            lambda rows: rows[0].__setitem__("common_path_status", "ambiguous"),
        )
        for attack in attacks:
            with self.subTest(attack=repr(attack)):
                store = SpyStore()
                tools = CountryOutageP2S1Tools(
                    store,
                    page_token_key=TOKEN_KEY,
                    query_receipt_key=RECEIPT_KEY,
                )
                rows = store.load_population("materialized_route_state_rows_at_exact_time")
                attack(rows)
                manifest = next(
                    item for item in store.manifest["population_manifests"]
                    if item["population_id"] == "materialized_route_state_rows_at_exact_time"
                )
                row_bytes = b"".join(
                    (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                    for row in rows
                )
                manifest["row_file"]["size_bytes"] = len(row_bytes)
                manifest["row_file"]["sha256"] = hashlib.sha256(row_bytes).hexdigest()
                error = self.assert_error(
                    "evidence_unclosed",
                    lambda: tools.query_materialized_route_states_at_time(
                        self.request(
                            state_point_utc="2026-02-27T00:00:00Z",
                            contains_asn=49666,
                        )
                    ),
                )
                self.assertIsNone(error.receipt)

    def test_tool11_path_at_time_semantics_do_not_claim_global_path_or_traffic(self) -> None:
        result = self.tools.query_materialized_route_states_at_time(
            self.request(state_point_utc="2026-02-27T00:00:00Z", contains_asn=49666)
        )
        self.assertIn(
            "path_at_time_is_collector_observation_not_global_reachability_or_traffic",
            result["limitations"],
        )
        self.assertIn(
            "active_path_requires_visible_and_ordered_or_unordered_common_path_status",
            result["limitations"],
        )
        self.assertEqual(
            result["query_receipt"]["state_time_semantics"],
            "after_all_legal_events_through_exact_state_point",
        )

    def test_stable_pagination_closes_without_duplicates(self) -> None:
        first = self.tools.query_prefix_states({"identity": identity(), "page_size": 1})
        self.assertEqual(first["set_completeness"], "partial_page")
        self.assertEqual(first["returned_count"], 1)
        self.assertEqual(first["total_count"], 2)
        self.assertIsNotNone(first["next_page_token"])
        second = self.tools.query_prefix_states(
            {"identity": identity(), "page_size": 1, "page_token": first["next_page_token"]}
        )
        self.assertEqual(second["set_completeness"], "partial_page")
        self.assertIsNone(second["next_page_token"])
        members = first["members"] + second["members"]
        self.assertEqual([member["state_point_utc"] for member in members], [
            "2026-02-27T00:00:00Z",
            "2026-02-27T00:05:00Z",
        ])
        self.assertEqual(len({member["evidence_ref"]["member_key"] for member in members}), 2)

    def test_page_token_tamper_and_cross_tool_reuse_fail_closed(self) -> None:
        first = self.tools.query_prefix_states({"identity": identity(), "page_size": 1})
        token = first["next_page_token"]
        assert token is not None
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        self.assert_error(
            "invalid_page_token",
            lambda: self.tools.query_prefix_states({"identity": identity(), "page_size": 1, "page_token": tampered}),
        )
        self.assert_error(
            "invalid_page_token",
            lambda: self.tools.query_as_states({"identity": identity(), "page_size": 1, "page_token": token}),
        )

    def test_page_token_binds_identity_query_and_page_size(self) -> None:
        first = self.tools.query_prefix_states({"identity": identity(), "page_size": 1})
        token = first["next_page_token"]
        assert token is not None
        changed_identity = identity()
        changed_identity["registry_snapshot_id"] = "registry-fixture-2"
        self.assert_error(
            "page_token_identity_mismatch",
            lambda: self.tools.query_prefix_states(
                {"identity": changed_identity, "page_size": 1, "page_token": token}
            ),
        )
        self.assert_error(
            "invalid_page_token",
            lambda: self.tools.query_prefix_states(
                {"identity": identity(), "page_size": 1, "page_token": token, "classification": "partial"}
            ),
        )
        self.assert_error(
            "invalid_page_token",
            lambda: self.tools.query_prefix_states(
                {"identity": identity(), "page_size": 2, "page_token": token}
            ),
        )

    def test_tool12_contains_asn_uses_verified_native_index(self) -> None:
        result = self.tools.query_window_path_associations(self.request(contains_asn=49666))
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["members"][0]["anchor_asn"], 49666)
        self.assertEqual(result["members"][0]["known_origin_asn"], 58224)
        self.assertEqual(result["members"][0]["observed_origin_asn"], 58224)
        self.assertEqual(result["members"][0]["path_parse_status"], "ordered")
        self.assertEqual(result["members"][0]["common_path_status"], "ordered")
        receipt = result["query_receipt"]
        self.assertEqual(receipt["filter_mode"], "pre_materialized_native_index")
        self.assertEqual(receipt["source_completeness"], "complete")
        self.assertEqual(
            receipt["complete_claim_label"],
            "complete_within_window_path_association_population",
        )
        self.assertEqual(receipt["target_contains_asn"], 49666)
        self.assertTrue(receipt["path_asn_membership_index_id"].startswith("window_path_asn_membership_v1_"))
        self.assertTrue(self.tools.verify_query_receipt(receipt))
        self.assertIn("source_native_path_status=known", receipt["source_row_invariants"])

    def test_tool12_contains_asn_zero_is_complete_empty(self) -> None:
        result = self.tools.query_window_path_associations(self.request(contains_asn=64496))
        self.assertEqual(result["members"], [])
        self.assertEqual(result["returned_count"], 0)
        self.assertEqual(result["total_count"], 0)
        self.assertEqual(result["set_completeness"], "complete")
        self.assertEqual(result["query_receipt"]["disposition"], "complete_empty")

    def test_tool12_noneligible_anchor_is_unsupported_with_trusted_failure_receipt(self) -> None:
        error = self.assert_error(
            "unsupported_filter",
            lambda: self.tools.query_window_path_associations(
                self.request(anchor_asn=64496, anchor_before_known_origin=True)
            ),
        )
        self.assertIsNotNone(error.receipt)
        assert error.receipt is not None
        self.assertEqual(error.receipt["disposition"], "unsupported_noneligible_anchor")
        self.assertFalse(error.receipt["anchor_population_eligible"])
        self.assertTrue(self.tools.verify_query_receipt(error.receipt))
        self.assertNotIn("result_set_id", error.as_dict())

    def test_tool12_eligible_anchor_with_additional_zero_match_is_complete_empty(self) -> None:
        result = self.tools.query_window_path_associations(
            self.request(anchor_asn=49666, anchor_before_known_origin=True, known_origin_asn=64496)
        )
        self.assertEqual(result["total_count"], 0)
        self.assertEqual(result["set_completeness"], "complete")
        self.assertTrue(result["query_receipt"]["anchor_population_eligible"])
        self.assertEqual(result["query_receipt"]["disposition"], "complete_empty")

    def test_tool12_anchor_and_contains_intersection_binds_all_native_receipt_fields(self) -> None:
        result = self.tools.query_window_path_associations(
            self.request(anchor_asn=49666, anchor_before_known_origin=True, contains_asn=48159)
        )
        receipt = result["query_receipt"]
        self.assertEqual(result["total_count"], 1)
        self.assertTrue(receipt["anchor_population_eligible"])
        self.assertEqual(receipt["target_anchor_asn"], 49666)
        self.assertEqual(receipt["target_contains_asn"], 48159)
        self.assertEqual(receipt["anchor_before_known_origin"], True)
        self.assertRegex(receipt["eligible_anchor_asns_digest"], r"^[0-9a-f]{64}$")
        self.assertRegex(receipt["path_association_index_digest"], r"^[0-9a-f]{64}$")
        self.assertRegex(receipt["path_association_materialization_receipt_digest"], r"^[0-9a-f]{64}$")

    def test_query_receipt_tamper_is_detected(self) -> None:
        result = self.tools.query_window_path_associations(self.request(contains_asn=49666))
        receipt = result["query_receipt"]
        self.assertTrue(self.tools.verify_query_receipt(receipt))
        receipt_semantic = copy.deepcopy(receipt)
        receipt_semantic.pop("receipt_auth_tag")
        receipt_digest = receipt_semantic.pop("receipt_digest")
        self.assertEqual(receipt_digest, digest_json(receipt_semantic))
        result_semantic = copy.deepcopy(result)
        content_digest = result_semantic.pop("content_digest")
        self.assertEqual(content_digest, digest_json(result_semantic))
        tampered = copy.deepcopy(receipt)
        tampered["matched_total_count"] = 999
        self.assertFalse(self.tools.verify_query_receipt(tampered))
        tampered = copy.deepcopy(receipt)
        tampered["receipt_auth_tag"] = "0" * 64
        self.assertFalse(self.tools.verify_query_receipt(tampered))

    def test_store_index_object_replacement_after_verification_fails_before_receipt(self) -> None:
        self.store.verify()
        index = self.store.load_index("window_path_association_evidence_rows")
        index["secondary_indexes"]["path_asn_membership"]["members_by_asn"]["49666"] = []
        self.assert_error(
            "evidence_unclosed",
            lambda: self.tools.query_window_path_associations(self.request(contains_asn=49666)),
        )

    def test_store_row_object_replacement_after_verification_fails_before_receipt(self) -> None:
        self.store.verify()
        row = self.store.load_population("window_path_association_evidence_rows")[0]
        row["known_origin_asn"] = 64496
        self.assert_error(
            "evidence_unclosed",
            lambda: self.tools.query_window_path_associations(self.request(contains_asn=49666)),
        )

    def test_deterministic_replay_preserves_ids_digests_and_receipts(self) -> None:
        request = self.request(contains_asn=49666)
        first = self.tools.query_window_path_associations(request)
        second = self.tools.query_window_path_associations(request)
        self.assertEqual(first["result_set_id"], second["result_set_id"])
        self.assertEqual(first["tool_run_id"], second["tool_run_id"])
        self.assertEqual(first["content_digest"], second["content_digest"])
        self.assertEqual(first["query_receipt"], second["query_receipt"])

    def test_identity_mismatch_and_stale_publication_are_distinct(self) -> None:
        wrong_collector = identity()
        wrong_collector["collector_id"] = "rrc26"
        self.assert_error(
            "identity_mismatch",
            lambda: self.tools.query_as_states({"identity": wrong_collector, "page_size": 10}),
        )
        wrong_publication = identity()
        wrong_publication["publication_id"] = "publication_fixture_ir_2"
        self.assert_error(
            "stale_publication",
            lambda: self.tools.query_as_states({"identity": wrong_publication, "page_size": 10}),
        )
        wrong_publication_digest = identity()
        wrong_publication_digest["publication_digest"] = "4" * 64
        self.assert_error(
            "stale_publication",
            lambda: self.tools.query_as_states({"identity": wrong_publication_digest, "page_size": 10}),
        )
        wrong_cohort_digest = identity()
        wrong_cohort_digest["cohort_digest"] = "5" * 64
        self.assert_error(
            "identity_mismatch",
            lambda: self.tools.query_as_states({"identity": wrong_cohort_digest, "page_size": 10}),
        )
        wrong_finality = identity()
        wrong_finality["finality"] = "event_end_known"
        self.assert_error(
            "identity_mismatch",
            lambda: self.tools.query_as_states({"identity": wrong_finality, "page_size": 10}),
        )

    def test_input_contract_rejects_wrong_shapes_ranges_and_silent_rounding(self) -> None:
        self.assert_error(
            "invalid_input",
            lambda: self.tools.query_prefix_states(self.request(state_point_utc="2026-02-27T00:01:00Z")),
        )
        self.assert_error(
            "invalid_input",
            lambda: self.tools.query_prefix_states(
                self.request(
                    state_point_utc="2026-02-27T00:05:00Z",
                    time_range_half_open={
                        "start_utc": "2026-02-27T00:00:00Z",
                        "end_utc": "2026-02-27T00:05:00Z",
                    },
                )
            ),
        )
        self.assert_error(
            "invalid_input",
            lambda: self.tools.query_window_path_associations(
                self.request(anchor_asn=49666, anchor_before_known_origin=True, contains_asn=True)
            ),
        )
        self.assert_error(
            "invalid_input",
            lambda: self.tools.query_fixed_cohort_members(self.request(prefix="109.74.224.1/20")),
        )
        self.assert_error(
            "invalid_input",
            lambda: self.tools.query_fixed_cohort_members(self.request(prefix="109.74.224.0/20", afi=6)),
        )
        self.assert_error(
            "invalid_input",
            lambda: self.tools.query_as_states({"identity": identity(), "page_size": True}),
        )
        self.assert_error(
            "invalid_input",
            lambda: self.tools.query_as_states(self.request(classification=["affected"])),
        )
        self.assert_error(
            "invalid_input",
            lambda: self.tools.query_prefix_states(
                self.request(
                    time_range_half_open={
                        "start_utc": "2026-02-27T00:00:00Z",
                        "end_utc": "2026-02-27T00:15:00Z",
                    }
                )
            ),
        )

    def test_unsupported_fields_false_anchor_and_deferred_tool_fail_closed(self) -> None:
        self.assert_error(
            "unsupported_filter",
            lambda: self.tools.query_as_states(self.request(rank=True)),
        )
        self.assert_error(
            "unsupported_filter",
            lambda: self.tools.query_window_path_associations(
                self.request(anchor_asn=49666, anchor_before_known_origin=False)
            ),
        )
        self.assert_error(
            "invalid_input",
            lambda: self.tools.query_window_path_associations(
                self.request(anchor_before_known_origin=True)
            ),
        )
        self.assert_error(
            "unsupported_filter",
            lambda: self.tools.execute("TOOL-13", self.request()),
        )
        self.assert_error(
            "invalid_input",
            lambda: self.tools.execute(["TOOL-09"], self.request()),
        )

    def test_hmac_keys_are_required_and_not_serialized_into_result(self) -> None:
        with self.assertRaises(ValueError):
            CountryOutageP2S1Tools(self.store, page_token_key=b"short")
        result = self.tools.query_prefix_states(self.request())
        serialized = repr(result)
        self.assertNotIn(TOKEN_KEY.decode("ascii"), serialized)
        self.assertNotIn(RECEIPT_KEY.decode("ascii"), serialized)
        self.assertEqual(len(bytes.fromhex(result["content_digest"])), hashlib.sha256().digest_size)


if __name__ == "__main__":
    unittest.main()
