#!/usr/bin/env python3
"""从已验收 P2-S0A 快照生成 P2-S0B 只读运行时候选。

该脚本只读取仓库文件并写调用者指定的本地输出目录；不连接运行时、生产数据库或远程
状态，也不部署。S0A 合同保持不变，S0B 使用新的 candidate 与内容寻址快照。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence


BASE_SNAPSHOT = Path("contracts/agent/country-outage-p2-s0a-lifecycle/registry-snapshot.json")
RUNTIME_ROOT = Path("contracts/agent/country-outage-p2-s0b-runtime")
RUNTIME_SOURCES = (
    "agent-sidecar/src/chat/p2-registry-runtime.ts",
    "agent-sidecar/src/chat/runtime-v2-semantic.ts",
    "agent-sidecar/src/chat/page-capability-executor.ts",
    "agent-sidecar/src/chat/index.ts",
)
RUNTIME_SOURCE_PATH = Path(RUNTIME_SOURCES[0])
REVIEWER_SOURCE_PATH = Path("dev/tools/review_country_outage_p2_s0b_semantics.py")
TASK_SPEC_PATH = Path("docs/agent/P2-组合式调查/Tool与Operator生命周期治理/P2-S0B-运行时接入/Task-Spec-最终验收文档.md")
PLAN_PATH = Path("docs/agent/P2-组合式调查/Tool与Operator生命周期治理/P2-S0B-运行时接入/Plan-执行与验收.md")


class CandidateBuildError(RuntimeError):
    pass


def canonical_number(value: int | float) -> str:
    """生成不依赖 Python/JavaScript 默认格式的有限 JSON 数字。"""
    decimal_value = Decimal(str(value)).normalize()
    if not decimal_value.is_finite():
        raise CandidateBuildError("摘要输入包含非有限数字")
    if decimal_value.is_zero():
        return "0"
    sign, digits_tuple, exponent = decimal_value.as_tuple()
    digits = "".join(str(digit) for digit in digits_tuple)
    scientific_exponent = exponent + len(digits) - 1
    coefficient = digits[0]
    if len(digits) > 1:
        coefficient += "." + digits[1:]
    return ("-" if sign else "") + coefficient + "e" + str(scientific_exponent)


def canonical_text(value: Any) -> str:
    """P2-S0B canonical-json-v1：键按码点排序，数字统一为科学计数法。"""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return canonical_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(canonical_text(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise CandidateBuildError("摘要对象键必须是字符串")
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + canonical_text(value[key])
            for key in sorted(value)
        ) + "}"
    raise CandidateBuildError(f"摘要输入包含不支持的类型：{type(value).__name__}")


def canonical(value: Any) -> bytes:
    return canonical_text(value).encode("utf-8")


def digest_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> MutableMapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise CandidateBuildError(f"不是规范普通文件：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateBuildError(f"JSON 无效：{path}：{exc}") from exc
    if not isinstance(value, dict):
        raise CandidateBuildError(f"JSON 根必须是对象：{path}")
    return value


def parse_utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateBuildError(f"时间不是规范 UTC：{value}") from exc
    if parsed.tzinfo is None:
        raise CandidateBuildError(f"时间缺少时区：{value}")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path = Path(os.path.abspath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise CandidateBuildError(f"输出不是规范普通文件：{path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
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


def plain_digest_value(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def append_binding_history(entry: MutableMapping[str, Any], created_at: str) -> None:
    history = entry.get("lifecycle_history")
    if not isinstance(history, list):
        raise CandidateBuildError("Registry entry lifecycle_history 无效")
    payload = {
        "sequence": len(history) + 1,
        "action": "runtime_candidate_binding",
        "actor": "p2-s0b-runtime-candidate-builder-v1",
        "at_utc": created_at,
        "from_state": entry.get("state"),
        "to_state": entry.get("state"),
        "reason": "绑定到只读 Runtime Snapshot 与执行前准入；不表示生产部署",
    }
    payload["receipt_digest"] = digest_value(payload)
    history.append(payload)


def refresh_implementation_manifests(repo_root: Path, payload: MutableMapping[str, Any], created_at: str) -> None:
    unit_registry = payload.get("execution_unit_registry")
    capability_registry = payload.get("capability_registry")
    if not isinstance(unit_registry, dict) or not isinstance(unit_registry.get("entries"), list):
        raise CandidateBuildError("Execution Unit Registry 无效")
    if not isinstance(capability_registry, dict) or not isinstance(capability_registry.get("entries"), list):
        raise CandidateBuildError("Capability Registry 无效")
    units: Dict[tuple[str, str], MutableMapping[str, Any]] = {}
    for raw in unit_registry["entries"]:
        if not isinstance(raw, dict):
            raise CandidateBuildError("Execution Unit entry 无效")
        manifest = raw.get("implementation_files")
        if not isinstance(manifest, list) or not manifest:
            raise CandidateBuildError(f"{raw.get('unit_id')} implementation_files 无效")
        refreshed = []
        for item in manifest:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise CandidateBuildError("implementation_files item 无效")
            source = repo_root / item["path"]
            if not source.is_file() or source.is_symlink():
                raise CandidateBuildError(f"实现文件不存在或不安全：{item['path']}")
            refreshed.append({"path": item["path"], "sha256": digest_file(source)})
        raw["implementation_files"] = refreshed
        raw["implementation_digest"] = digest_value(refreshed)
        append_binding_history(raw, created_at)
        units[(str(raw.get("unit_id")), str(raw.get("version")))] = raw
    for raw in capability_registry["entries"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("execution_units"), list):
            raise CandidateBuildError("Capability entry 无效")
        references = []
        for reference in raw["execution_units"]:
            if not isinstance(reference, dict):
                raise CandidateBuildError("Capability execution unit reference 无效")
            key = (str(reference.get("unit_id")), str(reference.get("version")))
            unit = units.get(key)
            if unit is None:
                raise CandidateBuildError(f"Capability 引用未知 Execution Unit：{key}")
            references.append({
                "unit_id": unit["unit_id"],
                "version": unit["version"],
                "contract_digest": unit["contract_digest"],
                "implementation_digest": unit["implementation_digest"],
                "semantic_digest": unit["semantic_digest"],
            })
        raw["execution_units"] = references
        raw["implementation_digest"] = digest_value(references)
        append_binding_history(raw, created_at)


def build(repo_root: Path, created_at: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    created_at = parse_utc(created_at)
    base = load(repo_root / BASE_SNAPSHOT)
    if base.get("production_deployed") is not False:
        raise CandidateBuildError("S0A 基线越过非部署边界")
    payload = copy.deepcopy(base.get("snapshot_payload"))
    if not isinstance(payload, dict):
        raise CandidateBuildError("S0A snapshot_payload 无效")
    runtime_material = []
    for relative in (
        str(RUNTIME_ROOT / "runtime-contract.json"),
        str(RUNTIME_ROOT / "runtime-plan-admission.schema.json"),
        str(RUNTIME_ROOT / "shadow-oracle.json"),
        *RUNTIME_SOURCES,
    ):
        path = repo_root / relative
        if not path.is_file() or path.is_symlink():
            raise CandidateBuildError(f"运行时候选来源缺失或不安全：{relative}")
        runtime_material.append({"path": relative, "sha256": digest_file(path)})
    identity_material = {
        "base_candidate_id": payload.get("candidate_id"),
        "base_registry_snapshot_id": base.get("registry_snapshot_id"),
        "runtime_material": runtime_material,
    }
    identity_digest = digest_value(identity_material)
    candidate_id = f"p2-s0b-{identity_digest.split(':', 1)[1][:16]}"
    payload["candidate_id"] = candidate_id
    payload["registry_revision"] = int(payload.get("registry_revision", 0)) + 1
    payload["activation_scope"] = "runtime_candidate_shadow_only"
    payload["runtime_integration"] = "implemented_not_deployed"
    refresh_implementation_manifests(repo_root, payload, created_at)
    snapshot_digest = digest_value(payload)
    snapshot = {
        "schema_version": "country_outage_p2_s0b_registry_snapshot_v1",
        "registry_snapshot_id": "registry-snapshot-sha256:" + snapshot_digest.split(":", 1)[1],
        "snapshot_digest": snapshot_digest,
        "created_at": created_at,
        "production_deployed": False,
        "snapshot_payload": payload,
    }
    candidate = {
        "schema_version": "country_outage_p2_s0b_candidate_v1",
        "candidate_id": candidate_id,
        "created_at": created_at,
        "base_candidate_id": identity_material["base_candidate_id"],
        "base_registry_snapshot_id": identity_material["base_registry_snapshot_id"],
        "registry_snapshot_id": snapshot["registry_snapshot_id"],
        "registry_revision": payload["registry_revision"],
        "source_identity": identity_material,
        "source_identity_digest": identity_digest,
        "migration_summary": {
            "capability_count": len(payload["capability_registry"]["entries"]),
            "execution_unit_count": len(payload["execution_unit_registry"]["entries"]),
            "tool_count": len([entry for entry in payload["execution_unit_registry"]["entries"] if str(entry["unit_id"]).startswith("TOOL-")]),
            "operator_count": len([entry for entry in payload["execution_unit_registry"]["entries"] if str(entry["unit_id"]).startswith("OP-")]),
            "runtime_behavior_semantics_changed": False,
        },
        "runtime_integration": "implemented_not_deployed",
        "activation_scope": "runtime_candidate_shadow_only",
        "shadow_status": "pending_same_candidate_evaluation",
        "production_deployed": False,
        "prod32_switched": False,
    }
    return snapshot, candidate


def run_check(repo_root: Path, name: str, command: Sequence[str]) -> Dict[str, Any]:
    completed = subprocess.run(
        list(command),
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    return {
        "name": name,
        "command": list(command),
        "exit_code": completed.returncode,
        "output_digest": "sha256:" + hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "output_tail": output[-1200:],
    }


def build_acceptance(repo_root: Path, output_root: Path, evidence_root: Path) -> Dict[str, Any]:
    candidate = load(output_root / "candidate.json")
    snapshot = load(output_root / "registry-snapshot.json")
    review_path = evidence_root / "product-semantic-review.json"
    review = load(review_path)
    review_payload = dict(review)
    review_digest = review_payload.pop("receipt_digest", None)
    if review_digest != plain_digest_value(review_payload):
        raise CandidateBuildError("产品语义 Reviewer 回执摘要无效")
    if review.get("status") != "PASS" or review.get("blocking_count") != 0:
        raise CandidateBuildError("产品语义 Reviewer 未通过")
    if review.get("candidate_id") != candidate.get("candidate_id"):
        raise CandidateBuildError("Reviewer 与候选身份不一致")

    checks = [
        run_check(
            repo_root,
            "P2-S0B Python候选测试",
            (sys.executable, "-m", "unittest", "dev.tests.test_country_outage_p2_s0b_candidate"),
        ),
        run_check(
            repo_root,
            "P2-S0B TypeScript专项及P1回归",
            (
                "bash", "-lc",
                "npm --prefix agent-sidecar run build && "
                "node --test agent-sidecar/dist/tests/p2-registry-runtime.test.js "
                "agent-sidecar/dist/tests/p1-page-capability-semantic.test.js "
                "agent-sidecar/dist/tests/p1-page-capability-conversation.test.js",
            ),
        ),
        run_check(
            repo_root,
            "独立产品语义Reviewer复核",
            (
                sys.executable,
                "dev/tools/review_country_outage_p2_s0b_semantics.py",
                "--repo-root", ".",
                "--candidate", str(RUNTIME_ROOT / "candidate.json"),
                "--output", str(review_path.relative_to(repo_root)),
                "--check",
            ),
        ),
    ]
    failures = [item for item in checks if item["exit_code"] != 0]
    if failures:
        raise CandidateBuildError(f"同候选验收命令失败：{[item['name'] for item in failures]}")
    python_output = checks[0]["output_tail"]
    typescript_output = checks[1]["output_tail"]
    python_match = re.search(r"Ran (\d+) tests?", python_output)
    node_tests = re.findall(r"tests (\d+)", typescript_output)
    node_passes = re.findall(r"pass (\d+)", typescript_output)
    python_count = int(python_match.group(1)) if python_match else 0
    typescript_count = int(node_tests[-1]) if node_tests else 0
    typescript_pass = int(node_passes[-1]) if node_passes else 0
    if python_count < 8 or typescript_count < 55 or typescript_pass != typescript_count:
        raise CandidateBuildError(
            f"量化测试门未满足：python={python_count}, node={typescript_pass}/{typescript_count}"
        )
    summary: Dict[str, Any] = {
        "schema_version": "country_outage_p2_s0b_same_candidate_test_summary_v1",
        "candidate_id": candidate["candidate_id"],
        "registry_snapshot_id": snapshot["registry_snapshot_id"],
        "status": "PASS",
        "checks": checks,
        "gates": {
            "python_test_count": python_count,
            "typescript_and_p1_test_count": typescript_count,
            "typescript_and_p1_pass_count": typescript_pass,
            "product_semantic_blocking_count": review["blocking_count"],
            "production_deployment_count": 0,
            "prod32_switch_count": 0,
            "remote_write_count": 0,
        },
        "runtime_integration": "implemented_not_deployed",
        "production_deployed": False,
        "does_not_prove": ["production_deployment", "production_traffic", "production_user_value"],
    }
    summary["receipt_digest"] = digest_value(summary)
    write_atomic(evidence_root / "same-candidate-test-summary.json", summary)

    artifact_paths = [
        RUNTIME_ROOT / "candidate.json",
        RUNTIME_ROOT / "registry-snapshot.json",
        RUNTIME_ROOT / "runtime-contract.json",
        RUNTIME_ROOT / "runtime-plan-admission.schema.json",
        RUNTIME_ROOT / "shadow-oracle.json",
        RUNTIME_SOURCE_PATH,
        Path("agent-sidecar/src/chat/runtime-v2-semantic.ts"),
        Path("agent-sidecar/src/chat/page-capability-executor.ts"),
        Path("agent-sidecar/tests/p2-registry-runtime.test.ts"),
        Path("dev/tests/test_country_outage_p2_s0b_candidate.py"),
        REVIEWER_SOURCE_PATH,
        TASK_SPEC_PATH,
        PLAN_PATH,
        review_path.relative_to(repo_root),
        evidence_root.relative_to(repo_root) / "same-candidate-test-summary.json",
    ]
    artifacts = [{"path": str(path), "sha256": digest_file(repo_root / path)} for path in artifact_paths]
    manifest: Dict[str, Any] = {
        "schema_version": "country_outage_p2_s0b_acceptance_manifest_v1",
        "candidate_id": candidate["candidate_id"],
        "registry_snapshot_id": snapshot["registry_snapshot_id"],
        "registry_revision": snapshot["snapshot_payload"]["registry_revision"],
        "status": "accepted_local_shadow_candidate",
        "artifacts": artifacts,
        "gates": summary["gates"],
        "review_receipt_digest": review["receipt_digest"],
        "test_summary_digest": summary["receipt_digest"],
        "activation_scope": "runtime_candidate_shadow_only",
        "runtime_integration": "implemented_not_deployed",
        "production_deployed": False,
        "prod32_switched": False,
        "next_gate": "独立生产灰度任务与明确授权",
    }
    manifest["manifest_digest"] = digest_value(manifest)
    write_atomic(evidence_root / "acceptance-manifest.json", manifest)
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="生成 P2-S0B 只读 Runtime Registry 候选")
    result.add_argument("--repo-root", required=True)
    result.add_argument("--output-root", required=True)
    result.add_argument("--created-at", required=True)
    result.add_argument("--acceptance-evidence-root")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        repo_root = Path(args.repo_root).resolve()
        output_root = Path(args.output_root)
        if not output_root.is_absolute():
            output_root = repo_root / output_root
        snapshot, candidate = build(repo_root, args.created_at)
        write_atomic(output_root / "registry-snapshot.json", snapshot)
        candidate["artifact_digests"] = {
            "registry-snapshot.json": digest_file(output_root / "registry-snapshot.json"),
            "runtime-contract.json": digest_file(output_root / "runtime-contract.json"),
            "runtime-plan-admission.schema.json": digest_file(output_root / "runtime-plan-admission.schema.json"),
            "shadow-oracle.json": digest_file(output_root / "shadow-oracle.json"),
        }
        write_atomic(output_root / "candidate.json", candidate)
        acceptance = None
        if args.acceptance_evidence_root:
            evidence_root = Path(args.acceptance_evidence_root)
            if not evidence_root.is_absolute():
                evidence_root = repo_root / evidence_root
            acceptance = build_acceptance(repo_root, output_root, evidence_root.resolve())
        print(json.dumps({
            "status": "created",
            "candidate_id": candidate["candidate_id"],
            "registry_snapshot_id": candidate["registry_snapshot_id"],
            "acceptance_status": acceptance.get("status") if acceptance else "not_requested",
            "production_deployed": False,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except CandidateBuildError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
