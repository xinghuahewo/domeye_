#!/usr/bin/env python3

"""按精确清单可恢复地隔离 Domeye Runtime release；默认只读。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_POLICY = SCRIPT_DIRECTORY / "server-directory-policy.json"
DEFAULT_AUDIT = SCRIPT_DIRECTORY / "audit-server-runtime-governance.py"
BATCH_SCHEMA = "domeye.runtime-release-quarantine-batch/v1"
RECEIPT_SCHEMA = "domeye.runtime-release-quarantine-receipt/v1"
OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{3,127}$")


class QuarantineError(RuntimeError):
    """表示隔离 Gate 未满足或读回失败。"""


def load_audit_module(path: Path = DEFAULT_AUDIT) -> Any:
    specification = importlib.util.spec_from_file_location("domeye_runtime_audit", path)
    if specification is None or specification.loader is None:
        raise QuarantineError("无法加载运行时只读审计器")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


AUDIT = load_audit_module()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require_string(value: Any, field: str, *, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > maximum:
        raise QuarantineError(f"{field} 必须是长度受限的非空字符串")
    return value


def safe_operation_id(value: Any) -> str:
    operation_id = require_string(value, "operationId", maximum=128)
    if not OPERATION_ID_PATTERN.fullmatch(operation_id):
        raise QuarantineError("operationId 只能包含字母、数字、点、下划线和连字符")
    return operation_id


def safe_release_id(value: Any) -> str:
    release_id = AUDIT.safe_release_id(value)
    if release_id is None or release_id in {".", ".."} or len(release_id) > 255:
        raise QuarantineError("releaseId 非法")
    return release_id


def policy_sha256(policy: dict[str, Any]) -> str:
    return sha256_value(policy)


def inventory_fingerprint(inventory: dict[str, Any]) -> str:
    """生成路径无关的对象清单摘要，供移动前后精确比对。"""
    manifests: list[dict[str, Any]] = []
    for item in inventory.get("manifestFiles", []):
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        manifests.append(
            {
                "bytes": item.get("bytes"),
                "sha256": item.get("sha256"),
                "hashStatus": item.get("hashStatus"),
                "evidence": {
                    "parseState": evidence.get("parseState"),
                    "declaredReleaseMatchesObject": evidence.get("declaredReleaseMatchesObject"),
                    "rollbackReleaseIds": sorted(evidence.get("rollbackReleaseIds", [])),
                    "acceptedEvidence": evidence.get("acceptedEvidence"),
                },
            }
        )
    material = {
        "exists": inventory.get("exists"),
        "kind": inventory.get("kind"),
        "mode": inventory.get("mode"),
        "uid": inventory.get("uid"),
        "gid": inventory.get("gid"),
        "coverageComplete": inventory.get("coverageComplete"),
        "entryCount": inventory.get("entryCount"),
        "directoryCount": inventory.get("directoryCount"),
        "regularFileCount": inventory.get("regularFileCount"),
        "logicalBytes": inventory.get("logicalBytes"),
        "allocatedBytesApproximate": inventory.get("allocatedBytesApproximate"),
        "manifestEvidenceCoverageComplete": inventory.get("manifestEvidenceCoverageComplete"),
        "externalHardLinkCount": inventory.get("externalHardLinkCount"),
        "manifestFiles": sorted(manifests, key=canonical_json),
    }
    return sha256_value(material)


def find_artifact_root(policy: dict[str, Any]) -> Path:
    managed = policy.get("managedRoots")
    if not isinstance(managed, list):
        raise QuarantineError("策略缺少 managedRoots")
    matches = [item for item in managed if isinstance(item, dict) and item.get("name") == "artifacts"]
    if len(matches) != 1:
        raise QuarantineError("策略必须恰好声明一个 artifacts 受管根")
    try:
        artifact_root = Path(AUDIT.require_string(matches[0].get("path"), "managedRoots.artifacts.path"))
    except AUDIT.DiscoveryError as error:
        raise QuarantineError(str(error)) from error
    if not artifact_root.is_absolute() or artifact_root.is_symlink() or not artifact_root.is_dir():
        raise QuarantineError("artifacts 受管根必须是现有非软链接目录")
    protected = [Path(item["path"]) for item in policy["protectedRoots"]]
    if any(AUDIT.is_within(AUDIT.canonical(artifact_root), AUDIT.canonical(item)) for item in protected):
        raise QuarantineError("隔离根不得位于保护树")
    return AUDIT.canonical(artifact_root)


def quarantine_root(policy: dict[str, Any]) -> Path:
    return find_artifact_root(policy) / "quarantine" / "runtime-releases"


def component_map(discovery: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in discovery.get("runtimeComponents", []) if isinstance(item, dict) and isinstance(item.get("name"), str)}


def candidate_map(discovery: dict[str, Any], component_name: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    component = component_map(discovery).get(component_name)
    if component is None:
        raise QuarantineError("批次组件不在当前受管 releaseComponents 中")
    candidates = {
        item["releaseId"]: item
        for item in component.get("releases", [])
        if isinstance(item, dict) and item.get("retentionState") == "future_quarantine_candidate" and isinstance(item.get("releaseId"), str)
    }
    return component, candidates


def require_complete_discovery(discovery: dict[str, Any], component: dict[str, Any]) -> None:
    if discovery.get("schemaVersion") != AUDIT.SCHEMA:
        raise QuarantineError("只读审计 schema 不受支持")
    if discovery.get("processPathCoverage", {}).get("coverageComplete") is not True:
        raise QuarantineError("进程引用扫描不完整")
    if discovery.get("mountCoverage", {}).get("coverageComplete") is not True:
        raise QuarantineError("挂载引用扫描不完整")
    if discovery.get("activeFileLockCoverage", {}).get("coverageComplete") is not True:
        raise QuarantineError("活动锁扫描不完整")
    if component.get("rollbackReferenceCoverageComplete") is not True:
        raise QuarantineError("组件回滚引用扫描不完整")


def build_batch_plan(
    policy: dict[str, Any],
    component_name: str,
    release_ids: list[str],
    operation_id: str,
    user_authorization: str,
) -> dict[str, Any]:
    AUDIT.validate_policy(policy)
    operation_id = safe_operation_id(operation_id)
    user_authorization = require_string(user_authorization, "userAuthorization")
    if not isinstance(component_name, str) or not component_name:
        raise QuarantineError("component 必须是非空字符串")
    normalized_ids = sorted({safe_release_id(item) for item in release_ids})
    if not normalized_ids:
        raise QuarantineError("批次至少需要一个 release")
    discovery = AUDIT.build_discovery(policy)
    component, candidates = candidate_map(discovery, component_name)
    require_complete_discovery(discovery, component)
    releases: list[dict[str, str]] = []
    for release_id in normalized_ids:
        candidate = candidates.get(release_id)
        if candidate is None:
            raise QuarantineError(f"release 不是当前可隔离候选：{release_id}")
        releases.append({"releaseId": release_id, "inventorySha256": inventory_fingerprint(candidate["inventory"])})
    return {
        "schemaVersion": BATCH_SCHEMA,
        "operationId": operation_id,
        "userAuthorization": user_authorization,
        "expectedPolicySha256": policy_sha256(policy),
        "expectedAuditSchemaVersion": AUDIT.SCHEMA,
        "component": component_name,
        "releases": releases,
    }


def load_batch(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QuarantineError("无法读取有效的隔离批次清单") from error
    if not isinstance(document, dict):
        raise QuarantineError("隔离批次清单顶层必须是对象")
    validate_batch(document)
    document["batchManifestSha256"] = hashlib.sha256(raw).hexdigest()
    return document


def validate_batch_manifest_metadata(path: Path, *, expected_uid: int = 0, expected_gid: int = 0) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise QuarantineError("无法读取隔离批次清单元数据") from error
    mode = stat.S_IMODE(metadata.st_mode)
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or metadata.st_uid != expected_uid or metadata.st_gid != expected_gid or mode != 0o600:
        raise QuarantineError("执行隔离要求批次清单为 root:root 0600 的普通文件")


def validate_batch(batch: dict[str, Any]) -> None:
    if batch.get("schemaVersion") != BATCH_SCHEMA:
        raise QuarantineError("隔离批次 schemaVersion 不受支持")
    safe_operation_id(batch.get("operationId"))
    require_string(batch.get("userAuthorization"), "userAuthorization")
    expected_policy = batch.get("expectedPolicySha256")
    if not isinstance(expected_policy, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_policy):
        raise QuarantineError("expectedPolicySha256 必须是 SHA-256")
    if batch.get("expectedAuditSchemaVersion") != AUDIT.SCHEMA:
        raise QuarantineError("expectedAuditSchemaVersion 不受支持")
    if not isinstance(batch.get("component"), str) or not batch["component"]:
        raise QuarantineError("component 必须是非空字符串")
    releases = batch.get("releases")
    if not isinstance(releases, list) or not releases:
        raise QuarantineError("releases 必须是非空数组")
    seen: set[str] = set()
    for index, item in enumerate(releases):
        if not isinstance(item, dict):
            raise QuarantineError(f"releases[{index}] 必须是对象")
        release_id = safe_release_id(item.get("releaseId"))
        if release_id in seen:
            raise QuarantineError("releases.releaseId 不得重复")
        seen.add(release_id)
        digest = item.get("inventorySha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise QuarantineError("releases.inventorySha256 必须是 SHA-256")


def preflight(policy: dict[str, Any], batch: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    AUDIT.validate_policy(policy)
    if batch["expectedPolicySha256"] != policy_sha256(policy):
        raise QuarantineError("策略 SHA-256 与冻结批次不一致")
    discovery = AUDIT.build_discovery(policy)
    if batch["expectedAuditSchemaVersion"] != discovery.get("schemaVersion"):
        raise QuarantineError("只读审计 schema 与冻结批次不一致")
    component, candidates = candidate_map(discovery, batch["component"])
    require_complete_discovery(discovery, component)
    release_root = AUDIT.canonical(Path(component["releaseRoot"]))
    destination_root = quarantine_root(policy)
    artifact_root = find_artifact_root(policy)
    if destination_root.exists() and (destination_root.is_symlink() or not destination_root.is_dir()):
        raise QuarantineError("runtime release 隔离根必须是目录且不得为软链接")
    destination_parent = destination_root if destination_root.exists() else artifact_root
    items: list[dict[str, Any]] = []
    for expected in batch["releases"]:
        release_id = expected["releaseId"]
        candidate = candidates.get(release_id)
        if candidate is None:
            raise QuarantineError(f"release 已不再是可隔离候选：{release_id}")
        source = AUDIT.canonical(release_root / release_id)
        if source.parent != release_root or source.is_symlink() or not source.is_dir():
            raise QuarantineError(f"release 路径不再是直接非软链接目录：{release_id}")
        observed_digest = inventory_fingerprint(candidate["inventory"])
        if observed_digest != expected["inventorySha256"]:
            raise QuarantineError(f"release 清单摘要漂移：{release_id}")
        destination = destination_root / batch["operationId"] / batch["component"] / release_id
        if not AUDIT.is_within(destination, destination_root) or destination.exists() or destination.is_symlink():
            raise QuarantineError(f"隔离目标不可用：{release_id}")
        if source.stat().st_dev != destination_parent.stat().st_dev:
            raise QuarantineError(f"隔离路径跨文件系统：{release_id}")
        items.append(
            {
                "releaseId": release_id,
                "source": str(source),
                "destination": str(destination),
                "inventorySha256": observed_digest,
                "allocatedBytesApproximate": candidate["inventory"]["allocatedBytesApproximate"],
            }
        )
    operation_root = destination_root / batch["operationId"]
    if operation_root.exists() or operation_root.is_symlink():
        raise QuarantineError("operationId 已存在或目标不是安全目录")
    return discovery, items


def write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise QuarantineError("回执目录不是安全目录")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json(document))
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


def receipt_document(batch: dict[str, Any], items: list[dict[str, Any]], state: str, *, error: str | None = None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schemaVersion": RECEIPT_SCHEMA,
        "operationId": batch["operationId"],
        "batchManifestSha256": batch["batchManifestSha256"],
        "component": batch["component"],
        "state": state,
        "observedAt": utc_now(),
        "items": items,
        "deleteAuthorized": False,
        "oldDomeyeTouched": False,
        "productionSwitchPerformed": False,
        "serverGitHubCredentialsChanged": False,
    }
    if error:
        document["error"] = error
    return document


def post_move_readback(policy: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runtime = policy["runtimeGovernance"]
    roots = [Path(item["releaseRoot"]) for item in runtime["releaseComponents"]] + [quarantine_root(policy)]
    process_snapshot = AUDIT.process_path_snapshot(Path(policy["processRoot"]), roots)
    mount_snapshot = AUDIT.parse_mounts(Path(runtime["mountInfoPath"]))
    lock_snapshot = AUDIT.parse_kernel_locks(Path(policy["processRoot"]) / "locks")
    if not process_snapshot["coverageComplete"] or not mount_snapshot["coverageComplete"] or not lock_snapshot["coverageComplete"]:
        raise QuarantineError("移动后引用读回覆盖不完整")
    readback: list[dict[str, Any]] = []
    for item in items:
        source = Path(item["source"])
        destination = Path(item["destination"])
        if source.exists() or source.is_symlink() or destination.is_symlink() or not destination.is_dir():
            raise QuarantineError(f"移动后路径读回失败：{item['releaseId']}")
        inventory = AUDIT.inventory_object(
            destination,
            set(runtime["manifestFileNames"]),
            runtime["maxEntriesPerObject"],
            runtime["maxManifestBytes"],
            lock_snapshot,
        )
        observed = inventory_fingerprint(inventory)
        references = AUDIT.references_for(destination, process_snapshot)
        mounts = AUDIT.mounts_for(destination, mount_snapshot)
        if observed != item["inventorySha256"] or references or mounts or inventory["activeLockPaths"] or inventory["externalHardLinkCount"]:
            raise QuarantineError(f"移动后对象或引用读回失败：{item['releaseId']}")
        readback.append({"releaseId": item["releaseId"], "inventorySha256": observed, "readback": "passed"})
    return readback


def restore_items(items: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for item in reversed(items):
        if item.get("state") != "quarantined":
            continue
        source = Path(item["source"])
        destination = Path(item["destination"])
        try:
            if source.exists() or source.is_symlink() or not destination.is_dir() or destination.is_symlink():
                raise QuarantineError("恢复路径状态不安全")
            os.replace(destination, source)
            item["state"] = "restored"
        except (OSError, QuarantineError) as error:
            item["state"] = "restore_failed"
            errors.append(f"{item['releaseId']}:{type(error).__name__}")
    return errors


def execute(policy: dict[str, Any], batch: dict[str, Any], *, apply: bool) -> dict[str, Any]:
    discovery, planned_items = preflight(policy, batch)
    result = {
        "schemaVersion": RECEIPT_SCHEMA,
        "mode": "apply" if apply else "read_only",
        "operationId": batch["operationId"],
        "batchManifestSha256": batch["batchManifestSha256"],
        "component": batch["component"],
        "candidateCount": len(planned_items),
        "estimatedAllocatedBytes": sum(item["allocatedBytesApproximate"] for item in planned_items),
        "items": planned_items,
        "gate": {"decision": "G4_READY" if apply else "READ_ONLY_READY", "reason": "已完成当前精确清单、引用覆盖、摘要与同文件系统预检。"},
        "oldDomeyeTouched": False,
        "productionSwitchPerformed": False,
        "serverGitHubCredentialsChanged": False,
        "auditObservedAt": discovery["observedAt"],
    }
    if not apply:
        return result

    operation_root = quarantine_root(policy) / batch["operationId"]
    receipt_path = operation_root / "quarantine-receipt.json"
    items = [{**item, "state": "pending"} for item in planned_items]
    write_json_atomic(receipt_path, receipt_document(batch, items, "prepared"))
    try:
        for item in items:
            item["state"] = "moving"
            write_json_atomic(receipt_path, receipt_document(batch, items, "moving"))
            destination = Path(item["destination"])
            if destination.parent.exists():
                if destination.parent.is_symlink() or not destination.parent.is_dir():
                    raise QuarantineError("隔离组件目录不是安全目录")
            else:
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
            if destination.parent.is_symlink():
                raise QuarantineError("隔离组件目录不是安全目录")
            os.replace(Path(item["source"]), destination)
            item["state"] = "quarantined"
            write_json_atomic(receipt_path, receipt_document(batch, items, "moving"))
        readback = post_move_readback(policy, items)
    except (OSError, QuarantineError) as error:
        restore_errors = restore_items(items)
        failure_state = "rollback_failed" if restore_errors else "rollback_complete"
        write_json_atomic(receipt_path, receipt_document(batch, items, failure_state, error=type(error).__name__))
        detail = ",".join(restore_errors) if restore_errors else "已恢复原路径"
        raise QuarantineError(f"隔离失败并进入 {failure_state}：{detail}") from error
    write_json_atomic(receipt_path, receipt_document(batch, items, "quarantined"))
    result.update({"items": items, "receiptPath": str(receipt_path), "readback": readback, "gate": {"decision": "QUARANTINED", "reason": "移动后清单摘要、引用、锁与路径读回通过；未删除。"}})
    return result


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY, help="策略 JSON")
    parser.add_argument("--batch-manifest", type=Path, help="精确隔离批次 JSON")
    parser.add_argument("--apply", action="store_true", help="执行可恢复移动；默认只读")
    parser.add_argument("--plan-component", help="生成批次清单时的组件名")
    parser.add_argument("--plan-release", action="append", default=[], help="生成批次清单时的 release ID；可重复")
    parser.add_argument("--plan-operation-id", help="生成批次清单时的 operation ID")
    parser.add_argument("--user-authorization", help="精确批次的用户授权原文")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        policy = AUDIT.load_policy(arguments.policy)
        planning = any((arguments.plan_component, arguments.plan_release, arguments.plan_operation_id, arguments.user_authorization))
        if planning:
            if arguments.batch_manifest or arguments.apply:
                raise QuarantineError("生成批次清单不能同时指定 --batch-manifest 或 --apply")
            if not arguments.plan_component or not arguments.plan_operation_id or not arguments.user_authorization:
                raise QuarantineError("生成批次清单需要组件、operation ID 和精确用户授权")
            result = build_batch_plan(policy, arguments.plan_component, arguments.plan_release, arguments.plan_operation_id, arguments.user_authorization)
        else:
            if arguments.batch_manifest is None:
                raise QuarantineError("必须提供 --batch-manifest，或使用 --plan-* 生成清单")
            if arguments.apply:
                validate_batch_manifest_metadata(arguments.batch_manifest)
            batch = load_batch(arguments.batch_manifest)
            result = execute(policy, batch, apply=arguments.apply)
    except (AUDIT.DiscoveryError, OSError, QuarantineError) as error:
        print(f"Runtime release 隔离 Gate 失败：{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
