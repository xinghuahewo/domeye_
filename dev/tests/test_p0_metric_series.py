import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import unittest

from backend.data_pipeline.metrics import (
    METRIC_DEFINITIONS,
    MetricSeriesError,
    build_metric_series,
    canonical_metric_series_bytes,
)


ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
START = "2026-02-28T16:00:00Z"
END = "2026-02-28T16:05:00Z"
SOURCE_REF = {
    "source_layer": "derived_metric",
    "ref_id": "table:feature_country",
    "locator": "feature_country/source=r",
    "sha256": None,
}
SUBJECT = {"subject_type": "global", "subject_id": "global", "display_name": None}
COLLECTOR_SCOPE = {
    "scope_kind": "collector_set",
    "collector_ids": ["rrc25"],
    "limitation_reason": None,
}


def utc_text(value):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def row_for_metric(metric_name, time=START):
    if metric_name == "bgp_announce_record_count":
        return {"time": time, "announ_num": 7}
    if metric_name == "bgp_withdraw_record_count":
        return {"time": time, "withdraw_num": 3}
    if metric_name in {"bgp_update_record_count", "bgp_withdraw_ratio"}:
        return {"time": time, "announ_num": 7, "withdraw_num": 3}
    if metric_name == "ipv4_24_equivalent_count":
        return {"time": time, "v4prefix_num": 4096}
    if metric_name == "ipv6_48_equivalent_count":
        return {"time": time, "v6prefix_num": 8192}
    if metric_name == "ipv4_equivalent_address_count":
        return {"time": time, "v4ip_num": 1048576}
    if metric_name == "anomaly_incident_count":
        return {
            "time": time,
            "incident_ids": ["inc_v1_0123456789abcdef01234567"],
        }
    if metric_name == "prefix_outage_concurrent_count":
        return {
            "time": time,
            "concurrency_samples": [
                {"time": time, "subject_ids": ["10.0.0.0/24"]},
            ],
        }
    if metric_name == "as_outage_concurrent_count":
        return {
            "time": time,
            "concurrency_samples": [
                {"time": time, "subject_ids": ["4134"]},
            ],
        }
    raise AssertionError(metric_name)


def build(metric_name="bgp_announce_record_count", **overrides):
    values = {
        "subject": SUBJECT,
        "collector_scope": COLLECTOR_SCOPE,
        "window_start": START,
        "window_end_exclusive": END,
        "source_available_slots": [START],
        "processing_gap_slots": [],
        "subject_rows": [row_for_metric(metric_name)],
        "source_refs": [SOURCE_REF],
        "generated_at": "2026-07-20T12:30:00Z",
    }
    values.update(overrides)
    return build_metric_series(metric_name, **values)


