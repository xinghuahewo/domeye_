#!/usr/bin/env python3
"""校验国家中断 Agent P1 runtime-v2 同候选合同、阶段证据与 manifest。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPOSITORY_ROOT / "contracts" / "agent" / "country-outage-p1-runtime-v2"
EVALUATION_ROOT = REPOSITORY_ROOT / "evaluation" / "country-outage" / "p1-runtime-v2"
P0_ROOT = REPOSITORY_ROOT / "evaluation" / "country-outage" / "p0-v1-3"
P0_REVISION = "p0-v1.3-20260809-ir-r1"
SELECTED_IDS = {f"CAP-{value:03d}" for value in range(1, 15)} | {
    "CAP-016", "CAP-017", "CAP-018"
}
DEFERRED_IDS = {"CAP-015", "CAP-019", "CAP-020", "CAP-025", "CAP-026"}
REJECTED_IDS = {"CAP-021", "CAP-022", "CAP-023", "CAP-024"}
CATEGORIES = {"normal", "missing", "null", "wrong_identity", "unavailable", "boundary"}
EXECUTION_UNITS = {
    "TOOL-01", "TOOL-02", "TOOL-03", "TOOL-04", "TOOL-05", "TOOL-06",
    "OP-01", "OP-02", "OP-03",
}
EXPECTED_SUPPORTED_CAPABILITIES = {
    "CAP-001": {"resolution=available"},
    "CAP-002": {"overview=available"},
    "CAP-003": {"overview=available"},
    "CAP-004": {"overview=available"},
    "CAP-005": {"overview=available"},
    "CAP-006": {"event_series=available"},
    "CAP-007": {"event_series=available"},
    "CAP-008": {"event_series=available"},
    "CAP-009": {"event_series=available"},
    "CAP-010": {"affected_as=available"},
    "CAP-011": {"affected_as=available"},
    "CAP-012": {"path_downstreams=available"},
    "CAP-013": {"path_downstreams=available", "full_path_evidence=audit_only"},
    "CAP-014": {"full_path_evidence=audit_only"},
    "CAP-016": {"inherits_registered_source_capability"},
    "CAP-017": {"event_series=available"},
    "CAP-018": {"overview=available", "event_series=available"},
}
EXPECTED_PARTIAL_CAPABILITIES = {
    "CAP-001": set(),
    "CAP-002": {"overview=available"},
    "CAP-003": {"overview=available"},
    "CAP-004": set(),
    "CAP-005": set(),
    "CAP-006": set(),
    "CAP-007": set(),
    "CAP-008": {"event_series=available"},
    "CAP-009": set(),
    "CAP-010": set(),
    "CAP-011": set(),
    "CAP-012": {"path_downstreams=available"},
    "CAP-013": {"path_downstreams=available"},
    "CAP-014": {"full_path_evidence=audit_only"},
    "CAP-016": set(),
    "CAP-017": {"event_series=available"},
    "CAP-018": {"overview=available"},
}
TOOL_FIELDS = {
    "unit_id", "name", "kind", "purpose", "capability_ids", "source_operation", "preconditions",
    "input_schema", "output_schema", "units", "time_semantics", "pagination",
    "null_semantics", "errors", "permission", "timeout_ms", "evidence_refs",
    "forbidden_uses",
}
REGISTERED_METRICS = {
    "interrupted_prefix_count", "completely_interrupted_prefix_count",
    "invisible_direction_count", "affected_asn_count",
    "route_interrupted_asn_count", "fixed_visible_ipv4_address_count",
    "fixed_visible_ipv6_slash48_count", "new_cumulative_ipv4_prefix_count",
    "new_cumulative_ipv4_address_count", "new_cumulative_ipv6_prefix_count",
    "new_cumulative_ipv6_slash48_count", "new_visible_ipv4_prefix_count",
    "new_visible_ipv4_address_count", "new_visible_ipv6_prefix_count",
    "new_visible_ipv6_slash48_count",
}
REGISTERED_METRIC_UNITS = {
    "interrupted_prefix_count": "prefix",
    "completely_interrupted_prefix_count": "prefix",
    "invisible_direction_count": "peer_asn_direction",
    "affected_asn_count": "asn",
    "route_interrupted_asn_count": "asn",
    "fixed_visible_ipv4_address_count": "unique_ipv4_address",
    "fixed_visible_ipv6_slash48_count": "ipv6_slash48_equivalent",
    "new_cumulative_ipv4_prefix_count": "prefix",
    "new_cumulative_ipv4_address_count": "unique_ipv4_address",
    "new_cumulative_ipv6_prefix_count": "prefix",
    "new_cumulative_ipv6_slash48_count": "ipv6_slash48_equivalent",
    "new_visible_ipv4_prefix_count": "prefix",
    "new_visible_ipv4_address_count": "unique_ipv4_address",
    "new_visible_ipv6_prefix_count": "prefix",
    "new_visible_ipv6_slash48_count": "ipv6_slash48_equivalent",
}
HOST_INVARIANTS = {f"GND-{index:02d}" for index in range(1, 14)}
ORACLE_CASE_FIELDS = {
    "case_id", "fixture_id", "adapter_id", "adapter_args", "input", "expected",
    "evidence_refs", "unit", "state_commit", "boundary_assertion",
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
    target = (REPOSITORY_ROOT / relative).resolve()
    try:
        target.relative_to(REPOSITORY_ROOT)
    except ValueError as error:
        raise RuntimeError(f"{label} 越出仓库：{value!r}") from error
    return target


def _path_parent(value: Any, dotted_path: str) -> tuple[Any, str | int]:
    parts = dotted_path.split(".")
    if not dotted_path or any(not part for part in parts):
        raise RuntimeError(f"无效 adapter path：{dotted_path!r}")
    current = value
    for part in parts[:-1]:
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as error:
                raise RuntimeError(f"adapter path 不存在：{dotted_path}") from error
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise RuntimeError(f"adapter path 不存在：{dotted_path}")
    last: str | int = parts[-1]
    if isinstance(current, list):
        try:
            last = int(last)
        except ValueError as error:
            raise RuntimeError(f"adapter 数组索引无效：{dotted_path}") from error
        if last < 0 or last >= len(current):
            raise RuntimeError(f"adapter path 不存在：{dotted_path}")
    elif not isinstance(current, dict) or last not in current:
        raise RuntimeError(f"adapter path 不存在：{dotted_path}")
    return current, last


def apply_oracle_adapter(
    fixture: dict[str, Any], adapter_operation: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """把 Oracle adapter 真正应用到冻结最小 fixture，供 S0/S1 共用。"""
    value = copy.deepcopy(fixture)
    if adapter_operation == "identity":
        return value
    if adapter_operation == "set_path_null" and isinstance(arguments.get("mutations"), list):
        mutations = arguments["mutations"]
        if not mutations:
            raise RuntimeError("adapter mutations 不能为空")
        for mutation in mutations:
            if not isinstance(mutation, dict) or "path" not in mutation or "value" not in mutation:
                raise RuntimeError("adapter mutation 必须包含 path/value")
            parent, key = _path_parent(value, mutation["path"])
            parent[key] = mutation["value"]
        return value
    if adapter_operation in {"omit_path", "set_path_null"}:
        parent, key = _path_parent(value, arguments.get("path", ""))
        if adapter_operation == "omit_path":
            if isinstance(parent, list):
                parent.pop(key)
            else:
                del parent[key]
        else:
            parent[key] = None
        return value
    if adapter_operation == "replace_publication_identity":
        value["_identity_override"] = {
            "publication_id": arguments.get("publication_id"),
            "revision": arguments.get("revision"),
        }
        return value
    if adapter_operation == "set_event_capability_unavailable":
        value["_capability_override"] = {
            "capability": arguments.get("capability"),
            "state": arguments.get("state"),
        }
        return value
    if adapter_operation == "validate_rejected_input_without_execution":
        value["_boundary_rejection"] = {"reason": arguments.get("reason")}
        return value
    raise RuntimeError(f"未知 Oracle adapter operation：{adapter_operation}")


def run_series_extrema(request: dict[str, Any]) -> dict[str, Any]:
    """执行 OP-01 的确定性微型实现；Oracle 比较的是实际输出而非静态摘要。"""
    metric = request.get("metric")
    timestamps = request.get("timestamps")
    values = request.get("values")
    unit = request.get("unit")
    if metric not in REGISTERED_METRIC_UNITS:
        return {"status": "unsupported", "error": "unknown_metric"}
    if unit != REGISTERED_METRIC_UNITS[metric]:
        return {"status": "invalid_data", "error": "unit_mismatch"}
    if (
        not isinstance(timestamps, list)
        or not isinstance(values, list)
        or not timestamps
        or len(timestamps) != len(values)
        or request.get("tie_policy") != "first_observed_occurrence"
    ):
        return {"status": "invalid_data", "error": "invalid_series_shape"}
    observed = [
        (index, value)
        for index, value in enumerate(values)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not observed:
        return {"status": "invalid_data", "error": "empty_observed_set"}
    minimum = min(value for _, value in observed)
    maximum = max(value for _, value in observed)
    minimum_index = next(index for index, value in observed if value == minimum)
    maximum_index = next(index for index, value in observed if value == maximum)
    return {
        "status": "supported",
        "facts": {
            "source_identity": request.get("source_identity"),
            "metric": metric,
            "first": observed[0][1],
            "last": observed[-1][1],
            "minimum": minimum,
            "minimum_at_utc": timestamps[minimum_index],
            "maximum": maximum,
            "maximum_at_utc": timestamps[maximum_index],
            "difference": maximum - minimum,
            "unit": unit,
            "source_evidence_refs": request.get("source_evidence_refs"),
        },
    }


def run_address_family_compare(request: dict[str, Any]) -> dict[str, Any]:
    """执行 OP-02，保留两种地址族的不同单位，绝不构造合计值。"""
    ipv4 = request.get("ipv4_extrema")
    ipv6 = request.get("ipv6_extrema")
    available = [item for item in (ipv4, ipv6) if isinstance(item, dict)]
    if not available:
        return {"status": "unsupported", "error": "capability_unavailable"}
    if len(available) == 1:
        side = "ipv4" if isinstance(ipv4, dict) else "ipv6"
        item = available[0]
        return {
            "status": "partial",
            "error": "missing_input",
            "facts": {
                "source_identity": item.get("source_identity"),
                f"{side}_drop": item.get("difference"),
                f"{side}_unit": item.get("unit"),
                "comparison": "separate_units_only",
                "combined_absolute_total": "forbidden",
                "source_evidence_refs": item.get("source_evidence_refs"),
            },
        }
    if ipv4.get("source_identity") != ipv6.get("source_identity"):
        return {"status": "invalid_data", "error": "identity_conflict"}
    if (
        ipv4.get("metric") != "fixed_visible_ipv4_address_count"
        or ipv4.get("unit") != "unique_ipv4_address"
        or ipv6.get("metric") != "fixed_visible_ipv6_slash48_count"
        or ipv6.get("unit") != "ipv6_slash48_equivalent"
    ):
        return {"status": "invalid_data", "error": "unit_mismatch"}
    evidence_refs = list(dict.fromkeys(
        list(ipv4.get("source_evidence_refs") or [])
        + list(ipv6.get("source_evidence_refs") or [])
    ))
    return {
        "status": "supported",
        "facts": {
            "source_identity": ipv4.get("source_identity"),
            "ipv4_drop": ipv4.get("difference"),
            "ipv4_unit": ipv4.get("unit"),
            "ipv6_drop": ipv6.get("difference"),
            "ipv6_unit": ipv6.get("unit"),
            "comparison": "separate_units_only",
            "combined_absolute_total": "forbidden",
            "source_evidence_refs": evidence_refs,
        },
    }


def _verified_fact_shape_valid(fact: object) -> bool:
    if not isinstance(fact, dict):
        return False
    kind, value, unit = fact.get("kind"), fact.get("value"), fact.get("unit")
    if (
        not isinstance(fact.get("at_utc"), str)
        or not isinstance(fact.get("source_identity"), dict)
        or not isinstance(fact.get("evidence_ref"), str)
    ):
        return False
    if kind in {"window_start", "detected", "event_end"}:
        return isinstance(value, str) and unit == "UTC"
    if kind == "data_through":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and unit == "prefix"
    if kind in {"interrupted_prefix_peak", "completely_interrupted_prefix_peak"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and unit == "prefix"
    if kind == "invisible_direction_peak":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and unit == "peer_asn_direction"
    if kind in {"affected_asn_peak", "route_interrupted_asn_peak"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and unit == "asn"
    return False


def _verified_fact_evidence_valid(fact: dict[str, Any], evidence_map: dict[str, Any]) -> bool:
    source_identity = evidence_map.get("source_identity")
    entries = evidence_map.get("entries")
    evidence = entries.get(fact.get("evidence_ref")) if isinstance(entries, dict) else None
    return bool(
        isinstance(source_identity, dict)
        and fact.get("source_identity") == source_identity
        and isinstance(evidence, dict)
        and isinstance(evidence.get("source_node_id"), str)
        and evidence["source_node_id"]
        and all(evidence.get(field) == fact.get(field) for field in ("kind", "at_utc", "value", "unit"))
    )


def run_fact_timeline(request: dict[str, Any], evidence_map: dict[str, Any]) -> dict[str, Any]:
    """执行 OP-03，并把事实类型、身份、生命周期与 evidence_ref 当作硬门。"""
    if request.get("lifecycle_state") != "event_end_unknown":
        return {"status": "invalid_data", "error": "lifecycle_state_mismatch"}
    identity_nodes = request.get("identity_nodes")
    peak_nodes = request.get("peak_nodes")
    data_through_node = request.get("data_through_node")
    required_kinds = request.get("required_kinds")
    if (
        not isinstance(identity_nodes, list)
        or not isinstance(peak_nodes, list)
        or not isinstance(data_through_node, dict)
        or not isinstance(required_kinds, list)
    ):
        return {"status": "invalid_data", "error": "parameter_schema_invalid"}
    nodes = identity_nodes + peak_nodes + [data_through_node]
    invalid_shape = [fact for fact in nodes if not _verified_fact_shape_valid(fact)]
    if invalid_shape:
        return {"status": "invalid_data", "error": "parameter_schema_invalid"}
    invalid_evidence = [fact for fact in nodes if not _verified_fact_evidence_valid(fact, evidence_map)]
    if invalid_evidence:
        entries = evidence_map.get("entries", {})
        unresolved = any(
            isinstance(fact, dict) and fact.get("evidence_ref") not in entries
            for fact in invalid_evidence
        )
        return {
            "status": "invalid_data",
            "error": "evidence_reference_not_resolved" if unresolved else "evidence_value_conflict",
        }
    identities = {json.dumps(fact["source_identity"], sort_keys=True) for fact in nodes}
    if len(identities) != 1:
        return {"status": "invalid_data", "error": "identity_conflict"}
    ordered = sorted(nodes, key=lambda fact: (fact["at_utc"], fact["fact_id"]))
    projected_nodes = [
        {
            "kind": fact["kind"],
            "at_utc": fact["at_utc"],
            "value": fact["value"],
            "unit": fact["unit"],
            "evidence_ref": fact["evidence_ref"],
        }
        for fact in ordered
    ]
    facts = {
        "source_identity": ordered[0]["source_identity"],
        "ordered_fact_nodes": projected_nodes,
        "terminal_unknown": {"reason": "event_end_unknown", "event_end_at_utc": None},
        "causal_edges": "forbidden",
    }
    if not set(required_kinds) <= {fact["kind"] for fact in ordered}:
        return {"status": "partial", "error": "timeline_node_missing", "facts": facts}
    return {"status": "supported", "facts": facts}


def execute_operator_oracle(capability_id: str, fixture: dict[str, Any]) -> dict[str, Any]:
    """执行一次已应用 adapter 的算子 Oracle，并返回可与 expected 精确比较的结果。"""
    if "_identity_override" in fixture:
        return {"status": "invalid_data", "error": "identity_conflict"}
    boundary = fixture.get("_boundary_rejection", {}).get("reason")
    if boundary:
        error_by_reason = {
            "extrema_not_recovery": "extrema_cannot_establish_recovery",
            "unit_mismatch": "unit_mismatch",
            "timeline_has_no_causal_edges": "timeline_has_no_causal_edges",
        }
        return {"status": "unsupported", "error": error_by_reason.get(boundary, boundary)}
    unavailable = fixture.get("_capability_override", {})
    if unavailable.get("state") == "unavailable":
        if capability_id == "CAP-018" and unavailable.get("capability") == "event_series":
            request = copy.deepcopy(fixture.get("execution_request", {}))
            request["peak_nodes"] = [
                node for node in request.get("peak_nodes", [])
                if isinstance(node, dict) and not str(node.get("evidence_ref", "")).startswith("series.")
            ]
            result = run_fact_timeline(request, fixture.get("evidence_map", {}))
            if result.get("status") == "partial":
                result["error"] = "peak_nodes_unavailable"
            return result
        return {"status": "unsupported", "error": "capability_unavailable"}
    request = fixture.get("execution_request")
    if not isinstance(request, dict):
        return {"status": "invalid_data", "error": "parameter_schema_invalid"}
    if capability_id == "CAP-016":
        return run_series_extrema(request)
    if capability_id == "CAP-017":
        return run_address_family_compare(request)
    if capability_id == "CAP-018":
        return run_fact_timeline(request, fixture.get("evidence_map", {}))
    raise RuntimeError(f"非确定性算子能力不可由 execute_operator_oracle 执行：{capability_id}")


def _local_schema(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any] | None:
    reference = schema.get("$ref")
    prefix = "#/$defs/"
    if not isinstance(reference, str) or not reference.startswith(prefix):
        return None
    value = document.get("$defs", {}).get(reference.removeprefix(prefix))
    return value if isinstance(value, dict) else None


def _coverage_by_id(coverage: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        item["capability_id"]: item
        for item in coverage
        if isinstance(item, dict) and isinstance(item.get("capability_id"), str)
    }


def validate_s0(
    catalog: dict[str, Any],
    tools: dict[str, Any],
    oracle: dict[str, Any],
    schema: dict[str, Any],
    policy: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    p0_disposition = read_json(P0_ROOT / "p1-disposition.json")
    p0_unknowns = read_json(P0_ROOT / "unknown-ledger.json")
    p0_oracle = read_json(P0_ROOT / "oracle-seed.json")
    p0_cases = read_json(P0_ROOT / "cases.json")
    p0_live = read_json(P0_ROOT / "evidence" / "live-probe-20260809.json")

    if catalog.get("schema_version") != "country_outage_p1_runtime_capability_catalog_v2":
        errors.append("Capability Catalog schema_version 无效")
    if catalog.get("p0_entry_revision") != P0_REVISION:
        errors.append("Capability Catalog P0 入口 revision 漂移")
    counts = catalog.get("counts")
    if counts != {"selected": 17, "deferred": 5, "rejected": 4, "unknown": 8}:
        errors.append("Capability Catalog 计数必须为 17/5/4/8")
    selected = catalog.get("selected")
    deferred = catalog.get("deferred")
    rejected = catalog.get("rejected")
    unknowns = catalog.get("unknowns")
    if not all(isinstance(value, list) for value in (selected, deferred, rejected, unknowns)):
        return errors + ["Capability Catalog 分区必须是数组"]
    selected_ids = {item.get("capability_id") for item in selected if isinstance(item, dict)}
    deferred_ids = {item.get("capability_id") for item in deferred if isinstance(item, dict)}
    rejected_ids = {item.get("capability_id") for item in rejected if isinstance(item, dict)}
    if selected_ids != SELECTED_IDS:
        errors.append(f"selected 能力漂移：{sorted(selected_ids)}")
    if deferred_ids != DEFERRED_IDS:
        errors.append(f"deferred 能力漂移：{sorted(deferred_ids)}")
    if rejected_ids != REJECTED_IDS:
        errors.append(f"rejected 能力漂移：{sorted(rejected_ids)}")
    if selected_ids | deferred_ids | rejected_ids != {
        f"CAP-{value:03d}" for value in range(1, 27)
    }:
        errors.append("Capability Catalog 未覆盖 CAP-001..CAP-026")
    if selected_ids & deferred_ids or selected_ids & rejected_ids or deferred_ids & rejected_ids:
        errors.append("Capability Catalog 分区不互斥")
    p0_selected = {item["capability_id"] for item in p0_disposition["adopt"]}
    p0_deferred = {item["capability_id"] for item in p0_disposition["defer"]}
    p0_rejected = {item["capability_id"] for item in p0_disposition["reject"]}
    if (selected_ids, deferred_ids, rejected_ids) != (p0_selected, p0_deferred, p0_rejected):
        errors.append("Capability Catalog 与 P0 v1.3 disposition 不一致")
    required_capability_fields = {
        "capability_id", "user_outcome", "execution_unit", "goal_kinds",
        "answer_modes", "required_for_supported", "sufficient_for_partial", "evidence_sources",
    }
    for item in selected:
        if not isinstance(item, dict):
            errors.append("selected 能力项必须为对象")
            continue
        missing = required_capability_fields - set(item)
        if missing:
            errors.append(f"{item.get('capability_id')} 缺少字段 {sorted(missing)}")
        if item.get("execution_unit") not in EXECUTION_UNITS:
            errors.append(f"{item.get('capability_id')} 引用未知执行单元")
        if not item.get("user_outcome") or not item.get("goal_kinds") or not item.get("evidence_sources"):
            errors.append(f"{item.get('capability_id')} 用户结果、目标或证据为空")
        capability_id = item.get("capability_id")
        if set(item.get("required_for_supported", [])) != EXPECTED_SUPPORTED_CAPABILITIES.get(capability_id):
            errors.append(f"{capability_id} supported 事件级能力协商条件漂移")
        if set(item.get("sufficient_for_partial", [])) != EXPECTED_PARTIAL_CAPABILITIES.get(capability_id):
            errors.append(f"{capability_id} partial 事件级能力协商条件漂移")
    for item in deferred + rejected:
        if not isinstance(item, dict) or not item.get("reason"):
            errors.append("defer/reject 能力必须保留非空处置原因")
    p0_unknown_ids = {item["unknown_id"] for item in p0_unknowns["unknowns"]}
    unknown_ids = {item.get("unknown_id") for item in unknowns if isinstance(item, dict)}
    if unknown_ids != p0_unknown_ids:
        errors.append("Runtime unknown 清单与 P0 8 项 unknown 不一致")
    for item in unknowns:
        if not isinstance(item, dict) or not item.get("owner") or not item.get("next_validation") or not item.get("runtime_effect"):
            errors.append(f"{item.get('unknown_id') if isinstance(item, dict) else 'unknown'} 缺少 owner/next_validation/runtime_effect")

    if tools.get("schema_version") != "country_outage_p1_typed_tool_contract_v2":
        errors.append("Typed Tool Contract schema_version 无效")
    units = tools.get("execution_units")
    if not isinstance(units, list):
        return errors + ["Typed Tool Contract 缺少 execution_units"]
    unit_ids = {item.get("unit_id") for item in units if isinstance(item, dict)}
    if unit_ids != EXECUTION_UNITS or len(units) != len(EXECUTION_UNITS):
        errors.append("执行单元必须恰好为 6 个只读 Tool 和 3 个确定性算子")
    kind_counts = {
        kind: sum(1 for item in units if isinstance(item, dict) and item.get("kind") == kind)
        for kind in ("read_tool", "deterministic_operator")
    }
    if kind_counts != {"read_tool": 6, "deterministic_operator": 3}:
        errors.append(f"执行单元类型计数错误：{kind_counts}")
    tool_capabilities: set[str] = set()
    openapi = read_json(REPOSITORY_ROOT / "contracts" / "openapi.json")
    openapi_schemas = openapi.get("components", {}).get("schemas", {})
    for unit in units:
        if not isinstance(unit, dict):
            errors.append("执行单元必须为对象")
            continue
        missing = TOOL_FIELDS - set(unit)
        if missing:
            errors.append(f"{unit.get('unit_id')} Typed Tool Contract 缺少 {sorted(missing)}")
        capabilities = unit.get("capability_ids")
        if not isinstance(capabilities, list) or not capabilities:
            errors.append(f"{unit.get('unit_id')} capability_ids 为空")
        else:
            tool_capabilities.update(capabilities)
            if not set(capabilities) <= selected_ids:
                errors.append(f"{unit.get('unit_id')} 引用非 selected 能力")
        for field in ("preconditions", "errors", "evidence_refs", "forbidden_uses"):
            if not isinstance(unit.get(field), list) or not unit[field]:
                errors.append(f"{unit.get('unit_id')} {field} 不完整")
        if not isinstance(unit.get("timeout_ms"), int) or unit["timeout_ms"] <= 0:
            errors.append(f"{unit.get('unit_id')} timeout_ms 无效")
        input_schema = unit.get("input_schema")
        output_schema = unit.get("output_schema")
        if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
            errors.append(f"{unit.get('unit_id')} 输入输出必须是机器 Schema")
        elif "$ref" in output_schema and unit.get("kind") == "read_tool":
            reference = output_schema["$ref"]
            prefix = "../../openapi.json#/components/schemas/"
            if not isinstance(reference, str) or not reference.startswith(prefix):
                errors.append(f"{unit.get('unit_id')} OpenAPI $ref 无效")
            elif reference.removeprefix(prefix) not in openapi_schemas:
                errors.append(f"{unit.get('unit_id')} OpenAPI $ref 不存在")
        effective_input_schema = (
            _local_schema(tools, input_schema) if isinstance(input_schema, dict) else None
        ) or input_schema
        if isinstance(effective_input_schema, dict) and effective_input_schema.get("additionalProperties") is not False:
            errors.append(f"{unit.get('unit_id')} 输入参数必须 additionalProperties=false")
        if unit.get("kind") == "deterministic_operator":
            if not isinstance(input_schema, dict) or _local_schema(tools, input_schema) is None:
                errors.append(f"{unit.get('unit_id')} 输入必须引用封闭本地算子 Schema")
            if not isinstance(output_schema, dict) or _local_schema(tools, output_schema) is None:
                errors.append(f"{unit.get('unit_id')} 输出必须引用封闭本地算子 Schema")
    if tool_capabilities != selected_ids:
        errors.append("Typed Tool Contract 未覆盖全部 17 个 selected 能力")
    for item in selected:
        unit = next((entry for entry in units if entry.get("unit_id") == item.get("execution_unit")), None)
        if not unit or item["capability_id"] not in unit.get("capability_ids", []):
            errors.append(f"{item['capability_id']} Capability→执行单元双向映射不闭合")
    registered_metrics = tools.get("registered_series_metrics")
    if registered_metrics != REGISTERED_METRIC_UNITS:
        errors.append("Typed Tool Contract 必须逐项登记正式 15 条 series metric")
    cap009 = next((item for item in selected if item.get("capability_id") == "CAP-009"), {})
    if cap009.get("execution_unit") != "TOOL-03" or cap009.get("evidence_sources") != ["series"]:
        errors.append("CAP-009 必须由真实 series.track_definitions 支撑")
    definitions = tools.get("$defs")
    required_operator_definitions = {
        "sourceIdentity", "registeredMetricUnitPair", "seriesExtremaInput",
        "seriesExtremaResult", "addressFamilyComparisonInput",
        "addressFamilyComparisonResult", "verifiedFactBase", "verifiedFact", "factTimelineInput",
        "factTimelineResult",
    }
    if not isinstance(definitions, dict) or not required_operator_definitions <= set(definitions):
        errors.append("确定性算子缺少封闭的身份、极值、比较或事实节点 Schema")
    else:
        metric_pairs = definitions["registeredMetricUnitPair"].get("oneOf", [])
        pairs = {
            (
                item.get("properties", {}).get("metric", {}).get("const"),
                item.get("properties", {}).get("unit", {}).get("const"),
            )
            for item in metric_pairs
            if isinstance(item, dict)
        }
        if pairs != set(REGISTERED_METRIC_UNITS.items()):
            errors.append("OP-01 metric 与 unit 映射未逐项封闭")
        extrema_required = set(definitions["seriesExtremaResult"].get("required", []))
        if not {"source_identity", "source_evidence_refs"} <= extrema_required:
            errors.append("seriesExtremaResult 未闭合 source identity 与 evidence")
        fact_base = definitions["verifiedFactBase"]
        fact_required = set(fact_base.get("required", []))
        fact_variants = definitions["verifiedFact"].get("oneOf", [])
        if (
            not {"source_identity", "evidence_ref", "kind", "value", "unit", "at_utc"} <= fact_required
            or len(fact_variants) != 5
            or fact_base.get("properties", {}).get("evidence_ref", {}).get("pattern")
            != "^(resolution|overview|series|derived)\\."
        ):
            errors.append("verifiedFact 未按 kind 闭合 value/unit/source identity/evidence")
        timeline_input = definitions["factTimelineInput"]
        timeline_result = definitions["factTimelineResult"]
        if (
            set(timeline_input.get("required", []))
            != {"identity_nodes", "peak_nodes", "data_through_node", "required_kinds", "lifecycle_state"}
            or timeline_input.get("properties", {}).get("lifecycle_state", {}).get("const") != "event_end_unknown"
            or timeline_result.get("properties", {}).get("terminal_unknown", {}).get("properties", {}).get("reason", {}).get("const") != "event_end_unknown"
        ):
            errors.append("OP-03 lifecycle 与 required fact kinds 未闭合")
    tool05 = next((unit for unit in units if unit.get("unit_id") == "TOOL-05"), {})
    expected_sample_ref = "../../openapi.json#/components/schemas/CountryOutageGeneralPathDownstreamItemV1/properties/path_samples"
    if tool05.get("row_sample_contract", {}).get("$ref") != expected_sample_ref:
        errors.append("TOOL-05 row_sample_contract JSON Pointer 无效")

    if oracle.get("schema_version") != "country_outage_p1_full_oracle_v2":
        errors.append("Full Oracle schema_version 无效")
    if set(oracle.get("categories", [])) != CATEGORIES:
        errors.append("Full Oracle 必须覆盖六类样例")
    fixture_path_value = oracle.get("fixture_registry")
    try:
        fixture_path = safe_path(fixture_path_value, "oracle.fixture_registry")
        fixtures_document = read_json(fixture_path)
    except RuntimeError as error:
        errors.append(str(error))
        fixtures_document = {}
    fixtures = fixtures_document.get("fixtures", {})
    if not isinstance(fixtures, dict) or not fixtures:
        errors.append("Oracle fixture registry 为空")
        fixtures = {}
    endpoint_responses = fixtures_document.get("source", {}).get("endpoint_responses", {})
    expected_response_ids = {
        "resolve", "overview", "series", "asns_48715",
        "paths_49666_concurrent", "audit",
    }
    if not isinstance(endpoint_responses, dict) or set(endpoint_responses) != expected_response_ids:
        errors.append("Oracle fixture 未逐 endpoint 登记查询参数与响应摘要")
    else:
        for response_id, response in endpoint_responses.items():
            if (
                not isinstance(response, dict)
                or not response.get("endpoint")
                or not isinstance(response.get("query"), dict)
                or not isinstance(response.get("response_sha256"), str)
                or len(response["response_sha256"]) != 64
            ):
                errors.append(f"Oracle fixture provenance {response_id} 不完整")
    provenance_refs = {
        fixture.get("provenance_ref")
        for fixture in fixtures.values()
        if isinstance(fixture, dict)
    }
    if provenance_refs != expected_response_ids:
        errors.append("每个 Oracle fixture 必须绑定已登记的实际 endpoint/query/sha")
    series_fixture = fixtures.get("FX-SERIES", {})
    track_definitions = series_fixture.get("track_definitions", {})
    if not isinstance(track_definitions, dict) or set(track_definitions) != REGISTERED_METRICS:
        errors.append("FX-SERIES 必须保存完整 15 条 track definition")
    else:
        for metric, definition in track_definitions.items():
            if (
                not isinstance(definition, dict)
                or not definition.get("label")
                or not definition.get("definition")
                or definition.get("unit") != REGISTERED_METRIC_UNITS[metric]
            ):
                errors.append(f"FX-SERIES {metric} label/definition/unit 与正式轨道不闭合")
    track_samples = series_fixture.get("track_samples", {})
    expected_track_samples = {
        "fixed_visible_ipv4_address_count": (10156800, 10069760, "unique_ipv4_address"),
        "fixed_visible_ipv6_slash48_count": (267292, 267288, "ipv6_slash48_equivalent"),
    }
    for metric, (first, last, unit) in expected_track_samples.items():
        sample = track_samples.get(metric, {})
        if sample != {
            "point_count": 3455, "timestamp_count": 3455, "track_length": 3455,
            "first": first, "last": last, "unit": unit,
        }:
            errors.append(f"FX-SERIES {metric} 原始时序投影漂移")
    series_response_sha = endpoint_responses.get("series", {}).get("response_sha256")
    if series_response_sha != p0_live.get("api_probes", {}).get("series", {}).get("response_sha256"):
        errors.append("算子微型 fixture 与 P0 live series 响应身份不一致")
    op01_request = fixtures.get("FX-OP01-ROUTE", {}).get("execution_request", {})
    expected_op01_sample = {
        "timestamps": [
            "2026-02-27T00:10:00Z", "2026-02-28T13:45:00Z",
            "2026-02-28T13:50:00Z", "2026-02-28T13:55:00Z",
            "2026-03-11T00:00:00Z",
        ],
        "values": [0, 92, 94, 94, 35],
    }
    if (
        op01_request.get("timestamps") != expected_op01_sample["timestamps"]
        or op01_request.get("values") != expected_op01_sample["values"]
        or op01_request.get("metric") != "route_interrupted_asn_count"
        or series_response_sha != "45700171b9cef9c41eeaa6e124c1f0920b57dd544be7e00d45b3c7c0706925d6"
    ):
        errors.append("FX-OP01-ROUTE 与同 SHA live series 的真实采样值冲突")
    op02_request = fixtures.get("FX-OP02-ADDRESS", {}).get("execution_request", {})
    p0_extrema = p0_live.get("series", {}).get("selected_extrema", {})
    for side, metric in (
        ("ipv4_extrema", "fixed_visible_ipv4_address_count"),
        ("ipv6_extrema", "fixed_visible_ipv6_slash48_count"),
    ):
        extrema = op02_request.get(side, {})
        truth = p0_extrema.get(metric, {})
        if any(
            extrema.get(field) != truth.get(truth_field)
            for field, truth_field in (
                ("first", "first"), ("last", "last"), ("minimum", "minimum"),
                ("minimum_at_utc", "minimum_at_utc"), ("maximum", "maximum"),
                ("difference", "max_to_min_drop"), ("unit", "unit"),
            )
        ):
            errors.append(f"FX-OP02-ADDRESS {metric} 与 P0 live extrema 冲突")
    op03_fixture = fixtures.get("FX-OP03-TIMELINE", {})
    op03_request = op03_fixture.get("execution_request", {})
    evidence_map = op03_fixture.get("evidence_map", {})
    evidence_entries = evidence_map.get("entries", {})
    op03_nodes = (
        list(op03_request.get("identity_nodes", []))
        + list(op03_request.get("peak_nodes", []))
        + ([op03_request.get("data_through_node")] if isinstance(op03_request.get("data_through_node"), dict) else [])
    )
    if (
        not isinstance(evidence_entries, dict)
        or len(evidence_entries) != 6
        or any(
            not isinstance(evidence_entries.get(node.get("evidence_ref")), dict)
            or not evidence_entries[node["evidence_ref"]].get("source_node_id")
            or any(
                evidence_entries[node["evidence_ref"]].get(field) != node.get(field)
                for field in ("kind", "at_utc", "value", "unit")
            )
            for node in op03_nodes
        )
        or any(node.get("source_identity") != evidence_map.get("source_identity") for node in op03_nodes)
    ):
        errors.append("FX-OP03-TIMELINE evidence map 未逐字段绑定值、时点、单位、身份与 source node")
    asn_item = ((fixtures.get("FX-ASNS", {}).get("items") or [{}])[0])
    expected_asn_projection = {
        "asn": 48715, "as_name": "SEFROYEKPARDAZENG-AS", "name_state": "observed",
        "path_downstream_asn_count": 1, "concurrent_downstream_asn_count": 1,
    }
    if any(asn_item.get(key) != value for key, value in expected_asn_projection.items()):
        errors.append("FX-ASNS 与同 publication AS48715 投影不一致")
    audit_fixture = fixtures.get("FX-AUDIT", {})
    if len(audit_fixture.get("source_identities", {})) != 11 or len(audit_fixture.get("files", {})) != 4:
        errors.append("FX-AUDIT 必须保留全部 11 个 source identity 与 4 个文件身份")
    adapters = oracle.get("adapter_registry")
    if not isinstance(adapters, dict) or len(adapters) != 6:
        errors.append("Oracle 必须登记六个可执行 adapter")
        adapters = {}
    else:
        for adapter_id, adapter in adapters.items():
            if not isinstance(adapter, dict) or adapter.get("handler") != "dev.tools.validate_country_outage_p1_runtime_v2:apply_oracle_adapter" or not adapter.get("operation"):
                errors.append(f"{adapter_id} 不是可执行 Oracle adapter")
    grounding_adapters = oracle.get("grounding_adapter_registry")
    if not isinstance(grounding_adapters, dict) or set(grounding_adapters) != selected_ids:
        errors.append("Oracle 必须逐 17 项能力登记 capability_input→execution_request adapter")
        grounding_adapters = {}
    else:
        for capability_id, adapter in grounding_adapters.items():
            if (
                not isinstance(adapter, dict)
                or adapter.get("handler") != "host:ground_capability_request"
                or adapter.get("execution_unit") != next(
                    (item.get("execution_unit") for item in selected if item.get("capability_id") == capability_id),
                    None,
                )
                or not isinstance(adapter.get("capability_input_fields"), list)
                or not isinstance(adapter.get("execution_request_fields"), list)
                or not adapter.get("execution_request_fields")
            ):
                errors.append(f"{capability_id} grounding adapter 不完整或执行单元漂移")
    coverage = oracle.get("capability_coverage")
    if not isinstance(coverage, list):
        return errors + ["Full Oracle 缺少 capability_coverage"]
    oracle_capabilities: set[str] = set()
    case_ids: list[str] = []
    unit_by_capability = {
        capability_id: unit["unit_id"]
        for unit in units
        for capability_id in unit.get("capability_ids", [])
    }
    for item in coverage:
        if not isinstance(item, dict):
            errors.append("Oracle coverage 项必须是对象")
            continue
        capability_id = item.get("capability_id")
        cases = item.get("cases")
        if capability_id not in selected_ids:
            errors.append(f"Oracle 引用非 selected 能力 {capability_id}")
        else:
            oracle_capabilities.add(capability_id)
            if item.get("execution_unit") != unit_by_capability.get(capability_id):
                errors.append(f"{capability_id} Oracle execution_unit 映射错误")
        if not isinstance(cases, dict) or set(cases) != CATEGORIES:
            errors.append(f"{capability_id} 未逐能力覆盖六类 Oracle")
            continue
        for category, case in cases.items():
            if not isinstance(case, dict):
                errors.append(f"{capability_id} {category} Oracle 无效")
                continue
            missing_case_fields = ORACLE_CASE_FIELDS - set(case)
            if missing_case_fields:
                errors.append(f"{capability_id} {category} 缺少 {sorted(missing_case_fields)}")
                continue
            case_ids.append(case["case_id"])
            if not isinstance(case.get("input"), dict) or not isinstance(case.get("expected"), dict) or not case["expected"].get("status"):
                errors.append(f"{case['case_id']} 缺少能力专属 input/expected status")
            if not isinstance(case.get("evidence_refs"), list) or not case.get("boundary_assertion") or not case.get("state_commit"):
                errors.append(f"{case['case_id']} 缺少 evidence/state/boundary 断言")
            fixture = fixtures.get(case.get("fixture_id"))
            adapter = adapters.get(case.get("adapter_id"))
            if not isinstance(fixture, dict):
                errors.append(f"{case['case_id']} fixture_id 不存在")
            elif not isinstance(adapter, dict):
                errors.append(f"{case['case_id']} adapter_id 不存在")
            else:
                try:
                    mutated = apply_oracle_adapter(
                        fixture,
                        adapter["operation"],
                        case.get("adapter_args", {}),
                    )
                    if capability_id in {"CAP-016", "CAP-017", "CAP-018"}:
                        actual = execute_operator_oracle(capability_id, mutated)
                        if actual != case.get("expected"):
                            errors.append(
                                f"{case['case_id']} 算子实际结果与 expected 不一致："
                                f"actual={json.dumps(actual, ensure_ascii=False, sort_keys=True)}"
                            )
                except RuntimeError as error:
                    errors.append(f"{case['case_id']} adapter 不可执行：{error}")
    if oracle_capabilities != selected_ids:
        errors.append("Full Oracle 未覆盖全部 17 个 selected 能力")
    if len(coverage) != 17 or len(case_ids) != 102 or len(set(case_ids)) != 102:
        errors.append("Full Oracle 必须为 17×6=102 个唯一逐能力案例")
    by_capability = _coverage_by_id(coverage)
    cap002_facts = by_capability.get("CAP-002", {}).get("cases", {}).get("normal", {}).get("expected", {}).get("facts", {})
    if not {"window_end_utc", "is_final_in_data_range"} <= set(cap002_facts):
        errors.append("CAP-002 normal 未验收 window_end 与 finality")
    cap005_normal = by_capability.get("CAP-005", {}).get("cases", {}).get("normal", {})
    cap005_facts = cap005_normal.get("expected", {}).get("facts", {})
    if (
        cap005_normal.get("fixture_id") != "FX-OVERVIEW"
        or cap005_normal.get("expected", {}).get("status") != "supported"
        or cap005_facts.get("route_interrupted_asn_peak_at_utc") != "2026-02-28T13:50:00Z"
    ):
        errors.append("CAP-005 normal 未绑定已修正 P0 首个 AS 峰值真值")
    for capability_id in ("CAP-006", "CAP-007"):
        normal = by_capability.get(capability_id, {}).get("cases", {}).get("normal", {})
        facts = normal.get("expected", {}).get("facts", {})
        if (
            {"maximum", "minimum", "drop"} & set(facts)
            or not {"point_count", "timestamp_count", "track_length", "first", "last", "definition", "unit"} <= set(facts)
            or normal.get("boundary_assertion") != "raw_series_only_extrema_requires_cap016"
        ):
            errors.append(f"{capability_id} 必须只验收原始时序，极值归 CAP-016")
    cap009_facts = by_capability.get("CAP-009", {}).get("cases", {}).get("normal", {}).get("expected", {}).get("facts", {})
    if cap009_facts.get("definition_count") != 15 or len(track_definitions) != 15:
        errors.append("CAP-009 normal 未真实覆盖 15 条指标定义")
    cap011_facts = by_capability.get("CAP-011", {}).get("cases", {}).get("normal", {}).get("expected", {}).get("facts", {})
    if cap011_facts.get("path_downstream_asn_count") != 1 or cap011_facts.get("concurrent_downstream_asn_count") != 1:
        errors.append("CAP-011 normal 未验收路径关联计数")
    cap014_facts = by_capability.get("CAP-014", {}).get("cases", {}).get("normal", {}).get("expected", {}).get("facts", {})
    if cap014_facts.get("source_identity_count") != 11 or cap014_facts.get("file_count") != 4:
        errors.append("CAP-014 normal 来源身份总数或文件总数错误")
    cap018_nodes = (
        by_capability.get("CAP-018", {}).get("cases", {}).get("normal", {})
        .get("expected", {}).get("facts", {}).get("ordered_fact_nodes")
    )
    expected_cap018_nodes = [
        {"kind": "window_start", "at_utc": "2026-02-27T00:10:00Z", "value": "2026-02-27T00:10:00Z", "unit": "UTC", "evidence_ref": "resolution.window_start_utc"},
        {"kind": "detected", "at_utc": "2026-02-27T01:12:32Z", "value": "2026-02-27T01:12:32Z", "unit": "UTC", "evidence_ref": "overview.event.detected_at_utc"},
        {"kind": "interrupted_prefix_peak", "at_utc": "2026-02-27T23:15:00Z", "value": 3855, "unit": "prefix", "evidence_ref": "overview.peaks.interrupted_prefix_count"},
        {"kind": "completely_interrupted_prefix_peak", "at_utc": "2026-02-28T14:35:00Z", "value": 1553, "unit": "prefix", "evidence_ref": "overview.peaks.completely_interrupted_prefix_count"},
        {"kind": "affected_asn_peak", "at_utc": "2026-03-02T11:30:00Z", "value": 350, "unit": "asn", "evidence_ref": "series.extrema.affected_asn_count"},
        {"kind": "data_through", "at_utc": "2026-03-11T00:00:00Z", "value": 1024, "unit": "prefix", "evidence_ref": "overview.current.interrupted_prefix_count"},
    ]
    if cap018_nodes != expected_cap018_nodes:
        errors.append("CAP-018 normal 未逐节点绑定 P0 ORC-09 的时点、值、单位与证据")
    for capability_id in ("CAP-010", "CAP-011"):
        null_case = by_capability.get(capability_id, {}).get("cases", {}).get("null", {})
        try:
            mutated = apply_oracle_adapter(
                fixtures[null_case["fixture_id"]],
                adapters[null_case["adapter_id"]]["operation"],
                null_case["adapter_args"],
            )
            mutated_item = mutated["items"][0]
            if mutated_item.get("as_name") is not None or mutated_item.get("name_state") != "unknown":
                errors.append(f"{capability_id} null case 未同时验证 value/state 迁移")
        except (KeyError, IndexError, TypeError, RuntimeError):
            errors.append(f"{capability_id} null case 无法验证 value/state 迁移")
    summary = oracle.get("summary")
    if summary != {"selected_capabilities": 17, "categories_per_capability": 6, "expanded_case_count": 102, "executable_adapters": 6, "unresolved_upstream_conflicts": 0}:
        errors.append("Full Oracle summary 漂移")
    conflicts = oracle.get("upstream_conflicts")
    if conflicts != []:
        errors.append("已修正的 P0 真值不得继续作为 unresolved conflict")
    corrections = oracle.get("resolved_upstream_corrections")
    if (
        not isinstance(corrections, list)
        or len(corrections) != 1
        or corrections[0].get("corrected_value") != "2026-02-28T13:50:00Z"
        or set(corrections[0].get("verified_consumers", [])) != {"CAP-005", "CAP-016", "P013-D-08"}
    ):
        errors.append("P0 原位真值修正回执未闭合 CAP-005/CAP-016/P013-D-08")
    p0_orc10 = next((item for item in p0_oracle.get("seeds", []) if item.get("oracle_id") == "ORC-10"), {})
    p0_orc10_time = p0_orc10.get("expected", {}).get("route_interrupted_asn_count", {}).get("maximum_at_utc")
    p0_case = next((item for item in p0_cases.get("cases", []) if item.get("case_id") == "P013-D-08"), {})
    p0_case_metric = next((item for item in p0_case.get("additional_expected_facts", []) if item.get("metric") == "route_interrupted_asn_count"), {})
    p0_live_time = p0_live.get("series", {}).get("selected_extrema", {}).get("route_interrupted_asn_count", {}).get("maximum_at_utc")
    if {p0_orc10_time, p0_case_metric.get("maximum_at_utc"), p0_live_time} != {"2026-02-28T13:50:00Z"}:
        errors.append("P0 v1.3 原位修正未在 live/ORC-10/P013-D-08 同步闭合")

    if schema.get("title") != "国家中断 P1 Runtime v2 语义计划合同":
        errors.append("Semantic Plan Schema 标题或版本无效")
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict) or not {"userGoalPlan", "groundingPlan", "goal", "node"} <= set(definitions):
        errors.append("Semantic Plan Schema 未分离 UserGoalPlan/GroundingPlan")
    else:
        requested_goal = definitions.get("goal", {}).get("properties", {}).get("requested_goal", {})
        if requested_goal.get("type") != "string" or "enum" in requested_goal:
            errors.append("UserGoalPlan.requested_goal 必须开放且不能被 enum 裁剪")
        identity_required = set(definitions.get("identity", {}).get("required", []))
        required_identity = {"binding_phase", "event_type", "incident_id", "publication_id", "revision", "collector_id", "cohort_id", "window_start_utc", "window_end_utc", "data_through", "is_final_in_data_range", "lifecycle_state", "observation_state", "capabilities"}
        if not required_identity <= identity_required:
            errors.append("GroundingPlan identity 未闭合事件、窗口、截止与 finality")
        node_variants = definitions.get("node", {}).get("oneOf", [])
        if not isinstance(node_variants, list) or len(node_variants) != 9:
            errors.append("GroundingPlan node 必须按 9 个执行单元 oneOf 封闭")
        else:
            series_branches = node_variants[2].get("allOf", [{}, {}])[1].get("oneOf", [])
            series_contracts = {
                branch.get("properties", {}).get("capability_ids", {}).get("const", [None])[0]:
                branch.get("properties", {}).get("inputs", {}).get("$ref")
                for branch in series_branches
                if isinstance(branch, dict)
            }
            expected_series_contracts = {
                "CAP-006": "#/$defs/seriesInputsCap006",
                "CAP-007": "#/$defs/seriesInputsCap007",
                "CAP-008": "#/$defs/seriesInputsCap008",
                "CAP-009": "#/$defs/seriesInputsCap009",
            }
            asn_branches = node_variants[3].get("allOf", [{}, {}])[1].get("oneOf", [])
            asn_contracts = {
                branch.get("properties", {}).get("capability_ids", {}).get("const", [None])[0]:
                branch.get("properties", {}).get("inputs", {}).get("$ref")
                for branch in asn_branches
                if isinstance(branch, dict)
            }
            if series_contracts != expected_series_contracts:
                errors.append("TOOL-03 未按 CAP-006..009 分别闭合参数合同")
            if asn_contracts != {
                "CAP-010": "#/$defs/asnListInputs",
                "CAP-011": "#/$defs/asnDetailInputs",
            }:
                errors.append("TOOL-04 未区分 AS 列表与指定 ASN 详情参数合同")
        cap006_metrics = (
            definitions.get("seriesInputsCap006", {}).get("allOf", [{}, {}])[1]
            .get("properties", {}).get("metrics", {}).get("const")
        )
        cap007_metrics = (
            definitions.get("seriesInputsCap007", {}).get("allOf", [{}, {}])[1]
            .get("properties", {}).get("metrics", {}).get("const")
        )
        cap011_required = set(
            definitions.get("asnDetailInputs", {}).get("allOf", [{}, {}])[1].get("required", [])
        )
        if cap006_metrics != ["fixed_visible_ipv4_address_count"] or cap007_metrics != ["fixed_visible_ipv6_slash48_count"]:
            errors.append("CAP-006/CAP-007 地址族参数可被错误轨道替换")
        if "asn" not in cap011_required:
            errors.append("CAP-011 未把指定 ASN 作为必填硬门")
        extrema_inputs = definitions.get("extremaInputs", {})
        extrema_properties = extrema_inputs.get("properties", {})
        if (
            set(extrema_inputs.get("required", [])) != {"source_node_id", "metric", "tie_policy"}
            or set(extrema_properties.get("metric", {}).get("enum", [])) != REGISTERED_METRICS
            or extrema_properties.get("source_node_id", {}).get("pattern") != "^node-[1-9][0-9]*$"
        ):
            errors.append("Semantic Plan OP-01 必须只保存封闭 metric 与已验证 source node 引用")
        compare_inputs = definitions.get("compareInputs", {})
        if set(compare_inputs.get("required", [])) != {"ipv4_extrema_node_id", "ipv6_extrema_node_id"}:
            errors.append("Semantic Plan OP-02 必须只引用两个已验证 extrema node")
        timeline_inputs = definitions.get("timelineInputs", {})
        if set(timeline_inputs.get("required", [])) != {"source_node_ids", "lifecycle_state"}:
            errors.append("Semantic Plan OP-03 必须只引用已验证 fact source nodes")
        serialized_operator_plans = json.dumps(
            {name: definitions.get(name) for name in ("extremaInputs", "compareInputs", "timelineInputs")},
            ensure_ascii=False,
        )
        if any(forbidden in serialized_operator_plans for forbidden in ('"timestamps"', '"values"', '"source_identity"', '"evidence_ref"')):
            errors.append("GroundingPlan 不得在依赖执行前伪装拥有 Tool 事实或证据")
        invariants = schema.get("x-host-invariants")
        invariant_ids = {item.get("invariant_id") for item in invariants if isinstance(item, dict)} if isinstance(invariants, list) else set()
        if invariant_ids != HOST_INVARIANTS:
            errors.append("GroundingPlan 跨引用宿主不变量不完整")

    if policy.get("schema_version") != "country_outage_p1_runtime_policy_v2":
        errors.append("Runtime Policy schema_version 无效")
    allowed_units = set(policy.get("grounding", {}).get("allowed_execution_units", []))
    if allowed_units != EXECUTION_UNITS:
        errors.append("Runtime Policy 执行白名单与 Tool Contract 不一致")
    if policy.get("scope") != {"event_type": "country_outage", "collector_id": "rrc25", "mode": "read_only"}:
        errors.append("Runtime Policy 必须限定 RRC25-only read-only")
    if policy.get("fact_publication", {}).get("model_generated_values") != "forbidden":
        errors.append("Runtime Policy 必须禁止模型生成事实值")
    state = policy.get("state_transaction", {})
    if state.get("unsupported_writes_executable_slots") is not False or state.get("shared_identity_conflict") != "rollback_entire_turn":
        errors.append("状态事务未闭合 unsupported/身份冲突语义")
    validator_contract = policy.get("grounding_validator", {})
    if set(validator_contract.get("required_invariants", [])) != HOST_INVARIANTS or validator_contract.get("all_checks_pass_before_execution") is not True:
        errors.append("Runtime Policy 未把 Grounding 宿主不变量变成执行前硬门")
    validation_order = validator_contract.get("validation_order", [])
    hard_gates = policy.get("grounding", {}).get("hard_gates", [])
    if "parameter_schema_valid" not in validation_order or "parameter_valid" not in hard_gates:
        errors.append("Runtime Policy 未在执行前落实 parameter_schema_valid 硬门")
    if "evidence_reference_resolution" not in validation_order or "evidence_reference_resolved" not in hard_gates:
        errors.append("Runtime Policy 未把 evidence_ref 解析与身份核对落实为发布前硬门")
    if policy.get("event_binding_protocol", {}).get("mode") != "two_phase":
        errors.append("事件切换必须使用两阶段 binding")
    return errors


def validate_stage_receipt(path: Path, stage: str) -> list[str]:
    errors: list[str] = []
    value = read_json(path)
    if value.get("schema_version") != "country_outage_p1_stage_receipt_v2":
        errors.append(f"{stage} receipt schema_version 无效")
    if value.get("stage") != stage or value.get("status") != "completed":
        errors.append(f"{stage} receipt 未标记 completed")
    if value.get("independent_semantic_review", {}).get("verdict") != "PASS":
        errors.append(f"{stage} 缺少独立产品语义 PASS")
    if value.get("alignment_hook", {}).get("exit_code") != 0:
        errors.append(f"{stage} Alignment Hook 未通过")
    checks = value.get("checks")
    if not isinstance(checks, list) or not checks or any(item.get("passed") is not True for item in checks if isinstance(item, dict)):
        errors.append(f"{stage} checks 未全部通过")
    return errors


def validate_manifest(path: Path, required_stage: str | None = None) -> list[str]:
    errors: list[str] = []
    value = read_json(path)
    if value.get("schema_version") != "country_outage_p1_runtime_manifest_v2":
        errors.append("runtime-v2 manifest schema_version 无效")
    if value.get("p0_entry_revision") != P0_REVISION or value.get("collector_id") != "rrc25":
        errors.append("runtime-v2 manifest P0/RRC25 身份漂移")
    stages = value.get("stages")
    if not isinstance(stages, dict):
        return errors + ["runtime-v2 manifest 缺少 stages"]
    required = [required_stage] if required_stage else ["S0", "S1", "S2", "S3", "S4"]
    for stage in required:
        if stages.get(stage) != "completed":
            errors.append(f"manifest {stage} 尚未 completed")
            continue
        receipt_path = EVALUATION_ROOT / "stage-receipts" / f"{stage}.json"
        if not receipt_path.is_file():
            errors.append(f"manifest {stage} receipt 不存在")
        else:
            errors.extend(validate_stage_receipt(receipt_path, stage))
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list):
        return errors + ["runtime-v2 manifest 缺少 artifacts"]
    seen: set[str] = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            errors.append(f"manifest artifact[{index}] 无效")
            continue
        try:
            target = safe_path(artifact.get("path"), f"artifact[{index}]")
        except RuntimeError as error:
            errors.append(str(error))
            continue
        relative = artifact["path"]
        if relative in seen:
            errors.append(f"manifest 重复制品 {relative}")
            continue
        seen.add(relative)
        if not target.is_file():
            errors.append(f"manifest 制品不存在 {relative}")
            continue
        if target.stat().st_size != artifact.get("size_bytes") or sha256(target) != artifact.get("sha256"):
            errors.append(f"manifest 制品摘要漂移 {relative}")
    return errors


def current_s0_errors() -> list[str]:
    return validate_s0(
        read_json(CONTRACT_ROOT / "capability-catalog.json"),
        read_json(CONTRACT_ROOT / "tool-contracts.json"),
        read_json(CONTRACT_ROOT / "oracle.json"),
        read_json(CONTRACT_ROOT / "semantic-plan.schema.json"),
        read_json(CONTRACT_ROOT / "policy.json"),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", nargs="?", type=Path)
    parser.add_argument("--stage", choices=["S0", "S1", "S2", "S3", "S4"])
    arguments = parser.parse_args()
    errors = current_s0_errors()
    if arguments.manifest:
        manifest_path = arguments.manifest
        if not manifest_path.is_absolute():
            manifest_path = REPOSITORY_ROOT / manifest_path
        errors.extend(validate_manifest(manifest_path, arguments.stage))
    if errors:
        for error in errors:
            print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print("[PASS] P1 runtime-v2 S0 合同闭合：17 selected，9 执行单元，102 Oracle")
    if arguments.manifest:
        print(f"[PASS] manifest 阶段校验：{arguments.stage or 'S0-S4'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
