from unittest.mock import patch

from services.country_outage_service import get_legacy_country_outage_observation


REFERENCE = "country_outage/2026-03-09 22:09:38/MW/2/r"
DETAIL = {
    "outage_country": "马拉维",
    "start_time": "2026-03-09 22:09:38",
    "end_time": "2026-03-10 01:43:06",
    "duration": "3:33:28",
    "total_as_num": 26,
    "outage_as_num": 1,
    "outage_ases": [328919],
    "event_level": "low",
    "event_descr": "旧事实测试",
    "event_info": "国家中断旧事实摘要",
}


def test_legacy_source_code_is_not_exposed_as_collector_identity():
    with patch(
        "services.country_outage_service.get_event_detail_data",
        return_value=DETAIL,
    ):
        observation = get_legacy_country_outage_observation(REFERENCE)

    assert observation["observation_scope"]["collector_id"] == "rrc25"
    assert observation["observation_scope"]["collector_ids"] == ["rrc25"]
    assert observation["observation_scope"]["collector_count"] == 1
    assert observation["legacy_summary"]["source"] == "r"
