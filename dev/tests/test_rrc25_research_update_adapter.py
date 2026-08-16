from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import ipaddress
import struct
import unittest
from unittest.mock import patch

from backend.data_pipeline.research.rrc25_country_outage import update_adapter as update_adapter_module

from backend.data_pipeline.route_event import (
    ParsedMrtRecord,
    ParsedRouteElement,
    artifact_id_v1,
    route_event_id_v1,
    vp_id_v1,
)
from backend.data_pipeline.research.rrc25_country_outage.update_adapter import (
    END_OF_RIB_RECORD,
    KEEPALIVE_RECORD,
    NOTIFICATION_RECORD,
    OPEN_RECORD,
    STATE_CHANGE_RECORD,
    UPDATE_RECORD,
    UpdateAdapterError,
    iter_adapted_update_records,
    iter_bgpdump_artifact_records,
)


UTC = timezone.utc
SLOT_TEXT = "2026-02-27T16:00:00Z"
SLOT_TIMESTAMP = int(
    datetime(2026, 2, 27, 16, 0, 0, tzinfo=UTC).timestamp()
)
FILE_SHA256 = hashlib.sha256(b"rrc25-update-adapter-fixture").hexdigest()
ARTIFACT_ID = artifact_id_v1(FILE_SHA256)


def artifact(**overrides):
    value = {
        "artifact_id": ARTIFACT_ID,
        "file_sha256": FILE_SHA256,
        "collector_id": "rrc25",
        "artifact_type": "update",
        "artifact_time_utc": SLOT_TEXT,
        "relative_path": "rrc25/2026.02/updates.20260227.1600.gz",
        "compression": "gz",
        "size_bytes": 1024,
    }
    value.update(overrides)
    return value


def _nlri(prefix: str) -> bytes:
    network = ipaddress.ip_network(prefix, strict=False)
    octets = (network.prefixlen + 7) // 8
    return bytes((network.prefixlen,)) + network.network_address.packed[:octets]


def update_frame(
    *,
    timestamp: int = SLOT_TIMESTAMP + 1,
    subtype: int = 4,
    peer_asn: int = 64500,
    peer_ip: str = "192.0.2.10",
    message_type: int = 2,
    message_body: bytes | None = None,
    mrt_type: int = 16,
    microseconds: int = 0,
) -> bytes:
    if message_body is None:
        if message_type == 2:
            withdrawn = _nlri("198.51.100.0/24")
            message_body = (
                struct.pack("!H", len(withdrawn))
                + withdrawn
                + b"\x00\x00"
                + _nlri("203.0.113.0/24")
            )
        else:
            message_body = b""
    message = (
        b"\xff" * 16
        + struct.pack("!HB", 19 + len(message_body), message_type)
        + message_body
    )
    if subtype == 1:
        identity = struct.pack("!HHHH", peer_asn, 64496, 0, 1)
    else:
        identity = struct.pack("!IIHH", peer_asn, 64496, 0, 1)
    payload = (
        identity
        + ipaddress.ip_address(peer_ip).packed
        + ipaddress.ip_address("192.0.2.1").packed
        + message
    )
    if mrt_type == 17:
        payload = struct.pack("!I", microseconds) + payload
    return struct.pack("!IHHI", timestamp, mrt_type, subtype, len(payload)) + payload


def state_change_frame(
    *,
    timestamp: int = SLOT_TIMESTAMP + 3,
    subtype: int = 5,
    old_state: int = 6,
    new_state: int = 1,
) -> bytes:
    if subtype == 0:
        identity = struct.pack("!HHHH", 64500, 64496, 0, 1)
    else:
        identity = struct.pack("!IIHH", 64500, 64496, 0, 1)
    payload = (
        identity
        + ipaddress.ip_address("192.0.2.10").packed
        + ipaddress.ip_address("192.0.2.1").packed
        + struct.pack("!HH", old_state, new_state)
    )
    return struct.pack("!IHHI", timestamp, 16, subtype, len(payload)) + payload


