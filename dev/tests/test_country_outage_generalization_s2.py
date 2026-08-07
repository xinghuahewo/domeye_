from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_generalization_s2.py"


def load_module():
    specification = importlib.util.spec_from_file_location(
        "verify_country_outage_generalization_s2",
        VERIFIER_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 verifier：{VERIFIER_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def passing_payload(module):
    expected = module.EXPECTED
    metric = {
        "dataset_id": expected["metric_dataset_id"],
        "content_sha256": expected["metric_content_sha256"],
        "manifest_sha256": expected["metric_manifest_sha256"],
        "implementation_id": expected["implementation_id"],
        "source_route_event_dataset_id": expected["route_event_dataset_id"],
        "source_route_event_content_sha256": expected["route_event_content_sha256"],
        "source_route_event_manifest_sha256": expected["route_event_manifest_sha256"],
        "source_route_state_dataset_id": expected["route_state_dataset_id"],
        "source_route_state_content_sha256": expected["route_state_content_sha256"],
        "source_route_state_manifest_sha256": expected["route_state_manifest_sha256"],
        "source_event_cohort_dataset_id": expected["cohort_dataset_id"],
        "source_event_cohort_content_sha256": expected["cohort_content_sha256"],
        "source_event_cohort_manifest_sha256": expected["cohort_manifest_sha256"],
        "source_peer_session_dataset_id": expected["peer_dataset_id"],
        "source_peer_session_content_sha256": expected["peer_content_sha256"],
        "mapping_version": expected["mapping_version"],
        "mapping_compatible_sha256": expected["mapping_compatible_sha256"],
        "mapping_revised_sha256": expected["mapping_revised_sha256"],
        "events": 81,
        "state_points": expected["state_points"],
        "fixed_prefixes": expected["fixed_prefixes"],
        "directions": expected["directions"],
        "prefix_state_rows": 1,
        "asn_state_rows": 1,
        "new_prefix_state_rows": 1,
        "unique_new_prefixes_across_events": 1,
    }
    sample = {
        "event_metric_id": "country_event_metric_v1_test",
        "legacy_reference": "country_outage/test",
        "state_points": 1,
        "fixed_prefixes": 1,
        "fixed_asns": 1,
        "new_prefixes": 0,
        "peaks": {
            "interrupted_prefix": 1,
            "complete_prefix": 1,
            "affected_asn": 0,
            "route_interrupted_asn": 1,
        },
        "final": {},
    }
    return {
        "status": "pass",
        "metric": metric,
        "samples": {
            "IR": {**sample, "country_code": "IR"},
            "MW": {**sample, "country_code": "MW"},
        },
    }


class CountryOutageGeneralizationS2VerifierTest(unittest.TestCase):
    def test_expected_remote_evidence_passes(self) -> None:
        module = load_module()
        self.assertEqual(module.validate_remote(passing_payload(module)), [])

    def test_metric_identity_drift_is_rejected(self) -> None:
        module = load_module()
        payload = passing_payload(module)
        payload["metric"]["dataset_id"] = "event_metric_dataset_v1_wrong"
        errors = module.validate_remote(payload)
        self.assertTrue(any("dataset_id" in item for item in errors), errors)

    def test_root_population_drift_is_rejected(self) -> None:
        module = load_module()
        payload = passing_payload(module)
        payload["metric"]["state_points"] -= 1
        errors = module.validate_remote(payload)
        self.assertTrue(any("根人口冲突" in item for item in errors), errors)

    def test_missing_country_sample_is_rejected(self) -> None:
        module = load_module()
        payload = passing_payload(module)
        del payload["samples"]["MW"]
        errors = module.validate_remote(payload)
        self.assertTrue(any("样本缺失" in item for item in errors), errors)

    def test_deep_audit_reconstructs_all_metric_layers(self) -> None:
        module = load_module()
        script = module.REMOTE_DEEP_AUDIT_SCRIPT
        self.assertIn('value["partially_interrupted_prefix_count"]', script)
        self.assertIn('value["route_interrupted_asn_count"]', script)
        self.assertIn('new_cumulative["ipv6"].covered', script)
        self.assertIn("class Coverage", script)
        self.assertIn('point["value_state"] == "observed"', script)


if __name__ == "__main__":
    unittest.main()
