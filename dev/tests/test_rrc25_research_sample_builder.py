from dataclasses import replace
import json
from pathlib import Path
import subprocess
import unittest

from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    MappingAssignment,
    SameSnapshotRatio,
    build_country_cohort,
    compute_country_snapshot_impact,
)
from backend.data_pipeline.research.rrc25_country_outage.sample_builder import (
    SampleBuildError,
    SampleSourceRef,
    build_country_outage_sample,
    observed_slot_count,
    unknown_slot_count,
)
from dev.tests.test_rrc25_research_country_impact import (
    MAPPED,
    entry,
    mapping,
    seed,
    snapshot,
)


RUN_ID = "research_run_v1_" + "a" * 24
ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts/research/country-outage-sample.schema.json"
SOURCE = SampleSourceRef(
    "state_shard", "state/window-0001.jsonl.gz", "b" * 64
)


def _impact(*, gapped=False):
    country_mapping = mapping(MappingAssignment(65001, ("IR",), MAPPED))
    baseline = seed(entry(1))
    current = snapshot(
        1,
        entry(2),
        continuity="unknown_after_gap" if gapped else "continuous",
        reasons=("update_slot_missing",) if gapped else (),
    )
    cohort = build_country_cohort(baseline, (current,), country_mapping)
    return current, compute_country_snapshot_impact(current, cohort)


def _build(current, impact, **overrides):
    values = {
        "run_id": RUN_ID,
        "collector_id": "rrc25",
        "announce_count": observed_slot_count(2),
        "withdraw_count": observed_slot_count(0),
        "vp_expected_count": observed_slot_count(1),
        "vp_observed_count": observed_slot_count(1),
        "source_refs": (SOURCE,),
    }
    values.update(overrides)
    return build_country_outage_sample(impact, current, **values)


def _validate_with_contract(payload):
    script = r"""
const fs = require('fs')
const path = require('path')
const root = process.argv[1]
const schemaPath = process.argv[2]
const Ajv2020 = require(path.join(root, 'frontend', 'node_modules', '@redocly', 'ajv', 'dist', '2020')).default
const schema = JSON.parse(fs.readFileSync(schemaPath, 'utf8'))
const payload = JSON.parse(fs.readFileSync(0, 'utf8'))
const ajv = new Ajv2020({allErrors: true, allowUnionTypes: true, strict: true})
ajv.addFormat('date-time', {
  type: 'string',
  validate: (value) => {
    const timestamp = Date.parse(value)
    return Number.isFinite(timestamp) && new Date(timestamp).toISOString().replace('.000Z', 'Z') === value
  },
})
const validate = ajv.compile(schema)
if (!validate(payload)) {
  process.stderr.write(ajv.errorsText(validate.errors, {separator: '; '}))
  process.exit(1)
}
"""
    result = subprocess.run(
        ["node", "-e", script, str(ROOT), str(SCHEMA_PATH)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


class Rrc25CountrySampleBuilderTests(unittest.TestCase):
    def test_all_metrics_sets_and_ratio_components_share_parent_identity(self):
        current, impact = _impact()
        sample = _build(current, impact)

        self.assertRegex(sample["sample_id"], r"^sample_v1_[0-9a-f]{24}$")
        self.assertEqual(sample["snapshot_id"], impact.snapshot_id)
        self.assertEqual(sample["slot"]["boundary"], "[start,end)")
        for measure in sample["metrics"].values():
            self.assertEqual(measure["sample_id"], sample["sample_id"])
            self.assertEqual(measure["snapshot_id"], sample["snapshot_id"])
        ratio = sample["metrics"]["damaged_asn_ratio"]
        self.assertEqual(ratio["numerator"]["sample_id"], sample["sample_id"])
        self.assertEqual(ratio["denominator"]["snapshot_id"], sample["snapshot_id"])
        for value in sample["asn_sets"].values():
            self.assertEqual(value["sample_id"], sample["sample_id"])
            self.assertEqual(value["snapshot_id"], sample["snapshot_id"])
        _validate_with_contract(sample)

    def test_true_zero_and_unknown_slot_counts_remain_distinct(self):
        current, impact = _impact()
        sample = _build(
            current,
            impact,
            announce_count=unknown_slot_count(
                "unknown_parse_failure", "bgpdump_record_mismatch"
            ),
            withdraw_count=observed_slot_count(0),
        )

        announce = sample["metrics"]["announce_count"]
        withdraw = sample["metrics"]["withdraw_count"]
        self.assertIsNone(announce["value"])
        self.assertEqual(announce["value_state"], "unknown_parse_failure")
        self.assertEqual(withdraw["value"], 0)
        self.assertEqual(withdraw["value_state"], "observed_zero")
        _validate_with_contract(sample)

    def test_state_gap_produces_null_country_values_not_fabricated_zero(self):
        current, impact = _impact(gapped=True)
        unknown = unknown_slot_count("unknown_state_gap", "prior_state_gap")
        sample = _build(
            current,
            impact,
            announce_count=unknown,
            withdraw_count=unknown,
            vp_expected_count=unknown,
            vp_observed_count=unknown,
        )

        self.assertEqual(sample["continuity_state"], "unknown_after_gap")
        self.assertIsNone(sample["metrics"]["visible_asn_count"]["value"])
        self.assertEqual(
            sample["metrics"]["visible_asn_count"]["value_state"],
            "unknown_state_gap",
        )
        self.assertIsNone(sample["asn_sets"]["damaged"]["value"])
        _validate_with_contract(sample)

    def test_ratio_mismatch_and_snapshot_mismatch_fail_closed(self):
        current, impact = _impact()
        wrong_ratio = SameSnapshotRatio(
            impact.snapshot_id,
            1,
            2,
            0.75,
            "observed",
            None,
        )
        wrong_metrics = replace(impact.metrics, damaged_asn_ratio=wrong_ratio)
        with self.assertRaisesRegex(SampleBuildError, "分子分母"):
            _build(current, replace(impact, metrics=wrong_metrics))

        with self.assertRaisesRegex(SampleBuildError, "observed_at"):
            _build(
                replace(
                    current,
                    slot_end_exclusive_utc="2026-02-27T16:10:00Z",
                ),
                impact,
            )

    def test_sources_are_deterministically_sorted_and_duplicates_rejected(self):
        current, impact = _impact()
        mapping_ref = SampleSourceRef(
            "mapping_snapshot", "mapping/as-country.json", "c" * 64
        )
        first = _build(current, impact, source_refs=(SOURCE, mapping_ref))
        second = _build(current, impact, source_refs=(mapping_ref, SOURCE))

        self.assertEqual(first, second)
        with self.assertRaisesRegex(SampleBuildError, "不得重复"):
            _build(current, impact, source_refs=(SOURCE, SOURCE))

    def test_invalid_unknown_reason_and_boolean_count_fail_closed(self):
        with self.assertRaises(SampleBuildError):
            unknown_slot_count("unknown_source_gap", "Bad reason")
        with self.assertRaises(SampleBuildError):
            observed_slot_count(True)


if __name__ == "__main__":
    unittest.main()
