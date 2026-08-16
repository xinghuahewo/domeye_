#!/usr/bin/env python3
"""验证 S5 同期国家投影参照、降级语义与同制品输出面。"""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.country_outage_trend_product import (  # noqa: E402
    CONTEMPORANEOUS_REFERENCE_INPUT_SCHEMA_VERSION,
    CONTEMPORANEOUS_REFERENCE_SCHEMA_VERSION,
    TrendProductValidationError,
    answer_trend_question_v1,
    compile_contemporaneous_reference_v1,
    compile_trend_product_v1,
)


FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s5-v1.json"
SCHEMA_PATH = REPOSITORY_ROOT / "contracts" / "agent" / "country-outage-contemporaneous-reference-v1.schema.json"
S4_VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s4.py"
DOC_PATH = REPOSITORY_ROOT / "docs" / "国家中断趋势分析S5验收记录.md"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须为对象：{path}")
    return value


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def build_reference_input(
    profile: dict[str, Any], fixture: dict[str, Any] | None = None
) -> dict[str, Any]:
    frozen = fixture or load_json(FIXTURE_PATH)
    times = [slot["observed_at_utc"] for slot in profile["slots"]]
    projections = []
    for item in frozen["projections"]:
        slots = []
        for index, (observed_at, value) in enumerate(zip(times, item["values"])):
            slots.append(
                {
                    "index": index,
                    "observed_at_utc": observed_at,
                    "state": "observed" if value is not None else "processing_gap",
                    "visible_prefix_vp_count": value,
                }
            )
        projections.append(
            {
                key: deepcopy(value)
                for key, value in item.items()
                if key != "values"
            }
            | {
                "slots": slots,
                "source_refs": [
                    f"rrc25-global-country-packages:/countries/{item['country_code']}/country-snapshots.jsonl.gz"
                ],
            }
        )
    return {
        "schema_version": CONTEMPORANEOUS_REFERENCE_INPUT_SCHEMA_VERSION,
        "identity": deepcopy(frozen["reference_identity"]),
        "time_grid": {
            "slot_seconds": profile["time_grid"]["slot_seconds"],
            "expected_slot_count": profile["time_grid"]["expected_slot_count"],
            "observed_at_utc": times,
        },
        "target_country_code": profile["snapshot"]["country_code"],
        "country_and_unknown_bucket_count": len(projections),
        "projections": projections,
    }


def build_candidate() -> dict[str, Any]:
    s4 = load_module("s4_verifier_for_s5", S4_VERIFIER_PATH).build_candidate()
    reference = compile_contemporaneous_reference_v1(
        s4["profile"], build_reference_input(s4["profile"])
    )
    return compile_trend_product_v1(
        s4["profile"],
        address_family_context=s4["contexts"]["address_family"],
        asn_context=s4["contexts"]["asn"],
        activity_context=s4["contexts"]["activity"],
        contemporaneous_reference_context=reference,
    )


def _negative_case_error(case: dict[str, Any]) -> str | None:
    s4 = load_module(f"s4_negative_{case['id']}", S4_VERIFIER_PATH).build_candidate()
    request = build_reference_input(s4["profile"])
    mutation = case["mutation"]
    if mutation == "collector_id":
        request["identity"]["collector_id"] = "rrc24"
    elif mutation == "data_through":
        request["identity"]["data_through"] = "2000-01-01T01:00:00Z"
    elif mutation == "time_grid":
        request["time_grid"]["observed_at_utc"][1] = "2000-01-01T00:06:00Z"
    elif mutation == "target_value":
        request["projections"][0]["slots"][1]["visible_prefix_vp_count"] = 99
    elif mutation == "bucket_count":
        request["country_and_unknown_bucket_count"] += 1
    elif mutation == "hash_verification":
        request["identity"]["consumed_deliverable_hashes_verified"] = False
    try:
        compile_contemporaneous_reference_v1(s4["profile"], request)
    except TrendProductValidationError as error:
        return error.code
    return None


