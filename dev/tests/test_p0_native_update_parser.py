from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import ipaddress
from pathlib import Path
import struct
import tempfile
import unittest

from backend.data_pipeline.route_event import (
    NATIVE_UPDATE_COMMAND_TOKEN,
    NATIVE_UPDATE_EXECUTION_POLICY,
    NATIVE_UPDATE_PARSER_NAME,
    NATIVE_UPDATE_PARSER_VERSION,
    ImportProvenance,
    NativeUpdateConfigurationError,
    NativeUpdateIntegrityError,
    NativeUpdateRecordStreamFactory,
    RouteEventIndex,
    RouteEventInputError,
    artifact_id_v1,
    build_route_event_index,
    canonical_json,
    derive_update_pilot_selection,
    verify_update_pilot_selection,
)
from dev.tests.test_p0_bgpdump_adapter import (
    BGPDUMP_APPROVED_VERSION,
    BgpdumpAdapterFixture,
    FakeBehavior,
    FakePopenFactory,
    make_manifest,
    normalized_artifact,
)


UTC = timezone.utc
SLOT = datetime(2026, 2, 27, 16, 0, tzinfo=UTC)
SLOT_TIMESTAMP = int(SLOT.timestamp())


def nlri(prefixes):
    payload = bytearray()
    for value in prefixes:
        network = ipaddress.ip_network(value, strict=False)
        payload.append(network.prefixlen)
        octets = (network.prefixlen + 7) // 8
        payload.extend(network.network_address.packed[:octets])
    return bytes(payload)


def path_payload(segments, width):
    payload = bytearray()
    kind = {
        "as_set": 1,
        "as_sequence": 2,
        "confederation_sequence": 3,
        "confederation_set": 4,
    }
    for segment_type, asns in segments:
        payload.extend((kind[segment_type], len(asns)))
        for asn in asns:
            payload.extend(int(asn).to_bytes(width, "big"))
    return bytes(payload)


def attribute(flags, attribute_type, value):
    value = bytes(value)
    if len(value) > 255:
        return bytes((flags | 0x10, attribute_type)) + struct.pack("!H", len(value)) + value
    return bytes((flags, attribute_type, len(value))) + value


def mp_reach(prefixes, afi):
    next_hop = (
        ipaddress.ip_address("192.0.2.1").packed
        if afi == 1
        else ipaddress.ip_address("2001:db8::1").packed
    )
    return (
        struct.pack("!HB", afi, 1)
        + bytes((len(next_hop),))
        + next_hop
        + b"\x00"
        + nlri(prefixes)
    )


def mp_unreach(prefixes, afi):
    return struct.pack("!HB", afi, 1) + nlri(prefixes)


def update_frame(
    *,
    timestamp=SLOT_TIMESTAMP + 1,
    mrt_type=16,
    subtype=4,
    microseconds=0,
    peer_asn=64500,
    peer_ip="192.0.2.10",
    withdraws=(),
    announces=(),
    mp_withdraws=(),
    mp_announces=(),
    mp_afi=2,
    path=(("as_sequence", (64500, 64496)),),
    as4_path=None,
    extra_attributes=(),
):
    width = 2 if subtype == 1 else 4
    attributes = bytearray()
    if announces or mp_announces:
        attributes.extend(attribute(0x40, 1, b"\x00"))
        attributes.extend(attribute(0x40, 2, path_payload(path, width)))
    if announces:
        attributes.extend(
            attribute(0x40, 3, ipaddress.ip_address("192.0.2.1").packed)
        )
    if as4_path is not None:
        attributes.extend(attribute(0xC0, 17, path_payload(as4_path, 4)))
    if mp_withdraws:
        attributes.extend(attribute(0x80, 15, mp_unreach(mp_withdraws, mp_afi)))
    if mp_announces:
        attributes.extend(attribute(0x80, 14, mp_reach(mp_announces, mp_afi)))
    for value in extra_attributes:
        attributes.extend(value)
    body = (
        struct.pack("!H", len(nlri(withdraws)))
        + nlri(withdraws)
        + struct.pack("!H", len(attributes))
        + attributes
        + nlri(announces)
    )
    message = b"\xff" * 16 + struct.pack("!HB", 19 + len(body), 2) + body
    peer_address = ipaddress.ip_address(peer_ip)
    identity_afi = 1 if peer_address.version == 4 else 2
    if subtype == 1:
        identity = struct.pack("!HHHH", peer_asn, 64496, 0, identity_afi)
    else:
        identity = struct.pack("!IIHH", peer_asn, 64496, 0, identity_afi)
    local_address = ipaddress.ip_address(
        "192.0.2.1" if identity_afi == 1 else "2001:db8::ffff"
    )
    payload = (
        identity
        + peer_address.packed
        + local_address.packed
        + message
    )
    if mrt_type == 17:
        payload = struct.pack("!I", microseconds) + payload
    return struct.pack("!IHHI", timestamp, mrt_type, subtype, len(payload)) + payload


