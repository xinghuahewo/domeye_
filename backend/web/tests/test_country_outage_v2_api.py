from unittest.mock import patch

from services import events_service


def test_country_outage_detail_exposes_structured_event_v2():
    row = {
        'country_chinese_name': '伊朗',
        'total_as_num': 100,
        'max_outage_as_num': 5,
        'outage_ases': [96, 97, 98, 99, 100],
        'outage_level': 'middle',
        'outage_level_descr': '结构化测试',
        'event_info': '结构化国家中断',
        's_time': '2026-02-28 18:05:00',
        'e_time': None,
        'duration': None,
        'incident_id_v2': 'incident_v2_example',
    }
    incident = {
        'incident_id': 'incident_v2_example',
        'detected_at': '2026-02-28T10:10:00Z',
        'onset_at': '2026-02-28T10:05:00Z',
        'peak_at': '2026-02-28T10:15:00Z',
        'trough_at': None,
        'partial_recovery_at': None,
        'full_recovery_at': None,
        'observation_end_at': '2026-02-28T15:00:00Z',
        'duration_state': 'lower_bound',
        'recovery_state': 'unknown',
        'cohort_id': 'cohort_live_v2_example',
        'peak_snapshot_id': 'snapshot_live_v2_example',
        'trough_snapshot_id': None,
        'algorithm_version': 'country_outage_live_event_model_v2',
        'milestones': {},
        'legacy_ref': None,
    }
    with patch(
        'services.events_service.get_country_outage_de',
        return_value=[row],
    ), patch(
        'services.events_service.get_country_outage_v2',
        return_value=incident,
    ) as structured_query:
        detail = events_service._get_country_outage_detail(
            '2026-02-28 18:05:00', 'IR', 1, 'r'
        )
    assert detail['event_model_v2']['detected_at'] != (
        detail['event_model_v2']['onset_at']
    )
    assert detail['event_model_v2']['duration_state'] == 'lower_bound'
    assert detail['event_model_v2']['recovery_state'] == 'unknown'
    structured_query.assert_called_once_with(
        events_service.conn_15,
        incident_id='incident_v2_example',
    )
