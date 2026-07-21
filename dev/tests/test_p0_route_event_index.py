from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import tempfile
import unittest

from backend.data_pipeline.route_event import (
    AsPathSegment,
    ImportProvenance,
    IncidentObject,
    IncidentObservation,
    MrtParserUnavailableError,
    ParsedMrtRecord,
    ParsedRouteElement,
    RouteEventIndex,
    RouteEventIndexIntegrityError,
    RouteEventInputError,
    build_route_event_index,
    builtin_mrt_parser_capability,
    artifact_id_v1,
    canonical_json,
    import_run_id_v1,
    parse_mrt_artifact,
    route_event_id_v1,
    vp_id_v1,
)


ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
MANIFEST_FINGERPRINT_SCHEMA = "mrt_artifact_manifest_fingerprint_v1"


def mrt_record(timestamp, payload=b"fixture", mrt_type=16, mrt_subtype=4):
    return struct.pack("!IHHI", timestamp, mrt_type, mrt_subtype, len(payload)) + payload


def route_element(
    timestamp="2026-03-04T11:35:43Z",
    *,
    action="announce",
    prefix="203.0.113.7/24",
    peer_ip="192.0.2.10",
    peer_asn=64500,
    as_path=None,
    quality_flags=(),
):
    if as_path is None and action != "withdraw":
        as_path = (
            AsPathSegment("as_sequence", (64500, 64496)),
        )
    return ParsedRouteElement(
        event_time_utc=timestamp,
        peer_ip=peer_ip,
        peer_asn=peer_asn,
        action=action,
        prefix=prefix,
        afi_safi="ipv4_unicast" if ":" not in prefix else "ipv6_unicast",
        as_path=as_path,
        quality_flags=quality_flags,
    )


def make_manifest(artifacts):
    payload = {
        "schema_version": 1,
        "manifest_kind": "mrt_artifact_manifest",
        "artifact_id_schema": "artifact_id_v1",
        "artifacts": artifacts,
    }
    fingerprint = hashlib.sha256(
        canonical_json(
            {"schema": MANIFEST_FINGERPRINT_SCHEMA, "manifest": payload}
        ).encode("utf-8")
    ).hexdigest()
    manifest = {**payload, "manifest_fingerprint_sha256": fingerprint}
    verification = {
        "verified": True,
        "artifact_count": len(artifacts),
        "manifest_fingerprint_sha256": fingerprint,
    }
    return manifest, verification


def make_artifact(label=b"compressed-mrt", artifact_type="update", collector="rrc25"):
    file_hash = hashlib.sha256(label).hexdigest()
    return {
        "artifact_id": artifact_id_v1(file_hash),
        "file_sha256": file_hash,
        "collector_id": collector,
        "artifact_type": artifact_type,
    }


def provenance():
    return ImportProvenance(
        parser_name="approved_fixture_parser",
        parser_version="1.2.3",
        importer_name="domeye_route_ingest",
        importer_version="1.0.0",
        processing_time_utc="2026-04-01T00:00:00Z",
        config={"afi_safi": ["ipv4_unicast", "ipv6_unicast"], "strict": True},
    )


def update_records(artifact):
    del artifact
    timestamp = int(datetime(2026, 3, 4, 11, 35, 43, tzinfo=UTC).timestamp())
    first = mrt_record(timestamp, b"update-payload")
    second = mrt_record(timestamp + 1, b"state-change")
    return (
        ParsedMrtRecord(
            record_ordinal=0,
            record_offset=0,
            raw_record=first,
            elements=(
                route_element(),
                route_element(action="withdraw", as_path=None),
            ),
        ),
        ParsedMrtRecord(
            record_ordinal=1,
            record_offset=len(first),
            raw_record=second,
            elements=(),
        ),
    )


