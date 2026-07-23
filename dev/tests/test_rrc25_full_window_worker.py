from datetime import datetime, timezone
import gzip
import hashlib
import ipaddress
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from backend.data_pipeline.route_event import (
    AsPathSegment,
    NativeUpdateRecordStreamFactory,
    ParsedMrtRecord,
    ParsedRouteElement,
    artifact_id_v1,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    MAPPED,
    UNKNOWN_MAPPING,
    MappingAssignment,
    build_country_mapping_view,
)
from backend.data_pipeline.research.rrc25_country_outage import (
    full_window_journal as journal_module,
    full_window_worker as worker_module,
)
from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import canonical_json
from backend.data_pipeline.research.rrc25_country_outage.full_window_journal import (
    begin_artifact_attempt,
    cumulative_reserved_raw_bytes,
    initialize_full_window_journal,
)
from backend.data_pipeline.research.rrc25_country_outage.full_window_worker import (
    FullWindowWorkerError,
    VerifiedSeedBootstrap,
    artifact_descriptor_from_manifest,
    compact_state_from_payload,
    derive_artifact_boundary,
    initialize_compact_state_from_seed,
    initialize_journal_from_verified_seed,
    run_one_update_artifact,
)
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    build_research_route_event,
    seed_state_from_rib,
)


UTC = timezone.utc
RUN_ID = "research_run_v1_" + "2" * 24
BINDINGS = {
    "profile_sha256": hashlib.sha256(b"profile-worker").hexdigest(),
    "input_selection_sha256": hashlib.sha256(b"selection-worker").hexdigest(),
    "code_sha256": hashlib.sha256(b"code-worker").hexdigest(),
    "mapping_sha256": hashlib.sha256(b"mapping-worker").hexdigest(),
}


def mapping():
    return build_country_mapping_view(
        (
            MappingAssignment(65001, ("IR",), MAPPED),
            MappingAssignment(65002, ("IR",), MAPPED),
            MappingAssignment(65003, (), UNKNOWN_MAPPING),
            MappingAssignment(65005, ("US",), MAPPED),
        ),
        view="compatible",
        target_country="IR",
        source_sha256=hashlib.sha256(b"as-map").hexdigest(),
        source_ref="asmap_v1_worker_fixture",
    )


def revised_mapping():
    return build_country_mapping_view(
        (
            MappingAssignment(65001, ("IR",), MAPPED),
            MappingAssignment(65002, ("IR",), MAPPED),
            MappingAssignment(65003, ("IR",), MAPPED),
            MappingAssignment(65005, ("US",), MAPPED),
        ),
        view="revised",
        target_country="IR",
        source_sha256=hashlib.sha256(b"as-map-revised").hexdigest(),
        source_ref="asmap_v1_worker_fixture_revised",
    )


def path(*asns):
    return (AsPathSegment("as_sequence", tuple(asns)),)


def seed_state():
    file_sha = hashlib.sha256(b"seed-worker").hexdigest()
    element = ParsedRouteElement(
        event_time_utc="2026-02-27T15:59:00Z",
        peer_ip="192.0.2.1",
        peer_asn=64500,
        action="rib_snapshot",
        prefix="203.0.113.0/24",
        afi_safi="ipv4_unicast",
        as_path=path(64500, 65001),
        quality_flags=(),
    )
    event = build_research_route_event(
        artifact_id=artifact_id_v1(file_sha),
        file_sha256=file_sha,
        collector_id="rrc25",
        artifact_slot_utc="2026-02-27T15:55:00Z",
        record_ordinal=0,
        element_ordinal=0,
        element=element,
    )
    return seed_state_from_rib((event,)), event.vp_id


def overlapping_seed_state():
    file_sha = hashlib.sha256(b"seed-worker-overlap").hexdigest()
    events = []
    for ordinal, prefix in enumerate(("203.0.113.0/24", "203.0.113.0/25")):
        seed_element = ParsedRouteElement(
            event_time_utc="2026-02-27T15:59:00Z",
            peer_ip="192.0.2.1",
            peer_asn=64500,
            action="rib_snapshot",
            prefix=prefix,
            afi_safi="ipv4_unicast",
            as_path=path(64500, 65001),
            quality_flags=(),
        )
        events.append(
            build_research_route_event(
                artifact_id=artifact_id_v1(file_sha),
                file_sha256=file_sha,
                collector_id="rrc25",
                artifact_slot_utc="2026-02-27T15:55:00Z",
                record_ordinal=ordinal,
                element_ordinal=0,
                element=seed_element,
            )
        )
    return seed_state_from_rib(tuple(events)), events[0].vp_id


def manifest(index, *, size=1000):
    file_sha = hashlib.sha256(f"worker-update-{index}".encode("ascii")).hexdigest()
    minute = index * 5
    return {
        "artifact_id": artifact_id_v1(file_sha),
        "file_sha256": file_sha,
        "collector_id": "rrc25",
        "artifact_type": "update",
        "artifact_time_utc": f"2026-02-27T16:{minute:02d}:00Z",
        "relative_path": f"rrc25/updates-{index}.gz",
        "compression": "gz",
        "size_bytes": size,
    }


def update_frame(slot_index, *, peer_ip, peer_asn=64500):
    timestamp = int(
        datetime(2026, 2, 27, 16, slot_index * 5, 1, tzinfo=UTC).timestamp()
    )
    message_body = b"\x00\x00\x00\x00"
    message = b"\xff" * 16 + struct.pack("!HB", 23, 2) + message_body
    identity = struct.pack("!IIHH", peer_asn, 64496, 0, 1)
    payload = (
        identity
        + ipaddress.ip_address(peer_ip).packed
        + ipaddress.ip_address("192.0.2.254").packed
        + message
    )
    return struct.pack("!IHHI", timestamp, 16, 4, len(payload)) + payload


