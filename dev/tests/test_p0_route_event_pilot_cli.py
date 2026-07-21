from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import backend.data_pipeline.route_event as route_event
from dev.data_quality import p0_route_event_pilot as cli


ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
MANIFEST_FINGERPRINT_SCHEMA = "mrt_artifact_manifest_fingerprint_v1"
ATTESTATION_FINGERPRINT_SCHEMA = "parser_attestation_fingerprint_v1"


def canonical_bytes(value):
    return (route_event.canonical_json(value) + "\n").encode("utf-8")


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class RouteEventPilotCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.raw_root = self.base / "raw"
        raw_directory = self.raw_root / "rrc25" / "2026.02"
        raw_directory.mkdir(parents=True)
        self.output = self.base / "output"
        self.output.mkdir()

        timestamp = int(
            datetime(2026, 2, 1, 0, 0, 30, tzinfo=UTC).timestamp()
        )
        payload = b"independent-post-build-fixture"
        self.frame = struct.pack("!IHHI", timestamp, 16, 4, len(payload)) + payload
        compressed = gzip.compress(self.frame, mtime=0)
        self.update_path = raw_directory / "updates.20260201.0000.gz"
        self.update_path.write_bytes(compressed)
        update_sha = hashlib.sha256(compressed).hexdigest()
        update = {
            "artifact_id": route_event.artifact_id_v1(update_sha),
            "artifact_id_schema": "artifact_id_v1",
            "collector_id": "rrc25",
            "artifact_type": "update",
            "artifact_time_utc": "2026-02-01T00:00:00Z",
            "relative_path": "rrc25/2026.02/updates.20260201.0000.gz",
            "filename_family": "updates",
            "compression": "gz",
            "size_bytes": len(compressed),
            "file_sha256": update_sha,
        }
        rib_sha = hashlib.sha256(b"unselected-rib").hexdigest()
        rib = {
            "artifact_id": route_event.artifact_id_v1(rib_sha),
            "artifact_id_schema": "artifact_id_v1",
            "collector_id": "rrc25",
            "artifact_type": "rib",
            "artifact_time_utc": "2026-02-01T00:00:00Z",
            "relative_path": "rrc25/2026.02/bview.20260201.0000.bz2",
            "filename_family": "bview",
            "compression": "bz2",
            "size_bytes": len(b"unselected-rib"),
            "file_sha256": rib_sha,
        }
        profile = {
            "id": "p0-cli-pilot-fixture",
            "timezone": "UTC",
            "window_start": "2026-02-01T00:00:00+00:00",
            "window_end_exclusive": "2026-02-01T00:10:00+00:00",
            "window_start_utc": "2026-02-01T00:00:00Z",
            "window_end_exclusive_utc": "2026-02-01T00:10:00Z",
        }
        manifest_payload = {
            "schema_version": 1,
            "manifest_kind": "mrt_artifact_manifest",
            "artifact_id_schema": "artifact_id_v1",
            "data_profile": profile,
            "artifacts": [update, rib],
            "coverage": {
                "coverage_status": "partial",
                "missing_value_state": "source_unavailable",
            },
        }
        manifest = dict(manifest_payload)
        manifest["manifest_fingerprint_sha256"] = hashlib.sha256(
            route_event.canonical_json(
                {
                    "schema": MANIFEST_FINGERPRINT_SCHEMA,
                    "manifest": manifest_payload,
                }
            ).encode("utf-8")
        ).hexdigest()
        self.manifest_path = self.base / "p0-artifact-manifest.json"
        self.manifest_path.write_bytes(canonical_bytes(manifest))
        verification = {
            "verified": True,
            "artifact_count": 2,
            "manifest_fingerprint_sha256": manifest[
                "manifest_fingerprint_sha256"
            ],
        }
        manifest_summary = {
            "manifest": {
                "sha256": file_sha256(self.manifest_path),
                "fingerprint_sha256": manifest[
                    "manifest_fingerprint_sha256"
                ],
            },
            "verification": verification,
        }
        self.manifest_summary_path = self.base / "manifest.summary.zh.json"
        self.manifest_summary_path.write_bytes(canonical_bytes(manifest_summary))
        self.update = update

    def tearDown(self):
        self.temporary.cleanup()

    def arguments(self):
        return [
            "--pipeline-root",
            str(ROOT),
            "--raw-root",
            str(self.raw_root),
            "--manifest",
            str(self.manifest_path),
            "--manifest-summary",
            str(self.manifest_summary_path),
            "--select-relative-path",
            self.update["relative_path"],
            "--bgpdump-path",
            "/usr/bin/true",
            "--bgpdump-sha256",
            "a" * 64,
            "--processing-time-utc",
            "2026-02-01T00:10:00Z",
            "--output-dir",
            str(self.output),
            "--max-artifacts",
            "1",
            "--max-compressed-bytes",
            "1048576",
            "--max-physical-records",
            "100",
            "--max-route-events",
            "100",
            "--max-spool-bytes",
            str(32 * 1024 * 1024),
        ]

    def test_cli_emits_d5_summary_and_independent_raw_audit(self):
        frame = self.frame

        class FakeAttestedFactory:
            def __init__(
                factory_self,
                _raw_root,
                artifacts,
                *,
                data_profile,
                pilot_limits,
                allowed_binary_sha256,
                **_options,
            ):
                factory_self.artifact = dict(artifacts[0])
                factory_self.limits = dict(pilot_limits)
                factory_self.statistics = {}
                configuration = {
                    "command_arguments": ["-m", "-p", "-v", "/dev/stdin"],
                    "binary_execution_policy": "verified_open_fd_exec",
                    "max_frame_bytes": 64 * 1024 * 1024,
                    "max_spool_bytes": pilot_limits["max_spool_bytes"],
                    "window_start_utc": data_profile["window_start_utc"],
                    "window_end_exclusive_utc": data_profile[
                        "window_end_exclusive_utc"
                    ],
                    "pilot_limits": factory_self.limits,
                }
                payload = {
                    "schema_version": "parser_attestation_v1",
                    "parser_name": "bgpdump",
                    "parser_version": "1.6.2",
                    "parser_binary_sha256": tuple(allowed_binary_sha256)[0],
                    "adapter_name": "domeye_bgpdump_adapter",
                    "adapter_version": "1.0.0",
                    "adapter_source_sha256": "b" * 64,
                    "binary_execution_policy": "verified_open_fd_exec",
                    "configuration": configuration,
                    "configuration_sha256": hashlib.sha256(
                        route_event.canonical_json(configuration).encode("utf-8")
                    ).hexdigest(),
                    "pilot_limits": factory_self.limits,
                    "security_boundary": "test verified opened fd execution",
                }
                payload["attestation_fingerprint_sha256"] = hashlib.sha256(
                    route_event.canonical_json(
                        {
                            "schema": ATTESTATION_FINGERPRINT_SCHEMA,
                            "attestation": payload,
                        }
                    ).encode("utf-8")
                ).hexdigest()
                factory_self.attestation = payload

            @property
            def parser_attestation(factory_self):
                return dict(factory_self.attestation)

            @property
            def statistics_by_artifact(factory_self):
                return dict(factory_self.statistics)

            def __call__(factory_self, artifact):
                raw_hash = hashlib.sha256(frame).hexdigest()
                chain = hashlib.sha256()
                chain.update(struct.pack("!QQQ", 0, 0, len(frame)))
                chain.update(bytes.fromhex(raw_hash))
                factory_self.statistics[artifact["artifact_id"]] = {
                    "status": "complete",
                    "artifact_id": artifact["artifact_id"],
                    "physical_record_count": 1,
                    "route_record_count": 1,
                    "state_change_record_count": 0,
                    "keepalive_record_count": 0,
                    "route_element_count": 1,
                    "announce_count": 1,
                    "withdraw_count": 0,
                    "state_change_transitions": [],
                    "record_hash_chain_sha256": chain.hexdigest(),
                    "compressed_file_sha256": artifact["file_sha256"],
                    "compressed_size_bytes": artifact["size_bytes"],
                    "compressed_read_passes": 1,
                    "peak_spool_bytes": len(frame) + 64,
                    "spool_persistence": "anonymous_unlinked_fd",
                    "parser_version": "1.6.2",
                    "parser_binary_sha256": "a" * 64,
                }
                return (
                    route_event.ParsedMrtRecord(
                        record_ordinal=0,
                        record_offset=0,
                        raw_record=frame,
                        elements=(
                            route_event.ParsedRouteElement(
                                event_time_utc="2026-02-01T00:00:30Z",
                                peer_ip="192.0.2.10",
                                peer_asn=64500,
                                action="announce",
                                prefix="203.0.113.0/24",
                                afi_safi="ipv4_unicast",
                                as_path=(
                                    route_event.AsPathSegment(
                                        "as_sequence", (64500, 64496)
                                    ),
                                ),
                            ),
                        ),
                    ),
                )

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            route_event, "BgpdumpRecordStreamFactory", FakeAttestedFactory
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(self.arguments())
        self.assertEqual((status, stderr.getvalue()), (0, ""))
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["pilot_only"])

        index_path = self.output / "p0-route-event-pilot.sqlite3"
        route_summary_path = (
            self.output / "route-event-reconciliation-summary.json"
        )
        self.assertTrue(index_path.is_file())
        route_summary = json.loads(route_summary_path.read_text(encoding="utf-8"))
        self.assertEqual(
            route_summary["schema_version"], "route_event_index_summary_v1"
        )
        self.assertEqual(
            route_summary["parser_capability"],
            "bgpdump_1_6_2_update_pilot",
        )
        self.assertEqual(
            route_summary["raw_reference_audit"]["record_offset_basis"],
            "decompressed_mrt_stream",
        )
        self.assertEqual(
            route_summary["raw_reference_audit"][
                "record_hash_checked_count"
            ],
            1,
        )
        quality_fields = (
            "raw_reference_unresolved_count",
            "processing_lineage_missing_count",
            "record_hash_verification_failed_count",
            "vp_identity_missing_count",
            "route_event_id_conflict_count",
            "invalid_asn_count",
            "invalid_prefix_count",
            "outside_window_record_count",
        )
        self.assertTrue(all(route_summary[field] == 0 for field in quality_fields))

        pilot_summary = json.loads(
            (self.output / "p0-route-event-pilot.summary.zh.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            pilot_summary["anonymous_spool"]["max_spool_bytes_per_artifact"],
            32 * 1024 * 1024,
        )
        self.assertEqual(
            tuple(
                pilot_summary["anonymous_spool"][
                    "peak_spool_bytes_by_artifact"
                ].values()
            ),
            (len(frame) + 64,),
        )

        checksum_lines = (self.output / "SHA256SUMS").read_text(
            encoding="utf-8"
        ).splitlines()
        names = [line.split("  ", 1)[1] for line in checksum_lines]
        self.assertEqual(
            names,
            sorted(
                (
                    "p0-route-event-pilot.sqlite3",
                    "p0-route-event-pilot.summary.zh.json",
                    "p0-update-pilot-selection.json",
                    "route-event-reconciliation-summary.json",
                )
            ),
        )

    def test_nonempty_output_directory_is_never_mixed_or_overwritten(self):
        owner = self.output / "owner-evidence.txt"
        owner.write_text("do not overwrite\n", encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(self.arguments())
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("输出目录必须为空", stderr.getvalue())
        self.assertEqual(owner.read_text(encoding="utf-8"), "do not overwrite\n")
        self.assertEqual(tuple(self.output.iterdir()), (owner,))


if __name__ == "__main__":
    unittest.main()