def state_frame(*, timestamp=SLOT_TIMESTAMP + 2, subtype=5, old=6, new=1):
    width = 2 if subtype == 0 else 4
    identity = (
        int(64500).to_bytes(width, "big")
        + int(64496).to_bytes(width, "big")
        + struct.pack("!HH", 0, 1)
    )
    payload = (
        identity
        + ipaddress.ip_address("192.0.2.10").packed
        + ipaddress.ip_address("192.0.2.1").packed
        + struct.pack("!HH", old, new)
    )
    return struct.pack("!IHHI", timestamp, 16, subtype, len(payload)) + payload


def keepalive_frame(*, timestamp=SLOT_TIMESTAMP + 3):
    message = b"\xff" * 16 + struct.pack("!HB", 19, 4)
    payload = (
        struct.pack("!IIHH", 64500, 64496, 0, 1)
        + ipaddress.ip_address("192.0.2.10").packed
        + ipaddress.ip_address("192.0.2.1").packed
        + message
    )
    return struct.pack("!IHHI", timestamp, 16, 4, len(payload)) + payload


def bgpdump_line(ordinal, timestamp, action, prefix, path=""):
    common = [
        "BGP4MP",
        str(ordinal),
        str(timestamp),
        action,
        "192.0.2.10",
        "64500",
        prefix,
    ]
    if action == "W":
        return ("|".join(common) + "\n").encode("ascii")
    return (
        "|".join(
            common
            + [path, "IGP", "192.0.2.1", "0", "0", "", "NAG", "", ""]
        )
        + "\n"
    ).encode("ascii")


def state_line(ordinal, timestamp, old=6, new=1):
    return (
        f"BGP4MP|{ordinal}|{timestamp}|STATE|192.0.2.10|64500|{old}|{new}\n"
    ).encode("ascii")