def native_announce_frame(slot_index, *, peer_ip, peer_asn=64500):
    timestamp = int(
        datetime(2026, 2, 27, 16, slot_index * 5, 1, tzinfo=UTC).timestamp()
    )
    attributes = (
        b"\x40\x01\x01\x00"  # ORIGIN IGP
        + b"\x40\x02\x0a\x02\x02"
        + struct.pack("!II", peer_asn, 65001)  # four-octet AS_SEQUENCE
        + b"\x40\x03\x04"
        + ipaddress.ip_address("192.0.2.254").packed  # NEXT_HOP
    )
    # withdrawn-len=0, required attributes, IPv4 NLRI 203.0.113.0/24.
    message_body = (
        b"\x00\x00"
        + struct.pack("!H", len(attributes))
        + attributes
        + b"\x18\xcb\x00\x71"
    )
    message = (
        b"\xff" * 16
        + struct.pack("!HB", 19 + len(message_body), 2)
        + message_body
    )
    identity = struct.pack("!IIHH", peer_asn, 64496, 0, 1)
    payload = (
        identity
        + ipaddress.ip_address(peer_ip).packed
        + ipaddress.ip_address("192.0.2.254").packed
        + message
    )
    return struct.pack("!IHHI", timestamp, 16, 4, len(payload)) + payload


def state_change_frame(
    slot_index, *, peer_ip="192.0.2.1", peer_asn=64500, old_state=6, new_state=1
):
    timestamp = int(
        datetime(2026, 2, 27, 16, slot_index * 5, 1, tzinfo=UTC).timestamp()
    )
    identity = struct.pack("!IIHH", peer_asn, 64496, 0, 1)
    payload = (
        identity
        + ipaddress.ip_address(peer_ip).packed
        + ipaddress.ip_address("192.0.2.254").packed
        + struct.pack("!HH", old_state, new_state)
    )
    return struct.pack("!IHHI", timestamp, 16, 5, len(payload)) + payload


def element(slot_index, *, peer_ip, prefix, as_path, action="announce"):
    return ParsedRouteElement(
        event_time_utc=f"2026-02-27T16:{slot_index * 5:02d}:01Z",
        peer_ip=peer_ip,
        peer_asn=64500,
        action=action,
        prefix=prefix,
        afi_safi="ipv4_unicast",
        as_path=as_path,
        quality_flags=(),
    )


class FakeStream:
    def __init__(self, records, artifact_row, *, status="complete"):
        self._records = tuple(records)
        self.statistics = {
            "status": status,
            "compressed_file_sha256": artifact_row["file_sha256"],
            "compressed_size_bytes": artifact_row["size_bytes"],
            "compressed_bytes_read_observed": artifact_row["size_bytes"],
            "compressed_read_passes": 1,
            "peak_spool_bytes": 0,
        }

    def __iter__(self):
        return iter(self._records)


