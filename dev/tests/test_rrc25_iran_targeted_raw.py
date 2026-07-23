import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from backend.data_pipeline.route_event import (
    AsPathSegment,
    ParsedRouteElement,
    artifact_id_v1,
)
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    build_research_route_event,
)
from backend.data_pipeline.research.rrc25_country_outage.update_adapter import (
    AdaptedUpdateRecord,
    RawRecordEvidence,
)
from dev.data_quality import rrc25_iran_targeted_raw as target


WINDOW_START = target._utc("2026-02-27T16:00:00Z", "fixture start")
WINDOW_END = target._utc("2026-03-06T08:40:00Z", "fixture end")
REQUEST_WINDOWS = (
    ("2026-02-27T22:30:00Z", "2026-02-27T22:45:00Z"),
    ("2026-02-28T10:35:00Z", "2026-02-28T11:00:00Z"),
    ("2026-02-28T14:20:00Z", "2026-02-28T14:45:00Z"),
)
PAIRS = (
    ("48715", "192.0.2.0/24", 4),
    ("42337", "198.51.100.0/24", 4),
    ("39501", "203.0.113.0/24", 4),
    ("61008", "2001:db8::/48", 6),
)


def parser_contract():
    semantic = {
        "schema_version": "rrc25-full-window-parser-attestation/v1",
        "backend": "native",
        "parser_name": "fixture-native",
        "parser_version": "fixture-v1",
        "binary_sha256": "1" * 64,
        "binary_execution_policy": "fixture-policy",
        "adapter_source_sha256": "2" * 64,
    }
    return {
        **semantic,
        "semantic_fingerprint_sha256": hashlib.sha256(
            target.canonical_json(semantic).encode("utf-8")
        ).hexdigest(),
    }


def parser_attestation():
    payload = {
        "schema_version": "parser_attestation_v1",
        "parser_name": "fixture-native",
        "parser_version": "fixture-v1",
        "parser_binary_sha256": "1" * 64,
        "adapter_source_sha256": "2" * 64,
        "binary_execution_policy": "fixture-policy",
    }
    return {
        **payload,
        "attestation_fingerprint_sha256": hashlib.sha256(
            target.canonical_json(
                {
                    "schema": "parser_attestation_fingerprint_v1",
                    "attestation": payload,
                }
            ).encode("utf-8")
        ).hexdigest(),
    }


def artifact(slot, index):
    digest = hashlib.sha256(slot.encode("ascii")).hexdigest()
    return {
        "artifact_id": artifact_id_v1(digest),
        "artifact_type": "update",
        "artifact_time_utc": slot,
        "collector_id": "rrc25",
        "relative_path": f"rrc25/updates.fixture.{index:04d}.gz",
        "file_sha256": digest,
        "size_bytes": index + 1,
        "compression": "gz",
    }


def selection():
    updates = []
    current = WINDOW_START
    index = 0
    while current < WINDOW_END:
        updates.append(
            artifact(current.strftime("%Y-%m-%dT%H:%M:%SZ"), index)
        )
        current += target.timedelta(minutes=5)
        index += 1
    return {
        "schema_version": "rrc25-country-outage-input-selection/v1",
        "status": "complete",
        "failures": [],
        "selection_id": "rsel_v1_" + "a" * 32,
        "semantic_fingerprint_sha256": "b" * 64,
        "window": {
            "start_utc": "2026-02-27T16:00:00Z",
            "end_exclusive_utc": "2026-03-06T08:40:00Z",
            "interval_semantics": "half_open",
            "granularity_seconds": 300,
        },
        "roles": {"analysis_updates": updates},
        "coverage": {
            "analysis_updates": {
                "expected_count": 1928,
                "observed_count": 1928,
                "missing_count": 0,
            }
        },
    }


