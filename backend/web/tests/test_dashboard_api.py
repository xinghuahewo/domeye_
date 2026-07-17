from unittest.mock import patch


def test_total_event_count_api(client):
    expected = [{'time': '2026-07-17', 'num': 12}]
    with patch('web.api.dashboard.api.get_total_event_counts', return_value=expected) as service:
        response = client.get('/api/v1/dashboard/counts/total')

    assert response.status_code == 200
    assert response.get_json() == expected
    service.assert_called_once_with(country=None)


def test_type_event_count_api(client):
    expected = {'event_type': '前缀劫持', 'num': 3, 'amplitude': '0'}
    with patch('web.api.dashboard.api.get_type_event_counts', return_value=expected) as service:
        response = client.get(
            '/api/v1/dashboard/counts/type',
            query_string={'event_type': '前缀劫持'},
        )

    assert response.status_code == 200
    assert response.get_json() == expected
    service.assert_called_once_with(event_type='前缀劫持')
