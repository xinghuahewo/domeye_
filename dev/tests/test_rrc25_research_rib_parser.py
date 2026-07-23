from __future__ import annotations

import gzip
import hashlib
import io
import ipaddress
import os
from pathlib import Path
import struct
import tempfile
import unittest

from backend.data_pipeline.research.rrc25_country_outage.rib_parser import (
    RibMrtSeekError,
    RibMrtParseError,
    RibSpoolError,
    build_rib_decompressed_spool,
    iter_rib_mrt_records,
    iter_rib_mrt_records_from_offset,
    iter_rib_spool_records,
    parse_rib_mrt_bytes,
    verify_rib_decompressed_spool,
)
from backend.data_pipeline.route_event.index import route_event_id_v1, vp_id_v1


MRT_TIME = 1_772_208_000  # 2026-02-27T16:00:00Z
ORIGIN_TIME = MRT_TIME - 60


def mrt_record(
    payload: bytes, *, mrt_type: int, subtype: int, timestamp: int = MRT_TIME
) -> bytes:
    return struct.pack("!IHHI", timestamp, mrt_type, subtype, len(payload)) + payload


def as_path_value(asn_width: int, *segments: tuple[int, tuple[int, ...]]) -> bytes:
    value = bytearray()
    for segment_type, asns in segments:
        value.extend((segment_type, len(asns)))
        for asn in asns:
            value.extend(asn.to_bytes(asn_width, "big"))
    return bytes(value)


def attribute(type_code: int, value: bytes, *, flags: int = 0x40) -> bytes:
    if len(value) > 255:
        return bytes((flags | 0x10, type_code)) + struct.pack("!H", len(value)) + value
    return bytes((flags, type_code, len(value))) + value


def attributes(path: bytes, *extra: bytes) -> bytes:
    return b"".join(extra) + attribute(2, path)


def table_dump_payload(
    *,
    prefix: str,
    peer_ip: str,
    peer_asn: int,
    attrs: bytes,
    sequence: int = 7,
    originated_time: int = ORIGIN_TIME,
) -> tuple[int, bytes]:
    network = ipaddress.ip_network(prefix, strict=False)
    subtype = 1 if network.version == 4 else 2
    peer = ipaddress.ip_address(peer_ip)
    payload = (
        struct.pack("!HH", 0, sequence)
        + network.network_address.packed
        + bytes((network.prefixlen, 1))
        + struct.pack("!I", originated_time)
        + peer.packed
        + struct.pack("!HH", peer_asn, len(attrs))
        + attrs
    )
    return subtype, payload


def peer_index_payload(
    *peers: tuple[str, int, bool], view_name: str = "rrc25"
) -> bytes:
    encoded_view = view_name.encode("utf-8")
    payload = bytearray(ipaddress.IPv4Address("192.0.2.254").packed)
    payload.extend(struct.pack("!H", len(encoded_view)))
    payload.extend(encoded_view)
    payload.extend(struct.pack("!H", len(peers)))
    for index, (peer_ip, peer_asn, as4) in enumerate(peers):
        address = ipaddress.ip_address(peer_ip)
        peer_type = (0x01 if address.version == 6 else 0) | (0x02 if as4 else 0)
        payload.append(peer_type)
        payload.extend(ipaddress.IPv4Address(f"198.51.100.{index + 1}").packed)
        payload.extend(address.packed)
        payload.extend(peer_asn.to_bytes(4 if as4 else 2, "big"))
    return bytes(payload)


def v2_rib_payload(
    prefix: str, *entries: tuple[int, int, bytes], sequence: int = 9
) -> tuple[int, bytes]:
    network = ipaddress.ip_network(prefix, strict=False)
    subtype = 2 if network.version == 4 else 4
    compact_length = (network.prefixlen + 7) // 8
    payload = bytearray(struct.pack("!I", sequence))
    payload.append(network.prefixlen)
    payload.extend(network.network_address.packed[:compact_length])
    payload.extend(struct.pack("!H", len(entries)))
    for peer_index, originated_time, attrs in entries:
        payload.extend(struct.pack("!HIH", peer_index, originated_time, len(attrs)))
        payload.extend(attrs)
    return subtype, bytes(payload)


