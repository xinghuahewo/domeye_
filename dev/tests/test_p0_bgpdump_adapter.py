from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import ipaddress
import os
from pathlib import Path
import queue
import struct
import subprocess
import tempfile
import threading
import unittest

from backend.data_pipeline.route_event import (
    BGPDUMP_APPROVED_VERSION,
    BgpdumpAdapterError,
    BgpdumpConfigurationError,
    BgpdumpIntegrityError,
    BgpdumpOutputError,
    BgpdumpRecordStreamFactory,
    ImportProvenance,
    RouteEventIndex,
    RouteEventIndexIntegrityError,
    RouteEventInputError,
    artifact_id_v1,
    build_route_event_index,
    canonical_json,
    derive_update_pilot_selection,
    route_event_id_v1,
    verify_update_pilot_selection,
)
from backend.data_pipeline.route_event.bgpdump import (
    _BoundedOutputQueue,
    _ParsedLine,
)


UTC = timezone.utc
MRT_TIMESTAMP = int(datetime(2026, 3, 4, 11, 35, 43, tzinfo=UTC).timestamp())
MANIFEST_FINGERPRINT_SCHEMA = "mrt_artifact_manifest_fingerprint_v1"


def mrt_frame(
    payload: bytes = b"fixture-update",
    *,
    timestamp: int = MRT_TIMESTAMP,
    mrt_type: int = 16,
    subtype: int = 4,
    microseconds: int = 0,
    announces=("203.0.113.0/24",),
    withdraws=(),
    mp_afi: int | None = None,
    mp_safi: int = 1,
) -> bytes:
    if mrt_type in {16, 17} and subtype in {1, 4}:
        def encode_nlri(prefixes):
            encoded = bytearray()
            for value in prefixes:
                network = ipaddress.ip_network(value, strict=False)
                encoded.append(network.prefixlen)
                octets = (network.prefixlen + 7) // 8
                encoded.extend(network.network_address.packed[:octets])
            return bytes(encoded)

        withdrawn = encode_nlri(withdraws)
        attributes = bytearray()
        if payload:
            tag = payload[:255]
            attributes.extend((0x80, 99, len(tag)))
            attributes.extend(tag)
        trailing = encode_nlri(announces)
        if mp_afi is not None:
            mp_prefixes = announces
            trailing = b""
            nlri = encode_nlri(mp_prefixes)
            next_hop = b"\xc0\x00\x02\x01" if mp_afi == 1 else b"\x20\x01\x0d\xb8" + b"\x00" * 12
            value = struct.pack("!HB", mp_afi, mp_safi) + bytes((len(next_hop),)) + next_hop + b"\x00" + nlri
            attributes.extend((0x80, 14, len(value)))
            attributes.extend(value)
        update = (
            struct.pack("!H", len(withdrawn))
            + withdrawn
            + struct.pack("!H", len(attributes))
            + bytes(attributes)
            + trailing
        )
        message = b"\xff" * 16 + struct.pack("!HB", 19 + len(update), 2) + update
        if subtype == 1:
            bgp4mp = struct.pack("!HHHH", 64500, 64496, 0, 1)
        else:
            bgp4mp = struct.pack("!IIHH", 64500, 64496, 0, 1)
        bgp4mp += ipaddress.ip_address("192.0.2.10").packed
        bgp4mp += ipaddress.ip_address("192.0.2.1").packed
        payload = bgp4mp + message
    if mrt_type == 17:
        payload = struct.pack("!I", microseconds) + payload
    return struct.pack("!IHHI", timestamp, mrt_type, subtype, len(payload)) + payload


def bgp_control_frame(
    message_type: int,
    *,
    timestamp: int = MRT_TIMESTAMP,
    subtype: int = 4,
    body: bytes = b"",
) -> bytes:
    """构造 BGP4MP 会话控制消息；仅 KEEPALIVE 应被适配器接纳。"""

    message = b"\xff" * 16 + struct.pack("!HB", 19 + len(body), message_type) + body
    if subtype == 1:
        bgp4mp = struct.pack("!HHHH", 64500, 64496, 0, 1)
    else:
        bgp4mp = struct.pack("!IIHH", 64500, 64496, 0, 1)
    bgp4mp += ipaddress.ip_address("192.0.2.10").packed
    bgp4mp += ipaddress.ip_address("192.0.2.1").packed
    payload = bgp4mp + message
    return struct.pack("!IHHI", timestamp, 16, subtype, len(payload)) + payload


def frame_time_text(frame: bytes) -> str:
    timestamp, mrt_type, _subtype, _length = struct.unpack("!IHHI", frame[:12])
    if mrt_type == 17:
        return f"{timestamp}.{struct.unpack('!I', frame[12:16])[0]:06d}"
    return str(timestamp)


def frame_format(frame: bytes) -> str:
    return "BGP4MP_ET" if struct.unpack("!H", frame[4:6])[0] == 17 else "BGP4MP"


def announce_line(
    ordinal: int,
    frame: bytes,
    *,
    prefix: str = "203.0.113.7/24",
    path: str = "64500 64496",
    peer_ip: str = "192.0.2.10",
    peer_asn: int = 64500,
) -> bytes:
    fields = [
        frame_format(frame),
        str(ordinal),
        frame_time_text(frame),
        "A",
        peer_ip,
        str(peer_asn),
        prefix,
        path,
        "IGP",
        "192.0.2.1",
        "0",
        "0",
        "",
        "NAG",
        "",
        "",
    ]
    return ("|".join(fields) + "\n").encode("ascii")


def withdraw_line(
    ordinal: int,
    frame: bytes,
    *,
    prefix: str = "198.51.100.9/24",
) -> bytes:
    fields = [
        frame_format(frame),
        str(ordinal),
        frame_time_text(frame),
        "W",
        "2001:db8::10",
        "64501",
        prefix,
    ]
    return ("|".join(fields) + "\n").encode("ascii")


