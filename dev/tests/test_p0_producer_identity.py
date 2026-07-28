import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dev.data_quality.p0_producer_identity import (
    DEFAULT_MANIFEST,
    ProducerIdentityError,
    validate_manifest,
)


PROJECT_ROOT = DEFAULT_MANIFEST.parents[2]
PROGRAM = PROJECT_ROOT / "dev" / "data_quality" / "p0_producer_identity.py"


class P0ProducerIdentityTest(unittest.TestCase):
    @staticmethod
    def _payload():
        return json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))

    @staticmethod
    def _validate_payload(payload):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "producer-identity.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            return validate_manifest(path)

    def test_repository_manifest_passes_with_exactly_37_tables(self):
        result = validate_manifest()
        self.assertEqual(result["verdict"], "passed")
        self.assertEqual(result["table_count"], 37)
        self.assertEqual(result["blank_identity_count"], 0)

    def test_missing_table_is_rejected(self):
        payload = self._payload()
        payload["tables"].pop()
        with self.assertRaisesRegex(ProducerIdentityError, "37 张表集合不一致"):
            self._validate_payload(payload)

    def test_unknown_producer_group_is_rejected(self):
        payload = self._payload()
        payload["tables"][0]["producer_group_refs"] = ["missing"]
        with self.assertRaisesRegex(ProducerIdentityError, "未知生产者组"):
            self._validate_payload(payload)

    def test_blank_or_partial_git_identity_is_rejected(self):
        payload = self._payload()
        payload["producer_groups"]["feature_mar_2026"]["segments"][1][
            "code_identity"
        ]["git_sha"] = "6f01237"
        with self.assertRaisesRegex(ProducerIdentityError, "完整 Git SHA"):
            self._validate_payload(payload)

    def test_algorithm_version_must_match_code_identity(self):
        payload = self._payload()
        segment = payload["producer_groups"]["feature_mar_2026"]["segments"][1]
        segment["algorithm_version"] = copy.deepcopy(segment["code_identity"])
        segment["algorithm_version"]["git_sha"] = "a" * 40
        with self.assertRaisesRegex(ProducerIdentityError, "Git SHA 不一致"):
            self._validate_payload(payload)

    def test_pid_cannot_be_both_producer_and_non_producer(self):
        payload = self._payload()
        group = payload["producer_groups"]["feature_mar_2026"]
        group["excluded_non_producer_processes"][0]["producer_pids"].append(
            group["segments"][0]["producer_pids"][0]
        )
        with self.assertRaisesRegex(
            ProducerIdentityError, "生产者和非生产者"
        ):
            self._validate_payload(payload)

    def test_cli_returns_machine_readable_pass(self):
        completed = subprocess.run(
            [sys.executable, str(PROGRAM)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["verdict"], "passed")


if __name__ == "__main__":
    unittest.main()
