from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import backend.data_pipeline.research.rrc25_country_outage.analysis_rib_anchor as analysis_rib_anchor
from dev.data_quality import rrc25_iran_analysis_ribs as analysis_rib_cli

from backend.data_pipeline.route_event import artifact_id_v1
from backend.data_pipeline.research.rrc25_country_outage.analysis_rib_anchor import (
    AnalysisRibAnchorError,
    DEFAULT_MAX_TEMPORARY_BYTES,
    EXPECTED_ANCHOR_COUNT,
    SimulatedAnalysisRibCrash,
    analysis_rib_execution_lock,
    build_analysis_rib_plan,
    build_analysis_rib_retention_policy,
    build_update_boundary_snapshot,
    compute_prior_journal_verification_candidate,
    cumulative_reserved_raw_bytes,
    initialize_anchor_workspace,
    import_full_window_seed_anchor,
    load_verified_prior_raw_accounting,
    load_prior_raw_accounting_from_verification_receipt,
    publish_prior_journal_verification_receipt,
    record_analysis_rib_supervisor_receipt,
    reconcile_anchor_with_update_boundary,
    reconcile_analysis_rib_anchor_workspace,
    reserve_raw_read,
    run_analysis_rib_anchor_segment,
    verify_analysis_rib_anchor_root,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    COUNTRY_MAPPING_SNAPSHOT_ID_SCHEMA,
    COUNTRY_MAPPING_SNAPSHOT_SCHEMA_VERSION,
    REVISED_MAPPING_SNAPSHOT_SCHEMA_VERSION,
    country_mapping_canonical_json,
    mapping_bundle_sha256,
)
from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (
    canonical_json,
)
from backend.data_pipeline.research.rrc25_country_outage.input_resolver import (
    resolve_research_inputs,
)
from backend.data_pipeline.research.rrc25_country_outage.profile import profile_sha256
from backend.data_pipeline.research.rrc25_country_outage.full_window_journal import (
    ArtifactDescriptor,
    ShardInput,
    SinglePassProof,
    begin_artifact_attempt,
    commit_artifact_boundary,
    initialize_full_window_journal,
)
from backend.data_pipeline.research.rrc25_country_outage.replay_persistence import (
    route_replay_state_from_payload,
)
from backend.data_pipeline.research.rrc25_country_outage.rib_parser import RibSpoolError
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    build_research_route_event,
    extend_streaming_rib_seed,
)
from backend.data_pipeline.research.rrc25_country_outage.replay_persistence import (
    route_replay_state_to_payload,
)
from backend.data_pipeline.route_event import AsPathSegment, ParsedRouteElement


UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "config/research/iran-rrc25-202602.json"
CLI_PATH = ROOT / "dev/data_quality/rrc25_iran_analysis_ribs.py"
START = datetime(2026, 2, 27, 16, 0, tzinfo=UTC)
END = datetime(2026, 3, 6, 8, 40, tzinfo=UTC)


def _mrt_record(timestamp: int, payload: bytes, *, subtype: int) -> bytes:
    return struct.pack("!IHHI", timestamp, 13, subtype, len(payload)) + payload


def _rib_bytes(at: datetime, *, two_prefixes: bool = False) -> bytes:
    timestamp = int(at.timestamp())
    peer_ip = "192.0.2.10"
    peer_asn = 64510
    view = b"rrc25"
    peer_payload = bytearray(ipaddress.IPv4Address("192.0.2.254").packed)
    peer_payload.extend(struct.pack("!H", len(view)))
    peer_payload.extend(view)
    peer_payload.extend(struct.pack("!H", 1))
    peer_payload.append(0x02)
    peer_payload.extend(ipaddress.IPv4Address("198.51.100.1").packed)
    peer_payload.extend(ipaddress.IPv4Address(peer_ip).packed)
    peer_payload.extend(peer_asn.to_bytes(4, "big"))

    def rib_record(prefix: str, sequence: int) -> bytes:
        network = ipaddress.ip_network(prefix)
        path = bytes((2, 2)) + peer_asn.to_bytes(4, "big") + (65001).to_bytes(
            4, "big"
        )
        attributes = bytes((0x40, 2, len(path))) + path
        payload = bytearray(struct.pack("!I", sequence))
        payload.append(network.prefixlen)
        payload.extend(network.network_address.packed[:3])
        payload.extend(struct.pack("!H", 1))
        payload.extend(struct.pack("!HIH", 0, timestamp, len(attributes)))
        payload.extend(attributes)
        return _mrt_record(timestamp, bytes(payload), subtype=2)

    records = [
        _mrt_record(timestamp, bytes(peer_payload), subtype=1),
        rib_record("203.0.113.0/24", 1),
    ]
    if two_prefixes:
        records.append(rib_record("198.51.100.0/24", 2))
    return b"".join(records)


def _artifact(
    kind: str,
    at: datetime,
    *,
    file_sha256: str,
    size_bytes: int,
) -> dict:
    stem = "bview" if kind == "rib" else "updates"
    return {
        "artifact_id": artifact_id_v1(file_sha256),
        "artifact_type": kind,
        "artifact_time_utc": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "collector_id": "rrc25",
        "relative_path": f"rrc25/{at:%Y.%m}/{stem}.{at:%Y%m%d.%H%M}.gz",
        "file_sha256": file_sha256,
        "size_bytes": size_bytes,
        "compression": "gz",
    }


