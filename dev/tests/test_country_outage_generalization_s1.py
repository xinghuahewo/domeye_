from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_generalization_s1.py"


def load_module():
    specification = importlib.util.spec_from_file_location(
        "verify_country_outage_generalization_s1",
        VERIFIER_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 verifier：{VERIFIER_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def passing_remote_payload(module):
    expected = module.EXPECTED
    return {
        "status": "pass",
        "lifecycle": {
            "snapshot_id": expected["lifecycle_snapshot_id"],
            "content_sha256": expected["lifecycle_content_sha256"],
            "file_sha256": expected["lifecycle_file_sha256"],
            "event_count": 81,
            "state_counts": {
                "event_end_outside_data_range": 4,
                "event_end_recorded": 72,
                "event_end_unknown": 5,
            },
        },
        "peer": {
            "dataset_id": expected["peer_dataset_id"],
            "content_sha256": expected["peer_content_sha256"],
            "manifest_sha256": expected["peer_manifest_sha256"],
            "implementation_id": expected["peer_implementation_id"],
            "source_route_event_dataset_id": expected["route_event_dataset_id"],
            "source_route_event_content_sha256": expected["route_event_content_sha256"],
            "source_route_event_manifest_sha256": expected["route_event_manifest_sha256"],
            "actual_route_event_manifest_sha256": expected["route_event_manifest_sha256"],
            "physical_records": 832_942_411,
            "observations": 970_176,
            "sessions": 142,
            "peer_asns": 81,
            "transition_counts": {"1->3": 84_653, "3->6": 1_597, "6->1": 883_926},
        },
        "cohort": {
            "dataset_id": expected["cohort_dataset_id"],
            "content_sha256": expected["cohort_content_sha256"],
            "manifest_sha256": expected["cohort_manifest_sha256"],
            "implementation_id": expected["implementation_id"],
            "source_route_state_dataset_id": expected["route_state_dataset_id"],
            "source_route_state_content_sha256": expected["route_state_content_sha256"],
            "source_route_state_manifest_sha256": expected["route_state_manifest_sha256"],
            "actual_route_state_manifest_sha256": expected["route_state_manifest_sha256"],
            "mapping_version": expected["mapping_version"],
            "mapping_compatible_sha256": expected["mapping_compatible_sha256"],
            "mapping_revised_sha256": expected["mapping_revised_sha256"],
            "event_count": 81,
            "unique_cohorts": 81,
            "members": 1,
            "directions": 1,
            "route_observations": 1,
        },
        "countries": {
            "IR": {
                "state_point": "2026-02-27T01:10:00Z",
                "country_origin_asns": 1,
                "observed_origin_asns": 2,
                "unknown_origin_observations": 1,
            },
            "MW": {
                "state_point": "2026-03-09T14:05:00Z",
                "country_origin_asns": 1,
                "observed_origin_asns": 1,
                "unknown_origin_observations": 0,
            },
        },
    }


class CountryOutageGeneralizationS1VerifierTest(unittest.TestCase):
    def test_expected_remote_evidence_passes(self) -> None:
        module = load_module()
        self.assertEqual(module.validate_remote(passing_remote_payload(module)), [])

    def test_peer_population_drift_is_rejected(self) -> None:
        module = load_module()
        payload = passing_remote_payload(module)
        payload["peer"]["observations"] -= 1
        errors = module.validate_remote(payload)
        self.assertTrue(any("会话事实人口冲突" in item for item in errors), errors)

    def test_cohort_identity_drift_is_rejected(self) -> None:
        module = load_module()
        payload = passing_remote_payload(module)
        payload["cohort"]["dataset_id"] = "event_cohort_dataset_v1_wrong"
        errors = module.validate_remote(payload)
        self.assertTrue(any("dataset_id" in item for item in errors), errors)

    def test_ir_and_mw_freeze_points_are_not_global_window_start(self) -> None:
        module = load_module()
        payload = passing_remote_payload(module)
        payload["countries"]["IR"]["state_point"] = "2026-02-24T00:00:00Z"
        errors = module.validate_remote(payload)
        self.assertTrue(any("冻结点冲突" in item for item in errors), errors)

    def test_remote_audit_checks_no_session_withdraw_inference(self) -> None:
        module = load_module()
        script = module.REMOTE_DEEP_AUDIT_SCRIPT
        self.assertIn('peer["prefix_withdrawal_inference"] == "not_permitted"', script)
        self.assertIn("peer_session_down_never_materializes_or_implies_a_route_withdrawal", script)
        self.assertNotIn("session_down_as_withdraw", script)

    def test_remote_audit_checks_complete_selected_prefix_denominator(self) -> None:
        module = load_module()
        script = module.REMOTE_DEEP_AUDIT_SCRIPT
        self.assertIn(
            "selected_by_at_least_one_country_origin_route_then_frozen_with_all_visible_rrc25_directions",
            script,
        )
        self.assertIn('observation["origin_status"]', script)
        self.assertIn('row["observed_origin_asns"]', script)
        self.assertIn('row["country_origin_asns"]', script)


if __name__ == "__main__":
    unittest.main()
