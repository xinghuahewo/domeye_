from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
import gzip
import hashlib
import io
import ipaddress
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from backend.data_pipeline.route_event import (
    AsPathSegment,
    ParsedMrtRecord,
    ParsedRouteElement,
    artifact_id_v1,
)
from backend.data_pipeline.research.rrc25_country_outage.bounded_pilot_worker import (
    BoundedPilotWorkerError,
    run_bounded_pilot_worker,
)
from backend.data_pipeline.research.rrc25_country_outage import bounded_pilot_worker
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    MappingAssignment,
    RevisedMappingDelta,
    RevisedMappingLineage,
    build_country_mapping_view,
    build_raw_retention_mapping_union,
)
from backend.data_pipeline.research.rrc25_country_outage.input_resolver import (
    resolve_research_inputs,
)
from backend.data_pipeline.research.resource_gate import ResourceLimits


UTC = timezone.utc
SLOT = "2026-02-27T16:00:00Z"
END = "2026-02-27T16:05:00Z"
TIMESTAMP = int(datetime(2026, 2, 27, 16, 0, tzinfo=UTC).timestamp())


def _probe_accounting(selection, prior_raw_bytes, code_identity):
    selection_id, selection_sha = bounded_pilot_worker._selection_identity(selection)
    semantic = {
        "schema_version": (
            bounded_pilot_worker.PROBE_TERMINAL_ACCOUNTING_SCHEMA_VERSION
        ),
        "ledger_id": "probe_ledger_v1_fixture",
        "prepared_directory": "/fixture/prepared",
        "prepared_receipt_ref": {
            "path": "PREPARATION.json",
            "sha256": "1" * 64,
            "size_bytes": 1,
        },
        "prepared_bindings": {
            "profile_sha256": "2" * 64,
            "input_selection_sha256": selection_sha,
            "code_sha256": code_identity,
            "mapping_sha256": "3" * 64,
        },
        "selection_id": selection_id,
        "terminal_receipt_ref": {
            "path": "probe-ledger/GENESIS.json",
            "sha256": "4" * 64,
            "size_bytes": 1,
        },
        "terminal_receipt_kind": (
            "zero_genesis" if prior_raw_bytes == 0 else "imported_genesis"
        ),
        "attempt_count": 0,
        "outcome_count": 0,
        "prior_accounting": {
            "kind": (
                "explicit_new_task_zero_genesis"
                if prior_raw_bytes == 0
                else "imported_preexisting_accounting"
            )
        },
        "initial_observed_lower_bound_new_raw_bytes": (
            0 if prior_raw_bytes else 0
        ),
        "initial_reserved_upper_bound_new_raw_bytes": prior_raw_bytes,
        "probe_observed_lower_bound_new_raw_bytes": 0,
        "probe_observed_upper_bound_new_raw_bytes": 0,
        "cumulative_reserved_new_raw_bytes": prior_raw_bytes,
        "cumulative_semantics": "nonrefundable_reserved_upper_bound",
        "reservation_refund_policy": (
            "never_refund_even_on_failure_timeout_or_retry"
        ),
        "chain_refs_sha256": "5" * 64,
    }
    return {
        **semantic,
        "accounting_fingerprint_sha256": hashlib.sha256(
            bounded_pilot_worker.canonical_json(
                {
                    "schema": (
                        bounded_pilot_worker.
                        PROBE_TERMINAL_ACCOUNTING_FINGERPRINT_SCHEMA
                    ),
                    "accounting": semantic,
                }
            ).encode("utf-8")
        ).hexdigest(),
    }


def _seed_reservation(selection, prior_raw_bytes, code_identity):
    probe = _probe_accounting(selection, prior_raw_bytes, code_identity)
    selection_id, _selection_sha = bounded_pilot_worker._selection_identity(selection)
    seed = selection["roles"]["state_seed_rib"]
    seed_identity = {
        field: seed[field]
        for field in (
            "artifact_id",
            "file_sha256",
            "size_bytes",
            "relative_path",
            "collector_id",
            "artifact_time_utc",
        )
    }
    semantic = {
        "schema_version": bounded_pilot_worker.SEED_RAW_RESERVATION_SCHEMA_VERSION,
        "ledger_id": probe["ledger_id"],
        "prepared_directory": probe["prepared_directory"],
        "prepared_bindings": probe["prepared_bindings"],
        "selection_id": selection_id,
        "probe_terminal_accounting_fingerprint_sha256": probe[
            "accounting_fingerprint_sha256"
        ],
        "probe_terminal_receipt_ref": probe["terminal_receipt_ref"],
        "attempt_ref": {
            "path": "probe-ledger/seed-attempts/seed-attempt-fixture.json",
            "sha256": "6" * 64,
            "size_bytes": 1,
        },
        "attempt_id": "seed_v1_" + "7" * 32,
        "sequence": 1,
        "seed_artifact": seed_identity,
        "previous_seed_terminal_ref": None,
        "prior_cumulative_reserved_new_raw_bytes": prior_raw_bytes,
        "reserved_new_raw_bytes": seed["size_bytes"],
        "cumulative_reserved_new_raw_bytes": (
            prior_raw_bytes + seed["size_bytes"]
        ),
        "reservation_refund_policy": (
            "never_refund_even_on_failure_timeout_or_retry"
        ),
    }
    return {
        **semantic,
        "reservation_fingerprint_sha256": hashlib.sha256(
            bounded_pilot_worker.canonical_json(
                {
                    "schema": (
                        bounded_pilot_worker.SEED_RAW_RESERVATION_FINGERPRINT_SCHEMA
                    ),
                    "reservation": semantic,
                }
            ).encode("utf-8")
        ).hexdigest(),
    }


def _mrt_record(payload, *, subtype, timestamp=TIMESTAMP):
    return struct.pack("!IHHI", timestamp, 13, subtype, len(payload)) + payload


def _peer_index_payload(*peers):
    view = b"rrc25"
    payload = bytearray(ipaddress.IPv4Address("192.0.2.254").packed)
    payload.extend(struct.pack("!H", len(view)))
    payload.extend(view)
    payload.extend(struct.pack("!H", len(peers)))
    for index, (peer_ip, peer_asn) in enumerate(peers):
        payload.append(0x02)
        payload.extend(ipaddress.IPv4Address(f"198.51.100.{index + 1}").packed)
        payload.extend(ipaddress.ip_address(peer_ip).packed)
        payload.extend(peer_asn.to_bytes(4, "big"))
    return bytes(payload)


def _path_bytes(segment_type, *asns):
    value = bytearray((segment_type, len(asns)))
    for asn in asns:
        value.extend(asn.to_bytes(4, "big"))
    return bytes(value)


