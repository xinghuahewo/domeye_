#!/usr/bin/env python3
"""独立审核 P2-S0B Runtime Registry 候选的产品语义与执行边界。

Reviewer 不导入 S0B 构建器或运行时实现，不执行 Registry 写操作，也不审核代码风格。
它先从 P2-S0A 已验收快照、P1/OP-04 合同和 S0B Task Spec 得到真值，再与候选比较。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence


REVIEW_SCHEMA = "country_outage_p2_s0b_product_semantic_review_v1"
BASE_SNAPSHOT = Path("contracts/agent/country-outage-p2-s0a-lifecycle/registry-snapshot.json")
RUNTIME_ROOT = Path("contracts/agent/country-outage-p2-s0b-runtime")
TASK_SPEC = Path("docs/agent/P2-组合式调查/Tool与Operator生命周期治理/P2-S0B-运行时接入/Task-Spec-最终验收文档.md")
PLAN = Path("docs/agent/P2-组合式调查/Tool与Operator生命周期治理/P2-S0B-运行时接入/Plan-执行与验收.md")
RUNTIME_SOURCE = Path("agent-sidecar/src/chat/p2-registry-runtime.ts")
SEMANTIC_SOURCE = Path("agent-sidecar/src/chat/runtime-v2-semantic.ts")
EXECUTOR_SOURCE = Path("agent-sidecar/src/chat/page-capability-executor.ts")
BUILDER_SOURCE = Path("dev/tools/build_country_outage_p2_s0b_candidate.py")
REVIEWER_SOURCE = Path("dev/tools/review_country_outage_p2_s0b_semantics.py")


class ReviewError(RuntimeError):
    pass


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


def digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def snapshot_number(value: int | float) -> str:
    decimal_value = Decimal(str(value)).normalize()
    if not decimal_value.is_finite():
        raise ReviewError("快照摘要包含非有限数字")
    if decimal_value.is_zero():
        return "0"
    sign, digits_tuple, exponent = decimal_value.as_tuple()
    digits = "".join(str(digit) for digit in digits_tuple)
    scientific_exponent = exponent + len(digits) - 1
    coefficient = digits[0] + (("." + digits[1:]) if len(digits) > 1 else "")
    return ("-" if sign else "") + coefficient + "e" + str(scientific_exponent)


def snapshot_canonical(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return snapshot_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(snapshot_canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + snapshot_canonical(value[key])
            for key in sorted(value)
        ) + "}"
    raise ReviewError(f"不支持的快照摘要类型：{type(value).__name__}")


def snapshot_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(snapshot_canonical(value).encode("utf-8")).hexdigest()


def atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise ReviewError(f"输出不是规范普通文件：{path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()


def imports(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.append(node.module or "")
    return result


def indexed(entries: Any, key: str) -> Dict[tuple[str, str], Mapping[str, Any]]:
    if not isinstance(entries, list):
        raise ReviewError("Registry entries 必须为数组")
    return {(str(item.get(key)), str(item.get("version"))): item for item in entries}


def review(repo_root: Path, candidate_path: Path, output_path: Path) -> Dict[str, Any]:
    candidate = load(candidate_path)
    snapshot = load(candidate_path.parent / "registry-snapshot.json")
    base_snapshot = load(repo_root / BASE_SNAPSHOT)
    runtime_contract = load(repo_root / RUNTIME_ROOT / "runtime-contract.json")
    admission_schema = load(repo_root / RUNTIME_ROOT / "runtime-plan-admission.schema.json")
    oracle = load(repo_root / RUNTIME_ROOT / "shadow-oracle.json")
    task_spec = (repo_root / TASK_SPEC).read_text(encoding="utf-8")
    plan = (repo_root / PLAN).read_text(encoding="utf-8")
    runtime_source = (repo_root / RUNTIME_SOURCE).read_text(encoding="utf-8")
    semantic_source = (repo_root / SEMANTIC_SOURCE).read_text(encoding="utf-8")
    executor_source = (repo_root / EXECUTOR_SOURCE).read_text(encoding="utf-8")
    findings: List[Dict[str, Any]] = []

    def check(check_id: str, condition: bool, truth: str, actual: Any) -> None:
        findings.append({
            "check_id": check_id,
            "status": "PASS" if condition else "BLOCK",
            "prior_truth": truth,
            "actual": actual,
        })

    payload = snapshot.get("snapshot_payload", {})
    base_payload = base_snapshot.get("snapshot_payload", {})
    capabilities = indexed(payload.get("capability_registry", {}).get("entries"), "capability_id")
    units = indexed(payload.get("execution_unit_registry", {}).get("entries"), "unit_id")
    base_capabilities = indexed(base_payload.get("capability_registry", {}).get("entries"), "capability_id")
    base_units = indexed(base_payload.get("execution_unit_registry", {}).get("entries"), "unit_id")

    check("SEM-COUNT-CAP", len(capabilities) == 18, "Capability 数量保持 18", len(capabilities))
    check("SEM-COUNT-UNIT", len(units) == 10, "Execution Unit 数量保持 10", len(units))
    check("SEM-COUNT-TOOL", sum(key[0].startswith("TOOL-") for key in units) == 6, "只读 Tool 保持 6 个", [key[0] for key in units])
    check("SEM-COUNT-OP", sum(key[0].startswith("OP-") for key in units) == 4, "OP-01..04 保持四个", [key[0] for key in units])

    semantic_diffs: List[Dict[str, Any]] = []
    for label, current, base in (("capability", capabilities, base_capabilities), ("unit", units, base_units)):
        if set(current) != set(base):
            semantic_diffs.append({"registry": label, "difference": "identity_or_version_set_changed"})
        for identity in set(current) & set(base):
            for field in ("contract_digest", "semantic_digest"):
                if current[identity].get(field) != base[identity].get(field):
                    semantic_diffs.append({"registry": label, "identity": identity, "difference": f"{field}_changed"})
    check("SEM-S0A-EQUIVALENCE", not semantic_diffs, "稳定 ID、版本、合同摘要和产品语义摘要与 S0A 相同", semantic_diffs)

    op04 = units.get(("OP-04", "1.2.0"), {})
    check(
        "SEM-OP04",
        op04.get("dependencies") == [{"unit_id": "TOOL-03", "version": "1.0.0", "relationship": "validated_series_source"}],
        "OP-04@1.2.0 仍只依赖 TOOL-03@1.0.0 的已验证序列",
        op04.get("dependencies"),
    )
    boundary_diffs = []
    for identity, capability in capabilities.items():
        constraints = capability.get("identity_constraints", {})
        boundaries = set(capability.get("boundaries", []))
        if constraints.get("event_type") != "country_outage" or constraints.get("collector_id") != "rrc25":
            boundary_diffs.append({"capability": identity, "difference": "identity_boundary"})
        if not {"no_cause", "no_recovery", "no_real_user_impact", "no_network_rca"}.issubset(boundaries):
            boundary_diffs.append({"capability": identity, "difference": "claim_boundary"})
    check("SEM-RRC25-NONRCA", not boundary_diffs, "所有 Capability 保持 RRC25、非原因、非恢复、非用户影响和非 RCA 边界", boundary_diffs)

    expected_snapshot_digest = snapshot_digest(payload)
    check("ID-SNAPSHOT", snapshot.get("snapshot_digest") == expected_snapshot_digest and snapshot.get("registry_snapshot_id") == "registry-snapshot-sha256:" + expected_snapshot_digest.split(":", 1)[1], "快照为 p2-s0b-canonical-json-v1 内容寻址", snapshot.get("registry_snapshot_id"))
    check("ID-CANDIDATE", candidate.get("candidate_id") == payload.get("candidate_id") and candidate.get("registry_snapshot_id") == snapshot.get("registry_snapshot_id") and candidate.get("registry_revision") == payload.get("registry_revision"), "candidate、snapshot 与 revision 身份闭合", {"candidate": candidate.get("candidate_id"), "snapshot": candidate.get("registry_snapshot_id"), "revision": candidate.get("registry_revision")})
    check("BOUNDARY-NONDEPLOY", candidate.get("production_deployed") is False and candidate.get("prod32_switched") is False and candidate.get("runtime_integration") == "implemented_not_deployed" and payload.get("activation_scope") == "runtime_candidate_shadow_only", "本候选只实现未部署且不切换 prod32", {"production": candidate.get("production_deployed"), "prod32": candidate.get("prod32_switched"), "scope": payload.get("activation_scope")})

    artifact_diffs = []
    for name, expected in candidate.get("artifact_digests", {}).items():
        actual = digest_file(candidate_path.parent / name)
        if actual != expected:
            artifact_diffs.append({"path": name, "expected": expected, "actual": actual})
    for item in candidate.get("source_identity", {}).get("runtime_material", []):
        actual = digest_file(repo_root / item["path"])
        if actual != item.get("sha256"):
            artifact_diffs.append({"path": item["path"], "expected": item.get("sha256"), "actual": actual})
    check("ID-ARTIFACTS", not artifact_diffs, "候选绑定的合同、快照和运行时源码摘要均为当前同候选", artifact_diffs)

    implementation_diffs = []
    for identity, unit in units.items():
        manifest = unit.get("implementation_files", [])
        for item in manifest:
            actual = digest_file(repo_root / item["path"])
            if actual != item.get("sha256"):
                implementation_diffs.append({"unit": identity, "path": item["path"]})
    check("ID-IMPLEMENTATION", not implementation_diffs, "每个 Unit implementation manifest 指向当前源码原始摘要", implementation_diffs)

    expected_handlers = {f"TOOL-{index:02d}" for index in range(1, 7)} | {f"OP-{index:02d}" for index in range(1, 5)}
    actual_handlers = {item.get("unit_id") for item in runtime_contract.get("supported_handlers", [])}
    check("RUNTIME-HANDLERS", actual_handlers == expected_handlers, "Host Handler 只登记当前 6 Tool 与 OP-01..04", sorted(actual_handlers))
    check("RUNTIME-READONLY", all(marker not in runtime_source for marker in ("writeFile", "appendFile", "renameSync", "unlinkSync", "mkdirSync", "manage_country_outage_p2_registry")), "Runtime Registry 入口只读且不导入治理写实现", "read_only_source_scan")
    admission_at = semantic_source.find("admitPlan(")
    execution_at = semantic_source.find("executor.execute(", admission_at)
    check("RUNTIME-ORDER", admission_at >= 0 and execution_at > admission_at, "真实语义轮在 Executor 前调用 Registry Admission", {"admission_offset": admission_at, "executor_offset": execution_at})
    check("RUNTIME-EXECUTOR-GATE", "registry_admission_missing" in executor_source and "node.registry_binding" in executor_source, "Executor 拒绝缺少 Registry Binding 的节点", "gate_present" if "registry_admission_missing" in executor_source else "gate_missing")
    required_policy = runtime_contract.get("call_policy", {}).get("required", [{}])
    check(
        "RUNTIME-REQUIRED",
        "required_call_missing" in runtime_source
        and "node.execution_unit === 'TOOL-01'" in runtime_source
        and required_policy[0].get("capability_id") == "CAP-001",
        "事实计划强制身份解析 Capability，缺失时失败关闭",
        required_policy,
    )

    expected_categories = {"required_call", "conditional_call", "forbidden_call", "missing", "null", "wrong_identity", "unavailable", "inactive", "tamper", "dependency", "snapshot_drift", "handler_missing", "evidence_binding", "rollback"}
    check("ORACLE-CATEGORIES", set(oracle.get("categories", [])) == expected_categories and len(oracle.get("cases", [])) >= 14, "Shadow Oracle 覆盖 14 类正常、边界和篡改真值", oracle.get("categories"))
    gates = oracle.get("gates", {})
    check("ORACLE-GATES", gates.get("should_call_coverage") == 1.0 and gates.get("should_not_call_rate") == 0.0 and gates.get("snapshot_drift_count") == 0 and gates.get("product_semantic_blocking_count") == 0, "量化门要求应调用 100%、误调用 0、漂移 0、语义阻断 0", gates)
    check(
        "SCHEMA-ADMISSION",
        admission_schema.get("properties", {}).get("production_deployed", {}).get("type") == "boolean"
        and admission_schema.get("properties", {}).get("execution_started", {}).get("const") is False
        and len(admission_schema.get("allOf", [])) == 1,
        "Admission 回执明确执行未开始，并强制 shadow/production 模式与部署标志一致",
        {"properties": admission_schema.get("properties"), "mode_rules": admission_schema.get("allOf")},
    )

    required_doc_markers = ("最终效果", "Host Handler", "required", "conditional", "forbidden", "同轮快照漂移次数 = 0", "本 Task Spec 通过不等于生产可用")
    check("DOC-SPEC", all(marker in task_spec for marker in required_doc_markers), "Task Spec 冻结产品效果、调用策略、Handler 边界、量化门和非生产声明", [marker for marker in required_doc_markers if marker not in task_spec])
    check("DOC-PLAN", all(f"S0B-{index}" in plan for index in range(7)) and "本轮不执行" in plan, "Plan 含 S0B-0..6 且生产阶段明确不执行", "stage_map")

    reviewer_imports = imports(repo_root / REVIEWER_SOURCE)
    independent = digest_file(repo_root / REVIEWER_SOURCE) != digest_file(repo_root / BUILDER_SOURCE) and all("build_country_outage_p2_s0b_candidate" not in name for name in reviewer_imports)
    check("REVIEW-INDEPENDENT", independent, "产品语义 Reviewer 与候选构建器职责和实现分离且不导入构建器", reviewer_imports)

    blocking = [item for item in findings if item["status"] == "BLOCK"]
    receipt: Dict[str, Any] = {
        "schema_version": REVIEW_SCHEMA,
        "candidate_id": candidate.get("candidate_id"),
        "registry_snapshot_id": snapshot.get("registry_snapshot_id"),
        "reviewed_at": candidate.get("created_at"),
        "reviewer_role_id": "p2-s0b-product-semantic-reviewer-v1",
        "builder_role_id": "p2-s0b-runtime-candidate-builder-v1",
        "truth_derived_before_comparison": True,
        "truth_sources": [str(BASE_SNAPSHOT), str(TASK_SPEC), str(RUNTIME_ROOT / "runtime-contract.json"), "P1 Capability/Tool contracts", "OP-04@1.2.0 contract"],
        "reviewer_source_digest": digest_file(repo_root / REVIEWER_SOURCE),
        "builder_source_digest": digest_file(repo_root / BUILDER_SOURCE),
        "reviewed_input_digest": digest_value({"candidate": candidate, "snapshot": snapshot, "runtime_contract": runtime_contract, "oracle": oracle}),
        "findings": findings,
        "blocking_items": blocking,
        "blocking_count": len(blocking),
        "status": "PASS" if not blocking else "BLOCK",
        "runtime_integration": "implemented_not_deployed",
        "production_deployed": False,
        "does_not_prove": ["production_deployment", "production_traffic", "user_value_in_production"],
        "receipt_path": str(output_path.resolve().relative_to(repo_root.resolve())),
    }
    receipt["receipt_digest"] = digest_value(receipt)
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="独立审核 P2-S0B Runtime Registry 产品语义")
    result.add_argument("--repo-root", required=True)
    result.add_argument("--candidate", required=True)
    result.add_argument("--output", required=True)
    result.add_argument("--check", action="store_true")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        repo_root = Path(args.repo_root).resolve()
        candidate_path = Path(args.candidate)
        if not candidate_path.is_absolute():
            candidate_path = repo_root / candidate_path
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = repo_root / output_path
        candidate_path = Path(os.path.abspath(candidate_path))
        output_path = Path(os.path.abspath(output_path))
        result = review(repo_root, candidate_path, output_path)
        if args.check:
            if load(output_path) != result:
                raise ReviewError("Reviewer 回执与当前候选或产品真值不一致")
        else:
            atomic_write(output_path, result)
        print(json.dumps({"status": result["status"], "candidate_id": result["candidate_id"], "blocking_count": result["blocking_count"], "receipt_digest": result["receipt_digest"]}, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "PASS" else 2
    except (OSError, KeyError, ReviewError, ValueError) as exc:
        print(json.dumps({"status": "BLOCK", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
