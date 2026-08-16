import csv
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

from backend.info_pipeline import full_loader as full_loader_module
from backend.info_pipeline import loader as loader_module
from backend.info_pipeline import manifest as manifest_module
from backend.info_pipeline.catalog import (
    AS_ENTITY_COLUMNS,
    COUNTRY_COLUMNS,
    DATA_FILE_SPECS,
    FULL_PHASE_FILE_NAMES,
    PREFIX_COLUMNS,
    SPEC_BY_NAME,
)
from backend.info_pipeline.full_loader import (
    RecordQuarantine,
    load_full_files,
    _spool_full_file,
    _transform,
    _validate_spool_capacity,
)
from backend.info_pipeline.excel import (
    ExcelReadError,
    detect_excel_container,
    iter_first_sheet_values,
)
from backend.info_pipeline.loader import (
    DockerPsql,
    LoadError,
    _as_entity_payload,
    _prefix_payload,
    _spool_prefix,
)
from backend.info_pipeline.manifest import ManifestError, build_manifest, validate_manifest
from backend.info_pipeline.output import write_text_exclusive
from backend.info_pipeline.quality import parse_literal_list, probe_core_files
from backend.info_pipeline.stream_json import JsonStreamError, iter_top_level_object


def _write_csv(path: Path, header, rows, delimiter=",", encoding="utf-8"):
    with path.open("w", encoding=encoding, newline="") as stream:
        writer = csv.writer(stream, delimiter=delimiter)
        writer.writerow(header)
        writer.writerows(rows)


def _empty_row(header):
    return ["" for _ in header]


class InfoFixture:
    def __init__(self, root: Path):
        self.root = root
        for spec in DATA_FILE_SPECS:
            path = root / spec.name
            if spec.file_format == "json":
                path.write_text('{"1":{"nested":[1,true,null]}}\n', encoding="utf-8")
            elif spec.file_format == "line_text":
                path.write_text("example.com 192.0.2.1\n", encoding="utf-8")
            elif spec.file_format == "csv":
                header = list(spec.required_columns) or ["raw_column"]
                if spec.name == "important_as.csv":
                    header.append("label")
                    rows = [["64500", "重点网络"]]
                elif spec.name == "as_entity.csv":
                    row = dict.fromkeys(header, "")
                    row.update(
                        {
                            "asn": "64500",
                            "as_name": "EXAMPLE",
                            "as_country": "CN",
                            "as_country_cn": "中国",
                            "import_as": "['64501']",
                            "export_as": "[]",
                            "sibling_as": "['AS64502']",
                            "v4Upstream": "[]",
                            "v4Downstream": "[]",
                            "v4Peer": "['64503']",
                            "v6Upstream": "[]",
                            "v6Downstream": "[]",
                            "v6Peer": "[]",
                            "admin_info": "private contact",
                        }
                    )
                    rows = [[row[name] for name in header]]
                elif spec.name == "ip_bgp_entity.csv":
                    row = dict.fromkeys(header, "")
                    row.update(
                        {
                            "prefix": "192.0.2.1/24",
                            "name": "TEST-NET-1",
                            "domain": "['example.com']",
                            "domain_num": "1",
                            "domain_auth": "[]",
                            "domain_auth_num": "0",
                        }
                    )
                    rows = [[row[name] for name in header]]
                else:
                    rows = [_empty_row(header)]
                _write_csv(path, header, rows, spec.delimiter or ",")
            elif spec.file_format == "xlsx":
                workbook = Workbook()
                worksheet = workbook.active
                worksheet.append(list(COUNTRY_COLUMNS))
                worksheet.append(
                    [
                        "People's Republic of China",
                        "China",
                        "中国",
                        "CN",
                        "CHN",
                        "156",
                        "86",
                        "UTC+8",
                        35.0,
                        103.0,
                    ]
                )
                workbook.save(path)
                workbook.close()
            elif spec.file_format == "xls":
                path.write_bytes(b"test-xls-fixture")
        (root / "README.md").write_text("# fixture\n", encoding="utf-8")


def _fake_xls_inspector(path, spec):
    return list(spec.required_columns), 1, "xls-first-sheet-logical-rows-excluding-header"


