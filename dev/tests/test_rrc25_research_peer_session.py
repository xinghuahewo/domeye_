from __future__ import annotations

import hashlib
import ipaddress
import struct
import unittest

from backend.data_pipeline.research.rrc25_country_outage.peer_session import (
    AFI_IPV4,
    AFI_IPV6,
    BGP4MP_STATE_CHANGE,
    BGP4MP_STATE_CHANGE_AS4,
    MRT_BGP4MP,
    MRT_BGP4MP_ET,
    PEER_SESSION_OBSERVATION_SEMANTICS,
    PEER_SESSION_PREFIX_INFERENCE,
    PeerSessionParseError,
    iter_peer_session_mrt_records,
    parse_peer_session_mrt_bytes,
    parse_peer_session_observation,
    peer_session_observation_id_v1,
)
from backend.data_pipeline.route_event.artifacts import artifact_id_v1
from backend.data_pipeline.route_event.index import ParsedMrtRecord, vp_id_v1


MRT_TIME = 1_772_208_000  # 2026-02-27T16:00:00Z


class OneByteStream:
    def __init__(self, value):
        self.value = value
        self.offset = 0

    def read(self, _size=-1):
        if self.offset >= len(self.value):
            return b""
        result = self.value[self.offset : self.offset + 1]
        self.offset += 1
        return result


def state_change_payload(
    *,
    peer_asn: int = 64500,
    local_asn: int = 12654,
    peer_ip: str = "192.0.2.1",
    local_ip: str = "192.0.2.254",
    interface_index: int = 7,
    afi: int = AFI_IPV4,
    old_state: int = 5,
    new_state: int = 6,
    asn_width: int = 2,
) -> bytes:
    return (
        peer_asn.to_bytes(asn_width, "big")
        + local_asn.to_bytes(asn_width, "big")
        + struct.pack("!HH", interface_index, afi)
        + ipaddress.ip_address(peer_ip).packed
        + ipaddress.ip_address(local_ip).packed
        + struct.pack("!HH", old_state, new_state)
    )


def mrt_record(
    payload: bytes,
    *,
    mrt_type: int = MRT_BGP4MP,
    subtype: int = BGP4MP_STATE_CHANGE,
    timestamp: int = MRT_TIME,
    microseconds: int = 0,
) -> bytes:
    body = struct.pack("!I", microseconds) + payload if mrt_type == 17 else payload
    return struct.pack("!IHHI", timestamp, mrt_type, subtype, len(body)) + body


def source_identity(value: bytes) -> tuple[str, str]:
    file_sha256 = hashlib.sha256(value).hexdigest()
    return file_sha256, artifact_id_v1(file_sha256)


