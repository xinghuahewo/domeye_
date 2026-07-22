from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import ipaddress
import struct
import unittest

from backend.data_pipeline.route_event import artifact_id_v1, route_event_id_v1
from backend.data_pipeline.research.rrc25_country_outage.rib_adapter import (
    DISCARDED_NON_TARGET,
    PEER_INDEX_RECORD,
    RETAINED_ORIGIN_UNKNOWN,
    RETAINED_PREFIX_CONTEXT,
    RETAINED_TARGET,
    RIB_RECORD,
    ObservedVpAccumulator,
    RibAdapterError,
    iter_adapted_rib_records,
    iter_rib_artifact_records,
)
from backend.data_pipeline.research.rrc25_country_outage.rib_parser import (
    iter_rib_mrt_records,
    parse_rib_mrt_bytes,
)
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    seed_state_from_rib,
)


UTC = timezone.utc
MRT_TIME = 1_772_208_000  # 2026-02-27T16:00:00Z，八小时槽边界
ORIGIN_TIME = MRT_TIME - 60
SLOT_TEXT = "2026-02-27T16:00:00Z"
FILE_SHA256 = hashlib.sha256(b"rrc25-rib-adapter-fixture").hexdigest()
ARTIFACT_ID = artifact_id_v1(FILE_SHA256)


def artifact(**overrides):
    value = {
        "artifact_id": ARTIFACT_ID,
        "file_sha256": FILE_SHA256,
        "collector_id": "rrc25",
        "artifact_type": "rib",
        "artifact_time_utc": SLOT_TEXT,
        "relative_path": "rrc25/2026.02/bview.20260227.1600.gz",
        "compression": "gz",
        "size_bytes": 1024,
    }
    value.update(overrides)
    return value


def mrt_record(payload, *, subtype, timestamp=MRT_TIME):
    return struct.pack("!IHHI", timestamp, 13, subtype, len(payload)) + payload


def as_path(*segments):
    value = bytearray()
    for segment_type, asns in segments:
        value.extend((segment_type, len(asns)))
        for asn in asns:
            value.extend(asn.to_bytes(4, "big"))
    return bytes(value)


def attributes(path):
    return bytes((0x40, 2, len(path))) + path


def peer_index_payload(*peers):
    view = b"rrc25"
    payload = bytearray(ipaddress.IPv4Address("192.0.2.254").packed)
    payload.extend(struct.pack("!H", len(view)))
    payload.extend(view)
    payload.extend(struct.pack("!H", len(peers)))
    for index, (peer_ip, peer_asn) in enumerate(peers):
        address = ipaddress.ip_address(peer_ip)
        as4 = peer_asn > 65535
        peer_type = (0x01 if address.version == 6 else 0) | (0x02 if as4 else 0)
        payload.append(peer_type)
        payload.extend(ipaddress.IPv4Address(f"198.51.100.{index + 1}").packed)
        payload.extend(address.packed)
        payload.extend(peer_asn.to_bytes(4 if as4 else 2, "big"))
    return bytes(payload)


def rib_payload(prefix, *entries, sequence=1):
    network = ipaddress.ip_network(prefix, strict=False)
    subtype = 2 if network.version == 4 else 4
    compact_length = (network.prefixlen + 7) // 8
    payload = bytearray(struct.pack("!I", sequence))
    payload.append(network.prefixlen)
    payload.extend(network.network_address.packed[:compact_length])
    payload.extend(struct.pack("!H", len(entries)))
    for peer_index, path in entries:
        attrs = attributes(path)
        payload.extend(struct.pack("!HIH", peer_index, ORIGIN_TIME, len(attrs)))
        payload.extend(attrs)
    return subtype, bytes(payload)


def mixed_rib_bytes(include_non_target_record=True):
    peers = (
        ("192.0.2.10", 64510),
        ("192.0.2.20", 64520),
        ("2001:db8::30", 4_200_000_030),
        ("2001:db8::40", 4_200_000_040),
        ("192.0.2.50", 64550),  # peer table 中存在但本 fixture 没有 route entry
    )
    peer_record = mrt_record(peer_index_payload(*peers), subtype=1)
    subtype, mixed = rib_payload(
        "203.0.113.0/24",
        (0, as_path((2, (64510, 65001)))),
        (1, as_path((2, (64520, 65100)))),
        (2, as_path((1, (65001, 65100)))),
        (3, as_path()),
    )
    values = [peer_record, mrt_record(mixed, subtype=subtype)]
    if include_non_target_record:
        subtype, non_target = rib_payload(
            "198.51.100.0/24",
            (1, as_path((2, (64520, 65100)))),
            sequence=2,
        )
        values.append(mrt_record(non_target, subtype=subtype))
    return b"".join(values), tuple(values)