def state_line(ordinal: int, frame: bytes, old: int = 6, new: int = 1) -> bytes:
    fields = [
        frame_format(frame),
        str(ordinal),
        frame_time_text(frame),
        "STATE",
        "192.0.2.20",
        "64502",
        str(old),
        str(new),
    ]
    return ("|".join(fields) + "\n").encode("ascii")


@dataclass
class FakeBehavior:
    output: object
    stderr: bytes = b""
    returncode: int = 0
    broken_pipe_ordinal: int | None = None
    hang_after_eof: bool = False
    buffer_stdout_until_eof: bool = False


class _FakeStdin:
    def __init__(self, process: "FakeProcess") -> None:
        self._process = process
        self.closed = False

    def write(self, payload) -> int:
        if self.closed:
            raise BrokenPipeError("fake stdin closed")
        return self._process.receive(bytes(payload))

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            if not self._process.behavior.hang_after_eof:
                self._process.finish(self._process.behavior.returncode)


class FakeProcess:
    def __init__(self, behavior: FakeBehavior, command, options) -> None:
        self.behavior = behavior
        self.command = list(command)
        self.options = dict(options)
        stdout_read, self._stdout_write = os.pipe()
        stderr_read, self._stderr_write = os.pipe()
        self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
        self.stderr = os.fdopen(stderr_read, "rb", buffering=0)
        self.stdin = _FakeStdin(self)
        self.returncode = None
        self.received = bytearray()
        self._buffer = bytearray()
        self._ordinal = 0
        self._finished = threading.Event()
        self._lock = threading.RLock()
        self._stderr_emitted = False
        self._pending_stdout: list[bytes] = []

    def _write_fd(self, descriptor: int, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]

    def receive(self, payload: bytes) -> int:
        with self._lock:
            if self.returncode is not None:
                raise BrokenPipeError("fake process ended")
            if self.behavior.broken_pipe_ordinal == self._ordinal:
                self.finish(23)
                raise BrokenPipeError("injected broken pipe")
            self.received.extend(payload)
            self._buffer.extend(payload)
            while len(self._buffer) >= 12:
                payload_length = struct.unpack("!I", self._buffer[8:12])[0]
                total = 12 + payload_length
                if len(self._buffer) < total:
                    break
                frame = bytes(self._buffer[:total])
                del self._buffer[:total]
                output = self.behavior.output
                lines = output(self._ordinal, frame) if callable(output) else output
                for line in lines:
                    encoded = bytes(line)
                    if self.behavior.buffer_stdout_until_eof:
                        self._pending_stdout.append(encoded)
                    else:
                        self._write_fd(self._stdout_write, encoded)
                self._ordinal += 1
            if self.behavior.stderr and not self._stderr_emitted:
                self._write_fd(self._stderr_write, self.behavior.stderr)
                self._stderr_emitted = True
            return len(payload)

    def finish(self, code: int) -> None:
        with self._lock:
            if self.returncode is not None:
                return
            if self._buffer:
                code = code or 24
            for line in self._pending_stdout:
                self._write_fd(self._stdout_write, line)
            self._pending_stdout.clear()
            self.returncode = code
            for descriptor in (self._stdout_write, self._stderr_write):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            self._finished.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self._finished.wait(timeout):
            raise subprocess.TimeoutExpired(self.command, timeout)
        return self.returncode

    def terminate(self):
        self.finish(-15)

    def kill(self):
        self.finish(-9)


class FakePopenFactory:
    def __init__(self, *behaviors: FakeBehavior) -> None:
        self.behaviors = list(behaviors)
        self.processes: list[FakeProcess] = []

    def __call__(self, command, **options):
        if not self.behaviors:
            raise AssertionError("没有可用的 fake bgpdump behavior")
        process = FakeProcess(self.behaviors.pop(0), command, options)
        self.processes.append(process)
        return process


def normalized_artifact(artifact):
    return {
        key: artifact[key]
        for key in (
            "artifact_id",
            "file_sha256",
            "collector_id",
            "artifact_type",
            "artifact_time_utc",
            "relative_path",
            "compression",
            "size_bytes",
        )
    }


def make_manifest(artifacts):
    payload = {
        "schema_version": 1,
        "manifest_kind": "mrt_artifact_manifest",
        "artifact_id_schema": "artifact_id_v1",
        "data_profile": {
            "id": "p0-test",
            "timezone": "UTC",
            "window_start": "2026-02-01T00:00:00+00:00",
            "window_end_exclusive": "2026-04-01T00:00:00+00:00",
            "window_start_utc": "2026-02-01T00:00:00Z",
            "window_end_exclusive_utc": "2026-04-01T00:00:00Z",
        },
        "artifacts": list(artifacts),
        "coverage": {
            "coverage_status": "partial",
            "missing_value_state": "source_unavailable",
        },
    }
    fingerprint = hashlib.sha256(
        canonical_json(
            {"schema": MANIFEST_FINGERPRINT_SCHEMA, "manifest": payload}
        ).encode("utf-8")
    ).hexdigest()
    return (
        {**payload, "manifest_fingerprint_sha256": fingerprint},
        {
            "verified": True,
            "artifact_count": len(artifacts),
            "manifest_fingerprint_sha256": fingerprint,
        },
    )


class BgpdumpAdapterFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw_root = self.root / "raw"
        (self.raw_root / "rrc25").mkdir(parents=True)
        self.binary = self.root / "bgpdump"
        self.binary.write_bytes(b"approved fake bgpdump 1.6.2 binary")
        self.binary.chmod(0o755)
        self.binary_hash = hashlib.sha256(self.binary.read_bytes()).hexdigest()

    def tearDown(self):
        self.temporary.cleanup()

    def write_artifact(
        self,
        frames,
        *,
        name="updates.20260304.1135.gz",
        artifact_type="update",
        truncate=0,
    ):
        compressed = gzip.compress(b"".join(frames), mtime=0)
        if truncate:
            compressed = compressed[:-truncate]
        relative = f"rrc25/{name}"
        path = self.raw_root / relative
        path.write_bytes(compressed)
        file_hash = hashlib.sha256(compressed).hexdigest()
        first_timestamp = struct.unpack("!I", frames[0][:4])[0]
        slot_timestamp = first_timestamp - first_timestamp % 300
        return {
            "artifact_id": artifact_id_v1(file_hash),
            "artifact_id_schema": "artifact_id_v1",
            "collector_id": "rrc25",
            "artifact_type": artifact_type,
            "artifact_time_utc": datetime.fromtimestamp(
                slot_timestamp, tz=UTC
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "relative_path": relative,
            "filename_family": "updates" if artifact_type == "update" else "bview",
            "compression": "gz",
            "size_bytes": len(compressed),
            "file_sha256": file_hash,
        }

    def factory(self, artifacts, popen, **overrides):
        options = {
            "data_profile": {
                "window_start_utc": "2026-02-01T00:00:00Z",
                "window_end_exclusive_utc": "2026-04-01T00:00:00Z",
            },
            "pilot_limits": {
                "max_artifact_count": 5,
                "max_compressed_bytes": 3 * 1024 * 1024 * 1024,
                "max_physical_records": 2_000_000,
                "max_route_events": 5_000_000,
                "max_spool_bytes": 32 * 1024 * 1024,
            },
            "bgpdump_path": self.binary,
            "expected_version": BGPDUMP_APPROVED_VERSION,
            "allowed_binary_sha256": (self.binary_hash,),
            "popen_factory": popen,
            "version_probe": lambda _path, **_options: "1.6.2",
            "exit_timeout_seconds": 2,
        }
        options.update(overrides)
        return BgpdumpRecordStreamFactory(self.raw_root, artifacts, **options)


class SuccessfulStreamTest(BgpdumpAdapterFixture):
    def test_stdout_queue_enforces_source_byte_budget_independently_of_item_count(self):
        outputs = _BoundedOutputQueue(max_items=4, max_source_bytes=10)
        first = _ParsedLine(
            0,
            "2026-01-01T00:00:00Z",
            0,
            0,
            "W",
            None,
            source_line_bytes=6,
        )
        second = _ParsedLine(
            1,
            "2026-01-01T00:00:00Z",
            0,
            0,
            "W",
            None,
            source_line_bytes=5,
        )
        outputs.put(first, timeout=0.01)

        with self.assertRaises(queue.Full):
            outputs.put(second, timeout=0.01)
        self.assertIs(outputs.get(timeout=0.01), first)
        outputs.put(second, timeout=0.01)
        self.assertEqual(outputs.source_bytes(), 5)
        self.assertEqual(outputs.snapshot()["peak_source_bytes"], 6)

    def test_open_and_notification_are_hashed_silent_controls(self):
        first = mrt_frame(b"before-controls")
        optional = b"\x02\x02\x02\x00"
        open_body = struct.pack(
            "!BHH4sB",
            4,
            23456,
            90,
            ipaddress.IPv4Address("172.23.0.0").packed,
            len(optional),
        ) + optional
        open_frame = bgp_control_frame(
            1, timestamp=MRT_TIMESTAMP + 1, body=open_body
        )
        notification = bgp_control_frame(
            3, timestamp=MRT_TIMESTAMP + 2, body=b"\x06\x05\x06\x05"
        )
        last = mrt_frame(b"after-controls", timestamp=MRT_TIMESTAMP + 3)
        artifact = self.write_artifact((first, open_frame, notification, last))

        popen = FakePopenFactory(
            FakeBehavior(
                lambda ordinal, frame: []
                if ordinal in {1, 2}
                else [announce_line(ordinal, frame)]
            )
        )
        stream = self.factory((artifact,), popen)(normalized_artifact(artifact))
        records = list(stream)

        self.assertEqual([record.record_ordinal for record in records], [0, 1, 2, 3])
        self.assertEqual(records[1].elements, ())
        self.assertEqual(records[2].elements, ())
        self.assertEqual(stream.statistics["open_record_count"], 1)
        self.assertEqual(stream.statistics["notification_record_count"], 1)
        self.assertEqual(stream.statistics["route_record_count"], 2)
        self.assertEqual(
            stream.statistics["physical_record_count"],
            sum(
                stream.statistics[field]
                for field in (
                    "route_record_count",
                    "state_change_record_count",
                    "open_record_count",
                    "notification_record_count",
                    "keepalive_record_count",
                )
            ),
        )
        self.assertEqual(
            bytes(popen.processes[0].received),
            first + open_frame + notification + last,
        )

    def test_keepalive_is_hashed_without_becoming_route_event(self):
        first = mrt_frame(b"before-keepalive")
        keepalive = bgp_control_frame(4, timestamp=MRT_TIMESTAMP + 1)
        third = mrt_frame(b"after-keepalive", timestamp=MRT_TIMESTAMP + 2)
        artifact = self.write_artifact((first, keepalive, third))

        def output(ordinal, frame):
            if ordinal == 1:
                return []
            return [announce_line(ordinal, frame)]

        popen = FakePopenFactory(FakeBehavior(output))
        stream = self.factory((artifact,), popen)(normalized_artifact(artifact))
        records = list(stream)

        self.assertEqual([record.record_ordinal for record in records], [0, 1, 2])
        self.assertEqual(records[1].elements, ())
        self.assertEqual(stream.statistics["physical_record_count"], 3)
        self.assertEqual(stream.statistics["route_record_count"], 2)
        self.assertEqual(stream.statistics["keepalive_record_count"], 1)
        self.assertEqual(bytes(popen.processes[0].received), first + keepalive + third)

    def test_single_read_framing_multi_element_withdraw_state_and_hashes(self):
        update = mrt_frame(
            b"first", subtype=4, withdraws=("198.51.100.0/24",)
        )
        state = mrt_frame(b"second", timestamp=MRT_TIMESTAMP + 1, subtype=5)
        artifact = self.write_artifact((update, state))

        def output(ordinal, frame):
            if ordinal == 0:
                return [announce_line(ordinal, frame), withdraw_line(ordinal, frame)]
            return [state_line(ordinal, frame, 6, 1)]

        popen = FakePopenFactory(FakeBehavior(output))
        factory = self.factory((artifact,), popen)
        stream = factory(normalized_artifact(artifact))
        records = list(stream)

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].record_ordinal, 0)
        self.assertEqual(records[0].record_offset, 0)
        self.assertEqual(records[0].raw_record, update)
        self.assertEqual(records[1].record_ordinal, 1)
        self.assertEqual(records[1].record_offset, len(update))
        self.assertEqual(records[1].raw_record, state)
        self.assertEqual([element.action for element in records[0].elements], ["announce", "withdraw"])
        self.assertEqual(
            records[0].elements[0].as_path[0].asns,
            (64500, 64496),
        )
        self.assertIsNone(records[0].elements[1].as_path)
        self.assertEqual(records[1].elements, ())
        self.assertEqual(bytes(popen.processes[0].received), update + state)
        self.assertEqual(
            popen.processes[0].command,
            [str(self.binary), "-m", "-p", "-v", "/dev/stdin"],
        )
        self.assertEqual(hashlib.sha256(records[0].raw_record).hexdigest(), hashlib.sha256(update).hexdigest())

        stats = stream.statistics
        self.assertEqual(stats["status"], "complete")
        self.assertEqual(stats["physical_record_count"], 2)
        self.assertEqual(stats["route_record_count"], 1)
        self.assertEqual(stats["state_change_record_count"], 1)
        self.assertEqual(stats["route_element_count"], 2)
        self.assertEqual(stats["announce_count"], 1)
        self.assertEqual(stats["withdraw_count"], 1)
        self.assertEqual(stats["state_change_transitions"], [{"old_state": 6, "new_state": 1, "count": 1}])
        self.assertEqual(stats["compressed_file_sha256"], artifact["file_sha256"])
        self.assertEqual(stats["compressed_read_passes"], 1)
        self.assertRegex(stats["record_hash_chain_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(factory.statistics_by_artifact[artifact["artifact_id"]], stats)

    def test_bgp4mp_et_microseconds_are_preserved_and_checked(self):
        frame = mrt_frame(
            b"extended",
            mrt_type=17,
            subtype=4,
            microseconds=123456,
        )
        artifact = self.write_artifact((frame,))
        popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        record = list(
            self.factory((artifact,), popen)(normalized_artifact(artifact))
        )[0]
        self.assertEqual(record.elements[0].event_time_utc, "2026-03-04T11:35:43.123456Z")

    def test_stdout_burst_larger_than_pipe_is_consumed_concurrently(self):
        prefixes = tuple(
            f"198.51.{index // 256}.{index % 256}/32" for index in range(2048)
        )
        frame = mrt_frame(
            b"stdout-backpressure", announces=(), withdraws=prefixes
        )
        artifact = self.write_artifact((frame,))

        def output(ordinal, raw):
            # 总输出远大于常见 pipe capacity；若 stdout 不与 stdin 写入并发
            # 消费，fake 进程会在 producer 的 write 调用内形成背压死锁。
            return [
                withdraw_line(ordinal, raw, prefix=f"198.51.{index // 256}.{index % 256}/32")
                for index in range(2048)
            ]

        popen = FakePopenFactory(FakeBehavior(output))
        stream = self.factory((artifact,), popen)(normalized_artifact(artifact))
        records = list(stream)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0].elements), 2048)
        self.assertEqual(stream.statistics["withdraw_count"], 2048)

    def test_high_cardinality_single_frame_does_not_wait_for_group_boundary_or_eof(self):
        prefixes = tuple(
            f"198.18.{index // 256}.{index % 256}/32" for index in range(4096)
        )
        frame = mrt_frame(
            b"single-frame-stream", announces=(), withdraws=prefixes
        )
        artifact = self.write_artifact((frame,))

        def output(ordinal, raw):
            # 模拟真实 bgpdump：同一 ordinal 的行逐条到达，但进程暂未给出
            # 下一个 ordinal/EOF。旧的整组缓冲实现会在此触发 idle watchdog。
            return (
                withdraw_line(ordinal, raw, prefix=prefix)
                for prefix in prefixes
            )

        popen = FakePopenFactory(
            FakeBehavior(output, hang_after_eof=True)
        )
        stream = self.factory(
            (artifact,), popen, idle_timeout_seconds=0.2
        )(normalized_artifact(artifact))
        iterator = iter(stream)
        try:
            record = next(iterator)
            self.assertEqual(len(record.elements), 4096)
            self.assertTrue(
                all(element.action == "withdraw" for element in record.elements)
            )
            self.assertIsNone(popen.processes[0].poll())
        finally:
            iterator.close()

    def test_stdout_buffered_until_eof_does_not_deadlock_stdin_feeding(self):
        frames = tuple(
            mrt_frame(
                f"eof-buffer-{ordinal}".encode("ascii"),
                timestamp=MRT_TIMESTAMP + ordinal,
            )
            for ordinal in range(10)
        )
        artifact = self.write_artifact(frames)
        popen = FakePopenFactory(
            FakeBehavior(
                lambda ordinal, raw: [announce_line(ordinal, raw)],
                buffer_stdout_until_eof=True,
            )
        )
        stream = self.factory(
            (artifact,),
            popen,
            queue_capacity=4,
            idle_timeout_seconds=0.5,
        )(normalized_artifact(artifact))

        records = list(stream)

        self.assertEqual([record.record_ordinal for record in records], list(range(10)))
        self.assertEqual(bytes(popen.processes[0].received), b"".join(frames))
        self.assertEqual(stream.statistics["compressed_read_passes"], 1)
        self.assertGreater(
            stream.statistics["peak_spool_bytes"], sum(map(len, frames))
        )
        self.assertEqual(
            stream.statistics["spool_persistence"], "anonymous_unlinked_fd"
        )

    def test_large_explicit_stdout_queue_preserves_order_and_attests_memory_bounds(self):
        frames = tuple(
            mrt_frame(f"queue-{ordinal}".encode("ascii"))
            for ordinal in range(512)
        )
        artifact = self.write_artifact(frames)
        popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        factory = self.factory(
            (artifact,),
            popen,
            queue_capacity=4096,
            max_stdout_queue_source_bytes=8 * 1024 * 1024,
        )

        records = list(factory(normalized_artifact(artifact)))

        self.assertEqual(
            [record.record_ordinal for record in records], list(range(512))
        )
        configuration = factory.parser_attestation["configuration"]
        self.assertEqual(configuration["queue_capacity"], 4096)
        self.assertEqual(
            configuration["max_stdout_queue_source_bytes"], 8 * 1024 * 1024
        )
        self.assertEqual(
            configuration["stdout_queue_retained_heap_upper_bound_bytes"],
            528 * 1024 * 1024,
        )

    def test_stdout_queue_count_and_source_byte_hard_limits_fail_closed(self):
        frame = mrt_frame(b"queue-bounds")
        artifact = self.write_artifact((frame,))
        for overrides in (
            {"queue_capacity": 4097},
            {"max_stdout_queue_source_bytes": 65_536},
            {"max_stdout_queue_source_bytes": 8 * 1024 * 1024 + 1},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(
                    BgpdumpConfigurationError, "queue|source_bytes|硬上限"
                ):
                    self.factory((artifact,), FakePopenFactory(), **overrides)

    def test_replay_is_deterministic(self):
        frame = mrt_frame(b"deterministic")
        artifact = self.write_artifact((frame,))
        first_popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        second_popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        first_stream = self.factory((artifact,), first_popen)(normalized_artifact(artifact))
        second_stream = self.factory((artifact,), second_popen)(normalized_artifact(artifact))
        self.assertEqual(list(first_stream), list(second_stream))
        self.assertEqual(first_stream.statistics, second_stream.statistics)
        self.assertEqual(bytes(first_popen.processes[0].received), bytes(second_popen.processes[0].received))

    def test_stream_can_feed_route_event_index_without_intermediate_file(self):
        frame = mrt_frame(b"index-integration")
        keepalive = bgp_control_frame(4, timestamp=MRT_TIMESTAMP + 1)
        artifact = self.write_artifact((frame, keepalive))
        popen = FakePopenFactory(
            FakeBehavior(
                lambda ordinal, raw: []
                if ordinal == 1
                else [announce_line(ordinal, raw)]
            )
        )
        manifest, verification = make_manifest((artifact,))
        selection = derive_update_pilot_selection(
            manifest,
            verification,
            (artifact["artifact_id"],),
            max_artifact_count=1,
            max_compressed_bytes=1024 * 1024,
            max_physical_records=100,
            max_route_events=100,
            max_spool_bytes=32 * 1024 * 1024,
        )
        selection_verification = verify_update_pilot_selection(
            manifest, verification, selection
        )
        factory = self.factory(
            (artifact,), popen, pilot_limits=selection["limits"]
        )
        result = build_route_event_index(
            self.root / "route-events.sqlite",
            manifest=manifest,
            manifest_verification=verification,
            provenance=ImportProvenance(
                parser_name="bgpdump",
                parser_version="1.6.2",
                importer_name="domeye_route_ingest",
                importer_version="1.0.0",
                processing_time_utc="2026-04-01T00:00:00Z",
                config={"mode": "-m -p", "strict": True},
            ),
            record_stream_factory=factory,
            artifact_selection=selection,
            selection_verification=selection_verification,
        )
        event_id = route_event_id_v1(artifact["file_sha256"], 0, 0)
        with RouteEventIndex(result.path) as index:
            event = index.get_route_event(event_id)
            verified = index.verify()
            reconciliation = index.reconciliation_summary(
                raw_root=self.raw_root,
                artifact_selection=selection,
            )
            index.verify_reconciliation_summary(
                reconciliation,
                raw_root=self.raw_root,
                artifact_selection=selection,
            )
            false_report = dict(reconciliation)
            false_report["invalid_prefix_count"] = 1
            with self.assertRaisesRegex(
                RouteEventIndexIntegrityError, "reconciliation|逐行复核"
            ):
                index.verify_reconciliation_summary(
                    false_report,
                    raw_root=self.raw_root,
                    artifact_selection=selection,
                )
        self.assertTrue(verified["verified"])
        self.assertEqual(event["lineage_status"], "raw_traceable")
        self.assertEqual(event["raw_ref"]["record_hash"], hashlib.sha256(frame).hexdigest())
        self.assertEqual(bytes(popen.processes[0].received), frame + keepalive)
        self.assertEqual(
            reconciliation["parser_capability"],
            "bgpdump_1_6_2_update_pilot",
        )
        self.assertEqual(
            reconciliation["raw_reference_audit"]["record_offset_basis"],
            "decompressed_mrt_stream",
        )
        self.assertEqual(
            reconciliation["raw_reference_audit"][
                "physical_record_checked_count"
            ],
            2,
        )
        for field in (
            "raw_reference_unresolved_count",
            "processing_lineage_missing_count",
            "record_hash_verification_failed_count",
            "vp_identity_missing_count",
            "route_event_id_conflict_count",
            "invalid_asn_count",
            "invalid_prefix_count",
            "outside_window_record_count",
        ):
            self.assertEqual(reconciliation[field], 0, field)

        raw_path = self.raw_root / artifact["relative_path"]
        raw_path.write_bytes(raw_path.read_bytes() + b"tampered")
        with RouteEventIndex(result.path) as index:
            with self.assertRaisesRegex(
                RouteEventIndexIntegrityError, "size|SHA|post-build"
            ):
                index.verify_reconciliation_summary(
                    reconciliation,
                    raw_root=self.raw_root,
                    artifact_selection=selection,
                )


class FailureClosedTest(BgpdumpAdapterFixture):
    def test_malformed_open_and_notification_fail_closed(self):
        valid_fixed = struct.pack(
            "!BHH4sB",
            4,
            23456,
            90,
            ipaddress.IPv4Address("172.23.0.0").packed,
            0,
        )
        cases = (
            (bgp_control_frame(1), "OPEN body"),
            (bgp_control_frame(1, body=valid_fixed[:-1]), "OPEN body"),
            (
                bgp_control_frame(1, body=b"\x03" + valid_fixed[1:]),
                "version 必须为 4",
            ),
            (
                bgp_control_frame(1, body=valid_fixed[:-1] + b"\x01"),
                "optional parameters",
            ),
            (
                bgp_control_frame(1, body=valid_fixed[:-1] + b"\x01\x02"),
                "optional parameter header",
            ),
            (
                bgp_control_frame(
                    1,
                    body=valid_fixed[:-1] + b"\x04\x02\x02\x02\x01",
                ),
                "capability value",
            ),
            (bgp_control_frame(3, body=b"\x06"), "NOTIFICATION body"),
            (bgp_control_frame(5, body=b"\x00\x01\x00\x01"), "message type=5"),
        )
        for frame, message in cases:
            with self.subTest(message=message):
                artifact = self.write_artifact((frame,))
                popen = FakePopenFactory(FakeBehavior(lambda _ordinal, _raw: []))
                with self.assertRaisesRegex(BgpdumpIntegrityError, message):
                    list(
                        self.factory((artifact,), popen)(normalized_artifact(artifact))
                    )

    def test_selection_spool_hard_limit_fails_closed_without_waiting(self):
        first = mrt_frame(b"spool-cap-first")
        second = mrt_frame(b"spool-cap-second", timestamp=MRT_TIMESTAMP + 1)
        artifact = self.write_artifact((first, second))
        pilot_limits = {
            "max_artifact_count": 5,
            "max_compressed_bytes": 3 * 1024 * 1024 * 1024,
            "max_physical_records": 2_000_000,
            "max_route_events": 5_000_000,
            # 一个 entry 恰好可提交；第二个 entry 必须在写入前失败关闭。
            "max_spool_bytes": 64 + len(first),
        }
        popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        stream = self.factory(
            (artifact,), popen, pilot_limits=pilot_limits
        )(normalized_artifact(artifact))

        with self.assertRaisesRegex(
            BgpdumpIntegrityError, "selection.max_spool_bytes|spool"
        ):
            list(stream)
        self.assertIsNotNone(popen.processes[0].returncode)

    def test_raw_update_multicast_safi_is_rejected_before_promotion(self):
        frame = mrt_frame(
            b"multicast",
            announces=("203.0.113.0/24",),
            mp_afi=1,
            mp_safi=2,
        )
        artifact = self.write_artifact((frame,))
        popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        with self.assertRaisesRegex(BgpdumpIntegrityError, "SAFI=2|unicast"):
            list(self.factory((artifact,), popen)(normalized_artifact(artifact)))
        self.assertEqual(popen.processes[0].received, b"")

    def test_record_time_outside_declared_update_slot_is_rejected(self):
        frame = mrt_frame(b"wrong-slot", timestamp=MRT_TIMESTAMP + 300)
        artifact = self.write_artifact((frame,))
        artifact["artifact_time_utc"] = "2026-03-04T11:35:00Z"
        popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        with self.assertRaisesRegex(BgpdumpIntegrityError, "五分钟槽"):
            list(self.factory((artifact,), popen)(normalized_artifact(artifact)))

    def test_idle_watchdog_terminates_process_that_never_finishes_stdout(self):
        frame = mrt_frame(b"idle-watchdog")
        artifact = self.write_artifact((frame,))
        popen = FakePopenFactory(
            FakeBehavior(lambda _ordinal, _raw: [], hang_after_eof=True)
        )
        stream = self.factory(
            (artifact,), popen, idle_timeout_seconds=0.05
        )(normalized_artifact(artifact))
        with self.assertRaisesRegex(
            BgpdumpOutputError, "无 frame/stdout 进展"
        ) as raised:
            list(stream)
        message = str(raised.exception)
        self.assertIn("wait_stage=stdout_line", message)
        self.assertIn("frame_spooled", message)
        self.assertIn("stdin_frame_written", message)
        self.assertIn("producer_done_spooled", message)
        self.assertIn("outputs_queue_size", message)
        self.assertIn("committed_spool_bytes", message)
        self.assertIn("producer_alive", message)
        self.assertIsNotNone(popen.processes[0].returncode)

    def test_index_rejects_provenance_that_disagrees_with_factory_attestation(self):
        frame = mrt_frame(b"attestation")
        artifact = self.write_artifact((frame,))
        manifest, verification = make_manifest((artifact,))
        popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        factory = self.factory((artifact,), popen)
        with self.assertRaisesRegex(RouteEventInputError, "attestation"):
            build_route_event_index(
                self.root / "attestation-mismatch.sqlite",
                manifest=manifest,
                manifest_verification=verification,
                provenance=ImportProvenance(
                    parser_name="not_bgpdump",
                    parser_version="1.6.2",
                    importer_name="domeye_route_ingest",
                    importer_version="1.0.0",
                    processing_time_utc="2026-04-01T00:00:00Z",
                    config={},
                ),
                record_stream_factory=factory,
            )
        self.assertEqual(popen.processes, [])

    def test_index_rejects_refingerprinted_incomplete_or_path_exec_attestation(self):
        frame = mrt_frame(b"attestation-schema")
        artifact = self.write_artifact((frame,))
        manifest, verification = make_manifest((artifact,))
        selection = derive_update_pilot_selection(
            manifest,
            verification,
            (artifact["artifact_id"],),
            max_artifact_count=1,
            max_compressed_bytes=1024 * 1024,
            max_physical_records=100,
            max_route_events=100,
            max_spool_bytes=32 * 1024 * 1024,
        )
        selection_verification = verify_update_pilot_selection(
            manifest, verification, selection
        )

        def refingerprint(attestation):
            payload = dict(attestation)
            payload.pop("attestation_fingerprint_sha256", None)
            payload["attestation_fingerprint_sha256"] = hashlib.sha256(
                canonical_json(
                    {
                        "schema": "parser_attestation_fingerprint_v1",
                        "attestation": payload,
                    }
                ).encode("utf-8")
            ).hexdigest()
            return payload

        cases = (
            ("configuration_sha256", None, "configuration_sha256"),
            ("binary_execution_policy", "path_exec", "binary fd"),
        )
        for field, replacement, message in cases:
            with self.subTest(field=field):
                popen = FakePopenFactory(
                    FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
                )
                factory = self.factory(
                    (artifact,), popen, pilot_limits=selection["limits"]
                )
                attestation = factory.parser_attestation
                if replacement is None:
                    attestation.pop(field)
                else:
                    attestation[field] = replacement
                factory._parser_attestation = refingerprint(attestation)
                with self.assertRaisesRegex(RouteEventInputError, message):
                    build_route_event_index(
                        self.root / f"bad-attestation-{field}.sqlite",
                        manifest=manifest,
                        manifest_verification=verification,
                        provenance=ImportProvenance(
                            parser_name="bgpdump",
                            parser_version="1.6.2",
                            importer_name="domeye_route_ingest",
                            importer_version="1.0.0",
                            processing_time_utc="2026-04-01T00:00:00Z",
                            config={},
                        ),
                        record_stream_factory=factory,
                        artifact_selection=selection,
                        selection_verification=selection_verification,
                    )
                self.assertEqual(popen.processes, [])

    def test_rib_is_rejected_before_binary_or_process_is_touched(self):
        frame = mrt_frame(b"rib-placeholder", mrt_type=13, subtype=2)
        artifact = self.write_artifact(
            (frame,),
            name="bview.20260304.0000.gz",
            artifact_type="rib",
        )
        popen = FakePopenFactory()
        with self.assertRaisesRegex(BgpdumpConfigurationError, "RIB|selection UPDATE"):
            self.factory((artifact,), popen)
        self.assertEqual(popen.processes, [])

    def test_as_set_and_confederation_segments_are_preserved(self):
        cases = (
            ("{64496,64497}", (("as_set", (64496, 64497)),)),
            ("(64500 64496)", (("confederation_sequence", (64500, 64496)),)),
            ("[64500,64496]", (("confederation_set", (64500, 64496)),)),
            (
                "64500 {64496,64497} (64498 64499) [64510,64511] 64512",
                (
                    ("as_sequence", (64500,)),
                    ("as_set", (64496, 64497)),
                    ("confederation_sequence", (64498, 64499)),
                    ("confederation_set", (64510, 64511)),
                    ("as_sequence", (64512,)),
                ),
            ),
        )
        for index, (path, expected) in enumerate(cases):
            with self.subTest(path=path):
                frame = mrt_frame(f"path-{index}".encode("ascii"))
                artifact = self.write_artifact(
                    (frame,), name=f"updates.20260304.{1140 + index:04d}.gz"
                )
                popen = FakePopenFactory(
                    FakeBehavior(
                        lambda ordinal, raw, path=path: [
                            announce_line(ordinal, raw, path=path)
                        ]
                    )
                )
                record = list(
                    self.factory((artifact,), popen)(normalized_artifact(artifact))
                )[0]
                actual = tuple(
                    (segment.segment_type, segment.asns)
                    for segment in record.elements[0].as_path
                )
                self.assertEqual(actual, expected)

    def test_empty_truncated_error_and_illegal_as_path_are_rejected(self):
        paths = ("", "64500 ...", "! Error !", "64500  {64496}", "{64496 64497}")
        for index, path in enumerate(paths):
            with self.subTest(path=path):
                frame = mrt_frame(f"invalid-path-{index}".encode("ascii"))
                artifact = self.write_artifact(
                    (frame,), name=f"updates.20260304.{1215 + index * 5:04d}.gz"
                )
                popen = FakePopenFactory(
                    FakeBehavior(
                        lambda ordinal, raw, path=path: [
                            announce_line(ordinal, raw, path=path)
                        ]
                    )
                )
                with self.assertRaises(BgpdumpOutputError):
                    list(self.factory((artifact,), popen)(normalized_artifact(artifact)))

    def test_ordinal_start_gap_and_frame_alignment_are_strict(self):
        frame = mrt_frame(b"ordinal")
        artifact = self.write_artifact((frame,))
        cases = (
            lambda _ordinal, raw: [announce_line(1, raw)],
            lambda _ordinal, raw: [announce_line(0, raw), announce_line(2, raw)],
        )
        for output in cases:
            with self.subTest(output=output):
                popen = FakePopenFactory(FakeBehavior(output))
                with self.assertRaises(BgpdumpOutputError):
                    list(self.factory((artifact,), popen)(normalized_artifact(artifact)))

    def test_silent_physical_record_is_not_misclassified_as_state(self):
        first = mrt_frame(b"visible", subtype=4)
        second = mrt_frame(b"silent-open", timestamp=MRT_TIMESTAMP + 1, subtype=4)
        artifact = self.write_artifact((first, second))

        def output(ordinal, raw):
            return [announce_line(ordinal, raw)] if ordinal == 0 else []

        popen = FakePopenFactory(FakeBehavior(output))
        with self.assertRaisesRegex(BgpdumpOutputError, "无 bgpdump|-p|physical"):
            list(self.factory((artifact,), popen)(normalized_artifact(artifact)))

    def test_unknown_stdout_line_and_wrong_field_layout_fail(self):
        frame = mrt_frame(b"unknown")
        artifact = self.write_artifact((frame,))
        outputs = (
            lambda _ordinal, _raw: [b"NOTICE|not-a-route\n"],
            lambda ordinal, raw: [announce_line(ordinal, raw).rstrip(b"|\n") + b"\n"],
        )
        for output in outputs:
            with self.subTest(output=output):
                popen = FakePopenFactory(FakeBehavior(output))
                with self.assertRaises(BgpdumpOutputError):
                    list(self.factory((artifact,), popen)(normalized_artifact(artifact)))

    def test_stderr_nonzero_exit_and_broken_pipe_each_fail_current_file(self):
        frame = mrt_frame(b"process-errors")
        artifact = self.write_artifact((frame,))
        behaviors = (
            FakeBehavior(
                lambda ordinal, raw: [announce_line(ordinal, raw)],
                stderr=b"parser warning\n",
            ),
            FakeBehavior(
                lambda ordinal, raw: [announce_line(ordinal, raw)],
                returncode=7,
            ),
            FakeBehavior(lambda _ordinal, _raw: [], broken_pipe_ordinal=0),
        )
        expected = (BgpdumpOutputError, BgpdumpOutputError, BgpdumpOutputError)
        for behavior, exception in zip(behaviors, expected):
            with self.subTest(behavior=behavior):
                popen = FakePopenFactory(behavior)
                with self.assertRaises(exception):
                    list(self.factory((artifact,), popen)(normalized_artifact(artifact)))

    def test_truncated_gzip_and_file_sha_tamper_fail_integrity(self):
        frame = mrt_frame(b"integrity")
        truncated = self.write_artifact(
            (frame,), name="updates.20260304.1150.gz", truncate=5
        )
        popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        with self.assertRaises(BgpdumpIntegrityError):
            list(self.factory((truncated,), popen)(normalized_artifact(truncated)))

        tampered = self.write_artifact(
            (frame,), name="updates.20260304.1155.gz"
        )
        path = self.raw_root / tampered["relative_path"]
        payload = bytearray(path.read_bytes())
        payload[4] ^= 0x01  # gzip MTIME，不改变解压内容、长度或 CRC。
        path.write_bytes(payload)
        popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        with self.assertRaisesRegex(BgpdumpIntegrityError, "SHA256"):
            list(self.factory((tampered,), popen)(normalized_artifact(tampered)))

    def test_binary_sha_and_version_allowlists_are_mandatory(self):
        frame = mrt_frame(b"binary")
        artifact = self.write_artifact((frame,))
        popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        wrong_hash = "0" * 64 if self.binary_hash != "0" * 64 else "1" * 64
        with self.assertRaisesRegex(BgpdumpConfigurationError, "allowlist"):
            self.factory((artifact,), popen, allowed_binary_sha256=(wrong_hash,))
        self.assertEqual(popen.processes, [])

        popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        stream = self.factory(
            (artifact,), popen, version_probe=lambda _path, **_options: "1.6.1"
        )(normalized_artifact(artifact))
        with self.assertRaisesRegex(BgpdumpConfigurationError, "版本"):
            list(stream)
        self.assertEqual(popen.processes, [])

        with self.assertRaisesRegex(BgpdumpConfigurationError, "1.6.2"):
            self.factory(
                (artifact,),
                FakePopenFactory(),
                expected_version="1.7.0",
            )

    def test_unsupported_mrt_type_and_local_subtype_fail_before_promotion(self):
        cases = (
            mrt_frame(b"rib", mrt_type=13, subtype=2),
            mrt_frame(b"local", mrt_type=16, subtype=7),
            mrt_frame(b"addpath", mrt_type=16, subtype=9),
        )
        for index, frame in enumerate(cases):
            with self.subTest(index=index):
                artifact = self.write_artifact(
                    (frame,), name=f"updates.20260304.{1200 + index * 5:04d}.gz"
                )
                popen = FakePopenFactory(FakeBehavior(lambda _ordinal, _raw: []))
                with self.assertRaises(BgpdumpIntegrityError):
                    list(self.factory((artifact,), popen)(normalized_artifact(artifact)))

    def test_failure_is_isolated_and_next_artifact_uses_new_process(self):
        bad_frame = mrt_frame(b"bad")
        good_frame = mrt_frame(b"good", timestamp=MRT_TIMESTAMP + 300)
        bad = self.write_artifact((bad_frame,), name="updates.20260304.1200.gz")
        good = self.write_artifact((good_frame,), name="updates.20260304.1205.gz")
        popen = FakePopenFactory(
            FakeBehavior(lambda _ordinal, _raw: [b"UNKNOWN\n"]),
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)]),
        )
        factory = self.factory((bad, good), popen)
        with self.assertRaises(BgpdumpOutputError):
            list(factory(normalized_artifact(bad)))
        records = list(factory(normalized_artifact(good)))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].raw_record, good_frame)
        self.assertEqual(len(popen.processes), 2)
        self.assertEqual(factory.statistics_by_artifact[bad["artifact_id"]]["status"], "failed")
        self.assertEqual(factory.statistics_by_artifact[good["artifact_id"]]["status"], "complete")

    def test_same_stream_and_same_factory_artifact_cannot_be_reused(self):
        frame = mrt_frame(b"single-use")
        artifact = self.write_artifact((frame,))
        popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, raw: [announce_line(ordinal, raw)])
        )
        factory = self.factory((artifact,), popen)
        stream = factory(normalized_artifact(artifact))
        self.assertEqual(len(list(stream)), 1)
        with self.assertRaisesRegex(BgpdumpAdapterError, "只能消费一次"):
            list(stream)
        with self.assertRaisesRegex(BgpdumpConfigurationError, "重复解析"):
            factory(normalized_artifact(artifact))


if __name__ == "__main__":
    unittest.main()