def db_first():
    update_slots = []
    windows = []
    for index, (start_text, end_text) in enumerate(REQUEST_WINDOWS):
        start = target._utc(start_text, "fixture window start")
        end = target._utc(end_text, "fixture window end")
        current = start
        count = 0
        while current < end:
            update_slots.append(
                {
                    "utc": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "local": "fixture",
                }
            )
            current += target.timedelta(minutes=5)
            count += 1
        windows.append(
            {
                "name": f"window_{index}",
                "start_utc": start_text,
                "end_exclusive_utc": end_text,
                "start_local": "fixture",
                "end_exclusive_local": "fixture",
                "update_slot_count": count,
            }
        )
    entities = [
        {
            "asn": asn,
            "selected_prefix": prefix,
            "preferred_ip_family": family,
            "selection_state": "ready",
            "selection_reason_zh": "fixture",
        }
        for asn, prefix, family in PAIRS
    ]
    payload = {
        "schema_version": "rrc25-iran-db-first/v2",
        "scope": {
            "incident_ref": target.EXPECTED_INCIDENT_REF,
            "country_code": "IR",
            "source": "r",
            "relationship_state": "unresolved_not_causal",
            "window": dict(target.EXPECTED_WINDOW),
            "raw_read_performed": False,
            "database_write_performed": False,
            "backend_core_invoked": False,
        },
        "source_release": {
            "release_id": "fixture-release",
            "system_identifier": "fixture-system",
            "state_sha256": "3" * 64,
            "manifest_sha256": "4" * 64,
            "database_manifest_sha256": "5" * 64,
            "inventory_sha256": "6" * 64,
        },
        "fact": {},
        "country_series": {},
        "event_fact_reconciliation": {},
        "fact_bucket_analysis": {},
        "sparse_feature_auxiliary": {},
        "phase_analysis": {},
        "metric_findings": {},
        "recovery_candidate": {},
        "gap_matrix": {},
        "assessment": {},
        "execution": {"transaction_finalization": "rollback_completed"},
        "minimal_raw_request": {
            "status": "not_executed",
            "causal_claim_allowed": False,
            "critical_slots": [
                update_slots[1],
                update_slots[5],
                update_slots[10],
            ],
            "evidence_windows": windows,
            "update_slots": update_slots,
            "representative_entities": entities,
            "representative_asns": [row[0] for row in PAIRS],
            "representative_prefixes": [row[1] for row in PAIRS],
            "scope_limit": {
                "maximum_update_slot_count": 13,
                "full_window_replay": False,
                "all_asn_population": False,
                "only_key_slots_and_representative_entities": True,
                "initial_rib_read_requested": False,
            },
        },
    }
    return refingerprint(payload)


def refingerprint(payload):
    stable = {
        field: payload[field] for field in target.DB_FIRST_FINGERPRINT_FIELDS
    }
    payload["content_fingerprint_sha256"] = hashlib.sha256(
        target.canonical_json(
            {
                "schema": "rrc25_iran_db_first_content_fingerprint/v2",
                "stable_research_content": stable,
            }
        ).encode("utf-8")
    ).hexdigest()
    return payload


def built_plan():
    return target.build_plan(
        db_first(),
        selection(),
        native_parser_contract=parser_contract(),
        targeted_executor_ref={
            "path": "dev/data_quality/rrc25_iran_targeted_raw.py",
            "sha256": "7" * 64,
            "size_bytes": 300,
        },
        db_first_ref={
            "path": "iran-db-first.json",
            "sha256": "c" * 64,
            "size_bytes": 100,
        },
        preparation_ref={
            "path": "PREPARATION.json",
            "sha256": "d" * 64,
            "size_bytes": 200,
        },
    )


class FakeFactory:
    def __init__(self, artifact_row):
        self.artifact = artifact_row
        self.parser_attestation = parser_attestation()

    def __call__(self, artifact_row):
        if artifact_row != self.artifact:
            raise AssertionError("factory artifact binding drift")
        return artifact_row

    @property
    def statistics_by_artifact(self):
        return {
            self.artifact["artifact_id"]: {
                "status": "complete",
                "compressed_read_passes": 1,
                "compressed_file_sha256": self.artifact["file_sha256"],
                "compressed_size_bytes": self.artifact["size_bytes"],
                "physical_record_count": 1,
                "route_element_count": 1,
                "announce_count": 1,
                "withdraw_count": 0,
            }
        }


def fake_factory_builder(_root, artifacts, **_kwargs):
    if len(artifacts) != 1:
        raise AssertionError("每个 factory 必须只绑定一个 artifact")
    return FakeFactory(artifacts[0])


