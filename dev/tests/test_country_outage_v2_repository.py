from __future__ import annotations

from copy import deepcopy
import json
import unittest

from backend.core.country_outage_v2 import (
    build_live_observation,
    legacy_peak_projection,
    new_runtime_state,
    reduce_live_observation,
)
from backend.database.country_outage_v2_repository import (
    CountryOutageV2RepositoryError,
    persist_v2,
    validate_legacy_projection,
)


class FakeCursor:
    def __init__(
        self,
        *,
        fail_at: int | None = None,
        conflict_payload: dict | None = None,
    ) -> None:
        self.fail_at = fail_at
        self.conflict_payload = conflict_payload
        self.calls = []
        self.rowcount = 1
        self.closed = False
        self._selecting_existing = False

    def execute(self, query, params=None):
        self.calls.append((query, params))
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise RuntimeError("injected database failure")
        query_text = str(query)
        self._selecting_existing = (
            "SELECT incident_id, observation_payload" in query_text
        )
        if (
            "INSERT INTO country_outage_observation_v2" in query_text
            and self.conflict_payload is not None
        ):
            self.rowcount = 0
        else:
            self.rowcount = 1

    def fetchone(self):
        if self._selecting_existing:
            return (
                'incident_v2_11e9b989f602b7d99ebf4a13',
                self.conflict_payload,
            )
        return None

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor_instance = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, *args, **kwargs):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def fixture():
    baseline = range(1, 101)
    state = new_runtime_state(
        source="r",
        country_code="IR",
        collector_id="legacy_live",
        baseline_asns=baseline,
    )
    rows = []
    for observed_at, affected in (
        ("2026-02-28 18:00:00", 4),
        ("2026-02-28 18:05:00", 5),
    ):
        observation = build_live_observation(
            source="r",
            country_code="IR",
            observed_at_local=observed_at,
            outage_asns=range(101 - affected, 101),
            normal_asns=range(1, 101 - affected),
            baseline_asns=baseline,
        )
        result = reduce_live_observation(state, observation)
        state = result["state"]
        rows.extend(result["persist_observations"])
    projection = legacy_peak_projection(
        incident=state["incident"],
        peak_observation=state["peak_observation"],
        country_chinese_name="伊朗",
        outage_level="high",
        outage_level_descr="测试",
        outage_id=1,
    )
    return state["incident"], [state["episode"]], rows, projection


class CountryOutageV2RepositoryTest(unittest.TestCase):
    def test_transaction_commits_once_after_all_writes(self) -> None:
        incident, episodes, rows, projection = fixture()
        cursor = FakeCursor()
        conn = FakeConnection(cursor)
        persist_v2(
            conn=conn,
            incident=incident,
            episodes=episodes,
            observations=rows,
            legacy_table="country_outage_202602",
            legacy_projection=projection,
            legacy_source="r",
            legacy_country="IR",
            legacy_outage_id=1,
        )
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)
        self.assertTrue(cursor.closed)

    def test_failure_rolls_back_and_rethrows(self) -> None:
        incident, episodes, rows, projection = fixture()
        conn = FakeConnection(FakeCursor(fail_at=3))
        with self.assertRaises(RuntimeError):
            persist_v2(
                conn=conn,
                incident=incident,
                episodes=episodes,
                observations=rows,
                legacy_table="country_outage_202602",
                legacy_projection=projection,
                legacy_source="r",
                legacy_country="IR",
                legacy_outage_id=1,
            )
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)

    def test_identical_observation_conflict_is_idempotent(self) -> None:
        incident, episodes, rows, _projection = fixture()
        conn = FakeConnection(
            FakeCursor(conflict_payload=deepcopy(rows[0]))
        )
        persist_v2(
            conn=conn,
            incident=incident,
            episodes=episodes,
            observations=[rows[0]],
        )
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)

    def test_different_observation_conflict_is_rejected(self) -> None:
        incident, episodes, rows, _projection = fixture()
        conflicting = deepcopy(rows[0])
        conflicting["asn_state"]["affected_asns"] = [1]
        conn = FakeConnection(FakeCursor(conflict_payload=conflicting))
        with self.assertRaises(CountryOutageV2RepositoryError):
            persist_v2(
                conn=conn,
                incident=incident,
                episodes=episodes,
                observations=[rows[0]],
            )
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)

    def test_peak_projection_mismatch_is_rejected(self) -> None:
        incident, _episodes, _rows, projection = fixture()
        invalid = deepcopy(projection)
        invalid["outage_ases"] = invalid["outage_ases"][:-1]
        with self.assertRaises(CountryOutageV2RepositoryError):
            validate_legacy_projection(incident, invalid)


if __name__ == "__main__":
    unittest.main()