class RouteEventIdentityTest(unittest.TestCase):
    def test_route_event_id_is_exact_canonical_coordinate_identity(self):
        file_hash = hashlib.sha256(b"fixture").hexdigest()
        identity = {
            "schema": "route_event_id_v1",
            "file_sha256": file_hash,
            "record_ordinal": 42,
            "element_ordinal": 3,
        }
        expected = "rte_v1_" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:32]
        self.assertEqual(route_event_id_v1(file_hash, 42, 3), expected)
        for values in (("A" * 64, 0, 0), (file_hash, -1, 0), (file_hash, 0, True)):
            with self.subTest(values=values):
                with self.assertRaises(RouteEventInputError):
                    route_event_id_v1(*values)

    def test_vp_and_import_run_ids_are_stable_and_normalized(self):
        first = vp_id_v1("rrc25", "2001:0db8::1", 64500)
        second = vp_id_v1("rrc25", "2001:db8:0:0::1", 64500)
        self.assertEqual(first, second)
        artifact = make_artifact()
        manifest, _verification = make_manifest([artifact])
        first_run = import_run_id_v1(
            manifest["manifest_fingerprint_sha256"], provenance()
        )
        second_run = import_run_id_v1(
            manifest["manifest_fingerprint_sha256"], provenance()
        )
        self.assertEqual(first_run, second_run)


class ParserBoundaryTest(unittest.TestCase):
    def test_builtin_file_parser_fails_closed(self):
        capability = builtin_mrt_parser_capability()
        self.assertFalse(capability["available"])
        self.assertEqual(capability["capability"], "injected_record_stream_only")
        self.assertEqual(capability["value_state"], "processing_gap")
        with self.assertRaises(MrtParserUnavailableError):
            tuple(parse_mrt_artifact("updates.20260304.1135.gz"))


class RouteEventIndexFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.artifact = make_artifact()
        self.manifest, self.verification = make_manifest([self.artifact])
        self.incident_id = "inc_v1_" + "1" * 24
        self.incidents = (
            IncidentObservation(
                incident_id=self.incident_id,
                event_time_utc="2026-03-04T11:35:43Z",
                affected_objects=(
                    IncidentObject("prefix", "203.0.113.9/24"),
                    IncidentObject("asn", "64496"),
                    IncidentObject("country", "CN"),
                ),
                window_before_seconds=0,
                window_after_seconds=0,
            ),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, name="route-events.sqlite", **overrides):
        options = {
            "manifest": self.manifest,
            "manifest_verification": self.verification,
            "provenance": provenance(),
            "record_stream_factory": update_records,
            "incidents": self.incidents,
        }
        options.update(overrides)
        return build_route_event_index(self.root / name, **options)


