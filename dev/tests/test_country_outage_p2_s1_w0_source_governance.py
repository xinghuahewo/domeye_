from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_country_outage_p2_s1_source_views import (
    POPULATION_IDS,
    SourceMaterializationError,
    build_source_store,
    canonical_json,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "contracts/data/country-outage-p2-s1"
FIXTURE_INPUT = CONTRACT_ROOT / "test-fixture/authoritative-source-bundle.json"
FIXTURE_STORE = CONTRACT_ROOT / "test-fixture/source-store"


class CountryOutageP2S1W0SourceGovernanceTest(unittest.TestCase):
    def payload(self):
        return json.loads(FIXTURE_INPUT.read_text(encoding="utf-8"))

    def build(self, payload):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        input_path = root / "input.json"
        input_path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
        output = root / "store"
        manifest = build_source_store(input_path, output, contract_root=CONTRACT_ROOT)
        return temporary, output, manifest

    def assert_build_rejected(self, payload, text):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.json"
            input_path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(SourceMaterializationError, text):
                build_source_store(input_path, root / "store", contract_root=CONTRACT_ROOT)

    def test_frozen_fixture_contains_six_ready_atomic_populations(self):
        manifest = json.loads((FIXTURE_STORE / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "country_outage_p2_s1_source_store_manifest_v1")
        self.assertEqual(tuple(item["population_id"] for item in manifest["population_manifests"]), POPULATION_IDS)
        self.assertTrue(all(item["readiness"] == "ready" for item in manifest["population_manifests"]))
        self.assertTrue(all(item["blocking_codes"] == [] for item in manifest["population_manifests"]))
        self.assertEqual(len({item["schema_ref"] for item in manifest["population_manifests"]}), 6)

    def test_build_is_byte_deterministic(self):
        first_tmp, first, first_manifest = self.build(self.payload())
        second_tmp, second, second_manifest = self.build(self.payload())
        try:
            self.assertEqual(first_manifest, second_manifest)
            first_files = {path.relative_to(first): path.read_bytes() for path in first.rglob("*") if path.is_file()}
            second_files = {path.relative_to(second): path.read_bytes() for path in second.rglob("*") if path.is_file()}
            self.assertEqual(first_files, second_files)
        finally:
            first_tmp.cleanup()
            second_tmp.cleanup()

    def test_new_prefix_profile_freezes_first_visible_denominator_and_dense_track(self):
        temporary, output, _ = self.build(self.payload())
        try:
            rows = [json.loads(line) for line in (output / "populations/new_prefix_state_rows.jsonl").read_text(encoding="utf-8").splitlines()]
            rows.sort(key=lambda item: item["state_point_utc"])
            self.assertEqual([item["classification"] for item in rows], ["normal", "partial"])
            self.assertEqual(rows[0]["expected_peer_asn_direction_ids"], ["rrc25:64500", "rrc25:64501"])
            self.assertEqual(rows[0]["projection_profile_id"], "PROFILE-NEW-PREFIX-FIXED-FIRST-OBSERVED-DIRECTIONS-1.0.0")
        finally:
            temporary.cleanup()

    def test_missing_dense_new_prefix_slot_is_rejected(self):
        payload = self.payload()
        payload["new_prefix_projection_inputs"][0]["state_points"].pop()
        self.assert_build_rejected(payload, "not dense")

    def test_incomplete_first_observed_view_is_rejected(self):
        payload = self.payload()
        payload["new_prefix_projection_inputs"][0]["first_observed_view_complete"] = False
        self.assert_build_rejected(payload, "first-observed exact view")

    def test_first_denominator_cannot_include_non_visible_direction(self):
        payload = self.payload()
        payload["new_prefix_projection_inputs"][0]["state_points"][0]["direction_states"]["rrc25:64501"] = "unknown"
        self.assert_build_rejected(payload, "exactly first-observed visible")

    def test_exact_route_state_requires_no_future_and_completeness_proof(self):
        for key in ("no_future_read_verified", "source_complete"):
            payload = self.payload()
            payload["exact_route_state_views"][0][key] = False
            self.assert_build_rejected(payload, "completeness/no-future")

    def test_exact_route_state_rejects_future_row(self):
        payload = self.payload()
        payload["exact_route_state_views"][0]["rows"][0]["last_update_utc"] = "2026-02-27T00:05:00Z"
        self.assert_build_rejected(payload, "future update")

    def test_window_path_requires_known_origin_tail_after_anchor(self):
        payload = self.payload()
        segments = [{"segment_type": "as_sequence", "asns": [3257, 49666, 58224, 48159]}]
        payload["window_path_associations"][0]["path_segments"] = segments
        from tools.build_country_outage_p2_s1_source_views import digest_json
        payload["window_path_associations"][0]["path_digest"] = digest_json(segments)
        self.assert_build_rejected(payload, "tail and strictly after")

    def test_window_path_rejects_forged_anchor_population_digest(self):
        payload = self.payload()
        payload["eligible_anchor_population"]["eligible_anchor_asns"] = [49666, 58224]
        self.assert_build_rejected(payload, "population digest mismatch")

    def test_path_digest_and_profile_are_not_query_time_guesses(self):
        payload = self.payload()
        payload["exact_route_state_views"][0]["rows"][0]["path_digest"] = "0" * 64
        self.assert_build_rejected(payload, "path digest mismatch")

    def test_cross_publication_source_ref_is_rejected(self):
        payload = self.payload()
        payload["source_refs"][0]["publication_id"] = "another-publication"
        self.assert_build_rejected(payload, "publication mismatch")

    def test_duplicate_json_key_is_rejected_before_projection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "input.json"
            path.write_text('{"schema_version":"a","schema_version":"b"}\n', encoding="utf-8")
            with self.assertRaisesRegex(SourceMaterializationError, "duplicate JSON key"):
                build_source_store(path, root / "store", contract_root=CONTRACT_ROOT)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.json"
            input_path.write_text(canonical_json(self.payload()) + "\n", encoding="utf-8")
            output = root / "store"
            output.mkdir()
            sentinel = output / "sentinel"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(SourceMaterializationError, "already exists"):
                build_source_store(input_path, output, contract_root=CONTRACT_ROOT)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
