from __future__ import annotations

import csv
import gzip
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    REPOSITORY_ROOT
    / "dev"
    / "data_quality"
    / "country_outage_event_lifecycle_snapshot.py"
)


def load_module():
    specification = importlib.util.spec_from_file_location(
        "country_outage_event_lifecycle_snapshot",
        SCRIPT_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载脚本：{SCRIPT_PATH}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def write_incidents(path: Path, rows: list[dict[str, str]]) -> None:
    fields = [
        "candidate_id",
        "incident_id",
        "legacy_reference",
        "country_code",
        "event_type",
        "source_code",
        "collector_id",
        "legacy_event_time_utc",
        "status",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
    writer.writeheader()
    writer.writerows(rows)
    path.write_bytes(gzip.compress(stream.getvalue().encode("utf-8"), mtime=0))


def incident(reference: str, country: str, detected_at_utc: str) -> dict[str, str]:
    return {
        "candidate_id": "candidate-v1",
        "incident_id": "incident-" + country.lower(),
        "legacy_reference": reference,
        "country_code": country,
        "event_type": "country_outage",
        "source_code": "r",
        "collector_id": "rrc25",
        "legacy_event_time_utc": detected_at_utc,
        "status": "complete",
    }


class CountryOutageEventLifecycleSnapshotTest(unittest.TestCase):
    def test_previous_complete_point_is_strict_and_window_has_twelve_slots(self) -> None:
        module = load_module()
        row = incident(
            "country_outage/2026-03-09 22:10:00/MW/2/r",
            "MW",
            "2026-03-09T14:10:00Z",
        )
        event = module.build_event(
            row,
            {
                "start_time": "2026-03-09 22:10:00",
                "end_time": "2026-03-09 22:15:01",
                "duration": "0:05:01",
            },
        )
        self.assertEqual(event["cohort_state_point_utc"], "2026-03-09T14:05:00Z")
        self.assertEqual(event["requested_window_start_utc"], "2026-03-09T13:05:00Z")
        self.assertEqual(event["projection_end_state_point_utc"], "2026-03-09T14:20:00Z")
        self.assertEqual(event["event_duration_seconds"], 301)
        self.assertEqual(event["lifecycle_state"], "event_end_recorded")
        self.assertTrue(event["is_final_in_data_range"])

    def test_unknown_end_is_not_invented_from_310_boundary(self) -> None:
        module = load_module()
        event = module.build_event(
            incident(
                "country_outage/2026-02-27 09:12:32/IR/1/r",
                "IR",
                "2026-02-27T01:12:32Z",
            ),
            {"start_time": "2026-02-27 09:12:32", "end_time": "", "duration": ""},
        )
        self.assertEqual(event["cohort_state_point_utc"], "2026-02-27T01:10:00Z")
        self.assertIsNone(event["event_end_at_utc"])
        self.assertEqual(event["projection_end_state_point_utc"], "2026-03-11T00:00:00Z")
        self.assertEqual(event["lifecycle_state"], "event_end_unknown")
        self.assertFalse(event["is_final_in_data_range"])

    def test_end_beyond_310_is_recorded_but_projection_is_capped(self) -> None:
        module = load_module()
        event = module.build_event(
            incident(
                "country_outage/2026-03-10 16:56:34/NE/1/r",
                "NE",
                "2026-03-10T08:56:34Z",
            ),
            {
                "start_time": "2026-03-10 16:56:34",
                "end_time": "2026-03-16 05:10:26",
                "duration": "5 days, 12:13:52",
            },
        )
        self.assertEqual(event["event_end_at_utc"], "2026-03-15T21:10:26Z")
        self.assertEqual(event["projection_end_state_point_utc"], "2026-03-11T00:00:00Z")
        self.assertEqual(event["lifecycle_state"], "event_end_outside_data_range")
        self.assertFalse(event["is_final_in_data_range"])

    def test_source_left_boundary_is_explicit_not_silently_expanded(self) -> None:
        module = load_module()
        event = module.build_event(
            incident(
                "country_outage/2026-02-24 08:20:00/ZZ/1/r",
                "ZZ",
                "2026-02-24T00:20:00Z",
            ),
            {"start_time": "2026-02-24 08:20:00", "end_time": "", "duration": ""},
        )
        self.assertEqual(event["requested_window_start_utc"], "2026-02-23T23:15:00Z")
        self.assertEqual(event["window_start_utc"], "2026-02-24T00:00:00Z")
        self.assertEqual(event["left_boundary_missing_slot_count"], 9)

    def test_inconsistent_duration_is_rejected(self) -> None:
        module = load_module()
        with self.assertRaisesRegex(module.LifecycleSnapshotError, "时长与起止时间不守恒"):
            module.build_event(
                incident(
                    "country_outage/2026-03-09 22:09:38/MW/2/r",
                    "MW",
                    "2026-03-09T14:09:38Z",
                ),
                {
                    "start_time": "2026-03-09 22:09:38",
                    "end_time": "2026-03-10 01:43:06",
                    "duration": "3:33:27",
                },
            )

    def test_snapshot_is_create_only_and_resume_requires_byte_identity(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "incident.tsv.gz"
            output = root / "snapshot.json"
            row = incident(
                "country_outage/2026-03-09 22:09:38/MW/2/r",
                "MW",
                "2026-03-09T14:09:38Z",
            )
            write_incidents(source, [row])
            detail = {
                "start_time": "2026-03-09 22:09:38",
                "end_time": "2026-03-10 01:43:06",
                "duration": "3:33:28",
            }
            with mock.patch.object(module, "fetch_detail", return_value=detail):
                first = module.build_snapshot(source, "http://example.invalid/api/v1", 1)
                second = module.build_snapshot(source, "http://example.invalid/api/v1", 1)
            self.assertEqual(first, second)
            self.assertEqual(first["event_count"], 1)
            self.assertEqual(first["lifecycle_state_counts"], {"event_end_recorded": 1})
            module.write_snapshot(output, first, resume=False)
            module.write_snapshot(output, first, resume=True)
            changed = dict(first)
            changed["event_count"] = 2
            with self.assertRaisesRegex(module.LifecycleSnapshotError, "不允许覆盖"):
                module.write_snapshot(output, changed, resume=True)
            decoded = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(decoded["snapshot_id"], first["snapshot_id"])


if __name__ == "__main__":
    unittest.main()