class BuildAndReadTest(RouteEventIndexFixture):
    def test_builds_contract_objects_raw_refs_and_observation_only_links(self):
        result = self.build()
        self.assertEqual(result.path.stat().st_mode & 0o777, 0o440)
        self.assertEqual(result.summary["artifact_count"], 1)
        self.assertEqual(result.summary["raw_record_count"], 2)
        self.assertEqual(result.summary["route_event_count"], 2)
        self.assertEqual(result.summary["incident_route_event_link_count"], 3)
        self.assertEqual(result.summary["unsupported_incident_object_count"], 1)
        self.assertEqual(result.summary["classification"], "observation_only")
        self.assertIsNone(result.summary["causal_conclusion"])

        announce_id = route_event_id_v1(self.artifact["file_sha256"], 0, 0)
        withdraw_id = route_event_id_v1(self.artifact["file_sha256"], 0, 1)
        with RouteEventIndex(result.path) as index:
            verified = index.verify()
            self.assertTrue(verified["verified"])
            announce = index.get_route_event(announce_id)
            withdraw = index.get_route_event(withdraw_id)
            links = index.links_for_incident(self.incident_id)
            incident = index.get_incident_observation(self.incident_id)

        self.assertEqual(announce["schema_version"], "route_event_v1")
        self.assertEqual(announce["record_kind"], "route_event")
        self.assertEqual(announce["route_event_id_schema"], "route_event_id_v1")
        self.assertEqual(announce["lineage_status"], "raw_traceable")
        self.assertEqual(announce["prefix"], "203.0.113.0/24")
        self.assertEqual(announce["origin_asn"], 64496)
        self.assertEqual(announce["as_path"]["causal_conclusion"], None)
        self.assertEqual(announce["raw_ref"]["record_ordinal"], 0)
        self.assertEqual(announce["raw_ref"]["element_ordinal"], 0)
        self.assertEqual(
            announce["raw_ref"]["record_hash"],
            hashlib.sha256(update_records(self.artifact)[0].raw_record).hexdigest(),
        )
        self.assertEqual(announce["parser_version"], "1.2.3")
        self.assertEqual(withdraw["as_path"], None)
        self.assertEqual(withdraw["origin_asn"], None)
        self.assertEqual(
            withdraw["missing_reasons"],
            {"as_path": "not_applicable", "origin_asn": "not_applicable"},
        )
        self.assertEqual(
            {(link["object_type"], link["route_event_id"]) for link in links},
            {
                ("prefix", announce_id),
                ("prefix", withdraw_id),
                ("asn", announce_id),
            },
        )
        self.assertTrue(
            all(link["classification"] == "observation_only" for link in links)
        )
        self.assertTrue(all(link["causal_conclusion"] is None for link in links))
        country = next(
            item
            for item in incident["affected_objects"]
            if item["object_type"] == "country"
        )
        self.assertEqual(country["association_state"], "not_applicable")
        self.assertEqual(country["missing_reason"], "not_applicable")
        self.assertEqual(incident["classification"], "observation_only")
        self.assertIsNone(incident["causal_conclusion"])

    def test_generated_route_event_passes_frozen_json_schema(self):
        result = self.build()
        event_id = route_event_id_v1(self.artifact["file_sha256"], 0, 0)
        with RouteEventIndex(result.path) as index:
            event = index.get_route_event(event_id)
        event_path = self.root / "route-event.json"
        schema_path = ROOT / "contracts/data/route-event.schema.json"
        event_path.write_text(json.dumps(event), encoding="utf-8")
        script = r"""
const fs = require('fs');
const path = require('path');
const root = process.argv[1];
const schema = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const payload = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const Ajv = require(path.join(root, 'frontend/node_modules/@redocly/ajv/dist/2020')).default;
const ajv = new Ajv({allErrors: true, allowUnionTypes: true, strict: true, validateFormats: true});
ajv.addFormat('date-time', {type: 'string', validate: (v) => typeof v === 'string' && v.endsWith('Z')});
ajv.addFormat('uri', {type: 'string', validate: () => true});
ajv.addFormat('uri-reference', {type: 'string', validate: () => true});
ajv.addFormat('ipv4', {type: 'string', validate: () => true});
ajv.addFormat('ipv6', {type: 'string', validate: () => true});
const validate = ajv.compile(schema);
if (!validate(payload)) {
  process.stderr.write(ajv.errorsText(validate.errors));
  process.exit(1);
}
"""
        subprocess.run(
            ["node", "-e", script, str(ROOT), str(schema_path), str(event_path)],
            cwd=ROOT,
            check=True,
        )

    def test_as_set_and_empty_path_keep_explicit_origin_missing_state(self):
        timestamp = int(datetime(2026, 3, 4, 11, 35, 43, tzinfo=UTC).timestamp())
        raw = mrt_record(timestamp, b"paths")

        def records(_artifact):
            return (
                ParsedMrtRecord(
                    0,
                    0,
                    raw,
                    (
                        route_element(
                            as_path=(AsPathSegment("as_set", (64496, 64497)),)
                        ),
                        route_element(as_path=()),
                    ),
                ),
            )

        result = self.build(record_stream_factory=records, incidents=())
        with RouteEventIndex(result.path) as index:
            as_set = index.get_route_event(
                route_event_id_v1(self.artifact["file_sha256"], 0, 0)
            )
            empty = index.get_route_event(
                route_event_id_v1(self.artifact["file_sha256"], 0, 1)
            )
        self.assertIsNone(as_set["origin_asn"])
        self.assertEqual(as_set["missing_reasons"]["origin_asn"], "not_observed")
        self.assertIn("as_set_present", as_set["quality_flags"])
        self.assertIn("origin_ambiguous", as_set["quality_flags"])
        self.assertEqual(empty["as_path"]["segments"], [])
        self.assertIn("empty_as_path", empty["quality_flags"])
        self.assertEqual(empty["missing_reasons"]["origin_asn"], "not_observed")

    def test_rib_snapshot_is_preserved_but_update_rib_mismatch_is_rejected(self):
        rib_artifact = make_artifact(b"rib", artifact_type="rib")
        rib_manifest, rib_verification = make_manifest([rib_artifact])
        timestamp = int(datetime(2026, 3, 4, 0, 0, 0, tzinfo=UTC).timestamp())
        raw = mrt_record(timestamp, b"rib", mrt_type=13, mrt_subtype=2)

        def rib_records(_artifact):
            return (
                ParsedMrtRecord(
                    0,
                    0,
                    raw,
                    (
                        route_element(
                            timestamp="2026-03-03T23:59:00Z",
                            action="rib_snapshot",
                            prefix="2001:db8:1::9/48",
                            peer_ip="2001:db8::10",
                        ),
                    ),
                ),
            )

        result = self.build(
            manifest=rib_manifest,
            manifest_verification=rib_verification,
            record_stream_factory=rib_records,
            incidents=(),
        )
        event_id = route_event_id_v1(rib_artifact["file_sha256"], 0, 0)
        with RouteEventIndex(result.path) as index:
            event = index.get_route_event(event_id)
        self.assertEqual(event["action"], "rib_snapshot")
        self.assertEqual(event["afi_safi"], "ipv6_unicast")
        self.assertEqual(event["prefix"], "2001:db8:1::/48")

        update_raw = mrt_record(timestamp, b"wrong-action", mrt_type=16, mrt_subtype=4)

        def update_with_rib_action(_artifact):
            return (
                ParsedMrtRecord(
                    0,
                    0,
                    update_raw,
                    (
                        route_element(
                            timestamp="2026-03-04T00:00:00Z",
                            action="rib_snapshot",
                        ),
                    ),
                ),
            )

        with self.assertRaisesRegex(RouteEventInputError, "UPDATE 制品"):
            self.build(
                name="bad-update.sqlite",
                record_stream_factory=update_with_rib_action,
                incidents=(),
            )

    def test_same_inputs_replay_to_same_ids_summary_fingerprint_and_bytes(self):
        first = self.build("first.sqlite")
        second = self.build("second.sqlite")
        self.assertEqual(first.summary, second.summary)
        self.assertEqual(
            hashlib.sha256(first.path.read_bytes()).hexdigest(),
            hashlib.sha256(second.path.read_bytes()).hexdigest(),
        )

    def test_published_index_is_read_only_and_never_overwritten(self):
        result = self.build()
        before = result.path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(result.path.read_bytes(), before)
        uri = "file:{}?mode=ro&immutable=1".format(result.path.resolve().as_posix())
        connection = sqlite3.connect(uri, uri=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM route_event")
        finally:
            connection.close()
        with self.assertRaisesRegex(RouteEventInputError, "不可写"):
            self.build(name="writable.sqlite", mode=0o600)


class FailureClosedTest(RouteEventIndexFixture):
    def test_manifest_must_have_matching_verification_and_fingerprint(self):
        with self.assertRaisesRegex(RouteEventInputError, "verify_artifact_manifest"):
            self.build(manifest_verification={})
        forged = dict(self.manifest)
        forged["artifact_id_schema"] = "forged"
        with self.assertRaisesRegex(RouteEventInputError, "fingerprint"):
            self.build(name="forged.sqlite", manifest=forged)

    def test_path_bytes_and_missing_as_path_cannot_be_promoted(self):
        with self.assertRaisesRegex(RouteEventInputError, "ParsedMrtRecord 流"):
            self.build(record_stream_factory=lambda _artifact: b"raw-file")

        timestamp = int(datetime(2026, 3, 4, 11, 35, 43, tzinfo=UTC).timestamp())
        raw = mrt_record(timestamp)

        def actually_missing_path(_artifact):
            return (
                ParsedMrtRecord(
                    0,
                    0,
                    raw,
                    (
                        ParsedRouteElement(
                            "2026-03-04T11:35:43Z",
                            "192.0.2.10",
                            64500,
                            "announce",
                            "203.0.113.0/24",
                            "ipv4_unicast",
                            None,
                        ),
                    ),
                ),
            )

        with self.assertRaisesRegex(RouteEventInputError, "缺少结构化 AS_PATH"):
            self.build(
                name="missing-path.sqlite",
                record_stream_factory=actually_missing_path,
                incidents=(),
            )

    def test_raw_traceable_rejects_legacy_untraceable_quality_flags(self):
        timestamp = int(datetime(2026, 3, 4, 11, 35, 43, tzinfo=UTC).timestamp())
        raw = mrt_record(timestamp)

        def records(_artifact):
            return (
                ParsedMrtRecord(
                    0,
                    0,
                    raw,
                    (route_element(quality_flags=("raw_reference_unavailable",)),),
                ),
            )

        with self.assertRaisesRegex(RouteEventInputError, "历史不可追溯"):
            self.build(
                name="legacy-flag.sqlite",
                record_stream_factory=records,
                incidents=(),
            )

    def test_record_coordinates_header_and_update_time_are_strict(self):
        timestamp = int(datetime(2026, 3, 4, 11, 35, 43, tzinfo=UTC).timestamp())
        raw = mrt_record(timestamp)
        cases = {
            "ordinal": ParsedMrtRecord(1, 0, raw, (route_element(),)),
            "offset": ParsedMrtRecord(0, 1, raw, (route_element(),)),
            "length": ParsedMrtRecord(0, 0, raw[:-1], (route_element(),)),
            "time": ParsedMrtRecord(
                0,
                0,
                raw,
                (route_element(timestamp="2026-03-04T11:35:44Z"),),
            ),
            "mrt_type": ParsedMrtRecord(
                0,
                0,
                mrt_record(timestamp, mrt_type=13),
                (route_element(),),
            ),
        }
        for label, record in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(RouteEventInputError):
                    self.build(
                        name=f"bad-{label}.sqlite",
                        record_stream_factory=lambda _artifact, record=record: (record,),
                        incidents=(),
                    )
                self.assertFalse((self.root / f"bad-{label}.sqlite").exists())

    def test_partial_failure_leaves_no_candidate(self):
        def failing(_artifact):
            records = update_records(self.artifact)
            yield records[0]
            raise RuntimeError("parser crashed")

        destination = self.root / "partial.sqlite"
        with self.assertRaisesRegex(RuntimeError, "parser crashed"):
            self.build(name=destination.name, record_stream_factory=failing)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".partial.sqlite.tmp-*")), [])

    def test_tampering_is_detected_by_content_fingerprint(self):
        result = self.build()
        os.chmod(result.path, 0o600)
        connection = sqlite3.connect(result.path)
        try:
            connection.execute(
                "UPDATE route_event SET prefix = '198.51.100.0/24' WHERE action = 'announce'"
            )
            connection.commit()
        finally:
            connection.close()
        os.chmod(result.path, 0o440)
        with RouteEventIndex(result.path) as index:
            with self.assertRaisesRegex(
                RouteEventIndexIntegrityError, "fingerprint 不一致"
            ):
                index.verify()


if __name__ == "__main__":
    unittest.main()
