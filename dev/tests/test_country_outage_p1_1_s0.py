from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPOSITORY_ROOT / "dev/tools/validate_country_outage_p1_1_s0.py"
MANIFEST_PATH = REPOSITORY_ROOT / "evaluation/country-outage/p1-1-s0/manifest.json"


def load_validator():
    specification = importlib.util.spec_from_file_location(
        "validate_country_outage_p1_1_s0",
        VALIDATOR_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载 S0 验证器：{VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CountryOutageP11S0Test(unittest.TestCase):
    def test_current_manifest_and_contracts_are_valid(self) -> None:
        module = load_validator()
        self.assertEqual(module.validate_all(MANIFEST_PATH), [])

    def test_plan_fixtures_close_schema_and_catalog_boundaries(self) -> None:
        module = load_validator()
        schema = module.load_json(module.PLAN_SCHEMA_PATH)
        catalog = module.load_json(module.CATALOG_PATH)
        self.assertEqual(module.validate_fixtures(schema, catalog), [])

    def test_system_identity_and_invented_operator_are_rejected(self) -> None:
        module = load_validator()
        schema = module.load_json(module.PLAN_SCHEMA_PATH)
        catalog = module.load_json(module.CATALOG_PATH)
        identity_errors = module.validate_plan(
            module.load_json(module.FIXTURE_ROOT / "invalid-system-identity.json"),
            schema,
            catalog,
        )
        self.assertTrue(any("系统身份字段" in error for error in identity_errors))
        operator_errors = module.validate_plan(
            module.load_json(module.FIXTURE_ROOT / "invalid-invented-operator.json"),
            schema,
            catalog,
        )
        self.assertTrue(any("operator 不在白名单" in error for error in operator_errors))

    def test_semantic_contrast_set_has_reviewed_minimums(self) -> None:
        module = load_validator()
        schema = module.load_json(module.PLAN_SCHEMA_PATH)
        catalog = module.load_json(module.CATALOG_PATH)
        case_set = module.load_json(module.CASE_SET_PATH)
        self.assertEqual(module.validate_case_set(case_set, schema, catalog), [])
        self.assertGreaterEqual(len(case_set["cases"]), 15)

    def test_address_change_regression_cannot_be_relabelled_as_event_switch(self) -> None:
        module = load_validator()
        schema = module.load_json(module.PLAN_SCHEMA_PATH)
        catalog = module.load_json(module.CATALOG_PATH)
        case_set = module.load_json(module.CASE_SET_PATH)
        drifted = copy.deepcopy(case_set)
        drifted["cases"][0]["expected_goals"][0]["requested_goal"] = "event_switch"
        errors = module.validate_case_set(drifted, schema, catalog)
        self.assertTrue(
            any("‘IP地址变换情况’不是事件切换" in error for error in errors)
        )

    def test_shadow_and_reviewed_plan_permissions_cannot_drift(self) -> None:
        module = load_validator()
        schema = module.load_json(module.PLAN_SCHEMA_PATH)
        policy = module.load_json(module.POLICY_PATH)
        drifted = copy.deepcopy(policy)
        shadow = next(
            item for item in drifted["plan_identities"] if item["plan_kind"] == "shadow"
        )
        shadow["may_execute"] = True
        errors = module.validate_policy(schema, drifted)
        self.assertTrue(any("shadow 计划必须完全隔离" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