def read_single_row(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    if len(rows) != 1:
        raise AssertionError("expected one row")
    return rows[0]


def read_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def country_view(path, view):
    rows = [row for row in read_rows(path) if row["mapping_view"] == view]
    if len(rows) != 1:
        raise AssertionError(f"expected exactly one {view} row")
    return rows[0]


def advancing_clock(step=0.1):
    value = [0.0]

    def clock():
        current = value[0]
        value[0] += step
        return current

    return clock


def parser_attestation():
    semantic = {
        "schema_version": "parser_attestation_v1",
        "parser_name": "fixture_native",
        "parser_version": "1.0.0",
        "parser_binary_sha256": hashlib.sha256(b"python-fixture").hexdigest(),
        "adapter_name": "fixture_adapter",
        "adapter_version": "1.0.0",
        "adapter_source_sha256": hashlib.sha256(b"adapter-fixture").hexdigest(),
        "binary_execution_policy": "verified_in_process_source",
        "configuration": {"fixture": True},
        "configuration_sha256": hashlib.sha256(b"configuration-fixture").hexdigest(),
        "pilot_limits": {"max_artifact_count": 1},
        "security_boundary": "fixture",
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


class FullWindowWorkerTests(unittest.TestCase):
    def test_full_seed_loader_carries_verified_seed_reservation(self):
        seed_artifact = {
            "artifact_id": "art_v1_seed_loader_fixture",
            "file_sha256": "a" * 64,
            "size_bytes": 50,
            "relative_path": "rrc25/bview.fixture.gz",
            "collector_id": "rrc25",
            "artifact_time_utc": "2026-02-27T16:00:00Z",
        }
        selection_id = "rsel_v1_seed_loader_fixture"
        selection_sha = "b" * 64
        code_sha = "c" * 64
        selection = {
            "selection_id": selection_id,
            "semantic_fingerprint_sha256": selection_sha,
            "roles": {"state_seed_rib": seed_artifact},
        }
        bindings = {
            "profile_sha256": "d" * 64,
            "input_selection_sha256": selection_sha,
            "code_sha256": code_sha,
            "mapping_sha256": "e" * 64,
        }
        probe_semantic = {
            "schema_version": "rrc25-native-probe-terminal-accounting/v1",
            "ledger_id": "probe_ledger_v1_loader_fixture",
            "prepared_directory": "/prepared/loader-fixture",
            "prepared_receipt_ref": {
                "path": "PREPARATION.json",
                "sha256": "1" * 64,
                "size_bytes": 1,
            },
            "prepared_bindings": bindings,
            "selection_id": selection_id,
            "terminal_receipt_ref": {
                "path": "probe-ledger/GENESIS.json",
                "sha256": "2" * 64,
                "size_bytes": 1,
            },
            "terminal_receipt_kind": "imported_genesis",
            "attempt_count": 0,
            "outcome_count": 0,
            "prior_accounting": {"accounting_state": "fixture"},
            "initial_observed_lower_bound_new_raw_bytes": 50,
            "initial_reserved_upper_bound_new_raw_bytes": 100,
            "probe_observed_lower_bound_new_raw_bytes": 0,
            "probe_observed_upper_bound_new_raw_bytes": 0,
            "cumulative_reserved_new_raw_bytes": 100,
            "cumulative_semantics": "nonrefundable_reserved_upper_bound",
            "reservation_refund_policy": (
                "never_refund_even_on_failure_timeout_or_retry"
            ),
            "chain_refs_sha256": "3" * 64,
        }
        probe = {
            **probe_semantic,
            "accounting_fingerprint_sha256": hashlib.sha256(
                canonical_json(
                    {
                        "schema": "rrc25_native_probe_terminal_accounting_v1",
                        "accounting": probe_semantic,
                    }
                ).encode("utf-8")
            ).hexdigest(),
        }
        reservation_semantic = {
            "schema_version": "rrc25-seed-raw-reservation/v1",
            "ledger_id": probe["ledger_id"],
            "prepared_directory": probe["prepared_directory"],
            "prepared_bindings": bindings,
            "selection_id": selection_id,
            "probe_terminal_accounting_fingerprint_sha256": probe[
                "accounting_fingerprint_sha256"
            ],
            "probe_terminal_receipt_ref": probe["terminal_receipt_ref"],
            "attempt_ref": {
                "path": "probe-ledger/seed-attempts/seed-attempt-000001.json",
                "sha256": "4" * 64,
                "size_bytes": 1,
            },
            "attempt_id": "seed_v1_" + "5" * 32,
            "sequence": 1,
            "seed_artifact": seed_artifact,
            "previous_seed_terminal_ref": None,
            "prior_cumulative_reserved_new_raw_bytes": 100,
            "reserved_new_raw_bytes": 50,
            "cumulative_reserved_new_raw_bytes": 150,
            "reservation_refund_policy": (
                "never_refund_even_on_failure_timeout_or_retry"
            ),
        }
        reservation = {
            **reservation_semantic,
            "reservation_fingerprint_sha256": hashlib.sha256(
                canonical_json(
                    {
                        "schema": "rrc25_seed_raw_reservation_v1",
                        "reservation": reservation_semantic,
                    }
                ).encode("utf-8")
            ).hexdigest(),
        }
        spool_semantic = {
            "schema_version": "rrc25-seed-spool-attestation/v1",
            "artifact_binding": {
                "artifact_id": seed_artifact["artifact_id"],
                "file_sha256": seed_artifact["file_sha256"],
                "compressed_size_bytes": 50,
            },
            "decompressed": {"size_bytes": 100, "sha256": "6" * 64},
            "measurement": {
                "method": "full_streaming_gzip_decompression_sha256_v1",
                "measured_at_utc": "2026-07-22T10:11:14Z",
                "raw_read_pass_count": 1,
            },
        }
        spool_attestation = {
            **spool_semantic,
            "semantic_fingerprint_sha256": hashlib.sha256(
                canonical_json(spool_semantic).encode("utf-8")
            ).hexdigest(),
        }
        empty_state = seed_state_from_rib(())
        state_payload = worker_module.route_replay_state_to_payload(empty_state)
        payload = {
            "schema_version": worker_module.FULL_SEED_CHECKPOINT_SCHEMA_VERSION,
            "code_identity_sha256": code_sha,
            "selection_id": selection_id,
            "selection_semantic_fingerprint_sha256": selection_sha,
            "mapping_fingerprint_sha256": "7" * 64,
            "raw_retention_mapping_kind": "fixture",
            "raw_retention_mapping_fingerprint_sha256": "8" * 64,
            "seed_spool_attestation_fingerprint_sha256": spool_attestation[
                "semantic_fingerprint_sha256"
            ],
            "pilot_start_utc": "2026-02-27T16:00:00Z",
            "pilot_end_exclusive_utc": "2026-02-27T16:05:00Z",
            "checkpoint_sequence": 1,
            "position": {"phase": "updates"},
            "seed_progress": {
                "artifact_id": seed_artifact["artifact_id"],
                "file_sha256": seed_artifact["file_sha256"],
                "size_bytes": 50,
            },
            "state": state_payload,
            "tracked_prefixes": [],
            "observed_vp_ids": [],
            "resources": {
                "prior_new_raw_read_bytes": 100,
                "prior_raw_accounting": probe,
                "seed_raw_reservation": reservation,
                "new_raw_read_bytes": 150,
            },
            "route_events": [],
            "raw_audits": [],
            "resume_policy": "worker_full_seed_record_offset_v2",
            "checkpoint_policy": {},
            "gaps": [],
            "errors": [],
        }
        semantic = dict(payload)
        payload["checkpoint_fingerprint_sha256"] = hashlib.sha256(
            canonical_json(
                {
                    "schema": worker_module.FULL_SEED_CHECKPOINT_FINGERPRINT_SCHEMA,
                    "checkpoint": semantic,
                }
            ).encode("utf-8")
        ).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "checkpoint.json.gz"
            checkpoint.write_bytes(b"fixture")
            identity = worker_module._file_identity(checkpoint.lstat())
            with mock.patch.object(
                worker_module,
                "verify_full_seed_checkpoint",
                return_value={
                    "checkpoint_fingerprint_sha256": payload[
                        "checkpoint_fingerprint_sha256"
                    ]
                },
            ), mock.patch.object(
                worker_module,
                "_read_stable_full_seed_checkpoint",
                return_value=(payload, "9" * 64, 7, identity),
            ):
                bootstrap = worker_module.load_verified_full_seed_bootstrap(
                    checkpoint,
                    selection=selection,
                    country_mapping=mapping(),
                    raw_retention_mapping=None,
                    seed_spool_attestation=spool_attestation,
                    window_end_exclusive_utc="2026-02-27T16:05:00Z",
                    code_identity_sha256=code_sha,
                )
        self.assertEqual(bootstrap.prior_raw_read_bytes, 100)
        self.assertEqual(bootstrap.seed_artifact_read_bytes, 50)
        self.assertEqual(
            bootstrap.checkpoint_bootstrap_context["seed_raw_reservation"],
            reservation,
        )

    def test_native_stream_commits_single_pass_proof_through_worker_and_journal(self):
        seed, seed_vp = seed_state()
        compact = initialize_compact_state_from_seed(
            seed,
            compatible_mapping=mapping(),
            revised_mapping=revised_mapping(),
            tracked_prefixes=("203.0.113.0/24",),
            expected_vp_ids=(seed_vp,),
            vp_population_source_sha256=hashlib.sha256(b"peer-index-native").hexdigest(),
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            raw_root = base / "raw"
            collector = raw_root / "rrc25"
            collector.mkdir(parents=True)
            compressed = gzip.compress(
                native_announce_frame(0, peer_ip="192.0.2.1"),
                compresslevel=1,
                mtime=0,
            )
            raw_path = collector / "updates-0.gz"
            raw_path.write_bytes(compressed)
            file_sha = hashlib.sha256(compressed).hexdigest()
            row = {
                **manifest(0, size=len(compressed)),
                "artifact_id": artifact_id_v1(file_sha),
                "file_sha256": file_sha,
            }
            journal_root = base / "journal"
            head = initialize_full_window_journal(
                journal_root,
                run_id=RUN_ID,
                bindings=BINDINGS,
                total_artifacts=1,
                initial_compact_state=compact,
                preliminary_seed_read_bytes=0,
                seed_artifact_read_bytes=0,
                additional_pre_update_raw_read_bytes=0,
                bootstrap_bytes_per_second=1_000_000,
            )
            factory = NativeUpdateRecordStreamFactory(
                raw_root,
                (row,),
                data_profile={
                    "window_start_utc": "2026-02-27T16:00:00Z",
                    "window_end_exclusive_utc": "2026-02-27T16:05:00Z",
                },
                pilot_limits={
                    "max_artifact_count": 1,
                    "max_compressed_bytes": len(compressed),
                    "max_physical_records": 100,
                    "max_route_events": 100,
                    "max_spool_bytes": 1024 * 1024,
                },
            )
            token = begin_artifact_attempt(
                head, artifact_descriptor_from_manifest(0, row)
            )
            committed = run_one_update_artifact(
                head,
                token,
                artifact_manifest_row=row,
                compatible_mapping=mapping(),
                revised_mapping=revised_mapping(),
                raw_retention_membership=lambda _asn: None,
                update_record_stream_factory=factory,
                parser_attestation=factory.parser_attestation,
                clock=advancing_clock(),
            )
            self.assertEqual(committed.head.next_artifact_index, 1)
            outcome = json.loads(
                (journal_root / committed.outcome_ref["path"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(outcome["outcome"], "complete_single_pass")
            self.assertEqual(
                outcome["proof"]["compressed_bytes_read_observed"],
                len(compressed),
            )

    def test_stable_seed_reader_accepts_deterministic_gzip_and_hashes_compressed_bytes(self):
        payload = {"schema_version": "fixture/v1", "rows": [1, 2, 3]}
        decoded = (canonical_json(payload) + "\n").encode("utf-8")
        compressed = gzip.compress(decoded, compresslevel=9, mtime=0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seed.json.gz"
            path.write_bytes(compressed)
            restored, file_sha, size, _identity = (
                worker_module._read_stable_full_seed_checkpoint(path)
            )
            self.assertEqual(restored, payload)
            self.assertEqual(file_sha, hashlib.sha256(compressed).hexdigest())
            self.assertEqual(size, len(compressed))
            path.write_bytes(compressed + b"trailing-member-forbidden")
            with self.assertRaisesRegex(FullWindowWorkerError, "单成员完整闭合"):
                worker_module._read_stable_full_seed_checkpoint(path)

    def test_verified_seed_initialization_publishes_offline_bootstrap_attestation(self):
        seed, seed_vp = seed_state()
        checkpoint_file_sha = hashlib.sha256(b"checkpoint-file").hexdigest()
        checkpoint_fingerprint = hashlib.sha256(b"checkpoint-semantic").hexdigest()
        expected_vps = (seed_vp,)
        vp_source_sha = hashlib.sha256(
            canonical_json(
                {
                    "schema": "rrc25_seed_vp_population_source_v1",
                    "checkpoint_file_sha256": checkpoint_file_sha,
                    "checkpoint_fingerprint_sha256": checkpoint_fingerprint,
                    "expected_vp_ids": list(expected_vps),
                }
            ).encode("utf-8")
        ).hexdigest()
        seed_file_sha = hashlib.sha256(b"seed-raw").hexdigest()
        seed_artifact = {
            "artifact_id": artifact_id_v1(seed_file_sha),
            "file_sha256": seed_file_sha,
            "size_bytes": 50,
        }
        prior_accounting_semantic = {
            "schema_version": "rrc25-native-probe-terminal-accounting/v1",
            "ledger_id": "probe_ledger_v1_worker_fixture",
            "prepared_directory": "/prepared/fixture",
            "prepared_receipt_ref": {
                "path": "PREPARATION.json",
                "sha256": "a" * 64,
                "size_bytes": 1,
            },
            "prepared_bindings": dict(BINDINGS),
            "selection_id": "rsel_v1_worker_fixture",
            "terminal_receipt_ref": {
                "path": "probe-ledger/GENESIS.json",
                "sha256": "b" * 64,
                "size_bytes": 1,
            },
            "terminal_receipt_kind": "imported_genesis",
            "attempt_count": 0,
            "outcome_count": 0,
            "prior_accounting": {"accounting_state": "fixture"},
            "initial_observed_lower_bound_new_raw_bytes": 50,
            "initial_reserved_upper_bound_new_raw_bytes": 100,
            "probe_observed_lower_bound_new_raw_bytes": 0,
            "probe_observed_upper_bound_new_raw_bytes": 0,
            "cumulative_reserved_new_raw_bytes": 100,
            "cumulative_semantics": "nonrefundable_reserved_upper_bound",
            "reservation_refund_policy": (
                "never_refund_even_on_failure_timeout_or_retry"
            ),
            "chain_refs_sha256": "c" * 64,
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
        seed_route_state_payload = worker_module.route_replay_state_to_payload(seed)
        bootstrap = VerifiedSeedBootstrap(
            checkpoint_path=Path("/not-packaged/full-seed.json.gz"),
            checkpoint_file_sha256=checkpoint_file_sha,
            checkpoint_size_bytes=123,
            checkpoint_fingerprint_sha256=checkpoint_fingerprint,
            checkpoint_sequence=7,
            route_state=seed,
            tracked_prefixes=("203.0.113.0/24",),
            expected_vp_ids=expected_vps,
            prior_raw_read_bytes=100,
            seed_artifact_read_bytes=50,
            seed_artifact_ref=seed_artifact,
            seed_route_event_rows=(),
            seed_raw_record_ref_rows=(),
            checkpoint_bootstrap_context={
                "checkpoint": {
                    "schema_version": "rrc25-bounded-pilot-worker-full-seed/v2",
                    "file_sha256": checkpoint_file_sha,
                    "size_bytes": 123,
                    "checkpoint_fingerprint_sha256": checkpoint_fingerprint,
                    "checkpoint_sequence": 7,
                    "checkpoint_bytes_packaged": False,
                    "packaging_limitation": "checkpoint_identity_hash_only_not_checkpoint_bytes",
                },
                "prior_raw_accounting": prior_raw_accounting,
                "vp_population_source_sha256": vp_source_sha,
                "expected_vp_ids": list(expected_vps),
                "expected_vp_ids_sha256": hashlib.sha256(b"vps").hexdigest(),
                "tracked_prefixes": ["203.0.113.0/24"],
                "tracked_prefixes_sha256": hashlib.sha256(b"prefixes").hexdigest(),
                "seed_route_state": seed_route_state_payload,
                "route_state_semantic_sha256": worker_module._semantic_sha256(
                    "rrc25_seed_route_state_v1", seed_route_state_payload
                ),
                "seed_route_events_semantic_sha256": hashlib.sha256(b"events").hexdigest(),
                "seed_raw_record_refs_semantic_sha256": hashlib.sha256(b"raw").hexdigest(),
            },
            seed_spool_attestation={"schema_version": "fixture-spool/v1"},
            seed_parser_attestation={"schema_version": "fixture-parser/v1"},
        )
        checkpoint_ref = {
            "path": str(bootstrap.checkpoint_path),
            "checkpoint_sequence": 7,
            "checkpoint_fingerprint_sha256": checkpoint_fingerprint,
        }
        attempt = {
            "schema_version": "rrc25-seed-spool-retirement-raw-attempt-receipt/v1",
            "operation": "seed_spool_retirement_raw_verification_attempt",
            "attempt_id": "attempt-fixture",
            "status": "complete",
            "receipt_fingerprint_sha256": hashlib.sha256(b"attempt").hexdigest(),
            "raw_accounting": {
                "checkpoint_cumulative_new_raw_read_bytes": 150,
                "cumulative_new_raw_read_bytes_after_reservation": 200,
            },
        }
        receipt = {
            "schema_version": "rrc25-seed-spool-retirement-receipt/v2",
            "operation": "seed_spool_retirement",
            "checkpoint": checkpoint_ref,
            "compressed_raw": {
                "artifact_id": seed_artifact["artifact_id"],
                "sha256": seed_file_sha,
                "size_bytes": 50,
                "hash_verified": True,
            },
            "raw_verification_attempt_receipt": {
                "attempt_id": attempt["attempt_id"],
                "receipt_fingerprint_sha256": attempt[
                    "receipt_fingerprint_sha256"
                ],
                "status": attempt["status"],
            },
            "recoverable_by_rebuild_from_compressed_raw": True,
            "resource_accounting": {
                "checkpoint_cumulative_new_raw_read_bytes": 150,
                "cumulative_new_raw_read_bytes_after_retirement_verification": 200,
            },
        }
        binding = {
            "schema_version": "rrc25-seed-retirement-bootstrap-binding/v1",
            "success_receipt": receipt,
            "success_receipt_file_sha256": hashlib.sha256(
                (canonical_json(receipt) + "\n").encode("utf-8")
            ).hexdigest(),
            "raw_attempt_receipt": attempt,
            "raw_attempt_receipt_file_sha256": hashlib.sha256(
                (canonical_json(attempt) + "\n").encode("utf-8")
            ).hexdigest(),
            "spool_absence_verified": True,
            "compressed_raw_stable_identity_verified": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            head = initialize_journal_from_verified_seed(
                root,
                bootstrap=bootstrap,
                run_id=RUN_ID,
                bindings=BINDINGS,
                total_update_artifacts=1,
                compatible_mapping=mapping(),
                revised_mapping=revised_mapping(),
                additional_pre_update_raw_read_bytes=50,
                bootstrap_bytes_per_second=1_000_000,
                retained_external_temporary_bytes=0,
                seed_retirement_binding=binding,
            )
            refs = {row["kind"]: row for row in head.receipt["shards"]}
            self.assertEqual(
                set(refs),
                {
                    "seed_bootstrap_attestation",
                    "seed_raw_record_refs",
                    "seed_route_events",
                },
            )
            attestation = read_single_row(
                root / refs["seed_bootstrap_attestation"]["path"]
            )
            self.assertEqual(attestation["expected_vp_ids"], [seed_vp])
            self.assertEqual(
                attestation["vp_population_source_sha256"], vp_source_sha
            )
            self.assertFalse(attestation["checkpoint"]["checkpoint_bytes_packaged"])
            self.assertEqual(
                attestation["prior_raw_accounting"], prior_raw_accounting
            )
            self.assertTrue(
                attestation["seed_retirement"]["spool_absence_verified"]
            )
            self.assertRegex(
                attestation["initial_compact_state_semantic_sha256"],
                r"^[0-9a-f]{64}$",
            )
            self.assertEqual(
                attestation["initial_compact_state_semantic_sha256"],
                worker_module._semantic_sha256(
                    "rrc25_full_window_initial_compact_state_v1",
                    attestation["initial_compact_state"],
                ),
            )

        tampered = dict(binding)
        tampered["success_receipt_file_sha256"] = "0" * 64
        with self.assertRaisesRegex(FullWindowWorkerError, "规范文件 SHA"):
            worker_module._validated_seed_retirement_binding(
                tampered,
                bootstrap=bootstrap,
                additional_pre_update_raw_read_bytes=50,
            )

    def test_two_artifact_resume_keeps_compatible_curve_and_no_dynamic_backfill(self):
        seed, seed_vp = seed_state()
        compact = initialize_compact_state_from_seed(
            seed,
            compatible_mapping=mapping(),
            revised_mapping=revised_mapping(),
            tracked_prefixes=("203.0.113.0/24",),
            expected_vp_ids=(seed_vp,),
            vp_population_source_sha256=hashlib.sha256(b"peer-index").hexdigest(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            head = initialize_full_window_journal(
                root,
                run_id=RUN_ID,
                bindings=BINDINGS,
                total_artifacts=2,
                initial_compact_state=compact,
                preliminary_seed_read_bytes=1_280_393_043,
                seed_artifact_read_bytes=426_797_681,
                additional_pre_update_raw_read_bytes=0,
                bootstrap_bytes_per_second=1_000_000,
            )

            first_manifest = manifest(0)
            first_descriptor = artifact_descriptor_from_manifest(0, first_manifest)
            first_token = begin_artifact_attempt(head, first_descriptor)
            first_raw = update_frame(0, peer_ip="192.0.2.1")
            first_records = (
                ParsedMrtRecord(
                    0,
                    0,
                    first_raw,
                    (
                        element(
                            0,
                            peer_ip="192.0.2.1",
                            prefix="203.0.113.0/24",
                            as_path=path(64500, 65001),
                        ),
                        element(
                            0,
                            peer_ip="192.0.2.1",
                            prefix="198.51.100.0/24",
                            as_path=(AsPathSegment("as_set", (65001, 65003)),),
                        ),
                    ),
                ),
            )
            first = run_one_update_artifact(
                head,
                first_token,
                artifact_manifest_row=first_manifest,
                compatible_mapping=mapping(),
                revised_mapping=revised_mapping(),
                raw_retention_membership=lambda asn: True if asn in {65001, 65002} else None,
                update_record_stream_factory=lambda _row: FakeStream(
                    first_records, first_manifest
                ),
                parser_attestation=parser_attestation(),
                clock=advancing_clock(),
            )
            head = first.head
            first_country_ref = next(
                row for row in head.receipt["shards"] if row["kind"] == "country_slots"
            )
            first_country = country_view(root / first_country_ref["path"], "compatible")
            first_revised = country_view(root / first_country_ref["path"], "revised")
            observation_ref = next(
                row
                for row in head.receipt["shards"]
                if row["kind"] == "record_observations"
            )
            observations = read_rows(root / observation_ref["path"])
            retained_events = tuple(
                build_research_route_event(
                    artifact_id=first_manifest["artifact_id"],
                    file_sha256=first_manifest["file_sha256"],
                    collector_id=first_manifest["collector_id"],
                    artifact_slot_utc=first_manifest["artifact_time_utc"],
                    record_ordinal=0,
                    element_ordinal=element_ordinal,
                    element=retained_element,
                )
                for element_ordinal, retained_element in enumerate(
                    first_records[0].elements
                )
            )
            independently_derived = derive_artifact_boundary(
                compact,
                first_descriptor,
                retained_events,
                observations,
                compatible_mapping=mapping(),
                revised_mapping=revised_mapping(),
            )
            self.assertEqual(
                independently_derived.compatible_country_slot, first_country
            )
            self.assertEqual(
                independently_derived.revised_country_slot, first_revised
            )
            self.assertEqual(
                independently_derived.final_compact_state,
                head.scratch["compact_state"],
            )
            tampered_observation = dict(observations[0])
            tampered_observation["raw_record_sha256"] = "0" * 63
            with self.assertRaisesRegex(
                FullWindowWorkerError, "raw SHA|physical record"
            ):
                derive_artifact_boundary(
                    compact,
                    first_descriptor,
                    retained_events,
                    (tampered_observation,),
                    compatible_mapping=mapping(),
                    revised_mapping=revised_mapping(),
                )
            self.assertTrue(first_country["main_curve"])
            self.assertFalse(first_revised["main_curve"])
            self.assertEqual(first_country["measurement_view"], "compatibility")
            self.assertEqual(first_revised["measurement_view"], "revised")
            self.assertEqual(first_revised["metrics"]["cohort_asn_count"], 1)
            self.assertEqual(first_country["metrics"]["value_state"], "observed")
            self.assertEqual(first_country["metrics"]["cohort_asn_count"], 1)
            self.assertEqual(first_country["strict_population"]["acceptance_state"], "not_accepted")
            self.assertIn("origin_as_set", first_country["strict_population"]["excluded_reason_counts"])
            route_ref = next(
                row for row in head.receipt["shards"] if row["kind"] == "route_events"
            )
            raw_ref = next(
                row for row in head.receipt["shards"] if row["kind"] == "raw_record_refs"
            )
            parser_ref = next(
                row
                for row in head.receipt["shards"]
                if row["kind"] == "parser_attestations"
            )
            routes = read_rows(root / route_ref["path"])
            raw_refs = read_rows(root / raw_ref["path"])
            self.assertEqual(
                read_rows(root / parser_ref["path"]),
                [parser_attestation()],
            )
            self.assertEqual(len(routes), 2)
            self.assertEqual(len(raw_refs), 2)
            self.assertEqual({row["record_ordinal"] for row in raw_refs}, {0})
            self.assertEqual({row["element_ordinal"] for row in raw_refs}, {0, 1})
            self.assertEqual(len({row["raw_record_ref_id"] for row in raw_refs}), 2)
            self.assertEqual(
                {row["raw_record_ref_id"] for row in routes},
                {row["raw_record_ref_id"] for row in raw_refs},
            )
            self.assertEqual(len({row["record_hash"] for row in raw_refs}), 1)

            second_manifest = manifest(1)
            second_descriptor = artifact_descriptor_from_manifest(1, second_manifest)
            second_token = begin_artifact_attempt(head, second_descriptor)
            second_raw = update_frame(1, peer_ip="192.0.2.2")
            second_records = (
                ParsedMrtRecord(
                    0,
                    0,
                    second_raw,
                    (
                        element(
                            1,
                            peer_ip="192.0.2.2",
                            prefix="192.0.2.0/24",
                            as_path=path(64500, 65002),
                        ),
                    ),
                ),
            )
            second = run_one_update_artifact(
                head,
                second_token,
                artifact_manifest_row=second_manifest,
                compatible_mapping=mapping(),
                revised_mapping=revised_mapping(),
                raw_retention_membership=lambda asn: True if asn in {65001, 65002} else None,
                update_record_stream_factory=lambda _row: FakeStream(
                    second_records, second_manifest
                ),
                parser_attestation=parser_attestation(),
                clock=advancing_clock(),
            )
            head = second.head
            second_country_ref = next(
                row for row in head.receipt["shards"] if row["kind"] == "country_slots"
            )
            second_country = country_view(root / second_country_ref["path"], "compatible")
            second_revised = country_view(root / second_country_ref["path"], "revised")
            self.assertEqual(second_country["metrics"]["cohort_asn_count"], 2)
            self.assertEqual(second_country["dynamic_discoveries"][0]["kind"], "dynamic_asn")
            self.assertEqual(second_country["dynamic_discoveries"][0]["asn"], 65002)
            self.assertEqual(
                second_country["dynamic_discoveries"][0]["first_seen_at"],
                "2026-02-27T16:05:01Z",
            )
            self.assertTrue(second_country["dynamic_discoveries"][0]["route_event_id"].startswith("rte_v1_"))
            self.assertTrue(second_country["dynamic_discoveries"][0]["raw_record_ref_id"].startswith("raw_v1_"))
            self.assertEqual(second_revised["metrics"]["cohort_asn_count"], 2)
            # 已发布的第一槽不可变，后续动态发现不会回填其分母。
            self.assertEqual(
                country_view(root / first_country_ref["path"], "compatible")["metrics"]["cohort_asn_count"],
                1,
            )
            vp = second_country["vp_population"]
            self.assertEqual(vp["expected_count"], 2)
            self.assertEqual(vp["state_visible_count"], 2)
            self.assertEqual(vp["update_active_peer_count"], 1)
            self.assertNotEqual(vp["state_visible_count"], vp["update_active_peer_count"])
            restored = compact_state_from_payload(head.scratch["compact_state"])
            self.assertEqual({row[0] for row in restored.cohort_members}, {65001, 65002})
            self.assertEqual(
                cumulative_reserved_raw_bytes(root),
                1_280_393_043 + 426_797_681 + 2_000,
            )

    def test_hot_loop_soft_stop_keeps_current_at_genesis_and_charges_attempt(self):
        seed, seed_vp = seed_state()
        compact = initialize_compact_state_from_seed(
            seed,
            compatible_mapping=mapping(),
            revised_mapping=revised_mapping(),
            tracked_prefixes=("203.0.113.0/24",),
            expected_vp_ids=(seed_vp,),
            vp_population_source_sha256=hashlib.sha256(b"peer-index-stop").hexdigest(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            head = initialize_full_window_journal(
                root,
                run_id=RUN_ID,
                bindings=BINDINGS,
                total_artifacts=1,
                initial_compact_state=compact,
                preliminary_seed_read_bytes=0,
                seed_artifact_read_bytes=0,
                additional_pre_update_raw_read_bytes=0,
                bootstrap_bytes_per_second=1_000_000,
            )
            row = manifest(0)
            token = begin_artifact_attempt(
                head, artifact_descriptor_from_manifest(0, row)
            )
            raw = update_frame(0, peer_ip="192.0.2.1")
            records = (
                ParsedMrtRecord(
                    0,
                    0,
                    raw,
                    (
                        element(
                            0,
                            peer_ip="192.0.2.1",
                            prefix="203.0.113.0/24",
                            as_path=path(64500, 65001),
                        ),
                    ),
                ),
            )
            times = iter((0.0, 541.0))
            with self.assertRaisesRegex(FullWindowWorkerError, "未提交"):
                run_one_update_artifact(
                    head,
                    token,
                    artifact_manifest_row=row,
                    compatible_mapping=mapping(),
                    revised_mapping=revised_mapping(),
                    raw_retention_membership=lambda asn: (
                        True
                        if asn in {65001, 65002, 65003}
                        else (False if asn == 65005 else None)
                    ),
                    update_record_stream_factory=lambda _row: FakeStream(records, row),
                    parser_attestation=parser_attestation(),
                    clock=lambda: next(times),
                    runtime_check_interval_records=1,
                )
            recovered = journal_module.load_full_window_head(
                root, expected_bindings=BINDINGS
            )
            self.assertEqual(recovered.next_artifact_index, 0)
            self.assertEqual(len(list((root / "receipts").glob("boundary-0001-*.json"))), 0)
            self.assertEqual(cumulative_reserved_raw_bytes(root), row["size_bytes"])

    def test_publication_soft_stop_leaves_no_boundary_receipt(self):
        seed, seed_vp = seed_state()
        compact = initialize_compact_state_from_seed(
            seed,
            compatible_mapping=mapping(),
            revised_mapping=revised_mapping(),
            tracked_prefixes=("203.0.113.0/24",),
            expected_vp_ids=(seed_vp,),
            vp_population_source_sha256=hashlib.sha256(b"peer-index-publish-stop").hexdigest(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            head = initialize_full_window_journal(
                root,
                run_id=RUN_ID,
                bindings=BINDINGS,
                total_artifacts=1,
                initial_compact_state=compact,
                preliminary_seed_read_bytes=0,
                seed_artifact_read_bytes=0,
                additional_pre_update_raw_read_bytes=0,
                bootstrap_bytes_per_second=1_000_000,
            )
            row = manifest(0)
            token = begin_artifact_attempt(
                head, artifact_descriptor_from_manifest(0, row)
            )
            raw = update_frame(0, peer_ip="192.0.2.1")
            records = (
                ParsedMrtRecord(
                    0,
                    0,
                    raw,
                    (
                        element(
                            0,
                            peer_ip="192.0.2.1",
                            prefix="203.0.113.0/24",
                            as_path=path(64500, 65001),
                        ),
                    ),
                ),
            )
            times = iter((0.0, 1.0, 500.0, 541.0))
            with self.assertRaisesRegex(FullWindowWorkerError, "未提交"):
                run_one_update_artifact(
                    head,
                    token,
                    artifact_manifest_row=row,
                    compatible_mapping=mapping(),
                    revised_mapping=revised_mapping(),
                    raw_retention_membership=lambda asn: True,
                    update_record_stream_factory=lambda _row: FakeStream(records, row),
                    parser_attestation=parser_attestation(),
                    clock=lambda: next(times),
                )
            recovered = journal_module.load_full_window_head(
                root, expected_bindings=BINDINGS
            )
            self.assertEqual(recovered.next_artifact_index, 0)
            self.assertEqual(len(list((root / "receipts").glob("boundary-0001-*.json"))), 0)

    def test_state_change_down_is_not_withdrawal_quiet_carries_and_recovery_restores_coverage(self):
        seed, seed_vp = seed_state()
        compact = initialize_compact_state_from_seed(
            seed,
            compatible_mapping=mapping(),
            revised_mapping=revised_mapping(),
            tracked_prefixes=("203.0.113.0/24",),
            expected_vp_ids=(seed_vp,),
            vp_population_source_sha256=hashlib.sha256(b"peer-index-session").hexdigest(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            head = initialize_full_window_journal(
                root,
                run_id=RUN_ID,
                bindings=BINDINGS,
                total_artifacts=3,
                initial_compact_state=compact,
                preliminary_seed_read_bytes=0,
                seed_artifact_read_bytes=0,
                additional_pre_update_raw_read_bytes=0,
                bootstrap_bytes_per_second=1_000_000,
            )

            def process(index, records):
                nonlocal head
                row = manifest(index)
                token = begin_artifact_attempt(
                    head, artifact_descriptor_from_manifest(index, row)
                )
                committed = run_one_update_artifact(
                    head,
                    token,
                    artifact_manifest_row=row,
                    compatible_mapping=mapping(),
                    revised_mapping=revised_mapping(),
                    raw_retention_membership=lambda asn: (
                        True
                        if asn in {65001, 65002, 65003}
                        else (False if asn == 65005 else None)
                    ),
                    update_record_stream_factory=lambda _row: FakeStream(records, row),
                    parser_attestation=parser_attestation(),
                    clock=advancing_clock(),
                )
                head = committed.head
                ref = next(
                    shard for shard in head.receipt["shards"]
                    if shard["kind"] == "country_slots"
                )
                return country_view(root / ref["path"], "compatible")

            down_raw = state_change_frame(0, old_state=6, new_state=1)
            down = process(0, (ParsedMrtRecord(0, 0, down_raw, ()),))
            self.assertEqual(
                down["metrics"]["value_state"],
                "observed_route_state_partial_vp_coverage",
            )
            self.assertEqual(down["metrics"]["visible_asn_count"], 1)
            self.assertEqual(down["metrics"]["damaged_asn_count"], 0)
            self.assertEqual(down["vp_population"]["down_vp_ids"], [seed_vp])
            self.assertEqual(down["vp_population"]["update_active_peer_count"], 0)
            self.assertEqual(
                down["vp_population"]["down_vp_route_semantics"],
                "carried_state_not_implicit_withdrawal",
            )
            control_ref = next(
                shard for shard in head.receipt["shards"]
                if shard["kind"] == "control_records"
            )
            raw_ref = next(
                shard for shard in head.receipt["shards"]
                if shard["kind"] == "raw_record_refs"
            )
            self.assertEqual(len(read_rows(root / control_ref["path"])), 1)
            self.assertEqual(read_rows(root / raw_ref["path"]), [])

            quiet_raw = update_frame(1, peer_ip="192.0.2.2")
            quiet = process(
                1,
                (
                    ParsedMrtRecord(
                        0,
                        0,
                        quiet_raw,
                        (
                            element(
                                1,
                                peer_ip="192.0.2.2",
                                prefix="198.18.0.0/24",
                                as_path=path(64500, 65005),
                            ),
                        ),
                    ),
                ),
            )
            self.assertEqual(
                quiet["metrics"]["value_state"],
                "observed_route_state_partial_vp_coverage",
            )
            self.assertEqual(quiet["metrics"]["visible_asn_count"], 1)
            self.assertEqual(quiet["metrics"]["damaged_asn_count"], 0)
            self.assertEqual(quiet["vp_population"]["down_vp_ids"], [seed_vp])
            self.assertEqual(quiet["vp_population"]["update_active_peer_count"], 1)
            self.assertEqual(quiet["vp_population"]["observable_state_visible_count"], 0)

            up_raw = state_change_frame(2, old_state=1, new_state=6)
            recovered = process(2, (ParsedMrtRecord(0, 0, up_raw, ()),))
            self.assertEqual(recovered["metrics"]["value_state"], "observed")
            self.assertEqual(recovered["metrics"]["visible_asn_count"], 1)
            self.assertEqual(recovered["metrics"]["damaged_asn_count"], 0)
            self.assertEqual(recovered["vp_population"]["down_vp_ids"], [])
            self.assertEqual(
                recovered["vp_population"]["update_active_peer_count"], 0
            )
            self.assertTrue(recovered["vp_population"]["coverage_complete"])
            restored = compact_state_from_payload(head.scratch["compact_state"])
            self.assertIn(
                (seed_vp, "203.0.113.0/24"),
                {(entry.key.vp_id, entry.key.prefix) for entry in restored.route_state.entries},
            )

    def test_overlapping_prefix_union_and_announce_withdraw_same_slot_keep_dynamic_reference(self):
        seed, seed_vp = overlapping_seed_state()
        compact = initialize_compact_state_from_seed(
            seed,
            compatible_mapping=mapping(),
            revised_mapping=revised_mapping(),
            tracked_prefixes=("203.0.113.0/24", "203.0.113.0/25"),
            expected_vp_ids=(seed_vp,),
            vp_population_source_sha256=hashlib.sha256(b"peer-index-overlap").hexdigest(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            head = initialize_full_window_journal(
                root,
                run_id=RUN_ID,
                bindings=BINDINGS,
                total_artifacts=1,
                initial_compact_state=compact,
                preliminary_seed_read_bytes=0,
                seed_artifact_read_bytes=0,
                additional_pre_update_raw_read_bytes=0,
                bootstrap_bytes_per_second=1_000_000,
            )
            row = manifest(0)
            token = begin_artifact_attempt(
                head, artifact_descriptor_from_manifest(0, row)
            )
            raw = update_frame(0, peer_ip="192.0.2.1")
            records = (
                ParsedMrtRecord(
                    0,
                    0,
                    raw,
                    (
                        element(
                            0,
                            peer_ip="192.0.2.1",
                            prefix="192.0.2.0/24",
                            as_path=path(64500, 65002),
                        ),
                        element(
                            0,
                            peer_ip="192.0.2.1",
                            prefix="192.0.2.0/24",
                            as_path=None,
                            action="withdraw",
                        ),
                    ),
                ),
            )
            committed = run_one_update_artifact(
                head,
                token,
                artifact_manifest_row=row,
                compatible_mapping=mapping(),
                revised_mapping=revised_mapping(),
                raw_retention_membership=lambda asn: (
                    True if asn in {65001, 65002, 65003} else None
                ),
                update_record_stream_factory=lambda _row: FakeStream(records, row),
                parser_attestation=parser_attestation(),
                clock=advancing_clock(),
            )
            head = committed.head
            country_ref = next(
                shard for shard in head.receipt["shards"]
                if shard["kind"] == "country_slots"
            )
            country = country_view(root / country_ref["path"], "compatible")
            self.assertEqual(country["metrics"]["visible_ipv4_prefix_count"], 2)
            self.assertEqual(country["metrics"]["visible_ipv4_address_union"], 256)
            self.assertEqual(country["metrics"]["visible_ipv4_24_equivalent"], 1)
            self.assertEqual(country["metrics"]["cohort_asn_count"], 2)
            self.assertEqual(country["metrics"]["damaged_asn_count"], 1)
            dynamic_asn = next(
                row for row in country["dynamic_discoveries"]
                if row["kind"] == "dynamic_asn"
            )
            self.assertEqual(dynamic_asn["asn"], 65002)
            self.assertEqual(dynamic_asn["first_seen_at"], "2026-02-27T16:00:01Z")
            impact = next(row for row in country["asn_impacts"] if row["asn"] == 65002)
            self.assertEqual(impact["classification"], "ipv4_only_fully_invisible")
            restored = compact_state_from_payload(head.scratch["compact_state"])
            self.assertIn(65002, {row[0] for row in restored.cohort_members})
            self.assertNotIn(
                "192.0.2.0/24",
                {entry.key.prefix for entry in restored.route_state.entries},
            )


if __name__ == "__main__":
    unittest.main()