class PeerSessionParserTests(unittest.TestCase):
    def parse(self, value: bytes):
        file_sha256, artifact_id = source_identity(value)
        return parse_peer_session_mrt_bytes(
            value,
            collector_id="rrc25",
            file_sha256=file_sha256,
            artifact_id=artifact_id,
        )

    def test_as2_ipv4_state_change_preserves_session_identity(self):
        value = mrt_record(state_change_payload())
        (observation,) = self.parse(value)

        self.assertEqual(observation.event_time_utc, "2026-02-27T16:00:00Z")
        self.assertEqual(observation.event_epoch_microseconds, MRT_TIME * 1_000_000)
        self.assertEqual((observation.mrt_type, observation.mrt_subtype), (16, 0))
        self.assertEqual((observation.peer_asn, observation.local_asn), (64500, 12654))
        self.assertEqual(observation.interface_index, 7)
        self.assertEqual(observation.afi, AFI_IPV4)
        self.assertEqual(observation.peer_ip, "192.0.2.1")
        self.assertEqual(observation.local_ip, "192.0.2.254")
        self.assertEqual((observation.old_state, observation.new_state), (5, 6))
        self.assertEqual(
            (observation.old_state_name, observation.new_state_name),
            ("open_confirm", "established"),
        )
        self.assertEqual(
            observation.vp_id, vp_id_v1("rrc25", "192.0.2.1", 64500)
        )
        self.assertEqual(observation.semantics, PEER_SESSION_OBSERVATION_SEMANTICS)
        self.assertEqual(
            observation.prefix_withdrawal_inference,
            PEER_SESSION_PREFIX_INFERENCE,
        )

    def test_as4_ipv6_state_change_preserves_full_width_asns(self):
        payload = state_change_payload(
            peer_asn=4_200_000_001,
            local_asn=4_294_967_295,
            peer_ip="2001:db8::1",
            local_ip="2001:db8::ffff",
            interface_index=65_535,
            afi=AFI_IPV6,
            old_state=6,
            new_state=1,
            asn_width=4,
        )
        value = mrt_record(payload, subtype=BGP4MP_STATE_CHANGE_AS4)
        (observation,) = self.parse(value)

        self.assertEqual((observation.mrt_type, observation.mrt_subtype), (16, 5))
        self.assertEqual(observation.peer_asn, 4_200_000_001)
        self.assertEqual(observation.local_asn, 4_294_967_295)
        self.assertEqual(observation.interface_index, 65_535)
        self.assertEqual(observation.afi, AFI_IPV6)
        self.assertEqual(observation.peer_ip, "2001:db8::1")
        self.assertEqual(observation.local_ip, "2001:db8::ffff")
        self.assertEqual((observation.old_state_name, observation.new_state_name), ("established", "idle"))

    def test_extended_timestamp_preserves_six_digit_microseconds(self):
        value = mrt_record(
            state_change_payload(),
            mrt_type=MRT_BGP4MP_ET,
            microseconds=123_456,
        )
        (observation,) = self.parse(value)

        self.assertEqual(observation.mrt_type, MRT_BGP4MP_ET)
        self.assertEqual(observation.microseconds, 123_456)
        self.assertEqual(
            observation.event_time_utc, "2026-02-27T16:00:00.123456Z"
        )
        self.assertEqual(
            observation.event_epoch_microseconds,
            MRT_TIME * 1_000_000 + 123_456,
        )

    def test_asn_zero_is_preserved_and_participates_in_vp_identity(self):
        value = mrt_record(state_change_payload(peer_asn=0, local_asn=0))
        (observation,) = self.parse(value)

        self.assertEqual((observation.peer_asn, observation.local_asn), (0, 0))
        self.assertEqual(
            observation.vp_id, vp_id_v1("rrc25", "192.0.2.1", 0)
        )

    def test_record_offsets_hashes_and_stable_ids_enable_raw_lookup(self):
        first = mrt_record(state_change_payload(old_state=1, new_state=2))
        second = mrt_record(
            state_change_payload(old_state=2, new_state=3), timestamp=MRT_TIME + 1
        )
        value = first + second
        file_sha256, artifact_id = source_identity(value)
        records = tuple(iter_peer_session_mrt_records(value))
        observations = tuple(
            parse_peer_session_observation(
                record,
                collector_id="rrc25",
                file_sha256=file_sha256,
                artifact_id=artifact_id,
            )
            for record in records
        )

        self.assertEqual(
            [(record.record_ordinal, record.record_offset) for record in records],
            [(0, 0), (1, len(first))],
        )
        for record, observation in zip(records, observations):
            raw = value[
                observation.record_offset : observation.record_offset
                + observation.record_length
            ]
            self.assertEqual(raw, record.raw_record)
            self.assertEqual(
                hashlib.sha256(raw).hexdigest(), observation.raw_record_sha256
            )
            self.assertEqual(
                observation.observation_id,
                peer_session_observation_id_v1(
                    file_sha256, observation.record_ordinal
                ),
            )
        self.assertNotEqual(observations[0].observation_id, observations[1].observation_id)

    def test_same_immutable_coordinate_produces_same_observation_id(self):
        file_sha256 = "a" * 64
        self.assertEqual(
            peer_session_observation_id_v1(file_sha256, 42),
            peer_session_observation_id_v1(file_sha256, 42),
        )
        self.assertNotEqual(
            peer_session_observation_id_v1(file_sha256, 42),
            peer_session_observation_id_v1(file_sha256, 43),
        )

    def test_truncated_header_and_payload_fail_closed(self):
        with self.assertRaisesRegex(PeerSessionParseError, "header 被截断"):
            tuple(iter_peer_session_mrt_records(b"\x00" * 11))

        complete = mrt_record(state_change_payload())
        with self.assertRaisesRegex(PeerSessionParseError, "payload.*被截断"):
            tuple(iter_peer_session_mrt_records(complete[:-1]))

    def test_binary_stream_short_reads_are_accumulated(self):
        complete = mrt_record(state_change_payload())
        records = tuple(iter_peer_session_mrt_records(OneByteStream(complete)))

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].raw_record, complete)

    def test_unknown_tail_and_header_length_mismatch_fail_closed(self):
        tailed = mrt_record(state_change_payload() + b"\x00")
        with self.assertRaisesRegex(PeerSessionParseError, "尾部"):
            tuple(iter_peer_session_mrt_records(tailed))

        valid = mrt_record(state_change_payload())
        record = ParsedMrtRecord(0, 0, valid + b"\x00", ())
        file_sha256, artifact_id = source_identity(valid + b"\x00")
        with self.assertRaisesRegex(PeerSessionParseError, "长度不一致"):
            parse_peer_session_observation(
                record,
                collector_id="rrc25",
                file_sha256=file_sha256,
                artifact_id=artifact_id,
            )

    def test_unknown_type_and_subtype_fail_closed(self):
        unknown_type = mrt_record(state_change_payload(), mrt_type=15)
        with self.assertRaisesRegex(PeerSessionParseError, "type 16"):
            tuple(iter_peer_session_mrt_records(unknown_type))

        unknown_subtype = mrt_record(state_change_payload(), subtype=1)
        with self.assertRaisesRegex(PeerSessionParseError, "subtype 0"):
            tuple(iter_peer_session_mrt_records(unknown_subtype))

    def test_invalid_afi_microseconds_and_fsm_state_fail_closed(self):
        invalid_afi = bytearray(state_change_payload())
        invalid_afi[6:8] = struct.pack("!H", 3)
        with self.assertRaisesRegex(PeerSessionParseError, "AFI"):
            tuple(iter_peer_session_mrt_records(mrt_record(bytes(invalid_afi))))

        invalid_microseconds = mrt_record(
            state_change_payload(),
            mrt_type=MRT_BGP4MP_ET,
            microseconds=1_000_000,
        )
        with self.assertRaisesRegex(PeerSessionParseError, "微秒"):
            tuple(iter_peer_session_mrt_records(invalid_microseconds))

        invalid_state = mrt_record(state_change_payload(new_state=7))
        with self.assertRaisesRegex(PeerSessionParseError, "FSM state"):
            tuple(iter_peer_session_mrt_records(invalid_state))

    def test_parsed_record_api_rejects_route_elements_and_artifact_mismatch(self):
        value = mrt_record(state_change_payload())
        file_sha256, artifact_id = source_identity(value)
        record = ParsedMrtRecord(0, 0, value, (object(),))  # type: ignore[arg-type]
        with self.assertRaisesRegex(PeerSessionParseError, "elements 必须为空"):
            parse_peer_session_observation(
                record,
                collector_id="rrc25",
                file_sha256=file_sha256,
                artifact_id=artifact_id,
            )

        empty_record = ParsedMrtRecord(0, 0, value, ())
        other_artifact_id = artifact_id_v1("b" * 64)
        with self.assertRaisesRegex(PeerSessionParseError, "不匹配"):
            parse_peer_session_observation(
                empty_record,
                collector_id="rrc25",
                file_sha256=file_sha256,
                artifact_id=other_artifact_id,
            )


if __name__ == "__main__":
    unittest.main()