def event_time(timestamp: int, microseconds: int = 0) -> str:
    moment = datetime.fromtimestamp(timestamp, tz=UTC)
    if microseconds:
        return moment.strftime("%Y-%m-%dT%H:%M:%S") + f".{microseconds:06d}Z"
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def announce_element(
    *,
    timestamp: int = SLOT_TIMESTAMP + 1,
    microseconds: int = 0,
    peer_ip: str = "192.0.2.10",
    peer_asn: int = 64500,
    action: str = "announce",
) -> ParsedRouteElement:
    return ParsedRouteElement(
        event_time_utc=event_time(timestamp, microseconds),
        peer_ip=peer_ip,
        peer_asn=peer_asn,
        action=action,
        prefix="203.0.113.7/24",
        afi_safi="ipv4_unicast",
        as_path=() if action != "withdraw" else None,
        quality_flags=(),
    )


class UpdateAdapterTests(unittest.TestCase):
    def test_mixed_records_are_classified_and_keep_raw_evidence(self):
        update = update_frame()
        keepalive = update_frame(timestamp=SLOT_TIMESTAMP + 2, message_type=4)
        optional = b"\x02\x02\x02\x00"
        open_body = struct.pack(
            "!BHH4sB",
            4,
            23456,
            90,
            ipaddress.IPv4Address("172.23.0.0").packed,
            len(optional),
        ) + optional
        open_frame = update_frame(
            timestamp=SLOT_TIMESTAMP + 3,
            message_type=1,
            message_body=open_body,
        )
        notification = update_frame(
            timestamp=SLOT_TIMESTAMP + 4,
            message_type=3,
            message_body=b"\x06\x05\x06\x05",
        )
        state = state_change_frame(timestamp=SLOT_TIMESTAMP + 5)
        withdraw = replace(
            announce_element(action="withdraw"), prefix="198.51.100.9/24"
        )
        records = (
            ParsedMrtRecord(0, 0, update, (announce_element(), withdraw)),
            ParsedMrtRecord(1, len(update), keepalive, ()),
            ParsedMrtRecord(2, len(update) + len(keepalive), open_frame, ()),
            ParsedMrtRecord(
                3,
                len(update) + len(keepalive) + len(open_frame),
                notification,
                (),
            ),
            ParsedMrtRecord(
                4,
                len(update) + len(keepalive) + len(open_frame) + len(notification),
                state,
                (),
            ),
        )

        adapted = tuple(iter_adapted_update_records(records, artifact=artifact()))

        self.assertEqual(
            [value.record_kind for value in adapted],
            [
                UPDATE_RECORD,
                KEEPALIVE_RECORD,
                OPEN_RECORD,
                NOTIFICATION_RECORD,
                STATE_CHANGE_RECORD,
            ],
        )
        self.assertEqual(len(adapted[0].route_events), 2)
        first_event = adapted[0].route_events[0]
        self.assertEqual(first_event.artifact_id, ARTIFACT_ID)
        self.assertEqual(first_event.file_sha256, FILE_SHA256)
        self.assertEqual(first_event.record_ordinal, 0)
        self.assertEqual(first_event.element_ordinal, 0)
        self.assertEqual(
            first_event.route_event_id, route_event_id_v1(FILE_SHA256, 0, 0)
        )
        self.assertEqual(
            adapted[0].raw_record.raw_record_sha256,
            hashlib.sha256(update).hexdigest(),
        )
        self.assertEqual(
            adapted[0].raw_record.raw_record_hash,
            adapted[0].raw_record.record_hash,
        )
        self.assertEqual(adapted[1].route_events, ())
        self.assertIsNone(adapted[1].peer_session_observation)
        self.assertEqual(adapted[2].route_events, ())
        self.assertEqual(adapted[3].route_events, ())
        observation = adapted[4].peer_session_observation
        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertEqual(observation.record_ordinal, 4)
        self.assertEqual(
            observation.raw_record_sha256, hashlib.sha256(state).hexdigest()
        )
        self.assertEqual(observation.prefix_withdrawal_inference, "not_permitted")

    def test_extended_timestamp_is_preserved_and_compared(self):
        raw = update_frame(
            mrt_type=17,
            microseconds=123456,
            timestamp=SLOT_TIMESTAMP + 4,
        )
        record = ParsedMrtRecord(
            0,
            0,
            raw,
            (
                announce_element(
                    timestamp=SLOT_TIMESTAMP + 4, microseconds=123456
                ),
            ),
        )

        adapted = tuple(iter_adapted_update_records((record,), artifact=artifact()))

        self.assertEqual(
            adapted[0].raw_record.event_time_utc, "2026-02-27T16:00:04.123456Z"
        )
        self.assertEqual(
            adapted[0].route_events[0].event_time_utc,
            "2026-02-27T16:00:04.123456Z",
        )

    def test_retention_selector_avoids_event_promotion_but_keeps_full_counts(self):
        raw = update_frame()
        announce = announce_element()
        withdraw = replace(announce, action="withdraw", as_path=None)
        observed = []

        adapted = tuple(
            iter_adapted_update_records(
                (ParsedMrtRecord(0, 0, raw, (announce, withdraw)),),
                artifact=artifact(),
                route_element_retention_selector=lambda elements: (
                    observed.append(elements) or (False, True)
                ),
            )
        )

        self.assertEqual(observed, [(announce, withdraw)])
        self.assertEqual(adapted[0].announce_count, 1)
        self.assertEqual(adapted[0].withdraw_count, 1)
        self.assertEqual(
            adapted[0].update_peer_observations,
            ((vp_id_v1("rrc25", "192.0.2.10", 64500), "2026-02-27T16:00:01Z"),),
        )
        self.assertEqual(len(adapted[0].route_events), 1)
        self.assertEqual(adapted[0].route_events[0].element_ordinal, 1)
        self.assertEqual(adapted[0].route_events[0].action, "withdraw")

    def test_repeated_record_identity_fields_are_parsed_once_without_skipping_counts(self):
        raw = update_frame()
        announce = announce_element()
        elements = (announce,) * 1000
        original = update_adapter_module._parsed_event_epoch_microseconds

        with patch.object(
            update_adapter_module,
            "_parsed_event_epoch_microseconds",
            wraps=original,
        ) as parse_time:
            adapted = tuple(
                iter_adapted_update_records(
                    (ParsedMrtRecord(0, 0, raw, elements),),
                    artifact=artifact(),
                    route_element_retention_selector=lambda values: (False,)
                    * len(values),
                )
            )

        self.assertEqual(parse_time.call_count, 1)
        self.assertEqual(adapted[0].announce_count, 1000)
        self.assertEqual(adapted[0].withdraw_count, 0)
        self.assertEqual(adapted[0].route_events, ())

    def test_retention_selector_cannot_hide_invalid_element_semantics(self):
        raw = update_frame()
        invalid = replace(announce_element(), quality_flags=("",))
        with self.assertRaisesRegex(UpdateAdapterError, "quality_flags"):
            tuple(
                iter_adapted_update_records(
                    (ParsedMrtRecord(0, 0, raw, (invalid,)),),
                    artifact=artifact(),
                    route_element_retention_selector=lambda _elements: (False,),
                )
            )

    def test_retention_selector_shape_fails_closed(self):
        raw = update_frame()
        record = ParsedMrtRecord(0, 0, raw, (announce_element(),))
        for result in ((), (1,), "bad"):
            with self.subTest(result=result):
                with self.assertRaisesRegex(UpdateAdapterError, "selector"):
                    tuple(
                        iter_adapted_update_records(
                            (record,),
                            artifact=artifact(),
                            route_element_retention_selector=lambda _elements, value=result: value,
                        )
                    )

    def test_factory_entrypoint_passes_normalized_artifact(self):
        raw = update_frame()
        observed = []

        def fake_factory(value):
            observed.append(value)
            return (ParsedMrtRecord(0, 0, raw, (announce_element(),)),)

        result = tuple(iter_bgpdump_artifact_records(fake_factory, artifact()))

        self.assertEqual(len(result), 1)
        self.assertEqual(observed, [artifact()])

    def test_artifact_identity_and_scope_fail_closed(self):
        raw = update_frame()
        record = ParsedMrtRecord(0, 0, raw, (announce_element(),))
        bad_values = (
            artifact(artifact_id="art_v1_" + "0" * 32),
            artifact(artifact_type="rib"),
            artifact(artifact_time_utc="2026-02-27T16:01:00Z"),
            artifact(relative_path="../updates.gz"),
            artifact(compression="bz2"),
            artifact(size_bytes=0),
        )
        for bad in bad_values:
            with self.subTest(bad=bad):
                with self.assertRaises(UpdateAdapterError):
                    tuple(iter_adapted_update_records((record,), artifact=bad))

    def test_coordinates_must_be_integer_contiguous_and_cover_stream(self):
        raw = update_frame()
        values = (
            ParsedMrtRecord(1, 0, raw, (announce_element(),)),
            ParsedMrtRecord(0, 1, raw, (announce_element(),)),
            ParsedMrtRecord(False, 0, raw, (announce_element(),)),
            ParsedMrtRecord(0, 0.0, raw, (announce_element(),)),
        )
        for record in values:
            with self.subTest(record=record):
                with self.assertRaises(UpdateAdapterError):
                    tuple(iter_adapted_update_records((record,), artifact=artifact()))

    def test_second_record_offset_must_follow_first_raw_record(self):
        first = update_frame()
        second = update_frame(timestamp=SLOT_TIMESTAMP + 2)
        records = (
            ParsedMrtRecord(0, 0, first, (announce_element(),)),
            ParsedMrtRecord(
                1,
                len(first) + 1,
                second,
                (announce_element(timestamp=SLOT_TIMESTAMP + 2),),
            ),
        )
        with self.assertRaisesRegex(UpdateAdapterError, "record_offset"):
            tuple(iter_adapted_update_records(records, artifact=artifact()))

    def test_header_subtype_and_slot_time_fail_closed(self):
        valid = update_frame()
        damaged_length = bytearray(valid)
        damaged_length[8:12] = struct.pack("!I", len(valid))
        unknown_subtype = update_frame(subtype=2)
        outside_slot = update_frame(timestamp=SLOT_TIMESTAMP + 300)
        cases = (
            (bytes(damaged_length), announce_element()),
            (unknown_subtype, announce_element()),
            (outside_slot, announce_element(timestamp=SLOT_TIMESTAMP + 300)),
        )
        for raw, element in cases:
            with self.subTest(raw=raw[:12]):
                with self.assertRaises(UpdateAdapterError):
                    tuple(
                        iter_adapted_update_records(
                            (ParsedMrtRecord(0, 0, raw, (element,)),),
                            artifact=artifact(),
                        )
                    )

    def test_peer_and_event_time_conflicts_fail_closed(self):
        raw = update_frame()
        bad_elements = (
            announce_element(peer_ip="192.0.2.11"),
            announce_element(peer_asn=64501),
            announce_element(timestamp=SLOT_TIMESTAMP + 2),
        )
        for element in bad_elements:
            with self.subTest(element=element):
                with self.assertRaises(UpdateAdapterError):
                    tuple(
                        iter_adapted_update_records(
                            (ParsedMrtRecord(0, 0, raw, (element,)),),
                            artifact=artifact(),
                        )
                    )

    def test_update_requires_nonempty_tuple_of_update_elements(self):
        raw = update_frame()
        records = (
            ParsedMrtRecord(0, 0, raw, ()),
            ParsedMrtRecord(0, 0, raw, [announce_element()]),
            ParsedMrtRecord(
                0,
                0,
                raw,
                (announce_element(action="rib_snapshot"),),
            ),
        )
        for record in records:
            with self.subTest(record=record):
                with self.assertRaises(UpdateAdapterError):
                    tuple(iter_adapted_update_records((record,), artifact=artifact()))

    def test_end_of_rib_is_retained_as_control_without_route_event(self):
        bodies = (
            b"\x00\x00\x00\x00",
            b"\x00\x00\x00\x06\x80\x0f\x03\x00\x02\x01",
        )
        records = []
        offset = 0
        for ordinal, body in enumerate(bodies):
            raw = update_frame(
                timestamp=SLOT_TIMESTAMP + ordinal + 1,
                message_body=body,
            )
            records.append(ParsedMrtRecord(ordinal, offset, raw, ()))
            offset += len(raw)
        adapted = tuple(iter_adapted_update_records(tuple(records), artifact=artifact()))
        self.assertEqual([row.record_kind for row in adapted], [END_OF_RIB_RECORD] * 2)
        self.assertTrue(all(not row.route_events for row in adapted))
        self.assertTrue(all(row.peer_session_observation is None for row in adapted))

    def test_state_change_and_control_messages_reject_route_elements(self):
        valid_open = struct.pack(
            "!BHH4sB",
            4,
            23456,
            90,
            ipaddress.IPv4Address("172.23.0.0").packed,
            0,
        )
        for raw in (
            state_change_frame(),
            update_frame(message_type=4),
            update_frame(message_type=1, message_body=valid_open),
            update_frame(message_type=3, message_body=b"\x06\x05"),
        ):
            with self.subTest(raw=raw[:12]):
                with self.assertRaises(UpdateAdapterError):
                    tuple(
                        iter_adapted_update_records(
                            (ParsedMrtRecord(0, 0, raw, (announce_element(),)),),
                            artifact=artifact(),
                        )
                    )

    def test_unknown_and_malformed_control_messages_fail_closed(self):
        valid_open = struct.pack(
            "!BHH4sB",
            4,
            23456,
            90,
            ipaddress.IPv4Address("172.23.0.0").packed,
            0,
        )
        unknown = update_frame(message_type=5)
        malformed_keepalive = update_frame(message_type=4, message_body=b"x")
        malformed_open = update_frame(message_type=1)
        wrong_open_version = update_frame(
            message_type=1, message_body=b"\x03" + valid_open[1:]
        )
        malformed_capability = update_frame(
            message_type=1,
            message_body=valid_open[:-1] + b"\x04\x02\x02\x02\x01",
        )
        malformed_notification = update_frame(message_type=3, message_body=b"\x06")
        for raw in (
            unknown,
            malformed_keepalive,
            malformed_open,
            wrong_open_version,
            malformed_capability,
            malformed_notification,
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(UpdateAdapterError):
                    tuple(
                        iter_adapted_update_records(
                            (ParsedMrtRecord(0, 0, raw, ()),), artifact=artifact()
                        )
                    )

    def test_damaged_state_change_is_wrapped_and_not_downgraded(self):
        raw = state_change_frame(old_state=0)
        with self.assertRaisesRegex(UpdateAdapterError, "STATE_CHANGE 解析失败"):
            tuple(
                iter_adapted_update_records(
                    (ParsedMrtRecord(0, 0, raw, ()),), artifact=artifact()
                )
            )

    def test_empty_or_non_record_stream_fails_closed(self):
        with self.assertRaisesRegex(UpdateAdapterError, "为空"):
            tuple(iter_adapted_update_records((), artifact=artifact()))
        with self.assertRaisesRegex(UpdateAdapterError, "非 ParsedMrtRecord"):
            tuple(iter_adapted_update_records((object(),), artifact=artifact()))
        with self.assertRaises(UpdateAdapterError):
            tuple(iter_adapted_update_records(b"not-a-stream", artifact=artifact()))

    def test_factory_contract_is_validated_before_and_after_call(self):
        called = []

        def must_not_call(_value):
            called.append(True)
            return ()

        with self.assertRaises(UpdateAdapterError):
            tuple(
                iter_bgpdump_artifact_records(
                    must_not_call, artifact(artifact_type="rib")
                )
            )
        self.assertEqual(called, [])

        with self.assertRaises(UpdateAdapterError):
            tuple(iter_bgpdump_artifact_records(lambda _value: b"bad", artifact()))


if __name__ == "__main__":
    unittest.main()
