#!/usr/bin/env python3
"""P2-S0B Runtime Registry 阶段 Alignment Hook。

Hook 只证明已冻结结构、身份、摘要、阶段依赖和非部署边界；不能单独证明生产发布或用户价值。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


STAGES = ("S0B-0", "S0B-1", "S0B-2", "S0B-3", "S0B-4", "S0B-5", "final")
CONTRACT_ROOT = Path("contracts/agent/country-outage-p2-s0b-runtime")
EVIDENCE_ROOT = Path("evaluation/country-outage/p2-s0b-runtime")
TASK_SPEC = Path("docs/agent/P2-组合式调查/Tool与Operator生命周期治理/P2-S0B-运行时接入/Task-Spec-最终验收文档.md")
PLAN = Path("docs/agent/P2-组合式调查/Tool与Operator生命周期治理/P2-S0B-运行时接入/Plan-执行与验收.md")


class AlignmentError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load(path: Path) -> Dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AlignmentError("artifact_missing", f"缺少规范普通文件：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AlignmentError("artifact_json_invalid", f"JSON 无效：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise AlignmentError("artifact_json_invalid", f"JSON 根必须是对象：{path}")
    return value


def digest_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise AlignmentError("artifact_missing", f"缺少规范普通文件：{path}")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_number(value: int | float) -> str:
    decimal_value = Decimal(str(value)).normalize()
    if not decimal_value.is_finite():
        raise AlignmentError("digest_invalid", "摘要输入包含非有限数字")
    if decimal_value.is_zero():
        return "0"
    sign, digits_tuple, exponent = decimal_value.as_tuple()
    digits = "".join(str(digit) for digit in digits_tuple)
    scientific_exponent = exponent + len(digits) - 1
    coefficient = digits[0] + (("." + digits[1:]) if len(digits) > 1 else "")
    return ("-" if sign else "") + coefficient + "e" + str(scientific_exponent)


def canonical_text(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return canonical_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(canonical_text(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + canonical_text(value[key])
            for key in sorted(value)
        ) + "}"
    raise AlignmentError("digest_invalid", f"不支持的摘要类型：{type(value).__name__}")


def digest_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_text(value).encode("utf-8")).hexdigest()


def plain_digest_value(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def check_docs(repo_root: Path, checks: List[str]) -> None:
    task = (repo_root / TASK_SPEC).read_text(encoding="utf-8")
    plan = (repo_root / PLAN).read_text(encoding="utf-8")
    task_markers = (
        "country-outage-agent-p2-s0b-governed-auto-invocation-v1",
        "最终效果",
        "Snapshot Loader",
        "Capability Resolver",
        "Plan Admission",
        "Executor 与 Evidence",
        "required_call_missing",
        "Host Handler",
        "本 Task Spec 通过不等于生产可用",
    )
    missing = [marker for marker in task_markers if marker not in task]
    if missing:
        raise AlignmentError("task_spec_marker_missing", f"Task Spec 缺少标记：{missing}")
    if any(f"S0B-{index}" not in plan for index in range(7)):
        raise AlignmentError("plan_stage_missing", "Plan 未覆盖 S0B-0..6")
    if "本轮不执行" not in plan or "不能替代" not in plan:
        raise AlignmentError("plan_boundary_missing", "Plan 缺少生产非目标或证据边界")
    checks.extend(["task_spec_contract", "plan_stage_map", "non_deployment_boundary"])


def check_contracts(repo_root: Path, checks: List[str]) -> None:
    runtime = load(repo_root / CONTRACT_ROOT / "runtime-contract.json")
    schema = load(repo_root / CONTRACT_ROOT / "runtime-plan-admission.schema.json")
    oracle = load(repo_root / CONTRACT_ROOT / "shadow-oracle.json")
    if runtime.get("interfaces", {}).get("snapshot_loader", {}).get("snapshot_digest_algorithm") != "p2-s0b-canonical-json-v1+sha256":
        raise AlignmentError("runtime_contract_drift", "快照摘要算法未冻结")
    if runtime.get("interfaces", {}).get("plan_admission", {}).get("must_precede_executor") is not True:
        raise AlignmentError("runtime_contract_drift", "Admission 未冻结为 Executor 前置门")
    modes = runtime.get("runtime_modes", {})
    if (
        modes.get("shadow", {}).get("production_deployed") is not False
        or modes.get("production", {}).get("production_deployed") is not True
        or modes.get("cross_mode_fallback") != "forbidden"
    ):
        raise AlignmentError("runtime_contract_drift", "shadow/production 模式隔离合同漂移")
    handlers = {item.get("unit_id") for item in runtime.get("supported_handlers", [])}
    expected = {f"TOOL-{index:02d}" for index in range(1, 7)} | {f"OP-{index:02d}" for index in range(1, 5)}
    if handlers != expected:
        raise AlignmentError("handler_set_drift", f"Host Handler 集漂移：{sorted(handlers)}")
    categories = set(oracle.get("categories", []))
    if len(categories) != 14 or len(oracle.get("cases", [])) < 14:
        raise AlignmentError("oracle_coverage_missing", "Shadow Oracle 未覆盖 14 类")
    if (
        schema.get("properties", {}).get("production_deployed", {}).get("type") != "boolean"
        or len(schema.get("allOf", [])) != 1
    ):
        raise AlignmentError("boundary_violation", "Admission Schema 缺少运行模式部署一致性门")
    checks.extend(["runtime_interfaces", "runtime_mode_isolation", "handler_allowlist", "shadow_oracle_14_categories", "admission_schema"])


def check_candidate(repo_root: Path, checks: List[str]) -> str:
    candidate = load(repo_root / CONTRACT_ROOT / "candidate.json")
    snapshot = load(repo_root / CONTRACT_ROOT / "registry-snapshot.json")
    payload = snapshot.get("snapshot_payload", {})
    expected_digest = digest_value(payload)
    expected_id = "registry-snapshot-sha256:" + expected_digest.split(":", 1)[1]
    if snapshot.get("snapshot_digest") != expected_digest or snapshot.get("registry_snapshot_id") != expected_id:
        raise AlignmentError("snapshot_digest_mismatch", "S0B 快照内容寻址摘要漂移")
    if candidate.get("candidate_id") != payload.get("candidate_id") or candidate.get("registry_snapshot_id") != expected_id:
        raise AlignmentError("candidate_identity_mismatch", "candidate 与 snapshot 身份不闭合")
    if candidate.get("runtime_integration") != "implemented_not_deployed" or candidate.get("production_deployed") is not False or candidate.get("prod32_switched") is not False:
        raise AlignmentError("boundary_violation", "candidate 越过未部署边界")
    capabilities = payload.get("capability_registry", {}).get("entries", [])
    units = payload.get("execution_unit_registry", {}).get("entries", [])
    if len(capabilities) != 18 or len(units) != 10:
        raise AlignmentError("migration_count_mismatch", "迁移必须为 18 Capability/10 Unit")
    for name, expected in candidate.get("artifact_digests", {}).items():
        if digest_file(repo_root / CONTRACT_ROOT / name) != expected:
            raise AlignmentError("candidate_artifact_drift", f"候选制品摘要漂移：{name}")
    for item in candidate.get("source_identity", {}).get("runtime_material", []):
        if digest_file(repo_root / item["path"]) != item.get("sha256"):
            raise AlignmentError("candidate_source_drift", f"候选源码摘要漂移：{item.get('path')}")
    checks.extend(["candidate_identity", "snapshot_content_address", "migration_18_10", "candidate_source_digests"])
    return str(candidate.get("candidate_id"))


def check_runtime(repo_root: Path, checks: List[str]) -> None:
    runtime = (repo_root / "agent-sidecar/src/chat/p2-registry-runtime.ts").read_text(encoding="utf-8")
    semantic = (repo_root / "agent-sidecar/src/chat/runtime-v2-semantic.ts").read_text(encoding="utf-8")
    if any(marker in runtime for marker in ("writeFile", "appendFile", "renameSync", "unlinkSync", "mkdirSync")):
        raise AlignmentError("runtime_write_surface", "Runtime Registry 出现文件写入口")
    required = ("P2RegistrySnapshotLoader", "deepFreeze", "admitPlan(", "required_call_missing", "execution_handler_missing", "capability_unit_digest_mismatch")
    missing = [marker for marker in required if marker not in runtime]
    if missing:
        raise AlignmentError("runtime_marker_missing", f"Runtime 缺少准入标记：{missing}")
    admission_at = semantic.find("admitPlan(")
    executor_at = semantic.find("executor.execute(", admission_at)
    if admission_at < 0 or executor_at <= admission_at:
        raise AlignmentError("runtime_order_invalid", "真实调用链没有在 Executor 前完成 Admission")
    checks.extend(["runtime_read_only", "snapshot_once_per_turn", "active_digest_dependency_handler_admission", "admission_before_executor"])


def check_executor(repo_root: Path, checks: List[str]) -> None:
    executor = (repo_root / "agent-sidecar/src/chat/page-capability-executor.ts").read_text(encoding="utf-8")
    semantic = (repo_root / "agent-sidecar/src/chat/runtime-v2-semantic.ts").read_text(encoding="utf-8")
    markers = ("registry_admission_missing", "registry_snapshot_id", "execution_unit_version", "unit_contract_digest", "unit_implementation_digest", "unit_semantic_digest")
    if any(marker not in executor for marker in markers):
        raise AlignmentError("executor_binding_missing", "Executor 回执 Registry 身份不完整")
    if "registry_admission" not in semantic or "registry_snapshot_id" not in semantic:
        raise AlignmentError("answer_identity_missing", "回答级 Runtime identity 不完整")
    checks.extend(["executor_rejects_unadmitted", "execution_receipt_identity", "answer_runtime_identity"])


def verify_review(repo_root: Path, candidate_id: str, checks: List[str]) -> None:
    review = load(repo_root / EVIDENCE_ROOT / "product-semantic-review.json")
    payload = dict(review)
    receipt_digest = payload.pop("receipt_digest", None)
    if receipt_digest != plain_digest_value(payload):
        raise AlignmentError("review_digest_mismatch", "Reviewer 回执摘要漂移")
    if review.get("status") != "PASS" or review.get("blocking_count") != 0 or review.get("candidate_id") != candidate_id:
        raise AlignmentError("review_blocked", "Reviewer 未通过或候选身份不一致")
    if review.get("reviewer_role_id") == review.get("builder_role_id"):
        raise AlignmentError("reviewer_not_independent", "Reviewer 与构建者角色未分离")
    checks.extend(["independent_product_semantic_review", "zero_semantic_blockers"])


def verify_summary(repo_root: Path, candidate_id: str, checks: List[str]) -> None:
    summary = load(repo_root / EVIDENCE_ROOT / "same-candidate-test-summary.json")
    payload = dict(summary)
    receipt_digest = payload.pop("receipt_digest", None)
    if receipt_digest != digest_value(payload):
        raise AlignmentError("test_summary_digest_mismatch", "测试汇总摘要漂移")
    gates = summary.get("gates", {})
    if summary.get("status") != "PASS" or summary.get("candidate_id") != candidate_id:
        raise AlignmentError("test_summary_invalid", "测试汇总未通过或候选身份不一致")
    if gates.get("python_test_count", 0) < 8 or gates.get("typescript_and_p1_test_count", 0) < 55 or gates.get("typescript_and_p1_pass_count") != gates.get("typescript_and_p1_test_count"):
        raise AlignmentError("test_gate_failed", "Python、TypeScript 或 P1 回归量化门未满足")
    if any(gates.get(name) != 0 for name in ("production_deployment_count", "prod32_switch_count", "remote_write_count")):
        raise AlignmentError("boundary_violation", "测试汇总记录到生产或远程写入")
    checks.extend(["same_candidate_tests", "quantified_test_gates", "zero_production_writes"])


def verify_acceptance(repo_root: Path, candidate_id: str, checks: List[str]) -> None:
    manifest = load(repo_root / EVIDENCE_ROOT / "acceptance-manifest.json")
    payload = dict(manifest)
    manifest_digest = payload.pop("manifest_digest", None)
    if manifest_digest != digest_value(payload):
        raise AlignmentError("acceptance_digest_mismatch", "验收 manifest 摘要漂移")
    if manifest.get("status") != "accepted_local_shadow_candidate" or manifest.get("candidate_id") != candidate_id:
        raise AlignmentError("acceptance_invalid", "验收 manifest 状态或候选身份无效")
    if manifest.get("production_deployed") is not False or manifest.get("prod32_switched") is not False:
        raise AlignmentError("boundary_violation", "验收 manifest 越过未部署边界")
    artifacts = manifest.get("artifacts", [])
    if len(artifacts) < 15:
        raise AlignmentError("acceptance_incomplete", "验收制品集合不完整")
    for item in artifacts:
        if digest_file(repo_root / item["path"]) != item.get("sha256"):
            raise AlignmentError("acceptance_artifact_drift", f"验收制品漂移：{item.get('path')}")
    checks.extend(["same_candidate_acceptance", "acceptance_artifact_digests", "local_shadow_only"])


def verify_stage_receipt(path: Path, expected_stage: str, task_digest: str, plan_digest: str, candidate_id: Optional[str]) -> None:
    receipt = load(path)
    payload = dict(receipt)
    actual_digest = payload.pop("receipt_digest", None)
    if actual_digest != digest_value(payload):
        raise AlignmentError("stage_receipt_digest_mismatch", f"阶段回执被篡改：{path}")
    if receipt.get("status") != "alignment_passed" or receipt.get("stage") != expected_stage:
        raise AlignmentError("stage_receipt_invalid", f"阶段回执无效：{path}")
    if receipt.get("task_spec_digest") != task_digest or receipt.get("plan_digest") != plan_digest:
        raise AlignmentError("stage_contract_drift", f"阶段回执合同漂移：{path}")
    if candidate_id is not None and receipt.get("candidate_id") != candidate_id:
        raise AlignmentError("stage_candidate_drift", f"阶段回执候选漂移：{path}")


def run_alignment(repo_root: Path, stage: str) -> Dict[str, Any]:
    if stage not in STAGES:
        raise AlignmentError("stage_invalid", f"未知阶段：{stage}")
    checks: List[str] = []
    check_docs(repo_root, checks)
    check_contracts(repo_root, checks)
    stage_index = STAGES.index(stage)
    candidate_id = "not-created"
    if stage_index >= STAGES.index("S0B-1"):
        candidate_id = check_candidate(repo_root, checks)
    if stage_index >= STAGES.index("S0B-2"):
        check_runtime(repo_root, checks)
    if stage_index >= STAGES.index("S0B-3"):
        check_executor(repo_root, checks)
    if stage_index >= STAGES.index("S0B-4"):
        verify_review(repo_root, candidate_id, checks)
        verify_summary(repo_root, candidate_id, checks)
    if stage_index >= STAGES.index("S0B-5"):
        verify_acceptance(repo_root, candidate_id, checks)

    task_digest = digest_file(repo_root / TASK_SPEC)
    plan_digest = digest_file(repo_root / PLAN)
    previous = STAGES[:stage_index] if stage != "final" else STAGES[:-1]
    for previous_stage in previous:
        previous_candidate = candidate_id if STAGES.index(previous_stage) >= STAGES.index("S0B-1") else None
        verify_stage_receipt(
            repo_root / EVIDENCE_ROOT / "stages" / f"{previous_stage}.json",
            previous_stage,
            task_digest,
            plan_digest,
            previous_candidate,
        )
    return {
        "schema_version": "country_outage_p2_s0b_alignment_receipt_v1",
        "stage": stage,
        "status": "alignment_passed",
        "task_spec_version": "country-outage-agent-p2-s0b-governed-auto-invocation-v1",
        "plan_version": "country-outage-agent-p2-s0b-execution-plan-v1",
        "candidate_id": candidate_id,
        "checks": checks,
        "task_spec_digest": task_digest,
        "plan_digest": plan_digest,
        "runtime_integration": "implemented_not_deployed",
        "production_deployed": False,
        "prod32_switched": False,
        "hook_limit": "只检查合同、结构、身份、摘要和阶段依赖；不单独证明生产部署或用户价值",
    }


def write_receipt(path: Path, payload: Mapping[str, Any], at_utc: str) -> Dict[str, Any]:
    value = {**payload, "at_utc": at_utc}
    value["receipt_digest"] = digest_value(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise AlignmentError("unsafe_output", f"回执输出不是规范普通文件：{path}")
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
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="P2-S0B Runtime Registry Alignment Hook")
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
            result = write_receipt(Path(os.path.abspath(output)), result, at_utc)
        print(json.dumps({"status": result["status"], "stage": result["stage"], "candidate_id": result["candidate_id"], "check_count": len(result["checks"])}, ensure_ascii=False, sort_keys=True))
        return 0
    except (AlignmentError, OSError) as exc:
        print(json.dumps({"status": "alignment_blocked", "error_code": getattr(exc, "code", "io_error"), "message": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