def _rib_payload(prefix, entries):
    network = ipaddress.ip_network(prefix)
    payload = bytearray(struct.pack("!I", 1))
    payload.append(network.prefixlen)
    payload.extend(network.network_address.packed[: (network.prefixlen + 7) // 8])
    payload.extend(struct.pack("!H", len(entries)))
    for peer_index, path in entries:
        attribute = bytes((0x40, 2, len(path))) + path
        payload.extend(struct.pack("!HIH", peer_index, TIMESTAMP - 60, len(attribute)))
        payload.extend(attribute)
    return _mrt_record(bytes(payload), subtype=2)


def _rib_bytes():
    peers = (("192.0.2.10", 64510), ("192.0.2.20", 64520), ("192.0.2.30", 64530))
    return b"".join(
        (
            _mrt_record(_peer_index_payload(*peers), subtype=1),
            _rib_payload(
                "203.0.113.0/24",
                (
                    (0, _path_bytes(2, 64510, 65001)),
                    (1, _path_bytes(1, 65001, 65100)),
                    (2, b""),
                ),
            ),
        )
    )


def _update_frame():
    prefix = ipaddress.ip_network("198.51.100.0/24")
    nlri = bytes((prefix.prefixlen,)) + prefix.network_address.packed[:3]
    body = b"\x00\x00\x00\x00" + nlri
    message = b"\xff" * 16 + struct.pack("!HB", 19 + len(body), 2) + body
    identity = struct.pack("!IIHH", 64500, 64496, 0, 1)
    payload = (
        identity
        + ipaddress.IPv4Address("192.0.2.40").packed
        + ipaddress.IPv4Address("192.0.2.1").packed
        + message
    )
    return struct.pack("!IHHI", TIMESTAMP + 1, 16, 4, len(payload)) + payload


def _control_frame(message_type, body):
    message = b"\xff" * 16 + struct.pack("!HB", 19 + len(body), message_type) + body
    identity = struct.pack("!IIHH", 64500, 64496, 0, 1)
    payload = (
        identity
        + ipaddress.IPv4Address("192.0.2.40").packed
        + ipaddress.IPv4Address("192.0.2.1").packed
        + message
    )
    return struct.pack("!IHHI", TIMESTAMP + 1, 16, 4, len(payload)) + payload


def _artifact(kind, relative, compressed):
    digest = hashlib.sha256(compressed).hexdigest()
    return {
        "artifact_id": artifact_id_v1(digest),
        "artifact_type": kind,
        "artifact_time_utc": SLOT,
        "collector_id": "rrc25",
        "relative_path": relative,
        "file_sha256": digest,
        "size_bytes": len(compressed),
        "compression": "gz",
    }


def _seed_spool_attestation(rib, decompressed):
    semantic = {
        "schema_version": "rrc25-seed-spool-attestation/v1",
        "artifact_binding": {
            "artifact_id": rib["artifact_id"],
            "file_sha256": rib["file_sha256"],
            "compressed_size_bytes": rib["size_bytes"],
        },
        "decompressed": {
            "size_bytes": len(decompressed),
            "sha256": hashlib.sha256(decompressed).hexdigest(),
        },
        "measurement": {
            "method": "full_streaming_gzip_decompression_sha256_v1",
            "measured_at_utc": "2026-07-22T10:11:14Z",
            "raw_read_pass_count": 1,
        },
    }
    return {
        **semantic,
        "semantic_fingerprint_sha256": hashlib.sha256(
            bounded_pilot_worker.canonical_json(semantic).encode("utf-8")
        ).hexdigest(),
    }


def _with_revised_lineage(revised, compatible):
    assignment = revised.assignments[0]
    base = compatible.assignment_for(assignment.asn)
    base_country = base.countries[0] if base.countries else "ZZ"
    delta = RevisedMappingDelta(
        asn=assignment.asn,
        countries=assignment.countries,
        mapping_state=assignment.mapping_state,
        delegated_date="20260227",
        base_country=base_country,
        override_reason="synthetic_test_delta",
        registry="ripencc",
        resource_type="asn",
        status="allocated",
        range_start=assignment.asn,
        range_count=1,
        provenance_sha256="9" * 64,
        provenance_ref="synthetic://delegated",
    )
    return replace(
        revised,
        revised_lineage=RevisedMappingLineage(
            compatible_snapshot_id="synthetic-compatible",
            compatible_source_sha256=compatible.source_sha256,
            compatible_semantic_fingerprint_sha256="8" * 64,
            event_cutoff_date="20260227",
            source_kind="synthetic_delegated",
            source_size_bytes=1,
            source_generated_on="2026-02-27",
            upstream_artifact_state="retained",
            excluded_after_cutoff_asns=(),
            limitations_zh=("仅用于测试冻结 lineage。",),
            delta_entries=(delta,),
        ),
    )
class _SyntheticUpdateStream:
    def __init__(self, path, artifact, record):
        self.path = path
        self.artifact = artifact
        self.record = record
        self._statistics = {
            "status": "not_started",
            "compressed_file_sha256": None,
            "compressed_size_bytes": None,
            "compressed_bytes_read_observed": 0,
            "compressed_read_passes": 0,
            "peak_spool_bytes": 0,
        }

    @property
    def statistics(self):
        return dict(self._statistics)

    def __iter__(self):
        compressed = self.path.read_bytes()
        self._statistics["compressed_bytes_read_observed"] = len(compressed)
        yield self.record
        self._statistics.update(
            {
                "status": "complete",
                "compressed_file_sha256": hashlib.sha256(compressed).hexdigest(),
                "compressed_size_bytes": len(compressed),
                "compressed_read_passes": 1,
            }
        )


class _StepClock:
    def __init__(self):
        self.value = -1.0

    def __call__(self):
        self.value += 1.0
        return self.value


def _synthetic_worker_case(root):
    collector = root / "rrc25" / "2026.02"
    collector.mkdir(parents=True)
    checkpoint = root / "checkpoints"
    checkpoint.mkdir()

    rib_decompressed = _rib_bytes()
    rib_compressed = gzip.compress(rib_decompressed, mtime=0)
    update_raw = _update_frame()
    update_compressed = gzip.compress(update_raw, mtime=0)
    rib_relative = "rrc25/2026.02/bview.20260227.1600.gz"
    update_relative = "rrc25/2026.02/updates.20260227.1600.gz"
    (root / rib_relative).write_bytes(rib_compressed)
    (root / update_relative).write_bytes(update_compressed)
    rib = _artifact("rib", rib_relative, rib_compressed)
    update = _artifact("update", update_relative, update_compressed)

    manifest = {
        "schema_version": 1,
        "manifest_kind": "mrt_artifact_manifest",
        "artifacts": [rib, update],
        "manifest_fingerprint_sha256": hashlib.sha256(
            b"synthetic-manifest"
        ).hexdigest(),
    }
    verification = {
        "verified": True,
        "manifest_fingerprint_sha256": manifest[
            "manifest_fingerprint_sha256"
        ],
        "artifact_count": 2,
    }
    profile = {
        "study_id": "iran-pilot-test",
        "collector_id": "rrc25",
        "country_code": "IR",
        "window": {
            "start_utc": SLOT,
            "end_exclusive_utc": END,
            "granularity_seconds": 300,
        },
    }
    selection = resolve_research_inputs(manifest, verification, profile)
    mapping = build_country_mapping_view(
        (
            MappingAssignment(65001, ("IR",), "mapped"),
            MappingAssignment(65100, ("US",), "mapped"),
        ),
        view="compatible",
        target_country="IR",
        source_sha256=hashlib.sha256(b"mapping").hexdigest(),
        source_ref="synthetic://mapping",
    )
    update_record = ParsedMrtRecord(
        0,
        0,
        update_raw,
        (
            ParsedRouteElement(
                event_time_utc="2026-02-27T16:00:01Z",
                peer_ip="192.0.2.40",
                peer_asn=64500,
                action="announce",
                prefix="198.51.100.0/24",
                afi_safi="ipv4_unicast",
                as_path=(AsPathSegment("as_sequence", (64500, 65001)),),
                quality_flags=(),
            ),
        ),
    )
    return {
        "checkpoint_directory": checkpoint,
        "mapping": mapping,
        "rib": rib,
        "seed_spool_attestation": _seed_spool_attestation(
            rib, rib_decompressed
        ),
        "rib_path": root / rib_relative,
        "selection": selection,
        "update": update,
        "update_compressed": update_compressed,
        "update_path": root / update_relative,
        "update_record": update_record,
    }


def _rewrite_checkpoint(path, mutate):
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("checkpoint_fingerprint_sha256")
    mutate(payload)
    rebuilt = bounded_pilot_worker._checkpoint_payload(payload)
    path.write_text(
        bounded_pilot_worker.canonical_json(rebuilt) + "\n", encoding="utf-8"
    )


def _rewrite_full_seed_checkpoint(path, mutate):
    payload = _read_full_seed_checkpoint(path)
    payload.pop("checkpoint_fingerprint_sha256")
    mutate(payload)
    # 改写后 gzip 体积可小幅增大；保留足够的历史峰值，使本
    # 夹具只测所指定的语义篡改，不被存储计量门提前截断。
    payload["resources"]["peak_temporary_bytes"] += 1024 * 1024
    rebuilt = bounded_pilot_worker._full_seed_checkpoint_payload(payload)
    encoded = (
        bounded_pilot_worker.canonical_json(rebuilt) + "\n"
    ).encode("utf-8")
    output = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", compresslevel=1, fileobj=output, mtime=0
    ) as stream:
        stream.write(encoded)
    path.write_bytes(output.getvalue())


def _read_full_seed_checkpoint(path):
    return dict(
        bounded_pilot_worker._read_checkpoint(
            Path(path),
            fingerprint_schema=(
                bounded_pilot_worker.FULL_SEED_CHECKPOINT_FINGERPRINT_SCHEMA
            ),
        )
    )


class BoundedPilotWorkerTests(unittest.TestCase):
    def test_seed_hot_path_materializes_evidence_only_at_checkpoint_boundaries(self):
        class CountingBoundary:
            def __init__(self):
                self.binding_calls = 0

            def checkpoint_binding(self):
                self.binding_calls += 1
                return {
                    "record_ordinal": 999_999,
                    "record_offset": 1_000_000,
                    "record_length": 64,
                    "record_sha256": "a" * 64,
                }

        class CountingPeerContext:
            def __init__(self):
                self.binding_calls = 0

            def checkpoint_binding(self):
                self.binding_calls += 1
                return {
                    "schema_version": "rrc25-rib-peer-index-context/v1",
                    "record_ordinal": 0,
                    "record_offset": 0,
                    "record_length": 64,
                    "record_sha256": "b" * 64,
                    "peers": [
                        {
                            "peer_ip": f"192.0.2.{index + 1}",
                            "peer_asn": 64_500 + index,
                        }
                        for index in range(118)
                    ],
                }

        class CountingAccumulator:
            def __init__(self):
                self.sort_calls = 0
                self._vp_ids = {
                    f"rrc25:192.0.2.{index + 1}:as{64_500 + index}"
                    for index in range(118)
                }

            @property
            def observed_vp_ids(self):
                self.sort_calls += 1
                return tuple(sorted(self._vp_ids))

        record_count = 1_000_000
        checkpoint_count = 4
        boundary = CountingBoundary()
        peer_context = CountingPeerContext()
        accumulators = []
        observed_vps = set()

        for segment in range(checkpoint_count):
            deferred = bounded_pilot_worker._DeferredSeedEvidence()
            accumulator = CountingAccumulator()
            accumulators.append(accumulator)
            deferred.attach_accumulator(accumulator)
            segment_records = record_count // checkpoint_count
            for _ in range(segment_records):
                deferred.observe_boundary(boundary, peer_context)

            self.assertEqual(boundary.binding_calls, segment)
            self.assertEqual(peer_context.binding_calls, segment)
            self.assertEqual(accumulator.sort_calls, 0)
            previous, peers = deferred.checkpoint_bindings()
            deferred.merge_observed_vps(observed_vps)
            # 同一退出路径重复收口也不得再次排序 VP 人口。
            deferred.merge_observed_vps(observed_vps)
            self.assertEqual(previous["record_ordinal"], 999_999)
            self.assertEqual(len(peers["peers"]), 118)

        self.assertEqual(boundary.binding_calls, checkpoint_count)
        self.assertEqual(peer_context.binding_calls, checkpoint_count)
        self.assertEqual(
            sum(accumulator.sort_calls for accumulator in accumulators),
            checkpoint_count,
        )
        self.assertEqual(len(observed_vps), 118)

    def test_seed_deferred_evidence_preserves_resume_mapping_input(self):
        previous = {
            "record_ordinal": 12,
            "record_offset": 345,
            "record_length": 67,
            "record_sha256": "a" * 64,
        }
        peer_context = {
            "schema_version": "rrc25-rib-peer-index-context/v1",
            "record_ordinal": 0,
            "record_offset": 0,
            "record_length": 128,
            "record_sha256": "b" * 64,
            "peers": [{"peer_ip": "192.0.2.1", "peer_asn": 64_500}],
        }
        deferred = bounded_pilot_worker._DeferredSeedEvidence()
        deferred.restore(previous, peer_context)

        self.assertIs(deferred.previous_record_boundary, previous)
        self.assertIs(deferred.peer_index_context, peer_context)
        restored_previous, restored_peers = deferred.checkpoint_bindings()
        self.assertEqual(restored_previous, previous)
        self.assertEqual(restored_peers, peer_context)

    def test_full_seed_checkpoint_storage_is_deterministic_gzip_and_legacy_plain_is_readable(self):
        context = {
            "resources": {"peak_temporary_bytes": 0},
            "large_payload": "same-payload-" * 2048,
        }
        with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
            first_root = Path(first_directory)
            second_root = Path(second_directory)
            first, first_size, _first_peak = (
                bounded_pilot_worker._publish_full_seed_checkpoint(
                    first_root,
                    selection_id="rsel_v1_fixture",
                    sequence=1,
                    context=context,
                    maximum_temporary_bytes=10_000_000,
                )
            )
            second, second_size, _second_peak = (
                bounded_pilot_worker._publish_full_seed_checkpoint(
                    second_root,
                    selection_id="rsel_v1_fixture",
                    sequence=1,
                    context=context,
                    maximum_temporary_bytes=10_000_000,
                )
            )
            self.assertTrue(first.name.endswith(".json.gz"))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_size, second_size)
            self.assertLess(first_size, len(context["large_payload"]))
            restored = bounded_pilot_worker._read_checkpoint(
                first,
                fingerprint_schema=(
                    bounded_pilot_worker.FULL_SEED_CHECKPOINT_FINGERPRINT_SCHEMA
                ),
            )
            self.assertEqual(restored["large_payload"], context["large_payload"])

            legacy_payload = bounded_pilot_worker._full_seed_checkpoint_payload(
                context
            )
            legacy = first_root / "legacy-full-seed.json"
            legacy.write_text(
                bounded_pilot_worker.canonical_json(legacy_payload) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                bounded_pilot_worker._read_checkpoint(
                    legacy,
                    fingerprint_schema=(
                        bounded_pilot_worker.FULL_SEED_CHECKPOINT_FINGERPRINT_SCHEMA
                    ),
                ),
                legacy_payload,
            )

    def test_full_seed_checkpoint_rejects_corrupt_gzip_and_decompression_bomb(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corrupt = root / "corrupt.json.gz"
            corrupt.write_bytes(b"\x1f\x8bnot-a-complete-gzip")
            with self.assertRaisesRegex(
                BoundedPilotWorkerError, "gzip EOF/CRC"
            ):
                bounded_pilot_worker._read_checkpoint(
                    corrupt,
                    fingerprint_schema=(
                        bounded_pilot_worker.FULL_SEED_CHECKPOINT_FINGERPRINT_SCHEMA
                    ),
                )

            payload = bounded_pilot_worker._full_seed_checkpoint_payload(
                {"resources": {"peak_temporary_bytes": 0}, "value": "x" * 256}
            )
            encoded = (
                bounded_pilot_worker.canonical_json(payload) + "\n"
            ).encode("utf-8")
            bomb = root / "bomb.json.gz"
            bomb.write_bytes(gzip.compress(encoded, compresslevel=1, mtime=0))
            with mock.patch.object(
                bounded_pilot_worker, "_MAX_CHECKPOINT_UNCOMPRESSED_BYTES", 64
            ), self.assertRaisesRegex(
                BoundedPilotWorkerError, "解压后超过 2 GB"
            ):
                bounded_pilot_worker._read_checkpoint(
                    bomb,
                    fingerprint_schema=(
                        bounded_pilot_worker.FULL_SEED_CHECKPOINT_FINGERPRINT_SCHEMA
                    ),
                )

    def test_full_seed_checkpoint_rejects_oversize_before_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory)
            context = {
                "resources": {"peak_temporary_bytes": 0},
                "large_payload": "x" * 512,
            }
            with mock.patch.object(
                bounded_pilot_worker, "_MAX_CHECKPOINT_BYTES", 128
            ), self.assertRaisesRegex(
                BoundedPilotWorkerError, "超过 512 MiB"
            ):
                bounded_pilot_worker._publish_full_seed_checkpoint(
                    checkpoint,
                    selection_id="rsel_v1_fixture",
                    sequence=1,
                    context=context,
                    maximum_temporary_bytes=10_000,
                )
            self.assertEqual(tuple(checkpoint.iterdir()), ())

    def test_raw_retention_union_keeps_revised_only_asn_for_rib_and_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)
            compatible = build_country_mapping_view(
                (
                    MappingAssignment(65001, ("US",), "mapped"),
                    MappingAssignment(65100, ("US",), "mapped"),
                ),
                view="compatible",
                target_country="IR",
                source_sha256="c" * 64,
                source_ref="compatible-non-ir",
            )
            revised = build_country_mapping_view(
                (
                    MappingAssignment(65001, ("IR",), "mapped"),
                    MappingAssignment(65100, ("US",), "mapped"),
                ),
                view="revised",
                target_country="IR",
                source_sha256="d" * 64,
                source_ref="revised-ir",
            )
            revised = _with_revised_lineage(revised, compatible)
            retention_union = build_raw_retention_mapping_union(
                (compatible, revised)
            )

            def update_factory(artifact):
                return _SyntheticUpdateStream(
                    case["update_path"], artifact, case["update_record"]
                )

            without_union = run_bounded_pilot_worker(
                case["selection"],
                artifact_root=root,
                country_mapping=compatible,
                pilot_end_exclusive_utc=END,
                update_record_stream_factory=update_factory,
                checkpoint_directory=case["checkpoint_directory"],
                clock=lambda: 0.0,
            )
            with_union = run_bounded_pilot_worker(
                case["selection"],
                artifact_root=root,
                country_mapping=compatible,
                raw_retention_mapping=retention_union,
                pilot_end_exclusive_utc=END,
                update_record_stream_factory=update_factory,
                checkpoint_directory=case["checkpoint_directory"],
                clock=lambda: 0.0,
            )

            self.assertFalse(
                any(
                    event.artifact_id == case["update"]["artifact_id"]
                    for event in without_union.route_events
                )
            )
            self.assertTrue(
                any(
                    event.artifact_id == case["rib"]["artifact_id"]
                    and event.as_path is not None
                    and event.as_path[-1].segment_type == "as_sequence"
                    and event.as_path[-1].asns[-1] == 65001
                    for event in with_union.route_events
                )
            )
            self.assertTrue(
                any(
                    event.artifact_id == case["update"]["artifact_id"]
                    and event.as_path is not None
                    and event.as_path[-1].asns[-1] == 65001
                    for event in with_union.route_events
                )
            )
            self.assertEqual(with_union.slot_counts[0].retained_announce_count, 1)
            self.assertIn("198.51.100.0/24", with_union.tracked_prefixes)
            self.assertEqual(
                with_union.ambiguity.mapped_compatible_cohort_state,
                "unknown_no_mapped_target_relation",
            )

    def test_full_seed_checkpoint_rejects_raw_retention_union_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)
            compatible = build_country_mapping_view(
                (MappingAssignment(65001, ("US",), "mapped"),),
                view="compatible",
                target_country="IR",
                source_sha256="c" * 64,
                source_ref="compatible",
            )
            revised_v1 = build_country_mapping_view(
                (MappingAssignment(65001, ("IR",), "mapped"),),
                view="revised",
                target_country="IR",
                source_sha256="d" * 64,
                source_ref="revised-v1",
            )
            revised_v2 = build_country_mapping_view(
                (
                    MappingAssignment(65001, ("IR",), "mapped"),
                    MappingAssignment(65002, ("IR",), "mapped"),
                ),
                view="revised",
                target_country="IR",
                source_sha256="e" * 64,
                source_ref="revised-v2",
            )
            revised_v1 = _with_revised_lineage(revised_v1, compatible)
            revised_v2 = _with_revised_lineage(revised_v2, compatible)
            union_v1 = build_raw_retention_mapping_union(
                (compatible, revised_v1)
            )
            union_v2 = build_raw_retention_mapping_union(
                (compatible, revised_v2)
            )

            class MutableClock:
                value = 0.0

                def __call__(self):
                    return self.value

            clock = MutableClock()
            real_rib_adapter = bounded_pilot_worker.iter_rib_spool_artifact_records

            def pause_after_first_record(*args, **kwargs):
                for index, record in enumerate(real_rib_adapter(*args, **kwargs)):
                    if index == 0:
                        clock.value = 420.0
                    yield record

            with mock.patch.object(
                bounded_pilot_worker,
                "iter_rib_spool_artifact_records",
                side_effect=pause_after_first_record,
            ):
                paused = run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=compatible,
                    raw_retention_mapping=union_v1,
                    seed_spool_attestation=case["seed_spool_attestation"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=mock.Mock(),
                    checkpoint_directory=case["checkpoint_directory"],
                    code_identity_sha256="a" * 64,
                    prior_raw_accounting=_probe_accounting(
                        case["selection"], 0, "a" * 64
                    ),
                    seed_raw_reservation=_seed_reservation(
                        case["selection"], 0, "a" * 64
                    ),
                    clock=clock,
                )

            checkpoint = _read_full_seed_checkpoint(paused.checkpoint_path)
            self.assertEqual(
                checkpoint["raw_retention_mapping_kind"],
                "compatible_revised_raw_retention_union",
            )
            bounded_pilot_worker.verify_full_seed_checkpoint(
                Path(paused.checkpoint_path),
                selection=case["selection"],
                country_mapping=compatible,
                raw_retention_mapping=union_v1,
                seed_spool_attestation=case["seed_spool_attestation"],
                pilot_end_exclusive_utc=END,
                code_identity_sha256="a" * 64,
            )

            with self.assertRaisesRegex(
                BoundedPilotWorkerError, "raw-retention union 身份不一致"
            ):
                bounded_pilot_worker.verify_full_seed_checkpoint(
                    Path(paused.checkpoint_path),
                    selection=case["selection"],
                    country_mapping=compatible,
                    raw_retention_mapping=union_v2,
                    seed_spool_attestation=case["seed_spool_attestation"],
                    pilot_end_exclusive_utc=END,
                    code_identity_sha256="a" * 64,
                )
            with self.assertRaisesRegex(
                BoundedPilotWorkerError, "raw-retention union 身份不一致"
            ), mock.patch.object(
                bounded_pilot_worker,
                "_open_rib_reader",
                side_effect=AssertionError("union mismatch 后不得打开 MRT"),
            ):
                run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=compatible,
                    raw_retention_mapping=union_v2,
                    seed_spool_attestation=case["seed_spool_attestation"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=mock.Mock(),
                    checkpoint_directory=case["checkpoint_directory"],
                    resume_checkpoint_path=Path(paused.checkpoint_path),
                    code_identity_sha256="a" * 64,
                    prior_raw_accounting=_probe_accounting(
                        case["selection"], 0, "a" * 64
                    ),
                    seed_raw_reservation=_seed_reservation(
                        case["selection"], 0, "a" * 64
                    ),
                )

    def test_full_seed_resume_matches_one_pass_without_gap_and_raw_reread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)
            code_identity = "a" * 64
            prior_new_raw_bytes = 1_234

            class MutableClock:
                value = 0.0

                def __call__(self):
                    return self.value

            clock = MutableClock()
            real_rib_adapter = bounded_pilot_worker.iter_rib_spool_artifact_records

            def pause_after_first_record(*args, **kwargs):
                for index, record in enumerate(real_rib_adapter(*args, **kwargs)):
                    if index == 0:
                        clock.value = 420.0
                    yield record

            update_factory = mock.Mock(
                side_effect=AssertionError("planned seed checkpoint 不得打开 UPDATE")
            )
            with mock.patch.object(
                bounded_pilot_worker,
                "iter_rib_spool_artifact_records",
                side_effect=pause_after_first_record,
            ):
                paused = run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    seed_spool_attestation=case["seed_spool_attestation"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=update_factory,
                    checkpoint_directory=case["checkpoint_directory"],
                    code_identity_sha256=code_identity,
                    prior_new_raw_read_bytes=prior_new_raw_bytes,
                    prior_raw_accounting=_probe_accounting(
                        case["selection"], prior_new_raw_bytes, code_identity
                    ),
                    seed_raw_reservation=_seed_reservation(
                        case["selection"], prior_new_raw_bytes, code_identity
                    ),
                    stop_after_seed=True,
                    clock=clock,
                )

            self.assertEqual(paused.status, "incomplete")
            self.assertEqual(paused.incomplete_reason, "planned_seed_checkpoint")
            self.assertEqual(paused.gaps, ())
            update_factory.assert_not_called()
            first_checkpoint = _read_full_seed_checkpoint(paused.checkpoint_path)
            self.assertEqual(
                first_checkpoint["schema_version"],
                "rrc25-bounded-pilot-worker-full-seed-checkpoint/v3",
            )
            self.assertEqual(first_checkpoint["position"]["phase"], "seed_rib")
            self.assertEqual(
                first_checkpoint["position"]["next_record_ordinal"], 1
            )
            self.assertFalse(
                first_checkpoint["seed_progress"]["seed_parse_complete"]
            )
            self.assertEqual(
                first_checkpoint["code_identity_sha256"], code_identity
            )
            self.assertEqual(
                first_checkpoint["checkpoint_policy"][
                    "active_root_retention_policy"
                ],
                "immutable_accumulate_no_automatic_reclamation_v1",
            )
            self.assertFalse(
                first_checkpoint["checkpoint_policy"]["automatic_deletion"]
            )
            self.assertEqual(
                first_checkpoint["checkpoint_policy"][
                    "capacity_exhaustion_behavior"
                ],
                "fail_closed_before_publish",
            )
            with mock.patch.object(
                bounded_pilot_worker,
                "_open_rib_reader",
                side_effect=AssertionError("只读 verifier 不得打开 MRT"),
            ):
                verification = (
                    bounded_pilot_worker.verify_full_seed_checkpoint(
                        Path(paused.checkpoint_path),
                        selection=case["selection"],
                        country_mapping=case["mapping"],
                        seed_spool_attestation=case["seed_spool_attestation"],
                        pilot_end_exclusive_utc=END,
                        code_identity_sha256=code_identity,
                    )
                )
            self.assertTrue(verification["verified"])
            self.assertEqual(verification["position"]["phase"], "seed_rib")
            self.assertEqual(verification["seed_read_pass_count"], 1)

            with self.assertRaisesRegex(
                bounded_pilot_worker.BoundedPilotWorkerError,
                "resources 非法",
            ):
                run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    seed_spool_attestation=case["seed_spool_attestation"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=lambda _artifact: (),
                    checkpoint_directory=case["checkpoint_directory"],
                    resume_checkpoint_path=Path(paused.checkpoint_path),
                    code_identity_sha256=code_identity,
                    prior_new_raw_read_bytes=prior_new_raw_bytes + 1,
                    prior_raw_accounting=_probe_accounting(
                        case["selection"], prior_new_raw_bytes + 1, code_identity
                    ),
                    seed_raw_reservation=_seed_reservation(
                        case["selection"], prior_new_raw_bytes + 1, code_identity
                    ),
                    stop_after_seed=True,
                    clock=lambda: 0.0,
                )

            resumed_update_factory = mock.Mock(
                side_effect=AssertionError("stop_after_seed 不得打开 UPDATE")
            )
            with mock.patch.object(
                bounded_pilot_worker,
                "_safe_artifact_path",
                side_effect=AssertionError("resume 不得打开压缩 raw"),
            ):
                resumed = run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    seed_spool_attestation=case["seed_spool_attestation"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=resumed_update_factory,
                    checkpoint_directory=case["checkpoint_directory"],
                    resume_checkpoint_path=Path(paused.checkpoint_path),
                    code_identity_sha256=code_identity,
                    prior_new_raw_read_bytes=prior_new_raw_bytes,
                    prior_raw_accounting=_probe_accounting(
                        case["selection"], prior_new_raw_bytes, code_identity
                    ),
                    seed_raw_reservation=_seed_reservation(
                        case["selection"], prior_new_raw_bytes, code_identity
                    ),
                    stop_after_seed=True,
                    clock=lambda: 0.0,
                )
            resumed_update_factory.assert_not_called()
            self.assertEqual(resumed.status, "incomplete")
            self.assertEqual(resumed.incomplete_reason, "stop_after_seed")
            self.assertEqual(resumed.gaps, ())

            one_pass_update_factory = mock.Mock(
                side_effect=AssertionError("stop_after_seed 不得打开 UPDATE")
            )
            one_pass_checkpoint_directory = root / "one-pass-checkpoints"
            one_pass_checkpoint_directory.mkdir()
            one_pass = run_bounded_pilot_worker(
                case["selection"],
                artifact_root=root,
                country_mapping=case["mapping"],
                seed_spool_attestation=case["seed_spool_attestation"],
                pilot_end_exclusive_utc=END,
                update_record_stream_factory=one_pass_update_factory,
                checkpoint_directory=one_pass_checkpoint_directory,
                code_identity_sha256=code_identity,
                prior_new_raw_read_bytes=prior_new_raw_bytes,
                prior_raw_accounting=_probe_accounting(
                    case["selection"], prior_new_raw_bytes, code_identity
                ),
                seed_raw_reservation=_seed_reservation(
                    case["selection"], prior_new_raw_bytes, code_identity
                ),
                stop_after_seed=True,
                clock=lambda: 0.0,
            )
            one_pass_update_factory.assert_not_called()

            self.assertEqual(resumed.state, one_pass.state)
            self.assertEqual(
                resumed.seed_state_at_window_start,
                one_pass.seed_state_at_window_start,
            )
            self.assertEqual(resumed.route_events, one_pass.route_events)
            self.assertEqual(resumed.raw_audits, one_pass.raw_audits)
            self.assertEqual(resumed.tracked_prefixes, one_pass.tracked_prefixes)
            self.assertEqual(resumed.observed_vp_ids, one_pass.observed_vp_ids)
            self.assertEqual(resumed.ambiguity, one_pass.ambiguity)
            self.assertEqual(resumed.gaps, one_pass.gaps)
            self.assertEqual(
                resumed.resources["new_raw_read_bytes"],
                prior_new_raw_bytes + case["rib"]["size_bytes"],
            )
            self.assertEqual(
                resumed.resources["prior_new_raw_read_bytes"],
                prior_new_raw_bytes,
            )
            final_checkpoint = _read_full_seed_checkpoint(resumed.checkpoint_path)
            self.assertEqual(final_checkpoint["position"]["phase"], "updates")
            self.assertTrue(
                final_checkpoint["seed_progress"]["seed_parse_complete"]
            )
            self.assertEqual(len(final_checkpoint["seed_read_ledger"]), 2)
            self.assertEqual(
                sum(
                    row["new_compressed_raw_bytes_read"]
                    for row in final_checkpoint["seed_read_ledger"]
                ),
                case["rib"]["size_bytes"],
            )

    def test_full_seed_resume_rejects_code_identity_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)

            class BoundaryClock:
                def __init__(self):
                    self.value = 0.0

                def __call__(self):
                    current = self.value
                    self.value = 420.0
                    return current

            paused = run_bounded_pilot_worker(
                case["selection"],
                artifact_root=root,
                country_mapping=case["mapping"],
                seed_spool_attestation=case["seed_spool_attestation"],
                pilot_end_exclusive_utc=END,
                update_record_stream_factory=mock.Mock(),
                checkpoint_directory=case["checkpoint_directory"],
                code_identity_sha256="a" * 64,
                prior_raw_accounting=_probe_accounting(
                    case["selection"], 0, "a" * 64
                ),
                seed_raw_reservation=_seed_reservation(
                    case["selection"], 0, "a" * 64
                ),
                clock=BoundaryClock(),
            )
            with self.assertRaisesRegex(
                BoundedPilotWorkerError, "code_identity_sha256 不一致"
            ), mock.patch.object(
                bounded_pilot_worker,
                "_open_rib_reader",
                side_effect=AssertionError("code mismatch 后不得打开 seed"),
            ):
                run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    seed_spool_attestation=case["seed_spool_attestation"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=mock.Mock(),
                    checkpoint_directory=case["checkpoint_directory"],
                    resume_checkpoint_path=Path(paused.checkpoint_path),
                    code_identity_sha256="b" * 64,
                    prior_raw_accounting=_probe_accounting(
                        case["selection"], 0, "b" * 64
                    ),
                    seed_raw_reservation=_seed_reservation(
                        case["selection"], 0, "b" * 64
                    ),
                )

    def test_full_seed_resume_rejects_boundary_tampering_against_spool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)

            class MutableClock:
                value = 0.0

                def __call__(self):
                    return self.value

            clock = MutableClock()
            real_adapter = bounded_pilot_worker.iter_rib_spool_artifact_records

            def pause_after_first_record(*args, **kwargs):
                for index, record in enumerate(real_adapter(*args, **kwargs)):
                    if index == 0:
                        clock.value = 420.0
                    yield record

            with mock.patch.object(
                bounded_pilot_worker,
                "iter_rib_spool_artifact_records",
                side_effect=pause_after_first_record,
            ):
                paused = run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    seed_spool_attestation=case["seed_spool_attestation"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=mock.Mock(),
                    checkpoint_directory=case["checkpoint_directory"],
                    code_identity_sha256="a" * 64,
                    prior_raw_accounting=_probe_accounting(
                        case["selection"], 0, "a" * 64
                    ),
                    seed_raw_reservation=_seed_reservation(
                        case["selection"], 0, "a" * 64
                    ),
                    clock=clock,
                )
            checkpoint = Path(paused.checkpoint_path)
            _rewrite_full_seed_checkpoint(
                checkpoint,
                lambda payload: payload["seed_progress"][
                    "previous_record_boundary"
                ].update({"record_sha256": "0" * 64}),
            )
            resumed = run_bounded_pilot_worker(
                case["selection"],
                artifact_root=root,
                country_mapping=case["mapping"],
                seed_spool_attestation=case["seed_spool_attestation"],
                pilot_end_exclusive_utc=END,
                update_record_stream_factory=mock.Mock(),
                checkpoint_directory=case["checkpoint_directory"],
                resume_checkpoint_path=checkpoint,
                code_identity_sha256="a" * 64,
                prior_raw_accounting=_probe_accounting(
                    case["selection"], 0, "a" * 64
                ),
                seed_raw_reservation=_seed_reservation(
                    case["selection"], 0, "a" * 64
                ),
                clock=lambda: 0.0,
            )
            self.assertEqual(
                resumed.incomplete_reason, "seed_rib_parse_or_integrity_failure"
            )
            self.assertEqual(
                resumed.errors[0]["reason"], "record_boundary_sha256_mismatch"
            )

    def test_full_seed_resume_reports_spool_integrity_reason_without_raw_reread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)

            class MutableClock:
                value = 0.0

                def __call__(self):
                    return self.value

            clock = MutableClock()
            real_adapter = bounded_pilot_worker.iter_rib_spool_artifact_records

            def pause_after_first_record(*args, **kwargs):
                for index, record in enumerate(real_adapter(*args, **kwargs)):
                    if index == 0:
                        clock.value = 420.0
                    yield record

            with mock.patch.object(
                bounded_pilot_worker,
                "iter_rib_spool_artifact_records",
                side_effect=pause_after_first_record,
            ):
                paused = run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    seed_spool_attestation=case["seed_spool_attestation"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=mock.Mock(),
                    checkpoint_directory=case["checkpoint_directory"],
                    code_identity_sha256="a" * 64,
                    prior_raw_accounting=_probe_accounting(
                        case["selection"], 0, "a" * 64
                    ),
                    seed_raw_reservation=_seed_reservation(
                        case["selection"], 0, "a" * 64
                    ),
                    clock=clock,
                )
            checkpoint = _read_full_seed_checkpoint(paused.checkpoint_path)
            spool = case["checkpoint_directory"] / checkpoint["seed_spool"][
                "file_name"
            ]
            spool.chmod(0o640)
            corrupted = bytearray(spool.read_bytes())
            corrupted[-1] ^= 0x01
            spool.write_bytes(corrupted)
            resumed = run_bounded_pilot_worker(
                case["selection"],
                artifact_root=root,
                country_mapping=case["mapping"],
                seed_spool_attestation=case["seed_spool_attestation"],
                pilot_end_exclusive_utc=END,
                update_record_stream_factory=mock.Mock(),
                checkpoint_directory=case["checkpoint_directory"],
                resume_checkpoint_path=Path(paused.checkpoint_path),
                code_identity_sha256="a" * 64,
                prior_raw_accounting=_probe_accounting(
                    case["selection"], 0, "a" * 64
                ),
                seed_raw_reservation=_seed_reservation(
                    case["selection"], 0, "a" * 64
                ),
                clock=lambda: 0.0,
            )
            self.assertEqual(
                resumed.incomplete_reason, "seed_rib_parse_or_integrity_failure"
            )
            self.assertEqual(resumed.errors[0]["reason"], "spool_sha256_mismatch")
            self.assertEqual(
                resumed.resources["new_raw_read_bytes"], case["rib"]["size_bytes"]
            )

    def test_full_seed_start_reuses_verified_spool_without_opening_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)
            attestation = case["seed_spool_attestation"]
            spool = bounded_pilot_worker._seed_spool_destination(
                case["checkpoint_directory"], attestation
            )
            spool.write_bytes(gzip.decompress(case["rib_path"].read_bytes()))
            case["rib_path"].unlink()
            update_factory = mock.Mock(
                side_effect=AssertionError("stop_after_seed 不得打开 UPDATE")
            )

            with mock.patch.object(
                bounded_pilot_worker,
                "build_rib_decompressed_spool",
                side_effect=AssertionError("复用模式不得重建 spool"),
            ), mock.patch.object(
                bounded_pilot_worker,
                "_safe_artifact_path",
                side_effect=AssertionError("复用模式不得打开压缩 raw"),
            ):
                result = run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    seed_spool_attestation=attestation,
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=update_factory,
                    checkpoint_directory=case["checkpoint_directory"],
                    code_identity_sha256="a" * 64,
                    prior_raw_accounting=_probe_accounting(
                        case["selection"], 0, "a" * 64
                    ),
                    seed_raw_reservation=None,
                    reuse_existing_seed_spool=True,
                    stop_after_seed=True,
                    clock=lambda: 0.0,
                )

            update_factory.assert_not_called()
            self.assertEqual(result.incomplete_reason, "stop_after_seed")
            self.assertEqual(result.resources["new_raw_read_bytes"], 0)
            checkpoint = _read_full_seed_checkpoint(result.checkpoint_path)
            self.assertIsNone(
                checkpoint["resources"]["seed_raw_reservation"]
            )
            self.assertEqual(
                [
                    row["new_compressed_raw_bytes_read"]
                    for row in checkpoint["seed_read_ledger"]
                ],
                [0],
            )
            verified = bounded_pilot_worker.verify_full_seed_checkpoint(
                result.checkpoint_path,
                selection=case["selection"],
                country_mapping=case["mapping"],
                seed_spool_attestation=attestation,
                pilot_end_exclusive_utc=END,
                code_identity_sha256="a" * 64,
            )
            self.assertTrue(verified["verified"])
            self.assertEqual(
                verified["resources"]["new_raw_read_bytes"], 0
            )

    def test_full_seed_start_rejects_reused_spool_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)
            attestation = case["seed_spool_attestation"]
            spool = bounded_pilot_worker._seed_spool_destination(
                case["checkpoint_directory"], attestation
            )
            expected_size = attestation["decompressed"]["size_bytes"]
            spool.write_bytes(b"\x00" * expected_size)
            case["rib_path"].unlink()

            with mock.patch.object(
                bounded_pilot_worker,
                "_safe_artifact_path",
                side_effect=AssertionError("身份失败不得回退打开压缩 raw"),
            ):
                result = run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    seed_spool_attestation=attestation,
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=mock.Mock(),
                    checkpoint_directory=case["checkpoint_directory"],
                    code_identity_sha256="a" * 64,
                    prior_raw_accounting=_probe_accounting(
                        case["selection"], 0, "a" * 64
                    ),
                    seed_raw_reservation=None,
                    reuse_existing_seed_spool=True,
                    stop_after_seed=True,
                    clock=lambda: 0.0,
                )

            self.assertEqual(
                result.incomplete_reason,
                "seed_rib_parse_or_integrity_failure",
            )
            self.assertEqual(
                result.errors[0]["reason"], "spool_sha256_mismatch"
            )
            self.assertEqual(result.resources["new_raw_read_bytes"], 0)

    def test_seed_batch_thresholds_preserve_legacy_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)
            peers = (
                ("192.0.2.10", 64510),
                ("192.0.2.20", 64520),
                ("192.0.2.30", 64530),
            )
            rib_decompressed = b"".join(
                (
                    _mrt_record(_peer_index_payload(*peers), subtype=1),
                    _rib_payload(
                        "203.0.113.0/24",
                        ((0, _path_bytes(2, 64510, 65001)),),
                    ),
                    _rib_payload(
                        "198.51.100.0/24",
                        ((1, _path_bytes(2, 64520, 65001)),),
                    ),
                    _rib_payload(
                        "192.0.2.0/24",
                        ((2, _path_bytes(2, 64530, 65001)),),
                    ),
                )
            )
            rib_compressed = gzip.compress(rib_decompressed, mtime=0)
            case["rib_path"].write_bytes(rib_compressed)
            rib = _artifact(
                "rib",
                case["rib"]["relative_path"],
                rib_compressed,
            )
            manifest = {
                "schema_version": 1,
                "manifest_kind": "mrt_artifact_manifest",
                "artifacts": [rib, case["update"]],
                "manifest_fingerprint_sha256": hashlib.sha256(
                    b"batch-equivalence-manifest"
                ).hexdigest(),
            }
            verification = {
                "verified": True,
                "manifest_fingerprint_sha256": manifest[
                    "manifest_fingerprint_sha256"
                ],
                "artifact_count": 2,
            }
            profile = {
                "study_id": "iran-batch-equivalence-test",
                "collector_id": "rrc25",
                "country_code": "IR",
                "window": {
                    "start_utc": SLOT,
                    "end_exclusive_utc": END,
                    "granularity_seconds": 300,
                },
            }
            selection = resolve_research_inputs(
                manifest, verification, profile
            )
            attestation = _seed_spool_attestation(rib, rib_decompressed)
            large_checkpoint = root / "large-batch-checkpoints"
            large_checkpoint.mkdir()
            code_identity = "a" * 64

            def execute(checkpoint, *, events, records):
                return run_bounded_pilot_worker(
                    selection,
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    seed_spool_attestation=attestation,
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=mock.Mock(),
                    checkpoint_directory=checkpoint,
                    code_identity_sha256=code_identity,
                    prior_raw_accounting=_probe_accounting(
                        selection, 0, code_identity
                    ),
                    seed_raw_reservation=_seed_reservation(
                        selection, 0, code_identity
                    ),
                    seed_batch_max_route_events=events,
                    seed_batch_max_records=records,
                    stop_after_seed=True,
                    clock=lambda: 0.0,
                )

            legacy = execute(
                case["checkpoint_directory"], events=1, records=1
            )
            accelerated = execute(
                large_checkpoint,
                events=1_048_576,
                records=65_536,
            )
            self.assertEqual(legacy.state, accelerated.state)
            self.assertEqual(legacy.route_events, accelerated.route_events)
            self.assertEqual(legacy.raw_audits, accelerated.raw_audits)
            self.assertEqual(
                legacy.tracked_prefixes, accelerated.tracked_prefixes
            )
            self.assertEqual(legacy.ambiguity, accelerated.ambiguity)

    def test_full_seed_planned_and_runtime_boundaries_include_checkpoint_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)
            with self.assertRaisesRegex(
                BoundedPilotWorkerError, "严格小于 worker 软停"
            ):
                run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    seed_spool_attestation=case["seed_spool_attestation"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=mock.Mock(),
                    checkpoint_directory=case["checkpoint_directory"],
                    code_identity_sha256="a" * 64,
                    planned_seed_checkpoint_seconds=540.0,
                )

        for boundary, expected_decision in (
            (540.0, "soft_stop"),
            (600.0, "approval_required"),
        ):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                case = _synthetic_worker_case(root)

                class MutableClock:
                    value = 0.0

                    def __call__(self):
                        return self.value

                clock = MutableClock()
                real_publish = bounded_pilot_worker._publish_full_seed_checkpoint
                real_rib_adapter = bounded_pilot_worker.iter_rib_spool_artifact_records

                def reach_planned_boundary(*args, **kwargs):
                    for record in real_rib_adapter(*args, **kwargs):
                        clock.value = 420.0
                        yield record

                def publish_then_advance(*args, **kwargs):
                    published = real_publish(*args, **kwargs)
                    clock.value = boundary
                    return published

                with mock.patch.object(
                    bounded_pilot_worker,
                    "_publish_full_seed_checkpoint",
                    side_effect=publish_then_advance,
                ), mock.patch.object(
                    bounded_pilot_worker,
                    "iter_rib_spool_artifact_records",
                    side_effect=reach_planned_boundary,
                ):
                    result = run_bounded_pilot_worker(
                        case["selection"],
                        artifact_root=root,
                        country_mapping=case["mapping"],
                        seed_spool_attestation=case["seed_spool_attestation"],
                        pilot_end_exclusive_utc=END,
                        update_record_stream_factory=mock.Mock(),
                        checkpoint_directory=case["checkpoint_directory"],
                        code_identity_sha256="a" * 64,
                        prior_raw_accounting=_probe_accounting(
                            case["selection"], 0, "a" * 64
                        ),
                        seed_raw_reservation=_seed_reservation(
                            case["selection"], 0, "a" * 64
                        ),
                        clock=clock,
                    )

                self.assertEqual(result.status, "incomplete")
                self.assertEqual(result.incomplete_reason, expected_decision)
                self.assertEqual(
                    result.resources["resource_gate"]["decision"],
                    expected_decision,
                )
                self.assertEqual(
                    result.resources["process_runtime_seconds"], boundary
                )
                self.assertTrue(Path(result.checkpoint_path).is_file())

    def test_ir_selector_keeps_full_count_without_promoting_unrelated_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)
            unrelated = ParsedMrtRecord(
                0,
                0,
                _update_frame(),
                (
                    ParsedRouteElement(
                        event_time_utc="2026-02-27T16:00:01Z",
                        peer_ip="192.0.2.40",
                        peer_asn=64500,
                        action="announce",
                        prefix="192.0.2.0/24",
                        afi_safi="ipv4_unicast",
                        as_path=(
                            AsPathSegment("as_sequence", (64500, 65100)),
                        ),
                        quality_flags=(),
                    ),
                ),
            )

            result = run_bounded_pilot_worker(
                case["selection"],
                artifact_root=root,
                country_mapping=case["mapping"],
                pilot_end_exclusive_utc=END,
                update_record_stream_factory=lambda artifact: _SyntheticUpdateStream(
                    case["update_path"], artifact, unrelated
                ),
                checkpoint_directory=case["checkpoint_directory"],
                clock=_StepClock(),
            )

            self.assertEqual(result.status, "complete")
            self.assertEqual(result.slot_counts[0].announce_count, 1)
            self.assertEqual(result.slot_counts[0].retained_announce_count, 0)
            self.assertEqual(result.state.route_count, 3)
            self.assertFalse(
                any(
                    event.artifact_id == case["update"]["artifact_id"]
                    for event in result.route_events
                )
            )

    def test_soft_stop_is_rechecked_after_complete_pass_before_state_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)

            class PostPassClock:
                def __init__(self):
                    self.calls = 0

                def __call__(self):
                    value = 4.0 if self.calls >= 9 else 0.0
                    self.calls += 1
                    return value

            result = run_bounded_pilot_worker(
                case["selection"],
                artifact_root=root,
                country_mapping=case["mapping"],
                pilot_end_exclusive_utc=END,
                update_record_stream_factory=lambda artifact: _SyntheticUpdateStream(
                    case["update_path"], artifact, case["update_record"]
                ),
                checkpoint_directory=case["checkpoint_directory"],
                resource_limits=ResourceLimits(
                    max_worker_runtime_seconds=10,
                    worker_soft_stop_seconds=3,
                ),
                clock=PostPassClock(),
            )

            self.assertEqual(result.status, "incomplete")
            self.assertEqual(result.incomplete_reason, "soft_stop")
            self.assertIsNotNone(result.checkpoint_path)
            self.assertEqual(result.state.route_count, 3)
            checkpoint = json.loads(
                Path(result.checkpoint_path).read_text(encoding="utf-8")
            )
            self.assertEqual(
                checkpoint["schema_version"],
                "rrc25-bounded-pilot-worker-diagnostic-checkpoint/v1",
            )
            self.assertFalse(checkpoint["resume_supported"])
            self.assertEqual(
                checkpoint["resume_policy"], "diagnostic_only_never_resume"
            )
            self.assertEqual(
                checkpoint["recovery_payload_state"],
                "omitted_to_meet_stop_deadline",
            )
            self.assertEqual(
                checkpoint["buffer_summary"]["pending_update_event_count"], 1
            )
            self.assertNotIn("state", checkpoint)
            self.assertNotIn("pending_update_events", checkpoint)

    def test_update_soft_stop_publishes_diagnostic_before_generator_close(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)
            update_active = False
            close_observations = []

            class UpdateBoundaryClock:
                def __call__(self):
                    return 4.0 if update_active else 0.0

            class ActivatingStream(_SyntheticUpdateStream):
                def __iter__(self):
                    nonlocal update_active
                    update_active = True
                    yield from super().__iter__()

            real_adapter = bounded_pilot_worker.iter_adapted_update_records

            class CloseAwareAdapter:
                def __init__(self, inner):
                    self.inner = inner

                def __iter__(self):
                    return self

                def __next__(self):
                    return next(self.inner)

                def close(self):
                    diagnostics = tuple(
                        case["checkpoint_directory"].glob("*.diagnostic.*.json")
                    )
                    close_observations.append(diagnostics)
                    self.inner.close()

            def wrapped_adapter(records, **kwargs):
                return CloseAwareAdapter(real_adapter(records, **kwargs))

            with mock.patch.object(
                bounded_pilot_worker,
                "iter_adapted_update_records",
                side_effect=wrapped_adapter,
            ), mock.patch.object(
                bounded_pilot_worker,
                "route_replay_state_to_payload",
                side_effect=AssertionError(
                    "UPDATE 软停诊断不得遍历/复制完整 RouteReplayState"
                ),
            ):
                result = run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=lambda artifact: ActivatingStream(
                        case["update_path"], artifact, case["update_record"]
                    ),
                    checkpoint_directory=case["checkpoint_directory"],
                    resource_limits=ResourceLimits(
                        max_worker_runtime_seconds=10,
                        worker_soft_stop_seconds=3,
                    ),
                    clock=UpdateBoundaryClock(),
                )

            self.assertEqual(result.incomplete_reason, "soft_stop")
            self.assertEqual(len(close_observations), 1)
            self.assertEqual(len(close_observations[0]), 1)
            self.assertEqual(
                close_observations[0][0], Path(result.checkpoint_path)
            )
            checkpoint = json.loads(
                Path(result.checkpoint_path).read_text(encoding="utf-8")
            )
            self.assertEqual(
                checkpoint["publication_order"],
                "before_update_stream_close",
            )
            self.assertFalse(checkpoint["resume_supported"])
            with self.assertRaisesRegex(BoundedPilotWorkerError, "指纹"):
                run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=mock.Mock(),
                    checkpoint_directory=case["checkpoint_directory"],
                    seed_sample_checkpoint_path=Path(result.checkpoint_path),
                )

    def test_open_control_retains_raw_audit_without_route_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)
            open_body = struct.pack(
                "!BHH4sB",
                4,
                23456,
                90,
                ipaddress.IPv4Address("172.23.0.0").packed,
                0,
            )
            open_raw = _control_frame(1, open_body)
            open_record = ParsedMrtRecord(0, 0, open_raw, ())

            result = run_bounded_pilot_worker(
                case["selection"],
                artifact_root=root,
                country_mapping=case["mapping"],
                pilot_end_exclusive_utc=END,
                update_record_stream_factory=lambda artifact: _SyntheticUpdateStream(
                    case["update_path"], artifact, open_record
                ),
                checkpoint_directory=case["checkpoint_directory"],
                clock=_StepClock(),
            )

            self.assertEqual(result.status, "complete")
            self.assertEqual(result.slot_counts[0].announce_count, 0)
            self.assertEqual(result.slot_counts[0].withdraw_count, 0)
            self.assertFalse(
                any(
                    event.artifact_id == case["update"]["artifact_id"]
                    for event in result.route_events
                )
            )
            self.assertTrue(
                any(
                    audit.artifact_id == case["update"]["artifact_id"]
                    and audit.raw_record_sha256 == hashlib.sha256(open_raw).hexdigest()
                    for audit in result.raw_audits
                )
            )

    def test_single_pass_worker_keeps_ambiguity_and_dynamic_unknown_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collector = root / "rrc25" / "2026.02"
            collector.mkdir(parents=True)
            checkpoint = root / "checkpoints"
            checkpoint.mkdir()

            rib_compressed = gzip.compress(_rib_bytes(), mtime=0)
            update_raw = _update_frame()
            update_compressed = gzip.compress(update_raw, mtime=0)
            rib_relative = "rrc25/2026.02/bview.20260227.1600.gz"
            update_relative = "rrc25/2026.02/updates.20260227.1600.gz"
            (root / rib_relative).write_bytes(rib_compressed)
            (root / update_relative).write_bytes(update_compressed)
            rib = _artifact("rib", rib_relative, rib_compressed)
            update = _artifact("update", update_relative, update_compressed)

            manifest_semantic = {
                "schema_version": 1,
                "manifest_kind": "mrt_artifact_manifest",
                "artifacts": [rib, update],
            }
            manifest_semantic["manifest_fingerprint_sha256"] = hashlib.sha256(
                b"synthetic-manifest"
            ).hexdigest()
            verification = {
                "verified": True,
                "manifest_fingerprint_sha256": manifest_semantic[
                    "manifest_fingerprint_sha256"
                ],
                "artifact_count": 2,
            }
            profile = {
                "study_id": "iran-pilot-test",
                "collector_id": "rrc25",
                "country_code": "IR",
                "window": {
                    "start_utc": SLOT,
                    "end_exclusive_utc": END,
                    "granularity_seconds": 300,
                },
            }
            selection = resolve_research_inputs(
                manifest_semantic, verification, profile
            )
            mapping = build_country_mapping_view(
                (
                    MappingAssignment(65001, ("IR",), "mapped"),
                    MappingAssignment(65100, ("US",), "mapped"),
                ),
                view="compatible",
                target_country="IR",
                source_sha256=hashlib.sha256(b"mapping").hexdigest(),
                source_ref="synthetic://mapping",
            )
            update_record = ParsedMrtRecord(
                0,
                0,
                update_raw,
                (
                    ParsedRouteElement(
                        event_time_utc="2026-02-27T16:00:01Z",
                        peer_ip="192.0.2.40",
                        peer_asn=64500,
                        action="announce",
                        prefix="198.51.100.0/24",
                        afi_safi="ipv4_unicast",
                        as_path=(AsPathSegment("as_sequence", (64500, 65001)),),
                        quality_flags=(),
                    ),
                ),
            )
            factory_calls = []

            def factory(artifact):
                factory_calls.append(artifact["artifact_id"])
                return _SyntheticUpdateStream(root / update_relative, artifact, update_record)

            with mock.patch.object(
                bounded_pilot_worker,
                "extend_streaming_rib_seed",
                wraps=bounded_pilot_worker.extend_streaming_rib_seed,
            ) as seed_merge:
                result = run_bounded_pilot_worker(
                    selection,
                    artifact_root=root,
                    country_mapping=mapping,
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=factory,
                    checkpoint_directory=checkpoint,
                    clock=_StepClock(),
                )

            self.assertEqual(result.status, "complete")
            # peer-index physical record 没有 RouteEvent，不能触发一次全状态重建；
            # 三个 seed element 在一个有界批次中只合并一次。
            self.assertEqual(seed_merge.call_count, 1)
            self.assertEqual(factory_calls, [update["artifact_id"]])
            self.assertEqual(len(result.snapshots), 1)
            self.assertIsNotNone(result.seed_state_at_window_start)
            self.assertEqual(result.seed_state_at_window_start.route_count, 3)
            self.assertEqual(result.state.route_count, 4)
            self.assertEqual(result.slot_counts[0].announce_count, 1)
            self.assertEqual(result.slot_counts[0].retained_announce_count, 1)
            self.assertIn("198.51.100.0/24", result.tracked_prefixes)
            self.assertEqual(len(result.pre_discovery_context_unknown), 1)
            self.assertEqual(
                result.pre_discovery_context_unknown[0]["policy"],
                "single_pass_no_backfill",
            )
            self.assertEqual(result.ambiguity.ambiguous_element_count, 2)
            self.assertEqual(len(result.ambiguity.ambiguous_record_refs), 1)
            self.assertEqual(len(result.ambiguity.ambiguous_vp_ids), 2)
            self.assertEqual(result.ambiguity.strict_population_state, "unknown")
            self.assertEqual(
                result.ambiguity.mapped_compatible_cohort_state, "measurable"
            )
            self.assertEqual(
                result.resources["new_raw_read_bytes"],
                len(rib_compressed) + len(update_compressed),
            )
            self.assertEqual(result.resources["database_writes"], 0)
            self.assertEqual(len(result.raw_audits), 2)
            self.assertEqual(
                result.resources["process_runtime_seconds"],
                result.resources["cumulative_worker_runtime_seconds"],
            )
            self.assertEqual(
                result.resources["max_worker_elapsed_seconds"],
                result.resources["process_runtime_seconds"],
            )
            self.assertIsNone(result.checkpoint_path)

    def test_runtime_hard_limit_is_cumulative_across_seed_and_update_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)

            class MutableClock:
                value = 0.0

                def __call__(self):
                    return self.value

            clock = MutableClock()
            real_open = bounded_pilot_worker._open_rib_reader

            class PhaseReader:
                def __init__(self, inner):
                    self.inner = inner

                def __getattr__(self, name):
                    return getattr(self.inner, name)

                def close(self):
                    self.inner.close()
                    # seed chunk 单独只用 200 秒；进入 UPDATE 时本进程累计
                    # 恰好 600 秒。若错误地按 artifact 重置，UPDATE 会从 0 秒
                    # 继续执行。
                    clock.value = 600.0

            def open_with_phase_clock(path, artifact):
                clock.value = 200.0
                return PhaseReader(real_open(path, artifact))

            update_factory = mock.Mock(
                return_value=_SyntheticUpdateStream(
                    case["update_path"], case["update"], case["update_record"]
                )
            )
            with mock.patch.object(
                bounded_pilot_worker,
                "_open_rib_reader",
                side_effect=open_with_phase_clock,
            ):
                result = run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=update_factory,
                    checkpoint_directory=case["checkpoint_directory"],
                    resource_limits=ResourceLimits(),
                    clock=clock,
                )

            self.assertEqual(result.status, "incomplete")
            self.assertEqual(result.incomplete_reason, "approval_required")
            self.assertEqual(
                result.resources["resource_gate"]["decision"],
                "approval_required",
            )
            self.assertGreaterEqual(
                result.resources["process_runtime_seconds"], 600.0
            )
            update_factory.assert_not_called()

    def test_truncated_seed_checkpoint_is_explicit_sample_and_skips_seed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)

            def producer_factory(artifact):
                return _SyntheticUpdateStream(
                    case["update_path"], artifact, case["update_record"]
                )

            paused = run_bounded_pilot_worker(
                case["selection"],
                artifact_root=root,
                country_mapping=case["mapping"],
                pilot_end_exclusive_utc=END,
                update_record_stream_factory=producer_factory,
                checkpoint_directory=case["checkpoint_directory"],
                resource_limits=ResourceLimits(
                    max_worker_runtime_seconds=100,
                    # 累计进程计时也包含 artifact 打开前核验；5 秒让合成
                    # RIB 至少完成一个可恢复 record 边界后再触发软停。
                    worker_soft_stop_seconds=5,
                ),
                clock=_StepClock(),
            )
            self.assertEqual(paused.status, "incomplete")
            self.assertEqual(paused.incomplete_reason, "soft_stop")
            checkpoint_path = Path(paused.checkpoint_path)
            producer_event_ids = tuple(
                event.route_event_id for event in paused.route_events
            )
            producer_audits = tuple(paused.raw_audits)
            producer_raw_bytes = paused.resources["new_raw_read_bytes"]

            # 模拟现有真实 checkpoint 的 producer 运行证据：后续样本
            # 执行不得把已触发软停的最大 worker 时长重置为零。
            _rewrite_checkpoint(
                checkpoint_path,
                lambda payload: payload["resources"].update(
                    {
                        "cumulative_worker_runtime_seconds": 540.421,
                        "max_worker_elapsed_seconds": 540.421,
                    }
                ),
            )

            update_factory_calls = []

            def sample_factory(artifact):
                update_factory_calls.append(artifact["artifact_id"])
                return _SyntheticUpdateStream(
                    case["update_path"], artifact, case["update_record"]
                )

            with mock.patch.object(
                bounded_pilot_worker,
                "_open_rib_reader",
                side_effect=AssertionError("seed RIB 不应再打开"),
            ):
                result = run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=sample_factory,
                    checkpoint_directory=case["checkpoint_directory"],
                    seed_sample_checkpoint_path=checkpoint_path,
                    clock=_StepClock(),
                )

            self.assertEqual(result.status, "complete")
            self.assertEqual(update_factory_calls, [case["update"]["artifact_id"]])
            self.assertEqual(
                tuple(
                    event.route_event_id
                    for event in result.route_events[: len(producer_event_ids)]
                ),
                producer_event_ids,
            )
            self.assertEqual(
                tuple(result.raw_audits[: len(producer_audits)]), producer_audits
            )
            self.assertEqual(
                result.resources["new_raw_read_bytes"],
                producer_raw_bytes + len(case["update_compressed"]),
            )
            self.assertGreater(
                result.resources["cumulative_worker_runtime_seconds"], 540.421
            )
            self.assertEqual(
                result.resources["max_worker_elapsed_seconds"], 540.421
            )
            self.assertEqual(
                result.resources["resource_gate"]["decision"], "allowed"
            )
            self.assertEqual(result.state.continuity_state, "unknown_after_gap")
            self.assertEqual(
                result.seed_state_at_window_start.continuity_state,
                "unknown_after_gap",
            )
            self.assertIn(
                "seed_rib_truncated_bounded_sample",
                {gap.missing_reason for gap in result.gaps},
            )
            self.assertIn(
                "seed_rib_truncated_bounded_sample",
                result.snapshots[0].missing_reasons,
            )
            self.assertIsNone(result.snapshots[0].route_count)

    def test_explicit_update_execution_subset_preserves_unselected_slot_as_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)
            factory = mock.Mock(side_effect=AssertionError("UPDATE 不应被打开"))

            result = run_bounded_pilot_worker(
                case["selection"],
                artifact_root=root,
                country_mapping=case["mapping"],
                pilot_end_exclusive_utc=END,
                update_record_stream_factory=factory,
                checkpoint_directory=case["checkpoint_directory"],
                analysis_update_artifact_ids=(),
                clock=_StepClock(),
            )

            factory.assert_not_called()
            self.assertEqual(result.status, "complete")
            self.assertEqual(len(result.snapshots), 1)
            self.assertEqual(result.slot_counts[0].input_state, "missing")
            self.assertEqual(
                result.slot_counts[0].missing_reasons,
                ("analysis_update_slot_not_selected_for_execution",),
            )
            self.assertIn(
                "analysis_update_slot_not_selected_for_execution",
                result.snapshots[0].missing_reasons,
            )

    def test_seed_sample_checkpoint_tampering_and_illegal_state_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = _synthetic_worker_case(root)

            paused = run_bounded_pilot_worker(
                case["selection"],
                artifact_root=root,
                country_mapping=case["mapping"],
                pilot_end_exclusive_utc=END,
                update_record_stream_factory=lambda artifact: _SyntheticUpdateStream(
                    case["update_path"], artifact, case["update_record"]
                ),
                checkpoint_directory=case["checkpoint_directory"],
                resource_limits=ResourceLimits(
                    max_worker_runtime_seconds=100,
                    worker_soft_stop_seconds=3,
                ),
                clock=_StepClock(),
            )
            original = Path(paused.checkpoint_path).read_bytes()

            tampered = case["checkpoint_directory"] / "tampered.json"
            tampered.write_bytes(
                original.replace(b'"update_index":0', b'"update_index":1')
            )
            with self.assertRaisesRegex(BoundedPilotWorkerError, "指纹"):
                run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=mock.Mock(),
                    checkpoint_directory=case["checkpoint_directory"],
                    seed_sample_checkpoint_path=tampered,
                )

            illegal = case["checkpoint_directory"] / "illegal.json"
            illegal.write_bytes(original)
            _rewrite_checkpoint(
                illegal,
                lambda payload: payload["position"].update({"update_index": 1}),
            )
            factory = mock.Mock()
            with self.assertRaisesRegex(BoundedPilotWorkerError, "position"):
                run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=factory,
                    checkpoint_directory=case["checkpoint_directory"],
                    seed_sample_checkpoint_path=illegal,
                )
            factory.assert_not_called()

            with self.assertRaisesRegex(
                BoundedPilotWorkerError, "coordinator/replay_persistence"
            ):
                run_bounded_pilot_worker(
                    case["selection"],
                    artifact_root=root,
                    country_mapping=case["mapping"],
                    pilot_end_exclusive_utc=END,
                    update_record_stream_factory=mock.Mock(),
                    checkpoint_directory=case["checkpoint_directory"],
                    resume_checkpoint_path=Path(paused.checkpoint_path),
                )


if __name__ == "__main__":
    unittest.main()
