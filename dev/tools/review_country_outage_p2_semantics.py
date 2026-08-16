#!/usr/bin/env python3
"""独立审核 P2-S0A Registry 候选的产品语义。

本 Reviewer 不导入治理实现，不执行 Registry 写操作，不审核代码风格，也不把
候选自称的 PASS/active 当作真值。真值来自 Product Semantic Charter 与迁移前
P1/OP-04 合同，然后才与候选进行比较。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence


REVIEW_SCHEMA = "country_outage_p2_s0a_product_semantic_review_v1"


class ReviewError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> MutableMapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ReviewError(f"不是规范普通文件：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"JSON 无效：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"JSON 根必须是对象：{path}")
    return value


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise ReviewError(f"输出不是规范普通文件：{path}")
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def relative(repo_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError as exc:
        raise ReviewError(f"回执必须位于仓库内：{path}") from exc


def extract_registry_set(candidate_path: Path, candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    schema = candidate.get("schema_version")
    if schema == "country_outage_p2_s0a_migration_proposal_v1":
        registry_set = candidate.get("registry_set")
    elif schema == "country_outage_p2_s0a_candidate_v1":
        registry_set = load(candidate_path.parent / "registry-set.json")
    else:
        raise ReviewError(f"不支持的候选 Schema：{schema}")
    if not isinstance(registry_set, dict):
        raise ReviewError("候选缺少 registry_set")
    return registry_set


def find(entries: List[Mapping[str, Any]], key: str, value: str) -> Optional[Mapping[str, Any]]:
    for entry in entries:
        if entry.get(key) == value:
            return entry
    return None


def semantic_findings(repo_root: Path, candidate_path: Path, output_path: Path) -> Dict[str, Any]:
    charter_path = repo_root / "contracts/agent/country-outage-p2-s0a-lifecycle/product-semantic-charter.json"
    probes_path = repo_root / "contracts/agent/country-outage-p2-s0a-lifecycle/question-probes.json"
    policy_path = repo_root / "contracts/agent/country-outage-p2-s0a-lifecycle/lifecycle-policy.json"
    catalog_path = repo_root / "contracts/agent/country-outage-p1-page-coverage/s2/capability-catalog.json"
    tool_path = repo_root / "contracts/agent/country-outage-p1-page-coverage/s2/tool-contracts.json"
    op_path = repo_root / "contracts/agent/country-outage-p1-trend-operator/v1/operator-contract.json"
    integration_path = repo_root / "contracts/agent/country-outage-p1-trend-operator/v1/p1-integration-contract.json"
    reviewer_path = repo_root / "dev/tools/review_country_outage_p2_semantics.py"
    builder_path = repo_root / "dev/tools/manage_country_outage_p2_registry.py"
    candidate = load(candidate_path)
    registry_set = extract_registry_set(candidate_path, candidate)
    charter = load(charter_path)
    probes = load(probes_path)
    policy = load(policy_path)
    catalog = load(catalog_path)
    tools = load(tool_path)
    op_contract = load(op_path)
    integration = load(integration_path)
    truths = charter["required_truths"]
    capabilities = registry_set.get("capability_registry", {}).get("entries", [])
    units = registry_set.get("execution_unit_registry", {}).get("entries", [])
    if not isinstance(capabilities, list) or not isinstance(units, list):
        raise ReviewError("双 Registry entries 无效")
    findings: List[Dict[str, Any]] = []

    def check(check_id: str, condition: bool, truth: str, actual: Any) -> None:
        findings.append(
            {
                "check_id": check_id,
                "status": "PASS" if condition else "BLOCK",
                "prior_truth": truth,
                "actual": actual,
            }
        )

    check("SEM-COUNT-CAP", len(capabilities) == truths["capability_count"], "Capability 必须为 18 个", len(capabilities))
    check("SEM-COUNT-UNIT", len(units) == truths["execution_unit_count"], "Execution Unit 必须为 10 个", len(units))
    tool_units = [unit for unit in units if str(unit.get("unit_id", "")).startswith("TOOL-")]
    operator_units = [unit for unit in units if str(unit.get("unit_id", "")).startswith("OP-")]
    check("SEM-COUNT-TOOL", len(tool_units) == 6, "当前迁移集合有 6 个只读 Tool", len(tool_units))
    check("SEM-COUNT-OP", len(operator_units) == 4, "当前迁移集合有 OP-01..04 四个 Operator", len(operator_units))
    check("SEM-ACTIVATION-SCOPE", registry_set.get("activation_scope") == truths["activation_scope"], "active 只表示离线候选快照", registry_set.get("activation_scope"))
    check("SEM-RUNTIME", registry_set.get("runtime_integration") == truths["runtime_integration"], "本轮未接入生产运行时", registry_set.get("runtime_integration"))
    check("SEM-POLICY-NONACTIVE", policy.get("plan_admission", {}).get("non_active_behavior") == truths["non_active_plan_admission"], "非 active 必须在执行前拒绝", policy.get("plan_admission", {}).get("non_active_behavior"))
    check("SEM-POLICY-SNAPSHOT", policy.get("plan_admission", {}).get("require_bound_snapshot") is True, "每轮计划必须绑定不可变快照", policy.get("plan_admission", {}).get("require_bound_snapshot"))
    check("SEM-POLICY-ID", policy.get("identity", {}).get("stable_id_reuse") == "forbidden", "Tombstone 后稳定 ID 永不复用", policy.get("identity", {}).get("stable_id_reuse"))
    check("SEM-REVIEWER-INDEPENDENT", digest_file(reviewer_path) != digest_file(builder_path), "Reviewer 与构建器为不同实现身份", {"reviewer": digest_file(reviewer_path), "builder": digest_file(builder_path)})
    reviewer_tree = ast.parse(reviewer_path.read_text(encoding="utf-8"))
    imported_modules: List[str] = []
    for node in ast.walk(reviewer_tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")
    check("SEM-REVIEWER-NO-IMPORT", all("manage_country_outage_p2_registry" not in module for module in imported_modules), "Reviewer 不导入治理实现", imported_modules)

    expected_capabilities = {entry["capability_id"]: entry for entry in catalog["selected"]}
    migration_diffs: List[Dict[str, Any]] = []
    for capability_id, expected in expected_capabilities.items():
        actual = find(capabilities, "capability_id", capability_id)
        if actual is None:
            migration_diffs.append({"capability_id": capability_id, "diff": "missing"})
            continue
        if actual.get("user_outcome") != expected.get("user_outcome"):
            migration_diffs.append({"capability_id": capability_id, "diff": "user_outcome_changed"})
        expected_unit = expected.get("execution_unit")
        actual_units = [item.get("unit_id") for item in actual.get("execution_units", [])]
        if actual_units != [expected_unit]:
            migration_diffs.append({"capability_id": capability_id, "diff": "execution_unit_changed", "expected": expected_unit, "actual": actual_units})
        identity = actual.get("identity_constraints", {})
        if identity.get("event_type") != "country_outage" or identity.get("collector_id") != "rrc25":
            migration_diffs.append({"capability_id": capability_id, "diff": "identity_boundary_changed"})
    check("SEM-P1-MIGRATION", not migration_diffs, "17 个 P1 selected Capability 的用户结果与执行映射不变", migration_diffs)

    expected_units = {entry["unit_id"]: entry for entry in tools["execution_units"]}
    unit_diffs: List[Dict[str, Any]] = []
    for unit_id, expected in expected_units.items():
        actual = find(units, "unit_id", unit_id)
        if actual is None:
            unit_diffs.append({"unit_id": unit_id, "diff": "missing"})
            continue
        for field in ("kind", "name", "purpose", "capability_ids", "permission", "source_operation", "timeout_ms", "null_semantics", "errors", "forbidden_uses"):
            if actual.get(field) != expected.get(field):
                unit_diffs.append({"unit_id": unit_id, "diff": f"{field}_changed"})
    check("SEM-P1-UNITS", not unit_diffs, "TOOL-01..06 与 OP-01..03 的产品语义字段不变", unit_diffs)

    op04 = find(units, "unit_id", "OP-04")
    trend_cap = find(capabilities, "capability_id", "CAP-TREND-001")
    op04_actual = {
        "version": op04.get("version") if op04 else None,
        "name": op04.get("name") if op04 else None,
        "capability_ids": op04.get("capability_ids") if op04 else None,
        "dependencies": op04.get("dependencies") if op04 else None,
    }
    op04_ok = bool(
        op04
        and trend_cap
        and op04.get("version") == truths["op04"]["version"]
        and op04.get("name") == truths["op04"]["operator_id"]
        and op04.get("capability_ids") == [truths["op04"]["capability_id"]]
        and op04.get("dependencies") == [{"unit_id": truths["op04"]["source_unit_id"], "version": "1.0.0", "relationship": "validated_series_source"}]
        and integration.get("operator", {}).get("model_dependency") == truths["op04"]["model_dependency"]
        and op_contract.get("operator_version") == truths["op04"]["version"]
    )
    check("SEM-OP04", op04_ok, "OP-04 保持 event-window-trend@1.2.0、CAP-TREND-001、TOOL-03 来源和无模型依赖", op04_actual)

    boundary_diffs: List[Dict[str, Any]] = []
    for capability in capabilities:
        boundaries = set(capability.get("boundaries", []))
        required = {"rrc25_control_plane_only", "no_cause", "no_recovery", "no_real_user_impact", "no_network_rca"}
        if capability.get("capability_id") == "CAP-TREND-001":
            required = {"current_publication_window_only", "no_cause", "no_recovery", "no_real_user_impact", "no_network_rca"}
        if not required.issubset(boundaries):
            boundary_diffs.append({"capability_id": capability.get("capability_id"), "missing": sorted(required - boundaries)})
    check("SEM-BOUNDARIES", not boundary_diffs, "每个 Capability 保持 RRC25 与非原因/恢复/用户影响/RCA 边界", boundary_diffs)
    check("SEM-IP-UNIT", truths["cross_unit_ipv4_ipv6_sum"] == "forbidden", "IPv4 unique address 与 IPv6 /48 等价量禁止相加", truths["cross_unit_ipv4_ipv6_sum"])
    check("SEM-PATH", truths["path_adjacency_causality"] == "forbidden", "AS_PATH 相邻不证明依赖、传播或原因", truths["path_adjacency_causality"])
    check("SEM-RECOVERY", truths["data_through_recovery"] == "forbidden", "data_through 或末值改善不证明恢复", truths["data_through_recovery"])
    probe_cases = probes.get("cases", [])
    check("SEM-PROBES", isinstance(probe_cases, list) and len(probe_cases) >= 10 and probes.get("may_decide_pass") is False, "问题探针至少覆盖 10 个高风险语义且不得自判 PASS", len(probe_cases) if isinstance(probe_cases, list) else "invalid")

    blocking = [finding for finding in findings if finding["status"] == "BLOCK"]
    input_material = {
        "candidate": candidate,
        "registry_set": registry_set,
        "charter_digest": digest_file(charter_path),
        "probe_digest": digest_file(probes_path),
    }
    payload: Dict[str, Any] = {
        "schema_version": REVIEW_SCHEMA,
        "candidate_id": candidate.get("candidate_id"),
        "reviewed_at": candidate.get("created_at"),
        "reviewer_role_id": "product-semantic-reviewer-v1",
        "builder_role_id": "registry-governance-builder-v1",
        "reviewer_source_digest": digest_file(reviewer_path),
        "builder_source_digest": digest_file(builder_path),
        "reviewed_input_digest": digest_value(input_material),
        "truth_charter_digest": digest_file(charter_path),
        "question_probe_digest": digest_file(probes_path),
        "truth_derived_before_comparison": True,
        "does_not_review": ["code_style", "formatting", "deployment", "production_runtime"],
        "findings": findings,
        "blocking_items": blocking,
        "blocking_count": len(blocking),
        "status": "PASS" if not blocking else "BLOCK",
        "runtime_integration": "not_implemented",
        "production_deployed": False,
        "receipt_path": relative(repo_root, output_path),
    }
    payload["receipt_digest"] = digest_value(payload)
    return payload


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="独立审核 P2-S0A Registry 产品语义")
    result.add_argument("--repo-root", required=True)
    result.add_argument("--candidate", required=True)
    result.add_argument("--output", required=True)
    result.add_argument("--check", action="store_true")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        repo_root = Path(args.repo_root).resolve()
        candidate_path = (repo_root / args.candidate).resolve() if not Path(args.candidate).is_absolute() else Path(args.candidate).resolve()
        output_path = (repo_root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output).resolve()
        review = semantic_findings(repo_root, candidate_path, output_path)
        if args.check:
            existing = load(output_path)
            if existing != review:
                raise ReviewError("Reviewer 回执与当前候选或真值不一致")
        else:
            write(output_path, review)
        sys.stdout.write(json.dumps({"status": review["status"], "candidate_id": review["candidate_id"], "blocking_count": review["blocking_count"], "receipt_digest": review["receipt_digest"]}, ensure_ascii=False, sort_keys=True) + "\n")
        return 0 if review["status"] == "PASS" else 2
    except ReviewError as exc:
        sys.stdout.write(json.dumps({"status": "BLOCK", "error": str(exc)}, ensure_ascii=False, sort_keys=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