class StreamJsonTests(unittest.TestCase):
    def test_streams_nested_values_across_small_chunks(self):
        payload = json.dumps(
            {"a": 1, '转义"键': {"items": [True, None, {"x": "}"}]}},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        rows = list(iter_top_level_object(io.StringIO(payload), chunk_size=8))
        self.assertEqual(rows[0], (1, "a", 1))
        self.assertEqual(rows[1][1], '转义"键')
        self.assertEqual(rows[1][2]["items"][2], {"x": "}"})

    def test_rejects_duplicate_keys_at_any_level(self):
        with self.assertRaises(JsonStreamError):
            list(iter_top_level_object(io.StringIO('{"a":1,"a":2}'), chunk_size=8))
        with self.assertRaises(JsonStreamError):
            list(
                iter_top_level_object(
                    io.StringIO('{"a":{"nested":1,"nested":2}}'),
                    chunk_size=8,
                )
            )

    def test_rejects_trailing_content(self):
        with self.assertRaises(JsonStreamError):
            list(iter_top_level_object(io.StringIO('{"a":1} []'), chunk_size=8))


class PrefixSpoolTests(unittest.TestCase):
    def test_flattens_domains_with_release_and_source_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ip_bgp_entity.csv"
            row = dict.fromkeys(PREFIX_COLUMNS, "")
            row.update(
                {
                    "prefix": "192.0.2.1/24",
                    "domain": "['example.com', 'www.example.com']",
                    "domain_num": "2",
                    "domain_auth": "['ns.example.com']",
                    "domain_auth_num": "1",
                }
            )
            _write_csv(
                path,
                list(PREFIX_COLUMNS),
                [[row[name] for name in PREFIX_COLUMNS]],
            )

            main, domains, logical_count, domain_count = _spool_prefix(
                path,
                17,
                23,
            )
            try:
                main.seek(0)
                main_rows = list(csv.reader(main, delimiter="\t"))
                domains.seek(0)
                domain_rows = list(csv.reader(domains, delimiter="\t"))
            finally:
                main.close()
                domains.close()

            self.assertEqual(logical_count, 1)
            self.assertEqual(domain_count, 3)
            self.assertEqual(len(main_rows), 1)
            self.assertNotIn("domains", json.loads(main_rows[0][3]))
            self.assertEqual(
                [item[:7] for item in domain_rows],
                [
                    ["17", "192.0.2.1/24", "example.com", "normal", "1", "23", "1"],
                    [
                        "17",
                        "192.0.2.1/24",
                        "www.example.com",
                        "normal",
                        "2",
                        "23",
                        "1",
                    ],
                    [
                        "17",
                        "192.0.2.1/24",
                        "ns.example.com",
                        "authoritative",
                        "1",
                        "23",
                        "1",
                    ],
                ],
            )
            self.assertTrue(all(len(item[7]) == 64 for item in domain_rows))

    def test_rejects_declared_domain_count_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ip_bgp_entity.csv"
            row = dict.fromkeys(PREFIX_COLUMNS, "")
            row.update(
                {
                    "prefix": "192.0.2.0/24",
                    "domain": "['example.com']",
                    "domain_num": "2",
                    "domain_auth": "[]",
                    "domain_auth_num": "0",
                }
            )
            _write_csv(
                path,
                list(PREFIX_COLUMNS),
                [[row[name] for name in PREFIX_COLUMNS]],
            )

            with self.assertRaisesRegex(LoadError, "声明/实际不一致"):
                _spool_prefix(path, 17, 23)


class ExcelContentDetectionTests(unittest.TestCase):
    def test_reads_ooxml_with_historical_xls_suffix_by_magic(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "legacy-name.xls"
            payload = io.BytesIO()
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.append(["prefix", "number", "host"])
            worksheet.append(["192.0.2.0/24", 256, "example"])
            workbook.save(payload)
            workbook.close()
            path.write_bytes(payload.getvalue())

            self.assertEqual(detect_excel_container(path), "ooxml")
            self.assertEqual(
                list(iter_first_sheet_values(path)),
                [
                    ("prefix", "number", "host"),
                    ("192.0.2.0/24", 256, "example"),
                ],
            )

    def test_rejects_unknown_excel_magic(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "not-an-excel.xls"
            path.write_bytes(b"not-an-excel")
            with self.assertRaises(ExcelReadError):
                list(iter_first_sheet_values(path))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        InfoFixture(self.root)
        self.xls_patch = mock.patch.object(
            manifest_module,
            "_inspect_xls",
            side_effect=_fake_xls_inspector,
        )
        self.xls_patch.start()

    def tearDown(self):
        self.xls_patch.stop()
        self.temporary.cleanup()

    def test_builds_reproducible_content_identity(self):
        first = build_manifest(
            self.root,
            source_release_label="20260724T000000Z-a",
            generated_at="2026-07-24T00:00:00Z",
        )
        second = build_manifest(
            self.root,
            source_release_label="20260724T000000Z-b",
            generated_at="2026-07-24T01:00:00Z",
        )
        self.assertEqual(first["file_count"], 24)
        self.assertEqual(first["content_id"], second["content_id"])
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
        validate_manifest(first)

    def test_content_change_changes_identity(self):
        first = build_manifest(
            self.root,
            source_release_label="20260724T000000Z",
        )
        with (self.root / "top_ip.txt").open("a", encoding="utf-8") as stream:
            stream.write("example.net 192.0.2.2\n")
        second = build_manifest(
            self.root,
            source_release_label="20260724T000000Z",
        )
        self.assertNotEqual(first["content_id"], second["content_id"])

    def test_manifest_accepts_bounded_large_csv_field(self):
        header = list(PREFIX_COLUMNS)
        row = dict.fromkeys(header, "")
        row.update(
            {
                "prefix": "192.0.2.0/24",
                "domain": "[]",
                "domain_num": "0",
                "domain_auth": "['" + "x" * (256 * 1024) + "']",
                "domain_auth_num": "1",
            }
        )
        _write_csv(
            self.root / "ip_bgp_entity.csv",
            header,
            [[row[name] for name in header]],
        )
        manifest = build_manifest(
            self.root,
            source_release_label="20260724T000000Z-large-field",
        )
        item = next(
            entry
            for entry in manifest["files"]
            if entry["name"] == "ip_bgp_entity.csv"
        )
        self.assertEqual(item["logical_record_count"], 1)
        self.assertIn("field-limit-67108864", item["count_method"])

    def test_manifest_reads_declared_gb18030_file_without_replacement(self):
        _write_csv(
            self.root / "ases_cn.csv",
            ["asn", "CN_Name"],
            [["64500", "示例网络"]],
            encoding="gb18030",
        )
        manifest = build_manifest(
            self.root,
            source_release_label="20260724T000000Z",
        )
        item = next(
            entry for entry in manifest["files"] if entry["name"] == "ases_cn.csv"
        )
        self.assertEqual(item["encoding"], "gb18030")
        self.assertEqual(item["logical_record_count"], 1)

    def test_validation_recomputes_header_hash(self):
        manifest = build_manifest(
            self.root,
            source_release_label="20260724T000000Z",
        )
        tampered = copy.deepcopy(manifest)
        tampered["files"][0]["header"][0] = "tampered"
        with self.assertRaises(ManifestError):
            validate_manifest(tampered)

    def test_rejects_unknown_file_and_symlink(self):
        (self.root / "unknown.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(ManifestError):
            build_manifest(self.root, source_release_label="release")
        (self.root / "unknown.txt").unlink()
        (self.root / "as_rank.json").unlink()
        (self.root / "as_rank.json").symlink_to(self.root / "as_dict.txt")
        with self.assertRaises(ManifestError):
            build_manifest(self.root, source_release_label="release")


class QualityAndTransformTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        InfoFixture(self.root)
        self.xls_patch = mock.patch.object(
            manifest_module,
            "_inspect_xls",
            side_effect=_fake_xls_inspector,
        )
        self.xls_patch.start()
        self.manifest = build_manifest(
            self.root,
            source_release_label="20260724T000000Z",
        )

    def tearDown(self):
        self.xls_patch.stop()
        self.temporary.cleanup()

    def test_core_probe_passes_and_discloses_noncanonical_prefix(self):
        report = probe_core_files(self.root, self.manifest)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["blocking_failure_count"], 0)
        self.assertEqual(
            report["files"]["ip_bgp_entity.csv"]["noncanonical_prefix_count"],
            1,
        )

    def test_core_probe_blocks_unsafe_literal(self):
        with (self.root / "as_entity.csv").open(encoding="utf-8") as stream:
            rows = list(csv.reader(stream))
        sibling_index = rows[0].index("sibling_as")
        rows[1][sibling_index] = "__import__('os').system('false')"
        _write_csv(self.root / "as_entity.csv", rows[0], rows[1:])
        changed_manifest = build_manifest(
            self.root,
            source_release_label="20260724T000000Z",
        )
        report = probe_core_files(self.root, changed_manifest)
        self.assertEqual(report["status"], "fail")
        self.assertIn(
            "core.as_entity.safe_literal_lists",
            report["blocking_failures"],
        )

    def test_transform_does_not_leak_contacts_into_as_attributes(self):
        with (self.root / "as_entity.csv").open(encoding="utf-8") as stream:
            rows = list(csv.reader(stream))
        header, values = rows[0], rows[1]
        payload = _as_entity_payload(dict(zip(header, values)))
        self.assertNotIn("admin_info", payload["attributes"])
        self.assertEqual(payload["contacts"][0]["contact_value"], "private contact")
        self.assertEqual(payload["relations"][0]["target_asn"], 64502)

    def test_prefix_transform_preserves_raw_and_canonical(self):
        with (self.root / "ip_bgp_entity.csv").open(encoding="utf-8") as stream:
            rows = list(csv.reader(stream))
        payload = _prefix_payload(dict(zip(rows[0], rows[1])))
        self.assertEqual(payload["prefix_raw"], "192.0.2.1/24")
        self.assertEqual(payload["prefix_cidr"], "192.0.2.0/24")
        self.assertEqual(payload["canonical_status"], "noncanonical")
        self.assertEqual(payload["domains"][0]["domain_key_raw"], "example.com")

    def test_literal_parser_never_executes_code(self):
        with self.assertRaises(ValueError):
            parse_literal_list(
                "__import__('os').system('false')",
                field_name="sibling_as",
            )

    def test_s2_contract_covers_exactly_the_twenty_non_core_files(self):
        self.assertEqual(len(FULL_PHASE_FILE_NAMES), 20)
        self.assertEqual(
            set(FULL_PHASE_FILE_NAMES),
            {
                spec.name
                for spec in DATA_FILE_SPECS
                if spec.name
                not in {
                    "as_entity.csv",
                    "important_as.csv",
                    "ip_bgp_entity.csv",
                    "country.xlsx",
                }
            },
        )

    def test_s2_domain_keeps_raw_key_and_quarantines_empty_key(self):
        record = _transform(
            "website_entity.csv",
            {
                "url": " Example.COM. ",
                "title": "示例",
                "industry": "test",
                "ip": "192.0.2.1",
                "ip_prefix": "192.0.2.0/24",
                "auth_ip": "",
            },
        )
        self.assertEqual(record.natural_key, " Example.COM. ")
        self.assertEqual(record.payload["normalized_key"], " Example.COM. ")
        self.assertEqual(record.payload["addresses"][0]["ip_value"], "192.0.2.1")
        with self.assertRaises(RecordQuarantine) as context:
            _transform(
                "domain_cn.csv",
                {
                    "url": " ",
                    "title": "",
                    "industry": "",
                    "ip": "",
                    "ip_prefix": "",
                    "auth_ip": "",
                },
            )
        self.assertEqual(context.exception.reason_code, "empty_domain_key")

    def test_s2_pfx2as_preserves_source_value_and_invalid_prefix(self):
        record = _transform(
            "pfx2as_dict.txt",
            {
                "key": "64500",
                "value": {
                    "192.0.2.0/24": 7,
                    "not-a-prefix": {"unexplained": True},
                },
            },
        )
        self.assertEqual(record.payload["asn"], 64500)
        self.assertEqual(record.payload["prefixes"][0]["source_value"], 7)
        self.assertEqual(
            record.payload["prefixes"][1]["quality_status"],
            "invalid_prefix",
        )
        self.assertEqual(
            record.payload["prefixes"][1]["source_value"],
            {"unexplained": True},
        )

    def test_s2_relation_detects_peer_alias_drift(self):
        record = _transform(
            "as_rel_dict.txt",
            {
                "key": "64500",
                "value": {
                    "provider": ["64501"],
                    "customer": ["64502"],
                    "peer": ["64503"],
                    "peers": ["64504"],
                },
            },
        )
        self.assertIn(
            "peer_peers_mismatch",
            {item["code"] for item in record.payload["quality_flags"]},
        )
        mismatch = next(
            item
            for item in record.payload["quality_flags"]
            if item["code"] == "peer_peers_mismatch"
        )
        self.assertTrue(mismatch["blocking"])

    def test_s2_preflight_rejects_insufficient_spool_space(self):
        with mock.patch.object(
            full_loader_module.shutil,
            "disk_usage",
            return_value=mock.Mock(free=1),
        ):
            with self.assertRaises(LoadError):
                _validate_spool_capacity(self.manifest)

    def test_s2_loaded_not_consumed_and_legacy_sources_stay_inactive(self):
        important_prefix = _transform(
            "ipv4_all_prefix.xls",
            {"prefix": "192.0.2.0/24", "number": "1", "host": "example"},
        )
        old_relation = _transform(
            "as_rel_dict_old.txt",
            {"key": "64500", "value": {"peers": ["64501"]}},
        )
        current_relation = _transform(
            "as_rel_dict.txt",
            {"key": "64500", "value": {"peers": ["64501"]}},
        )
        self.assertFalse(important_prefix.payload["source_active"])
        self.assertFalse(old_relation.payload["source_active"])
        self.assertTrue(current_relation.payload["source_active"])

    def test_s2_rejects_missing_s1_before_schema_mutation(self):
        class MissingS1Database:
            schema_mutated = False

            def execute(self, _sql, *, capture=False):
                return "" if capture else ""

            def execute_file(self, _path):
                self.schema_mutated = True

        database = MissingS1Database()
        schema_sql = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "database"
            / "sql"
            / "info-schema-v2.sql"
        )
        with mock.patch.object(
            full_loader_module.shutil,
            "disk_usage",
            return_value=mock.Mock(free=10**15),
        ):
            with self.assertRaises(LoadError):
                load_full_files(
                    self.root,
                    self.manifest,
                    database,
                    schema_sql=schema_sql,
                )
        self.assertFalse(database.schema_mutated)

    def test_s2_line_spool_accounts_for_non_empty_logical_records(self):
        path = self.root / "top_ip.txt"
        path.write_text(
            "\nexample.com 192.0.2.1\nmalformed-only-domain\n",
            encoding="utf-8",
        )
        stream, logical, accepted, quarantined = _spool_full_file(
            path,
            SPEC_BY_NAME["top_ip.txt"],
        )
        try:
            self.assertEqual((logical, accepted, quarantined), (2, 2, 0))
            stream.seek(0)
            rows = list(csv.reader(stream, delimiter="\t"))
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                json.loads(rows[1][6])["quality_status"],
                "incomplete",
            )
        finally:
            stream.close()


class SqlContractTests(unittest.TestCase):
    def test_docker_psql_rejects_unmarked_database_before_sql(self):
        inspection = mock.Mock(
            returncode=0,
            stdout="production\n",
            stderr="",
        )
        with mock.patch.object(
            loader_module.subprocess,
            "run",
            return_value=inspection,
        ) as run:
            with self.assertRaisesRegex(LoadError, "offline-candidate"):
                DockerPsql("database", "postgres", "domeye")
        run.assert_called_once()

    def test_schema_prevents_partial_activation_and_separates_contacts(self):
        sql = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "database"
            / "sql"
            / "info-schema-v1.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("<> 24", sql)
        self.assertIn("REVOKE ALL ON info.as_contact FROM PUBLIC", sql)
        self.assertIn("PARTITION BY LIST (release_sk)", sql)
        self.assertNotIn("eval(", sql)

    def test_info_json_contracts_are_valid_json(self):
        contract_root = Path(__file__).resolve().parents[2] / "contracts" / "info"
        for path in sorted(contract_root.glob("*.schema.json")):
            with self.subTest(path=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_s2_schema_has_full_traceability_and_business_tables(self):
        sql = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "database"
            / "sql"
            / "info-schema-v2.sql"
        ).read_text(encoding="utf-8")
        for table in (
            "source_record",
            "mapping_record",
            "domain_record",
            "domain_address",
            "as_prefix_history",
            "important_prefix",
            "important_domain",
            "private_as_location",
            "route_triplet_baseline",
            "dns_observation",
            "as_rank",
            "organization",
            "organization_as",
            "organization_prefix",
            "legacy_record",
        ):
            with self.subTest(table=table):
                self.assertIn(f"info.{table}", sql)
        self.assertIn("implementation_scope = 'all_24_files'", sql)
        self.assertIn("REVOKE ALL ON info.source_record FROM PUBLIC", sql)
        self.assertIn("REVOKE ALL ON info.legacy_record FROM PUBLIC", sql)

        reader_sql = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "database"
            / "sql"
            / "create-reader.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("'source_record', 'legacy_record'", reader_sql)
        self.assertIn(
            "'^(source_record|legacy_record)_r[0-9]+$'",
            reader_sql,
        )

        integrity_sql = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "database"
            / "sql"
            / "validate-integrity.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("scope_value IN ('core_four_files', 'all_24_files')", integrity_sql)
        self.assertIn("'source_record', 'static_info_restricted'", integrity_sql)


class EvidenceOutputTests(unittest.TestCase):
    def test_exclusive_output_refuses_overwrite_and_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "result.json"
            write_text_exclusive(evidence, '{"status":"pass"}\n')
            self.assertEqual(evidence.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                write_text_exclusive(evidence, '{"status":"changed"}\n')
            self.assertEqual(
                evidence.read_text(encoding="utf-8"),
                '{"status":"pass"}\n',
            )

            target = root / "target.json"
            target.write_text("untouched\n", encoding="utf-8")
            linked = root / "linked.json"
            linked.symlink_to(target)
            with self.assertRaises(OSError):
                write_text_exclusive(linked, "changed\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "untouched\n")


if __name__ == "__main__":
    unittest.main()
