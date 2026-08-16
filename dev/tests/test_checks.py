import importlib.util
from pathlib import Path
import unittest


CHECKS_PATH = Path(__file__).resolve().parents[1] / "checks.py"
SPEC = importlib.util.spec_from_file_location("domeye_checks", CHECKS_PATH)
CHECKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKS)
ROOT = Path(__file__).resolve().parents[2]


class RiskClassificationTest(unittest.TestCase):
    def test_style_and_docs_are_l0(self):
        self.assertEqual(CHECKS.classify("frontend/src/styles/main.css"), 0)
        self.assertEqual(CHECKS.classify("docs/开发流水线.md"), 0)

    def test_frontend_feature_is_l1(self):
        self.assertEqual(CHECKS.classify("frontend/src/pages/HomePage.vue"), 1)

    def test_contract_and_backend_logic_are_l2(self):
        self.assertEqual(CHECKS.classify("frontend/src/api/events.ts"), 2)
        self.assertEqual(CHECKS.classify("backend/services/events_service.py"), 2)
        self.assertEqual(CHECKS.classify("config/performance-budget.json"), 2)
        self.assertEqual(
            CHECKS.classify("config/country-outage-agent-acceptance-v2.json"),
            2,
        )
        self.assertEqual(CHECKS.classify(".codex/hooks/country_outage_agent_review.py"), 2)
        self.assertEqual(CHECKS.classify("tools/verify_rrc25_global_country_packages.py"), 2)

    def test_database_and_deploy_are_l3(self):
        self.assertEqual(CHECKS.classify("backend/database/event.py"), 3)
        self.assertEqual(CHECKS.classify("deploy/database/restore-database.sh"), 3)
        self.assertEqual(
            CHECKS.classify("deploy/country-outage-agent/prepare.sh"),
            3,
        )
        self.assertEqual(CHECKS.classify("dev/database/manage-dev-database.sh"), 3)
        self.assertEqual(CHECKS.classify("dev/backend/manage-dev-api.sh"), 3)
        self.assertEqual(
            CHECKS.classify("agent-sidecar/src/cli/serve-formal.ts"),
            3,
        )

    def test_pipeline_rules_and_data_profile_are_l3(self):
        self.assertEqual(CHECKS.classify("Makefile"), 3)
        self.assertEqual(CHECKS.classify("dev/checks.py"), 3)
        self.assertEqual(CHECKS.classify("config/data-profile.json"), 3)

    def test_research_paths_have_explicit_risk_levels(self):
        self.assertEqual(CHECKS.classify("config/research/iran-rrc25-202602.json"), 3)
        self.assertEqual(
            CHECKS.classify("contracts/research/research-profile.schema.json"),
            3,
        )
        self.assertEqual(
            CHECKS.classify("contracts/research/fixtures/research-run/valid-accepted.json"),
            2,
        )
        self.assertEqual(
            CHECKS.classify("backend/data_pipeline/research/resource_gate.py"),
            2,
        )
        self.assertEqual(CHECKS.classify("openspec/config.yaml"), 2)
        self.assertEqual(CHECKS.classify("openspec/specs/.gitkeep"), 0)

    def test_unknown_research_config_type_still_fails_closed(self):
        with self.assertRaisesRegex(CHECKS.ClassificationError, "未识别文件"):
            CHECKS.classify("config/research/run-worker.sh")

    def test_unknown_file_fails_closed(self):
        with self.assertRaisesRegex(CHECKS.ClassificationError, "未识别文件"):
            CHECKS.classify("mystery/release-switch.conf")

    def test_name_status_includes_deletions_and_both_rename_paths(self):
        output = (
            b"D\0docs/old.md\0"
            b"R100\0deploy/old.sh\0deploy/new.sh\0"
            b"M\0frontend/src/App.vue\0"
        )
        self.assertEqual(
            CHECKS.parse_name_status(output),
            [
                "docs/old.md",
                "deploy/old.sh",
                "deploy/new.sh",
                "frontend/src/App.vue",
            ],
        )


