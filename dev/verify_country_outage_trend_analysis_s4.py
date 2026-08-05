#!/usr/bin/env python3
"""验证 S4 Evidence Graph、同制品阅读面与有界组合问答。"""

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
    EVIDENCE_GRAPH_SCHEMA_VERSION,
    QA_RULE_VERSION,
    TREND_PRODUCT_SCHEMA_VERSION,
    answer_trend_question_v1,
    compile_trend_product_v1,
    validate_evidence_graph_v1,
)


S0_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s0-v1.json"
S1_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s1-v1.json"
S3_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s3-v1.json"
S4_FIXTURE_PATH = REPOSITORY_ROOT / "dev" / "fixtures" / "country-outage-trend-analysis-s4-v1.json"
S1_VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s1.py"
S3_VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s3.py"
SCHEMA_PATH = REPOSITORY_ROOT / "contracts" / "agent" / "country-outage-evidence-graph-v1.schema.json"
DOC_PATH = REPOSITORY_ROOT / "docs" / "国家中断趋势分析S4验收记录.md"


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


def build_candidate() -> dict[str, Any]:
    s0 = load_json(S0_FIXTURE_PATH)
    s1 = load_json(S1_FIXTURE_PATH)
    s3 = load_json(S3_FIXTURE_PATH)
    s4 = load_json(S4_FIXTURE_PATH)
    s1_verifier = load_module("s1_verifier_for_s4", S1_VERIFIER_PATH)
    s3_verifier = load_module("s3_verifier_for_s4", S3_VERIFIER_PATH)
    profile = s3_verifier.analyzed_curve(
        s0,
        s1,
        s1_verifier,
        s4["profile_curve_id"],
    )
    ipv4, ipv6 = s3_verifier.build_address_family_profiles(
        s0, s1, s3, s1_verifier
    )
    from services.country_outage_trend_profile import (  # noqa: E402
        align_activity_context_v1,
        compare_address_families_v1,
        compile_asn_state_context_v1,
    )

    return compile_trend_product_v1(
        profile,
        address_family_context=compare_address_families_v1(ipv4, ipv6),
        asn_context=compile_asn_state_context_v1(
            profile, s3_verifier.build_asn_rows(s3)
        ),
        activity_context=align_activity_context_v1(
            profile, s3_verifier.build_activity_tracks(s3)
        ),
    )


def validate() -> list[str]:
    errors: list[str] = []
    fixture = load_json(S4_FIXTURE_PATH)
    schema = load_json(SCHEMA_PATH)
    product = build_candidate()
    expected = fixture["expected"]
    graph = product["evidence_graph"]

    if product.get("schema_version") != TREND_PRODUCT_SCHEMA_VERSION:
        errors.append("S4 趋势制品 schema_version 不正确。")
    if graph.get("schema_version") != EVIDENCE_GRAPH_SCHEMA_VERSION:
        errors.append("S4 Evidence Graph schema_version 不正确。")
    errors.extend(validate_evidence_graph_v1(graph))

    node_types = sorted({node["node_type"] for node in graph["nodes"]})
    if node_types != sorted(expected["allowed_node_types"]):
        errors.append("Evidence Graph 节点类型不是固定白名单。")
    relation_types = sorted({edge["relation"] for edge in graph["edges"]})
    if relation_types != sorted(expected["allowed_relation_types"]):
        errors.append("Evidence Graph 关系类型不是固定白名单。")
    claim_kinds = [
        node["claim_kind"]
        for node in graph["nodes"]
        if node["node_type"] == "Claim"
    ]
    if claim_kinds != expected["claim_kinds"]:
        errors.append("S4 Claim 集合或顺序漂移。")
    if graph.get("hypothesis_nodes_allowed") is not False:
        errors.append("Evidence Graph 没有禁止 Hypothesis。")
    if graph.get("causal_relations_allowed") is not False:
        errors.append("Evidence Graph 没有禁止因果关系。")
    if product["render_contract"]["surfaces"] != expected["required_surfaces"]:
        errors.append("页面、报告、QA 与下载没有声明共同制品来源。")
    if product["render_contract"]["source_product_id"] != product["product_id"]:
        errors.append("输出面没有绑定同一 product_id。")
    if product.get("qa_rule_version") != QA_RULE_VERSION:
        errors.append("QA 规则版本不正确。")

    answers = [
        answer_trend_question_v1(product, item["text"])
        for item in fixture["questions"]
    ]
    for case, answer in zip(fixture["questions"], answers):
        if answer["status"] != case["status"] or answer["operator"] != case["operator"]:
            errors.append(f"问答结果漂移：{case['text']}")
        if answer["product_id"] != product["product_id"]:
            errors.append(f"问答未绑定同一 product_id：{case['text']}")
    abstained = [item for item in answers if item["status"] == "abstained"]
    if len(abstained) != expected["correct_abstention_count"]:
        errors.append("正确弃答数量不符合冻结验收集。")
    for answer in abstained:
        if not answer["limitation_refs"] or not answer["unknown_refs"]:
            errors.append("弃答没有同时返回 Limitation 与 Unknown。")
    answered_operators = {
        item["operator"] for item in answers if item["status"] == "answered"
    }
    missing = set(expected["answer_operators"]) - answered_operators
    # evidence_navigation 的“为什么”必须优先进入原因边界，因此单独用“查看依据”验证。
    evidence_answer = answer_trend_question_v1(product, "查看结论依据")
    if evidence_answer["operator"] != "evidence_navigation" or evidence_answer["status"] != "answered":
        errors.append("证据导航算子不可用。")
    else:
        missing.discard("evidence_navigation")
    if missing:
        errors.append("缺少白名单问答算子：" + ", ".join(sorted(missing)))

    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("Evidence Graph Schema 必须使用 JSON Schema 2020-12。")
    edge_relation = (
        schema.get("properties", {})
        .get("edges", {})
        .get("items", {})
        .get("properties", {})
        .get("relation", {})
        .get("enum")
    )
    if edge_relation != expected["allowed_relation_types"]:
        errors.append("Schema 没有冻结关系白名单。")
    if schema.get("properties", {}).get("hypothesis_nodes_allowed", {}).get("const") is not False:
        errors.append("Schema 没有禁止 Hypothesis。")

    document = DOC_PATH.read_text(encoding="utf-8")
    for phrase in (
        "Evidence Graph v1",
        "Claim、Evidence、Limitation、Unknown",
        "不包含 Hypothesis",
        "页面、报告、QA、Markdown、PDF 与 JSON 下载",
        "正确弃答",
        "TAE-12",
        "TAE-13",
        "TAE-14",
        "不是生产部署",
    ):
        if phrase not in document:
            errors.append(f"S4 验收记录缺少语义：{phrase}")

    repeated = build_candidate()
    if repeated != product:
        errors.append("同一冻结输入不能重复生成相同 S4 制品。")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print(json.dumps({"status": "failed", "errors": errors}, ensure_ascii=False))
        return 1
    product = build_candidate()
    print(
        json.dumps(
            {
                "status": "passed",
                "stage": "S4",
                "product_id": product["product_id"],
                "graph_id": product["graph_id"],
                "claim_count": len(product["claim_ids"]),
                "result": "一致",
                "scope": "候选确定性制品与冻结问题集；不代表生产或最终验收",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

