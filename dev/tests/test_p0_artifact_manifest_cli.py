import bz2
from contextlib import redirect_stderr, redirect_stdout
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from dev.data_quality import p0_artifact_manifest as cli


ROOT = Path(__file__).resolve().parents[2]
SOURCE_SCANNER = ROOT / "backend/data_pipeline/route_event/artifacts.py"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ArtifactManifestCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.pipeline = self.base / "staging"
        self.scanner = self.pipeline / "backend/data_pipeline/route_event/artifacts.py"
        self.scanner.parent.mkdir(parents=True)
        source = SOURCE_SCANNER.read_text(encoding="utf-8")
        self.scanner.write_text(source + "\n# staging-import-fixture\n", encoding="utf-8")

        self.raw_root = self.base / "raw"
        self.raw = self.raw_root / "rrc25" / "2026.02"
        self.raw.mkdir(parents=True)
        (self.raw / "updates.20260201.0000.gz").write_bytes(
            gzip.compress(b"update", mtime=0)
        )
        (self.raw / "bview.20260201.0000.bz2").write_bytes(bz2.compress(b"rib"))
        # 窗口内空文件必须完整哈希后隔离，不能让整个文件级扫描失去结果。
        (self.raw / "updates.20260201.0005.gz").write_bytes(b"")
        # 排他终点文件故意不是合法 gzip 载荷：默认路径只能读取元数据并排除。
        (self.raw / "updates.20260201.0010.gz").write_bytes(b"excluded-unread")

        self.profile = self.base / "data-profile.json"
        self.profile.write_text(
            json.dumps(
                {
                    "id": "cli-fixture",
                    "timezone": "UTC",
                    "window_start": "2026-02-01T00:00:00+00:00",
                    "window_end_exclusive": "2026-02-01T00:10:00+00:00",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.output_dir = self.base / "output"
        self.output_dir.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def arguments(self, output=None):
        return [
            "--raw-root",
            str(self.raw_root),
            "--data-profile",
            str(self.profile),
            "--collector",
            "rrc25",
            "--output",
            str(output or self.output_dir / "p0-artifact-manifest.json"),
            "--pipeline-root",
            str(self.pipeline),
            "--verify",
        ]

    def invoke(self, arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(arguments)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_staging_import_scan_verify_summary_and_portable_checksums(self):
        output = self.output_dir / "p0-artifact-manifest.json"
        status, stdout, stderr = self.invoke(self.arguments(output))
        self.assertEqual((status, stderr), (0, ""))
        result = json.loads(stdout)
        summary_path = self.output_dir / "p0-artifact-manifest.summary.zh.json"
        checksum_path = self.output_dir / "SHA256SUMS"
        self.assertTrue(output.is_file())
        self.assertTrue(summary_path.is_file())
        self.assertTrue(checksum_path.is_file())

        manifest = json.loads(output.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(result["状态"], "通过")
        self.assertEqual(summary["verification"]["verified"], True)
        self.assertEqual(summary["artifact_counts"], {
            "total": 2,
            "update": 1,
            "rib": 1,
            "size_bytes": manifest["summary"]["size_bytes"],
        })
        self.assertEqual(summary["coverage"]["expected_slots"], 3)
        self.assertEqual(summary["coverage"]["available_slots"], 2)
        self.assertEqual(summary["coverage"]["missing_ranges"][0]["value_state"], "parse_failed")
        self.assertEqual(summary["invalid_in_window"]["file_count"], 1)
        self.assertEqual(summary["invalid_in_window"]["size_bytes"], 0)
        self.assertEqual(
            summary["invalid_in_window"]["records"],
            manifest["invalid_in_window"],
        )
        invalid = summary["invalid_in_window"]["records"][0]
        self.assertEqual(invalid["missing_reason"], "empty_file")
        self.assertEqual(invalid["value_state"], "parse_failed")
        self.assertEqual(invalid["file_sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(summary["verification"]["invalid_in_window_count"], 1)
        self.assertEqual(
            summary["directory_scope"],
            {
                "basis": "utc_month_directories_intersecting_half_open_profile_window",
                "included_month_directories": ["2026.02"],
                "missing_included_month_directory": "treat_as_empty",
                "other_month_directories": "excluded_without_inventory",
                "filename_utc_month_must_match_directory": True,
            },
        )
        excluded = summary["excluded_out_of_window"]
        self.assertEqual(excluded["file_count"], 1)
        self.assertEqual(excluded["boundary_samples"][0]["relative_path"], "rrc25/2026.02/updates.20260201.0010.gz")
        self.assertNotIn("file_sha256", json.dumps(excluded, sort_keys=True))

        # 三个输入 provenance 必须逐文件计算，且 scanner 确为 staging 副本。
        self.assertEqual(
            summary["provenance"]["data_profile"]["sha256"], sha256_file(self.profile)
        )
        self.assertEqual(
            summary["provenance"]["scanner"]["sha256"], sha256_file(self.scanner)
        )
        self.assertNotEqual(sha256_file(self.scanner), sha256_file(SOURCE_SCANNER))
        self.assertEqual(
            summary["provenance"]["cli"]["sha256"], sha256_file(Path(cli.__file__))
        )
        for source in ("data_profile", "scanner", "cli"):
            self.assertRegex(summary["provenance"][source]["sha256"], r"^[0-9a-f]{64}$")

        lines = checksum_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            lines,
            [
                f"{sha256_file(output)}  {output.name}",
                f"{sha256_file(summary_path)}  {summary_path.name}",
            ],
        )
        self.assertNotIn(str(self.base), output.read_text(encoding="utf-8"))

    def test_same_data_has_identical_manifest_and_fingerprint_in_second_output_dir(self):
        first = self.output_dir / "p0-artifact-manifest.json"
        first_status, _, _ = self.invoke(self.arguments(first))
        future = self.raw_root / "rrc25" / "2026.03"
        future.mkdir()
        (future / "updates.20260301.0000.gz").write_bytes(
            gzip.compress(b"live-future-growth", mtime=0)
        )
        (future / "unknown.future").write_bytes(b"not inventoried")
        second_dir = self.base / "output-2"
        second_dir.mkdir()
        second = second_dir / first.name
        second_status, _, second_error = self.invoke(self.arguments(second))
        self.assertEqual((first_status, second_status, second_error), (0, 0, ""))
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(
            json.loads(first.read_text())["manifest_fingerprint_sha256"],
            json.loads(second.read_text())["manifest_fingerprint_sha256"],
        )

    def test_duplicate_collector_and_missing_verify_fail_before_outputs(self):
        duplicate = self.arguments() + ["--collector", "rrc25"]
        status, _, stderr = self.invoke(duplicate)
        self.assertEqual(status, 2)
        self.assertIn("不得重复", stderr)
        self.assertEqual(list(self.output_dir.iterdir()), [])

        without_verify = self.arguments()[:-1]
        status, _, stderr = self.invoke(without_verify)
        self.assertEqual(status, 2)
        self.assertIn("--verify", stderr)
        self.assertEqual(list(self.output_dir.iterdir()), [])

    def test_any_existing_output_target_blocks_all_new_outputs(self):
        output = self.output_dir / "p0-artifact-manifest.json"
        checksum = self.output_dir / "SHA256SUMS"
        checksum.write_text("owner evidence\n", encoding="utf-8")
        status, _, stderr = self.invoke(self.arguments(output))
        self.assertEqual(status, 2)
        self.assertIn("拒绝覆盖", stderr)
        self.assertEqual(checksum.read_text(encoding="utf-8"), "owner evidence\n")
        self.assertFalse(output.exists())
        self.assertFalse((self.output_dir / "p0-artifact-manifest.summary.zh.json").exists())

    def test_profile_symlink_and_duplicate_json_keys_are_rejected(self):
        real_profile = self.profile
        linked_profile = self.base / "linked-profile.json"
        linked_profile.symlink_to(real_profile)
        arguments = self.arguments()
        arguments[arguments.index(str(real_profile))] = str(linked_profile)
        status, _, stderr = self.invoke(arguments)
        self.assertEqual(status, 2)
        self.assertIn("符号链接", stderr)
        self.assertEqual(list(self.output_dir.iterdir()), [])

        real_profile.write_text(
            '{"id":"one","id":"two","timezone":"UTC",'
            '"window_start":"2026-02-01T00:00:00Z",'
            '"window_end_exclusive":"2026-02-01T00:10:00Z"}',
            encoding="utf-8",
        )
        status, _, stderr = self.invoke(self.arguments())
        self.assertEqual(status, 2)
        self.assertIn("重复 JSON 字段", stderr)
        self.assertEqual(list(self.output_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