def assert_schema_valid(test_case, payloads):
    """用仓库已锁定的 AJV 2020 严格校验动态生成对象。"""

    ajv_module = ROOT / "frontend" / "node_modules" / "@redocly" / "ajv" / "dist" / "2020"
    schema_path = ROOT / "contracts" / "data" / "metric-series.schema.json"
    script = r"""
const fs = require('fs')
const Ajv2020 = require(process.argv[1]).default
const schema = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const ajv = new Ajv2020({allErrors: true, allowUnionTypes: true, strict: true, validateFormats: true})
ajv.addFormat('date-time', {
  type: 'string',
  validate: (value) => {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) return false
    const timestamp = Date.parse(value)
    return Number.isFinite(timestamp) && new Date(timestamp).toISOString().replace('.000Z', 'Z') === value
  },
})
const validate = ajv.compile(schema)
for (const payload of JSON.parse(fs.readFileSync(0, 'utf8'))) {
  if (!validate(payload)) {
    process.stderr.write(ajv.errorsText(validate.errors, {separator: '; '}))
    process.exit(1)
  }
}
"""
    result = subprocess.run(
        ["node", "-e", script, str(ajv_module), str(schema_path)],
        cwd=str(ROOT),
        input=json.dumps(payloads, ensure_ascii=False, allow_nan=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    test_case.assertEqual(result.returncode, 0, result.stderr)


class MetricDefinitionAndSchemaTest(unittest.TestCase):
    def test_all_ten_admitted_metrics_match_the_frozen_schema(self):
        self.assertEqual(len(METRIC_DEFINITIONS), 10)
        payloads = [build(metric_name) for metric_name in sorted(METRIC_DEFINITIONS)]
        assert_schema_valid(self, payloads)
        for payload in payloads:
            definition = METRIC_DEFINITIONS[payload["metric_name"]]
            self.assertEqual(payload["unit"], definition.unit)
            self.assertEqual(payload["aggregation"], definition.aggregation)
            self.assertEqual(payload["formula"], definition.formula)
            self.assertEqual(payload["formula_version"], definition.formula_version)

    def test_ipv4_ipv6_and_address_units_are_never_mixed(self):
        expected = {
            "ipv4_24_equivalent_count": "ipv4_24_equivalent",
            "ipv6_48_equivalent_count": "ipv6_48_equivalent",
            "ipv4_equivalent_address_count": "ipv4_equivalent_address",
        }
        for metric_name, unit in expected.items():
            with self.subTest(metric_name=metric_name):
                payload = build(metric_name)
                self.assertEqual(payload["unit"], unit)
                self.assertNotIn("resource_change", payload["formula"])

    def test_same_semantic_input_is_byte_identical_after_reordering(self):
        second = "2026-02-28T16:05:00Z"
        refs = [
            SOURCE_REF,
            {
                "source_layer": "data_quality_report",
                "ref_id": "dqr:p0",
                "locator": "quality-report/sample-coverage",
                "sha256": "0" * 64,
            },
        ]
        common = {
            "window_end_exclusive": "2026-02-28T16:10:00Z",
            "collector_scope": {
                "scope_kind": "collector_set",
                "collector_ids": ["rrc25", "rrc00"],
                "limitation_reason": None,
            },
        }
        first = build(
            source_available_slots=[second, START],
            subject_rows=[
                {"time": second, "announ_num": 2},
                {"time": START, "announ_num": 1},
            ],
            source_refs=list(reversed(refs)),
            **common,
        )
        other = build(
            source_available_slots=[START, second],
            subject_rows=[
                {"time": START, "announ_num": 1},
                {"time": second, "announ_num": 2},
            ],
            source_refs=refs,
            **common,
        )
        self.assertEqual(canonical_metric_series_bytes(first), canonical_metric_series_bytes(other))


class MissingAndCoverageSemanticsTest(unittest.TestCase):
    def test_concurrency_can_express_strict_legacy_unknown_slots_without_zero_fill(self):
        second = "2026-02-28T16:05:00Z"
        payload = build(
            "prefix_outage_concurrent_count",
            window_end_exclusive="2026-02-28T16:10:00Z",
            source_available_slots=[START, second],
            subject_rows=[row_for_metric("prefix_outage_concurrent_count")],
            metric_missing_slots=[
                {
                    "time": second,
                    "value_state": "legacy_unknown",
                    "missing_reason": "legacy_unknown",
                }
            ],
        )
        self.assertEqual(payload["points"][0]["value"], 1)
        self.assertEqual(payload["points"][1]["value_state"], "legacy_unknown")
        self.assertEqual(payload["points"][1]["missing_reason"], "legacy_unknown")
        self.assertIsNone(payload["points"][1]["value"])
        self.assertEqual(payload["metric_observed_sample_count"], 1)
        self.assertEqual(payload["coverage"]["metric_coverage_ratio"], 0.5)
        assert_schema_valid(self, [payload])

    def test_empty_concurrency_samples_are_unknown_not_observed_zero(self):
        payload = build(
            "prefix_outage_concurrent_count",
            subject_rows=[{"time": START, "concurrency_samples": []}],
        )
        point = payload["points"][0]
        self.assertIsNone(point["value"])
        self.assertEqual(point["value_state"], "legacy_unknown")
        self.assertEqual(point["missing_reason"], "legacy_unknown")
        self.assertEqual(payload["metric_observed_sample_count"], 0)
        assert_schema_valid(self, [payload])

        explicit_zero = build(
            "prefix_outage_concurrent_count",
            subject_rows=[
                {
                    "time": START,
                    "concurrency_samples": [
                        {"time": START, "subject_ids": []},
                    ],
                }
            ],
        )
        self.assertEqual(explicit_zero["points"][0]["value"], 0)
        self.assertEqual(explicit_zero["points"][0]["value_state"], "observed_zero")

    def test_explicit_metric_missing_is_mutually_exclusive_and_metric_scoped(self):
        missing = [
            {
                "time": START,
                "value_state": "legacy_unknown",
                "missing_reason": "legacy_unknown",
            }
        ]
        with self.assertRaisesRegex(MetricSeriesError, "不允许该显式"):
            build(metric_missing_slots=missing, subject_rows=[])
        with self.assertRaisesRegex(MetricSeriesError, "不能覆盖已观测"):
            build("as_outage_concurrent_count", metric_missing_slots=missing)
        with self.assertRaisesRegex(MetricSeriesError, "source_unavailable"):
            build(
                "as_outage_concurrent_count",
                source_available_slots=[],
                subject_rows=[],
                metric_missing_slots=missing,
            )
        with self.assertRaisesRegex(MetricSeriesError, "processing_gap"):
            build(
                "as_outage_concurrent_count",
                processing_gap_slots=[START],
                subject_rows=[],
                metric_missing_slots=missing,
            )
        with self.assertRaisesRegex(MetricSeriesError, "状态/原因"):
            build(
                "as_outage_concurrent_count",
                subject_rows=[],
                metric_missing_slots=[
                    {
                        "time": START,
                        "value_state": "legacy_unknown",
                        "missing_reason": "not_retained",
                    }
                ],
            )

    def test_fixed_d0_window_has_exactly_6720_source_gaps_and_6_processing_gaps(self):
        start = datetime(2026, 1, 31, 16, 0, tzinfo=UTC)
        end = datetime(2026, 3, 31, 16, 0, tzinfo=UTC)
        source_start = datetime(2026, 2, 24, 0, 0, tzinfo=UTC)
        gap_start = datetime(2026, 3, 30, 23, 30, tzinfo=UTC)
        source_slots = []
        rows = []
        cursor = source_start
        while cursor < end:
            source_slots.append(utc_text(cursor))
            if not gap_start <= cursor < gap_start + timedelta(minutes=30):
                rows.append({"time": utc_text(cursor), "announ_num": 0})
            cursor += timedelta(minutes=5)
        processing = [
            utc_text(gap_start + timedelta(minutes=5 * index)) for index in range(6)
        ]
        payload = build(
            window_start=utc_text(start),
            window_end_exclusive=utc_text(end),
            source_available_slots=reversed(source_slots),
            processing_gap_slots=reversed(processing),
            subject_rows=reversed(rows),
        )
        self.assertEqual(payload["expected_sample_count"], 16992)
        self.assertEqual(payload["source_observed_sample_count"], 10272)
        self.assertEqual(payload["metric_observed_sample_count"], 10266)
        self.assertEqual(payload["coverage"]["source_gap_sample_count"], 6720)
        self.assertEqual(payload["coverage"]["processing_gap_sample_count"], 6)
        states = {}
        for point in payload["points"]:
            states[point["value_state"]] = states.get(point["value_state"], 0) + 1
        self.assertEqual(
            states,
            {"source_unavailable": 6720, "processing_gap": 6, "observed_zero": 10266},
        )
        self.assertIsNone(payload["points"][0]["value"])
        self.assertEqual(payload["points"][0]["missing_reason"], "source_unavailable")

    def test_parse_failed_slots_remain_null_and_are_not_source_observed(self):
        second = "2026-02-28T16:05:00Z"
        payload = build(
            window_end_exclusive="2026-02-28T16:15:00Z",
            source_available_slots=[START],
            source_parse_failed_slots=[second],
            subject_rows=[row_for_metric("bgp_announce_record_count")],
        )

        self.assertEqual(
            [point["value_state"] for point in payload["points"]],
            ["observed_nonzero", "parse_failed", "source_unavailable"],
        )
        self.assertIsNone(payload["points"][1]["value"])
        self.assertEqual(payload["points"][1]["missing_reason"], "parse_failed")
        self.assertEqual(payload["source_observed_sample_count"], 1)
        self.assertEqual(payload["coverage"]["source_gap_sample_count"], 2)
        assert_schema_valid(self, [payload])

        with self.assertRaisesRegex(MetricSeriesError, "必须互斥"):
            build(source_parse_failed_slots=[START])

    def test_withdraw_ratio_denominator_zero_is_not_zero_percent(self):
        payload = build(
            "bgp_withdraw_ratio",
            subject_rows=[{"time": START, "announ_num": 0, "withdraw_num": 0}],
        )
        point = payload["points"][0]
        self.assertIsNone(point["value"])
        self.assertEqual(point["value_state"], "not_applicable")
        self.assertEqual(point["missing_reason"], "denominator_zero")
        self.assertEqual(
            point["formula_inputs"],
            {"numerator_withdraw_count": 0, "denominator_update_total": 0},
        )
        self.assertEqual(payload["metric_observed_sample_count"], 0)
        self.assertEqual(payload["subject_active_sample_count"], 1)
        assert_schema_valid(self, [payload])

    def test_asn_sparse_absence_can_be_zero_only_for_update_counts(self):
        second = "2026-02-28T16:05:00Z"
        third = "2026-02-28T16:10:00Z"
        asn = {"subject_type": "asn", "subject_id": "4134", "display_name": None}
        payload = build(
            subject=asn,
            window_end_exclusive="2026-02-28T16:15:00Z",
            source_available_slots=[third, START, second],
            subject_rows=[{"time": second, "announ_num": 5}],
            sparse_asn_activity=True,
        )
        self.assertEqual([point["value"] for point in payload["points"]], [0, 5, 0])
        self.assertEqual(payload["metric_observed_sample_count"], 3)
        self.assertEqual(payload["subject_active_sample_count"], 1)
        self.assertEqual(payload["coverage"]["source_coverage_ratio"], 1)
        self.assertEqual(payload["coverage"]["metric_coverage_ratio"], 1)
        self.assertEqual(payload["coverage"]["subject_activity_density"], 0.3333333333)
        with self.assertRaisesRegex(MetricSeriesError, "只允许三个更新计数"):
            build(
                "ipv4_24_equivalent_count",
                subject=asn,
                subject_rows=[],
                sparse_asn_activity=True,
            )
        with self.assertRaisesRegex(MetricSeriesError, "只允许三个更新计数"):
            build(
                subject=SUBJECT,
                subject_rows=[],
                sparse_asn_activity=True,
            )

    def test_resource_gap_is_not_zero_and_is_never_forward_filled(self):
        second = "2026-02-28T16:05:00Z"
        payload = build(
            "ipv6_48_equivalent_count",
            window_end_exclusive="2026-02-28T16:10:00Z",
            source_available_slots=[START, second],
            subject_rows=[{"time": START, "v6prefix_num": 12}],
        )
        self.assertEqual(payload["points"][0]["value"], 12)
        self.assertEqual(payload["points"][1]["value_state"], "not_observed")
        self.assertIsNone(payload["points"][1]["value"])
        self.assertEqual(payload["metric_observed_sample_count"], 1)


class ExplicitNormalizedInputsTest(unittest.TestCase):
    def test_incidents_are_explicit_and_counted_distinctly(self):
        incident_id = "inc_v1_0123456789abcdef01234567"
        payload = build(
            "anomaly_incident_count",
            subject_rows=[{"time": START, "incident_ids": [incident_id, incident_id]}],
        )
        self.assertEqual(payload["points"][0]["value"], 1)
        with self.assertRaisesRegex(MetricSeriesError, "已规范 incident_id_v1"):
            build(
                "anomaly_incident_count",
                subject_rows=[{"time": START, "incident_ids": ["legacy-1"]}],
            )

    def test_concurrency_uses_explicit_normalized_identity_samples(self):
        payload = build(
            "prefix_outage_concurrent_count",
            subject_rows=[
                {
                    "time": START,
                    "concurrency_samples": [
                        {"time": START, "subject_ids": ["10.0.0.0/24"]},
                        {
                            "time": "2026-02-28T16:03:00Z",
                            "subject_ids": ["10.0.0.0/24", "2001:db8::/48"],
                        },
                    ],
                }
            ],
        )
        self.assertEqual(payload["points"][0]["value"], 2)
        validated_count = build(
            "as_outage_concurrent_count",
            subject_rows=[
                {
                    "time": START,
                    "concurrency_samples": [
                        {
                            "time": START,
                            "distinct_subject_count": 2,
                            "identity_validation": "d2_stable_incident_interval_index_v1",
                        }
                    ],
                }
            ],
        )
        self.assertEqual(validated_count["points"][0]["value"], 2)
        with self.assertRaisesRegex(MetricSeriesError, "identity_validation"):
            build(
                "as_outage_concurrent_count",
                subject_rows=[
                    {
                        "time": START,
                        "concurrency_samples": [
                            {
                                "time": START,
                                "distinct_subject_count": 2,
                                "identity_validation": "caller_asserted",
                            }
                        ],
                    }
                ],
            )
        with self.assertRaisesRegex(MetricSeriesError, "不在所属五分钟槽内"):
            build(
                "as_outage_concurrent_count",
                subject_rows=[
                    {
                        "time": START,
                        "concurrency_samples": [
                            {"time": END, "subject_ids": ["4134"]},
                        ],
                    }
                ],
            )

    def test_query_exceptions_are_not_converted_to_missing_or_zero(self):
        with self.assertRaisesRegex(MetricSeriesError, "调用方必须直接上抛"):
            build(subject_rows=RuntimeError("database timeout"))


class SlotValidationTest(unittest.TestCase):
    def test_duplicate_overlap_and_out_of_window_slots_are_rejected(self):
        with self.assertRaisesRegex(MetricSeriesError, "重复或时区归一化后重叠"):
            build(source_available_slots=[START, "2026-03-01T00:00:00+08:00"])
        with self.assertRaisesRegex(MetricSeriesError, "必须是 source_available_slots 的子集"):
            build(source_available_slots=[], processing_gap_slots=[START], subject_rows=[])
        with self.assertRaisesRegex(MetricSeriesError, "与 processing_gap 槽冲突"):
            build(processing_gap_slots=[START])
        with self.assertRaisesRegex(MetricSeriesError, "越出半开窗口"):
            build(subject_rows=[{"time": END, "announ_num": 1}])
        with self.assertRaisesRegex(MetricSeriesError, "重复或重叠槽"):
            build(
                subject_rows=[
                    {"time": START, "announ_num": 1},
                    {"time": "2026-03-01T00:00:00+08:00", "announ_num": 2},
                ]
            )

    def test_unclassified_dense_missing_is_rejected(self):
        with self.assertRaisesRegex(MetricSeriesError, "请显式分类 processing_gap"):
            build(subject_rows=[])

    def test_window_is_strictly_aligned_and_half_open(self):
        with self.assertRaisesRegex(MetricSeriesError, "未对齐 300 秒网格"):
            build(window_start="2026-02-28T16:00:01Z")
        with self.assertRaisesRegex(MetricSeriesError, "起点必须早于终点"):
            build(window_start=END, window_end_exclusive=START)


if __name__ == "__main__":
    unittest.main()
