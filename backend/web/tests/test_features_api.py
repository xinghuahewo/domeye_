from unittest.mock import patch

from services import features_service


def test_top_feature_api(client):
    expected = [{'t': '2026-07-17 00:00:00', 'announce': 3, 'withdraw': 1}]
    with patch('web.api.features.api.get_top_feature_data', return_value=expected) as service:
        response = client.get('/api/v1/features/top', query_string={
            'start_time': '2026-07-16 00:00:00',
            'end_time': '2026-07-17 00:00:00',
            'target': 'collector',
        })

    assert response.status_code == 200
    assert response.get_json() == expected
    service.assert_called_once()


def test_as_feature_loads_static_data_only_on_demand():
    with patch('services.features_service.data_loader.ensure_core_data_loaded') as ensure, \
         patch('services.features_service.get_as_country', return_value='中国'), \
         patch('services.features_service.select_as_feature_db', return_value=[]), \
         patch('services.features_service.deal_features', return_value=[]):
        result = features_service.get_top_feature_data(
            '2026-07-16 00:00:00',
            '2026-07-17 00:00:00',
            'AS4134',
            conn=object(),
        )

    assert result == []
    ensure.assert_called_once_with()


def test_outage_feature_requires_time_range():
    result = features_service.get_as_outage_feature(
        country=None,
        start_time=None,
        end_time=None,
        conn=object(),
    )
    assert result['status'] is False
