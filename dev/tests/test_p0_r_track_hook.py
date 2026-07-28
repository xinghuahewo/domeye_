import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOOK = PROJECT_ROOT / ".codex" / "hooks" / "p0_data_foundation_r_track_review.py"


class P0RTrackHookTest(unittest.TestCase):
    def test_explicit_r0_review_runs_machine_gate(self):
        completed = subprocess.run(
            [sys.executable, str(HOOK), "--stage", "R0"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("RFA-01 机器清单校验：通过", completed.stdout)

    def test_stop_hook_is_silent_without_declared_r_track_stage(self):
        environment = os.environ.copy()
        environment.pop("DOMEYE_P0_R_TRACK_STAGE", None)
        completed = subprocess.run(
            [sys.executable, str(HOOK)],
            cwd=PROJECT_ROOT,
            env=environment,
            input=json.dumps({"hook_event_name": "Stop"}),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {})

    def test_stop_hook_reviews_explicit_r0_stage(self):
        environment = os.environ.copy()
        environment["DOMEYE_P0_R_TRACK_STAGE"] = "R0"
        completed = subprocess.run(
            [sys.executable, str(HOOK)],
            cwd=PROJECT_ROOT,
            env=environment,
            input=json.dumps(
                {"hook_event_name": "Stop", "stop_hook_active": False}
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("37/37", payload["reason"])


if __name__ == "__main__":
    unittest.main()
