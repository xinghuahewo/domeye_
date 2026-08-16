import bz2
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from backend.data_pipeline.route_event import (
    ArtifactIntegrityError,
    ArtifactManifestError,
    artifact_id_v1,
    atomic_write_manifest,
    canonical_json,
    derive_update_pilot_selection,
    scan_mrt_artifacts,
    verify_artifact_manifest,
    verify_update_pilot_selection,
)


UTC = timezone.utc


def profile(start="2026-02-01T00:00:00+00:00", end="2026-02-01T00:10:00+00:00"):
    return {
        "id": "fixture-window",
        "timezone": "UTC",
        "window_start": start,
        "window_end_exclusive": end,
    }


def compressed_payload(name, payload):
    if name.endswith(".gz"):
        return gzip.compress(payload, mtime=0)
    if name.endswith(".bz2"):
        return bz2.compress(payload)
    raise AssertionError("fixture 扩展名非法")


class RawArtifactFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "raw"
        self.collector = self.root / "rrc25" / "2026.02"
        self.collector.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, payload=b"fixture-mrt"):
        path = self.collector / name
        path.write_bytes(compressed_payload(name, payload))
        return path

    def scan(self, data_profile=None):
        return scan_mrt_artifacts(self.root, data_profile or profile(), ["rrc25"])


class ArtifactIdentityTest(unittest.TestCase):
    def test_artifact_id_hashes_canonical_identity_instead_of_slicing_file_hash(self):
        file_hash = hashlib.sha256(b"fixture").hexdigest()
        identity = {"schema": "artifact_id_v1", "file_sha256": file_hash}
        expected = "art_v1_" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:32]
        self.assertEqual(artifact_id_v1(file_hash), expected)
        self.assertNotEqual(artifact_id_v1(file_hash), "art_v1_" + file_hash[:32])
        for invalid in ("", "A" * 64, "0" * 63, None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ArtifactManifestError):
                    artifact_id_v1(invalid)


