import hashlib
from pathlib import Path
import tempfile
import unittest

from backend.data_pipeline.research.rrc25_country_outage.country_mapping import (
    CountryMappingError,
    canonical_json,
    freeze_as_country_mapping,
)


class CountryMappingSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, content: str, name: str = "as_entity.csv") -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_freezes_first_row_compatibility_and_surfaces_conflicts(self):
        path = self.write(
            "asn,as_country,as_name\n"
            "AS10,IR,first\n"
            "10,IR,duplicate\n"
            "10,US,conflict\n"
            "20,,missing\n"
            "30,IR,second\n"
            "bad,IR,invalid\n"
        )

        first = freeze_as_country_mapping(path)
        second = freeze_as_country_mapping(path)

        self.assertEqual(first, second)
        self.assertEqual(first["source_file_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(
            [(row["asn"], row["country_code"]) for row in first["rows"]],
            [(10, "IR"), (20, None), (30, "IR")],
        )
        self.assertEqual(first["summary"]["target_country_asn_count"], 2)
        self.assertEqual(first["summary"]["duplicate_same_count"], 1)
        self.assertEqual(first["summary"]["conflict_record_count"], 1)
        self.assertEqual(first["invalid"]["count"], 1)
        self.assertNotIn(str(self.root), canonical_json(first))

    def test_content_or_target_change_changes_stable_identity(self):
        path = self.write("asn,as_country\n10,IR\n")
        iran = freeze_as_country_mapping(path, target_country="IR")
        us = freeze_as_country_mapping(path, target_country="US")
        self.assertNotEqual(iran["snapshot_id"], us["snapshot_id"])
        self.assertNotEqual(
            iran["semantic_fingerprint_sha256"], us["semantic_fingerprint_sha256"]
        )

        path.write_text("asn,as_country\n10,IR\n20,IR\n", encoding="utf-8")
        changed = freeze_as_country_mapping(path)
        self.assertNotEqual(iran["snapshot_id"], changed["snapshot_id"])

    def test_rejects_symlink_missing_columns_and_non_utf8(self):
        target = self.write("asn,as_country\n10,IR\n", "target.csv")
        link = self.root / "link.csv"
        link.symlink_to(target)
        with self.assertRaisesRegex(CountryMappingError, "符号链接"):
            freeze_as_country_mapping(link)

        missing = self.write("asn,country\n10,IR\n", "missing.csv")
        with self.assertRaisesRegex(CountryMappingError, "asn 和 as_country"):
            freeze_as_country_mapping(missing)

        binary = self.root / "binary.csv"
        binary.write_bytes(b"asn,as_country\n10,\xff\n")
        with self.assertRaisesRegex(CountryMappingError, "UTF-8"):
            freeze_as_country_mapping(binary)

    def test_invalid_rows_are_not_silently_mapped(self):
        path = self.write(
            "asn,as_country\n"
            "0,IR\n"
            "4294967296,IR\n"
            "10,IRN\n"
            "20,ir\n"
        )
        snapshot = freeze_as_country_mapping(path)
        self.assertEqual(snapshot["invalid"]["count"], 3)
        self.assertEqual(snapshot["rows"][0]["country_code"], "IR")
        self.assertEqual(snapshot["rows"][0]["asn"], 20)


if __name__ == "__main__":
    unittest.main()
