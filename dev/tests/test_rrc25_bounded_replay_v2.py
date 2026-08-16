from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from backend.data_pipeline.route_event import artifact_id_v1
from backend.data_pipeline.research.rrc25_country_outage.bounded_replay_v2 import (
    BoundedReplayExecutionError,
    select_fixed_inputs,
)


UTC = timezone.utc
RIB_SHA = "036e1a5b4d1554eae083d8b4d9de648f0ed95bfcd0ea781c4d001df68a23159c"


def selection() -> dict:
    rib = {
        "artifact_id": artifact_id_v1(RIB_SHA),
        "file_sha256": RIB_SHA,
        "collector_id": "rrc25",
        "artifact_type": "rib",
        "artifact_time_utc": "2026-02-28T08:00:00Z",
        "relative_path": "rrc25/2026.02/bview.20260228.0800.gz",
        "compression": "gz",
        "size_bytes": 426_297_361,
    }
    base = 401_865_192 // 84
    remainder = 401_865_192 - base * 84
    start = datetime(2026, 2, 28, 8, 0, tzinfo=UTC)
    updates = []
    for index in range(84):
        observed = start + timedelta(minutes=5 * index)
        sha = f"{index + 1:064x}"
        updates.append(
            {
                "artifact_id": artifact_id_v1(sha),
                "file_sha256": sha,
                "collector_id": "rrc25",
                "artifact_type": "update",
                "artifact_time_utc": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "relative_path": (
                    "rrc25/2026.02/updates."
                    + observed.strftime("%Y%m%d.%H%M")
                    + ".gz"
                ),
                "compression": "gz",
                "size_bytes": base + (remainder if index == 83 else 0),
            }
        )
    return {
        "roles": {
            "analysis_ribs": [rib],
            "analysis_updates": updates,
        }
    }


class BoundedReplaySelectionTest(unittest.TestCase):
    def test_selects_exact_one_plus_eighty_four(self) -> None:
        selected = select_fixed_inputs(selection())
        self.assertEqual(len(selected["catch_up_updates"]), 25)
        self.assertEqual(len(selected["formal_updates"]), 59)
        self.assertEqual(
            selected["formal_updates"][-1]["artifact_time_utc"],
            "2026-02-28T14:55:00Z",
        )

    def test_missing_update_fails_closed(self) -> None:
        value = selection()
        del value["roles"]["analysis_updates"][20]
        with self.assertRaises(BoundedReplayExecutionError):
            select_fixed_inputs(value)

    def test_wrong_bview_identity_fails_closed(self) -> None:
        value = deepcopy(selection())
        value["roles"]["analysis_ribs"][0]["size_bytes"] += 1
        with self.assertRaises(BoundedReplayExecutionError):
            select_fixed_inputs(value)


if __name__ == "__main__":
    unittest.main()
