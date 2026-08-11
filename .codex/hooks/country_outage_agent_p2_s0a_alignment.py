#!/usr/bin/env python3
"""P2-S0A Tool/Operator 生命周期治理 Alignment Hook。

Hook 只检查已冻结结构、身份、摘要、阶段依赖和非部署边界；退出码不能单独
证明运行时接入、部署、生产状态或用户价值。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


STAGES = ("S0A-0", "S0A-1", "S0A-2", "S0A-3", "S0A-4", "S0A-5", "S0A-6", "final")
EVIDENCE_ROOT = Path("evaluation/country-outage/p2-s0a-lifecycle")
TASK_SPEC = Path("docs/agent/P2-组合式调查/Tool与Operator生命周期治理/Task-Spec-最终验收文档.md")
PLAN = Path("docs/agent/P2-组合式调查/Tool与Operator生命周期治理/Plan-分阶段计划.md")
CONTRACT_ROOT = Path("contracts/agent/country-outage-p2-s0a-lifecycle")


class AlignmentError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AlignmentError("artifact_missing", f"缺少规范普通文件：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AlignmentError("artifact_json_invalid", f"JSON 无效：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise AlignmentError("artifact_json_invalid", f"JSON 根不是对象：{path}")
    return value


def require_file(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise AlignmentError("artifact_missing", f"缺少规范普通文件：{path}")


def load_manager(repo_root: Path):
    path = repo_root / "dev/tools/manage_country_outage_p2_registry.py"
    require_file(path)
    spec = importlib.util.spec_from_file_location("p2_s0a_registry_manager_for_alignment", path)
    if spec is None or spec.loader is None:
        raise AlignmentError("manager_import_failed", "无法装载离线 Registry 校验器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_receipt(
    path: Path,
    expected_stage: Optional[str] = None,
    task_spec_digest: Optional[str] = None,
    plan_digest: Optional[str] = None,
    candidate_id: Optional[str] = None,
) -> Dict[str, Any]:
    receipt = load_json(path)
    digest = receipt.get("receipt_digest")
    payload = dict(receipt)
    payload.pop("receipt_digest", None)
    if digest != digest_value(payload):
        raise AlignmentError("stage_receipt_digest_mismatch", f"阶段回执摘要漂移：{path}")
    if receipt.get("status") != "alignment_passed":
        raise AlignmentError("stage_receipt_failed", f"阶段回执未通过：{path}")
    if expected_stage is not None and receipt.get("stage") != expected_stage:
        raise AlignmentError("stage_receipt_identity_mismatch", f"阶段回执身份不一致：{path}")
    if receipt.get("runtime_integration") != "not_implemented" or receipt.get("production_deployed") is not False:
        raise AlignmentError("boundary_violation", f"阶段回执越过非运行时/非部署边界：{path}")
    if task_spec_digest is not None and receipt.get("task_spec_digest") != task_spec_digest:
        raise AlignmentError("stage_receipt_contract_drift", f"阶段回执绑定的 Task Spec 已漂移：{path}")
    if plan_digest is not None and receipt.get("plan_digest") != plan_digest:
        raise AlignmentError("stage_receipt_contract_drift", f"阶段回执绑定的 Plan 已漂移：{path}")
    if candidate_id is not None and expected_stage in STAGES[2:] and receipt.get("candidate_id") != candidate_id:
        raise AlignmentError("stage_receipt_candidate_drift", f"阶段回执绑定的 candidate 已漂移：{path}")
    return receipt


def previous_stages(stage: str) -> Sequence[str]:
    index = STAGES.index(stage)
    if stage == "final":
        return STAGES[:-1]
    return STAGES[:index]


def _check_task_spec(repo_root: Path, checks: List[str]) -> None:
    task_path = repo_root / TASK_SPEC
    plan_path = repo_root / PLAN
    require_file(task_path)
    require_file(plan_path)
    task = task_path.read_text(encoding="utf-8")
    plan = plan_path.read_text(encoding="utf-8")
    required_task_markers = (
        "country-outage-agent-p2-s0a-lifecycle-governance-v1",
        "Capability Registry",
        "Execution Unit Registry",
        "discovered → proposed → oracle_ready → certified → active → deprecated → retired → tombstoned",
        "Create SOP",
        "Read SOP",
        "Update SOP",
        "Deprecate SOP",
        "Retire SOP",
        "Delete SOP",
        "运行时快照绑定与计划准入",
        "非目标与后续入口",
        "不表示已接入、已部署或已在生产执行",
    )
    missing = [marker for marker in required_task_markers if marker not in task]
    if missing:
        raise AlignmentError("task_spec_marker_missing", f"Task Spec 缺少冻结标记：{missing}")
    for stage in STAGES[:-1]:
        if stage not in plan:
            raise AlignmentError("plan_stage_missing", f"Plan 缺少阶段 {stage}")
    if "不部署、不切换 prod32" not in plan or "Hook 通过只证明" not in plan:
        raise AlignmentError("plan_boundary_missing", "Plan 缺少非部署或 Hook 证据边界")
    checks.extend(["task_spec_markers", "plan_stage_map", "non_deployment_boundary"])


def _check_contracts(repo_root: Path, checks: List[str]) -> None:
    names = (
        "lifecycle-policy.json",
        "registry-set.schema.json",
        "governance-request.schema.json",
        "governance-receipt.schema.json",
        "plan-admission.schema.json",
        "product-semantic-charter.json",
        "question-probes.json",
        "governance-oracle.json",
    )
    for name in names:
        load_json(repo_root / CONTRACT_ROOT / name)
    policy = load_json(repo_root / CONTRACT_ROOT / "lifecycle-policy.json")
    expected_states = ["discovered", "proposed", "oracle_ready", "certified", "active", "deprecated", "retired", "tombstoned"]
    if policy.get("states") != expected_states:
        raise AlignmentError("lifecycle_policy_drift", "生命周期状态或顺序漂移")
    if policy.get("activation_scope") != "offline_candidate_only" or policy.get("runtime_integration") != "not_implemented":
        raise AlignmentError("boundary_violation", "生命周期 Policy 越过离线边界")
    oracle = load_json(repo_root / CONTRACT_ROOT / "governance-oracle.json")
    expected_categories = {"normal", "missing", "null", "wrong_identity", "unavailable", "boundary", "migration", "tamper", "rollback", "plan_admission"}
    if set(oracle.get("categories", [])) != expected_categories:
        raise AlignmentError("oracle_coverage_missing", "治理 Oracle 十类覆盖不完整")
    checks.extend(["machine_contracts_json", "lifecycle_state_order", "governance_oracle_categories"])


def _check_migration(repo_root: Path, checks: List[str]) -> str:
    proposal = load_json(repo_root / EVIDENCE_ROOT / "migration-proposal.json")
    if proposal.get("schema_version") != "country_outage_p2_s0a_migration_proposal_v1":
        raise AlignmentError("proposal_invalid", "迁移 proposal Schema 无效")
    summary = proposal.get("migration_summary", {})
    expected = {"capability_count": 18, "execution_unit_count": 10, "tool_count": 6, "base_operator_count": 3, "independent_operator_count": 1, "op04_version": "1.2.0"}
    for key, value in expected.items():
        if summary.get(key) != value:
            raise AlignmentError("migration_count_mismatch", f"迁移 {key} 不等于 {value}")
    if summary.get("runtime_behavior_changed") is not False or summary.get("production_deployed") is not False:
        raise AlignmentError("boundary_violation", "迁移 proposal 越过行为不变/非部署边界")
    states = {
        entry.get("state")
        for registry_key in ("capability_registry", "execution_unit_registry")
        for entry in proposal.get("registry_set", {}).get(registry_key, {}).get("entries", [])
    }
    if states != {"oracle_ready"}:
        raise AlignmentError("proposal_state_invalid", f"proposal 必须全为 oracle_ready，实际 {states}")
    checks.extend(["migration_18_capabilities", "migration_10_execution_units", "proposal_oracle_ready_only"])
    return str(proposal.get("candidate_id"))


def _check_registry(repo_root: Path, checks: List[str]) -> str:
    manager = load_manager(repo_root)
    registry_path = repo_root / CONTRACT_ROOT / "registry-set.json"
    snapshot_path = repo_root / CONTRACT_ROOT / "registry-snapshot.json"
    registry_set = load_json(registry_path)
    snapshot = load_json(snapshot_path)
    try:
        result = manager.validate_registry_set(registry_set, require_active_snapshot=True)
        manager.validate_snapshot(snapshot)
    except Exception as exc:
        code = getattr(exc, "code", "registry_invalid")
        raise AlignmentError(str(code), f"Registry 校验失败：{exc}") from exc
    if result.get("capability_count") != 18 or result.get("execution_unit_count") != 10:
        raise AlignmentError("registry_count_mismatch", "最终 Registry 迁移数量漂移")
    if result.get("active_capability_count") != 18 or result.get("active_execution_unit_count") != 10:
        raise AlignmentError("registry_active_count_mismatch", "最终离线 active 集合不完整")
    if snapshot.get("registry_snapshot_id") != registry_set.get("active_snapshot_id"):
        raise AlignmentError("registry_snapshot_conflict", "活动快照指针不一致")
    if snapshot.get("snapshot_payload") != manager.snapshot_payload(registry_set):
        raise AlignmentError("registry_snapshot_conflict", "Registry 当前内容与快照不一致")
    candidate = load_json(repo_root / CONTRACT_ROOT / "candidate.json")
    if candidate.get("candidate_id") != registry_set.get("candidate_id") or candidate.get("registry_snapshot_id") != snapshot.get("registry_snapshot_id"):
        raise AlignmentError("candidate_identity_conflict", "candidate/registry/snapshot 身份不一致")
    if candidate.get("activation_scope") != "offline_candidate_only" or candidate.get("runtime_integration") != "not_implemented" or candidate.get("production_deployed") is not False:
        raise AlignmentError("boundary_violation", "candidate 越过离线/非部署边界")
    artifacts = candidate.get("artifact_digests", {})
    for name, expected_digest in artifacts.items():
        actual = digest_file(repo_root / CONTRACT_ROOT / name)
        if actual != expected_digest:
            raise AlignmentError("candidate_artifact_digest_mismatch", f"candidate 制品摘要漂移：{name}")
    migration = load_json(repo_root / CONTRACT_ROOT / "migration-map.json")
    if len(migration.get("capabilities", [])) != 18 or len(migration.get("execution_units", [])) != 10:
        raise AlignmentError("migration_count_mismatch", "migration-map 数量漂移")
    if any(item.get("semantic_change") != "none" for item in migration.get("capabilities", []) + migration.get("execution_units", [])):
        raise AlignmentError("migration_semantic_drift", "迁移出现产品语义变化")
    checks.extend(["registry_validator", "snapshot_content_address", "candidate_artifact_digests", "migration_semantic_equivalence"])
    return str(candidate.get("candidate_id"))


def _verify_review(path: Path, candidate_id: str) -> None:
    review = load_json(path)
    digest = review.get("receipt_digest")
    payload = dict(review)
    payload.pop("receipt_digest", None)
    if digest != digest_value(payload):
        raise AlignmentError("reviewer_receipt_digest_mismatch", f"Reviewer 回执摘要漂移：{path}")
    if review.get("candidate_id") != candidate_id or review.get("status") != "PASS" or review.get("blocking_count") != 0:
        raise AlignmentError("reviewer_blocked", f"Reviewer 未通过或 candidate 不一致：{path}")
    if review.get("reviewer_role_id") == review.get("builder_role_id"):
        raise AlignmentError("reviewer_not_independent", f"Reviewer 与构建者角色相同：{path}")
    if review.get("runtime_integration") != "not_implemented" or review.get("production_deployed") is not False:
        raise AlignmentError("boundary_violation", f"Reviewer 越过非运行时/非部署边界：{path}")


def _check_review(repo_root: Path, candidate_id: str, checks: List[str]) -> None:
    _verify_review(repo_root / EVIDENCE_ROOT / "product-semantic-proposal-review.json", candidate_id)
    _verify_review(repo_root / EVIDENCE_ROOT / "product-semantic-final-review.json", candidate_id)
    checks.extend(["proposal_product_semantic_review", "final_product_semantic_review", "reviewer_separation_of_duties"])


def _check_rollback_evidence(repo_root: Path, checks: List[str]) -> None:
    required = (
        "single-unit-rollback-receipt.json",
        "whole-edition-rollback-receipt.json",
        "tombstone-receipt.json",
        "plan-admission-receipt.json",
    )
    for name in required:
        receipt = load_json(repo_root / EVIDENCE_ROOT / "governance" / name)
        if receipt.get("status") not in ("applied", "admitted"):
            raise AlignmentError("governance_evidence_failed", f"治理证据未通过：{name}")
    edition = load_json(repo_root / EVIDENCE_ROOT / "governance/whole-edition-rollback-receipt.json")
    if edition.get("impact", {}).get("snapshot_mode") != "whole_edition" or edition.get("impact", {}).get("mixed_snapshot") is not False:
        raise AlignmentError("rollback_evidence_invalid", "整版回滚不是完整快照模式")
    tombstone = load_json(repo_root / EVIDENCE_ROOT / "governance/tombstone-receipt.json")
    if tombstone.get("tombstone", {}).get("id_reuse_forbidden") is not True:
        raise AlignmentError("tombstone_evidence_invalid", "Tombstone 未证明 ID 永不复用")
    checks.extend(["single_unit_rollback_evidence", "whole_edition_rollback_evidence", "tombstone_evidence", "plan_admission_evidence"])


def _check_acceptance(repo_root: Path, candidate_id: str, checks: List[str]) -> None:
    manifest = load_json(repo_root / EVIDENCE_ROOT / "acceptance-manifest.json")
    manifest_digest = manifest.get("manifest_digest")
    manifest_payload = dict(manifest)
    manifest_payload.pop("manifest_digest", None)
    if manifest_digest != digest_value(manifest_payload):
        raise AlignmentError("acceptance_manifest_digest_mismatch", "最终验收 manifest 摘要漂移")
    if manifest.get("candidate_id") != candidate_id or manifest.get("status") != "accepted_offline_candidate":
        raise AlignmentError("acceptance_manifest_invalid", "最终验收 manifest 身份或状态无效")
    if manifest.get("runtime_integration") != "not_implemented" or manifest.get("production_deployed") is not False:
        raise AlignmentError("boundary_violation", "验收 manifest 越过非运行时/非部署边界")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) < 30:
        raise AlignmentError("acceptance_manifest_invalid", "最终验收 manifest 制品集合不完整")
    for item in artifacts:
        path = repo_root / item["path"]
        if digest_file(path) != item["sha256"]:
            raise AlignmentError("acceptance_artifact_digest_mismatch", f"最终验收制品摘要漂移：{item['path']}")
    gates = manifest.get("gates", {})
    if gates.get("registry_test_count", 0) < 8 or gates.get("alignment_test_count", 0) < 6 or gates.get("product_semantic_blocking_count") != 0:
        raise AlignmentError("acceptance_gate_failed", "最终验收量化门未满足")
    checks.extend(["same_candidate_acceptance_manifest", "acceptance_artifact_digests", "quantified_acceptance_gates"])


def run_alignment(repo_root: Path, stage: str) -> Dict[str, Any]:
    if stage not in STAGES:
        raise AlignmentError("stage_invalid", f"未知阶段：{stage}")
    checks: List[str] = []
    _check_task_spec(repo_root, checks)
    candidate_id = "not-yet-created"
    stage_index = STAGES.index(stage)
    if stage_index >= STAGES.index("S0A-1"):
        _check_contracts(repo_root, checks)
    if stage_index >= STAGES.index("S0A-2"):
        candidate_id = _check_migration(repo_root, checks)
    if stage_index >= STAGES.index("S0A-3"):
        candidate_id = _check_registry(repo_root, checks)
    if stage_index >= STAGES.index("S0A-4"):
        _check_review(repo_root, candidate_id, checks)
    if stage_index >= STAGES.index("S0A-5"):
        _check_rollback_evidence(repo_root, checks)
    if stage_index >= STAGES.index("S0A-6"):
        _check_acceptance(repo_root, candidate_id, checks)
    task_spec_digest = digest_file(repo_root / TASK_SPEC)
    plan_digest = digest_file(repo_root / PLAN)
    for previous in previous_stages(stage):
        verify_receipt(
            repo_root / EVIDENCE_ROOT / "stages" / f"{previous}.json",
            previous,
            task_spec_digest,
            plan_digest,
            candidate_id if stage_index >= STAGES.index("S0A-2") else None,
        )
    return {
        "schema_version": "country_outage_p2_s0a_alignment_receipt_v1",
        "stage": stage,
        "status": "alignment_passed",
        "task_spec_version": "country-outage-agent-p2-s0a-lifecycle-governance-v1",
        "plan_version": "country-outage-agent-p2-s0a-lifecycle-plan-v1",
        "candidate_id": candidate_id,
        "checks": checks,
        "task_spec_digest": task_spec_digest,
        "plan_digest": plan_digest,
        "runtime_integration": "not_implemented",
        "production_deployed": False,
        "hook_limit": "结构、身份、摘要与已知边界检查；不单独证明产品或生产验收",
    }


def write_receipt(path: Path, payload: Mapping[str, Any], at_utc: str) -> Dict[str, Any]:
    value = {**payload, "at_utc": at_utc}
    value["receipt_digest"] = digest_value(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise AlignmentError("unsafe_output", f"回执输出不是普通文件：{path}")
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="P2-S0A 生命周期治理 Alignment Hook")
    result.add_argument("--repo-root", required=True)
    result.add_argument("--stage", choices=STAGES, required=True)
    result.add_argument("--output")
    result.add_argument("--at-utc")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        repo_root = Path(args.repo_root).resolve()
        result = run_alignment(repo_root, args.stage)
        if args.output:
            output = Path(args.output)
            if not output.is_absolute():
                output = repo_root / output
            at_utc = args.at_utc or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            result = write_receipt(output.resolve(), result, at_utc)
        sys.stdout.write(json.dumps({"status": result["status"], "stage": result["stage"], "candidate_id": result["candidate_id"], "check_count": len(result["checks"])}, ensure_ascii=False, sort_keys=True) + "\n")
        return 0
    except AlignmentError as exc:
        sys.stdout.write(json.dumps({"status": "alignment_blocked", "error_code": exc.code, "message": str(exc)}, ensure_ascii=False, sort_keys=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
