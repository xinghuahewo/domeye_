from __future__ import annotations

import hashlib
import io
import ipaddress
import struct
import unittest

from backend.data_pipeline.research.rrc25_country_outage.rib_parser import (
    RibMrtParseError,
    iter_rib_mrt_records,
    parse_rib_mrt_bytes,
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


if __name__ == "__main__":
    unittest.main()
