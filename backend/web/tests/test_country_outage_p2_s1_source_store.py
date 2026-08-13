from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from backend.services.country_outage_p2_s1_source_store import (
    CountryOutageP2S1SourceStore,
    SourcePopulationUnavailable,
    SourceStoreIntegrityError,
    canonical_json,
    digest_json,
)


ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "contracts/data/country-outage-p2-s1"
FIXTURE_STORE = CONTRACT_ROOT / "test-fixture/source-store"


class CountryOutageP2S1SourceStoreTest(unittest.TestCase):
    def copied_store(self):
        temporary = tempfile.TemporaryDirectory()
        target = Path(temporary.name) / "store"
        shutil.copytree(FIXTURE_STORE, target)
        return temporary, target

    def rewrite_manifest(self, target, mutate):
        path = target / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        mutate(manifest)
        manifest.pop("content_sha256", None)
        manifest.pop("store_id", None)
        manifest["store_id"] = "country_outage_p2_s1_source_store_v1_" + digest_json(manifest)
        manifest["content_sha256"] = digest_json(manifest)
        path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")

    def test_fixture_verifies_and_loads_only_complete_atomic_population(self):
        store = CountryOutageP2S1SourceStore(FIXTURE_STORE, contract_root=CONTRACT_ROOT)
        manifest = store.verify()
        self.assertEqual(len(manifest["population_manifests"]), 6)
        self.assertEqual(len(store.load_population("new_prefix_state_rows")), 2)
        self.assertEqual(
            store.load_index("new_prefix_state_rows")["population_id"],
            "new_prefix_state_rows",
        )
        self.assertFalse(hasattr(store, "query"))
        self.assertFalse(hasattr(store, "filter"))
        self.assertFalse(hasattr(store, "join"))

    def test_row_file_tampering_is_rejected(self):
        temporary, target = self.copied_store()
        try:
            path = target / "populations/prefix_state_rows.jsonl"
            path.write_bytes(path.read_bytes() + b" ")
            with self.assertRaisesRegex(SourceStoreIntegrityError, "size mismatch"):
                CountryOutageP2S1SourceStore(target, contract_root=CONTRACT_ROOT).verify()
        finally:
            temporary.cleanup()

    def test_ghost_row_cannot_be_resigned_only_at_manifest_layer(self):
        temporary, target = self.copied_store()
        try:
            population = "prefix_state_rows"
            row_path = target / f"populations/{population}.jsonl"
            rows = [json.loads(line) for line in row_path.read_text(encoding="utf-8").splitlines()]
            rows[0]["visible_direction_count"] = 1
            rows[0]["invisible_direction_count"] = 1
            semantic = dict(rows[0]); semantic.pop("row_digest")
            rows[0]["row_digest"] = digest_json(semantic)
            raw = b"".join((canonical_json(row) + "\n").encode() for row in rows)
            row_path.write_bytes(raw)
            def mutate(manifest):
                entry = next(item for item in manifest["population_manifests"] if item["population_id"] == population)
                entry["row_file"]["sha256"] = hashlib.sha256(raw).hexdigest()
                entry["row_file"]["size_bytes"] = len(raw)
            self.rewrite_manifest(target, mutate)
            with self.assertRaisesRegex(SourceStoreIntegrityError, "index members mismatch"):
                CountryOutageP2S1SourceStore(target, contract_root=CONTRACT_ROOT).verify()
        finally:
            temporary.cleanup()

    def test_index_tamper_is_rejected_even_when_file_ref_is_resigned(self):
        temporary, target = self.copied_store()
        try:
            population = "materialized_route_state_rows_at_exact_time"
            index_path = target / f"indexes/{population}.index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["secondary_indexes"]["path_asn_membership"]["members_by_asn"].pop("49666")
            index.pop("content_sha256")
            index["content_sha256"] = digest_json(index)
            raw = (canonical_json(index) + "\n").encode()
            index_path.write_bytes(raw)
            def mutate(manifest):
                entry = next(item for item in manifest["population_manifests"] if item["population_id"] == population)
                entry["index_file"]["sha256"] = hashlib.sha256(raw).hexdigest()
                entry["index_file"]["size_bytes"] = len(raw)
            self.rewrite_manifest(target, mutate)
            with self.assertRaisesRegex(SourceStoreIntegrityError, "receipt completeness mismatch"):
                CountryOutageP2S1SourceStore(target, contract_root=CONTRACT_ROOT).verify()
        finally:
            temporary.cleanup()

    def test_window_path_membership_index_is_recomputed_before_exposure(self):
        temporary, target = self.copied_store()
        try:
            population = "window_path_association_evidence_rows"
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            population_manifest = next(
                item
                for item in manifest["population_manifests"]
                if item["population_id"] == population
            )
            index_path = target / f"indexes/{population}.index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["secondary_indexes"]["path_asn_membership"]["members_by_asn"].pop("49666")
            index.pop("content_sha256")
            index["content_sha256"] = digest_json(index)
            raw = (canonical_json(index) + "\n").encode()
            index_path.write_bytes(raw)

            receipt_path = target / population_manifest["materialization_receipt_ref"]
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["index_digest"] = index["content_sha256"]
            receipt.pop("content_sha256")
            receipt_semantic = dict(receipt)
            receipt_semantic.pop("receipt_id")
            receipt["receipt_id"] = "p2s1_materialization_receipt_v1_" + digest_json(receipt_semantic)
            receipt["content_sha256"] = digest_json(receipt)
            new_receipt_digest = receipt["content_sha256"]
            receipt_raw = (canonical_json(receipt) + "\n").encode()
            new_receipt_path = target / f"receipts/{new_receipt_digest}.json"
            receipt_path.rename(new_receipt_path)
            new_receipt_path.write_bytes(receipt_raw)

            def mutate(manifest):
                entry = next(item for item in manifest["population_manifests"] if item["population_id"] == population)
                entry["index_file"]["sha256"] = hashlib.sha256(raw).hexdigest()
                entry["index_file"]["size_bytes"] = len(raw)
                entry["materialization_receipt_digest"] = new_receipt_digest
                entry["materialization_receipt_ref"] = f"receipts/{new_receipt_digest}.json"
            self.rewrite_manifest(target, mutate)
            with self.assertRaisesRegex(SourceStoreIntegrityError, "window path membership index content mismatch"):
                CountryOutageP2S1SourceStore(target, contract_root=CONTRACT_ROOT).verify()
        finally:
            temporary.cleanup()

    def test_fully_resigned_window_path_with_wrong_origin_tail_is_rejected(self):
        """即使攻击者重签所有外层摘要，错误 AS_PATH 语义仍必须 fail closed。"""

        temporary, target = self.copied_store()
        try:
            population = "window_path_association_evidence_rows"
            manifest_path = target / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            population_manifest = next(
                item
                for item in manifest["population_manifests"]
                if item["population_id"] == population
            )

            row_path = target / population_manifest["row_file"]["path"]
            rows = [json.loads(line) for line in row_path.read_text(encoding="utf-8").splitlines()]
            rows[0]["path_segments"] = [
                {"segment_type": "as_sequence", "asns": [3257, 58224, 49666]}
            ]
            rows[0]["path_digest"] = digest_json(rows[0]["path_segments"])
            row_semantic = dict(rows[0])
            row_semantic.pop("row_digest")
            rows[0]["row_digest"] = digest_json(row_semantic)
            row_raw = b"".join((canonical_json(row) + "\n").encode("utf-8") for row in rows)
            row_path.write_bytes(row_raw)

            index_path = target / population_manifest["index_file"]["path"]
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["members"][0]["row_digest"] = rows[0]["row_digest"]
            index.pop("content_sha256")
            index["content_sha256"] = digest_json(index)
            index_raw = (canonical_json(index) + "\n").encode("utf-8")
            index_path.write_bytes(index_raw)

            old_receipt_path = target / population_manifest["materialization_receipt_ref"]
            receipt = json.loads(old_receipt_path.read_text(encoding="utf-8"))
            receipt["row_file_sha256"] = hashlib.sha256(row_raw).hexdigest()
            receipt["index_digest"] = index["content_sha256"]
            receipt.pop("content_sha256")
            receipt_semantic = dict(receipt)
            receipt_semantic.pop("receipt_id")
            receipt["receipt_id"] = "p2s1_materialization_receipt_v1_" + digest_json(receipt_semantic)
            receipt["content_sha256"] = digest_json(receipt)
            receipt_raw = (canonical_json(receipt) + "\n").encode("utf-8")
            new_receipt_path = target / f"receipts/{receipt['content_sha256']}.json"
            old_receipt_path.rename(new_receipt_path)
            new_receipt_path.write_bytes(receipt_raw)

            def mutate(current_manifest):
                entry = next(
                    item
                    for item in current_manifest["population_manifests"]
                    if item["population_id"] == population
                )
                entry["row_file"]["sha256"] = hashlib.sha256(row_raw).hexdigest()
                entry["row_file"]["size_bytes"] = len(row_raw)
                entry["index_file"]["sha256"] = hashlib.sha256(index_raw).hexdigest()
                entry["index_file"]["size_bytes"] = len(index_raw)
                entry["materialization_receipt_digest"] = receipt["content_sha256"]
                entry["materialization_receipt_ref"] = (
                    f"receipts/{receipt['content_sha256']}.json"
                )

            self.rewrite_manifest(target, mutate)
            with self.assertRaisesRegex(
                SourceStoreIntegrityError,
                "window origin tail or anchor-before invariant mismatch",
            ):
                CountryOutageP2S1SourceStore(target, contract_root=CONTRACT_ROOT).verify()
        finally:
            temporary.cleanup()

    def test_path_traversal_is_rejected(self):
        temporary, target = self.copied_store()
        try:
            def mutate(manifest):
                manifest["population_manifests"][0]["row_file"]["path"] = "../outside.jsonl"
            self.rewrite_manifest(target, mutate)
            with self.assertRaisesRegex(SourceStoreIntegrityError, "escapes store"):
                CountryOutageP2S1SourceStore(target, contract_root=CONTRACT_ROOT).verify()
        finally:
            temporary.cleanup()

    def test_store_root_and_nested_file_symlinks_are_rejected(self):
        temporary, target = self.copied_store()
        try:
            linked_root = Path(temporary.name) / "linked-store"
            linked_root.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(SourceStoreIntegrityError, "unsafe directory"):
                CountryOutageP2S1SourceStore(linked_root, contract_root=CONTRACT_ROOT)

            row_path = target / "populations/prefix_state_rows.jsonl"
            actual_path = target / "populations/prefix_state_rows.actual.jsonl"
            row_path.rename(actual_path)
            row_path.symlink_to(actual_path.name)
            with self.assertRaisesRegex(SourceStoreIntegrityError, "symlink forbidden"):
                CountryOutageP2S1SourceStore(target, contract_root=CONTRACT_ROOT).verify()
        finally:
            temporary.cleanup()

    def test_blocked_source_semantics_fail_closed_with_machine_code(self):
        temporary, target = self.copied_store()
        try:
            def mutate(manifest):
                entry = manifest["population_manifests"][3]
                entry["readiness"] = "blocked_source_semantics"
                entry["blocking_codes"] = ["FIRST_OBSERVED_EXACT_VIEW_INCOMPLETE"]
            self.rewrite_manifest(target, mutate)
            with self.assertRaisesRegex(SourcePopulationUnavailable, "FIRST_OBSERVED"):
                CountryOutageP2S1SourceStore(target, contract_root=CONTRACT_ROOT).verify()
        finally:
            temporary.cleanup()

    def test_schema_digest_drift_is_rejected(self):
        temporary, target = self.copied_store()
        contract_tmp = tempfile.TemporaryDirectory()
        try:
            contracts = Path(contract_tmp.name) / "contracts"
            shutil.copytree(CONTRACT_ROOT, contracts)
            schema = contracts / "prefix-state-row.schema.json"
            schema.write_bytes(schema.read_bytes() + b"\n")
            with self.assertRaisesRegex(SourceStoreIntegrityError, "schema digest mismatch"):
                CountryOutageP2S1SourceStore(target, contract_root=contracts).verify()
        finally:
            temporary.cleanup()
            contract_tmp.cleanup()

    def test_manifest_duplicate_key_and_noncanonical_encoding_are_rejected(self):
        temporary, target = self.copied_store()
        try:
            manifest = (target / "manifest.json").read_text(encoding="utf-8")
            (target / "manifest.json").write_text(manifest.replace('{"content_sha256":', '{"content_sha256":"' + '0' * 64 + '","content_sha256":', 1), encoding="utf-8")
            with self.assertRaisesRegex(SourceStoreIntegrityError, "duplicate JSON key"):
                CountryOutageP2S1SourceStore(target, contract_root=CONTRACT_ROOT).verify()
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
