import datetime
from unittest.mock import patch

import pandas as pd
import pytest

from config.data_window import configured_data_window, resolve_query_now
from services.dashboard_service import get_total_event_counts, get_type_event_counts
from services.events_service import get_top_event_items


DEV_SNAPSHOT = "2026-03-31 23:59:59"


def test_query_now_uses_development_snapshot_without_changing_default(monkeypatch):
    monkeypatch.setenv("DOMEYE_DATA_SNAPSHOT_TIME", DEV_SNAPSHOT)
    assert resolve_query_now() == datetime.datetime(2026, 3, 31, 23, 59, 59)

    explicit = datetime.datetime(2026, 2, 10, 12, 0, 0)
    assert resolve_query_now(explicit) is explicit


def test_query_now_uses_system_clock_when_snapshot_is_unconfigured(monkeypatch):
    monkeypatch.delenv("DOMEYE_DATA_SNAPSHOT_TIME", raising=False)
    before = datetime.datetime.now()
    actual = resolve_query_now()
    after = datetime.datetime.now()
    assert before <= actual <= after


@pytest.mark.parametrize(
    "value",
    [
        "2026-03-31",
        "2026-03-31 23:59",
        "2026-03-31 23:59:59+08:00",
        "2026-02-30 00:00:00",
    ],
)
def test_snapshot_rejects_non_second_or_invalid_timestamps(monkeypatch, value):
    monkeypatch.setenv("DOMEYE_DATA_SNAPSHOT_TIME", value)
    with pytest.raises(RuntimeError):
        resolve_query_now()


def test_window_validation_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DOMEYE_ENFORCE_DATA_WINDOW", raising=False)
    monkeypatch.setenv("DOMEYE_DATA_WINDOW_START", "invalid")
    assert configured_data_window() is None


def test_window_requires_snapshot_inside_exclusive_bounds(monkeypatch):
    monkeypatch.setenv("DOMEYE_ENFORCE_DATA_WINDOW", "true")
    monkeypatch.setenv("DOMEYE_DATA_WINDOW_START", "2026-02-01 00:00:00")
    monkeypatch.setenv("DOMEYE_DATA_WINDOW_END_EXCLUSIVE", "2026-04-01 00:00:00")
    monkeypatch.setenv("DOMEYE_DATA_SNAPSHOT_TIME", "2026-04-01 00:00:00")
    with pytest.raises(RuntimeError):
        configured_data_window()


def test_top_events_use_march_tables_and_snapshot_clock(monkeypatch):
    monkeypatch.setenv("DOMEYE_DATA_SNAPSHOT_TIME", DEV_SNAPSHOT)
    with patch(
        "services.events_service.get_top_event",
        return_value=pd.DataFrame(),
    ) as query:
        assert get_top_event_items() == []

    assert query.call_args.kwargs["last_month_table"] == "event_table_202602"
    assert query.call_args.kwargs["event_table"] == "event_table_202603"
    assert query.call_args.kwargs["now"] == datetime.datetime(2026, 3, 31, 23, 59, 59)


def test_dashboard_uses_march_tables_and_snapshot_clock(monkeypatch):
    monkeypatch.setenv("DOMEYE_DATA_SNAPSHOT_TIME", DEV_SNAPSHOT)
    with patch(
        "services.dashboard_service.get_event_count",
        return_value=[],
    ) as query:
        assert get_total_event_counts() == []

    assert query.call_args.kwargs["last_month_table"] == "event_table_202602"
    assert query.call_args.kwargs["event_table"] == "event_table_202603"
    assert query.call_args.kwargs["now"] == datetime.datetime(2026, 3, 31, 23, 59, 59)


def test_type_dashboard_uses_march_tables_and_snapshot_clock(monkeypatch):
    monkeypatch.setenv("DOMEYE_DATA_SNAPSHOT_TIME", DEV_SNAPSHOT)
    with patch(
        "services.dashboard_service.get_type_event_count",
        return_value=([], []),
    ) as query:
        payload = get_type_event_counts(event_type="前缀劫持")

    assert payload["event_type"] == "前缀劫持"
    assert query.call_args.kwargs["last_month_table"] == "event_table_202602"
    assert query.call_args.kwargs["event_table"] == "event_table_202603"
    assert query.call_args.kwargs["now"] == datetime.datetime(2026, 3, 31, 23, 59, 59)
