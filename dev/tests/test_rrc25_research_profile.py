from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from backend.data_pipeline.research.rrc25_country_outage import (
    ResearchProfileError,
    canonical_profile_bytes,
    iter_update_slots,
    load_research_profile,
    profile_sha256,
    research_run_id_v1,
    validate_research_profile,
)


ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "config" / "research" / "iran-rrc25-202602.json"
SCHEMA_PATH = ROOT / "contracts" / "research" / "research-profile.schema.json"


class ResearchProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = load_research_profile(PROFILE_PATH)

    def test_iran_profile_freezes_bounded_utc_research_contract(self):
        profile = self.profile
        self.assertEqual(profile["collector_id"], "rrc25")
        self.assertEqual(profile["country_code"], "IR")
        self.assertEqual(profile["time_basis"], "UTC")
        self.assertEqual(profile["window"]["start_utc"], "2026-02-27T16:00:00Z")
        self.assertEqual(profile["window"]["end_exclusive_utc"], "2026-03-06T08:40:00Z")
        self.assertEqual(profile["window"]["interval_semantics"], "half_open")
        self.assertEqual(profile["window"]["granularity_seconds"], 300)
        self.assertEqual(profile["measurement"]["address_families"], ["ipv4", "ipv6"])
        self.assertEqual(profile["resource_limits"]["database_writes"], "forbidden")

    def test_half_open_window_has_1928_updates_and_excludes_end_boundary(self):
        slots = list(iter_update_slots(self.profile))
        self.assertEqual(len(slots), 1928)
        self.assertEqual(slots[0], "2026-02-27T16:00:00Z")
        self.assertEqual(slots[-1], "2026-03-06T08:35:00Z")
        self.assertNotIn("2026-03-06T08:40:00Z", slots)
        self.assertEqual(
            self.profile["window"]["observation_end_utc"],
            "2026-03-06T08:40:00Z",
        )

    def test_seed_baseline_and_expected_counts_are_explicit(self):
        selection = self.profile["input_selection"]
        self.assertEqual(
            selection["state_seed_rib"]["selection_policy"],
            "complete_at_start_or_nearest_complete_before",
        )
        self.assertTrue(selection["state_seed_rib"]["complete_required"])
        self.assertEqual(
            selection["baseline_reference_rib"]["selection_policy"],
            "nearest_complete_strictly_before_start",
        )
        self.assertTrue(
            selection["baseline_reference_rib"]["strictly_before_window_start"]
        )
        self.assertEqual(selection["baseline_reference_rib"]["expected_count"], 1)
        self.assertEqual(selection["analysis_updates"]["expected_slot_count"], 1928)
        self.assertEqual(selection["analysis_ribs"]["expected_slot_count"], 21)

    def test_profile_hash_is_stable_across_json_key_order_and_formatting(self):
        first = profile_sha256(self.profile)
        reordered = json.loads(
            json.dumps(self.profile, ensure_ascii=False, indent=4, sort_keys=False)
        )
        self.assertEqual(first, profile_sha256(reordered))
        self.assertEqual(canonical_profile_bytes(self.profile), canonical_profile_bytes(reordered))
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_run_identity_binds_profile_input_mapping_and_processing_hashes(self):
        values = {
            "input_manifest_sha256": "1" * 64,
            "mapping_sha256": "2" * 64,
            "processing_sha256": "3" * 64,
        }
        first = research_run_id_v1(self.profile, **values)
        second = research_run_id_v1(self.profile, **values)
        changed = deepcopy(self.profile)
        changed["algorithms"]["wave"]["baseline_ratio_floor"] = 0.006
        self.assertEqual(first, second)
        self.assertRegex(first, r"^research_run_v1_[0-9a-f]{24}$")
        self.assertNotEqual(first, research_run_id_v1(changed, **values))

    def test_missing_and_unknown_fields_are_not_defaulted(self):
        missing = deepcopy(self.profile)
        del missing["algorithms"]["episode"]["confirm_consecutive_slots"]
        with self.assertRaisesRegex(ResearchProfileError, "缺少显式字段"):
            validate_research_profile(missing)

        unknown = deepcopy(self.profile)
        unknown["window"]["inclusive_end"] = True
        with self.assertRaisesRegex(ResearchProfileError, "未知字段"):
            validate_research_profile(unknown)

    def test_non_utc_or_inclusive_update_boundary_is_rejected(self):
        local = deepcopy(self.profile)
        local["window"]["start_utc"] = "2026-02-28T00:00:00+08:00"
        with self.assertRaisesRegex(ResearchProfileError, "规范 UTC"):
            validate_research_profile(local)

        inclusive = deepcopy(self.profile)
        inclusive["input_selection"]["analysis_updates"]["expected_slot_count"] = 1929
        with self.assertRaisesRegex(ResearchProfileError, "推导值 1928"):
            validate_research_profile(inclusive)

    def test_incomplete_seed_or_relaxed_readonly_boundary_is_rejected(self):
        incomplete_seed = deepcopy(self.profile)
        incomplete_seed["input_selection"]["state_seed_rib"]["complete_required"] = False
        with self.assertRaisesRegex(ResearchProfileError, "完整制品"):
            validate_research_profile(incomplete_seed)

        writable = deepcopy(self.profile)
        writable["resource_limits"]["database_writes"] = "allowed"
        with self.assertRaisesRegex(ResearchProfileError, "forbidden"):
            validate_research_profile(writable)

    def test_loader_rejects_duplicate_keys_and_non_finite_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.json"
            duplicate.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")
            with self.assertRaisesRegex(ResearchProfileError, "字段重复"):
                load_research_profile(duplicate)

            non_finite = Path(directory) / "nan.json"
            non_finite.write_text('{"value":NaN}', encoding="utf-8")
            with self.assertRaisesRegex(ResearchProfileError, "非有限数值"):
                load_research_profile(non_finite)

    def test_json_schema_is_strict_and_matches_profile_entrypoint(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["$id"],
            "https://domeye.example/contracts/research/research-profile.schema.json",
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(self.profile["$schema"], schema["$id"])


if __name__ == "__main__":
    unittest.main()
