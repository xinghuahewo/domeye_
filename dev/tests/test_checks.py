import importlib.util
from pathlib import Path
import unittest


CHECKS_PATH = Path(__file__).resolve().parents[1] / "checks.py"
SPEC = importlib.util.spec_from_file_location("domeye_checks", CHECKS_PATH)
CHECKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKS)


class RiskClassificationTest(unittest.TestCase):
    def test_style_and_docs_are_l0(self):
        self.assertEqual(CHECKS.classify("frontend/src/styles/main.css"), 0)
        self.assertEqual(CHECKS.classify("docs/开发流水线.md"), 0)

    def test_frontend_feature_is_l1(self):
        self.assertEqual(CHECKS.classify("frontend/src/pages/HomePage.vue"), 1)

    def test_contract_and_backend_logic_are_l2(self):
        self.assertEqual(CHECKS.classify("frontend/src/api/events.ts"), 2)
        self.assertEqual(CHECKS.classify("backend/services/events_service.py"), 2)

    def test_database_and_deploy_are_l3(self):
        self.assertEqual(CHECKS.classify("backend/database/event.py"), 3)
        self.assertEqual(CHECKS.classify("deploy/database/restore-database.sh"), 3)
        self.assertEqual(CHECKS.classify("dev/database/manage-dev-database.sh"), 3)
        self.assertEqual(CHECKS.classify("dev/backend/manage-dev-api.sh"), 3)


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


if __name__ == "__main__":
    unittest.main()
