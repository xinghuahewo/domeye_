from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.data_pipeline.route_event import (
    AsPathSegment,
    ParsedMrtRecord,
    ParsedRouteElement,
    artifact_id_v1,
)
from backend.data_pipeline.research.rrc25_country_outage import (
    full_window_finalize as finalizer,
    full_window_journal as journal,
    full_window_worker as worker,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    REVISED_MAPPING_SNAPSHOT_SCHEMA_VERSION,
    build_raw_retention_mapping_union,
    mapping_bundle_sha256,
    mapping_view_from_frozen_snapshot,
    mapping_view_from_revised_snapshot,
)
from backend.data_pipeline.research.rrc25_country_outage.country_mapping import (
    freeze_as_country_mapping,
)
from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (
    canonical_json,
)
from backend.data_pipeline.research.rrc25_country_outage.input_resolver import (
    resolve_research_inputs,
)
from backend.data_pipeline.research.rrc25_country_outage.profile import (
    profile_sha256,
)
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    build_research_route_event,
    seed_state_from_rib,
)
from dev.data_quality.rrc25_iran_bounded_pilot import build_code_identity
from dev.tests.test_rrc25_full_window_worker import (
    FakeStream,
    advancing_clock,
    element as update_element,
    update_frame,
)
from dev.tests.test_rrc25_iran_research_coordinator import (
    manifest_bundle,
    small_profile,
)


UTC = timezone.utc
RUN_ID = "research_run_v1_" + "8" * 24


def _semantic_sha(schema: str, value) -> str:
    return hashlib.sha256(
        canonical_json({"schema": schema, "value": value}).encode("utf-8")
    ).hexdigest()


def _receipt_fingerprint(schema: str, semantic: dict) -> str:
    return hashlib.sha256(
        canonical_json({"schema": schema, "receipt": semantic}).encode("utf-8")
    ).hexdigest()