def fake_adapter(stream, *, artifact, route_element_retention_selector):
    slot_index = built_plan()["requested_update_slots"].index(
        artifact["artifact_time_utc"]
    )
    # 仅前两个代表实体分别产生 ANNOUNCE/WITHDRAW；另外两个必须显式零命中。
    if slot_index > 1:
        outside = ParsedRouteElement(
            event_time_utc=artifact["artifact_time_utc"],
            peer_ip="192.0.2.1",
            peer_asn=64500,
            action="announce",
            prefix="10.0.0.0/8",
            afi_safi="ipv4_unicast",
            as_path=(AsPathSegment("as_sequence", (64500, 64496)),),
        )
        if route_element_retention_selector((outside,)) != (False,):
            raise AssertionError("allowlist 外前缀不得保留")
        return ()
    asn, prefix, family = PAIRS[slot_index]
    action = "announce" if slot_index == 0 else "withdraw"
    element = ParsedRouteElement(
        event_time_utc=artifact["artifact_time_utc"],
        peer_ip="192.0.2.1",
        peer_asn=64500,
        action=action,
        prefix=prefix,
        afi_safi="ipv4_unicast" if family == 4 else "ipv6_unicast",
        as_path=(
            (AsPathSegment("as_sequence", (64500, int(asn))),)
            if action == "announce"
            else None
        ),
    )
    if route_element_retention_selector((element,)) != (True,):
        raise AssertionError("allowlist 前缀必须保留")
    event = build_research_route_event(
        artifact_id=artifact["artifact_id"],
        file_sha256=artifact["file_sha256"],
        collector_id=artifact["collector_id"],
        artifact_slot_utc=artifact["artifact_time_utc"],
        record_ordinal=0,
        element_ordinal=0,
        element=element,
    )
    raw_payload = f"raw-{slot_index}".encode("ascii")
    raw = RawRecordEvidence(
        artifact_id=artifact["artifact_id"],
        file_sha256=artifact["file_sha256"],
        collector_id=artifact["collector_id"],
        artifact_slot_utc=artifact["artifact_time_utc"],
        record_ordinal=0,
        record_offset=0,
        record_length=len(raw_payload),
        raw_record_sha256=hashlib.sha256(raw_payload).hexdigest(),
        event_time_utc=artifact["artifact_time_utc"],
        event_epoch_microseconds=0,
        mrt_type=16,
        mrt_subtype=4,
    )
    return (
        AdaptedUpdateRecord(
            record_kind="update",
            raw_record=raw,
            route_events=(event,),
            peer_session_observation=None,
            announce_count=action == "announce",
            withdraw_count=action == "withdraw",
        ),
    )