class NativeUpdateParserTests(BgpdumpAdapterFixture):
    def native_factory(self, artifacts, **overrides):
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
        }
        options.update(overrides)
        return NativeUpdateRecordStreamFactory(self.raw_root, artifacts, **options)

    def test_golden_mixed_artifact_matches_bgpdump_parsed_records_field_by_field(self):
        path = (
            ("as_sequence", (64500,)),
            ("as_set", (64496, 64497)),
            ("as_sequence", (65000,)),
        )
        first = update_frame(
            withdraws=("198.51.100.0/24",),
            announces=("203.0.113.0/24",),
            mp_withdraws=("2001:db8:1::/48",),
            mp_announces=("2001:db8:2::/48",),
            path=path,
        )
        second = state_frame()
        third = keepalive_frame()
        artifact = self.write_artifact((first, second, third))
        expected_lines = {
            0: (
                bgpdump_line(0, SLOT_TIMESTAMP + 1, "W", "198.51.100.0/24"),
                bgpdump_line(0, SLOT_TIMESTAMP + 1, "W", "2001:db8:1::/48"),
                bgpdump_line(
                    0,
                    SLOT_TIMESTAMP + 1,
                    "A",
                    "203.0.113.0/24",
                    "64500 {64496,64497} 65000",
                ),
                bgpdump_line(
                    0,
                    SLOT_TIMESTAMP + 1,
                    "A",
                    "2001:db8:2::/48",
                    "64500 {64496,64497} 65000",
                ),
            ),
            1: (state_line(1, SLOT_TIMESTAMP + 2),),
            2: (),
        }
        popen = FakePopenFactory(
            FakeBehavior(lambda ordinal, _raw: expected_lines[ordinal])
        )
        bgpdump_factory = self.factory((artifact,), popen, queue_capacity=4096)
        native_factory = self.native_factory((artifact,))

        bgpdump_stream = bgpdump_factory(normalized_artifact(artifact))
        native_stream = native_factory(normalized_artifact(artifact))
        bgpdump_records = tuple(bgpdump_stream)
        native_records = tuple(native_stream)

        self.assertEqual(native_records, bgpdump_records)
        self.assertEqual(
            native_stream.statistics["record_hash_chain_sha256"],
            bgpdump_stream.statistics["record_hash_chain_sha256"],
        )
        self.assertEqual(native_stream.statistics["compressed_read_passes"], 1)
        self.assertEqual(
            native_stream.statistics["compressed_bytes_read_observed"],
            artifact["size_bytes"],
        )
        self.assertEqual(native_stream.statistics["route_element_count"], 4)
        self.assertEqual(native_stream.statistics["state_change_record_count"], 1)
        self.assertEqual(native_stream.statistics["keepalive_record_count"], 1)

    def test_as4_path_merge_matches_bgpdump_observation(self):
        frame = update_frame(
            subtype=1,
            announces=("203.0.113.0/24",),
            path=(("as_sequence", (65000, 23456, 23456)),),
            as4_path=(("as_sequence", (70000, 80000)),),
        )
        artifact = self.write_artifact((frame,))
        popen = FakePopenFactory(
            FakeBehavior(
                lambda ordinal, _raw: (
                    bgpdump_line(
                        ordinal,
                        SLOT_TIMESTAMP + 1,
                        "A",
                        "203.0.113.0/24",
                        "65000 70000 80000",
                    ),
                )
            )
        )
        bgpdump_stream = self.factory((artifact,), popen)(normalized_artifact(artifact))
        native_stream = self.native_factory((artifact,))(
            normalized_artifact(artifact)
        )
        self.assertEqual(tuple(native_stream), tuple(bgpdump_stream))

    def test_extended_timestamp_and_mp_ipv4_are_preserved(self):
        frame = update_frame(
            timestamp=SLOT_TIMESTAMP + 4,
            mrt_type=17,
            microseconds=123456,
            mp_afi=1,
            mp_withdraws=("198.51.100.0/24",),
            mp_announces=("203.0.113.0/24",),
        )
        artifact = self.write_artifact((frame,))
        record = tuple(
            self.native_factory((artifact,))(normalized_artifact(artifact))
        )[0]
        self.assertEqual(
            [element.event_time_utc for element in record.elements],
            ["2026-02-27T16:00:04.123456Z"] * 2,
        )
        self.assertEqual(
            [(element.action, element.afi_safi) for element in record.elements],
            [("withdraw", "ipv4_unicast"), ("announce", "ipv4_unicast")],
        )

    def test_ipv6_peer_and_confederation_segments_are_preserved(self):
        frame = update_frame(
            peer_ip="2001:db8::10",
            mp_announces=("2001:db8:10::/48",),
            path=(
                ("confederation_sequence", (64501, 64502)),
                ("confederation_set", (64503, 64504)),
                ("as_sequence", (64500, 64496)),
            ),
        )
        artifact = self.write_artifact((frame,))
        record = tuple(
            self.native_factory((artifact,))(normalized_artifact(artifact))
        )[0]
        element = record.elements[0]
        self.assertEqual(element.peer_ip, "2001:db8::10")
        self.assertEqual(
            [(segment.segment_type, segment.asns) for segment in element.as_path],
            [
                ("confederation_sequence", (64501, 64502)),
                ("confederation_set", (64503, 64504)),
                ("as_sequence", (64500, 64496)),
            ],
        )

    def test_only_to_customer_attribute_is_validated_and_ignored_for_route_semantics(self):
        frame = update_frame(
            announces=("203.0.113.0/24",),
            extra_attributes=(attribute(0xC0, 35, struct.pack("!I", 64500)),),
        )
        artifact = self.write_artifact((frame,))
        record = tuple(
            self.native_factory((artifact,))(normalized_artifact(artifact))
        )[0]
        self.assertEqual(
            [(element.action, element.prefix) for element in record.elements],
            [("announce", "203.0.113.0/24")],
        )

    def test_optional_development_attribute_is_opaque_but_well_known_form_is_rejected(self):
        frame = update_frame(
            announces=("203.0.113.0/24",),
            extra_attributes=(attribute(0x80, 255, b"rrc25"),),
        )
        artifact = self.write_artifact((frame,))
        record = tuple(
            self.native_factory((artifact,))(normalized_artifact(artifact))
        )[0]
        self.assertEqual(record.elements[0].prefix, "203.0.113.0/24")

        malformed = update_frame(
            announces=("203.0.113.0/24",),
            extra_attributes=(attribute(0x40, 255, b"rrc25"),),
        )
        malformed_artifact = self.write_artifact(
            (malformed,), name="updates.20260227.1645.gz"
        )
        with self.assertRaisesRegex(NativeUpdateIntegrityError, "flags"):
            tuple(
                self.native_factory((malformed_artifact,))(
                    normalized_artifact(malformed_artifact)
                )
            )

    def test_unknown_duplicate_addpath_and_ambiguous_attributes_fail_closed(self):
        valid_path = path_payload((("as_sequence", (64500, 64496)),), 4)
        cases = (
            update_frame(
                announces=("203.0.113.0/24",),
                extra_attributes=(attribute(0x80, 99, b"x"),),
            ),
            update_frame(
                announces=("203.0.113.0/24",),
                extra_attributes=(attribute(0x40, 2, valid_path),),
            ),
            update_frame(
                subtype=4,
                announces=("203.0.113.0/24",),
                as4_path=(("as_sequence", (70000,)),),
            ),
            update_frame(subtype=8, withdraws=("198.51.100.0/24",)),
        )
        for index, frame in enumerate(cases):
            with self.subTest(index=index):
                artifact = self.write_artifact((frame,), name=f"updates.20260227.16{index:02d}.gz")
                factory = self.native_factory((artifact,))
                with self.assertRaises(NativeUpdateIntegrityError):
                    tuple(factory(normalized_artifact(artifact)))

    def test_ipv4_and_mp_end_of_rib_are_control_records(self):
        ipv4 = update_frame()
        mp = update_frame(
            extra_attributes=(
                attribute(0x80, 15, struct.pack("!HB", 2, 1)),
            )
        )
        artifact = self.write_artifact((ipv4, mp), name="updates.20260227.1750.gz")
        stream = self.native_factory((artifact,))(normalized_artifact(artifact))
        records = tuple(stream)
        self.assertEqual(len(records), 2)
        self.assertEqual([record.elements for record in records], [(), ()])
        self.assertEqual(stream.statistics["end_of_rib_record_count"], 2)
        self.assertEqual(stream.statistics["route_record_count"], 0)

    def test_non_eor_empty_update_bad_state_and_noncanonical_nlri_fail_closed(self):
        empty = update_frame(
            extra_attributes=(attribute(0x40, 1, b"\x00"),)
        )
        bad_state = state_frame(old=0)
        malformed = bytearray(update_frame(withdraws=("198.51.100.0/25",)))
        # Locate the /25 NLRI last octet and set one host bit. The raw prefix is
        # near the end but attributes/body offsets are deliberately not assumed.
        marker = bytes((25, 198, 51, 100, 0))
        at = bytes(malformed).find(marker)
        self.assertGreater(at, 0)
        malformed[at + 4] = 1
        for index, frame in enumerate((empty, bad_state, bytes(malformed))):
            artifact = self.write_artifact((frame,), name=f"updates.20260227.17{index:02d}.gz")
            stream = self.native_factory((artifact,))(
                normalized_artifact(artifact)
            )
            with self.assertRaises(NativeUpdateIntegrityError):
                tuple(stream)
            self.assertGreater(
                stream.statistics["compressed_bytes_read_observed"], 0
            )

    def test_file_hash_path_and_single_use_gates_are_not_weakened(self):
        frame = update_frame(withdraws=("198.51.100.0/24",))
        artifact = self.write_artifact((frame,))
        factory = self.native_factory((artifact,))
        stream = factory(normalized_artifact(artifact))
        self.assertEqual(len(tuple(stream)), 1)
        with self.assertRaisesRegex(NativeUpdateConfigurationError, "重复"):
            factory(normalized_artifact(artifact))
        with self.assertRaisesRegex(RouteEventInputError, "只能消费一次"):
            tuple(stream)

        damaged = dict(artifact)
        damaged["file_sha256"] = "0" * 64
        with self.assertRaises(NativeUpdateConfigurationError):
            self.native_factory((damaged,))

        hash_factory = self.native_factory((artifact,))
        raw_path = self.raw_root / artifact["relative_path"]
        compressed = bytearray(raw_path.read_bytes())
        compressed[4] ^= 1  # gzip mtime，不改变解压内容、长度或 CRC。
        raw_path.write_bytes(compressed)
        with self.assertRaisesRegex(NativeUpdateIntegrityError, "SHA256"):
            tuple(hash_factory(normalized_artifact(artifact)))

    def test_symlink_artifact_path_is_rejected_before_read(self):
        frame = update_frame(withdraws=("198.51.100.0/24",))
        artifact = self.write_artifact((frame,))
        factory = self.native_factory((artifact,))
        raw_path = self.raw_root / artifact["relative_path"]
        target = raw_path.with_name("immutable-target.gz")
        raw_path.replace(target)
        raw_path.symlink_to(target.name)
        with self.assertRaisesRegex(NativeUpdateConfigurationError, "符号链接"):
            factory(normalized_artifact(artifact))

    def test_attestation_is_explicit_and_index_rejects_native_token_drift(self):
        frame = update_frame(withdraws=("198.51.100.0/24",))
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
        factory = self.native_factory(
            (artifact,), pilot_limits=selection["limits"]
        )
        attestation = factory.parser_attestation
        self.assertEqual(attestation["parser_name"], NATIVE_UPDATE_PARSER_NAME)
        self.assertEqual(attestation["parser_version"], NATIVE_UPDATE_PARSER_VERSION)
        self.assertEqual(
            attestation["binary_execution_policy"], NATIVE_UPDATE_EXECUTION_POLICY
        )
        self.assertEqual(
            attestation["configuration"]["command_arguments"],
            [NATIVE_UPDATE_COMMAND_TOKEN],
        )
        result = build_route_event_index(
            self.root / "native-index.sqlite",
            manifest=manifest,
            manifest_verification=verification,
            provenance=ImportProvenance(
                parser_name=NATIVE_UPDATE_PARSER_NAME,
                parser_version=NATIVE_UPDATE_PARSER_VERSION,
                importer_name="domeye_route_ingest",
                importer_version="1.0.0",
                processing_time_utc="2026-04-01T00:00:00Z",
                config={},
            ),
            record_stream_factory=factory,
            artifact_selection=selection,
            selection_verification=selection_verification,
        )
        self.assertEqual(result.summary["route_event_count"], 1)
        with RouteEventIndex(result.path) as index:
            reconciliation = index.reconciliation_summary(
                raw_root=self.raw_root,
                artifact_selection=selection,
            )
        self.assertEqual(
            reconciliation["processing_lineage_missing_count"], 0
        )

        bad_factory = self.native_factory(
            (artifact,), pilot_limits=selection["limits"]
        )
        bad = bad_factory.parser_attestation
        bad["configuration"]["command_arguments"] = ["arbitrary-path"]
        bad["configuration_sha256"] = hashlib.sha256(
            canonical_json(bad["configuration"]).encode("utf-8")
        ).hexdigest()
        payload = dict(bad)
        payload.pop("attestation_fingerprint_sha256")
        bad["attestation_fingerprint_sha256"] = hashlib.sha256(
            canonical_json(
                {
                    "schema": "parser_attestation_fingerprint_v1",
                    "attestation": payload,
                }
            ).encode("utf-8")
        ).hexdigest()
        bad_factory._parser_attestation = bad
        with self.assertRaisesRegex(RouteEventInputError, "执行合同"):
            build_route_event_index(
                self.root / "native-bad-index.sqlite",
                manifest=manifest,
                manifest_verification=verification,
                provenance=ImportProvenance(
                    parser_name=NATIVE_UPDATE_PARSER_NAME,
                    parser_version=NATIVE_UPDATE_PARSER_VERSION,
                    importer_name="domeye_route_ingest",
                    importer_version="1.0.0",
                    processing_time_utc="2026-04-01T00:00:00Z",
                    config={},
                ),
                record_stream_factory=bad_factory,
                artifact_selection=selection,
                selection_verification=selection_verification,
            )

        unknown_factory = self.native_factory(
            (artifact,), pilot_limits=selection["limits"]
        )
        unknown = unknown_factory.parser_attestation
        unknown["parser_name"] = "native_bgp4mp_update_custom"
        unknown_payload = dict(unknown)
        unknown_payload.pop("attestation_fingerprint_sha256")
        unknown["attestation_fingerprint_sha256"] = hashlib.sha256(
            canonical_json(
                {
                    "schema": "parser_attestation_fingerprint_v1",
                    "attestation": unknown_payload,
                }
            ).encode("utf-8")
        ).hexdigest()
        unknown_factory._parser_attestation = unknown
        with self.assertRaisesRegex(RouteEventInputError, "allowlist"):
            build_route_event_index(
                self.root / "native-unknown-index.sqlite",
                manifest=manifest,
                manifest_verification=verification,
                provenance=ImportProvenance(
                    parser_name="native_bgp4mp_update_custom",
                    parser_version=NATIVE_UPDATE_PARSER_VERSION,
                    importer_name="domeye_route_ingest",
                    importer_version="1.0.0",
                    processing_time_utc="2026-04-01T00:00:00Z",
                    config={},
                ),
                record_stream_factory=unknown_factory,
                artifact_selection=selection,
                selection_verification=selection_verification,
            )

    def test_bounded_100k_elements_are_single_pass_and_coordinate_complete(self):
        chunks = []
        frames = []
        for ordinal in range(10):
            prefixes = tuple(
                str(ipaddress.ip_address(0x0A000000 + ordinal * 10_000 + index))
                + "/32"
                for index in range(10_000)
            )
            chunks.append(prefixes)
            frames.append(
                update_frame(
                    timestamp=SLOT_TIMESTAMP + ordinal,
                    withdraws=prefixes,
                )
            )
        artifact = self.write_artifact(tuple(frames))
        stream = self.native_factory((artifact,))(normalized_artifact(artifact))
        records = tuple(stream)
        self.assertEqual(len(records), 10)
        self.assertEqual(sum(len(record.elements) for record in records), 100_000)
        self.assertEqual(
            [record.record_ordinal for record in records], list(range(10))
        )
        self.assertEqual(stream.statistics["compressed_read_passes"], 1)
        self.assertEqual(stream.statistics["route_element_count"], 100_000)
        self.assertRegex(
            stream.statistics["record_hash_chain_sha256"], r"^[0-9a-f]{64}$"
        )


if __name__ == "__main__":
    unittest.main()
