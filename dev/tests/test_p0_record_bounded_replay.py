from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

from dev.data_quality import p0_record_bounded_replay as recorded


class RecordedBoundedReplayTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runner = self.root / "p0_normalize_candidate.py"
        self.runner.write_text(
            textwrap.dedent(
                """
                import hashlib
                from pathlib import Path
                import sys

                output = Path(sys.argv[sys.argv.index("--output-dir") + 1])
                output.mkdir()
                payload = b"fixture\\n"
                (output / "manifest.json").write_bytes(payload)
                digest = hashlib.sha256(payload).hexdigest()
                (output / "SHA256SUMS").write_text(
                    f"{digest}  manifest.json\\n", encoding="utf-8"
                )
                print("sample-ready")
                """
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def args(self, max_events="64"):
        candidate = self.root / "candidate"
        return Namespace(
            execution_id="sample-a",
            candidate_dir=str(candidate),
            stdout_log=str(self.root / "stdout.log"),
            stderr_log=str(self.root / "stderr.log"),
            evidence_out=str(self.root / "execution.json"),
            command=[
                sys.executable,
                str(self.runner),
                "--max-events",
                max_events,
                "--output-dir",
                str(candidate),
            ],
        )

    def test_records_success_without_environment_or_command_contents(self):
        args = self.args()
        result = recorded.run(args)

        evidence = json.loads(Path(args.evidence_out).read_text(encoding="utf-8"))
        self.assertEqual(evidence, result)
        self.assertEqual(evidence["schema_version"], recorded.SCHEMA_VERSION)
        self.assertEqual(evidence["exit_code"], 0)
        self.assertNotIn("command", evidence)
        self.assertNotIn("environment", evidence)
        self.assertEqual(Path(args.stdout_log).read_text(encoding="utf-8"), "sample-ready\n")

    def test_rejects_any_sample_size_other_than_64_before_execution(self):
        args = self.args("63")

        with self.assertRaisesRegex(recorded.RecordedRunError, "max-events 64"):
            recorded.run(args)
        self.assertFalse(Path(args.candidate_dir).exists())
        self.assertFalse(Path(args.evidence_out).exists())


if __name__ == "__main__":
    unittest.main()