def _mapping_snapshots():
    semantic = {
        "schema_version": COUNTRY_MAPPING_SNAPSHOT_SCHEMA_VERSION,
        "source_file_sha256": "a" * 64,
        "compatibility_policy": "first_valid_row_wins",
        "target_country": "IR",
        "rows": [
            {
                "asn": 65001,
                "country_code": "ZZ",
                "value_state": "observed",
                "source_line_number": 2,
            },
            {
                "asn": 65002,
                "country_code": "US",
                "value_state": "observed",
                "source_line_number": 3,
            },
        ],
        "conflicts": [],
        "invalid": {"count": 0, "samples": [], "samples_truncated": False},
        "summary": {
            "unique_asn_count": 2,
            "target_country_asn_count": 0,
            "missing_country_count": 0,
            "duplicate_same_count": 0,
            "conflict_record_count": 0,
            "conflict_asn_count": 0,
        },
    }
    snapshot_id = "asmap_v1_" + hashlib.sha256(
        country_mapping_canonical_json(
            {"schema": COUNTRY_MAPPING_SNAPSHOT_ID_SCHEMA, "mapping": semantic}
        ).encode("utf-8")
    ).hexdigest()[:32]
    compatible = {
        "snapshot_id": snapshot_id,
        **semantic,
        "source_metadata": {"size_bytes": 10, "basename": "mapping.csv"},
        "semantic_fingerprint_sha256": hashlib.sha256(
            country_mapping_canonical_json(semantic).encode("utf-8")
        ).hexdigest(),
    }
    revised = {
        "schema_version": REVISED_MAPPING_SNAPSHOT_SCHEMA_VERSION,
        "target_country": "IR",
        "compatible_base_binding": {
            "snapshot_id": compatible["snapshot_id"],
            "source_file_sha256": compatible["source_file_sha256"],
            "semantic_fingerprint_sha256": compatible[
                "semantic_fingerprint_sha256"
            ],
        },
        "source": {
            "path": "/fixture/revised.csv",
            "sha256": "b" * 64,
            "size_bytes": 10,
            "source_kind": "legacy_derived_official_delegate_missing_list",
            "generated_on": "2026-04-07",
            "upstream_artifact_state": "not_retained_in_discovered_directory",
        },
        "temporal_policy": {
            "delegated_date_on_or_before": "20260227",
            "interval_role": "known_allocated_by_research_window_start_date",
            "excluded_after_cutoff_count": 0,
            "excluded_after_cutoff_asns": [],
        },
        "rows": [
            {
                "asn": 65001,
                "registry": "ripencc",
                "country_code": "IR",
                "resource_type": "asn",
                "delegated_date": "20260220",
                "status": "allocated",
                "range_start": 65001,
                "range_count": 1,
            }
        ],
        "summary": {
            "source_row_count": 1,
            "included_row_count": 1,
            "excluded_after_cutoff_count": 0,
            "present_in_compatible_snapshot_count": 1,
            "compatible_zz_count": 1,
            "compatible_explicit_other_country_count": 0,
        },
        "compatible_override_audit": {
            "policy": "revised_view_only_compatible_view_unchanged",
            "explicit_other_country_rows": [],
        },
        "limitations_zh": [
            "该增量文件由 fixture 在2026-04-07生成。",
            "上游官方 delegated 原始文件未在 fixture 中保留。",
            "delegated 国家归属不等同于路由运营位置。",
        ],
    }
    return compatible, revised


