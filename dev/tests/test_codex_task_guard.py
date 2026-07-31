import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


GUARD_PATH = Path(__file__).resolve().parents[1] / "codex_task_guard.py"
SPEC = importlib.util.spec_from_file_location("codex_task_guard", GUARD_PATH)
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def run_git(root, *arguments):
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


class TemporaryRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        run_git(self.root, "init", "-b", "main")
        run_git(self.root, "config", "user.email", "codex-test@example.invalid")
        run_git(self.root, "config", "user.name", "Codex Test")
        (self.root / "AGENTS.md").write_text("# 测试规则\n", encoding="utf-8")
        (self.root / ".gitignore").write_text(
            "/.codex/TASK.json\n", encoding="utf-8"
        )
        (self.root / "allowed").mkdir()
        (self.root / "allowed" / "baseline.txt").write_text(
            "baseline\n", encoding="utf-8"
        )
        (self.root / "backend" / "core").mkdir(parents=True)
        (self.root / "backend" / "core" / "frozen.py").write_text(
            "FROZEN = True\n", encoding="utf-8"
        )
        run_git(self.root, "add", ".")
        run_git(self.root, "commit", "-m", "baseline")
        self.base = run_git(self.root, "rev-parse", "HEAD")
        run_git(self.root, "switch", "-c", "codex/test-boundary")
        (self.root / ".codex").mkdir()
        self.contract_path = self.root / ".codex" / "TASK.json"
        self.contract = {
            "schemaVersion": GUARD.TASK_SCHEMA,
            "taskId": "test-boundary",
            "targetVersion": "test-v2",
            "worktreeRoot": str(self.root.resolve()),
            "targetBranch": "codex/test-boundary",
            "baseCommit": self.base,
            "allowedPaths": ["allowed/**"],
            "forbiddenPaths": ["backend/core/**"],
            "authoritativeReferences": ["AGENTS.md"],
            "explicitNonGoals": ["不修改冻结核心"],
            "forbiddenContentRules": [],
            "requiredChecks": [
                {
                    "name": "空检查",
                    "command": ["git", "diff", "--check"],
                }
            ],
        }
        self.write_contract()
        self.policy = {
            "immutablePaths": ["backend/core/**"],
            "forbiddenContentRules": [],
        }

    def tearDown(self):
        self.directory.cleanup()

    def write_contract(self):
        self.contract_path.write_text(
            json.dumps(self.contract, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_contract(self):
        return GUARD.load_contract(self.root, ".codex/TASK.json")

    def test_preflight_accepts_exact_clean_worktree_contract(self):
        GUARD.run_preflight(self.root, self.load_contract())

    def test_preflight_rejects_branch_mismatch(self):
        self.contract["targetBranch"] = "codex/other"
        self.write_contract()
        with self.assertRaisesRegex(GUARD.GuardError, "分支不匹配"):
            GUARD.run_preflight(self.root, self.load_contract())

    def test_preflight_rejects_existing_change(self):
        (self.root / "allowed" / "dirty.txt").write_text(
            "dirty\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(GUARD.GuardError, "不是干净状态"):
            GUARD.run_preflight(self.root, self.load_contract())

    def test_contract_seal_rejects_scope_expansion(self):
        contract = self.load_contract()
        GUARD.run_preflight(self.root, contract)
        self.contract["allowedPaths"].append("backend/**")
        self.write_contract()
        with self.assertRaisesRegex(GUARD.GuardError, "preflight 后变化"):
            GUARD.verify_contract_seal(self.root, self.load_contract())

    def test_postflight_accepts_only_allowed_change(self):
        (self.root / "allowed" / "new.txt").write_text("ok\n", encoding="utf-8")
        changed = GUARD.all_task_changes(self.root, self.base)
        self.assertEqual(
            GUARD.check_task_boundaries(
                self.root, changed, self.load_contract(), self.policy
            ),
            [],
        )

    def test_postflight_rejects_unapproved_and_frozen_change(self):
        frozen = self.root / "backend" / "core" / "frozen.py"
        frozen.write_text("FROZEN = False\n", encoding="utf-8")
        changed = GUARD.all_task_changes(self.root, self.base)
        errors = GUARD.check_task_boundaries(
            self.root, changed, self.load_contract(), self.policy
        )
        self.assertTrue(any("仓库级冻结路径" in error for error in errors))
        self.assertTrue(any("任务合同禁止路径" in error for error in errors))
        self.assertTrue(any("未授权路径" in error for error in errors))

    def test_forbidden_content_rule_reports_file_and_line(self):
        target = self.root / "allowed" / "new.py"
        target.write_text("from legacy import old\n", encoding="utf-8")
        rules = GUARD.validate_content_rules(
            [
                {
                    "name": "禁止旧版导入",
                    "includePaths": ["allowed/**"],
                    "regex": r"from\s+legacy",
                    "message": "必须经过适配器。",
                }
            ],
            "rules",
        )
        violations = GUARD.rule_violations(
            self.root, {"allowed/new.py"}, rules
        )
        self.assertEqual(
            violations,
            ["禁止旧版导入：allowed/new.py:1：必须经过适配器。"],
        )


class PathMatchingTest(unittest.TestCase):
    def test_directory_glob_matches_only_same_tree(self):
        self.assertTrue(GUARD.path_matches("src/v2/**", "src/v2/agent/main.py"))
        self.assertTrue(GUARD.path_matches("src/v2/**", "src/v2"))
        self.assertFalse(GUARD.path_matches("src/v2/**", "src/v20/main.py"))

    def test_rename_parser_returns_old_and_new_paths(self):
        output = b"R100\0legacy/old.py\0src/v2/new.py\0"
        self.assertEqual(
            GUARD.parse_name_status(output),
            {"legacy/old.py", "src/v2/new.py"},
        )

    def test_unsafe_pattern_is_rejected(self):
        with self.assertRaisesRegex(GUARD.GuardError, "安全"):
            GUARD.validate_repo_pattern("../sibling/**", "path")

    def test_policy_cannot_remove_builtin_frozen_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "policy.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": GUARD.POLICY_SCHEMA,
                        "immutablePaths": ["backend/core/**"],
                        "forbiddenContentRules": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(GUARD.GuardError, "内建冻结路径"):
                GUARD.load_policy(root, "policy.json")


if __name__ == "__main__":
    unittest.main()
