import json
from pathlib import Path
import tempfile
import unittest

from dev.data_profile import DataProfileError, load_data_profile
from dev.verify_data_profile import verify


ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "config" / "data-profile.json"


class DataProfileTest(unittest.TestCase):
    def test_canonical_profile_and_fixture_are_consistent(self):
        profile = verify()
        self.assertEqual(profile["id"], "feb-mar-2026")
        self.assertEqual(profile["timezone"], "Asia/Shanghai")
        self.assertEqual(profile["local"]["end_exclusive"], "2026-04-01 00:00:00")

    def test_timezone_is_required(self):
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        profile["snapshot_time"] = "2026-03-31T23:59:59"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaisesRegex(DataProfileError, "UTC 偏移"):
                load_data_profile(path)

    def test_snapshot_must_be_one_second_before_exclusive_end(self):
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        profile["snapshot_time"] = "2026-03-31T23:59:00+08:00"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaisesRegex(DataProfileError, "前一秒"):
                load_data_profile(path)


if __name__ == "__main__":
    unittest.main()
