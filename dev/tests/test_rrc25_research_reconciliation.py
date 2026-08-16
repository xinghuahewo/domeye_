import copy
import json
from pathlib import Path
import subprocess
import unittest

from backend.data_pipeline.research.rrc25_country_outage.reconciliation import (
    ReconciliationInputError,
    build_reconciliation_result,
    evidence_id_v1,
)


ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = ROOT / "config" / "research" / "iran-rrc25-report-claims.json"
SCHEMA_PATH = ROOT / "contracts" / "research" / "reconciliation-result.schema.json"
RUN_ID = "research_run_v1_" + "a" * 24
SAMPLE_REF = "samples/iran/20260228.jsonl.gz#sample_v1_" + "1" * 24
SOURCE_FACT_REF = "source-facts/legacy-country-outage.json#176-556"
SESSION_REF = "raw/updates.20260228.0810.gz#record=19"
LIMITATION_REF = "limitations/rrc25-single-collector-v1.json"


def _inventory():
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _evidence_registry():
    return [
        {"kind": "sample", "ref": SAMPLE_REF, "sha256": "1" * 64},
        {"kind": "source_fact", "ref": SOURCE_FACT_REF, "sha256": "2" * 64},
        {"kind": "raw_record", "ref": SESSION_REF, "sha256": "3" * 64},
        {"kind": "limitation", "ref": LIMITATION_REF, "sha256": "4" * 64},
    ]


def _known(value, unit, snapshot="snapshot_v1_" + "b" * 24):
    return {
        "value": value,
        "value_state": "recomputed",
        "unit": unit,
        "snapshot_id": snapshot,
        "missing_reason": None,
    }


def _unknown(reason):
    return {
        "value": None,
        "value_state": "unknown",
        "unit": None,
        "snapshot_id": None,
        "missing_reason": reason,
    }


def _assessment(
    outcome,
    recomputed,
    *,
    evidence=(),
    counterevidence=(),
    rationale="按冻结研究口径完成逐项比较。",
    limitations=("结论仅代表 RRC25 观测范围。",),
    unknown_rating=None,
):
    result = {
        "comparison_outcome": outcome,
        "recomputed_value": recomputed,
        "evidence_refs": list(evidence),
        "counterevidence_refs": list(counterevidence),
        "limitations_zh": list(limitations),
        "rationale_zh": rationale,
    }
    if unknown_rating is not None:
        result["unknown_rating"] = unknown_rating
    return result


def _assessments():
    return {
        "report_event_time": _assessment(
            "different",
            _known("2026-02-28T08:15:00Z", "utc_datetime"),
            evidence=(SAMPLE_REF,),
            rationale="复算开始时刻与报告时刻不同。",
        ),
        "ipv4_decline": _assessment(
            "consistent",
            _known(0.058, "baseline_fraction_decline"),
            evidence=(SAMPLE_REF,),
            rationale="复算降幅支持报告的约百分之六描述。",
        ),
        "recovery_state": _assessment(
            "different",
            _known("partially_recovered", "recovery_state"),
            evidence=(SAMPLE_REF,),
            rationale="冻结恢复规则只支持部分恢复。",
        ),
        "report_affected_asn_ratio": _assessment(
            "different",
            _known({"affected": 187, "total": 574}, "asn_count_ratio_components"),
            evidence=(SAMPLE_REF,),
            rationale="同快照的分子和分母与报告不同。",
        ),
        "report_visibility_class_counts": _assessment(
            "different",
            _known(
                {"fully_invisible": 68, "partially_visible": 119},
                "asn_count",
            ),
            evidence=(SAMPLE_REF,),
            rationale="按地址族重算后的分类数量不同。",
        ),
        "database_affected_asn_ratio": _assessment(
            "different",
            _known({"affected": 187, "total": 574}, "legacy_database_asn_ratio"),
            evidence=(SAMPLE_REF,),
            counterevidence=(SOURCE_FACT_REF,),
            rationale="旧数据库摘要不是冻结口径下的同快照结果。",
        ),
        "active_withdrawal_intent": _assessment(
            "not_computable",
            _unknown("路由撤回观测不能证明主动意图"),
            evidence=(SAMPLE_REF,),
            rationale="可观察到撤回，但不能据此识别行为主体的意图。",
        ),
        "physical_cut": _assessment(
            "not_computable",
            _unknown("缺少物理链路遥测"),
            evidence=(LIMITATION_REF,),
            rationale="当前证据不能区分物理断路与其他机制。",
        ),
        "bgp_session_closed": _assessment(
            "not_computable",
            _unknown("单条会话状态不能证明全局关闭机制"),
            evidence=(SESSION_REF,),
            rationale="会话状态仅是单个对等会话的观测限制。",
            unknown_rating="unverifiable",
        ),
        "traffic_impact": _assessment(
            "not_computable",
            _unknown("缺少业务流量遥测"),
            evidence=(LIMITATION_REF,),
            rationale="路由可见性不能替代业务流量测量。",
            unknown_rating="unverifiable",
        ),
        "government_intent": _assessment(
            "not_computable",
            _unknown("缺少决策主体与意图证据"),
            evidence=(LIMITATION_REF,),
            rationale="单一路由观测点不能识别政府意图。",
        ),
    }