class TargetedRawTests(unittest.TestCase):
    def test_plan_is_exactly_four_pairs_and_thirteen_requested_slots(self):
        plan = built_plan()
        self.assertEqual(plan["evidence_scope"], "message_observation_only")
        self.assertEqual(len(plan["requested_pairs"]), 4)
        self.assertEqual(len(plan["requested_update_slots"]), 13)
        self.assertEqual(len(plan["selected_artifacts"]), 13)
        self.assertEqual(
            [row["artifact_time_utc"] for row in plan["selected_artifacts"]],
            plan["requested_update_slots"],
        )
        self.assertEqual(plan["limits"]["soft_runtime_seconds"], 540.0)
        self.assertEqual(
            plan["limits"]["selected_compressed_bytes"],
            sum(row["size_bytes"] for row in plan["selected_artifacts"]),
        )
        self.assertEqual(
            plan["limits"]["maximum_selected_compressed_bytes"],
            512 * 1024 * 1024,
        )
        policy = plan["execution_policy"]
        self.assertEqual(policy["database_connections"], 0)
        self.assertEqual(policy["rib_files_opened"], 0)
        self.assertFalse(policy["seed_performed"])
        self.assertFalse(policy["state_replay_performed"])
        self.assertFalse(policy["full_window_replay"])

    def test_plan_rejects_scope_expansion_and_pair_gap(self):
        payload = db_first()
        payload["minimal_raw_request"]["scope_limit"][
            "maximum_update_slot_count"
        ] = 14
        refingerprint(payload)
        with self.assertRaisesRegex(target.TargetedRawError, "13 槽"):
            target.build_plan(
                payload,
                selection(),
                native_parser_contract=parser_contract(),
                targeted_executor_ref={
                    "path": "targeted.py",
                    "sha256": "7" * 64,
                    "size_bytes": 1,
                },
            )

        payload = db_first()
        payload["minimal_raw_request"]["representative_entities"].pop()
        refingerprint(payload)
        with self.assertRaisesRegex(target.TargetedRawError, "4 个"):
            target.build_plan(
                payload,
                selection(),
                native_parser_contract=parser_contract(),
                targeted_executor_ref={
                    "path": "targeted.py",
                    "sha256": "7" * 64,
                    "size_bytes": 1,
                },
            )

        payload = db_first()
        payload["minimal_raw_request"]["update_slots"][0]["utc"] = (
            "2026-02-27T22:25:00Z"
        )
        refingerprint(payload)
        with self.assertRaisesRegex(target.TargetedRawError, "固定伊朗"):
            target.build_plan(
                payload,
                selection(),
                native_parser_contract=parser_contract(),
                targeted_executor_ref={
                    "path": "targeted.py",
                    "sha256": "7" * 64,
                    "size_bytes": 1,
                },
            )

    def test_worker_annotates_origin_withdraw_and_zero_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_root = Path(directory) / "raw"
            raw_root.mkdir()
            result = target.execute_targeted_scan(
                built_plan(),
                raw_root,
                factory_builder=fake_factory_builder,
                adapted_record_iterator=fake_adapter,
                clock=lambda: 1.0,
            )
        self.assertEqual(len(result["route_rows"]), 2)
        self.assertEqual(len(result["raw_rows"]), 2)
        announce, withdraw = result["route_rows"]
        self.assertEqual(
            announce["requested_pair_matches"][0]["status"],
            "matched_target_origin",
        )
        self.assertEqual(
            withdraw["origin_resolution"]["state"], "not_applicable"
        )
        self.assertEqual(
            withdraw["requested_pair_matches"][0]["status"],
            "not_applicable",
        )
        observations = result["parser_stats"]["entity_observations"]
        self.assertEqual(
            [row["observation_state"] for row in observations],
            [
                "message_observations_present",
                "message_observations_present",
                "no_observation_in_requested_window",
                "no_observation_in_requested_window",
            ],
        )
        self.assertTrue(all(row["window_expanded"] is False for row in observations))
        self.assertEqual(
            result["parser_stats"]["aggregate"][
                "selected_update_artifact_count"
            ],
            13,
        )
        self.assertEqual(
            result["parser_stats"]["aggregate"]["compressed_read_pass_count"],
            13,
        )

    def test_worker_enforces_540_second_soft_limit_before_raw_factory(self):
        values = iter((0.0, 541.0))
        factory_calls = []

        def forbidden_factory(*args, **kwargs):
            factory_calls.append((args, kwargs))
            raise AssertionError("超时后不得建立 raw factory")

        with tempfile.TemporaryDirectory() as directory:
            raw_root = Path(directory) / "raw"
            raw_root.mkdir()
            with self.assertRaisesRegex(target.TargetedRawError, "540 秒软限"):
                target.execute_targeted_scan(
                    built_plan(),
                    raw_root,
                    factory_builder=forbidden_factory,
                    clock=lambda: next(values),
                )
        self.assertEqual(factory_calls, [])

    def test_worker_rejects_runtime_parser_identity_drift_before_stream(self):
        stream_calls = []

        class DriftFactory(FakeFactory):
            def __init__(self, artifact_row):
                super().__init__(artifact_row)
                payload = dict(self.parser_attestation)
                payload.pop("attestation_fingerprint_sha256")
                payload["parser_binary_sha256"] = "9" * 64
                payload["attestation_fingerprint_sha256"] = hashlib.sha256(
                    target.canonical_json(
                        {
                            "schema": "parser_attestation_fingerprint_v1",
                            "attestation": payload,
                        }
                    ).encode("utf-8")
                ).hexdigest()
                self.parser_attestation = payload

            def __call__(self, artifact_row):
                stream_calls.append(artifact_row)
                return super().__call__(artifact_row)

        def drift_factory(_root, artifacts, **_kwargs):
            return DriftFactory(artifacts[0])

        with tempfile.TemporaryDirectory() as directory:
            raw_root = Path(directory) / "raw"
            raw_root.mkdir()
            with self.assertRaisesRegex(
                target.TargetedRawError,
                "定向 UPDATE artifact 扫描失败",
            ):
                target.execute_targeted_scan(
                    built_plan(),
                    raw_root,
                    factory_builder=drift_factory,
                    adapted_record_iterator=fake_adapter,
                    clock=lambda: 1.0,
                )
        self.assertEqual(stream_calls, [])

    def test_selector_canonicalizes_equivalent_ipv6_text_before_retention(self):
        selector_results = []

        def expanded_ipv6_adapter(
            _stream,
            *,
            artifact,
            route_element_retention_selector,
        ):
            element = ParsedRouteElement(
                event_time_utc=artifact["artifact_time_utc"],
                peer_ip="2001:db8::1",
                peer_asn=64500,
                action="withdraw",
                prefix="2001:0db8:0000:0000:0000:0000:0000:0000/48",
                afi_safi="ipv6_unicast",
                as_path=None,
            )
            selector_results.append(route_element_retention_selector((element,)))
            return ()

        with tempfile.TemporaryDirectory() as directory:
            raw_root = Path(directory) / "raw"
            raw_root.mkdir()
            target.execute_targeted_scan(
                built_plan(),
                raw_root,
                factory_builder=fake_factory_builder,
                adapted_record_iterator=expanded_ipv6_adapter,
                clock=lambda: 1.0,
            )
        self.assertEqual(selector_results, [(True,)] * 13)

    def test_create_only_package_contains_sidecar_checksums_and_zero_states(self):
        plan = built_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_root = root / "raw"
            prepared = root / "prepared"
            raw_root.mkdir()
            prepared.mkdir()
            result = target.execute_targeted_scan(
                plan,
                raw_root,
                factory_builder=fake_factory_builder,
                adapted_record_iterator=fake_adapter,
                clock=lambda: 1.0,
            )
            output = root / "published"
            published = target.publish_result(
                plan,
                result,
                output,
                raw_root=raw_root,
                prepared_directory=prepared,
            )
            self.assertEqual(len(published["files"]), 5)
            self.assertEqual(
                {path.name for path in output.iterdir()},
                {
                    "route-events.jsonl.gz",
                    "raw-record-refs.jsonl.gz",
                    "parser-stats.json",
                    "MANIFEST.json",
                    "SHA256SUMS",
                },
            )
            with gzip.open(output / "route-events.jsonl.gz", "rt") as stream:
                route_rows = [json.loads(line) for line in stream]
            self.assertEqual(len(route_rows), 2)
            manifest = json.loads(
                (output / "MANIFEST.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["evidence_scope"], "message_observation_only"
            )
            self.assertEqual(
                manifest["entity_observations"][2]["observation_state"],
                "no_observation_in_requested_window",
            )
            self.assertEqual(
                manifest["plan"]["request_bindings"]["targeted_executor_source"][
                    "sha256"
                ],
                "7" * 64,
            )
            self.assertEqual(
                manifest["plan"]["native_parser_contract"],
                parser_contract(),
            )
            self.assertEqual(
                result["parser_stats"]["artifact_stats"][0][
                    "runtime_parser_attestation"
                ],
                parser_attestation(),
            )
            checksums = (
                output / "SHA256SUMS"
            ).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(checksums), 4)
            with self.assertRaises(FileExistsError):
                target.publish_result(
                    plan,
                    result,
                    output,
                    raw_root=raw_root,
                    prepared_directory=prepared,
                )

    def test_script_has_no_database_or_state_replay_entrypoint(self):
        source = Path(target.__file__).read_text(encoding="utf-8")
        self.assertNotIn("psycopg", source)
        self.assertNotIn("connect_database", source)
        self.assertNotIn("replay_route", source)
        self.assertNotIn("state_seed_rib", source)


if __name__ == "__main__":
    unittest.main()