def _terminal_full_window_journal(
    root: Path,
    *,
    bindings: dict,
    seed_artifact: dict,
):
    event = build_research_route_event(
        artifact_id=seed_artifact["artifact_id"],
        file_sha256=seed_artifact["file_sha256"],
        collector_id="rrc25",
        artifact_slot_utc=seed_artifact["artifact_time_utc"],
        record_ordinal=1,
        element_ordinal=0,
        element=ParsedRouteElement(
            event_time_utc=seed_artifact["artifact_time_utc"],
            peer_ip="198.51.100.1",
            peer_asn=64510,
            action="rib_snapshot",
            prefix="203.0.113.0/24",
            afi_safi="ipv4_unicast",
            as_path=(AsPathSegment("as_sequence", (64510, 65001)),),
            quality_flags=(),
        ),
    )
    state = extend_streaming_rib_seed(None, (event,))
    raw_id = "raw_v1_" + "4" * 32
    event_row = {
        "schema_version": "fixture-route-event/v1",
        "route_event_id": event.route_event_id,
        "artifact_id": event.artifact_id,
        "file_sha256": event.file_sha256,
        "collector_id": event.collector_id,
        "artifact_slot_utc": event.artifact_slot_utc,
        "record_ordinal": event.record_ordinal,
        "element_ordinal": event.element_ordinal,
        "event_time_utc": event.event_time_utc,
        "peer_ip": event.peer_ip,
        "peer_asn": event.peer_asn,
        "vp_id": event.vp_id,
        "action": event.action,
        "afi_safi": event.afi_safi,
        "prefix": event.prefix,
        "as_path": [
            {"segment_type": "as_sequence", "asns": [64510, 65001]}
        ],
        "quality_flags": [],
        "raw_record_ref_id": raw_id,
        "raw_record_ref_ids": [raw_id],
    }
    raw_ref = {
        "schema_version": "fixture-raw-ref/v1",
        "raw_record_ref_id": raw_id,
        "route_event_id": event.route_event_id,
        "artifact_id": event.artifact_id,
        "file_sha256": event.file_sha256,
        "artifact_slot_utc": event.artifact_slot_utc,
        "record_ordinal": event.record_ordinal,
        "element_ordinal": event.element_ordinal,
        "record_offset": 1,
        "record_length": 2,
        "record_hash": "5" * 64,
        "raw_record_sha256": "5" * 64,
        "verification_status": "verified",
        "verification_basis": "complete_artifact_single_pass_sha256_and_record_hash",
    }
    bootstrap = {
        "seed_artifact_ref": {
            "artifact_id": seed_artifact["artifact_id"],
            "file_sha256": seed_artifact["file_sha256"],
            "size_bytes": seed_artifact["size_bytes"],
        },
        "seed_parser": {"fixture": True},
        "seed_spool_attestation": {"fixture": True},
        "seed_route_state": route_replay_state_to_payload(state),
    }
    journal_root = root / "full-window-journal"
    head = initialize_full_window_journal(
        journal_root,
        run_id="research_run_v1_" + "1" * 24,
        bindings=bindings,
        total_artifacts=1,
        initial_compact_state={"seed": True},
        preliminary_seed_read_bytes=100,
        seed_artifact_read_bytes=seed_artifact["size_bytes"],
        additional_pre_update_raw_read_bytes=0,
        bootstrap_bytes_per_second=1_000_000.0,
        genesis_shards=(
            ShardInput("seed_bootstrap_attestation", (bootstrap,)),
            ShardInput("seed_route_events", (event_row,)),
            ShardInput("seed_raw_record_refs", (raw_ref,)),
        ),
    )
    update = ArtifactDescriptor(
        index=0,
        artifact_id=artifact_id_v1("6" * 64),
        file_sha256="6" * 64,
        size_bytes=1,
        collector_id="rrc25",
        slot_start_utc=START.strftime("%Y-%m-%dT%H:%M:%SZ"),
        slot_end_exclusive_utc=(START + timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    )
    token = begin_artifact_attempt(head, update)
    committed = commit_artifact_boundary(
        head,
        token,
        proof=SinglePassProof(
            status="complete",
            compressed_file_sha256=update.file_sha256,
            compressed_size_bytes=1,
            compressed_bytes_read_observed=1,
            compressed_read_passes=1,
            process_seconds=1.0,
            peak_temporary_bytes=0,
            database_write_operations=0,
        ),
        compact_state={"done": True},
        shards=(),
    )
    del committed
    return journal_root


def _fixture(root: Path, *, two_prefixes_first: bool = False):
    raw_root = root / "raw"
    raw_root.mkdir()
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    artifacts = []
    rib_paths = {}
    rib_times = [START - timedelta(hours=8)] + [
        START + timedelta(hours=8 * index) for index in range(21)
    ]
    for index, at in enumerate(rib_times):
        raw = _rib_bytes(at, two_prefixes=two_prefixes_first and index == 0)
        compressed = gzip.compress(raw, compresslevel=1, mtime=0)
        digest = hashlib.sha256(compressed).hexdigest()
        artifact = _artifact(
            "rib", at, file_sha256=digest, size_bytes=len(compressed)
        )
        path = raw_root / artifact["relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(compressed)
        artifacts.append(artifact)
        rib_paths[artifact["artifact_id"]] = path
    update_count = int((END - START).total_seconds()) // 300
    for index in range(update_count):
        at = START + timedelta(minutes=5 * index)
        digest = hashlib.sha256(f"update-{index}".encode()).hexdigest()
        artifacts.append(
            _artifact("update", at, file_sha256=digest, size_bytes=1)
        )
    manifest_fingerprint = hashlib.sha256(
        canonical_json(artifacts).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": 1,
        "manifest_kind": "mrt_artifact_manifest",
        "manifest_fingerprint_sha256": manifest_fingerprint,
        "artifacts": artifacts,
    }
    verification = {
        "verified": True,
        "manifest_fingerprint_sha256": manifest_fingerprint,
        "artifact_count": len(artifacts),
    }
    selection = resolve_research_inputs(manifest, verification, profile)
    compatible, revised = _mapping_snapshots()
    bindings = {
        "profile_sha256": profile_sha256(profile),
        "input_selection_sha256": selection["semantic_fingerprint_sha256"],
        "code_sha256": "2" * 64,
        "mapping_sha256": mapping_bundle_sha256(compatible, revised),
    }
    seed_artifact = next(
        row
        for row in artifacts
        if row["artifact_type"] == "rib"
        and row["artifact_time_utc"] == START.strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    journal_root = _terminal_full_window_journal(
        root, bindings=bindings, seed_artifact=seed_artifact
    )
    accounting = load_verified_prior_raw_accounting(
        journal_root, bindings=bindings
    )
    retention = build_analysis_rib_retention_policy(
        compatible, revised, bindings=bindings
    )
    compatible_path = root / "compatible-mapping.json"
    revised_path = root / "revised-mapping.json"
    _write_json(compatible_path, compatible)
    _write_json(revised_path, revised)
    return (
        raw_root,
        profile,
        selection,
        bindings,
        rib_paths,
        accounting,
        retention,
        journal_root,
        compatible_path,
        revised_path,
    )


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class AnalysisRibAnchorTests(unittest.TestCase):
    def test_prior_journal_deep_verification_is_sealed_once_then_lightweight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                _raw,
                _profile,
                _selection,
                bindings,
                _paths,
                accounting,
                _retention,
                journal_root,
                _compatible_path,
                _revised_path,
            ) = _fixture(root)
            candidate = compute_prior_journal_verification_candidate(
                journal_root, bindings=bindings
            )
            verification_root = root / "prior-verification"
            verification_root.mkdir()
            supervision = {
                "semantics": (
                    "independent_process_group_420_observe_540_term_590_kill_596_exit_v1"
                ),
                "policy": {
                    "observation_seconds": 420.0,
                    "term_seconds": 540.0,
                    "kill_seconds": 590.0,
                    "parent_exit_seconds_exclusive": 596.0,
                },
                "actions": {
                    "term_sent": False,
                    "kill_sent": False,
                    "child_reaped_within_parent_deadline": True,
                },
                "child_exit_code": 0,
                "elapsed_seconds": 1.0,
                "database_writes": 0,
            }
            published = publish_prior_journal_verification_receipt(
                verification_root,
                candidate=candidate,
                journal_root=journal_root,
                bindings=bindings,
                supervision=supervision,
            )
            with mock.patch.object(
                analysis_rib_anchor,
                "load_verified_prior_raw_accounting",
                side_effect=AssertionError("轻验不得重复遍历 1928 槽 ancestry"),
            ):
                loaded = load_prior_raw_accounting_from_verification_receipt(
                    published["path"],
                    journal_root=journal_root,
                    bindings=bindings,
                )
            self.assertEqual(loaded.to_dict(), accounting.to_dict())
            self.assertEqual(
                Path(published["path"]).name,
                f"prior-journal-verification-{published['sha256']}.json",
            )

    def test_plan_requires_exact_21_analysis_plus_one_baseline_and_exclusive_raw_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                _raw,
                profile,
                selection,
                bindings,
                _paths,
                accounting,
                _retention,
                _journal,
                _compatible_path,
                _revised_path,
            ) = _fixture(root)
            plan = build_analysis_rib_plan(
                selection,
                profile,
                prior_raw_accounting=accounting,
                bindings=bindings,
            )
            self.assertEqual(len(plan.artifacts), EXPECTED_ANCHOR_COUNT)
            self.assertEqual(plan.artifacts[0].role, "baseline_reference_rib")
            self.assertTrue(
                all(item.role == "analysis_rib" for item in plan.artifacts[1:])
            )
            self.assertTrue(plan.execution_allowed)
            self.assertEqual(plan.to_dict()["imported_seed_anchor_count"], 1)
            self.assertEqual(plan.to_dict()["new_raw_analysis_rib_count"], 20)
            self.assertEqual(
                plan.projected_cumulative_raw_read_bytes,
                accounting.cumulative_reserved_raw_bytes
                + sum(
                    item.size_bytes
                    for item in plan.artifacts
                    if item.ingestion_mode == "new_raw"
                ),
            )

            missing = deepcopy(selection)
            missing["roles"]["analysis_ribs"].pop()
            with self.assertRaisesRegex(AnalysisRibAnchorError, "selection/Profile"):
                build_analysis_rib_plan(
                    missing,
                    profile,
                    prior_raw_accounting=accounting,
                    bindings=bindings,
                )

            at_limit = build_analysis_rib_plan(
                selection,
                profile,
                prior_raw_accounting=accounting,
                bindings=bindings,
                max_raw_read_bytes=(
                    accounting.cumulative_reserved_raw_bytes
                    + plan.planned_new_raw_read_bytes
                ),
            )
            self.assertFalse(at_limit.execution_allowed)
            self.assertEqual(at_limit.blocker, "cumulative_raw_read_limit_reached")

    def test_create_only_raw_ledger_never_refunds_failed_or_retried_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                raw_root,
                profile,
                selection,
                bindings,
                _paths,
                accounting,
                retention,
                _journal,
                _compatible_path,
                _revised_path,
            ) = _fixture(root)
            base_plan = build_analysis_rib_plan(
                selection,
                profile,
                prior_raw_accounting=accounting,
                bindings=bindings,
            )
            raw_limit = (
                accounting.cumulative_reserved_raw_bytes
                + base_plan.planned_new_raw_read_bytes
                + 2 * base_plan.artifacts[0].size_bytes
            )
            plan = build_analysis_rib_plan(
                selection,
                profile,
                prior_raw_accounting=accounting,
                bindings=bindings,
                max_raw_read_bytes=raw_limit,
            )
            anchor_root = root / "anchors"
            initialize_anchor_workspace(
                anchor_root,
                artifact_root=raw_root,
                plan=plan,
                bindings=bindings,
                retention_policy=retention,
                max_raw_read_bytes=raw_limit,
            )
            new_raw = [
                item for item in plan.artifacts if item.ingestion_mode == "new_raw"
            ]
            tokens = []
            for index, descriptor in enumerate(new_raw):
                tokens.append(
                    reserve_raw_read(
                        anchor_root,
                        descriptor,
                        attempt_id="attempt_v1_" + f"{index + 1:032x}",
                    )
                )
            first = tokens[0]
            second = reserve_raw_read(
                anchor_root,
                new_raw[0],
                attempt_id="attempt_v1_" + "e" * 32,
            )
            self.assertEqual(
                cumulative_reserved_raw_bytes(anchor_root),
                accounting.cumulative_reserved_raw_bytes
                + plan.planned_new_raw_read_bytes
                + new_raw[0].size_bytes,
            )
            self.assertGreater(second.cumulative_reserved_raw_bytes, first.cumulative_reserved_raw_bytes)
            with self.assertRaisesRegex(AnalysisRibAnchorError, "attempt_id 已存在"):
                reserve_raw_read(
                    anchor_root,
                    new_raw[1],
                    attempt_id=tokens[1].attempt_id,
                )
            with self.assertRaisesRegex(AnalysisRibAnchorError, "达到或超过"):
                reserve_raw_read(
                    anchor_root,
                    new_raw[0],
                    attempt_id="attempt_v1_" + "f" * 32,
                )
            with self.assertRaisesRegex(AnalysisRibAnchorError, "禁止重复读取 raw"):
                reserve_raw_read(anchor_root, plan.artifacts[1])

    def test_corrupt_gzip_and_temporary_limit_fail_without_raw_refund(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                raw_root, profile, selection, bindings, rib_paths,
                accounting, retention, _journal, _compatible_path, _revised_path,
            ) = _fixture(root)
            plan = build_analysis_rib_plan(
                selection, profile, prior_raw_accounting=accounting, bindings=bindings
            )
            anchor_root = root / "anchors"
            initialize_anchor_workspace(
                anchor_root,
                artifact_root=raw_root,
                plan=plan,
                bindings=bindings,
                retention_policy=retention,
            )
            descriptor = plan.artifacts[0]
            token = reserve_raw_read(
                anchor_root,
                descriptor,
                attempt_id="attempt_v1_" + "a" * 32,
            )
            reserved = cumulative_reserved_raw_bytes(anchor_root)
            raw_path = rib_paths[descriptor.artifact_id]
            corrupted = bytearray(raw_path.read_bytes())
            corrupted[-1] ^= 0x01
            raw_path.write_bytes(corrupted)
            with self.assertRaises(RibSpoolError):
                run_analysis_rib_anchor_segment(
                    anchor_root,
                    artifact_root=raw_root,
                    descriptor=descriptor,
                    bindings=bindings,
                    reservation=token,
                    retention_policy=retention,
                    clock=lambda: 0.0,
                )
            self.assertEqual(cumulative_reserved_raw_bytes(anchor_root), reserved)
            self.assertEqual(tuple((anchor_root / "spools").glob("*.mrt")), ())
            with self.assertRaisesRegex(AnalysisRibAnchorError, "重试必须新增"):
                run_analysis_rib_anchor_segment(
                    anchor_root,
                    artifact_root=raw_root,
                    descriptor=descriptor,
                    bindings=bindings,
                    reservation=token,
                    retention_policy=retention,
                    clock=lambda: 0.0,
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                raw_root, profile, selection, bindings, rib_paths,
                accounting, retention, _journal, _compatible_path, _revised_path,
            ) = _fixture(root)
            plan = build_analysis_rib_plan(
                selection, profile, prior_raw_accounting=accounting, bindings=bindings
            )
            anchor_root = root / "anchors"
            initialize_anchor_workspace(
                anchor_root,
                artifact_root=raw_root,
                plan=plan,
                bindings=bindings,
                retention_policy=retention,
            )
            descriptor = plan.artifacts[0]
            token = reserve_raw_read(
                anchor_root,
                descriptor,
                attempt_id="attempt_v1_" + "b" * 32,
            )
            with self.assertRaisesRegex(AnalysisRibAnchorError, "540<590"):
                run_analysis_rib_anchor_segment(
                    anchor_root,
                    artifact_root=raw_root,
                    descriptor=descriptor,
                    bindings=bindings,
                    reservation=token,
                    retention_policy=retention,
                    soft_stop_seconds=541,
                    clock=lambda: 0.0,
                )
            spent_after_invalid_policy = cumulative_reserved_raw_bytes(anchor_root)
            retry_token = reserve_raw_read(
                anchor_root,
                descriptor,
                attempt_id="attempt_v1_" + "d" * 32,
            )
            with self.assertRaises(RibSpoolError):
                run_analysis_rib_anchor_segment(
                    anchor_root,
                    artifact_root=raw_root,
                    descriptor=descriptor,
                    bindings=bindings,
                    reservation=retry_token,
                    retention_policy=retention,
                    max_temporary_bytes=64,
                    clock=lambda: 0.0,
                )
            self.assertEqual(
                cumulative_reserved_raw_bytes(anchor_root),
                spent_after_invalid_policy + descriptor.size_bytes,
            )
            self.assertEqual(tuple((anchor_root / "spools").glob("*.mrt")), ())

    def test_execution_flock_is_single_writer_across_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                raw_root, profile, selection, bindings, _rib_paths,
                accounting, retention, _journal, _compatible_path, _revised_path,
            ) = _fixture(root)
            plan = build_analysis_rib_plan(
                selection,
                profile,
                prior_raw_accounting=accounting,
                bindings=bindings,
            )
            anchor_root = root / "anchors"
            initialize_anchor_workspace(
                anchor_root,
                artifact_root=raw_root,
                plan=plan,
                bindings=bindings,
                retention_policy=retention,
            )
            child = """
import sys
from backend.data_pipeline.research.rrc25_country_outage.analysis_rib_anchor import AnalysisRibAnchorError, analysis_rib_execution_lock
try:
    with analysis_rib_execution_lock(sys.argv[1], nonblocking=True):
        print("acquired")
except AnalysisRibAnchorError:
    print("blocked")
    raise SystemExit(73)
"""
            environment = {**os.environ, "PYTHONPATH": str(ROOT)}
            with analysis_rib_execution_lock(anchor_root):
                blocked = subprocess.run(
                    [sys.executable, "-c", child, str(anchor_root)],
                    cwd=ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
            self.assertEqual(blocked.returncode, 73, blocked.stderr)
            self.assertEqual(blocked.stdout.strip(), "blocked")

            acquired = subprocess.run(
                [sys.executable, "-c", child, str(anchor_root)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(acquired.returncode, 0, acquired.stderr)
            self.assertEqual(acquired.stdout.strip(), "acquired")

    def test_supervisor_fixture_policy_sends_term_then_kill_and_stays_non_production(self):
        cases = (
            (False, "e", False),
            (True, "f", True),
        )
        for ignore_term, attempt_suffix, expect_kill in cases:
            with self.subTest(ignore_term=ignore_term):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (
                        raw_root, profile, selection, bindings, _rib_paths,
                        accounting, retention, _journal,
                        _compatible_path, _revised_path,
                    ) = _fixture(root)
                    plan = build_analysis_rib_plan(
                        selection,
                        profile,
                        prior_raw_accounting=accounting,
                        bindings=bindings,
                    )
                    anchor_root = root / "anchors"
                    initialize_anchor_workspace(
                        anchor_root,
                        artifact_root=raw_root,
                        plan=plan,
                        bindings=bindings,
                        retention_policy=retention,
                    )
                    descriptor = plan.artifacts[0]
                    token = reserve_raw_read(
                        anchor_root,
                        descriptor,
                        attempt_id="attempt_v1_" + attempt_suffix * 32,
                    )
                    # 先建立可核验的 attempt/outcome；该 reservation 已消费且不退款。
                    with self.assertRaisesRegex(AnalysisRibAnchorError, "540<590"):
                        run_analysis_rib_anchor_segment(
                            anchor_root,
                            artifact_root=raw_root,
                            descriptor=descriptor,
                            bindings=bindings,
                            reservation=token,
                            retention_policy=retention,
                            soft_stop_seconds=541,
                            clock=lambda: 0.0,
                        )
                    child_code = "import time; time.sleep(10)"
                    if ignore_term:
                        child_code = (
                            "import signal,time; "
                            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                            "time.sleep(10)"
                        )
                    supervised = analysis_rib_cli._supervise_child(
                        [sys.executable, "-c", child_code],
                        anchor_root=anchor_root,
                        bindings=bindings,
                        attempt_id=token.attempt_id,
                        artifact_id=descriptor.artifact_id,
                        command_kind="fixture-supervisor-test",
                        observation_seconds=0.05,
                        term_seconds=0.10,
                        kill_seconds=0.25,
                    )
                    self.assertTrue(supervised["abnormal_exit"])
                    self.assertIsNotNone(supervised["supervisor_receipt_ref"])
                    receipt = _load_json(
                        anchor_root
                        / supervised["supervisor_receipt_ref"]["path"]
                    )
                    self.assertTrue(receipt["actions"]["observed_420"])
                    self.assertTrue(receipt["actions"]["term_sent"])
                    self.assertEqual(
                        receipt["actions"]["kill_sent"], expect_kill
                    )
                    self.assertFalse(
                        receipt["policy"]["is_frozen_production_policy"]
                    )
                    closure = analysis_rib_anchor._verify_execution_closure(
                        anchor_root,
                        bindings=bindings,
                        expected_by_id={
                            item.artifact_id: item for item in plan.artifacts
                        },
                    )
                    self.assertFalse(closure["execution_ready"])
                    self.assertIn(
                        "frozen_420_540_590_supervisor_evidence_missing",
                        closure["blocking_reasons"],
                    )

    def test_reconcile_retires_spool_after_anchor_publish_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                raw_root, profile, selection, bindings, _rib_paths,
                accounting, retention, _journal, _compatible_path, _revised_path,
            ) = _fixture(root)
            plan = build_analysis_rib_plan(
                selection,
                profile,
                prior_raw_accounting=accounting,
                bindings=bindings,
            )
            anchor_root = root / "anchors"
            initialize_anchor_workspace(
                anchor_root,
                artifact_root=raw_root,
                plan=plan,
                bindings=bindings,
                retention_policy=retention,
            )
            descriptor = plan.artifacts[0]
            token = reserve_raw_read(
                anchor_root,
                descriptor,
                attempt_id="attempt_v1_" + "7" * 32,
            )

            def crash(stage):
                if stage == "after_anchor_receipt_publish":
                    raise SimulatedAnalysisRibCrash(stage)

            with self.assertRaisesRegex(
                SimulatedAnalysisRibCrash, "after_anchor_receipt_publish"
            ):
                run_analysis_rib_anchor_segment(
                    anchor_root,
                    artifact_root=raw_root,
                    descriptor=descriptor,
                    bindings=bindings,
                    reservation=token,
                    retention_policy=retention,
                    clock=lambda: 0.0,
                    crash_hook=crash,
                )
            self.assertTrue((anchor_root / "execution/ACTIVE.json").is_file())
            self.assertEqual(len(tuple((anchor_root / "receipts").glob("anchor-*.json"))), 1)
            self.assertEqual(len(tuple((anchor_root / "spools").glob("*.mrt"))), 1)

            reconciled = reconcile_analysis_rib_anchor_workspace(
                anchor_root, bindings=bindings
            )
            self.assertEqual(reconciled["status"], "reconciled_complete")
            self.assertEqual(reconciled["action"], "published_anchor_spool_retired")
            self.assertTrue(reconciled["active_cleared"])
            self.assertFalse((anchor_root / "execution/ACTIVE.json").exists())
            self.assertEqual(tuple((anchor_root / "spools").glob("*.mrt")), ())
            self.assertEqual(
                len(
                    tuple(
                        (anchor_root / "retirements").glob(
                            "retirement-success-*.json"
                        )
                    )
                ),
                1,
            )

    def test_reconcile_reconstructs_success_after_spool_unlink_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                raw_root, profile, selection, bindings, _rib_paths,
                accounting, retention, _journal, _compatible_path, _revised_path,
            ) = _fixture(root)
            plan = build_analysis_rib_plan(
                selection,
                profile,
                prior_raw_accounting=accounting,
                bindings=bindings,
            )
            anchor_root = root / "anchors"
            initialize_anchor_workspace(
                anchor_root,
                artifact_root=raw_root,
                plan=plan,
                bindings=bindings,
                retention_policy=retention,
            )
            descriptor = plan.artifacts[0]
            token = reserve_raw_read(
                anchor_root,
                descriptor,
                attempt_id="attempt_v1_" + "8" * 32,
            )

            def crash(stage):
                if stage == "after_spool_unlink_before_success":
                    raise SimulatedAnalysisRibCrash(stage)

            with self.assertRaisesRegex(
                SimulatedAnalysisRibCrash,
                "after_spool_unlink_before_success",
            ):
                run_analysis_rib_anchor_segment(
                    anchor_root,
                    artifact_root=raw_root,
                    descriptor=descriptor,
                    bindings=bindings,
                    reservation=token,
                    retention_policy=retention,
                    clock=lambda: 0.0,
                    crash_hook=crash,
                )
            self.assertEqual(tuple((anchor_root / "spools").glob("*.mrt")), ())
            self.assertEqual(
                len(
                    tuple(
                        (anchor_root / "retirements").glob(
                            "retirement-attempt-*.json"
                        )
                    )
                ),
                1,
            )
            self.assertEqual(
                tuple(
                    (anchor_root / "retirements").glob(
                        "retirement-success-*.json"
                    )
                ),
                (),
            )

            reconciled = reconcile_analysis_rib_anchor_workspace(
                anchor_root, bindings=bindings
            )
            self.assertEqual(reconciled["status"], "reconciled_complete")
            self.assertEqual(
                reconciled["action"], "retirement_success_reconstructed"
            )
            self.assertTrue(reconciled["active_cleared"])
            successes = tuple(
                (anchor_root / "retirements").glob("retirement-success-*.json")
            )
            self.assertEqual(len(successes), 1)
            success = _load_json(successes[0])
            self.assertEqual(success["status"], "retired_and_directory_fsynced")

    def test_near_five_gb_sparse_staging_and_spool_hit_combined_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                raw_root, profile, selection, bindings, rib_paths,
                accounting, retention, _journal, _compatible_path, _revised_path,
            ) = _fixture(root)
            plan = build_analysis_rib_plan(
                selection,
                profile,
                prior_raw_accounting=accounting,
                bindings=bindings,
            )
            anchor_root = root / "anchors"
            initialize_anchor_workspace(
                anchor_root,
                artifact_root=raw_root,
                plan=plan,
                bindings=bindings,
                retention_policy=retention,
            )
            descriptor = plan.artifacts[0]
            token = reserve_raw_read(
                anchor_root,
                descriptor,
                attempt_id="attempt_v1_" + "9" * 32,
            )
            spool_size = len(
                gzip.decompress(rib_paths[descriptor.artifact_id].read_bytes())
            )
            staging_size = DEFAULT_MAX_TEMPORARY_BYTES - spool_size + 1
            self.assertLess(staging_size, DEFAULT_MAX_TEMPORARY_BYTES)
            self.assertGreaterEqual(
                staging_size + spool_size, DEFAULT_MAX_TEMPORARY_BYTES
            )
            staging = anchor_root / "shards/.near-5gb.tmp-fixture"
            with staging.open("wb") as stream:
                stream.truncate(staging_size)

            reserved = cumulative_reserved_raw_bytes(anchor_root)
            with self.assertRaisesRegex(RibSpoolError, "达到或超过"):
                run_analysis_rib_anchor_segment(
                    anchor_root,
                    artifact_root=raw_root,
                    descriptor=descriptor,
                    bindings=bindings,
                    reservation=token,
                    retention_policy=retention,
                    clock=lambda: 0.0,
                )
            self.assertEqual(cumulative_reserved_raw_bytes(anchor_root), reserved)
            self.assertEqual(tuple((anchor_root / "spools").glob("*.mrt")), ())
            self.assertFalse(staging.exists())

    def test_record_boundary_checkpoint_resumes_without_new_raw_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                raw_root, profile, selection, bindings, rib_paths,
                accounting, retention, _journal, _compatible_path, _revised_path,
            ) = _fixture(root, two_prefixes_first=True)
            plan = build_analysis_rib_plan(
                selection, profile, prior_raw_accounting=accounting, bindings=bindings
            )
            anchor_root = root / "anchors"
            initialize_anchor_workspace(
                anchor_root,
                artifact_root=raw_root,
                plan=plan,
                bindings=bindings,
                retention_policy=retention,
            )
            descriptor = plan.artifacts[0]
            token = reserve_raw_read(
                anchor_root,
                descriptor,
                attempt_id="attempt_v1_" + "3" * 32,
            )

            class BoundaryClock:
                def __init__(self):
                    self.calls = 0

                def __call__(self):
                    self.calls += 1
                    return 0.0 if self.calls == 1 else 420.0

            with mock.patch.object(
                analysis_rib_anchor,
                "verify_rib_decompressed_spool",
                wraps=analysis_rib_anchor.verify_rib_decompressed_spool,
            ) as verify_spool:
                paused = run_analysis_rib_anchor_segment(
                    anchor_root,
                    artifact_root=raw_root,
                    descriptor=descriptor,
                    bindings=bindings,
                    reservation=token,
                    retention_policy=retention,
                    clock=BoundaryClock(),
                )
                self.assertEqual(paused.status, "checkpointed")
                self.assertEqual(paused.next_record_ordinal, 1)
                self.assertTrue((anchor_root / paused.checkpoint_path).is_file())
                self.assertEqual(
                    len(tuple((anchor_root / "spools").glob("*.mrt"))), 1
                )
                self.assertEqual(verify_spool.call_count, 0)

                # 恢复只依赖已核验 spool；原始压缩文件不再次打开。
                # spool 的唯一次全量复核发生在最终退役前。
                raw_path = rib_paths[descriptor.artifact_id]
                moved = raw_path.with_suffix(".unavailable")
                raw_path.rename(moved)
                completed = run_analysis_rib_anchor_segment(
                    anchor_root,
                    artifact_root=raw_root,
                    descriptor=descriptor,
                    bindings=bindings,
                    reservation=token,
                    retention_policy=retention,
                    resume_checkpoint_path=paused.checkpoint_path,
                    clock=lambda: 0.0,
                )
                self.assertEqual(verify_spool.call_count, 1)
            self.assertEqual(completed.status, "complete")
            self.assertEqual(tuple((anchor_root / "spools").glob("*.mrt")), ())
            self.assertEqual(len(tuple((anchor_root / "ledger").glob("reservation-*.json"))), 1)

    def test_parse_complete_at_540_soft_boundary_defers_only_finalization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                raw_root, profile, selection, bindings, _paths,
                accounting, retention, _journal, _compatible_path, _revised_path,
            ) = _fixture(root)
            plan = build_analysis_rib_plan(
                selection, profile, prior_raw_accounting=accounting, bindings=bindings
            )
            anchor_root = root / "anchors"
            initialize_anchor_workspace(
                anchor_root,
                artifact_root=raw_root,
                plan=plan,
                bindings=bindings,
                retention_policy=retention,
            )
            descriptor = plan.artifacts[0]
            token = reserve_raw_read(
                anchor_root,
                descriptor,
                attempt_id="attempt_v1_" + "c" * 32,
            )

            class FinalizeClock:
                def __init__(self):
                    self.calls = 0

                def __call__(self):
                    self.calls += 1
                    return 0.0 if self.calls <= 3 else 540.0

            paused = run_analysis_rib_anchor_segment(
                anchor_root,
                artifact_root=raw_root,
                descriptor=descriptor,
                bindings=bindings,
                reservation=token,
                retention_policy=retention,
                clock=FinalizeClock(),
            )
            self.assertEqual(paused.status, "checkpointed")
            self.assertEqual(paused.reason, "soft_runtime_stop")
            checkpoint = _load_json(anchor_root / paused.checkpoint_path)
            self.assertTrue(checkpoint["position"]["parse_complete"])
            completed = run_analysis_rib_anchor_segment(
                anchor_root,
                artifact_root=raw_root,
                descriptor=descriptor,
                bindings=bindings,
                reservation=token,
                retention_policy=retention,
                resume_checkpoint_path=paused.checkpoint_path,
                clock=lambda: 0.0,
            )
            self.assertEqual(completed.status, "complete")
            self.assertEqual(tuple((anchor_root / "spools").glob("*.mrt")), ())

    def test_fixture_closes_all_22_anchors_verifies_and_reconciles_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (
                raw_root, profile, selection, bindings, rib_paths,
                accounting, retention, journal_root, compatible_path, revised_path,
            ) = _fixture(root)
            plan = build_analysis_rib_plan(
                selection, profile, prior_raw_accounting=accounting, bindings=bindings
            )
            anchor_root = root / "anchors"
            initialize_anchor_workspace(
                anchor_root,
                artifact_root=raw_root,
                plan=plan,
                bindings=bindings,
                retention_policy=retention,
            )
            imported_descriptor = next(
                item
                for item in plan.artifacts
                if item.ingestion_mode == "imported_full_window_seed"
            )
            # 导入闭环不能偷读同一 seed raw；即使源文件不可用也必须成功。
            rib_paths[imported_descriptor.artifact_id].rename(
                rib_paths[imported_descriptor.artifact_id].with_suffix(".unavailable")
            )
            results = []
            supervised_attempts = []
            for index, descriptor in enumerate(plan.artifacts):
                attempt_id = "attempt_v1_" + f"{index + 100:032x}"
                if descriptor.ingestion_mode == "imported_full_window_seed":
                    results.append(
                        import_full_window_seed_anchor(
                            anchor_root,
                            descriptor=descriptor,
                            bindings=bindings,
                            retention_policy=retention,
                            attempt_id=attempt_id,
                        )
                    )
                    supervised_attempts.append(
                        (attempt_id, descriptor.artifact_id)
                    )
                    continue
                token = reserve_raw_read(
                    anchor_root,
                    descriptor,
                    attempt_id=attempt_id,
                )
                results.append(
                    run_analysis_rib_anchor_segment(
                        anchor_root,
                        artifact_root=raw_root,
                        descriptor=descriptor,
                        bindings=bindings,
                        reservation=token,
                        retention_policy=retention,
                        clock=lambda: 0.0,
                    )
                )
                supervised_attempts.append(
                    (token.attempt_id, descriptor.artifact_id)
                )
            self.assertTrue(all(result.status == "complete" for result in results))
            self.assertEqual(tuple((anchor_root / "spools").glob("*.mrt")), ())

            verified = verify_analysis_rib_anchor_root(
                anchor_root,
                selection=selection,
                profile=profile,
                bindings=bindings,
            )
            self.assertTrue(verified["verified"])
            self.assertEqual(verified["anchor_count"], 22)
            self.assertEqual(verified["imported_seed_anchor_count"], 1)
            self.assertFalse(verified["execution_ready"])
            self.assertIn(
                "frozen_420_540_590_supervisor_evidence_missing",
                verified["blocking_reasons"],
            )
            self.assertEqual(verified["database_writes"], 0)

            # 缩短时限只用于 fixture，即使存在收据也不能冒充冻结生产监督。
            first_attempt_id, first_artifact_id = supervised_attempts[0]
            fixture_supervisor = record_analysis_rib_supervisor_receipt(
                anchor_root,
                bindings=bindings,
                attempt_id=first_attempt_id,
                artifact_id=first_artifact_id,
                child_pid=7001,
                started_at_utc="2026-03-07T00:00:00Z",
                finished_at_utc="2026-03-07T00:00:01Z",
                returncode=0,
                observation_seconds=0.05,
                term_seconds=0.10,
                kill_seconds=0.25,
                observed_420=False,
                term_sent=False,
                kill_sent=False,
                reconciliation=None,
            )
            fixture_supervisor_payload = _load_json(
                anchor_root / fixture_supervisor["path"]
            )
            self.assertFalse(
                fixture_supervisor_payload["policy"][
                    "is_frozen_production_policy"
                ]
            )
            fixture_only = verify_analysis_rib_anchor_root(
                anchor_root,
                selection=selection,
                profile=profile,
                bindings=bindings,
            )
            self.assertFalse(fixture_only["execution_ready"])

            # 22 个单段 child outcome 各补一份冻结 420/540/590 监督收据。
            for index, (attempt_id, artifact_id) in enumerate(
                supervised_attempts
            ):
                record_analysis_rib_supervisor_receipt(
                    anchor_root,
                    bindings=bindings,
                    attempt_id=attempt_id,
                    artifact_id=artifact_id,
                    child_pid=8000 + index,
                    started_at_utc="2026-03-07T01:00:00Z",
                    finished_at_utc="2026-03-07T01:00:01Z",
                    returncode=0,
                    observation_seconds=420.0,
                    term_seconds=540.0,
                    kill_seconds=590.0,
                    observed_420=False,
                    term_sent=False,
                    kill_sent=False,
                    reconciliation=None,
                )
            ready = verify_analysis_rib_anchor_root(
                anchor_root,
                selection=selection,
                profile=profile,
                bindings=bindings,
            )
            self.assertTrue(ready["execution_ready"])
            self.assertEqual(ready["blocking_reasons"], [])
            self.assertEqual(ready["production_supervisor_receipt_count"], 22)
            self.assertEqual(
                ready["acceptance_state"],
                "anchor_verified_pending_overall_research_acceptance",
            )

            analysis_result = results[1]
            receipt = _load_json(anchor_root / analysis_result.anchor_receipt_path)
            state_shard = anchor_root / receipt["route_state"]["shard"]["path"]
            state_payload = json.loads(gzip.decompress(state_shard.read_bytes()))
            update_snapshot = build_update_boundary_snapshot(
                route_replay_state_from_payload(state_payload),
                collector_id="rrc25",
                boundary_at_utc=receipt["boundary_at_utc"],
            )
            reconciliation = reconcile_anchor_with_update_boundary(
                receipt, update_snapshot
            )
            self.assertEqual(reconciliation["status"], "consistent")
            self.assertEqual(
                reconciliation["update_curve_action"],
                "none_independent_reconciliation_only",
            )
            provenance_differs = {
                **update_snapshot,
                "route_state_semantic_sha256": "8" * 64,
            }
            projection_consistent = reconcile_anchor_with_update_boundary(
                receipt, provenance_differs
            )
            self.assertEqual(projection_consistent["status"], "consistent")
            self.assertFalse(
                projection_consistent["comparisons"][
                    "route_state_semantic_sha256_equal"
                ]
            )
            changed = {**update_snapshot, "projection_semantic_sha256": "9" * 64}
            mismatch = reconcile_anchor_with_update_boundary(receipt, changed)
            self.assertEqual(mismatch["status"], "mismatch")

            selection_path = root / "selection.json"
            profile_path = root / "profile.json"
            bindings_path = root / "bindings.json"
            _write_json(selection_path, selection)
            _write_json(profile_path, profile)
            _write_json(bindings_path, bindings)
            prior_candidate = compute_prior_journal_verification_candidate(
                journal_root, bindings=bindings
            )
            prior_root = root / "prior-verification-for-cli"
            prior_root.mkdir()
            prior_receipt = publish_prior_journal_verification_receipt(
                prior_root,
                candidate=prior_candidate,
                journal_root=journal_root,
                bindings=bindings,
                supervision={
                    "semantics": (
                        "independent_process_group_420_observe_540_term_590_kill_596_exit_v1"
                    ),
                    "policy": {
                        "observation_seconds": 420.0,
                        "term_seconds": 540.0,
                        "kill_seconds": 590.0,
                        "parent_exit_seconds_exclusive": 596.0,
                    },
                    "actions": {
                        "term_sent": False,
                        "kill_sent": False,
                        "child_reaped_within_parent_deadline": True,
                    },
                    "child_exit_code": 0,
                    "elapsed_seconds": 1.0,
                    "database_writes": 0,
                },
            )
            dry_run = subprocess.run(
                [
                    "python3",
                    str(CLI_PATH),
                    "dry-run",
                    "--selection",
                    str(selection_path),
                    "--profile",
                    str(profile_path),
                    "--bindings",
                    str(bindings_path),
                    "--full-window-journal-root",
                    str(journal_root),
                    "--prior-verification-receipt",
                    prior_receipt["path"],
                    "--compatible-mapping",
                    str(compatible_path),
                    "--revised-mapping",
                    str(revised_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            dry_payload = json.loads(dry_run.stdout)
            self.assertEqual(dry_payload["anchor_count"], 22)
            self.assertEqual(dry_payload["raw_files_opened"], 0)
            self.assertEqual(dry_payload["files_written"], 0)
            self.assertFalse(dry_payload["execution_ready"])

            verify = subprocess.run(
                [
                    "python3",
                    str(CLI_PATH),
                    "verify",
                    "--selection",
                    str(selection_path),
                    "--profile",
                    str(profile_path),
                    "--bindings",
                    str(bindings_path),
                    "--anchor-root",
                    str(anchor_root),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(verify.returncode, 0, verify.stderr)
            verify_payload = json.loads(verify.stdout)
            self.assertTrue(verify_payload["verified"])
            self.assertEqual(verify_payload["raw_files_opened"], 0)
            self.assertTrue(verify_payload["execution_ready"])
            self.assertEqual(
                verify_payload["acceptance_state"],
                "anchor_verified_pending_overall_research_acceptance",
            )

            raw_receipt = _load_json(
                anchor_root / results[0].anchor_receipt_path
            )
            shard_path = anchor_root / raw_receipt["route_event_shards"][0]["path"]
            damaged = bytearray(shard_path.read_bytes())
            damaged[-1] ^= 0x01
            shard_path.chmod(0o640)
            shard_path.write_bytes(damaged)
            with self.assertRaisesRegex(AnalysisRibAnchorError, "shard 文件 SHA256"):
                verify_analysis_rib_anchor_root(
                    anchor_root,
                    selection=selection,
                    profile=profile,
                    bindings=bindings,
                )


if __name__ == "__main__":
    unittest.main()