def _build(assessments=None, evidence_registry=None):
    return build_reconciliation_result(
        run_id=RUN_ID,
        claim_inventory=_inventory(),
        assessments=_assessments() if assessments is None else assessments,
        evidence_registry=(
            _evidence_registry() if evidence_registry is None else evidence_registry
        ),
    )


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


class ResearchReconciliationTest(unittest.TestCase):
    def test_generates_all_eleven_claims_and_matches_strict_contract(self):
        result = _build()

        self.assertEqual(len(result["claims"]), 11)
        self.assertEqual(
            {claim["claim_type"] for claim in result["claims"]},
            {claim["claim_type"] for claim in _inventory()["claims"]},
        )
        self.assertEqual(result["schema_version"], "reconciliation-result/v1")
        _validate_with_contract(result)

    def test_original_values_are_normalized_without_losing_components(self):
        result = _build()
        by_type = {claim["claim_type"]: claim for claim in result["claims"]}

        self.assertEqual(
            by_type["report_event_time"]["original_value"]["value"],
            "2026-02-28T08:14:00Z",
        )
        self.assertEqual(
            by_type["report_affected_asn_ratio"]["original_value"]["value"],
            '{"affected":199,"total":595}',
        )
        self.assertEqual(
            by_type["database_affected_asn_ratio"]["original_value"]["value"],
            '{"affected":176,"ratio":0.3165,"total":556}',
        )

    def test_evidence_is_content_addressed_and_all_claim_refs_are_closed(self):
        result = _build()
        registry_ids = {row["evidence_id"] for row in result["evidence_registry"]}
        expected = evidence_id_v1(kind="sample", ref=SAMPLE_REF, sha256="1" * 64)

        self.assertIn(expected, registry_ids)
        for claim in result["claims"]:
            self.assertLessEqual(set(claim["evidence_refs"]), registry_ids)
            self.assertLessEqual(set(claim["counterevidence_refs"]), registry_ids)

    def test_summary_is_exact_and_comparison_outcome_remains_explicit_in_chinese(self):
        result = _build()

        self.assertEqual(
            result["summary"],
            {"confirmed": 1, "revised": 5, "unverifiable": 2, "hypothesis_only": 3},
        )
        actual = {name: 0 for name in result["summary"]}
        for claim in result["claims"]:
            actual[claim["rating"]] += 1
            self.assertRegex(claim["rationale_zh"], r"^比较结果：(一致|不同|不可算)。")
        self.assertEqual(actual, result["summary"])

    def test_output_is_deterministic_across_registry_and_mapping_order(self):
        first = _build()
        assessments = _assessments()
        reversed_assessments = dict(reversed(list(assessments.items())))
        second = _build(
            assessments=reversed_assessments,
            evidence_registry=list(reversed(_evidence_registry())),
        )

        self.assertEqual(second, first)
        self.assertEqual(second["reconciliation_id"], first["reconciliation_id"])
        self.assertEqual(
            [claim["claim_id"] for claim in second["claims"]],
            [claim["claim_id"] for claim in first["claims"]],
        )

    def test_rrc25_only_rejects_causal_confirmation(self):
        assessments = _assessments()
        assessments["active_withdrawal_intent"] = _assessment(
            "consistent",
            _known(True, "mechanism_claim"),
            evidence=(SAMPLE_REF,),
            rationale="错误地把路由撤回当作主动意图证明。",
        )

        with self.assertRaisesRegex(ReconciliationInputError, "RRC25 单源不能"):
            _build(assessments=assessments)

    def test_structured_session_observation_cannot_confirm_global_mechanism(self):
        assessments = _assessments()
        assessments["bgp_session_closed"] = _assessment(
            "consistent",
            _known(True, "mechanism_claim"),
            evidence=(SESSION_REF,),
            rationale="错误地从单条会话状态推出全局机制。",
        )

        with self.assertRaisesRegex(ReconciliationInputError, "bgp_session_closed"):
            _build(assessments=assessments)

        safe = _build()
        claim = next(
            item for item in safe["claims"] if item["claim_type"] == "bgp_session_closed"
        )
        self.assertEqual(claim["rating"], "unverifiable")
        self.assertTrue(any("单条对等会话" in text for text in claim["limitations_zh"]))

    def test_confirmed_or_revised_requires_evidence(self):
        assessments = _assessments()
        assessments["ipv4_decline"]["evidence_refs"] = []

        with self.assertRaisesRegex(ReconciliationInputError, "至少一条证据"):
            _build(assessments=assessments)

    def test_unknown_never_accepts_zero_as_a_substitute(self):
        assessments = _assessments()
        assessments["traffic_impact"]["recomputed_value"]["value"] = 0

        with self.assertRaisesRegex(ReconciliationInputError, "不能补零"):
            _build(assessments=assessments)

        safe = _build()
        unknowns = [
            claim["recomputed_value"]
            for claim in safe["claims"]
            if claim["rating"] in {"unverifiable", "hypothesis_only"}
        ]
        self.assertTrue(all(item["value"] is None for item in unknowns))

    def test_unknown_or_duplicate_evidence_ref_is_rejected(self):
        assessments = _assessments()
        assessments["ipv4_decline"]["evidence_refs"] = ["不存在的证据引用"]
        with self.assertRaisesRegex(ReconciliationInputError, "未知证据引用"):
            _build(assessments=assessments)

        duplicate = _evidence_registry()
        duplicate.append(copy.deepcopy(duplicate[0]))
        with self.assertRaisesRegex(ReconciliationInputError, "重复证据"):
            _build(evidence_registry=duplicate)

    def test_duplicate_or_missing_assessment_is_rejected(self):
        rows = [dict(value, claim_key=key) for key, value in _assessments().items()]
        rows.append(copy.deepcopy(rows[0]))
        with self.assertRaisesRegex(ReconciliationInputError, "重复 claim_key"):
            _build(assessments=rows)

        missing = _assessments()
        del missing["ipv4_decline"]
        with self.assertRaisesRegex(ReconciliationInputError, "精确覆盖"):
            _build(assessments=missing)

    def test_wrong_supplied_content_id_and_overlapping_refs_are_rejected(self):
        registry = _evidence_registry()
        registry[0]["evidence_id"] = "evidence_v1_" + "f" * 24
        with self.assertRaisesRegex(ReconciliationInputError, "内容寻址结果不一致"):
            _build(evidence_registry=registry)

        assessments = _assessments()
        assessments["database_affected_asn_ratio"]["counterevidence_refs"] = [
            SAMPLE_REF
        ]
        with self.assertRaisesRegex(ReconciliationInputError, "不能同时作为"):
            _build(assessments=assessments)

    def test_government_and_active_intent_must_remain_hypotheses(self):
        for key in ("active_withdrawal_intent", "government_intent"):
            assessments = _assessments()
            assessments[key]["unknown_rating"] = "unverifiable"
            with self.subTest(claim_key=key):
                with self.assertRaisesRegex(ReconciliationInputError, "hypothesis_only"):
                    _build(assessments=assessments)

    def test_limitations_and_rationale_must_be_chinese(self):
        assessments = _assessments()
        assessments["ipv4_decline"]["rationale_zh"] = "same result"
        with self.assertRaisesRegex(ReconciliationInputError, "必须包含中文"):
            _build(assessments=assessments)


if __name__ == "__main__":
    unittest.main()
