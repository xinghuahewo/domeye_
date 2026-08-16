#!/usr/bin/env python3
"""校验国家中断 Agent P0 v1.3 能力普查 revision。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REVISION = "p0-v1.3-20260809-ir-r1"
ROOT = REPOSITORY_ROOT / "evaluation" / "country-outage" / "p0-v1-3"
EXPECTED_MATURITY = {
    "page_visible": 13,
    "api_published": 1,
    "backend_available_unpublished": 0,
    "deterministically_derivable_unproductized": 3,
    "semantics_unfrozen": 0,
    "unavailable": 3,
    "out_of_scope": 4,
    "unknown": 2,
}
EXPECTED_DISPOSITIONS = {"adopt": 17, "defer": 5, "reject": 4}
EXPECTED_CATEGORIES = {"direct": 20, "multi_turn": 5, "boundary": 5, "exception": 5}
EXPECTED_ARTIFACT_ROLES = {
    "live_evidence",
    "system_surface",
    "capability_ledger",
    "unknown_ledger",
    "oracle_seed",
    "case_set",
    "p1_disposition",
    "stage_receipts",
    "execution_contract",
    "discovery_report",
    "p1_receipt",
    "discovery_probe",
    "deterministic_validator",
    "stage_hook",
    "regression_tests",
}
DIMENSION_KEYS = {
    "source_exists",
    "tests_exist",
    "runtime_includes",
    "data_available",
    "api_published",
    "page_consumes",
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法读取 JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON 顶层必须是对象：{path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{label} 必须是非空仓库相对路径")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"{label} 越出仓库：{value!r}")
    path = (REPOSITORY_ROOT / relative).resolve()
    try:
        path.relative_to(REPOSITORY_ROOT)
    except ValueError as error:
        raise RuntimeError(f"{label} 越出仓库：{value!r}") from error
    return path


def validate_live(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if evidence.get("schema_version") != "country_outage_p0_capability_evidence_v1":
        errors.append("live evidence schema_version 无效")
    if evidence.get("revision") != REVISION or evidence.get("probe_mode") != "read_only":
        errors.append("live evidence revision 或 probe_mode 无效")
    identity = evidence.get("identity")
    expected_identity = {
        "publication_id": "country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f",
        "collector_id": "rrc25",
        "observation_state": "evidence_complete",
        "quality_state": "complete",
        "missing_slot_count": 0,
        "lifecycle_state": "event_end_unknown",
        "is_final_in_data_range": False,
    }
    if not isinstance(identity, dict):
        return errors + ["live evidence 缺少 identity"]
    for key, expected in expected_identity.items():
        if identity.get(key) != expected:
            errors.append(f"live identity.{key} 漂移")
    series = evidence.get("series")
    if not isinstance(series, dict):
        errors.append("live evidence 缺少 series")
    else:
        for key, expected in {
            "point_count": 3455,
            "timestamp_count": 3455,
            "track_count": 15,
            "all_track_lengths": 3455,
            "all_track_null_counts": 0,
        }.items():
            if series.get(key) != expected:
                errors.append(f"live series.{key} 应为 {expected}")
        ipv4 = (series.get("selected_extrema") or {}).get(
            "fixed_visible_ipv4_address_count"
        )
        if not isinstance(ipv4, dict) or ipv4.get("max_to_min_drop") != 579072:
            errors.append("live IPv4 最大下降参考真值漂移")
    probes = evidence.get("api_probes")
    expected_statuses = {
        "overview": 200,
        "series": 200,
        "asns": 200,
        "path_downstreams": 200,
        "audit": 200,
        "trend": 404,
        "wrong_publication": 404,
        "invalid_asn_query": 400,
    }
    if not isinstance(probes, dict):
        errors.append("live evidence 缺少 api_probes")
    else:
        for name, expected in expected_statuses.items():
            probe = probes.get(name)
            if not isinstance(probe, dict) or probe.get("status") != expected:
                errors.append(f"api probe {name} 状态漂移")
            elif not isinstance(probe.get("response_sha256"), str) or len(
                probe["response_sha256"]
            ) != 64:
                errors.append(f"api probe {name} 缺少响应摘要")
    external = evidence.get("external_evidence")
    if not isinstance(external, dict) or external.get("state") != "not_configured":
        errors.append("外部证据运行态必须记录为 not_configured")
    page = evidence.get("page")
    if not isinstance(page, dict):
        errors.append("live evidence 缺少页面观测")
    else:
        requested = {
            item.get("name")
            for item in page.get("actual_xhr", [])
            if isinstance(item, dict)
        }
        if requested != {"health", "resolve", "overview", "series", "asns", "path_downstreams"}:
            errors.append("页面实际 XHR 集合漂移")
        if "报告与追问工作台" not in page.get("not_visible", []):
            errors.append("必须显式记录当前 general page 未显示报告与追问")
    return errors


def validate_ledger(ledger: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if ledger.get("schema_version") != "country_outage_capability_discovery_ledger_v1":
        errors.append("Capability Ledger schema_version 无效")
    if ledger.get("revision") != REVISION:
        errors.append("Capability Ledger revision 无效")
    capabilities = ledger.get("capabilities")
    if not isinstance(capabilities, list):
        return errors + ["Capability Ledger 缺少 capabilities"]
    ids = [item.get("capability_id") for item in capabilities if isinstance(item, dict)]
    expected_ids = [f"CAP-{index:03d}" for index in range(1, 27)]
    if ids != expected_ids:
        errors.append("Capability ID 必须严格为 CAP-001..CAP-026")
    maturities = Counter(
        item.get("maturity") for item in capabilities if isinstance(item, dict)
    )
    if dict(maturities) != {key: value for key, value in EXPECTED_MATURITY.items() if value}:
        errors.append(f"能力成熟度计数漂移：{dict(maturities)}")
    dispositions = Counter(
        item.get("p1_disposition") for item in capabilities if isinstance(item, dict)
    )
    if dict(dispositions) != EXPECTED_DISPOSITIONS:
        errors.append(f"能力处置计数漂移：{dict(dispositions)}")
    for index, capability in enumerate(capabilities):
        if not isinstance(capability, dict):
            errors.append(f"capabilities[{index}] 必须是对象")
            continue
        capability_id = capability.get("capability_id", f"capabilities[{index}]")
        for key in (
            "user_outcome",
            "maturity",
            "evidence_dimensions",
            "evidence_refs",
            "inputs",
            "outputs",
            "semantics",
            "failure_behavior",
            "limitations",
            "prohibited_claims",
            "feasibility",
            "p1_disposition",
        ):
            if key not in capability:
                errors.append(f"{capability_id} 缺少 {key}")
        dimensions = capability.get("evidence_dimensions")
        if not isinstance(dimensions, dict) or set(dimensions) != DIMENSION_KEYS:
            errors.append(f"{capability_id} evidence_dimensions 不完整")
            continue
        maturity = capability.get("maturity")
        if maturity == "page_visible" and dimensions.get("page_consumes") is not True:
            errors.append(f"{capability_id} page_visible 但 page_consumes 不为 true")
        if maturity == "api_published" and (
            dimensions.get("api_published") is not True
            or dimensions.get("runtime_includes") is not True
        ):
            errors.append(f"{capability_id} api_published 缺少合同或运行证据")
        if maturity == "deterministically_derivable_unproductized" and (
            dimensions.get("api_published") is not False
            or dimensions.get("page_consumes") is not False
        ):
            errors.append(f"{capability_id} feasibility 被越级写成产品能力")
    if ledger.get("summary") != {
        "total": 26,
        **EXPECTED_MATURITY,
        "p1_adopt": 17,
        "p1_defer": 5,
        "p1_reject": 4,
    }:
        errors.append("Capability Ledger summary 漂移")
    return errors


def validate_surface(surface: dict[str, Any], capability_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if surface.get("schema_version") != "country_outage_system_surface_v1":
        errors.append("system surface schema_version 无效")
    entries = surface.get("surfaces")
    if not isinstance(entries, list):
        return errors + ["system surface 缺少 surfaces"]
    ids = [entry.get("surface_id") for entry in entries if isinstance(entry, dict)]
    if len(ids) != len(set(ids)):
        errors.append("system surface ID 重复")
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("system surface 项必须是对象")
            continue
        for capability_id in entry.get("capability_ids", []):
            if capability_id not in capability_ids:
                errors.append(f"{entry.get('surface_id')} 引用未知能力 {capability_id}")
        if not entry.get("evidence_refs") or not entry.get("limitations"):
            errors.append(f"{entry.get('surface_id')} 缺少证据或限制")
    coverage = surface.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("declared_surface_count") != len(entries):
        errors.append("system surface declared count 与实际不一致")
    elif coverage.get("covered_surface_count") != len(entries) or coverage.get(
        "silent_omission_count"
    ) != 0:
        errors.append("system surface 未达到显式覆盖")
    return errors


def validate_unknowns(ledger: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if ledger.get("schema_version") != "country_outage_capability_unknown_ledger_v1":
        errors.append("unknown ledger schema_version 无效")
    unknowns = ledger.get("unknowns")
    if not isinstance(unknowns, list) or len(unknowns) != 8:
        return errors + ["unknown ledger 必须有 8 项显式 unknown"]
    ids = [item.get("unknown_id") for item in unknowns if isinstance(item, dict)]
    if ids != [f"UNK-{index:03d}" for index in range(1, 9)]:
        errors.append("unknown ID 必须为 UNK-001..UNK-008")
    for item in unknowns:
        if not isinstance(item, dict):
            errors.append("unknown 项必须是对象")
            continue
        for key in (
            "subject",
            "known",
            "unknown",
            "risk",
            "next_validation",
            "owner",
            "blocks",
            "does_not_block",
        ):
            if not item.get(key):
                errors.append(f"{item.get('unknown_id')} 缺少 {key}")
    summary = ledger.get("summary")
    if summary != {"total": 8, "has_owner": 8, "has_next_validation": 8, "silent_unknown_count": 0}:
        errors.append("unknown ledger summary 漂移")
    return errors


def validate_oracle(oracle: dict[str, Any], capability_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if oracle.get("schema_version") != "country_outage_p0_oracle_seed_v1":
        errors.append("oracle seed schema_version 无效")
    if oracle.get("status") != "seed_only_not_tool_registry":
        errors.append("oracle seed 必须声明非 Tool Registry")
    seeds = oracle.get("seeds")
    if not isinstance(seeds, list) or len(seeds) != 10:
        return errors + ["oracle seed 必须严格为 10 项"]
    if [item.get("oracle_id") for item in seeds if isinstance(item, dict)] != [
        f"ORC-{index:02d}" for index in range(1, 11)
    ]:
        errors.append("oracle ID 必须为 ORC-01..ORC-10")
    for seed in seeds:
        if not isinstance(seed, dict):
            errors.append("oracle 项必须是对象")
            continue
        for capability_id in seed.get("capability_ids", []):
            if capability_id not in capability_ids:
                errors.append(f"{seed.get('oracle_id')} 引用未知能力 {capability_id}")
        if not seed.get("evidence_refs") or not seed.get("result"):
            errors.append(f"{seed.get('oracle_id')} 缺少证据或结果")
    by_id = {item["oracle_id"]: item for item in seeds if isinstance(item, dict)}
    if by_id.get("ORC-02", {}).get("expected", {}).get("maximum") != 3855:
        errors.append("ORC-02 峰值真值漂移")
    ipv4 = by_id.get("ORC-03", {}).get("expected", {}).get("ipv4", {})
    if ipv4.get("drop") != 579072:
        errors.append("ORC-03 IPv4 差值真值漂移")
    if by_id.get("ORC-07", {}).get("expected", {}).get("capability_state") != "unavailable":
        errors.append("ORC-07 必须记录趋势当前不可用")
    timeline = by_id.get("ORC-09", {}).get("expected", {})
    nodes = timeline.get("ordered_fact_nodes")
    if not isinstance(nodes, list) or len(nodes) < 6:
        errors.append("ORC-09 缺少完整有序事实节点")
    else:
        times = [node.get("at_utc") for node in nodes if isinstance(node, dict)]
        if times != sorted(times) or any(not node.get("evidence_ref") for node in nodes):
            errors.append("ORC-09 事实节点顺序或 evidence ref 无效")
    if timeline.get("terminal_unknown") != {
        "lifecycle_state": "event_end_unknown",
        "event_end_at_utc": None,
    } or timeline.get("causal_edges") != "forbidden":
        errors.append("ORC-09 未保留未知结束状态或禁止因果边")
    as_peaks = by_id.get("ORC-10", {}).get("expected", {})
    if as_peaks.get("affected_asn_count") != {
        "maximum": 350,
        "maximum_at_utc": "2026-03-02T11:30:00Z",
        "last": 121,
        "unit": "asn",
    } or as_peaks.get("route_interrupted_asn_count") != {
        "maximum": 94,
        "maximum_at_utc": "2026-02-28T14:35:00Z",
        "last": 35,
        "unit": "asn",
    }:
        errors.append("ORC-10 两条 AS 时序峰值或时点漂移")
    population = as_peaks.get("population_boundary")
    if not isinstance(population, dict) or population.get("affected_as_count") != 525 or population.get("route_interrupted_as_count") != 151 or as_peaks.get("same_peak_time") is not False:
        errors.append("ORC-10 未区分逐槽峰值、累计人口或峰值时点")
    return errors


def validate_disposition(
    disposition: dict[str, Any], capability_ids: set[str]
) -> list[str]:
    errors: list[str] = []
    if disposition.get("schema_version") != "country_outage_p0_to_p1_disposition_v1":
        errors.append("P1 disposition schema_version 无效")
    partitions: dict[str, list[dict[str, Any]]] = {}
    for name, expected in EXPECTED_DISPOSITIONS.items():
        items = disposition.get(name)
        if not isinstance(items, list) or len(items) != expected:
            errors.append(f"P1 {name} 数量应为 {expected}")
            partitions[name] = []
        else:
            partitions[name] = items
    seen = [
        item.get("capability_id")
        for items in partitions.values()
        for item in items
        if isinstance(item, dict)
    ]
    if set(seen) != capability_ids or len(seen) != len(set(seen)):
        errors.append("P1 adopt/defer/reject 未对全部能力形成互斥处置")
    if disposition.get("counts") != {**EXPECTED_DISPOSITIONS, "total": 26}:
        errors.append("P1 disposition counts 漂移")
    if not disposition.get("p1_must_fail_closed"):
        errors.append("P1 disposition 缺少失败关闭清单")
    return errors


def validate_cases(case_set: dict[str, Any], capability_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if case_set.get("schema_version") != "country_outage_p0_capability_case_set_v1":
        errors.append("P0 v1.3 case set schema_version 无效")
    if case_set.get("revision") != REVISION:
        errors.append("P0 v1.3 case set revision 无效")
    base_contract = case_set.get("base_case_set")
    if not isinstance(base_contract, dict):
        return errors + ["case set 缺少 base_case_set"]
    try:
        base_path = safe_path(base_contract.get("path"), "base_case_set.path")
    except RuntimeError as error:
        return errors + [str(error)]
    if not base_path.is_file() or sha256(base_path) != base_contract.get("sha256"):
        errors.append("不可变 base case set 摘要不一致")
        return errors
    base_cases_value = read_json(base_path).get("cases")
    if not isinstance(base_cases_value, list):
        return errors + ["base case set 无效"]
    base_cases = {
        item.get("case_id"): item for item in base_cases_value if isinstance(item, dict)
    }
    cases = case_set.get("cases")
    if not isinstance(cases, list) or len(cases) != 35:
        return errors + ["P0 v1.3 case set 必须严格为 35 项"]
    expected_ids = (
        [f"P013-D-{index:02d}" for index in range(1, 21)]
        + [f"P013-M-{index:02d}" for index in range(1, 6)]
        + [f"P013-B-{index:02d}" for index in range(1, 6)]
        + [f"P013-X-{index:02d}" for index in range(1, 6)]
    )
    if [item.get("case_id") for item in cases if isinstance(item, dict)] != expected_ids:
        errors.append("P0 v1.3 case ID 或顺序漂移")
    counts = Counter(item.get("category") for item in cases if isinstance(item, dict))
    if dict(counts) != EXPECTED_CATEGORIES:
        errors.append(f"P0 v1.3 case 分类数量漂移：{dict(counts)}")
    if case_set.get("expected_counts") != {**EXPECTED_CATEGORIES, "total": 35}:
        errors.append("P0 v1.3 expected_counts 漂移")
    by_new_id = {
        item.get("case_id"): item for item in cases if isinstance(item, dict)
    }
    for case in cases:
        if not isinstance(case, dict):
            errors.append("case 必须是对象")
            continue
        case_id = case.get("case_id")
        base_case = base_cases.get(case.get("base_case_id"))
        if not isinstance(base_case, dict):
            errors.append(f"{case_id} 引用未知 base case")
            continue
        for key in ("category", "coverage_key", "question"):
            if case.get(key) != base_case.get(key):
                errors.append(f"{case_id} {key} 与不可变 base case 不一致")
        if case.get("expected_mode") != base_case.get("answerability"):
            errors.append(f"{case_id} expected_mode 与 base answerability 不一致")
        for key in ("user_goal", "capability_ids", "evidence_refs", "hard_gates"):
            value = case.get(key)
            if not value:
                errors.append(f"{case_id} 缺少 {key}")
        for capability_id in case.get("capability_ids", []):
            if capability_id not in capability_ids:
                errors.append(f"{case_id} 引用未知能力 {capability_id}")
    as_scope = by_new_id.get("P013-D-08", {})
    as_facts = as_scope.get("additional_expected_facts")
    if not isinstance(as_facts, list) or {
        (item.get("metric"), item.get("maximum"), item.get("maximum_at_utc"))
        for item in as_facts
        if isinstance(item, dict)
    } != {
        ("affected_asn_count", 350, "2026-03-02T11:30:00Z"),
        ("route_interrupted_asn_count", 94, "2026-02-28T14:35:00Z"),
    }:
        errors.append("P013-D-08 未真实覆盖 CAP-005 两条 AS 时序峰值")
    nationwide = by_new_id.get("P013-B-01", {})
    if set(nationwide.get("capability_ids", [])) != {"CAP-003", "CAP-004", "CAP-023"}:
        errors.append("P013-B-01 未同时映射可观测事实与用户影响边界")
    if nationwide.get("subgoal_policy") != {
        "observable_rrc25_state": "answer",
        "nationwide_or_user_connectivity": "unsupported",
    } or "observable_subgoal_answered" not in nationwide.get("hard_gates", []):
        errors.append("P013-B-01 未逐子目标执行 partial 语义")
    return errors


def validate_stage_receipts(receipts: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if receipts.get("schema_version") != "country_outage_p0_v1_3_stage_receipts_v1":
        errors.append("stage receipts schema_version 无效")
    stages = receipts.get("stages")
    if not isinstance(stages, list) or [stage.get("stage") for stage in stages] != [
        "S0",
        "S1",
        "S2",
        "S3",
    ]:
        return errors + ["stage receipts 必须完整覆盖 S0-S3"]
    for stage in stages:
        if stage.get("status") != "completed":
            errors.append(f"{stage.get('stage')} 未标记 completed")
        if not stage.get("evidence_refs") or not stage.get("exit_conclusion"):
            errors.append(f"{stage.get('stage')} 缺少阶段证据或出口结论")
    return errors


def validate_documents() -> list[str]:
    errors: list[str] = []
    documents = {
        "docs/agent/P0-需求与评测/P0-v1.3-系统能力普查执行合同.md": [
            "证据层与判定规则",
            "源码、测试、数据或 feasibility",
        ],
        "docs/agent/P0-需求与评测/P0-v1.3-系统能力普查报告.md": [
            "系统表面结论",
            "Capability Discovery Ledger",
            "不能推出",
        ],
        "docs/agent/P0-需求与评测/P0-v1.3-P1入口回执.md": [
            "adopt",
            "defer",
            "reject",
            "不是 Tool Registry",
        ],
    }
    for relative, required in documents.items():
        path = REPOSITORY_ROOT / relative
        if not path.is_file():
            errors.append(f"缺少文档 {relative}")
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in required:
            if phrase not in text:
                errors.append(f"文档 {relative} 缺少关键表述：{phrase}")
    return errors


def validate_manifest(path: Path) -> list[str]:
    errors: list[str] = []
    manifest = read_json(path)
    if manifest.get("schema_version") != "country_outage_p0_v1_3_manifest_v1":
        errors.append("manifest schema_version 无效")
    if manifest.get("revision") != REVISION:
        errors.append("manifest revision 无效")
    if manifest.get("case_counts") != {**EXPECTED_CATEGORIES, "total": 35}:
        errors.append("manifest case_counts 漂移")
    if manifest.get("capability_counts") != {
        "total": 26,
        **EXPECTED_MATURITY,
        "p1_adopt": 17,
        "p1_defer": 5,
        "p1_reject": 4,
    }:
        errors.append("manifest capability_counts 漂移")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return errors + ["manifest 缺少 artifacts"]
    roles = {item.get("role") for item in artifacts if isinstance(item, dict)}
    if roles != EXPECTED_ARTIFACT_ROLES or len(artifacts) != len(roles):
        errors.append("manifest artifact roles 不完整或重复")
    for item in artifacts:
        if not isinstance(item, dict):
            errors.append("manifest artifact 必须是对象")
            continue
        try:
            artifact_path = safe_path(item.get("path"), f"artifact {item.get('role')}")
        except RuntimeError as error:
            errors.append(str(error))
            continue
        if not artifact_path.is_file():
            errors.append(f"manifest artifact 不存在：{artifact_path}")
        elif sha256(artifact_path) != item.get("sha256"):
            errors.append(f"manifest artifact 摘要漂移：{item.get('path')}")
    return errors


def load_all() -> dict[str, dict[str, Any]]:
    return {
        "live": read_json(ROOT / "evidence" / "live-probe-20260809.json"),
        "surface": read_json(ROOT / "system-surface.json"),
        "ledger": read_json(ROOT / "capability-ledger.json"),
        "unknowns": read_json(ROOT / "unknown-ledger.json"),
        "oracle": read_json(ROOT / "oracle-seed.json"),
        "cases": read_json(ROOT / "cases.json"),
        "disposition": read_json(ROOT / "p1-disposition.json"),
        "receipts": read_json(ROOT / "stage-receipts.json"),
    }


def validate_stage(stage: str) -> list[str]:
    values = load_all()
    capability_ids = {
        item.get("capability_id")
        for item in values["ledger"].get("capabilities", [])
        if isinstance(item, dict)
    }
    errors: list[str] = []
    if stage in {"S0", "S1", "S2", "S3"}:
        errors.extend(validate_live(values["live"]))
        errors.extend(validate_surface(values["surface"], capability_ids))
        errors.extend(validate_unknowns(values["unknowns"]))
    if stage in {"S1", "S2", "S3"}:
        errors.extend(validate_ledger(values["ledger"]))
        errors.extend(validate_oracle(values["oracle"], capability_ids))
        errors.extend(validate_disposition(values["disposition"], capability_ids))
    if stage in {"S2", "S3"}:
        errors.extend(validate_cases(values["cases"], capability_ids))
    if stage == "S3":
        errors.extend(validate_stage_receipts(values["receipts"]))
        errors.extend(validate_documents())
        errors.extend(validate_manifest(ROOT / "manifest.json"))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", nargs="?", default=str(ROOT / "manifest.json"))
    arguments = parser.parse_args()
    errors = validate_stage("S3")
    manifest_path = Path(arguments.manifest)
    if manifest_path.resolve() != (ROOT / "manifest.json").resolve():
        errors.append("只允许校验固定 P0 v1.3 manifest")
    if errors:
        print("P0 v1.3 校验失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("P0 v1.3 校验通过：16 个系统表面、26 项能力、8 项 unknown、10 个 Oracle seed、35 个案例。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