class SelectionTest(unittest.TestCase):
    def test_normalize_change_selects_contract_test(self):
        self.assertEqual(
            CHECKS.frontend_tests(["frontend/src/utils/normalize.ts"]),
            ["src/utils/normalize.test.ts"],
        )

    def test_backend_event_change_selects_event_contract(self):
        self.assertEqual(
            CHECKS.backend_tests(["backend/services/events_service.py"]),
            ["web/tests/test_events_api.py"],
        )

    def test_highest_risk_wins(self):
        self.assertEqual(
            CHECKS.risk_for([
                "frontend/src/styles/main.css",
                "backend/database/event.py",
            ]),
            3,
        )

    def test_risk_summary_separates_level_and_stateful_boundary(self):
        query_summary = CHECKS.risk_summary(["backend/database/event.py"])
        self.assertEqual(query_summary["risk"], 3)
        self.assertIn("db-access", query_summary["flags"])
        self.assertFalse(query_summary["stateful"])

        profile_summary = CHECKS.risk_summary(["config/data-profile.json"])
        self.assertEqual(profile_summary["risk"], 3)
        self.assertIn("data-range", profile_summary["flags"])
        self.assertTrue(profile_summary["stateful"])
        self.assertEqual(
            profile_summary["required_checks"],
            ("check-fast", "check-integration", "check-release"),
        )

        release_summary = CHECKS.risk_summary(["deploy/release/activate.sh"])
        self.assertIn("deployment-switch", release_summary["flags"])
        self.assertTrue(release_summary["stateful"])

    def test_check_release_full_has_no_stateful_command(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = makefile.split("check-release-full:", 1)[1]
        self.assertIn("不允许恢复数据库或切换生产服务", target)
        self.assertNotIn("full-acceptance.sh", target)

    def test_p0_contract_change_selects_only_p0_contract_validator(self):
        commands = CHECKS.fast_checks(["contracts/data/route-event.schema.json"])
        labels = [label for label, _command, _cwd in commands]
        self.assertIn("P0 数据合同", labels)
        self.assertNotIn("OpenAPI 生成类型一致性", labels)

    def test_openapi_change_keeps_openapi_validators(self):
        commands = CHECKS.fast_checks(["contracts/openapi.json"])
        labels = [label for label, _command, _cwd in commands]
        self.assertIn("OpenAPI 生成类型一致性", labels)
        self.assertIn("OpenAPI 与 Flask 路由一致性", labels)
        self.assertNotIn("P0 数据合同", labels)

    def test_agent_sidecar_change_runs_typecheck_and_full_tests(self):
        commands = CHECKS.fast_checks(
            ["agent-sidecar/src/cli/formal-sidecar.ts"]
        )
        labels = [label for label, _command, _cwd in commands]
        self.assertIn("国家中断 Agent Sidecar 类型检查", labels)
        self.assertIn("国家中断 Agent Sidecar 全量测试", labels)

    def test_agent_contract_and_hook_changes_keep_sidecar_gate(self):
        for path in (
            "contracts/agent/country-outage-report-facts-v1.schema.json",
            "config/country-outage-agent-core-acceptance-v3.json",
            ".codex/hooks/country_outage_agent_review.py",
        ):
            labels = [
                label
                for label, _command, _cwd in CHECKS.fast_checks([path])
            ]
            self.assertIn("国家中断 Agent Sidecar 类型检查", labels)
            self.assertIn("国家中断 Agent Sidecar 全量测试", labels)

    def test_agent_deploy_is_stateful_security_boundary(self):
        summary = CHECKS.risk_summary(
            ["deploy/country-outage-agent/activate.sh"]
        )
        self.assertEqual(summary["risk"], 3)
        self.assertTrue(summary["stateful"])
        self.assertIn("deployment-switch", summary["flags"])
        self.assertIn("security-config", summary["flags"])

    def test_p0_quality_gate_is_integration_only_and_uses_file_fixture(self):
        files = ["dev/data_quality/p0_quality_gate.py"]
        fast = CHECKS.fast_checks(files)
        integration = CHECKS.integration_checks(files)
        fast_labels = [label for label, _command, _cwd in fast]
        integration_by_label = {
            label: command for label, command, _cwd in integration
        }
        self.assertNotIn("P0 数据质量门禁离线 fixture", fast_labels)
        self.assertEqual(
            integration_by_label["P0 数据质量门禁离线 fixture"],
            ["make", "check-data-p0", "P0_QUALITY_FIXTURE=1"],
        )

    def test_check_data_p0_requires_explicit_files_and_has_no_database_arguments(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        target = makefile.split("check-data-p0:", 1)[1].split("\ncheck-release:", 1)[0]
        for name in (
            "P0_D2_MANIFEST",
            "P0_D2_CHECKSUMS",
            "P0_D3_MANIFEST",
            "P0_D3_VERIFICATION_SUMMARY",
            "P0_EXECUTION_CONTEXT",
            "P0_QUALITY_OUTPUT_DIR",
        ):
            self.assertIn(name, target)
        self.assertIn("P0_QUALITY_FIXTURE", target)
        self.assertNotIn("DATABASE_ENV", target)
        self.assertNotIn("psql", target)


if __name__ == "__main__":
    unittest.main()