class ShortReadStream(io.BytesIO):
    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = 3
        return super().read(min(size, 3))


class CountingSeekStream(io.BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self.bytes_read = 0
        self.read_calls = 0

    def read(self, size: int = -1) -> bytes:
        value = super().read(size)
        self.bytes_read += len(value)
        self.read_calls += 1
        return value


class Rrc25ResearchRibParserTest(unittest.TestCase):
    def test_empty_stream_fails_closed(self):
        with self.assertRaisesRegex(RibMrtParseError, "为空"):
            parse_rib_mrt_bytes(b"")

    def test_table_dump_v1_ipv4_preserves_physical_identity_and_path(self):
        attrs = attributes(as_path_value(2, (2, (64500, 64501))))
        subtype, payload = table_dump_payload(
            prefix="203.0.113.0/24",
            peer_ip="192.0.2.1",
            peer_asn=64500,
            attrs=attrs,
        )
        raw = mrt_record(payload, mrt_type=12, subtype=subtype)

        records = parse_rib_mrt_bytes(raw)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual((record.record_ordinal, record.record_offset), (0, 0))
        self.assertEqual(record.raw_record, raw)
        self.assertEqual(len(record.elements), 1)
        element = record.elements[0]
        self.assertEqual(element.action, "rib_snapshot")
        self.assertEqual(element.event_time_utc, "2026-02-27T15:59:00Z")
        self.assertEqual((element.peer_ip, element.peer_asn), ("192.0.2.1", 64500))
        self.assertEqual((element.prefix, element.afi_safi), ("203.0.113.0/24", "ipv4_unicast"))
        self.assertEqual(
            tuple((segment.segment_type, segment.asns) for segment in element.as_path),
            (("as_sequence", (64500, 64501)),),
        )

    def test_table_dump_v1_ipv6_is_canonical_and_uses_two_octet_asns(self):
        attrs = attributes(as_path_value(2, (2, (64510,))))
        subtype, payload = table_dump_payload(
            prefix="2001:db8:10::/48",
            peer_ip="2001:db8::1",
            peer_asn=64510,
            attrs=attrs,
        )
        element = parse_rib_mrt_bytes(
            mrt_record(payload, mrt_type=12, subtype=subtype)
        )[0].elements[0]
        self.assertEqual(element.prefix, "2001:db8:10::/48")
        self.assertEqual(element.peer_ip, "2001:db8::1")
        self.assertEqual(element.afi_safi, "ipv6_unicast")

    def test_v2_peer_table_and_multi_entry_rib_preserve_element_order(self):
        peer_record = mrt_record(
            peer_index_payload(
                ("192.0.2.10", 64510, False),
                ("2001:db8::20", 4_200_000_020, True),
            ),
            mrt_type=13,
            subtype=1,
        )
        subtype, rib_payload = v2_rib_payload(
            "198.51.100.128/25",
            (0, ORIGIN_TIME, attributes(as_path_value(4, (2, (64510, 64496))))),
            (
                1,
                ORIGIN_TIME + 1,
                attributes(as_path_value(4, (2, (4_200_000_020, 64497)))),
            ),
        )
        rib_record = mrt_record(rib_payload, mrt_type=13, subtype=subtype)

        records = parse_rib_mrt_bytes(peer_record + rib_record)

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].elements, ())
        self.assertEqual(records[0].raw_record, peer_record)
        self.assertEqual(
            (records[1].record_ordinal, records[1].record_offset),
            (1, len(peer_record)),
        )
        self.assertEqual(records[1].raw_record, rib_record)
        self.assertEqual(
            tuple((element.peer_ip, element.peer_asn) for element in records[1].elements),
            (("192.0.2.10", 64510), ("2001:db8::20", 4_200_000_020)),
        )
        self.assertEqual(
            tuple(element.as_path[0].asns for element in records[1].elements),
            ((64510, 64496), (4_200_000_020, 64497)),
        )

    def test_v2_ipv6_prefix_and_peer_are_independent_address_families(self):
        peer_record = mrt_record(
            peer_index_payload(("192.0.2.30", 4_200_000_030, True)),
            mrt_type=13,
            subtype=1,
        )
        subtype, payload = v2_rib_payload(
            "2001:db8:abcd::/48",
            (0, ORIGIN_TIME, attributes(as_path_value(4, (2, (4_200_000_030, 44244))))),
        )
        element = parse_rib_mrt_bytes(
            peer_record + mrt_record(payload, mrt_type=13, subtype=subtype)
        )[1].elements[0]
        self.assertEqual(element.prefix, "2001:db8:abcd::/48")
        self.assertEqual(element.afi_safi, "ipv6_unicast")
        self.assertEqual(element.peer_ip, "192.0.2.30")

    def test_as_set_and_confederation_segments_are_not_flattened(self):
        path = as_path_value(
            4,
            (2, (64500,)),
            (1, (64496, 64497)),
            (3, (64520, 64521)),
            (4, (64530, 64531)),
        )
        peer_record = mrt_record(
            peer_index_payload(("192.0.2.40", 64500, False)),
            mrt_type=13,
            subtype=1,
        )
        subtype, payload = v2_rib_payload(
            "203.0.113.0/24", (0, ORIGIN_TIME, attributes(path))
        )
        element = parse_rib_mrt_bytes(
            peer_record + mrt_record(payload, mrt_type=13, subtype=subtype)
        )[1].elements[0]
        self.assertEqual(
            tuple((segment.segment_type, segment.asns) for segment in element.as_path),
            (
                ("as_sequence", (64500,)),
                ("as_set", (64496, 64497)),
                ("confederation_sequence", (64520, 64521)),
                ("confederation_set", (64530, 64531)),
            ),
        )
        self.assertEqual(
            element.quality_flags,
            ("as_set_present", "confederation_segment_present", "origin_ambiguous"),
        )

    def test_present_but_empty_as_path_has_explicit_quality_flag(self):
        subtype, payload = table_dump_payload(
            prefix="203.0.113.0/24",
            peer_ip="192.0.2.1",
            peer_asn=64500,
            attrs=attributes(b""),
        )
        element = parse_rib_mrt_bytes(
            mrt_record(payload, mrt_type=12, subtype=subtype)
        )[0].elements[0]
        self.assertEqual(element.as_path, ())
        self.assertEqual(element.quality_flags, ("empty_as_path",))

    def test_unknown_attribute_is_skipped_only_when_framing_is_complete(self):
        unknown = attribute(99, b"opaque", flags=0x80)
        subtype, payload = table_dump_payload(
            prefix="203.0.113.0/24",
            peer_ip="192.0.2.1",
            peer_asn=64500,
            attrs=attributes(as_path_value(2, (2, (64500,))), unknown),
        )
        record = parse_rib_mrt_bytes(
            mrt_record(payload, mrt_type=12, subtype=subtype)
        )[0]
        self.assertEqual(record.elements[0].as_path[0].asns, (64500,))

    def test_unknown_multicast_generic_addpath_and_non_rib_types_fail_closed(self):
        peer = mrt_record(
            peer_index_payload(("192.0.2.1", 64500, False)),
            mrt_type=13,
            subtype=1,
        )
        for name, raw in (
            ("non_rib", mrt_record(b"", mrt_type=16, subtype=1)),
            ("table_dump_unknown", mrt_record(b"", mrt_type=12, subtype=9)),
            ("multicast4", peer + mrt_record(b"", mrt_type=13, subtype=3)),
            ("multicast6", peer + mrt_record(b"", mrt_type=13, subtype=5)),
            ("generic", peer + mrt_record(b"", mrt_type=13, subtype=6)),
            ("addpath_or_unknown", peer + mrt_record(b"", mrt_type=13, subtype=8)),
        ):
            with self.subTest(name=name), self.assertRaises(RibMrtParseError):
                parse_rib_mrt_bytes(raw)

    def test_truncated_header_payload_and_attribute_fail_closed(self):
        good_attrs = attributes(as_path_value(2, (2, (64500,))))
        subtype, payload = table_dump_payload(
            prefix="203.0.113.0/24",
            peer_ip="192.0.2.1",
            peer_asn=64500,
            attrs=good_attrs,
        )
        good = mrt_record(payload, mrt_type=12, subtype=subtype)
        malformed_attribute = good_attrs[:-1]
        bad_subtype, bad_payload = table_dump_payload(
            prefix="203.0.113.0/24",
            peer_ip="192.0.2.1",
            peer_asn=64500,
            attrs=malformed_attribute,
        )
        cases = (
            good[:7],
            good[:-1],
            mrt_record(bad_payload, mrt_type=12, subtype=bad_subtype),
        )
        for raw in cases:
            with self.subTest(length=len(raw)), self.assertRaises(RibMrtParseError):
                parse_rib_mrt_bytes(raw)

    def test_missing_peer_table_and_peer_index_out_of_bounds_fail_closed(self):
        subtype, payload = v2_rib_payload(
            "203.0.113.0/24",
            (0, ORIGIN_TIME, attributes(as_path_value(4, (2, (64500,))))),
        )
        rib = mrt_record(payload, mrt_type=13, subtype=subtype)
        with self.assertRaisesRegex(RibMrtParseError, "PEER_INDEX_TABLE"):
            parse_rib_mrt_bytes(rib)

        peer = mrt_record(
            peer_index_payload(("192.0.2.1", 64500, False)),
            mrt_type=13,
            subtype=1,
        )
        bad_subtype, bad_payload = v2_rib_payload(
            "203.0.113.0/24",
            (1, ORIGIN_TIME, attributes(as_path_value(4, (2, (64500,))))),
        )
        with self.assertRaisesRegex(RibMrtParseError, "peer_index"):
            parse_rib_mrt_bytes(
                peer + mrt_record(bad_payload, mrt_type=13, subtype=bad_subtype)
            )

    def test_as_path_is_strict_about_missing_duplicate_flags_and_segments(self):
        invalid_attrs = (
            attribute(1, b"\x00", flags=0x40),
            attribute(2, b"", flags=0x80),
            attribute(2, b"") + attribute(2, b""),
            attribute(2, b"\x09\x01\x00\x01"),
            attribute(2, b"\x02\x00"),
        )
        for index, attrs in enumerate(invalid_attrs):
            subtype, payload = table_dump_payload(
                prefix="203.0.113.0/24",
                peer_ip="192.0.2.1",
                peer_asn=64500,
                attrs=attrs,
                sequence=index,
            )
            with self.subTest(index=index), self.assertRaises(RibMrtParseError):
                parse_rib_mrt_bytes(
                    mrt_record(payload, mrt_type=12, subtype=subtype)
                )

    def test_short_read_binary_stream_is_supported(self):
        attrs = attributes(as_path_value(2, (2, (64500,))))
        subtype, payload = table_dump_payload(
            prefix="203.0.113.0/24",
            peer_ip="192.0.2.1",
            peer_asn=64500,
            attrs=attrs,
        )
        raw = mrt_record(payload, mrt_type=12, subtype=subtype)
        records = tuple(iter_rib_mrt_records(ShortReadStream(raw)))
        self.assertEqual(records[0].raw_record, raw)

    def test_route_event_vp_and_raw_reference_hashes_are_stable_and_replayable(self):
        peer_record = mrt_record(
            peer_index_payload(("192.0.2.1", 64500, False)),
            mrt_type=13,
            subtype=1,
        )
        subtype, payload = v2_rib_payload(
            "203.0.113.0/24",
            (0, ORIGIN_TIME, attributes(as_path_value(4, (2, (64500, 44244))))),
            (0, ORIGIN_TIME + 1, attributes(as_path_value(4, (2, (64500, 44245))))),
        )
        rib_record = mrt_record(payload, mrt_type=13, subtype=subtype)
        raw_file = peer_record + rib_record
        file_sha256 = hashlib.sha256(raw_file).hexdigest()
        record = parse_rib_mrt_bytes(raw_file)[1]

        identities = tuple(
            route_event_id_v1(file_sha256, record.record_ordinal, ordinal)
            for ordinal, _element in enumerate(record.elements)
        )
        self.assertEqual(len(set(identities)), 2)
        self.assertEqual(
            identities[0], route_event_id_v1(file_sha256, 1, 0)
        )
        self.assertEqual(
            vp_id_v1("rrc25", record.elements[0].peer_ip, record.elements[0].peer_asn),
            vp_id_v1("rrc25", "192.0.2.1", 64500),
        )

        raw_ref = {
            "record_ordinal": record.record_ordinal,
            "record_offset": record.record_offset,
            "record_length": len(record.raw_record),
            "record_sha256": hashlib.sha256(record.raw_record).hexdigest(),
        }
        replayed = raw_file[
            raw_ref["record_offset"] : raw_ref["record_offset"]
            + raw_ref["record_length"]
        ]
        self.assertEqual(replayed, record.raw_record)
        self.assertEqual(hashlib.sha256(replayed).hexdigest(), raw_ref["record_sha256"])

    def test_build_and_verify_addressable_spool_with_checkpoint_binding(self):
        peer_record = mrt_record(
            peer_index_payload(("192.0.2.1", 64500, False)),
            mrt_type=13,
            subtype=1,
        )
        subtype, payload = v2_rib_payload(
            "203.0.113.0/24",
            (0, ORIGIN_TIME, attributes(as_path_value(4, (2, (64500, 44244))))),
        )
        raw = peer_record + mrt_record(payload, mrt_type=13, subtype=subtype)
        compressed = gzip.compress(raw, mtime=0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bview.fixture.gz"
            destination = root / "seed-rib.mrt"
            source.write_bytes(compressed)

            result = build_rib_decompressed_spool(
                source,
                destination,
                expected_compressed_sha256=hashlib.sha256(compressed).hexdigest(),
                expected_compressed_size_bytes=len(compressed),
                expected_decompressed_sha256=hashlib.sha256(raw).hexdigest(),
                expected_decompressed_size_bytes=len(raw),
                max_temporary_bytes=len(raw) + 1,
            )

            self.assertEqual(destination.read_bytes(), raw)
            self.assertTrue(destination.is_file())
            self.assertFalse(destination.is_symlink())
            self.assertEqual(result.compressed_size_bytes, len(compressed))
            self.assertEqual(
                result.checkpoint_binding(),
                {
                    "schema_version": "rrc25-seed-decompressed-spool/v1",
                    "file_name": "seed-rib.mrt",
                    "size_bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                },
            )
            verified = verify_rib_decompressed_spool(
                destination,
                expected_decompressed_sha256=hashlib.sha256(raw).hexdigest(),
                expected_decompressed_size_bytes=len(raw),
            )
            self.assertEqual(verified.checkpoint_binding(), result.spool.checkpoint_binding())
            self.assertFalse(any(".tmp-" in path.name for path in root.iterdir()))

    def test_spool_limit_is_strict_and_failure_never_publishes(self):
        raw = b"exact-temporary-limit"
        compressed = gzip.compress(raw, mtime=0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.gz"
            destination = root / "spool.mrt"
            source.write_bytes(compressed)

            with self.assertRaises(RibSpoolError) as caught:
                build_rib_decompressed_spool(
                    source,
                    destination,
                    expected_compressed_sha256=hashlib.sha256(compressed).hexdigest(),
                    expected_compressed_size_bytes=len(compressed),
                    max_temporary_bytes=len(raw),
                )
            self.assertEqual(caught.exception.reason, "temporary_limit_reached")
            self.assertFalse(destination.exists())
            self.assertEqual(tuple(root.iterdir()), (source,))

            with self.assertRaises(RibSpoolError) as caught:
                build_rib_decompressed_spool(
                    source,
                    destination,
                    expected_compressed_sha256="0" * 64,
                    expected_compressed_size_bytes=len(compressed),
                    max_temporary_bytes=len(raw) + 1,
                )
            self.assertEqual(caught.exception.reason, "compressed_sha256_mismatch")
            self.assertFalse(destination.exists())
            self.assertEqual(tuple(root.iterdir()), (source,))

            destination.write_bytes(b"do-not-overwrite")
            with self.assertRaises(RibSpoolError) as caught:
                build_rib_decompressed_spool(
                    source,
                    destination,
                    expected_compressed_sha256=hashlib.sha256(compressed).hexdigest(),
                    expected_compressed_size_bytes=len(compressed),
                    max_temporary_bytes=len(raw) + 1,
                )
            self.assertEqual(caught.exception.reason, "destination_exists")
            self.assertEqual(destination.read_bytes(), b"do-not-overwrite")

    def test_spool_verifier_rejects_symlink_size_and_hash_mismatch(self):
        raw = b"verified-spool"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spool = root / "spool.mrt"
            spool.write_bytes(raw)
            link = root / "spool-link.mrt"
            os.symlink(spool.name, link)

            cases = (
                (link, hashlib.sha256(raw).hexdigest(), len(raw), "spool_not_regular"),
                (spool, hashlib.sha256(raw).hexdigest(), len(raw) + 1, "spool_size_mismatch"),
                (spool, "0" * 64, len(raw), "spool_sha256_mismatch"),
            )
            for path, digest, size, reason in cases:
                with self.subTest(reason=reason):
                    with self.assertRaises(RibSpoolError) as caught:
                        verify_rib_decompressed_spool(
                            path,
                            expected_decompressed_sha256=digest,
                            expected_decompressed_size_bytes=size,
                        )
                    self.assertEqual(caught.exception.reason, reason)

    def test_seek_iterator_restores_peer_context_from_spool_only(self):
        peers = (
            ("192.0.2.10", 64510, False),
            ("2001:db8::20", 4_200_000_020, True),
        )
        peer_record = mrt_record(
            peer_index_payload(*peers), mrt_type=13, subtype=1
        )
        first_subtype, first_payload = v2_rib_payload(
            "198.51.100.0/24",
            (0, ORIGIN_TIME, attributes(as_path_value(4, (2, (64510, 64496))))),
        )
        first_record = mrt_record(
            first_payload, mrt_type=13, subtype=first_subtype
        )
        second_subtype, second_payload = v2_rib_payload(
            "203.0.113.0/24",
            (
                1,
                ORIGIN_TIME + 1,
                attributes(as_path_value(4, (2, (4_200_000_020, 44244)))),
            ),
        )
        second_record = mrt_record(
            second_payload, mrt_type=13, subtype=second_subtype
        )
        raw = peer_record + first_record + second_record
        compressed = gzip.compress(raw, mtime=0)
        resume_offset = len(peer_record) + len(first_record)
        bootstrap = iter_rib_mrt_records_from_offset(
            raw, next_record_ordinal=0, next_record_offset=0
        )
        next(bootstrap)
        peer_context = bootstrap.current_peer_index_context
        next(bootstrap)
        previous_boundary = bootstrap.previous_record_boundary
        self.assertIsNotNone(peer_context)
        self.assertIsNotNone(previous_boundary)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.gz"
            spool = root / "spool.mrt"
            source.write_bytes(compressed)
            result = build_rib_decompressed_spool(
                source,
                spool,
                expected_compressed_sha256=hashlib.sha256(compressed).hexdigest(),
                expected_compressed_size_bytes=len(compressed),
                max_temporary_bytes=len(raw) + 1,
            )
            # 恢复不再依赖 gzip；删除 fixture 压缩源后仍只读 spool 成功。
            source.unlink()

            with self.assertRaises(RibMrtSeekError) as missing_boundary:
                iter_rib_spool_records(
                    spool,
                    expected_decompressed_sha256=result.spool.sha256,
                    expected_decompressed_size_bytes=result.spool.size_bytes,
                    next_record_ordinal=2,
                    next_record_offset=resume_offset,
                )
            self.assertEqual(
                missing_boundary.exception.reason,
                "previous_record_boundary_required",
            )

            with iter_rib_spool_records(
                spool,
                expected_decompressed_sha256=result.spool.sha256,
                expected_decompressed_size_bytes=result.spool.size_bytes,
                next_record_ordinal=2,
                next_record_offset=resume_offset,
                previous_record_boundary=previous_boundary.checkpoint_binding(),
                peer_index_context=peer_context.checkpoint_binding(),
            ) as records:
                context = records.seek_context
                self.assertEqual(context.peer_index.record_ordinal, 0)
                self.assertEqual(context.peer_index.record_offset, 0)
                self.assertEqual(
                    context.peer_index.peers,
                    (("192.0.2.10", 64510), ("2001:db8::20", 4_200_000_020)),
                )
                resumed = tuple(records)
            self.assertEqual(len(resumed), 1)
            self.assertEqual(
                (resumed[0].record_ordinal, resumed[0].record_offset),
                (2, resume_offset),
            )
            self.assertEqual(
                (resumed[0].elements[0].peer_ip, resumed[0].elements[0].peer_asn),
                ("2001:db8::20", 4_200_000_020),
            )

            with self.assertRaises(RibMrtSeekError) as ordinal_error:
                iter_rib_mrt_records_from_offset(
                    raw,
                    next_record_ordinal=1,
                    next_record_offset=resume_offset,
                    previous_record_boundary=previous_boundary,
                    peer_index_context=peer_context,
                )
            self.assertEqual(ordinal_error.exception.reason, "seek_ordinal_mismatch")
            with self.assertRaises(RibMrtSeekError) as offset_error:
                iter_rib_mrt_records_from_offset(
                    raw,
                    next_record_ordinal=2,
                    next_record_offset=len(peer_record) + 1,
                    previous_record_boundary=previous_boundary,
                    peer_index_context=peer_context,
                )
            self.assertEqual(
                offset_error.exception.reason, "seek_offset_not_record_boundary"
            )

            bad_boundary = previous_boundary.checkpoint_binding()
            bad_boundary["record_sha256"] = "0" * 64
            with self.assertRaises(RibMrtSeekError) as hash_error:
                iter_rib_mrt_records_from_offset(
                    raw,
                    next_record_ordinal=2,
                    next_record_offset=resume_offset,
                    previous_record_boundary=bad_boundary,
                    peer_index_context=peer_context,
                )
            self.assertEqual(
                hash_error.exception.reason, "record_boundary_sha256_mismatch"
            )

            bad_peer_context = peer_context.checkpoint_binding()
            bad_peer_context["peers"][0]["peer_asn"] += 1
            with self.assertRaises(RibMrtSeekError) as peer_error:
                iter_rib_mrt_records_from_offset(
                    raw,
                    next_record_ordinal=2,
                    next_record_offset=resume_offset,
                    previous_record_boundary=previous_boundary,
                    peer_index_context=bad_peer_context,
                )
            self.assertEqual(
                peer_error.exception.reason, "peer_index_population_mismatch"
            )

    def test_direct_seek_read_volume_is_independent_of_target_distance(self):
        peer_record = mrt_record(
            peer_index_payload(("192.0.2.10", 64510, False)),
            mrt_type=13,
            subtype=1,
        )
        physical = [peer_record]
        for sequence in range(1, 221):
            subtype, payload = v2_rib_payload(
                "198.51.100.0/24",
                (
                    0,
                    ORIGIN_TIME,
                    attributes(as_path_value(4, (2, (64510, 64496)))),
                ),
                sequence=sequence,
            )
            physical.append(mrt_record(payload, mrt_type=13, subtype=subtype))
        offsets = [0]
        for record in physical:
            offsets.append(offsets[-1] + len(record))
        raw = b"".join(physical)
        bootstrap = iter_rib_mrt_records_from_offset(
            raw, next_record_ordinal=0, next_record_offset=0
        )
        next(bootstrap)
        peer_context = bootstrap.current_peer_index_context

        def boundary_before(target_ordinal):
            ordinal = target_ordinal - 1
            record = physical[ordinal]
            return {
                "record_ordinal": ordinal,
                "record_offset": offsets[ordinal],
                "record_length": len(record),
                "record_sha256": hashlib.sha256(record).hexdigest(),
            }

        read_totals = []
        for target_ordinal in (2, 200):
            stream = CountingSeekStream(raw)
            iterator = iter_rib_mrt_records_from_offset(
                stream,
                next_record_ordinal=target_ordinal,
                next_record_offset=offsets[target_ordinal],
                previous_record_boundary=boundary_before(target_ordinal),
                peer_index_context=peer_context.checkpoint_binding(),
            )
            self.assertEqual(stream.tell(), offsets[target_ordinal])
            self.assertEqual(
                iterator.seek_context.next_record_ordinal, target_ordinal
            )
            read_totals.append(stream.bytes_read)

        # 定位阶段只读取 peer table 与上一条完整 record，不能随 target 线性增长。
        self.assertEqual(read_totals[0], read_totals[1])
        self.assertEqual(read_totals[1], len(peer_record) + len(physical[199]))
        self.assertLess(read_totals[1] * 20, offsets[200])


if __name__ == "__main__":
    unittest.main()
