import unittest

from backend.data_pipeline.research.rrc25_country_outage.reporting import (
    ResearchReportInputError,
    build_research_report_zh,
)


def _inputs():
    profile = {
        "study_id": "iran-rrc25-country-outage-202602-v1",
        "collector_id": "rrc25",
        "country_code": "IR",
        "window": {
            "start_utc": "2026-02-27T16:00:00Z",
            "end_exclusive_utc": "2026-03-06T08:40:00Z",
        },
    }
    run = {
        "run_id": "research_run_v1_" + "a" * 24,
        "incident_ref": "country_outage/legacy",
        "execution_mode": "bounded_pilot",
        "execution": {
            "new_raw_bytes_read": 123,
            "peak_temporary_bytes": 45,
            "max_worker_seconds": 6,
            "database_write_operations": 0,
        },
    }
    selection = {
        "status": "complete",
        "selected_unique_artifact_count": 3,
        "selected_unique_size_bytes": 123,
        "coverage": {
            "analysis_updates": {
                "expected_count": 2,
                "observed_count": 2,
                "missing_count": 0,
            },
            "analysis_ribs": {
                "expected_count": 1,
                "observed_count": 1,
                "missing_count": 0,
            },
        },
    }
    mapping = {
        "unique_asn_count": 600,
        "target_country_asn_count": 595,
        "conflict_asn_count": 1,
        "missing_country_count": 2,
    }
    baseline = {
        "value_state": "stable",
        "median": 10_000,
        "mad": 10,
        "actual_start_utc": "2026-02-27T16:00:00Z",
        "actual_end_exclusive_utc": "2026-02-27T22:00:00Z",
        "exclusion_boundary": {
            "at_utc": "2026-02-27T22:00:00Z",
            "role": "user_supplied_earliest_possible_precursor_boundary",
            "confirmation_state": "candidate_not_confirmed",
            "causal_claim_allowed": False,
        },
    }
    episode = {
        "episode_id": "episode_v1_" + "b" * 24,
        "onset_at": "2026-02-28T08:10:00Z",
        "detected_at": "2026-02-28T08:15:00Z",
        "trough_at": "2026-02-28T08:20:00Z",
        "recovery_state": "partially_recovered",
        "duration": {
            "duration_state": "lower_bound",
            "minimum_seconds": 900,
        },
        "wave_ids": ["wave_v1_" + "c" * 24],
    }
    reconciliation = {
        "claims": [
            {
                "claim_type": "ipv4_decline",
                "original_value": {
                    "value": 0.06,
                    "value_state": "reported",
                    "missing_reason": None,
                },
                "recomputed_value": {
                    "value": None,
                    "value_state": "unknown",
                    "missing_reason": "有界样本未覆盖完整恢复窗",
                },
                "rating": "unverifiable",
                "rationale_zh": "比较结果：不可计算。样本范围不足。",
                "limitations_zh": ["RRC25 不提供流量遥测。"],
            }
        ]
    }
    quality = {
        "acceptance_state": "not_accepted",
        "gates": [
            {"status": "pass"},
            {"status": "warn"},
            {"status": "fail"},
        ],
    }
    return {
        "profile": profile,
        "run": run,
        "input_selection": selection,
        "mapping_summary": mapping,
        "baseline": baseline,
        "samples": ({"sample_id": "sample"},),
        "episodes": (episode,),
        "waves": ({"wave_id": "wave"},),
        "episode_as_records": ({"asn": 1},),
        "reconciliation": reconciliation,
        "quality": quality,
        "reproduction_commands": ("python3 dev/data_quality/rrc25_iran_research.py verify",),
        "source_temporal_evidence": (
            {
                "incident_id": "inc_v1_" + "d" * 24,
                "locator_record_start": {
                    "utc": "2026-02-27T01:12:32Z",
                    "role": "source_record_identity_only",
                },
                "embedded_message_candidate": {
                    "utc": "2026-02-28T14:34:40Z",
                    "role": "candidate_event_time_from_legacy_text",
                },
                "relationship_state": "unresolved_not_causal",
                "single_event_time_merge_allowed": False,
                "precursor_causality_state": "undetermined",
            },
        ),
    }


class ResearchReportingTest(unittest.TestCase):
    def test_report_is_deterministic_chinese_and_keeps_unknown(self):
        inputs = _inputs()
        first = build_research_report_zh(**inputs)
        second = build_research_report_zh(**inputs)

        self.assertEqual(first, second)
        self.assertIn("有界研究样本闭环", first)
        self.assertIn("不得外推为完整事件人口", first)
        self.assertIn("未知（有界样本未覆盖完整恢复窗）", first)
        self.assertIn("至少 900 秒", first)
        self.assertIn("数据库写操作 | 0", first)
        self.assertNotIn("生成时间", first)
        self.assertIn("2026-02-27T01:12:32Z`（仅源记录身份）", first)
        self.assertIn("2026-02-28T14:34:40Z`（候选）", first)
        self.assertIn("不得合并为单一事件时间", first)
        self.assertIn("基线扩展排除边界为 `2026-02-27T22:00:00Z`", first)
        self.assertIn("`candidate_not_confirmed`", first)
        self.assertIn("该边界不是 Episode onset", first)

    def test_full_profile_omits_pilot_warning(self):
        inputs = _inputs()
        inputs["run"] = dict(inputs["run"], execution_mode="full_profile")
        report = build_research_report_zh(**inputs)

        self.assertIn("冻结 Profile 全窗口闭环", report)
        self.assertNotIn("不得外推为完整事件人口", report)

    def test_incomplete_selection_adds_blocking_limitation(self):
        inputs = _inputs()
        inputs["input_selection"] = dict(
            inputs["input_selection"], status="incomplete"
        )
        report = build_research_report_zh(**inputs)

        self.assertIn("输入 selection 不完整", report)

    def test_report_rejects_promoted_boundary_or_merged_legacy_times(self):
        promoted = _inputs()
        promoted["baseline"] = {
            **promoted["baseline"],
            "exclusion_boundary": {
                **promoted["baseline"]["exclusion_boundary"],
                "confirmation_state": "confirmed_onset",
            },
        }
        with self.assertRaisesRegex(ResearchReportInputError, "不得冒充确认 onset"):
            build_research_report_zh(**promoted)

        merged = _inputs()
        temporal = dict(merged["source_temporal_evidence"][0])
        temporal["single_event_time_merge_allowed"] = True
        merged["source_temporal_evidence"] = (temporal,)
        with self.assertRaisesRegex(ResearchReportInputError, "不得冒充确认事件时间"):
            build_research_report_zh(**merged)

    def test_requires_reproduction_command(self):
        inputs = _inputs()
        inputs["reproduction_commands"] = ()
        with self.assertRaisesRegex(ResearchReportInputError, "复现命令"):
            build_research_report_zh(**inputs)


if __name__ == "__main__":
    unittest.main()
