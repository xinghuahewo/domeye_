from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

from backend.data_pipeline.research.rrc25_country_outage.profile import (
    validate_research_profile,
)
from backend.data_pipeline.research.rrc25_country_outage.source_fact import (
    load_frozen_incident_fact,
)
from dev.data_quality import rrc25_iran_db_proxy_finalize as finalize


ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "config" / "research" / "iran-rrc25-202602.json"
INVENTORY_PATH = (
    ROOT / "config" / "research" / "iran-rrc25-report-claims.json"
)
SOURCE_FACT_PATH = (
    ROOT
    / "config"
    / "research"
    / "iran-country-outage-source-fact-20260227.json"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(value) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value) -> None:
    path.write_bytes(_json_bytes(value))


def _profile():
    return validate_research_profile(
        json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    )


def _source_fact():
    payload = json.loads(SOURCE_FACT_PATH.read_text(encoding="utf-8"))
    return payload, load_frozen_incident_fact(payload)


def _points(values):
    start = datetime(2026, 2, 27, 16, 0, tzinfo=timezone.utc)
    rows = []
    for index, value in enumerate(values):
        observed = start + timedelta(minutes=5 * index)
        rows.append(
            {
                "announce_count": 10,
                "withdraw_count": 1,
                "ipv4_24_equivalent": int(value // 256),
                "ipv4_address_equivalent": value,
                "ipv6_48_equivalent": 200_000,
                "missing_reason": None,
                "observed_at_local": observed.astimezone(
                    timezone(timedelta(hours=8))
                ).isoformat(timespec="seconds"),
                "value_state": "observed",
            }
        )
    return rows


def _db_first_payload(frozen_fact, values):
    points = _points(values)
    raw_slots = [
        {
            "utc": (
                datetime(2026, 2, 27, 22, 30, tzinfo=timezone.utc)
                + timedelta(minutes=5 * index)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        for index in range(13)
    ]
    payload = {
        "schema_version": finalize.DB_FIRST_SCHEMA_VERSION,
        "generated_at_utc": "2026-07-23T00:00:00Z",
        "scope": {
            "incident_ref": finalize.EXPECTED_INCIDENT_REF,
            "country_code": "IR",
            "country_name_zh": "伊朗",
            "raw_read_performed": False,
            "database_write_performed": False,
            "window": {
                "start_utc": "2026-02-27T16:00:00Z",
                "end_exclusive_utc": "2026-03-06T08:40:00Z",
                "start_local": "2026-02-28T00:00:00+08:00",
                "end_exclusive_local": "2026-03-06T16:40:00+08:00",
                "granularity_seconds": 300,
                "expected_slot_count": 1928,
                "semantics": "half_open",
            },
        },
        "source_release": {"release_id": "fixture-release"},
        "fact": {
            "incident_ref": finalize.EXPECTED_INCIDENT_REF,
            "affected_asn_count": frozen_fact.legacy_affected_asn_count,
            "total_asn_count": frozen_fact.legacy_total_asn_count,
            "affected_asns": list(frozen_fact.affected_asns),
        },
        "country_series": {
            "source": "feature_country",
            "granularity_seconds": 300,
            "window_semantics": "half_open",
            "metric_semantics": {
                "ipv4_address_equivalent": (
                    "旧算法 IPv4 /24 等价值乘 256，不是去重地址并集"
                ),
                "unknown_is_zero": False,
                "cross_family_sum_allowed": False,
            },
            "coverage": {
                "status": "complete",
                "coverage_ratio": 1,
                "expected_slot_count": 1928,
                "observed_slot_count": 1928,
                "missing_slot_count": 0,
                "off_grid_row_count": 0,
                "missing_ranges": [],
                "off_grid_samples": [],
            },
            "points": points,
        },
        "event_fact_reconciliation": {},
        "fact_bucket_analysis": [],
        "sparse_feature_auxiliary": {},
        "phase_analysis": {},
        "metric_findings": {},
        "recovery_candidate": {},
        "gap_matrix": [],
        "minimal_raw_request": {
            "representative_entities": [
                {
                    "asn": "64496",
                    "selected_prefix": "192.0.2.0/24",
                    "preferred_ip_family": 4,
                }
            ],
            "update_slots": raw_slots,
        },
        "assessment": {},
        "database_security": {
            "current_user": "reader",
            "transaction_isolation": "repeatable read",
            "transaction_read_only": True,
            "default_transaction_read_only": True,
        },
        "execution": {
            "transaction_finalization": "rollback_completed",
            "transaction_mode": "repeatable_read_read_only",
            "output_semantics": "create_only",
        },
    }
    payload["content_fingerprint_sha256"] = hashlib.sha256(
        finalize._canonical_bytes(
            {
                "schema": "rrc25_iran_db_first_content_fingerprint/v2",
                "stable_research_content": {
                    field: payload[field]
                    for field in finalize.DB_FIRST_FINGERPRINT_FIELDS
                },
            }
        )
    ).hexdigest()
    return payload


def _make_db_first(root: Path, frozen_fact, values):
    directory = root / "db-first"
    directory.mkdir()
    path = directory / "iran-db-first.json"
    _write_json(path, _db_first_payload(frozen_fact, values))
    digest = _sha(path)
    (directory / "SHA256SUMS").write_text(
        f"{digest}  iran-db-first.json\n", encoding="utf-8"
    )
    return path, digest


def _gzip_jsonl(rows):
    body = b"".join(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
        for row in rows
    )
    return gzip.compress(body, mtime=0)


def _file_ref(path: Path):
    return {
        "path": str(path),
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
    }


def _make_targeted(root: Path, db_path: Path, db_sha: str):
    directory = root / "targeted"
    directory.mkdir()
    slots = [
        (
            datetime(2026, 2, 27, 22, 30, tzinfo=timezone.utc)
            + timedelta(minutes=5 * index)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        for index in range(13)
    ]
    selected_artifacts = [
        {
            "artifact_id": f"art_v1_{index + 1:024x}",
            "artifact_time_utc": slot,
            "artifact_type": "update",
            "collector_id": "rrc25",
            "compression": "gz",
            "file_sha256": f"{index + 1:064x}",
            "relative_path": f"rrc25/fixture/updates.{index:04d}.gz",
            "size_bytes": 100 + index,
        }
        for index, slot in enumerate(slots)
    ]
    selected_compressed_bytes = sum(
        artifact["size_bytes"] for artifact in selected_artifacts
    )
    requested_pair = {
        "asn": "64496",
        "prefix": "192.0.2.0/24",
        "ip_family": 4,
        "pair_index": 0,
    }
    route = {
        "route_event_id": "route_event_v1_" + "1" * 24,
        "raw_record_ref_id": "targeted_raw_ref_v1_" + "2" * 24,
        "artifact_id": selected_artifacts[0]["artifact_id"],
        "artifact_slot_utc": slots[0],
        "event_time_utc": "2026-02-27T22:31:00Z",
        "file_sha256": selected_artifacts[0]["file_sha256"],
        "collector_id": "rrc25",
        "record_ordinal": 7,
        "element_ordinal": 1,
        "prefix": "192.0.2.0/24",
        "action": "withdraw",
        "as_path": None,
        "origin_resolution": {
            "state": "not_applicable",
            "origins": [],
            "reason": "withdraw_has_no_as_path",
        },
        "vp_id": "vp_v1_" + "7" * 32,
        "requested_pairs": [requested_pair],
        "requested_pair_matches": [
            {
                "pair_index": 0,
                "target_asn": "64496",
                "status": "not_applicable",
                "reason": "withdraw_has_no_as_path_prefix_only_association",
            }
        ],
        "evidence_scope": finalize.TARGETED_EVIDENCE_SCOPE,
        "lineage_status": "raw_traceable_message_observation",
        "causal_claim_allowed": False,
    }
    raw = {
        "route_event_id": route["route_event_id"],
        "raw_record_ref_id": route["raw_record_ref_id"],
        "artifact_id": route["artifact_id"],
        "artifact_slot_utc": route["artifact_slot_utc"],
        "event_time_utc": route["event_time_utc"],
        "file_sha256": route["file_sha256"],
        "collector_id": route["collector_id"],
        "vp_id": route["vp_id"],
        "record_ordinal": route["record_ordinal"],
        "element_ordinal": route["element_ordinal"],
        "prefix": route["prefix"],
        "action": route["action"],
        "raw_record_sha256": "5" * 64,
        "record_offset": 128,
        "record_length": 64,
        "evidence_scope": finalize.TARGETED_EVIDENCE_SCOPE,
        "verification_status": "native_parser_verified",
    }
    (directory / "route-events.jsonl.gz").write_bytes(_gzip_jsonl([route]))
    (directory / "raw-record-refs.jsonl.gz").write_bytes(_gzip_jsonl([raw]))
    parser_contract_semantic = {
        "schema_version": "rrc25-full-window-parser-attestation/v1",
        "backend": "native",
        "parser_name": "fixture_native_parser",
        "parser_version": "1.0",
        "binary_sha256": "8" * 64,
        "binary_execution_policy": "verified_in_process_source",
        "adapter_source_sha256": "9" * 64,
    }
    parser_contract = {
        **parser_contract_semantic,
        "semantic_fingerprint_sha256": hashlib.sha256(
            finalize._canonical_bytes(parser_contract_semantic)
        ).hexdigest(),
    }
    plan_semantic = {
        "schema_version": "rrc25-iran-targeted-raw-plan/v1",
        "evidence_scope": finalize.TARGETED_EVIDENCE_SCOPE,
        "execution_state": "planned_not_executed",
        "request_bindings": {
            "db_first_json": {
                "path": db_path.name,
                "sha256": db_sha,
                "size_bytes": db_path.stat().st_size,
            }
        },
        "selection_window": {
            "start_utc": "2026-02-27T16:00:00Z",
            "end_exclusive_utc": "2026-03-06T08:40:00Z",
            "granularity_seconds": 300,
            "interval_semantics": "half_open",
        },
        "native_parser_contract": parser_contract,
        "selected_artifacts": selected_artifacts,
        "requested_update_slots": slots,
        "requested_pairs": [requested_pair],
        "limits": {
            "selected_update_slot_count": 13,
            "maximum_update_slot_count": 13,
            "selected_compressed_bytes": selected_compressed_bytes,
            "maximum_selected_compressed_bytes": 1_000_000,
        },
        "execution_policy": {
            "database_connections": 0,
            "database_writes": 0,
            "rib_files_opened": 0,
            "seed_performed": False,
            "state_replay_performed": False,
            "full_window_replay": False,
            "update_artifact_read_passes_per_file": 1,
            "causal_claim_allowed": False,
        },
    }
    plan_fingerprint = hashlib.sha256(
        finalize._canonical_bytes(plan_semantic)
    ).hexdigest()
    plan = {
        **plan_semantic,
        "plan_id": "traw_v1_" + plan_fingerprint[:32],
        "semantic_fingerprint_sha256": plan_fingerprint,
    }
    artifact_stats = []
    for index, artifact in enumerate(selected_artifacts):
        configuration = {"fixture_artifact_index": index}
        attestation_semantic = {
            "schema_version": "parser_attestation_v1",
            "parser_name": parser_contract["parser_name"],
            "parser_version": parser_contract["parser_version"],
            "parser_binary_sha256": parser_contract["binary_sha256"],
            "adapter_source_sha256": parser_contract[
                "adapter_source_sha256"
            ],
            "binary_execution_policy": parser_contract[
                "binary_execution_policy"
            ],
            "configuration": configuration,
            "configuration_sha256": hashlib.sha256(
                finalize._canonical_bytes(configuration)
            ).hexdigest(),
        }
        attestation_fingerprint = hashlib.sha256(
            finalize._canonical_bytes(
                {
                    "schema": "parser_attestation_fingerprint_v1",
                    "attestation": attestation_semantic,
                }
            )
        ).hexdigest()
        attestation = {
            **attestation_semantic,
            "attestation_fingerprint_sha256": attestation_fingerprint,
        }
        retained = 1 if index == 0 else 0
        artifact_stats.append(
            {
                "artifact_index": index,
                "artifact_id": artifact["artifact_id"],
                "artifact_time_utc": artifact["artifact_time_utc"],
                "file_sha256": artifact["file_sha256"],
                "size_bytes": artifact["size_bytes"],
                "native_statistics": {
                    "artifact_id": artifact["artifact_id"],
                    "status": "complete",
                    "compressed_file_sha256": artifact["file_sha256"],
                    "compressed_size_bytes": artifact["size_bytes"],
                    "compressed_bytes_read_observed": artifact["size_bytes"],
                    "compressed_read_passes": 1,
                },
                "runtime_parser_attestation": attestation,
                "parser_attestation_fingerprint_sha256": (
                    attestation_fingerprint
                ),
                "retained_route_event_count": retained,
                "retained_announce_count": 0,
                "retained_withdraw_count": retained,
            }
        )
    stats = {
        "schema_version": finalize.TARGETED_STATS_SCHEMA_VERSION,
        "evidence_scope": finalize.TARGETED_EVIDENCE_SCOPE,
        "execution_state": "completed",
        "artifact_stats": artifact_stats,
        "entity_observations": [
            {
                "asn": "64496",
                "prefix": "192.0.2.0/24",
                "pair_index": 0,
                "observation_count": 1,
                "observation_state": "message_observations_present",
                "window_expanded": False,
                "interpretation_zh": "固定测试窗口内存在一条消息观测。",
            }
        ],
        "aggregate": {
            "retained_route_event_count": 1,
            "retained_raw_record_ref_count": 1,
            "selected_update_artifact_count": 13,
            "compressed_read_pass_count": 13,
            "compressed_bytes_read": selected_compressed_bytes,
            "retained_announce_count": 0,
            "retained_withdraw_count": 1,
            "retained_observation_count_by_pair_index": {"0": 1},
        },
        "plan_id": plan["plan_id"],
        "non_actions": {
            "database_connections": 0,
            "database_writes": 0,
            "rib_files_opened": 0,
            "seed_performed": False,
            "state_replay_performed": False,
            "causal_claim_allowed": False,
        },
    }
    _write_json(directory / "parser-stats.json", stats)
    contents = {
        "route_events": _file_ref(directory / "route-events.jsonl.gz"),
        "raw_record_refs": _file_ref(directory / "raw-record-refs.jsonl.gz"),
        "parser_stats": _file_ref(directory / "parser-stats.json"),
    }
    manifest = {
        "schema_version": finalize.TARGETED_MANIFEST_SCHEMA_VERSION,
        "package_schema_version": "rrc25-iran-targeted-raw/v1",
        "evidence_scope": finalize.TARGETED_EVIDENCE_SCOPE,
        "acceptance_state": "message_observations_only_not_state_replay",
        "contents": contents,
        "counts": {
            "route_event_count": 1,
            "raw_record_ref_count": 1,
            "update_artifact_count": 13,
        },
        "database_connections": 0,
        "database_writes": 0,
        "rib_files_opened": 0,
        "state_replay_performed": False,
        "causal_claim_allowed": False,
        "entity_observations": stats["entity_observations"],
        "plan": plan,
    }
    _write_json(directory / "MANIFEST.json", manifest)
    checksums = []
    for name in finalize.TARGETED_CHECKSUM_FILES:
        checksums.append(f"{_sha(directory / name)}  {name}")
    (directory / "SHA256SUMS").write_text(
        "\n".join(checksums) + "\n", encoding="utf-8"
    )
    return directory, _sha(directory / "MANIFEST.json")


class ProxyFinalizationFixture:
    def __init__(self, root: Path, values=None):
        self.root = root
        _, frozen = _source_fact()
        if values is None:
            values = [256_000] * 1928
            values[100] = 243_200
            values[101] = 240_640
            values[102:108] = [256_000] * 6
        self.db_path, self.db_sha = _make_db_first(root, frozen, values)
        self.targeted, self.targeted_manifest_sha = _make_targeted(
            root, self.db_path, self.db_sha
        )

    def arguments(self, output: Path):
        return {
            "db_first_json": self.db_path,
            "db_first_sha256": self.db_sha,
            "targeted_raw_directory": self.targeted,
            "targeted_manifest_sha256": self.targeted_manifest_sha,
            "profile_path": PROFILE_PATH,
            "profile_sha256": _sha(PROFILE_PATH),
            "claim_inventory_path": INVENTORY_PATH,
            "claim_inventory_sha256": _sha(INVENTORY_PATH),
            "source_fact_path": SOURCE_FACT_PATH,
            "source_fact_sha256": _sha(SOURCE_FACT_PATH),
            "output_directory": output,
        }


class IranDbProxyFinalizeTest(unittest.TestCase):
    def test_proxy_boundaries_are_metric_only_and_never_state_contracts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ProxyFinalizationFixture(root)
            output = root / "out"

            result = finalize.finalize(**fixture.arguments(output))
            proxy = json.loads(
                (output / "proxy-analysis.json").read_text(encoding="utf-8")
            )

            self.assertEqual(result["workflow_state"], "completed")
            self.assertEqual(result["acceptance_state"], "not_accepted")
            self.assertEqual(proxy["episode_count"], 1)
            episode = proxy["episodes"][0]
            self.assertEqual(
                episode["onset_at_utc"], "2026-02-28T00:20:00Z"
            )
            self.assertEqual(
                episode["detected_at_utc"], "2026-02-28T00:25:00Z"
            )
            self.assertEqual(
                episode["full_recovery_candidate"]["confirmed_at_utc"],
                "2026-02-28T01:00:00Z",
            )
            self.assertEqual(
                proxy["source_metric"]["is_deduplicated_address_union"],
                False,
            )
            serialized = json.dumps(proxy, ensure_ascii=False)
            self.assertNotIn("country-outage-sample/v1", serialized)
            self.assertNotIn("country-outage-episode/v1", serialized)
            self.assertNotIn("country-outage-wave/v1", serialized)
            self.assertFalse(
                proxy["state_mapping"][
                    "route_event_to_state_mapping_performed"
                ]
            )
            self.assertEqual(
                proxy["targeted_message_evidence"]["entity_observations"],
                [
                    {
                        "pair_index": 0,
                        "asn": "64496",
                        "prefix": "192.0.2.0/24",
                        "ip_family": 4,
                        "observation_state": "message_observations_present",
                        "observation_count": 1,
                        "announce_count": 0,
                        "withdraw_count": 1,
                        "announce_target_origin_match_count": 0,
                        "distinct_vp_count": 1,
                        "window_expanded": False,
                        "interpretation_zh": "固定测试窗口内存在一条消息观测。",
                    }
                ],
            )

    def test_reconciliation_has_exact_frozen_eleven_claim_ratings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ProxyFinalizationFixture(root)
            output = root / "out"
            finalize.finalize(**fixture.arguments(output))

            reconciliation = json.loads(
                (output / "reconciliation-result.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(reconciliation["claims"]), 11)
            self.assertEqual(
                reconciliation["summary"],
                {
                    "confirmed": 2,
                    "revised": 2,
                    "unverifiable": 4,
                    "hypothesis_only": 3,
                },
            )
            ratings = {
                row["claim_type"]: row["rating"]
                for row in reconciliation["claims"]
            }
            self.assertEqual(ratings["report_event_time"], "revised")
            self.assertEqual(ratings["ipv4_decline"], "confirmed")
            self.assertEqual(ratings["recovery_state"], "revised")
            self.assertEqual(
                ratings["report_affected_asn_ratio"], "unverifiable"
            )
            self.assertEqual(
                ratings["report_visibility_class_counts"], "unverifiable"
            )
            self.assertEqual(
                ratings["database_affected_asn_ratio"], "confirmed"
            )
            self.assertEqual(
                ratings["active_withdrawal_intent"], "hypothesis_only"
            )
            self.assertEqual(ratings["physical_cut"], "hypothesis_only")
            self.assertEqual(ratings["bgp_session_closed"], "unverifiable")
            self.assertEqual(ratings["traffic_impact"], "unverifiable")
            self.assertEqual(
                ratings["government_intent"], "hypothesis_only"
            )

    def test_two_empty_output_directories_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ProxyFinalizationFixture(root)
            first = root / "first"
            second = root / "second"

            result_a = finalize.finalize(**fixture.arguments(first))
            result_b = finalize.finalize(**fixture.arguments(second))

            self.assertEqual(
                result_a["semantic_fingerprint_sha256"],
                result_b["semantic_fingerprint_sha256"],
            )
            for name in finalize.OUTPUT_CHECKSUM_FILES + ("SHA256SUMS",):
                self.assertEqual(
                    (first / name).read_bytes(),
                    (second / name).read_bytes(),
                    name,
                )

    def test_targeted_hash_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ProxyFinalizationFixture(root)
            route_path = fixture.targeted / "route-events.jsonl.gz"
            route_path.write_bytes(route_path.read_bytes() + b"tamper")

            with self.assertRaisesRegex(
                finalize.ProxyFinalizeError, "哈希不闭合"
            ):
                finalize.finalize(**fixture.arguments(root / "out"))

    def test_targeted_package_from_another_db_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ProxyFinalizationFixture(root)
            manifest_path = fixture.targeted / "MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["plan"]["request_bindings"]["db_first_json"][
                "sha256"
            ] = "f" * 64
            plan_semantic = dict(manifest["plan"])
            plan_semantic.pop("plan_id")
            plan_semantic.pop("semantic_fingerprint_sha256")
            plan_fingerprint = hashlib.sha256(
                finalize._canonical_bytes(plan_semantic)
            ).hexdigest()
            manifest["plan"]["plan_id"] = (
                "traw_v1_" + plan_fingerprint[:32]
            )
            manifest["plan"]["semantic_fingerprint_sha256"] = (
                plan_fingerprint
            )
            stats_path = fixture.targeted / "parser-stats.json"
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            stats["plan_id"] = manifest["plan"]["plan_id"]
            _write_json(stats_path, stats)
            manifest["contents"]["parser_stats"] = _file_ref(stats_path)
            _write_json(manifest_path, manifest)
            checksums = [
                f"{_sha(fixture.targeted / name)}  {name}"
                for name in finalize.TARGETED_CHECKSUM_FILES
            ]
            (fixture.targeted / "SHA256SUMS").write_text(
                "\n".join(checksums) + "\n", encoding="utf-8"
            )
            arguments = fixture.arguments(root / "out")
            arguments["targeted_manifest_sha256"] = _sha(manifest_path)

            with self.assertRaisesRegex(
                finalize.ProxyFinalizeError, "同一 DB-first"
            ):
                finalize.finalize(**arguments)

    def test_no_network_database_mrt_or_rib_and_output_is_create_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ProxyFinalizationFixture(root)
            output = root / "out"
            with mock.patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("不应访问网络或数据库"),
            ):
                finalize.finalize(**fixture.arguments(output))

            quality = json.loads(
                (output / "QUALITY.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                quality["non_actions"],
                {
                    "database_connections": 0,
                    "database_writes": 0,
                    "mrt_files_opened": 0,
                    "rib_files_opened": 0,
                    "seed_performed": False,
                    "state_replay_performed": False,
                    "route_event_to_state_mapping_performed": False,
                },
            )
            with self.assertRaises(FileExistsError):
                finalize.finalize(**fixture.arguments(output))

    def test_single_threshold_crossing_does_not_form_proxy_episode(self):
        profile = _profile()
        values = [1000] * 1928
        values[100] = 980
        points = _points(values)
        normalized = []
        for index, row in enumerate(points):
            start = datetime.fromisoformat(
                row["observed_at_local"]
            ).astimezone(timezone.utc)
            normalized.append(
                {
                    "index": index,
                    "start": start,
                    "end": start + timedelta(minutes=5),
                    "start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "start_local": row["observed_at_local"],
                    "value": float(row["ipv4_address_equivalent"]),
                }
            )

        proxy = finalize.build_proxy_analysis(
            {},
            normalized,
            profile,
            db_first_sha256="a" * 64,
            targeted_message_evidence={
                "evidence_scope": finalize.TARGETED_EVIDENCE_SCOPE,
                "route_event_count": 0,
                "raw_record_ref_count": 0,
                "mapping_to_state_performed": False,
                "state_claim_allowed": False,
                "causal_claim_allowed": False,
            },
            input_bindings=[],
        )

        self.assertEqual(proxy["episode_count"], 0)

    def test_profile_identity_cannot_be_changed_to_another_collector(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ProxyFinalizationFixture(root)
            changed_profile = json.loads(
                PROFILE_PATH.read_text(encoding="utf-8")
            )
            changed_profile["collector_id"] = "rrc99"
            changed_path = root / "changed-profile.json"
            _write_json(changed_path, changed_profile)
            arguments = fixture.arguments(root / "out")
            arguments["profile_path"] = changed_path
            arguments["profile_sha256"] = _sha(changed_path)

            with self.assertRaisesRegex(
                finalize.ProxyFinalizeError, "固定伊朗 RRC25"
            ):
                finalize.finalize(**arguments)

    def test_large_decline_cannot_keep_the_frozen_confirmed_rating(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = [256_000] * 1928
            values[100] = 128_000
            values[101] = 102_400
            fixture = ProxyFinalizationFixture(root, values=values)

            with self.assertRaisesRegex(
                finalize.ProxyFinalizeError, "评级数量偏离"
            ):
                finalize.finalize(**fixture.arguments(root / "out"))
            self.assertFalse((root / "out").exists())

    def test_output_cannot_be_nested_inside_targeted_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ProxyFinalizationFixture(root)
            nested_output = fixture.targeted / "nested-output"

            with self.assertRaisesRegex(
                finalize.ProxyFinalizeError, "嵌套或重合"
            ):
                finalize.finalize(**fixture.arguments(nested_output))
            self.assertFalse(nested_output.exists())
            self.assertEqual(
                {
                    item.name
                    for item in fixture.targeted.iterdir()
                    if not item.name.startswith(".")
                },
                finalize.EXPECTED_TARGETED_FILES,
            )

    def test_fractional_or_formula_inconsistent_ipv4_equivalent_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = [256_000] * 1928
            values[100] = 243_200
            values[101] = 240_640.5
            fixture = ProxyFinalizationFixture(root, values=values)

            with self.assertRaisesRegex(
                finalize.ProxyFinalizeError, "指标值或等价值公式非法"
            ):
                finalize.finalize(**fixture.arguments(root / "out"))


if __name__ == "__main__":
    unittest.main()