def _revised_snapshot(compatible: dict, source_path: Path) -> dict:
    return {
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
            "path": str(source_path.resolve()),
            "sha256": hashlib.sha256(b"empty-revised-source").hexdigest(),
            "size_bytes": 1,
            "source_kind": "legacy_derived_official_delegate_missing_list",
            "generated_on": "2026-02-27",
            "upstream_artifact_state": "retained",
        },
        "temporal_policy": {
            "delegated_date_on_or_before": "20260227",
            "interval_role": "known_allocated_by_research_window_start_date",
            "excluded_after_cutoff_count": 0,
            "excluded_after_cutoff_asns": [],
        },
        "rows": [
            {
                "asn": 65003,
                "registry": "ripencc",
                "country_code": "IR",
                "resource_type": "asn",
                "delegated_date": "20260227",
                "status": "allocated",
                "range_start": 65003,
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
        "limitations_zh": ["端到端夹具不引入修订 ASN，仅验证双视图流程闭包。"],
    }


def _parser_attestation(artifact: dict) -> dict:
    start = datetime.fromisoformat(
        artifact["artifact_time_utc"].replace("Z", "+00:00")
    )
    end = (start + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    limits = {
        "max_artifact_count": 1,
        "max_compressed_bytes": artifact["size_bytes"],
        "max_physical_records": 100,
        "max_route_events": 100,
        "max_spool_bytes": 1024,
    }
    configuration = {
        "window_start_utc": artifact["artifact_time_utc"],
        "window_end_exclusive_utc": end,
        "pilot_limits": limits,
        "binary_execution_policy": "verified_in_process_source",
    }
    semantic = {
        "schema_version": "parser_attestation_v1",
        "parser_name": "fixture_native",
        "parser_version": "1.0.0",
        "parser_binary_sha256": hashlib.sha256(b"fixture-parser").hexdigest(),
        "adapter_name": "fixture_adapter",
        "adapter_version": "1.0.0",
        "adapter_source_sha256": hashlib.sha256(b"fixture-adapter").hexdigest(),
        "binary_execution_policy": "verified_in_process_source",
        "configuration": configuration,
        "configuration_sha256": hashlib.sha256(
            canonical_json(configuration).encode("utf-8")
        ).hexdigest(),
        "pilot_limits": limits,
        "security_boundary": "synthetic_records_no_raw_mrt",
    }
    return {
        **semantic,
        "attestation_fingerprint_sha256": hashlib.sha256(
            canonical_json(
                {
                    "schema": "parser_attestation_fingerprint_v1",
                    "attestation": semantic,
                }
            ).encode("utf-8")
        ).hexdigest(),
    }


def _seed_evidence(selection: dict):
    selected = selection["roles"]["state_seed_rib"]
    parsed = ParsedRouteElement(
        event_time_utc=selected["artifact_time_utc"],
        peer_ip="192.0.2.1",
        peer_asn=64500,
        action="rib_snapshot",
        prefix="203.0.113.0/24",
        afi_safi="ipv4_unicast",
        as_path=(AsPathSegment("as_sequence", (64500, 65001)),),
        quality_flags=(),
    )
    event = build_research_route_event(
        artifact_id=selected["artifact_id"],
        file_sha256=selected["file_sha256"],
        collector_id=selected["collector_id"],
        artifact_slot_utc=selected["artifact_time_utc"],
        record_ordinal=0,
        element_ordinal=0,
        element=parsed,
    )
    route_row = worker._event_row(event)
    raw_id = route_row["raw_record_ref_id"]
    raw_row = {
        "schema_version": worker.RAW_RECORD_REF_SHARD_SCHEMA_VERSION,
        "raw_record_ref_id": raw_id,
        "route_event_id": event.route_event_id,
        "artifact_id": selected["artifact_id"],
        "file_sha256": selected["file_sha256"],
        "artifact_slot_utc": selected["artifact_time_utc"],
        "record_ordinal": 0,
        "element_ordinal": 0,
        "record_offset": 0,
        "record_length": 100,
        "record_hash": "5" * 64,
        "raw_record_sha256": "5" * 64,
        "verification_status": "verified",
        "verification_basis": (
            "complete_artifact_single_pass_sha256_and_record_hash"
        ),
    }
    return event, route_row, raw_row


def _seed_attestation(
    *,
    selection: dict,
    bindings: dict,
    code_identity: dict,
    compatible,
    revised,
    compact: dict,
    route_state_payload: dict,
    route_row: dict,
    raw_row: dict,
    expected_vp_ids: list[str],
    vp_source_sha: str,
) -> dict:
    selected = selection["roles"]["state_seed_rib"]
    decompressed = {"size_bytes": 100, "sha256": "6" * 64}
    spool_semantic = {
        "schema_version": "rrc25-seed-spool-attestation/v1",
        "artifact_binding": {
            "artifact_id": selected["artifact_id"],
            "file_sha256": selected["file_sha256"],
            "compressed_size_bytes": selected["size_bytes"],
        },
        "decompressed": decompressed,
        "measurement": {
            "method": "full_streaming_gzip_decompression_sha256_v1",
            "measured_at_utc": "2026-02-27T16:00:00Z",
            "raw_read_pass_count": 1,
        },
    }
    spool = {
        **spool_semantic,
        "semantic_fingerprint_sha256": hashlib.sha256(
            canonical_json(spool_semantic).encode("utf-8")
        ).hexdigest(),
    }
    mapping_identity = finalizer._pilot_mapping_identity(compatible)
    raw_union = build_raw_retention_mapping_union((compatible, revised))
    raw_kind, raw_identity = finalizer._pilot_raw_retention_identity(
        raw_union,
        statistical_mapping=compatible,
        statistical_mapping_hash=mapping_identity,
    )
    checkpoint_fingerprint = "7" * 64
    checkpoint = {
        "schema_version": worker.FULL_SEED_CHECKPOINT_SCHEMA_VERSION,
        "file_sha256": "9" * 64,
        "size_bytes": 123,
        "checkpoint_fingerprint_sha256": checkpoint_fingerprint,
        "checkpoint_sequence": 1,
        "checkpoint_bytes_packaged": False,
        "packaging_limitation": (
            "checkpoint_identity_hash_only_not_checkpoint_bytes"
        ),
    }
    checkpoint_ref = {
        "path": "/not-packaged/full-seed.json.gz",
        "checkpoint_sequence": 1,
        "checkpoint_fingerprint_sha256": checkpoint_fingerprint,
    }
    spool_ref = {
        "path": "/retired/seed.spool",
        "sha256": decompressed["sha256"],
        "size_bytes": decompressed["size_bytes"],
        "stable_file_identity": {
            "st_dev": 1,
            "st_ino": 1,
            "st_size": decompressed["size_bytes"],
            "st_mtime_ns": 1,
            "st_ctime_ns": 1,
        },
    }
    attempt_semantic = {
        "schema_version": finalizer.SEED_RETIREMENT_ATTEMPT_SCHEMA,
        "operation": "seed_spool_retirement_raw_verification_attempt",
        "attempt_id": "fixture-attempt",
        "status": "raw_verification_reserved_outcome_unknown_until_success_receipt",
        "selection_id": selection["selection_id"],
        "checkpoint": checkpoint_ref,
        "spool": spool_ref,
        "compressed_raw_expected": {
            "path": "/raw/seed.gz",
            "relative_path": selected["relative_path"],
            "artifact_id": selected["artifact_id"],
            "file_sha256": selected["file_sha256"],
            "size_bytes": selected["size_bytes"],
        },
        "raw_accounting": {
            "reservation_policy": (
                "full_artifact_reserved_before_open_failed_or_crashed_attempts_still_count"
            ),
            "checkpoint_cumulative_new_raw_read_bytes": 11,
            "full_artifact_reserved_bytes": selected["size_bytes"],
            "cumulative_new_raw_read_bytes_after_reservation": 21,
        },
    }
    attempt = {
        **attempt_semantic,
        "receipt_fingerprint_sha256": _receipt_fingerprint(
            finalizer.SEED_RETIREMENT_ATTEMPT_FINGERPRINT_SCHEMA,
            attempt_semantic,
        ),
    }
    success_semantic = {
        "schema_version": finalizer.SEED_RETIREMENT_RECEIPT_SCHEMA,
        "operation": "seed_spool_retirement",
        "checkpoint": checkpoint_ref,
        "spool": spool_ref,
        "compressed_raw": {
            "path": "/raw/seed.gz",
            "relative_path": selected["relative_path"],
            "artifact_id": selected["artifact_id"],
            "sha256": selected["file_sha256"],
            "size_bytes": selected["size_bytes"],
            "hash_verified": True,
            "stable_file_identity": {
                "st_dev": 1,
                "st_ino": 2,
                "st_size": selected["size_bytes"],
                "st_mtime_ns": 1,
                "st_ctime_ns": 1,
            },
        },
        "raw_verification_attempt_receipt": {
            "path": "/receipts/attempt.json",
            "attempt_id": attempt["attempt_id"],
            "receipt_fingerprint_sha256": attempt[
                "receipt_fingerprint_sha256"
            ],
            "status": attempt["status"],
            "durable_before_raw_open": True,
        },
        "recoverable_by_rebuild_from_compressed_raw": True,
        "resource_accounting": {
            "checkpoint_cumulative_new_raw_read_bytes": 11,
            "retirement_verification_new_raw_read_bytes": selected["size_bytes"],
            "cumulative_new_raw_read_bytes_after_retirement_verification": 21,
            "reservation_policy": (
                "full_artifact_reserved_before_open_failed_or_crashed_attempts_still_count"
            ),
        },
    }
    success = {
        **success_semantic,
        "receipt_fingerprint_sha256": _receipt_fingerprint(
            finalizer.SEED_RETIREMENT_RECEIPT_FINGERPRINT_SCHEMA,
            success_semantic,
        ),
    }
    retirement = {
        "schema_version": "rrc25-seed-retirement-bootstrap-binding/v1",
        "success_receipt": success,
        "success_receipt_file_sha256": hashlib.sha256(
            (canonical_json(success) + "\n").encode("utf-8")
        ).hexdigest(),
        "raw_attempt_receipt": attempt,
        "raw_attempt_receipt_file_sha256": hashlib.sha256(
            (canonical_json(attempt) + "\n").encode("utf-8")
        ).hexdigest(),
        "spool_absence_verified": True,
        "compressed_raw_stable_identity_verified": True,
    }
    tracked = ["203.0.113.0/24"]
    window = selection["window"]
    prior_accounting_semantic = {
        "schema_version": "rrc25-native-probe-terminal-accounting/v1",
        "ledger_id": "probe_ledger_v1_end_to_end_fixture",
        "prepared_directory": "/prepared/end-to-end-fixture",
        "prepared_receipt_ref": {
            "path": "PREPARATION.json",
            "sha256": "1" * 64,
            "size_bytes": 1,
        },
        "prepared_bindings": dict(bindings),
        "selection_id": selection["selection_id"],
        "terminal_receipt_ref": {
            "path": "probe-ledger/GENESIS.json",
            "sha256": "2" * 64,
            "size_bytes": 1,
        },
        "terminal_receipt_kind": "imported_genesis",
        "attempt_count": 0,
        "outcome_count": 0,
        "prior_accounting": {"accounting_state": "fixture"},
        "initial_observed_lower_bound_new_raw_bytes": 1,
        "initial_reserved_upper_bound_new_raw_bytes": 1,
        "probe_observed_lower_bound_new_raw_bytes": 0,
        "probe_observed_upper_bound_new_raw_bytes": 0,
        "cumulative_reserved_new_raw_bytes": 1,
        "cumulative_semantics": "nonrefundable_reserved_upper_bound",
        "reservation_refund_policy": (
            "never_refund_even_on_failure_timeout_or_retry"
        ),
        "chain_refs_sha256": "3" * 64,
    }
    prior_raw_accounting = {
        **prior_accounting_semantic,
        "accounting_fingerprint_sha256": hashlib.sha256(
            canonical_json(
                {
                    "schema": "rrc25_native_probe_terminal_accounting_v1",
                    "accounting": prior_accounting_semantic,
                }
            ).encode("utf-8")
        ).hexdigest(),
    }
    seed_reservation_semantic = {
        "schema_version": "rrc25-seed-raw-reservation/v1",
        "ledger_id": prior_raw_accounting["ledger_id"],
        "prepared_directory": prior_raw_accounting["prepared_directory"],
        "prepared_bindings": dict(bindings),
        "selection_id": selection["selection_id"],
        "probe_terminal_accounting_fingerprint_sha256": (
            prior_raw_accounting["accounting_fingerprint_sha256"]
        ),
        "probe_terminal_receipt_ref": dict(
            prior_raw_accounting["terminal_receipt_ref"]
        ),
        "attempt_ref": {
            "path": "probe-ledger/seed-attempts/seed-attempt-0001.json",
            "sha256": "4" * 64,
            "size_bytes": 1,
        },
        "attempt_id": "seed_v1_" + "5" * 32,
        "sequence": 1,
        "seed_artifact": {
            field: selected[field]
            for field in (
                "artifact_id",
                "file_sha256",
                "size_bytes",
                "relative_path",
                "collector_id",
                "artifact_time_utc",
            )
        },
        "previous_seed_terminal_ref": None,
        "prior_cumulative_reserved_new_raw_bytes": 1,
        "reserved_new_raw_bytes": selected["size_bytes"],
        "cumulative_reserved_new_raw_bytes": 11,
        "reservation_refund_policy": (
            "never_refund_even_on_failure_timeout_or_retry"
        ),
    }
    seed_raw_reservation = {
        **seed_reservation_semantic,
        "reservation_fingerprint_sha256": hashlib.sha256(
            canonical_json(
                {
                    "schema": "rrc25_seed_raw_reservation_v1",
                    "reservation": seed_reservation_semantic,
                }
            ).encode("utf-8")
        ).hexdigest(),
    }
    semantic = {
        "schema_version": worker.SEED_BOOTSTRAP_ATTESTATION_SCHEMA_VERSION,
        "checkpoint": checkpoint,
        "checkpoint_bindings": {
            "code_identity_sha256": code_identity["identity_sha256"],
            "selection_id": selection["selection_id"],
            "selection_semantic_fingerprint_sha256": selection[
                "semantic_fingerprint_sha256"
            ],
            "mapping_fingerprint_sha256": mapping_identity,
            "raw_retention_mapping_kind": raw_kind,
            "raw_retention_mapping_fingerprint_sha256": raw_identity,
            "seed_spool_attestation_fingerprint_sha256": spool[
                "semantic_fingerprint_sha256"
            ],
            "pilot_start_utc": window["start_utc"],
            "pilot_end_exclusive_utc": window["end_exclusive_utc"],
        },
        "position": {
            "phase": "updates",
            "update_index": 0,
            "next_record_ordinal": 0,
            "boundary": "after_complete_physical_record",
        },
        "seed_progress": {
            "artifact_id": selected["artifact_id"],
            "file_sha256": selected["file_sha256"],
            "collector_id": selected["collector_id"],
            "artifact_time_utc": selected["artifact_time_utc"],
            "size_bytes": selected["size_bytes"],
            "next_record_ordinal": 1,
            "next_record_offset": decompressed["size_bytes"],
            "seed_parse_complete": True,
            "previous_record_boundary": {
                "record_ordinal": 0,
                "record_offset": 0,
                "record_length": decompressed["size_bytes"],
                "record_sha256": raw_row["raw_record_sha256"],
            },
            "peer_index_context": None,
        },
        "resume_policy": "worker_full_seed_record_offset_v2",
        "checkpoint_policy": {
            "planned_seed_checkpoint_seconds": 420.0,
            "worker_soft_stop_seconds": 540.0,
            "max_worker_runtime_seconds": 600.0,
            "active_root_retention_policy": (
                "immutable_accumulate_no_automatic_reclamation_v1"
            ),
            "automatic_deletion": False,
            "archive_before_reclamation_required": True,
            "archive_hash_and_receipt_required": True,
            "capacity_exhaustion_behavior": "fail_closed_before_publish",
        },
        "prior_raw_accounting": prior_raw_accounting,
        "seed_raw_reservation": seed_raw_reservation,
        "seed_artifact_ref": {
            "artifact_id": selected["artifact_id"],
            "file_sha256": selected["file_sha256"],
            "size_bytes": selected["size_bytes"],
        },
        "expected_vp_ids": expected_vp_ids,
        "expected_vp_ids_sha256": _semantic_sha(
            "rrc25_seed_expected_vp_ids_v1", expected_vp_ids
        ),
        "vp_population_source_sha256": vp_source_sha,
        "tracked_prefixes": tracked,
        "tracked_prefixes_sha256": _semantic_sha(
            "rrc25_seed_tracked_prefixes_v1", tracked
        ),
        "route_state_semantic_sha256": _semantic_sha(
            "rrc25_seed_route_state_v1", route_state_payload
        ),
        "seed_route_state": route_state_payload,
        "seed_route_events_semantic_sha256": _semantic_sha(
            "rrc25_seed_route_events_v1", [route_row]
        ),
        "seed_raw_record_refs_semantic_sha256": _semantic_sha(
            "rrc25_seed_raw_record_refs_v1", [raw_row]
        ),
        "gaps": [],
        "errors": [],
        "seed_spool_attestation": spool,
        "seed_parser": worker._seed_parser_attestation(
            code_identity["identity_sha256"]
        ),
        "seed_retirement": retirement,
        "initial_compact_state": compact,
        "initial_compact_state_semantic_sha256": _semantic_sha(
            "rrc25_full_window_initial_compact_state_v1", compact
        ),
        "offline_verification_scope": finalizer.SEED_OFFLINE_VERIFICATION_SCOPE,
    }
    return {
        **semantic,
        "attestation_fingerprint_sha256": _semantic_sha(
            worker.SEED_BOOTSTRAP_ATTESTATION_FINGERPRINT_SCHEMA, semantic
        ),
    }


class FullWindowEndToEndTests(unittest.TestCase):
    def test_synthetic_full_flow_finalize_verify_reproduce_and_accept(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            profile = small_profile()
            manifest, manifest_verification = manifest_bundle()
            selection = resolve_research_inputs(
                manifest, manifest_verification, profile
            )
            mapping_csv = base / "as-country.csv"
            mapping_csv.write_text(
                "asn,as_country\n65001,IR\n65002,IR\n65003,ZZ\n",
                encoding="utf-8",
            )
            compatible_snapshot = freeze_as_country_mapping(mapping_csv)
            revised_source = base / "empty-revised.txt"
            revised_source.write_text("\n", encoding="utf-8")
            revised_snapshot = _revised_snapshot(
                compatible_snapshot, revised_source
            )
            compatible = mapping_view_from_frozen_snapshot(compatible_snapshot)
            revised = mapping_view_from_revised_snapshot(
                revised_snapshot, compatible_snapshot
            )
            code_identity = dict(build_code_identity())
            bindings = {
                "profile_sha256": profile_sha256(profile),
                "input_selection_sha256": selection[
                    "semantic_fingerprint_sha256"
                ],
                "code_sha256": code_identity["identity_sha256"],
                "mapping_sha256": mapping_bundle_sha256(
                    compatible_snapshot, revised_snapshot
                ),
            }
            seed_event, seed_route_row, seed_raw_row = _seed_evidence(selection)
            seed_state = seed_state_from_rib((seed_event,))
            expected_vps = [seed_event.vp_id]
            checkpoint_file_sha = "9" * 64
            checkpoint_fingerprint = "7" * 64
            vp_source_sha = hashlib.sha256(
                canonical_json(
                    {
                        "schema": "rrc25_seed_vp_population_source_v1",
                        "checkpoint_file_sha256": checkpoint_file_sha,
                        "checkpoint_fingerprint_sha256": checkpoint_fingerprint,
                        "expected_vp_ids": expected_vps,
                    }
                ).encode("utf-8")
            ).hexdigest()
            compact = worker.initialize_compact_state_from_seed(
                seed_state,
                compatible_mapping=compatible,
                revised_mapping=revised,
                tracked_prefixes=("203.0.113.0/24",),
                expected_vp_ids=expected_vps,
                vp_population_source_sha256=vp_source_sha,
            )
            route_state_payload = worker.route_replay_state_to_payload(seed_state)
            seed_attestation = _seed_attestation(
                selection=selection,
                bindings=bindings,
                code_identity=code_identity,
                compatible=compatible,
                revised=revised,
                compact=compact,
                route_state_payload=route_state_payload,
                route_row=seed_route_row,
                raw_row=seed_raw_row,
                expected_vp_ids=expected_vps,
                vp_source_sha=vp_source_sha,
            )
            journal_root = base / "journal"
            head = journal.initialize_full_window_journal(
                journal_root,
                run_id=RUN_ID,
                bindings=bindings,
                total_artifacts=2,
                initial_compact_state=compact,
                preliminary_seed_read_bytes=1,
                seed_artifact_read_bytes=10,
                additional_pre_update_raw_read_bytes=10,
                bootstrap_bytes_per_second=1_000_000,
                genesis_shards=(
                    journal.ShardInput(
                        "seed_bootstrap_attestation", (seed_attestation,)
                    ),
                    journal.ShardInput("seed_route_events", (seed_route_row,)),
                    journal.ShardInput("seed_raw_record_refs", (seed_raw_row,)),
                ),
            )
            raw_union = build_raw_retention_mapping_union((compatible, revised))
            for index, artifact in enumerate(
                selection["roles"]["analysis_updates"]
            ):
                descriptor = worker.artifact_descriptor_from_manifest(
                    index, artifact
                )
                token = journal.begin_artifact_attempt(head, descriptor)
                raw = update_frame(index, peer_ip="192.0.2.1")
                records = (
                    ParsedMrtRecord(
                        0,
                        0,
                        raw,
                        (
                            update_element(
                                index,
                                peer_ip="192.0.2.1",
                                prefix="203.0.113.0/24",
                                as_path=(AsPathSegment("as_sequence", (64500, 65001)),),
                            ),
                        ),
                    ),
                )
                head = worker.run_one_update_artifact(
                    head,
                    token,
                    artifact_manifest_row=artifact,
                    compatible_mapping=compatible,
                    revised_mapping=revised,
                    raw_retention_membership=lambda asn: raw_union.decision_for(
                        asn
                    ).retain,
                    update_record_stream_factory=lambda _row, rows=records, item=artifact: FakeStream(
                        rows, item
                    ),
                    parser_attestation=_parser_attestation(artifact),
                    clock=advancing_clock(),
                ).head

            source_fact = json.loads(
                Path(
                    "config/research/iran-country-outage-source-fact-20260227.json"
                ).read_text(encoding="utf-8")
            )
            incident_policy = json.loads(
                Path(
                    "config/research/iran-rrc25-incident-episode-link-policy.json"
                ).read_text(encoding="utf-8")
            )
            claims = json.loads(
                Path("config/research/iran-rrc25-report-claims.json").read_text(
                    encoding="utf-8"
                )
            )
            claims = copy.deepcopy(claims)
            claims["study_id"] = profile["study_id"]
            common = {
                "journal_root": journal_root,
                "profile": profile,
                "source_fact_snapshot": source_fact,
                "incident_policy": incident_policy,
                "compatible_mapping_snapshot": compatible_snapshot,
                "revised_mapping_snapshot": revised_snapshot,
                "code_identity": code_identity,
                "input_selection": selection,
                "claim_inventory": claims,
                "bindings": bindings,
            }
            verified_inputs = finalizer.verify_frozen_full_window_inputs(**common)
            ancestry_inventory = finalizer._journal_ancestry_refs(verified_inputs)
            with (
                patch.object(
                    finalizer,
                    "_journal_ancestry_refs",
                    side_effect=AssertionError("纯业务构建不得读取 journal ancestry"),
                ),
                patch.object(
                    finalizer,
                    "_iter_shard_rows",
                    side_effect=AssertionError("纯业务构建不得重读 observation/shard"),
                ),
                patch.object(
                    finalizer,
                    "_read_stable_regular",
                    side_effect=AssertionError("纯业务构建不得读取文件"),
                ),
                patch.object(
                    finalizer,
                    "_publish_package",
                    side_effect=AssertionError("纯业务构建不得发布文件"),
                ),
            ):
                business = finalizer.derive_full_window_business_outputs(
                    verified_inputs,
                    journal_ancestry_inventory=ancestry_inventory,
                )
            self.assertEqual(
                set(business.object_files),
                {
                    "metadata/finalization.json",
                    "frozen/profile.json",
                    "frozen/source-fact.json",
                    "frozen/incident-policy.json",
                    "frozen/compatible-mapping.json",
                    "frozen/revised-mapping.json",
                    "frozen/code-identity.json",
                    "frozen/input-selection.json",
                    "frozen/claim-inventory.json",
                    "frozen/bindings.json",
                    "data/compatible-baseline.json",
                    "data/revised-baseline.json",
                    "reconciliation.json",
                    "quality-and-accounting.json",
                },
            )
            self.assertEqual(
                set(business.sequence_files),
                {
                    "data/compatible-country-samples.jsonl.gz",
                    "data/revised-country-samples.jsonl.gz",
                    "data/compatible-sample-measurement-semantics.jsonl.gz",
                    "data/revised-sample-measurement-semantics.jsonl.gz",
                    "data/compatible-episodes.jsonl.gz",
                    "data/compatible-waves.jsonl.gz",
                    "data/revised-episodes.jsonl.gz",
                    "data/revised-waves.jsonl.gz",
                    "data/compatible-episode-as.jsonl.gz",
                    "data/compatible-episode-as-measurement-semantics.jsonl.gz",
                    "data/compatible-prefix-impact.jsonl.gz",
                    "data/revised-episode-as.jsonl.gz",
                    "data/revised-episode-as-measurement-semantics.jsonl.gz",
                    "data/revised-prefix-impact.jsonl.gz",
                    "data/incident-episode-mappings.jsonl.gz",
                    "evidence/research-evidence-packages.jsonl.gz",
                },
            )
            report_path = "报告/RRC25伊朗国家路由中断事件复算与对账报告.md"
            self.assertEqual(set(business.byte_files), {report_path})
            self.assertIn(
                "RRC25 伊朗国家路由中断事件复算与对账报告",
                business.byte_files[report_path][1].decode("utf-8"),
            )
            self.assertEqual(
                business.semantic_core_sha256,
                finalizer._canonical_hash(business.semantic_core),
            )
            self.assertEqual(
                business.counts,
                business.object_files["metadata/finalization.json"][1]["counts"],
            )
            first_root = base / "package-a"
            first = finalizer.finalize_full_window_package(
                output_root=first_root, **common
            )
            self.assertEqual(
                first.semantic_core_sha256,
                business.semantic_core_sha256,
            )
            self.assertEqual(
                (first.root / report_path).read_bytes(),
                business.byte_files[report_path][1],
            )
            with gzip.open(
                first.root / "evidence/research-evidence-packages.jsonl.gz",
                "rt",
                encoding="utf-8",
            ) as stream:
                evidence_package = json.loads(next(stream))
            bundle = evidence_package["bundles"][0]
            self.assertEqual(
                bundle["data_snapshot"]["window_start"],
                "2026-02-27T01:12:32Z",
            )
            self.assertEqual(
                bundle["data_snapshot"]["raw_source_status"], "partial"
            )
            parameters = {
                row["name"]: row["value"]
                for row in bundle["reproducibility"]["parameters"]
            }
            self.assertEqual(
                parameters["research_window_start_utc"],
                "2026-02-27T16:00:00Z",
            )
            self.assertEqual(
                parameters["metric_window_policy"],
                "strict_research_profile_half_open_window",
            )
            self.assertTrue(
                any(
                    "[2026-02-27T01:12:32Z,2026-02-27T16:00:00Z)"
                    in limitation
                    for limitation in evidence_package["limitations_zh"]
                )
            )
            verified_first = finalizer.verify_finalized_package(first.root)
            self.assertEqual(
                verified_first["acceptance_state"], "not_accepted"
            )
            second_root = base / "package-b"
            acceptance_path = base / "accepted.json"
            acceptance = finalizer.reproduce_semantics(
                reference_package_root=first.root,
                output_root=second_root,
                acceptance_receipt_path=acceptance_path,
                **common,
            )
            self.assertEqual(acceptance["acceptance_state"], "accepted")
            finalizer.verify_reproduction_acceptance_receipt(acceptance_path)
            with self.assertRaises(FileExistsError):
                finalizer.reproduce_semantics(
                    reference_package_root=first.root,
                    output_root=base / "package-c",
                    acceptance_receipt_path=acceptance_path,
                    **common,
                )


if __name__ == "__main__":
    unittest.main()
