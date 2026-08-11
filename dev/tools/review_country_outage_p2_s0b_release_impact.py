#!/usr/bin/env python3
"""独立审核 P2-S0B6 发布对既有 P1 模型认证的影响边界。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence


REVIEW_SCHEMA = "country_outage_p2_s0b6_certification_impact_review_v1"
DEFAULT_POLICY = Path("deploy/country-outage-agent/p1-chat/certification-impact-policy.json")
BASE_CERTIFICATION = Path("evaluation/country-outage/p1-prod-release/attempt-004/manifest.json")
P2_ACCEPTANCE = Path("evaluation/country-outage/p2-s0b-runtime/acceptance-manifest.json")
P2_PRODUCT_REVIEW = Path("evaluation/country-outage/p2-s0b-runtime/product-semantic-review.json")
REVIEWER_SOURCE = Path("dev/tools/review_country_outage_p2_s0b_release_impact.py")


class ImpactReviewError(RuntimeError):
    pass


def load(path: Path) -> MutableMapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ImpactReviewError(f"不是规范普通文件：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImpactReviewError(f"JSON 无效：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise ImpactReviewError(f"JSON 根必须是对象：{path}")
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def digest_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ImpactReviewError(f"不是规范普通文件：{path}")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise ImpactReviewError(f"输出不是规范普通文件：{path}")
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


def imported_modules(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


def review(repo_root: Path, policy_path: Path, output_path: Path) -> Dict[str, Any]:
    policy = load(policy_path)
    certification_path = repo_root / BASE_CERTIFICATION
    acceptance_path = repo_root / P2_ACCEPTANCE
    product_review_path = repo_root / P2_PRODUCT_REVIEW
    certification = load(certification_path)
    acceptance = load(acceptance_path)
    product_review = load(product_review_path)
    findings: List[Dict[str, Any]] = []

    def check(check_id: str, condition: bool, truth: str, actual: Any) -> None:
        findings.append({
            "check_id": check_id,
            "status": "PASS" if condition else "BLOCK",
            "prior_truth": truth,
            "actual": actual,
        })

    base = policy.get("base_certification", {})
    check(
        "BASE-CERTIFICATION-IDENTITY",
        policy.get("schema_version") ==
        "country_outage_p2_s0b6_certification_impact_policy_v1"
        and certification.get("candidate_id") == base.get("candidate_id")
        and certification.get("evidence_id") == base.get("evidence_id")
        and digest_file(certification_path) == base.get("manifest_sha256"),
        "沿用范围必须绑定既有 P1 模型认证的候选、Evidence ID 和原始清单摘要",
        {
            "candidate_id": certification.get("candidate_id"),
            "evidence_id": certification.get("evidence_id"),
            "manifest_sha256": digest_file(certification_path),
        },
    )

    certified_files = {
        item.get("path"): "sha256:" + str(item.get("sha256"))
        for item in certification.get("source_identity", {}).get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    actual_files: Dict[str, str] = {}
    for relative_path in certified_files:
        actual_files[relative_path] = digest_file(repo_root / relative_path)
    mismatch_paths = sorted(
        relative_path
        for relative_path, certified_digest in certified_files.items()
        if actual_files[relative_path] != certified_digest
    )
    allowed_changes = policy.get("allowed_source_changes", [])
    allowed_by_path = {
        item.get("path"): item
        for item in allowed_changes
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    check(
        "SOURCE-IMPACT-CLOSED-SET",
        len(allowed_by_path) == len(allowed_changes) == 1
        and mismatch_paths == sorted(allowed_by_path),
        "旧认证源码只允许 runtime-v2-semantic.ts 这一项显式差异",
        {"mismatches": mismatch_paths, "allowed": sorted(allowed_by_path)},
    )
    source_binding_errors = []
    for relative_path, rule in allowed_by_path.items():
        if certified_files.get(relative_path) != rule.get("certified_sha256"):
            source_binding_errors.append({"path": relative_path, "field": "certified_sha256"})
        if actual_files.get(relative_path) != rule.get("release_sha256"):
            source_binding_errors.append({"path": relative_path, "field": "release_sha256"})
        if rule.get("classification") != "deterministic_post_grounding_registry_admission":
            source_binding_errors.append({"path": relative_path, "field": "classification"})
    check(
        "SOURCE-IMPACT-DIGESTS",
        not source_binding_errors,
        "唯一差异的认证摘要、发布摘要和影响类别必须精确绑定",
        source_binding_errors,
    )

    semantic_source_path = repo_root / "agent-sidecar/src/chat/runtime-v2-semantic.ts"
    semantic_source = semantic_source_path.read_text(encoding="utf-8")
    admission_at = semantic_source.find("admitPlan(")
    execution_at = semantic_source.find("executor.execute(", admission_at)
    required_markers = (
        "P2GovernedRegistryRuntime",
        "registry_admission: admitted.receipt",
        "registry_snapshot_id: admitted.receipt.registry_snapshot_id",
        "registry_production_deployed: admitted.receipt.production_deployed",
    )
    check(
        "SOURCE-DETERMINISTIC-ADMISSION",
        admission_at >= 0
        and execution_at > admission_at
        and all(marker in semantic_source for marker in required_markers),
        "差异只通过确定性 Registry Admission 约束 Executor，并输出可审计身份",
        {
            "admission_offset": admission_at,
            "execution_offset": execution_at,
            "missing_markers": [marker for marker in required_markers if marker not in semantic_source],
        },
    )

    p2 = policy.get("p2_runtime_evidence", {})
    check(
        "P2-RUNTIME-EVIDENCE",
        acceptance.get("candidate_id") == p2.get("candidate_id")
        and acceptance.get("manifest_digest") == p2.get("acceptance_manifest_digest")
        and digest_file(acceptance_path) == p2.get("acceptance_manifest_sha256")
        and product_review.get("candidate_id") == p2.get("candidate_id")
        and product_review.get("receipt_digest") == p2.get("product_semantic_review_digest")
        and digest_file(product_review_path) == p2.get("product_semantic_review_sha256")
        and product_review.get("status") == p2.get("required_status") == "PASS"
        and product_review.get("blocking_count") == p2.get("required_blocking_count") == 0,
        "新增确定性执行层必须绑定已验收 P2 同候选与独立产品语义 Reviewer",
        {
            "candidate_id": acceptance.get("candidate_id"),
            "review_status": product_review.get("status"),
            "blocking_count": product_review.get("blocking_count"),
        },
    )

    decision = policy.get("decision", {})
    check(
        "CERTIFICATION-RESPONSIBILITY-SPLIT",
        decision.get("base_model_certification_reused_as_new_runtime_certification") is False
        and decision.get("base_model_certification_remains_valid_for_unchanged_scope") is True
        and decision.get("new_registry_runtime_impact_certification_required") is True
        and decision.get("full_model_recertification_required") is False
        and decision.get("production_live_smoke_required") is True
        and decision.get("maximum_provider_request_count_for_live_smoke") == 1,
        "旧模型认证不得冒充新执行层认证；新执行层必须独立认证并做一次生产烟测",
        decision,
    )
    check(
        "RESOURCE-BOUNDARY",
        decision.get("fee_audit_gate") == "not_required"
        and decision.get("resource_gate") == "cpu_rss_call_count_and_error_log",
        "费用审计不是发布硬门，只审核调用次数、CPU、RSS 和错误日志",
        {
            "fee_audit_gate": decision.get("fee_audit_gate"),
            "resource_gate": decision.get("resource_gate"),
        },
    )

    reviewer_source = repo_root / REVIEWER_SOURCE
    reviewer_imports = imported_modules(reviewer_source)
    check(
        "REVIEW-INDEPENDENT",
        reviewer_source.resolve() != semantic_source_path.resolve()
        and all(
            "build_country_outage_p2_s0b_candidate" not in module
            for module in reviewer_imports
        ),
        "影响 Reviewer 与运行时实现和候选构建器职责分离",
        {"reviewer": str(REVIEWER_SOURCE), "imports": reviewer_imports},
    )

    blocking = [item for item in findings if item["status"] == "BLOCK"]
    receipt: Dict[str, Any] = {
        "schema_version": REVIEW_SCHEMA,
        "review_id": policy.get("policy_id"),
        "reviewed_at": policy.get("reviewed_at"),
        "reviewer_role_id": "p2-s0b6-certification-impact-reviewer-v1",
        "truth_derived_before_comparison": True,
        "truth_sources": [
            str(BASE_CERTIFICATION),
            str(P2_ACCEPTANCE),
            str(P2_PRODUCT_REVIEW),
            str(DEFAULT_POLICY),
        ],
        "base_certification_evidence_id": certification.get("evidence_id"),
        "p2_candidate_id": acceptance.get("candidate_id"),
        "policy_digest": digest_value(policy),
        "reviewer_source_digest": digest_file(reviewer_source),
        "source_mismatch_paths": mismatch_paths,
        "findings": findings,
        "blocking_items": blocking,
        "blocking_count": len(blocking),
        "status": "PASS" if not blocking else "BLOCK",
        "model_provider_calls": 0,
        "fee_audit_gate": "not_required",
        "does_not_prove": [
            "production_deployment",
            "production_live_smoke",
            "future_runtime_changes",
        ],
        "receipt_path": str(output_path.resolve().relative_to(repo_root.resolve())),
    }
    receipt["receipt_digest"] = digest_value(receipt)
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="独立审核 P2-S0B6 发布认证影响")
    result.add_argument("--repo-root", required=True)
    result.add_argument("--policy", default=str(DEFAULT_POLICY))
    result.add_argument("--output", required=True)
    result.add_argument("--check", action="store_true")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        repo_root = Path(args.repo_root).resolve()
        policy_path = Path(args.policy)
        if not policy_path.is_absolute():
            policy_path = repo_root / policy_path
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = repo_root / output_path
        result = review(repo_root, policy_path, output_path)
        if args.check:
            if load(output_path) != result:
                raise ImpactReviewError("影响 Reviewer 回执与当前源码或真值不一致")
        else:
            atomic_write(output_path, result)
        print(json.dumps({
            "status": result["status"],
            "p2_candidate_id": result["p2_candidate_id"],
            "blocking_count": result["blocking_count"],
            "receipt_digest": result["receipt_digest"],
        }, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "PASS" else 2
    except (ImpactReviewError, KeyError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCK", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
