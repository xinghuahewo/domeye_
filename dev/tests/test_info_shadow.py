import csv
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from backend.info_pipeline.catalog import AS_ENTITY_COLUMNS
from backend.info_pipeline.shadow import (
    SemanticDigest,
    ShadowAccumulator,
    _collect_as_file,
    _collect_grouped_rows,
)


class SemanticDigestTests(unittest.TestCase):
    def test_is_order_independent_but_preserves_multiplicity(self):
        first = SemanticDigest()
        second = SemanticDigest()
        for value in ({"key": "a"}, {"key": "b"}):
            first.add(value)
        for value in ({"key": "b"}, {"key": "a"}):
            second.add(value)

        self.assertEqual(first.signature(), second.signature())
        self.assertEqual(first.count, second.count)

        second.add({"key": "a"})
        self.assertNotEqual(first.signature(), second.signature())
        self.assertNotEqual(first.count, second.count)

    def test_integral_float_and_integer_have_same_value_semantics(self):
        file_digest = SemanticDigest()
        database_digest = SemanticDigest()
        file_digest.add({"latitude": 35.0, "longitude": 103.5})
        database_digest.add({"latitude": 35, "longitude": 103.5})
        self.assertEqual(file_digest.signature(), database_digest.signature())


class ContactRedactionTests(unittest.TestCase):
    def test_contact_plaintext_never_enters_section_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "as_entity.csv"
            row = dict.fromkeys(AS_ENTITY_COLUMNS, "")
            row.update(
                {
                    "asn": "64500",
                    "as_name": "EXAMPLE",
                    "as_country": "CN",
                    "admin_info": "绝不能出现在证据中的联系人原文",
                    "import_as": "[]",
                    "export_as": "[]",
                    "sibling_as": "[]",
                    "v4Upstream": "[]",
                    "v4Downstream": "[]",
                    "v4Peer": "[]",
                    "v6Upstream": "[]",
                    "v6Downstream": "[]",
                    "v6Peer": "[]",
                }
            )
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=AS_ENTITY_COLUMNS)
                writer.writeheader()
                writer.writerow(row)

            accumulator = ShadowAccumulator()
            _collect_as_file(root, accumulator)
            evidence = json.dumps(
                accumulator.results(),
                ensure_ascii=False,
                sort_keys=True,
            )

            self.assertNotIn("绝不能出现在证据中的联系人原文", evidence)
            self.assertNotIn("admin_info", evidence)
            self.assertEqual(
                accumulator.results()["asn_contact_redacted"]["status"],
                "fail",
            )


class _FakeDatabase:
    def __init__(self, rows):
        self.rows = rows

    @contextmanager
    def csv_rows(self, _query):
        yield iter(self.rows)


class GroupedDatabaseTests(unittest.TestCase):
    def test_keeps_empty_mapping_key_in_snapshot(self):
        database = _FakeDatabase(
            [
                ["1", "64500", "", "", "", "", ""],
                ["2", "64501", "1", "192.0.2.0/24", "192.0.2.0/24", "7", "valid"],
            ]
        )
        accumulator = ShadowAccumulator()
        _collect_grouped_rows(
            database,
            accumulator,
            "pfx2as_exact_and_snapshot",
            "SELECT fixture",
            kind="pfx2as",
        )

        result = accumulator.results()["pfx2as_exact_and_snapshot"]
        self.assertEqual(result["database_record_count"], 2)
        self.assertEqual(result["file_record_count"], 0)
        self.assertEqual(result["status"], "fail")


if __name__ == "__main__":
    unittest.main()
