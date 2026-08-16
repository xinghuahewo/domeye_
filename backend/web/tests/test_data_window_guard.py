from unittest.mock import patch
from urllib.parse import quote

import pytest


@pytest.fixture
def enforced_window(monkeypatch):
    monkeypatch.setenv("DOMEYE_ENFORCE_DATA_WINDOW", "true")
    monkeypatch.setenv("DOMEYE_DATA_WINDOW_START", "2026-02-01 00:00:00")
    monkeypatch.setenv("DOMEYE_DATA_WINDOW_END_EXCLUSIVE", "2026-04-01 00:00:00")
    monkeypatch.setenv("DOMEYE_DATA_SNAPSHOT_TIME", "2026-03-31 23:59:59")


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/events",
        "/api/v1/features/top?target=collector",
        "/api/v1/features/countries",
        "/api/v1/features/outages/global-as",
        "/api/v1/dashboard/overview",
    ],
)
def test_development_window_rejects_missing_query_times(client, enforced_window, path):
    response = client.get(path)
    assert response.status_code == 400
    assert response.get_json()["status"] is False


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/events?date=2026-03-31_2026-04-01",
        "/api/v1/features/top?target=collector&start_time=2026-03-31%2023:59:59&end_time=2026-04-01%2000:00:00",
        "/api/v1/features/outages/global-prefix?start_time=2026-01-31%2023:59:59&end_time=2026-02-01%2000:00:00",
        "/api/v1/dashboard/overview?start_time=2026-03-31%2023:59:59&end_time=2026-04-01%2000:00:00",
        "/api/v1/hijack/{}/203.0.113.0-24/1/r".format(
            quote("2026-04-01 00:00:00", safe="")
        ),
    ],
)
def test_development_window_rejects_out_of_range_queries(client, enforced_window, path):
    response = client.get(path)
    assert response.status_code == 400
    assert response.get_json()["status"] is False


def test_march_boundary_reaches_event_service(client, enforced_window):
    expected = {"total_page": 0, "record_count": "0", "data": []}
    with patch(
        "web.api.events.api.get_event_list_data",
        return_value=expected,
    ) as query:
        response = client.get("/api/v1/events?date=2026-03-31_2026-03-31")

    assert response.status_code == 200
    assert response.get_json() == expected
    query.assert_called_once()


def test_second_precision_march_range_reaches_event_service(client, enforced_window):
    expected = {"total_page": 0, "record_count": "0", "data": []}
    with patch(
        "web.api.events.api.get_event_list_data",
        return_value=expected,
    ) as query:
        response = client.get(
            "/api/v1/events"
            "?datetime=2026-03-31%2000:00:00_2026-03-31%2023:59:59"
        )

    assert response.status_code == 200
    assert response.get_json() == expected
    query.assert_called_once()
    assert query.call_args.kwargs["params"]["datetime"] == (
        "2026-03-31 00:00:00_2026-03-31 23:59:59"
    )


def test_rejected_feature_range_does_not_reach_service(client, enforced_window):
    with patch("web.api.features.api.get_top_feature_data") as query:
        response = client.get(
            "/api/v1/features/top"
            "?target=collector"
            "&start_time=2026-03-31%2023:59:59"
            "&end_time=2026-04-01%2000:00:00"
        )

    assert response.status_code == 400
    query.assert_not_called()


def test_rejected_detail_time_does_not_reach_service(client, enforced_window):
    path = "/api/v1/hijack/{}/203.0.113.0-24/1/r".format(
        quote("2026-04-01 00:00:00", safe="")
    )
    with patch("web.api.events.api.get_event_detail_data") as query:
        response = client.get(path)

    assert response.status_code == 400
    query.assert_not_called()


def test_production_default_does_not_enable_window_guard(client, monkeypatch):
    monkeypatch.delenv("DOMEYE_ENFORCE_DATA_WINDOW", raising=False)
    with patch(
        "web.api.events.api.get_event_list_data",
        return_value={"total_page": 0, "record_count": "0", "data": []},
    ) as query:
        response = client.get("/api/v1/events")

    assert response.status_code == 200
    query.assert_called_once()