class ScannerTest(RawArtifactFixture):
    def test_verified_mixed_manifest_derives_bounded_update_only_selection(self):
        self.write("updates.20260201.0000.gz", b"update")
        self.write("bview.20260201.0000.gz", b"rib")
        manifest = self.scan()
        verification = verify_artifact_manifest(self.root, manifest)
        update = next(
            row for row in manifest["artifacts"] if row["artifact_type"] == "update"
        )
        selection = derive_update_pilot_selection(
            manifest,
            verification,
            (update["artifact_id"],),
            max_artifact_count=1,
            max_compressed_bytes=1024 * 1024,
            max_physical_records=1000,
            max_route_events=5000,
            max_spool_bytes=32 * 1024 * 1024,
        )
        selected_verification = verify_update_pilot_selection(
            manifest, verification, selection
        )

        self.assertTrue(selected_verification["verified"])
        self.assertTrue(selection["pilot_only"])
        self.assertFalse(selection["production_complete"])
        self.assertEqual(
            selection["parent_manifest_fingerprint_sha256"],
            manifest["manifest_fingerprint_sha256"],
        )
        self.assertEqual(
            [row["artifact_type"] for row in selection["selected_artifacts"]],
            ["update"],
        )
        self.assertEqual(
            selection["selection_summary"]["excluded"]["rib_not_supported"][
                "artifact_count"
            ],
            1,
        )
        self.assertEqual(
            selection["coverage_semantics"]["parent_manifest_coverage"],
            manifest["coverage"],
        )
        self.assertEqual(
            selection["coverage_semantics"]["selection_coverage_claim"],
            "none_pilot_subset",
        )
        self.assertEqual(
            selection["limits"]["max_spool_bytes"], 32 * 1024 * 1024
        )

        forged = deepcopy(selection)
        forged["selection_summary"]["excluded"]["rib_not_supported"][
            "artifact_count"
        ] = 0
        with self.assertRaisesRegex(ArtifactIntegrityError, "fingerprint"):
            verify_update_pilot_selection(manifest, verification, forged)

    def test_update_selection_rejects_rib_and_limits_above_absolute_boundary(self):
        self.write("updates.20260201.0000.gz", b"update")
        self.write("bview.20260201.0000.gz", b"rib")
        manifest = self.scan()
        verification = verify_artifact_manifest(self.root, manifest)
        rib = next(row for row in manifest["artifacts"] if row["artifact_type"] == "rib")
        with self.assertRaisesRegex(ArtifactManifestError, "RIB"):
            derive_update_pilot_selection(
                manifest,
                verification,
                (rib["artifact_id"],),
                max_artifact_count=1,
                max_compressed_bytes=1024 * 1024,
                max_physical_records=100,
                max_route_events=100,
                max_spool_bytes=32 * 1024 * 1024,
            )
        update = next(row for row in manifest["artifacts"] if row["artifact_type"] == "update")
        with self.assertRaisesRegex(ArtifactManifestError, "1..5"):
            derive_update_pilot_selection(
                manifest,
                verification,
                (update["artifact_id"],),
                max_artifact_count=6,
                max_compressed_bytes=1024 * 1024,
                max_physical_records=100,
                max_route_events=100,
                max_spool_bytes=32 * 1024 * 1024,
            )
        with self.assertRaisesRegex(ArtifactManifestError, "max_spool_bytes|1.."):
            derive_update_pilot_selection(
                manifest,
                verification,
                (update["artifact_id"],),
                max_artifact_count=1,
                max_compressed_bytes=1024 * 1024,
                max_physical_records=100,
                max_route_events=100,
                max_spool_bytes=16 * 1024 * 1024 * 1024 + 1,
            )

    def test_boundary_slots_sorting_and_rerun_are_fully_deterministic(self):
        self.write("updates.20260201.0005.bz2", b"second")
        self.write("bview.20260201.0000.gz", b"rib")
        self.write("updates.20260201.0000.gz", b"first")

        first = self.scan()
        second = self.scan()

        self.assertEqual(first, second)
        self.assertEqual(
            first["manifest_fingerprint_sha256"], second["manifest_fingerprint_sha256"]
        )
        self.assertNotIn("generated_at", canonical_json(first))
        self.assertNotIn(str(self.root), canonical_json(first))
        self.assertEqual(
            [(row["artifact_type"], row["artifact_time_utc"]) for row in first["artifacts"]],
            [
                ("rib", "2026-02-01T00:00:00Z"),
                ("update", "2026-02-01T00:00:00Z"),
                ("update", "2026-02-01T00:05:00Z"),
            ],
        )
        self.assertEqual(first["coverage"]["expected_slots"], 3)
        self.assertEqual(first["coverage"]["available_slots"], 3)
        self.assertEqual(first["coverage"]["coverage_status"], "complete")
        self.assertIsNone(first["coverage"]["missing_value_state"])
        self.assertEqual(first["summary"]["artifact_count"], 3)
        self.assertEqual(
            first["summary"]["size_bytes"],
            sum(row["size_bytes"] for row in first["artifacts"]),
        )
        self.assertTrue(verify_artifact_manifest(self.root, first)["verified"])

    def test_out_of_window_files_are_excluded_unhashed_unless_strict(self):
        data_profile = profile(
            start="2026-02-01T00:05:00+00:00",
            end="2026-02-01T00:10:00+00:00",
        )
        before = self.collector / "updates.20260201.0000.gz"
        after = self.collector / "updates.20260201.0010.gz"
        before.write_bytes(b"outside-before-is-not-read")
        after.write_bytes(b"outside-after-is-not-read")

        manifest = self.scan(data_profile)
        self.assertEqual(manifest["artifacts"], [])
        self.assertEqual(manifest["coverage"]["available_slots"], 0)
        excluded = manifest["summary"]["excluded_out_of_window"]
        self.assertEqual(excluded["file_count"], 2)
        self.assertEqual(
            excluded["size_bytes"], before.stat().st_size + after.stat().st_size
        )
        self.assertEqual(
            [row["reason"] for row in excluded["boundary_samples"]],
            ["before_window", "at_or_after_window_end"],
        )
        self.assertNotIn("file_sha256", canonical_json(excluded))

        with self.assertRaisesRegex(ArtifactManifestError, "越出数据档窗口"):
            scan_mrt_artifacts(
                self.root, data_profile, ["rrc25"], strict_out_of_window=True
            )

    def test_future_month_growth_is_excluded_without_inventory_and_fingerprint_stays_stable(self):
        self.write("updates.20260201.0000.gz", b"in-scope")
        first = self.scan()
        self.assertEqual(
            first["scan_policy"]["directory_scope"],
            {
                "basis": "utc_month_directories_intersecting_half_open_profile_window",
                "included_month_directories": ["2026.02"],
                "missing_included_month_directory": "treat_as_empty",
                "other_month_directories": "excluded_without_inventory",
                "filename_utc_month_must_match_directory": True,
            },
        )

        future = self.root / "rrc25" / "2026.03"
        future.mkdir()
        # 即使未来目录继续增长、含未知条目或重复载荷，也不得被 inventory。
        (future / "updates.20260301.0000.gz").write_bytes(
            (self.collector / "updates.20260201.0000.gz").read_bytes()
        )
        (future / "README.unknown").write_text("live future month", encoding="utf-8")
        second = self.scan()

        self.assertEqual(first, second)
        self.assertEqual(
            first["manifest_fingerprint_sha256"],
            second["manifest_fingerprint_sha256"],
        )
        self.assertTrue(verify_artifact_manifest(self.root, first)["verified"])
        self.assertNotIn("2026.03", canonical_json(first))

    def test_selected_month_filename_utc_month_must_match_directory(self):
        misplaced = self.collector / "updates.20260301.0000.gz"
        misplaced.write_bytes(gzip.compress(b"misplaced", mtime=0))
        with self.assertRaisesRegex(ArtifactManifestError, "年月与所属月目录"):
            self.scan()

    def test_unaligned_slots_are_rejected(self):
        self.write("updates.20260201.0001.gz")
        with self.assertRaisesRegex(ArtifactManifestError, "未对齐"):
            self.scan()

    def test_bview_and_rib_aliases_cannot_duplicate_one_slot(self):
        self.write("bview.20260201.0000.gz", b"first")
        self.write("rib.20260201.0000.bz2", b"second")
        with self.assertRaisesRegex(ArtifactManifestError, "重复槽"):
            self.scan()

    def test_duplicate_out_of_window_slot_also_fails_closed(self):
        self.write("bview.20260201.0800.gz", b"first")
        self.write("rib.20260201.0800.bz2", b"second")
        with self.assertRaisesRegex(ArtifactManifestError, "重复槽"):
            self.scan()

    def test_symlink_non_regular_and_unknown_names_are_rejected(self):
        outside = Path(self.temporary.name) / "outside.gz"
        outside.write_bytes(gzip.compress(b"outside", mtime=0))
        link = self.collector / "updates.20260201.0000.gz"
        link.symlink_to(outside)
        with self.assertRaisesRegex(ArtifactManifestError, "符号链接"):
            self.scan()
        link.unlink()

        unknown = self.collector / "README.txt"
        unknown.write_text("not an artifact", encoding="utf-8")
        with self.assertRaisesRegex(ArtifactManifestError, "未知原始制品命名"):
            self.scan()
        unknown.unlink()

        if hasattr(os, "mkfifo"):
            fifo = self.collector / "updates.20260201.0000.gz"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ArtifactManifestError, "仅允许普通文件"):
                self.scan()

    def test_empty_and_wrong_magic_are_hashed_quarantined_and_reverified(self):
        empty_paths = [
            self.collector / "updates.20260201.{}.gz".format(slot)
            for slot in ("0000", "0005", "0010", "0015")
        ]
        wrong_magic = self.collector / "updates.20260201.0020.bz2"
        for empty in empty_paths:
            empty.write_bytes(b"")
        wrong_magic.write_bytes(b"not-bzip2")

        manifest = self.scan(profile(end="2026-02-01T00:25:00+00:00"))
        self.assertEqual(manifest["artifacts"], [])
        self.assertEqual(manifest["coverage"]["available_slots"], 0)
        self.assertEqual(manifest["coverage"]["missing_slots"], 6)
        invalid = manifest["invalid_in_window"]
        self.assertEqual(
            [(row["artifact_time_utc"], row["missing_reason"]) for row in invalid],
            [
                ("2026-02-01T00:00:00Z", "empty_file"),
                ("2026-02-01T00:05:00Z", "empty_file"),
                ("2026-02-01T00:10:00Z", "empty_file"),
                ("2026-02-01T00:15:00Z", "empty_file"),
                ("2026-02-01T00:20:00Z", "compression_magic_mismatch"),
            ],
        )
        for row in invalid[:4]:
            self.assertEqual(row["size_bytes"], 0)
            self.assertEqual(row["file_sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(invalid[4]["file_sha256"], hashlib.sha256(b"not-bzip2").hexdigest())
        self.assertTrue(all(row["value_state"] == "parse_failed" for row in invalid))
        self.assertEqual(
            manifest["summary"]["invalid_in_window"]["by_missing_reason"],
            {
                "compressed_stream_invalid": {
                    "file_count": 0,
                    "size_bytes": 0,
                },
                "compression_magic_mismatch": {
                    "file_count": 1,
                    "size_bytes": len(b"not-bzip2"),
                },
                "empty_file": {"file_count": 4, "size_bytes": 0},
            },
        )
        self.assertEqual(
            manifest["scan_policy"]["duplicate_content"],
            {
                "valid_artifact": "reject_across_paths",
                "invalid_compressed_stream_invalid": "reject_across_paths",
                "invalid_empty_file": "allow_across_unique_paths_and_slots",
                "invalid_compression_magic_mismatch": "reject_across_paths",
            },
        )
        verification = verify_artifact_manifest(self.root, manifest)
        self.assertTrue(verification["verified"])
        self.assertEqual(verification["invalid_in_window_count"], 5)

        # 隔离记录仍绑定完整文件哈希；空文件被替换为合法 gzip 后必须失败。
        empty_paths[0].write_bytes(gzip.compress(b"later-content", mtime=0))
        with self.assertRaisesRegex(ArtifactIntegrityError, "不一致"):
            verify_artifact_manifest(self.root, manifest)

    def test_crc_eof_and_bzip2_truncation_are_hashed_then_quarantined(self):
        crc_bad = bytearray(gzip.compress(b"crc-failure", mtime=0))
        crc_bad[-8] ^= 0x01
        (self.collector / "updates.20260201.0000.gz").write_bytes(crc_bad)
        truncated = gzip.compress(b"truncated", mtime=0)[:-4]
        (self.collector / "updates.20260201.0005.gz").write_bytes(truncated)
        bzip_truncated = bz2.compress(b"rib-truncated")[:-3]
        (self.collector / "bview.20260201.0000.bz2").write_bytes(bzip_truncated)

        manifest = self.scan()

        self.assertEqual(manifest["artifacts"], [])
        self.assertEqual(manifest["coverage"]["available_slots"], 0)
        self.assertEqual(manifest["coverage"]["missing_slots"], 3)
        self.assertEqual(
            [row["missing_reason"] for row in manifest["invalid_in_window"]],
            ["compressed_stream_invalid"] * 3,
        )
        self.assertEqual(
            manifest["summary"]["invalid_in_window"]["by_missing_reason"]
            ["compressed_stream_invalid"]["file_count"],
            3,
        )
        self.assertTrue(
            verify_artifact_manifest(self.root, manifest, integrity_workers=2)["verified"]
        )

    def test_invalid_deflate_block_is_hashed_then_quarantined(self):
        # gzip magic/header 合法，但 deflate BTYPE=3 会由 gzip 直接抛出
        # zlib.error；该内容错误必须隔离，不能让全窗口扫描硬失败。
        invalid_deflate = (
            b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
            + b"\x07"
            + b"\x00" * 8
        )
        path = self.collector / "updates.20260201.0000.gz"
        path.write_bytes(invalid_deflate)

        manifest = self.scan()

        self.assertEqual(manifest["artifacts"], [])
        self.assertEqual(
            manifest["invalid_in_window"],
            [
                {
                    "collector_id": "rrc25",
                    "artifact_type": "update",
                    "artifact_time_utc": "2026-02-01T00:00:00Z",
                    "relative_path": "rrc25/2026.02/updates.20260201.0000.gz",
                    "filename_family": "updates",
                    "compression": "gz",
                    "size_bytes": len(invalid_deflate),
                    "file_sha256": hashlib.sha256(invalid_deflate).hexdigest(),
                    "value_state": "parse_failed",
                    "missing_reason": "compressed_stream_invalid",
                }
            ],
        )
        self.assertTrue(verify_artifact_manifest(self.root, manifest)["verified"])

    def test_concatenated_gzip_members_are_a_valid_compression_envelope(self):
        payload = gzip.compress(b"first", mtime=0) + gzip.compress(b"second", mtime=0)
        path = self.collector / "updates.20260201.0000.gz"
        path.write_bytes(payload)

        manifest = scan_mrt_artifacts(
            self.root,
            profile(),
            ["rrc25"],
            integrity_workers=2,
        )

        self.assertEqual(len(manifest["artifacts"]), 1)
        self.assertEqual(manifest["invalid_in_window"], [])
        self.assertEqual(
            manifest["scan_policy"]["compression_envelope_validation"],
            "full_stream_to_eof_crc_or_equivalent",
        )

    def test_concatenated_bzip2_members_are_a_valid_compression_envelope(self):
        payload = bz2.compress(b"first") + bz2.compress(b"second")
        path = self.collector / "updates.20260201.0000.bz2"
        path.write_bytes(payload)

        manifest = scan_mrt_artifacts(
            self.root,
            profile(),
            ["rrc25"],
            integrity_workers=2,
        )

        self.assertEqual(len(manifest["artifacts"]), 1)
        self.assertEqual(manifest["invalid_in_window"], [])
        self.assertEqual(
            manifest["artifacts"][0]["file_sha256"],
            hashlib.sha256(payload).hexdigest(),
        )
        self.assertTrue(verify_artifact_manifest(self.root, manifest)["verified"])

    def test_bzip2_trailing_garbage_and_incomplete_next_member_are_quarantined(self):
        trailing_garbage = bz2.compress(b"valid-first") + b"trailing-garbage"
        incomplete_next_member = (
            bz2.compress(b"valid-first") + bz2.compress(b"truncated-second")[:-3]
        )
        payloads = {
            "rrc25/2026.02/updates.20260201.0000.bz2": trailing_garbage,
            "rrc25/2026.02/updates.20260201.0005.bz2": incomplete_next_member,
        }
        for relative_path, payload in payloads.items():
            (self.root / relative_path).write_bytes(payload)

        manifest = self.scan()

        self.assertEqual(manifest["artifacts"], [])
        self.assertEqual(len(manifest["invalid_in_window"]), 2)
        invalid_by_path = {
            row["relative_path"]: row for row in manifest["invalid_in_window"]
        }
        self.assertEqual(set(invalid_by_path), set(payloads))
        for relative_path, payload in payloads.items():
            with self.subTest(relative_path=relative_path):
                row = invalid_by_path[relative_path]
                self.assertEqual(row["missing_reason"], "compressed_stream_invalid")
                self.assertEqual(row["value_state"], "parse_failed")
                self.assertEqual(row["size_bytes"], len(payload))
                self.assertEqual(
                    row["file_sha256"], hashlib.sha256(payload).hexdigest()
                )
        self.assertEqual(
            manifest["summary"]["invalid_in_window"]["by_missing_reason"]
            ["compressed_stream_invalid"]["file_count"],
            2,
        )
        self.assertTrue(
            verify_artifact_manifest(self.root, manifest, integrity_workers=2)[
                "verified"
            ]
        )

    def test_duplicate_valid_or_nonempty_invalid_content_still_fails_closed(self):
        self.write("updates.20260201.0000.gz", b"same-valid-content")
        self.write("updates.20260201.0005.gz", b"same-valid-content")
        with self.assertRaisesRegex(ArtifactManifestError, "复用"):
            self.scan()

        for path in self.collector.iterdir():
            path.unlink()
        (self.collector / "updates.20260201.0000.gz").write_bytes(b"same-error")
        (self.collector / "updates.20260201.0005.gz").write_bytes(b"same-error")
        with self.assertRaisesRegex(ArtifactManifestError, "非空无效"):
            self.scan()

    def test_unknown_collector_is_rejected(self):
        bad = self.collector / "updates.20260201.0000.gz"
        bad.write_bytes(b"not-gzip")
        with self.assertRaises(ArtifactManifestError):
            scan_mrt_artifacts(self.root, profile(), ["../rrc25"])

    def test_missing_slots_are_exactly_compressed_as_source_unavailable(self):
        data_profile = profile(end="2026-02-01T00:25:00+00:00")
        self.write("updates.20260201.0000.gz", b"first")
        self.write("updates.20260201.0020.gz", b"last")

        manifest = self.scan(data_profile)
        update = manifest["coverage"]["by_collector"][0]["by_artifact_type"]["update"]
        self.assertEqual(update["expected_slots"], 5)
        self.assertEqual(update["available_slots"], 2)
        self.assertEqual(update["missing_slots"], 3)
        self.assertEqual(
            update["missing_ranges"],
            [
                {
                    "start_time_utc": "2026-02-01T00:05:00Z",
                    "end_time_exclusive_utc": "2026-02-01T00:20:00Z",
                    "slot_count": 3,
                    "value_state": "source_unavailable",
                }
            ],
        )
        self.assertEqual(manifest["coverage"]["missing_value_state"], "source_unavailable")

    def test_fixed_data_profile_expected_slot_counts_are_exact(self):
        fixed_profile = {
            "id": "feb-mar-2026",
            "timezone": "Asia/Shanghai",
            "window_start": "2026-02-01T00:00:00+08:00",
            "window_end_exclusive": "2026-04-01T00:00:00+08:00",
        }
        manifest = self.scan(fixed_profile)
        self.assertEqual(
            manifest["scan_policy"]["directory_scope"]["included_month_directories"],
            ["2026.01", "2026.02", "2026.03"],
        )
        by_type = manifest["coverage"]["by_collector"][0]["by_artifact_type"]
        self.assertEqual(by_type["update"]["expected_slots"], 16_992)
        self.assertEqual(by_type["rib"]["expected_slots"], 177)
        self.assertEqual(manifest["coverage"]["available_slots"], 0)
        self.assertEqual(
            by_type["update"]["missing_ranges"],
            [
                {
                    "start_time_utc": "2026-01-31T16:00:00Z",
                    "end_time_exclusive_utc": "2026-03-31T16:00:00Z",
                    "slot_count": 16_992,
                    "value_state": "source_unavailable",
                }
            ],
        )

    def test_tampering_is_found_by_full_rescan_and_changes_fingerprint(self):
        path = self.write("updates.20260201.0000.gz", b"original")
        manifest = self.scan()
        path.write_bytes(gzip.compress(b"tampered", mtime=0))

        with self.assertRaisesRegex(ArtifactIntegrityError, "不一致"):
            verify_artifact_manifest(self.root, manifest)
        rescanned = self.scan()
        self.assertNotEqual(
            manifest["artifacts"][0]["file_sha256"],
            rescanned["artifacts"][0]["file_sha256"],
        )
        self.assertNotEqual(
            manifest["manifest_fingerprint_sha256"],
            rescanned["manifest_fingerprint_sha256"],
        )

        forged = deepcopy(rescanned)
        forged["artifacts"][0]["file_sha256"] = "0" * 64
        with self.assertRaisesRegex(ArtifactIntegrityError, "fingerprint"):
            verify_artifact_manifest(self.root, forged)

    def test_atomic_writer_creates_once_and_never_overwrites(self):
        self.write("updates.20260201.0000.gz")
        manifest = self.scan()
        output = Path(self.temporary.name) / "output" / "manifest.json"
        output.parent.mkdir()

        self.assertEqual(atomic_write_manifest(output, manifest), output)
        before = output.read_bytes()
        self.assertEqual(before, (canonical_json(manifest) + "\n").encode("utf-8"))
        with self.assertRaises(FileExistsError):
            atomic_write_manifest(output, manifest)
        self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