class RibAdapterTests(unittest.TestCase):
    def test_round_trip_raw_ref_and_peer_index_audit(self):
        raw, physical = mixed_rib_bytes(include_non_target_record=False)
        adapted = tuple(iter_rib_artifact_records(raw, artifact=artifact()))

        self.assertEqual([item.record_kind for item in adapted], [PEER_INDEX_RECORD, RIB_RECORD])
        self.assertEqual(adapted[0].source_element_count, 0)
        self.assertEqual(adapted[0].route_events, ())
        self.assertEqual(adapted[0].raw_record.record_ordinal, 0)
        self.assertEqual(adapted[0].raw_record.record_offset, 0)
        self.assertEqual(adapted[0].raw_record.record_length, len(physical[0]))
        self.assertEqual(
            adapted[0].raw_record.raw_record_sha256,
            hashlib.sha256(physical[0]).hexdigest(),
        )

        events = tuple(event for record in adapted for event in record.route_events)
        self.assertEqual([event.element_ordinal for event in events], [0, 1, 2, 3])
        self.assertEqual(
            [event.route_event_id for event in events],
            [route_event_id_v1(FILE_SHA256, 1, ordinal) for ordinal in range(4)],
        )
        state = seed_state_from_rib(events)
        self.assertEqual(len(state.entries), 4)
        for entry in state.entries:
            raw_ref = entry.last_raw_ref
            self.assertEqual(raw_ref.artifact_id, ARTIFACT_ID)
            self.assertEqual(raw_ref.file_sha256, FILE_SHA256)
            self.assertEqual(raw_ref.record_ordinal, 1)
            self.assertEqual(
                raw_ref.route_event_id,
                route_event_id_v1(FILE_SHA256, 1, raw_ref.element_ordinal),
            )

    def test_prefix_level_filter_keeps_all_moas_and_vp_elements(self):
        raw, _physical = mixed_rib_bytes()
        unfiltered = tuple(iter_rib_artifact_records(raw, artifact=artifact()))
        predicate_calls = []

        def is_ir(asn):
            predicate_calls.append(asn)
            return asn == 65001

        filtered = tuple(
            iter_rib_artifact_records(
                raw,
                artifact=artifact(),
                origin_asn_predicate=is_ir,
            )
        )
        mixed = filtered[1]
        non_target = filtered[2]

        # 同一 prefix 内 IR、非 IR、AS_SET 和空路径四个 VP 全部保留。
        self.assertEqual([event.element_ordinal for event in mixed.route_events], [0, 1, 2, 3])
        self.assertEqual(
            [event.route_event_id for event in mixed.route_events],
            [event.route_event_id for event in unfiltered[1].route_events],
        )
        self.assertEqual(
            [decision.filter_decision for decision in mixed.element_decisions],
            [
                RETAINED_TARGET,
                RETAINED_PREFIX_CONTEXT,
                RETAINED_ORIGIN_UNKNOWN,
                RETAINED_ORIGIN_UNKNOWN,
            ],
        )
        self.assertIn("as_set_present", mixed.route_events[2].quality_flags)
        self.assertIn("origin_ambiguous", mixed.route_events[2].quality_flags)
        self.assertEqual(mixed.route_events[3].quality_flags, ("empty_as_path",))

        # 另一个全部明确为非 IR 的 prefix 可整组流式丢弃，原 element ordinal 留档。
        self.assertEqual(non_target.route_events, ())
        self.assertEqual(non_target.discarded_element_count, 1)
        self.assertEqual(non_target.element_decisions[0].element_ordinal, 0)
        self.assertEqual(
            non_target.element_decisions[0].filter_decision,
            DISCARDED_NON_TARGET,
        )
        self.assertIn(65001, predicate_calls)
        self.assertIn(65100, predicate_calls)

    def test_vp_accumulator_observes_all_peers_before_ir_filter(self):
        raw, _physical = mixed_rib_bytes()
        accumulator = ObservedVpAccumulator("rrc25")
        adapted = tuple(
            iter_rib_artifact_records(
                raw,
                artifact=artifact(),
                origin_asn_predicate=lambda asn: asn == 65001,
                vp_observer=accumulator.observe,
            )
        )

        self.assertEqual(accumulator.observed_vp_count, 5)
        self.assertEqual(len(accumulator.observed_vp_ids), 5)
        self.assertEqual(adapted[2].retained_element_count, 0)
        # 非 IR-only record 的 VP 已在过滤前进入全 RIB VP 集合。
        self.assertTrue(accumulator.observed_vp_ids)

    def test_expected_record_hash_is_checked_and_missing_ordinal_fails(self):
        raw, physical = mixed_rib_bytes(include_non_target_record=False)
        expected = {
            ordinal: hashlib.sha256(value).hexdigest()
            for ordinal, value in enumerate(physical)
        }
        self.assertEqual(
            len(
                tuple(
                    iter_rib_artifact_records(
                        raw,
                        artifact=artifact(),
                        expected_record_sha256_by_ordinal=expected,
                    )
                )
            ),
            2,
        )
        with self.assertRaisesRegex(RibAdapterError, "SHA256"):
            tuple(
                iter_rib_artifact_records(
                    raw,
                    artifact=artifact(),
                    expected_record_sha256_by_ordinal={0: "0" * 64},
                )
            )
        with self.assertRaisesRegex(RibAdapterError, "不存在的 ordinal"):
            tuple(
                iter_rib_artifact_records(
                    raw,
                    artifact=artifact(),
                    expected_record_sha256_by_ordinal={2: "0" * 64},
                )
            )

    def test_filter_does_not_materialize_entire_record_stream(self):
        raw, _physical = mixed_rib_bytes(include_non_target_record=False)
        parsed = parse_rib_mrt_bytes(raw)
        allow_second = [False]

        def guarded_stream():
            yield parsed[0]
            if not allow_second[0]:
                raise AssertionError("适配器在首次 yield 前预读了下一 record")
            yield parsed[1]

        iterator = iter_adapted_rib_records(
            guarded_stream(),
            artifact=artifact(),
            origin_asn_predicate=lambda asn: asn == 65001,
        )
        first = next(iterator)
        self.assertEqual(first.record_kind, PEER_INDEX_RECORD)
        allow_second[0] = True
        self.assertEqual(len(tuple(iterator)), 1)

    def test_artifact_identity_type_path_compression_and_slot_fail_closed(self):
        raw, _physical = mixed_rib_bytes(include_non_target_record=False)
        bad_artifacts = (
            artifact(artifact_id="art_v1_" + "0" * 32),
            artifact(artifact_type="update"),
            artifact(artifact_time_utc="2026-02-27T17:00:00Z"),
            artifact(relative_path="../bview.gz"),
            artifact(compression="bz2"),
            artifact(size_bytes=0),
        )
        for bad in bad_artifacts:
            with self.subTest(bad=bad):
                with self.assertRaises(RibAdapterError):
                    tuple(iter_rib_artifact_records(raw, artifact=bad))

        outside_raw = bytearray(raw)
        outside_raw[0:4] = struct.pack("!I", MRT_TIME + 8 * 60 * 60)
        with self.assertRaisesRegex(RibAdapterError, "八小时槽"):
            tuple(iter_rib_artifact_records(bytes(outside_raw), artifact=artifact()))

    def test_ordinal_offset_raw_length_and_element_type_fail_closed(self):
        raw, _physical = mixed_rib_bytes(include_non_target_record=False)
        parsed = parse_rib_mrt_bytes(raw)
        cases = (
            (replace(parsed[0], record_ordinal=1), parsed[1]),
            (parsed[0], replace(parsed[1], record_offset=parsed[1].record_offset + 1)),
            (replace(parsed[0], elements=(parsed[1].elements[0],)), parsed[1]),
        )
        for records in cases:
            with self.subTest(records=records):
                with self.assertRaises(RibAdapterError):
                    tuple(iter_adapted_rib_records(records, artifact=artifact()))

        damaged = bytearray(parsed[0].raw_record)
        damaged[8:12] = struct.pack("!I", len(damaged))
        with self.assertRaisesRegex(RibAdapterError, "header length"):
            tuple(
                iter_adapted_rib_records(
                    (replace(parsed[0], raw_record=bytes(damaged)),),
                    artifact=artifact(),
                )
            )

    def test_empty_stream_predicate_and_parser_fail_closed(self):
        with self.assertRaisesRegex(RibAdapterError, "为空"):
            tuple(iter_adapted_rib_records((), artifact=artifact()))
        with self.assertRaisesRegex(RibAdapterError, "strictly|严格|bool"):
            raw, _physical = mixed_rib_bytes(include_non_target_record=False)
            tuple(
                iter_rib_artifact_records(
                    raw,
                    artifact=artifact(),
                    origin_asn_predicate=lambda _asn: 1,
                )
            )
        with self.assertRaisesRegex(RibAdapterError, "parser"):
            tuple(iter_rib_artifact_records(b"broken", artifact=artifact()))


if __name__ == "__main__":
    unittest.main()
