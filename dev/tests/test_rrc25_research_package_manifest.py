import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from backend.data_pipeline.research.rrc25_country_outage.package_manifest import (
    ResearchPackageError,
    build_package_manifest,
    publish_package_metadata,
    verify_package_directory,
    verify_published_package,
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
        run_id="research_run_v1_" + "a" * 24,
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


class ResearchPackageManifestTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_build_publish_and_verify_closed_package(self):
        sample = b'{"sample":1}\n'
        report = "中文报告\n".encode("utf-8")
        (self.root / "samples.jsonl").write_bytes(sample)
        (self.root / "report.zh.md").write_bytes(report)
        manifest = _manifest(
            [
                _ref("samples.jsonl", sample),
                _ref("report.zh.md", report, "report-zh"),
            ]
        )

        self.assertEqual(verify_package_directory(self.root, manifest), manifest)
        publish_package_metadata(self.root, manifest)
        verified = verify_published_package(self.root)

        self.assertEqual(verified["semantic_fingerprint_sha256"], manifest["semantic_fingerprint_sha256"])
        self.assertTrue((self.root / "SHA256SUMS").read_text().endswith("\n"))
        with self.assertRaises(FileExistsError):
            publish_package_metadata(self.root, manifest)

    def test_mutation_extra_file_and_symlink_fail_closed(self):
        payload = b"one"
        target = self.root / "samples.jsonl"
        target.write_bytes(payload)
        manifest = _manifest([_ref("samples.jsonl", payload)])
        target.write_bytes(b"two")
        with self.assertRaisesRegex(ResearchPackageError, "哈希或大小"):
            verify_package_directory(self.root, manifest)

        target.write_bytes(payload)
        (self.root / "extra").write_text("x")
        with self.assertRaisesRegex(ResearchPackageError, "不闭合"):
            verify_package_directory(self.root, manifest)

        (self.root / "extra").unlink()
        target.unlink()
        target.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(ResearchPackageError, "普通文件"):
            verify_package_directory(self.root, manifest)

    def test_semantic_fingerprint_is_order_and_runtime_independent(self):
        one = _ref("b.json", b"b", "waves")
        two = _ref("a.json", b"a", "episodes")
        first = _manifest([one, two])
        second = _manifest([two, one])

        self.assertEqual(first, second)
        self.assertTrue(first["runtime_metadata_excluded_from_semantic_fingerprint"])

    def test_tampered_manifest_identity_is_rejected(self):
        payload = b"x"
        (self.root / "samples.jsonl").write_bytes(payload)
        manifest = _manifest([_ref("samples.jsonl", payload)])
        tampered = json.loads(json.dumps(manifest))
        tampered["release_id"] = "rrc25_iran_v1_" + "f" * 24

        with self.assertRaisesRegex(ResearchPackageError, "内容寻址"):
            verify_package_directory(self.root, tampered)

    def test_published_metadata_symlink_is_rejected(self):
        payload = b"x"
        (self.root / "samples.jsonl").write_bytes(payload)
        manifest = _manifest([_ref("samples.jsonl", payload)])
        publish_package_metadata(self.root, manifest)

        real_manifest = self.root / "real-manifest.json"
        (self.root / "package-manifest.json").replace(real_manifest)
        (self.root / "package-manifest.json").symlink_to(real_manifest)
        with self.assertRaisesRegex(ResearchPackageError, "非符号链接普通文件"):
            verify_published_package(self.root)


if __name__ == "__main__":
    unittest.main()