def validate() -> list[str]:
    errors: list[str] = []
    fixture = load_json(FIXTURE_PATH)
    schema = load_json(SCHEMA_PATH)
    product = build_candidate()
    context = product["contexts"]["contemporaneous_reference"]
    expected = fixture["expected"]
    historical_probe = fixture.get("historical_data_probe") or {}

    if (
        historical_probe.get("mode") != "remote_read_only_recompute"
        or historical_probe.get("bucket_count") != 241
        or historical_probe.get("observation_count_per_bucket") != 60
        or historical_probe.get("target_country_code") != "IR"
        or historical_probe.get("target_asn_migration_percentile") != 99.583333
    ):
        errors.append("S5 没有保留既有 241 桶历史制品的只读数据可行性证据。")
    if historical_probe.get("boundary") != "只读证明现有历史数据可生成同期参照；不是生产或S6同候选验收。":
        errors.append("历史数据探针边界漂移。")

    if context["schema_version"] != CONTEMPORANEOUS_REFERENCE_SCHEMA_VERSION:
        errors.append("同期参照 schema_version 不正确。")
    for field in (
        "projection_bucket_count",
        "comparable_country_count",
        "excluded_projection_count",
    ):
        if context[field] != expected[field]:
            errors.append(f"同期参照 {field} 漂移。")
    if context["exclusion_reason_counts"] != expected["exclusion_reason_counts"]:
        errors.append("小分母、质量不足和未知桶没有按固定规则排除。")
    positions = context["distribution_positions"]
    if positions["maximum_decline_percentage_points"]["empirical_percentile"] != expected["decline_percentile"]:
        errors.append("下降幅度经验百分位不可复算。")
    if positions["persistence_below_95_slot_count"]["empirical_percentile"] != expected["persistence_percentile"]:
        errors.append("持续性经验百分位不可复算。")
    if positions["asn_migration_ratio"]["empirical_percentile"] != expected["asn_migration_percentile"]:
        errors.append("ASN 迁移经验百分位不可复算。")
    target_shape = next(
        item for item in context["curve_shape_distribution"] if item["is_target_shape"]
    )
    if target_shape["country_share"] != expected["target_shape_share"]:
        errors.append("曲线形状同期分布占比不可复算。")
    target_drop = context["common_fluctuation"]["target_largest_drop_slot"]
    if target_drop["declining_country_share"] != expected["target_drop_common_share"]:
        errors.append("目标最大下降槽的同期共同波动比例不可复算。")
    if context["common_fluctuation"]["collector_failure_claim"] is not False:
        errors.append("共同波动被错误升级为 collector 故障结论。")
    if not all(
        marker in context["limitations"]
        for marker in (
            "contemporaneous_distribution_is_not_historical_normal_baseline",
            "country_projection_is_not_automatic_incident_identity",
            "common_fluctuation_is_not_collector_failure_or_cause",
        )
    ):
        errors.append("同期参照边界没有显式保留。")

    claim = next(
        node
        for node in product["evidence_graph"]["nodes"]
        if node.get("claim_kind") == expected["claim_kind"]
    )
    if not claim["evidence_refs"] or not claim["limitation_refs"] or not claim["unknown_refs"]:
        errors.append("同期参照 Claim 未完整绑定 Evidence、Limitation 与 Unknown。")
    answer = answer_trend_question_v1(product, "目标国家在同期全球分布中的位置？")
    if answer["status"] != "answered" or answer["operator"] != expected["qa_operator"]:
        errors.append("同期参照组合追问未命中固定算子。")
    if answer.get("claim_refs") != [claim["node_id"]]:
        errors.append("同期参照追问没有绑定同一 Claim。")

    for case in fixture["negative_cases"]:
        actual = _negative_case_error(case)
        if actual != case["expected_code"]:
            errors.append(
                f"{case['id']} 未按预期失败关闭：{actual} != {case['expected_code']}"
            )
    if schema.get("properties", {}).get("context_type", {}).get("const") != "contemporaneous_country_projection_reference":
        errors.append("同期参照 Schema 没有冻结上下文类型。")
    if schema.get("properties", {}).get("status", {}).get("enum") != ["complete", "insufficient_data"]:
        errors.append("同期参照 Schema 没有冻结降级状态。")
    document = DOC_PATH.read_text(encoding="utf-8")
    for phrase in (
        "同一 RRC25",
        "经验百分位",
        "ASN 迁移",
        "曲线形状",
        "同槽共同波动",
        "不是历史正常基线",
        "不自动构成真实中断事件",
        "TAE-11",
        "不是生产部署",
    ):
        if phrase not in document:
            errors.append(f"S5 验收记录缺少语义：{phrase}")
    if build_candidate() != product:
        errors.append("相同冻结输入不能重复生成相同 S5 制品。")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print(json.dumps({"status": "failed", "errors": errors}, ensure_ascii=False))
        return 1
    product = build_candidate()
    context = product["contexts"]["contemporaneous_reference"]
    print(
        json.dumps(
            {
                "status": "passed",
                "stage": "S5",
                "product_id": product["product_id"],
                "context_id": context["context_id"],
                "comparable_country_count": context["comparable_country_count"],
                "excluded_projection_count": context["excluded_projection_count"],
                "result": "一致",
                "scope": "同期参照候选与冻结投影集；不代表生产或最终验收",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
