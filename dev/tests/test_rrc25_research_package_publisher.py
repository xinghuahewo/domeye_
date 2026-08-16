import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest

from backend.data_pipeline.research.rrc25_country_outage.package_manifest import (
    ResearchPackageError,
    build_package_manifest,
)
from backend.data_pipeline.research.rrc25_country_outage.package_publisher import (
    publish_research_package,
)


def _ref(path, payload, kind="samples", records=1):
    return {
        "kind": kind,
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "record_count": records,
    }


def _manifest(contents):
    return build_package_manifest(
        run_id="research_run_v1_" + "b" * 24,
        study_id="iran-rrc25-country-outage-202602-v1",
        incident_ref="country_outage/legacy",
        execution_mode="bounded_pilot",
        acceptance_state="not_accepted",
        bindings={
            "profile_sha256": "1" * 64,
            "selection_sha256": "2" * 64,
            "mapping_sha256": "3" * 64,
            "code_sha256": "4" * 64,
        },
        contents=contents,
    )


class ResearchPackagePublisherTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_publishes_nested_closed_read_only_package(self):
        samples = b'{"slot":"2026-02-28T08:10:00Z"}\n'
        report = "# 伊朗事件研究报告\n".encode("utf-8")
        contents = {
            "derived/samples.jsonl": samples,
            "report/report.zh.md": report,
        }
        manifest = _manifest(
            [
                _ref("derived/samples.jsonl", samples),
                _ref("report/report.zh.md", report, kind="report-zh"),
            ]
        )

        verified = publish_research_package(self.root, contents, manifest)

        self.assertEqual(verified, manifest)
        for relative in (*contents, "package-manifest.json", "SHA256SUMS"):
            path = self.root / relative
            self.assertTrue(path.is_file())
            self.assertFalse(path.is_symlink())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o440)
        self.assertNotIn(str(self.root), (self.root / "package-manifest.json").read_text())

    def test_repeated_publish_never_overwrites(self):
        payload = b"first"
        contents = {"samples.jsonl": payload}
        manifest = _manifest([_ref("samples.jsonl", payload)])
        publish_research_package(self.root, contents, manifest)
        original = (self.root / "samples.jsonl").read_bytes()

        with self.assertRaises(FileExistsError):
            publish_research_package(self.root, contents, manifest)

        self.assertEqual((self.root / "samples.jsonl").read_bytes(), original)

    def test_extra_file_or_symlink_in_output_is_rejected(self):
        payload = b"x"
        contents = {"samples.jsonl": payload}
        manifest = _manifest([_ref("samples.jsonl", payload)])
        (self.root / "unexpected.txt").write_bytes(b"do-not-touch")
        with self.assertRaises(FileExistsError):
            publish_research_package(self.root, contents, manifest)
        self.assertEqual((self.root / "unexpected.txt").read_bytes(), b"do-not-touch")
        self.assertFalse((self.root / "samples.jsonl").exists())

        (self.root / "unexpected.txt").unlink()
        (self.root / "trap").symlink_to(self.root / "missing")
        with self.assertRaisesRegex(ResearchPackageError, "符号链接"):
            publish_research_package(self.root, contents, manifest)
        self.assertTrue((self.root / "trap").is_symlink())

    def test_manifest_mismatch_is_rejected_before_writing(self):
        expected = b"expected"
        manifest = _manifest([_ref("samples.jsonl", expected)])

        with self.assertRaisesRegex(ResearchPackageError, "大小|哈希"):
            publish_research_package(
                self.root,
                {"samples.jsonl": b"different"},
                manifest,
            )

        self.assertEqual(list(self.root.iterdir()), [])

    def test_symlink_output_root_is_rejected(self):
        payload = b"x"
        manifest = _manifest([_ref("samples.jsonl", payload)])
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)

        with self.assertRaisesRegex(ResearchPackageError, "非符号链接目录"):
            publish_research_package(link, {"samples.jsonl": payload}, manifest)

        self.assertEqual(list(real.iterdir()), [])

    def test_unsafe_manifest_path_and_file_directory_collision_are_rejected(self):
        payload = b"x"
        manifest = _manifest([_ref("safe.json", payload)])
        unsafe = dict(manifest)
        unsafe["contents"] = [dict(manifest["contents"][0], path="../escape")]
        with self.assertRaisesRegex(ResearchPackageError, "安全相对路径"):
            publish_research_package(self.root, {"../escape": payload}, unsafe)

        first = b"file"
        second = b"nested"
        collision_manifest = _manifest(
            [_ref("same", first), _ref("same/child", second)]
        )
        with self.assertRaisesRegex(ResearchPackageError, "文件/目录冲突"):
            publish_research_package(
                self.root,
                {"same": first, "same/child": second},
                collision_manifest,
            )
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
