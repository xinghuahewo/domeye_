from __future__ import annotations

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
    build_country_mapping_view,
)
from backend.data_pipeline.research.rrc25_country_outage.input_resolver import (
    resolve_research_inputs,
)
from backend.data_pipeline.research.resource_gate import ResourceLimits


UTC = timezone.utc
SLOT = "2026-02-27T16:00:00Z"
END = "2026-02-27T16:05:00Z"
TIMESTAMP = int(datetime(2026, 2, 27, 16, 0, tzinfo=UTC).timestamp())


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

    rib_compressed = gzip.compress(_rib_bytes(), mtime=0)
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


class BoundedPilotWorkerTests(unittest.TestCase):
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
