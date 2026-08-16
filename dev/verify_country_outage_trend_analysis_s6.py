#!/usr/bin/env python3
"""验证 S6 最终同候选闭环、确定性与 Agent Value Gate。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.country_outage_trend_product import (  # noqa: E402
    answer_trend_question_v1,
    compile_contemporaneous_reference_v1,
    compile_trend_product_v1,
    validate_evidence_graph_v1,
)


FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s6-v1.json"
SCHEMA_PATH = REPOSITORY_ROOT / "contracts" / "agent" / "country-outage-trend-analysis-s6-v1.schema.json"
S5_VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s5.py"
DOC_PATH = REPOSITORY_ROOT / "docs" / "国家中断趋势分析S6最终验收报告.md"


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


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def build_candidate() -> dict[str, Any]:
    return load_module("s5_verifier_for_s6", S5_VERIFIER_PATH).build_candidate()


def build_candidate_with_projection_order(seed: int) -> dict[str, Any]:
    s5 = load_module(f"s5_verifier_for_s6_seed_{seed}", S5_VERIFIER_PATH)
    s4 = load_module(
        f"s4_verifier_for_s6_seed_{seed}", s5.S4_VERIFIER_PATH
    ).build_candidate()
    request = s5.build_reference_input(s4["profile"])
    random.Random(seed).shuffle(request["projections"])
    reference = compile_contemporaneous_reference_v1(s4["profile"], request)
    return compile_trend_product_v1(
        s4["profile"],
        address_family_context=s4["contexts"]["address_family"],
        asn_context=s4["contexts"]["asn"],
        activity_context=s4["contexts"]["activity"],
        contemporaneous_reference_context=reference,
    )


def _claim_nodes(product: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        node
        for node in product["evidence_graph"]["nodes"]
        if node.get("node_type") == "Claim"
    ]


def _fact_map(product: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["metric"]: item
        for item in product["profile"]["analysis"]["derived_facts"]
    }


def _verify_invariance(
    fixture: dict[str, Any], product: dict[str, Any], errors: list[str]
) -> None:
    expected = canonical_json(product)
    invariance = fixture["invariance"]
    for _ in range(invariance["sequential_repeat_count"]):
        if canonical_json(build_candidate()) != expected:
            errors.append("相同冻结输入的顺序执行不能生成逐字节相同制品。")
            break
    for seed in range(invariance["projection_order_seed_count"]):
        if canonical_json(build_candidate_with_projection_order(seed)) != expected:
            errors.append(f"同期投影输入顺序影响制品：seed={seed}。")
            break

    seeds = [index % invariance["projection_order_seed_count"] for index in range(invariance["concurrent_run_count"])]
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(build_candidate_with_projection_order, seeds))
    if any(canonical_json(item) != expected for item in results):
        errors.append("并发执行不能生成逐字节相同制品。")


def _verify_numeric_facts(
    fixture: dict[str, Any], product: dict[str, Any], errors: list[str]
) -> None:
    expected = fixture["numeric_expectations"]
    slots = product["profile"]["slots"]
    values = [slot["value"] for slot in slots]
    analysis = product["profile"]["analysis"]
    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    actual = {
        "start_value": values[0],
        "extreme_value": min(values),
        "end_value": values[-1],
        "largest_drop_end_index": 1 + min(range(len(deltas)), key=deltas.__getitem__),
        "largest_drop_delta": min(deltas),
        "loss_magnitude": values[0] - min(values),
        "rebound": values[-1] - min(values),
        "end_residual": values[0] - values[-1],
        "gap_integral": sum(max(values[0] - value, 0) for value in values),
    }
    for field, value in actual.items():
        if value != expected[field]:
            errors.append(f"冻结数值不可复算：{field}={value}。")

    facts = _fact_map(product)
    expected_facts = {
        "loss_magnitude": (28, "start_value - extreme_value"),
        "extreme_to_end_rebound": (12, "end_value - extreme_value"),
        "end_residual_from_start": (16, "start_value - end_value"),
        "fixed_cohort_visibility_gap_integral": (
            196,
            "sum(max(fixed_cohort_value - slot_value, 0))",
        ),
    }
    for metric, (value, formula) in expected_facts.items():
        fact = facts.get(metric) or {}
        if fact.get("value") != value or fact.get("formula") != formula or not fact.get("operands"):
            errors.append(f"派生事实的值、公式或操作数漂移：{metric}。")

    threshold_counts = {
        str(item["threshold_visible_ratio"]): item["observed_slot_count"]
        for item in analysis["window_ledger"]["threshold_slots"]
    }
    if threshold_counts != expected["threshold_slot_counts"]:
        errors.append("阈值槽位计数不可复算。")

    phases = analysis["phases"]
    if phases[0]["start_slot_index"] != 0 or phases[-1]["end_slot_index"] != len(slots) - 1:
        errors.append("阶段边界没有覆盖完整窗口。")
    for previous, current in zip(phases, phases[1:]):
        if previous["end_slot_index"] + 1 != current["start_slot_index"]:
            errors.append("阶段边界不连续或重叠。")
            break


def _verify_graph_and_qa(
    fixture: dict[str, Any], product: dict[str, Any], errors: list[str]
) -> dict[str, float]:
    graph = product["evidence_graph"]
    errors.extend(validate_evidence_graph_v1(graph))
    claims = _claim_nodes(product)
    if any(
        not claim.get("evidence_refs")
        or not claim.get("limitation_refs")
        or not claim.get("unknown_refs")
        for claim in claims
    ):
        errors.append("存在未完整绑定 Evidence、Limitation、Unknown 的 Claim。")
    if graph.get("snapshot") != product.get("snapshot"):
        errors.append("Evidence Graph 与趋势制品没有绑定同一快照。")
    if graph.get("hypothesis_nodes_allowed") is not False or graph.get("causal_relations_allowed") is not False:
        errors.append("Evidence Graph 越界允许 Hypothesis 或因果关系。")

    core_answers = [answer_trend_question_v1(product, item["question"]) for item in fixture["core_tasks"]]
    for task, answer in zip(fixture["core_tasks"], core_answers):
        if answer["status"] != "answered" or answer["operator"] != task["expected_operator"]:
            errors.append(f"核心任务未完成：{task['id']}。")
        if not answer.get("claim_refs") or not answer.get("evidence_refs") or not answer.get("limitation_refs") or not answer.get("unknown_refs"):
            errors.append(f"核心任务没有完整证据导航：{task['id']}。")
        if answer.get("product_id") != product["product_id"]:
            errors.append(f"核心任务未绑定同一 product_id：{task['id']}。")

    refused = [answer_trend_question_v1(product, item["question"]) for item in fixture["refusal_cases"]]
    for case, answer in zip(fixture["refusal_cases"], refused):
        if answer["status"] != "abstained" or answer["operator"] != "evidence_boundary":
            errors.append(f"越界问题未正确弃答：{case['id']}。")
        if not answer.get("limitation_refs") or not answer.get("unknown_refs"):
            errors.append(f"弃答没有说明限制与未知：{case['id']}。")

    composition_answers = [
        answer_trend_question_v1(product, item["question"])
        for item in fixture["unfamiliar_composition_tasks"]
    ]
    for task, answer in zip(fixture["unfamiliar_composition_tasks"], composition_answers):
        if answer["status"] != "answered" or answer["operator"] != task["operator"]:
            errors.append(f"陌生组合问题未由冻结算子覆盖：{task['id']}。")

    total = len(fixture["unfamiliar_composition_tasks"])
    b0 = sum(bool(item["b0"]) for item in fixture["unfamiliar_composition_tasks"]) / total
    b1 = sum(bool(item["b1"]) for item in fixture["unfamiliar_composition_tasks"]) / total
    b2 = sum(answer["status"] == "answered" for answer in composition_answers) / total
    metrics = {
        "b0_coverage": b0,
        "b1_coverage": b1,
        "b2_coverage": b2,
        "numeric_accuracy": 1.0,
        "phase_boundary_consistency": 1.0,
        "claim_evidence_consistency": 1.0,
        "correct_abstention_rate": sum(answer["status"] == "abstained" for answer in refused) / len(refused),
        "evidence_coverage": sum(bool(answer.get("evidence_refs")) for answer in core_answers) / len(core_answers),
        "core_task_completion": sum(answer["status"] == "answered" for answer in core_answers) / len(core_answers),
    }
    gate = fixture["value_gate"]
    expected_metrics = {
        "b0_coverage": gate["b0_expected"],
        "b1_coverage": gate["b1_expected"],
        "b2_coverage": gate["b2_expected"],
        "numeric_accuracy": gate["numeric_accuracy_minimum"],
        "phase_boundary_consistency": gate["phase_boundary_consistency_minimum"],
        "claim_evidence_consistency": gate["claim_evidence_consistency_minimum"],
        "correct_abstention_rate": gate["correct_abstention_rate_minimum"],
        "evidence_coverage": gate["evidence_coverage_minimum"],
        "core_task_completion": gate["core_task_completion_minimum"],
    }
    for metric, expected in expected_metrics.items():
        if metrics[metric] != expected:
            errors.append(f"Value Gate 未通过：{metric}={metrics[metric]} != {expected}。")
    return metrics


def validate(*, require_final_document: bool = True) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    fixture = load_json(FIXTURE_PATH)
    schema = load_json(SCHEMA_PATH)
    product = build_candidate()

    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("S6 验收 Schema 必须使用 JSON Schema 2020-12。")
    if schema.get("properties", {}).get("schema_version", {}).get("const") != fixture["schema_version"]:
        errors.append("S6 fixture 与 Schema 版本没有互相冻结。")
    tae_ids = [item["id"] for item in fixture["tae_matrix"]]
    if tae_ids != [f"TAE-{index:02d}" for index in range(1, 16)]:
        errors.append("TAE-01..TAE-15 没有完整且有序冻结。")
    surfaces = [item["surface"] for item in fixture["surface_contract"]]
    if surfaces != ["data", "api", "page", "report", "qa", "download", "value"]:
        errors.append("七个同候选输出面没有完整冻结。")
    boundaries = fixture["boundaries"]
    if boundaries != {
        "collector_ids": ["rrc25"],
        "control_plane_only": True,
        "hypothesis_allowed": False,
        "causal_claim_allowed": False,
        "user_impact_claim_allowed": False,
        "external_evidence_allowed": False,
        "production_deployed": False,
    }:
        errors.append("S6 最终边界漂移。")
    if product["snapshot"]["collector_id"] != "rrc25" or product["snapshot"]["collector_count"] != 1:
        errors.append("最终候选不是单一 RRC25 快照。")
    if product["render_contract"]["source_product_id"] != product["product_id"]:
        errors.append("输出面未绑定同一 product_id。")

    _verify_invariance(fixture, product, errors)
    _verify_numeric_facts(fixture, product, errors)
    metrics = _verify_graph_and_qa(fixture, product, errors)

    if require_final_document:
        if not DOC_PATH.exists():
            errors.append("缺少 S6 最终验收报告。")
        else:
            document = DOC_PATH.read_text(encoding="utf-8")
            for phrase in (
                "TAE-01..TAE-15",
                product["product_id"],
                product["graph_id"],
                canonical_sha256(product),
                "B0 = 0%",
                "B1 = 25%",
                "B2 = 100%",
                "RRC25 BGP 控制面",
                "Evidence Graph v1",
                "正确弃答",
                "浏览器证据",
                "不是生产部署",
            ):
                if phrase not in document:
                    errors.append(f"S6 最终验收报告缺少：{phrase}。")

    return errors, {
        "product": product,
        "canonical_sha256": canonical_sha256(product),
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-product", action="store_true")
    parser.add_argument("--without-final-document", action="store_true")
    arguments = parser.parse_args()
    if arguments.emit_product:
        print(canonical_json(build_candidate()))
        return 0
    errors, result = validate(require_final_document=not arguments.without_final_document)
    if errors:
        print(json.dumps({"status": "failed", "errors": errors}, ensure_ascii=False))
        return 1
    product = result["product"]
    print(
        json.dumps(
            {
                "status": "passed",
                "stage": "S6",
                "product_id": product["product_id"],
                "graph_id": product["graph_id"],
                "canonical_sha256": result["canonical_sha256"],
                "metrics": result["metrics"],
                "result": "已修正",
                "scope": "同一候选的数据、API、页面、报告、QA、下载与价值证据；不代表生产部署",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
