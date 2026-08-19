#!/usr/bin/env python3

"""只读发现 Domeye S3--S6 治理对象；不读取凭证或进程实参。"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import sys
import time
from typing import Any


SCHEMA = "domeye.server-runtime-governance-discovery/v1"
POLICY_SCHEMA = "domeye.server-directory-policy/v1"
DEFAULT_POLICY = Path(__file__).with_name("server-directory-policy.json")
POLICY_ENV = "DOMEYE_SERVER_GOVERNANCE_POLICY_B64"
ACCEPTED_CHECK_VALUES = {"passed", "verified"}


class DiscoveryError(RuntimeError):
    """表示只读发现无法安全完成。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def canonical(path: Path) -> Path:
    return path.resolve(strict=False)


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DiscoveryError(f"策略字段 {field} 必须是非空字符串")
    return value


def require_path(value: Any, field: str) -> Path:
    path = Path(require_string(value, field))
    if not path.is_absolute():
        raise DiscoveryError(f"策略字段 {field} 必须是绝对路径")
    return path


def load_policy(path: Path | None) -> dict[str, Any]:
    try:
        if path is not None:
            raw = path.read_bytes()
        elif os.getenv(POLICY_ENV):
            raw = base64.b64decode(os.getenv(POLICY_ENV, ""), validate=True)
        else:
            raw = DEFAULT_POLICY.read_bytes()
        policy = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DiscoveryError("无法读取有效的服务器目录策略") from error
    if not isinstance(policy, dict):
        raise DiscoveryError("服务器目录策略顶层必须是对象")
    validate_policy(policy)
    return policy


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schemaVersion") != POLICY_SCHEMA:
        raise DiscoveryError("服务器目录策略 schemaVersion 不受支持")
    require_path(policy.get("processRoot"), "processRoot")
    require_path(policy.get("configDirectory"), "configDirectory")
    protected = policy.get("protectedRoots")
    managed = policy.get("managedRoots")
    if not isinstance(protected, list) or not protected:
        raise DiscoveryError("protectedRoots 必须是非空数组")
    if not isinstance(managed, list) or not managed:
        raise DiscoveryError("managedRoots 必须是非空数组")
    protected_paths = [require_path(item.get("path"), "protectedRoots.path") for item in protected]
    managed_paths = [require_path(item.get("path"), "managedRoots.path") for item in managed]
    for managed_path in managed_paths:
        if any(is_within(canonical(managed_path), canonical(item)) for item in protected_paths):
            raise DiscoveryError(f"受管路径不得进入保护树：{managed_path}")

    mutation = policy.get("mutationPolicy")
    if not isinstance(mutation, dict):
        raise DiscoveryError("mutationPolicy 必须是对象")
    for name in ("auditWritesServer", "deleteEnabled", "moveEnabled", "restartEnabled", "productionSwitchEnabled"):
        if mutation.get(name) is not False:
            raise DiscoveryError(f"只读发现要求 mutationPolicy.{name}=false")

    runtime = policy.get("runtimeGovernance")
    if not isinstance(runtime, dict):
        raise DiscoveryError("runtimeGovernance 必须是对象")
    require_path(runtime.get("mountInfoPath"), "runtimeGovernance.mountInfoPath")
    for name in ("maxEntriesPerObject", "maxManifestBytes"):
        if not isinstance(runtime.get(name), int) or runtime[name] <= 0:
            raise DiscoveryError(f"runtimeGovernance.{name} 必须是正整数")
    for name in ("rollbackStateRequiredUid", "rollbackStateRequiredGid"):
        if not isinstance(runtime.get(name), int) or runtime[name] < 0:
            raise DiscoveryError(f"runtimeGovernance.{name} 必须是非负整数")
    if runtime.get("rollbackStateRequiredMode") != "0600":
        raise DiscoveryError("runtimeGovernance.rollbackStateRequiredMode 必须固定为 0600")
    manifest_names = runtime.get("manifestFileNames")
    if not isinstance(manifest_names, list) or not manifest_names or any(
        not isinstance(item, str) or not item or "/" in item for item in manifest_names
    ):
        raise DiscoveryError("runtimeGovernance.manifestFileNames 必须是非空文件名数组")
    components = runtime.get("releaseComponents")
    data_roots = runtime.get("developmentDataRoots")
    if not isinstance(components, list) or not components:
        raise DiscoveryError("runtimeGovernance.releaseComponents 必须是非空数组")
    if not isinstance(data_roots, list) or not data_roots:
        raise DiscoveryError("runtimeGovernance.developmentDataRoots 必须是非空数组")
    seen_names: set[str] = set()
    for index, item in enumerate(components):
        if not isinstance(item, dict):
            raise DiscoveryError(f"releaseComponents[{index}] 必须是对象")
        name = require_string(item.get("name"), f"releaseComponents[{index}].name")
        if name in seen_names:
            raise DiscoveryError(f"releaseComponents.name 重复：{name}")
        seen_names.add(name)
        require_path(item.get("activeLinkPath"), f"releaseComponents[{index}].activeLinkPath")
        release_root = require_path(item.get("releaseRoot"), f"releaseComponents[{index}].releaseRoot")
        if not any(is_within(canonical(release_root), canonical(root)) for root in managed_paths):
            raise DiscoveryError(f"release root 必须位于受管根：{release_root}")
        state_paths = item.get("rollbackStatePaths", [])
        if not isinstance(state_paths, list):
            raise DiscoveryError(f"releaseComponents[{index}].rollbackStatePaths 必须是数组")
        for state_index, state_path in enumerate(state_paths):
            state = require_path(state_path, f"releaseComponents[{index}].rollbackStatePaths[{state_index}]")
            if not any(is_within(canonical(state), canonical(root)) for root in managed_paths):
                raise DiscoveryError(f"rollback state 必须位于受管根：{state}")
    for index, item in enumerate(data_roots):
        if not isinstance(item, dict):
            raise DiscoveryError(f"developmentDataRoots[{index}] 必须是对象")
        require_string(item.get("name"), f"developmentDataRoots[{index}].name")
        data_path = require_path(item.get("path"), f"developmentDataRoots[{index}].path")
        if not any(is_within(canonical(data_path), canonical(root)) for root in managed_paths):
            raise DiscoveryError(f"开发数据根必须位于受管根：{data_path}")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_sha256(value: Any) -> str:
    """为只读清单生成稳定摘要，不输出原始目录内容。"""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def safe_release_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\x00" in value or "/" in value:
        return None
    return value


def manifest_evidence(path: Path, object_id: str, max_bytes: int) -> dict[str, Any]:
    """从小型 release manifest 提取保留关系，不输出原始内容或校验值。"""
    result: dict[str, Any] = {"path": str(path), "parseState": "not_attempted", "rollbackReleaseIds": [], "acceptedEvidence": False}
    try:
        if path.stat().st_size > max_bytes:
            result["parseState"] = "skipped_too_large"
            return result
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        result["parseState"] = type(error).__name__
        return result
    if not isinstance(document, dict):
        result["parseState"] = "not_object"
        return result
    result["parseState"] = "parsed"
    declared = safe_release_id(document.get("release_id", document.get("releaseId")))
    result["declaredReleaseMatchesObject"] = declared == object_id
    rollback_ids: set[str] = set()
    rollback = document.get("rollback")
    if isinstance(rollback, dict):
        release_id = safe_release_id(rollback.get("release_id", rollback.get("releaseId")))
        if release_id:
            rollback_ids.add(release_id)
    direct_rollback = safe_release_id(document.get("rollback_release_id", document.get("rollbackReleaseId")))
    if direct_rollback:
        rollback_ids.add(direct_rollback)
    result["rollbackReleaseIds"] = sorted(rollback_ids)
    checks = document.get("checks")
    result["acceptedEvidence"] = bool(
        result["declaredReleaseMatchesObject"]
        and isinstance(checks, dict)
        and checks
        and all(isinstance(value, str) and value in ACCEPTED_CHECK_VALUES for value in checks.values())
    )
    return result


def path_metadata(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.exists() or path.is_symlink()}
    if not result["exists"]:
        return result
    info = path.lstat()
    result.update(
        {
            "kind": "symlink" if stat.S_ISLNK(info.st_mode) else "directory" if stat.S_ISDIR(info.st_mode) else "file",
            "mode": format(stat.S_IMODE(info.st_mode), "04o"),
            "uid": info.st_uid,
            "gid": info.st_gid,
            "modifiedAt": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    )
    return result


def active_link(path: Path, release_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "isSymlink": path.is_symlink()}
    if not path.is_symlink():
        result.update({"valid": False, "reason": "not_symlink"})
        return result
    resolved = canonical(path)
    result.update(
        {
            "rawTarget": os.readlink(path),
            "resolvedTarget": str(resolved),
            "targetExists": resolved.exists(),
            "withinReleaseRoot": is_within(resolved, canonical(release_root)),
        }
    )
    result["valid"] = bool(result["targetExists"] and result["withinReleaseRoot"])
    if not result["valid"]:
        result["reason"] = "missing_or_outside_release_root"
    return result


def parse_mounts(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"source": str(path), "coverageComplete": False, "mountPoints": []}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        result["error"] = type(error).__name__
        return result
    for line in lines:
        fields = line.split(" - ", 1)[0].split()
        if len(fields) < 5:
            result["malformedLines"] = result.get("malformedLines", 0) + 1
            continue
        raw = fields[4].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")
        result["mountPoints"].append(str(canonical(Path(raw))))
    result["coverageComplete"] = not result.get("malformedLines")
    return result


def is_nonfilesystem_descriptor(raw_target: str) -> bool:
    return raw_target.startswith(("socket:[", "pipe:[", "anon_inode:", "memfd:"))


def raw_target_is_within(raw_target: str, roots: list[Path]) -> bool:
    if not raw_target.startswith("/"):
        return False
    path = Path(raw_target.removesuffix(" (deleted)"))
    return any(is_within(canonical(path), root) for root in roots)


def process_path_snapshot(process_root: Path, roots: list[Path]) -> dict[str, Any]:
    canonical_roots = [canonical(root) for root in roots]
    result: dict[str, Any] = {
        "coverageComplete": True,
        "scannedPids": 0,
        "unreadablePids": [],
        "executableUnavailablePids": [],
        "processes": [],
    }
    if not process_root.is_dir():
        result.update({"coverageComplete": False, "error": "process_root_missing"})
        return result
    for process in sorted((item for item in process_root.iterdir() if item.name.isdigit()), key=lambda item: int(item.name)):
        result["scannedPids"] += 1
        references: dict[str, str] = {}
        incomplete = False
        try:
            cwd = (process / "cwd").resolve(strict=True)
        except FileNotFoundError:
            try:
                raw_cwd = os.readlink(process / "cwd")
            except FileNotFoundError:
                raw_cwd = ""
            except (PermissionError, OSError):
                incomplete = process.is_dir()
                raw_cwd = ""
            if raw_target_is_within(raw_cwd, canonical_roots):
                incomplete = True
        except (PermissionError, OSError):
            incomplete = True
        else:
            if any(is_within(cwd, root) for root in canonical_roots):
                references["cwd"] = str(cwd)
        try:
            executable = (process / "exe").resolve(strict=True)
        except (FileNotFoundError, PermissionError, OSError):
            result["executableUnavailablePids"].append(int(process.name))
        else:
            if any(is_within(executable, root) for root in canonical_roots):
                references["exe"] = str(executable)
        descriptors = process / "fd"
        try:
            descriptor_list = sorted(descriptors.iterdir(), key=lambda item: item.name)
        except FileNotFoundError:
            descriptor_list = []
            incomplete = process.is_dir()
        except (PermissionError, OSError):
            descriptor_list = []
            incomplete = True
        for descriptor in descriptor_list:
            try:
                raw_target = os.readlink(descriptor)
            except FileNotFoundError:
                continue
            except (PermissionError, OSError):
                incomplete = True
                continue
            if is_nonfilesystem_descriptor(raw_target):
                continue
            try:
                target = descriptor.resolve(strict=True)
            except FileNotFoundError:
                if raw_target_is_within(raw_target, canonical_roots):
                    incomplete = True
                continue
            except (PermissionError, OSError):
                incomplete = True
                continue
            if any(is_within(target, root) for root in canonical_roots):
                references[f"fd:{descriptor.name}"] = str(target)
        if incomplete:
            result["coverageComplete"] = False
            result["unreadablePids"].append(int(process.name))
        if not references:
            continue
        try:
            command = (process / "comm").read_text(encoding="utf-8").strip()
        except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError):
            command = "unknown"
        result["processes"].append({"pid": int(process.name), "command": command, "references": references})
    return result


def parse_kernel_locks(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"source": str(path), "coverageComplete": False, "lockedInodeCount": 0, "_lockedInodes": {}}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        result["error"] = type(error).__name__
        return result
    for line in lines:
        fields = line.split()
        if len(fields) < 6:
            result["malformedLines"] = result.get("malformedLines", 0) + 1
            continue
        try:
            major_hex, minor_hex, inode_text = fields[5].split(":", 2)
            key = (int(major_hex, 16), int(minor_hex, 16), int(inode_text))
            holder = int(fields[4])
        except ValueError:
            result["malformedLines"] = result.get("malformedLines", 0) + 1
            continue
        result["_lockedInodes"].setdefault(key, []).append(holder)
    result["coverageComplete"] = not result.get("malformedLines")
    result["lockedInodeCount"] = len(result["_lockedInodes"])
    return result


def references_for(path: Path, process_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    path = canonical(path)
    matches: list[dict[str, Any]] = []
    for process in process_snapshot["processes"]:
        kinds = sorted(name for name, target in process["references"].items() if is_within(canonical(Path(target)), path))
        if kinds:
            matches.append({"pid": process["pid"], "command": process["command"], "referenceKinds": kinds})
    return matches


def mounts_for(path: Path, mount_snapshot: dict[str, Any]) -> list[str]:
    root = canonical(path)
    return sorted(point for point in mount_snapshot["mountPoints"] if is_within(canonical(Path(point)), root))


def inventory_object(
    path: Path,
    manifest_names: set[str],
    max_entries: int,
    max_manifest_bytes: int,
    lock_snapshot: dict[str, Any],
) -> dict[str, Any]:
    result = path_metadata(path)
    result.update(
        {
            "coverageComplete": True,
            "entryCount": 0,
            "directoryCount": 0,
            "regularFileCount": 0,
            "logicalBytes": 0,
            "allocatedBytesApproximate": 0,
            "manifestFiles": [],
            "manifestEvidenceCoverageComplete": True,
            "namedLockPaths": [],
            "activeLockPaths": [],
            "externalHardLinkCount": 0,
            "metadataSha256": None,
        }
    )
    if not path.is_dir() or path.is_symlink():
        result["coverageComplete"] = False
        result["error"] = "not_directory"
        return result
    seen_inodes: dict[tuple[int, int], dict[str, int]] = {}
    manifest_candidates: list[Path] = []
    metadata_digest = hashlib.sha256()
    try:
        for current, directory_names, file_names in os.walk(path, topdown=True, followlinks=False):
            current_path = Path(current)
            result["directoryCount"] += 1
            kept_directories: list[str] = []
            for name in sorted(directory_names):
                child = current_path / name
                result["entryCount"] += 1
                if result["entryCount"] > max_entries:
                    result["coverageComplete"] = False
                    result["truncatedAtEntries"] = max_entries
                    directory_names[:] = []
                    file_names[:] = []
                    break
                if child.is_symlink():
                    continue
                kept_directories.append(name)
            else:
                directory_names[:] = kept_directories
            if not result["coverageComplete"]:
                break
            for name in sorted(file_names):
                child = current_path / name
                result["entryCount"] += 1
                if result["entryCount"] > max_entries:
                    result["coverageComplete"] = False
                    result["truncatedAtEntries"] = max_entries
                    break
                try:
                    info = child.lstat()
                except (FileNotFoundError, PermissionError, OSError) as error:
                    result["coverageComplete"] = False
                    result["metadataError"] = type(error).__name__
                    continue
                if stat.S_ISLNK(info.st_mode):
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                metadata_digest.update(
                    json.dumps(
                        [
                            str(child.relative_to(path)),
                            info.st_dev,
                            info.st_ino,
                            info.st_size,
                            info.st_mtime_ns,
                            stat.S_IMODE(info.st_mode),
                            info.st_nlink,
                        ],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                result["regularFileCount"] += 1
                result["logicalBytes"] += info.st_size
                result["allocatedBytesApproximate"] += info.st_blocks * 512
                key = (info.st_dev, info.st_ino)
                observed = seen_inodes.setdefault(key, {"observed": 0, "nlink": info.st_nlink})
                observed["observed"] += 1
                if name == ".lock" or name.endswith(".lock"):
                    result["namedLockPaths"].append(str(child))
                lock_key = (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino)
                if lock_key in lock_snapshot["_lockedInodes"]:
                    result["activeLockPaths"].append(str(child))
                if name in manifest_names:
                    manifest_candidates.append(child)
            if not result["coverageComplete"]:
                break
    except (PermissionError, OSError) as error:
        result["coverageComplete"] = False
        result["walkError"] = type(error).__name__
    if result["coverageComplete"]:
        result["externalHardLinkCount"] = sum(max(item["nlink"] - item["observed"], 0) for item in seen_inodes.values())
        result["metadataSha256"] = metadata_digest.hexdigest()
    for manifest in sorted(manifest_candidates):
        try:
            size = manifest.stat().st_size
            item: dict[str, Any] = {"path": str(manifest), "bytes": size}
            if size <= max_manifest_bytes:
                item["sha256"] = file_sha256(manifest)
                item["evidence"] = manifest_evidence(manifest, path.name, max_manifest_bytes)
                if item["evidence"]["parseState"] != "parsed":
                    result["manifestEvidenceCoverageComplete"] = False
            else:
                item["hashStatus"] = "skipped_too_large"
                result["manifestEvidenceCoverageComplete"] = False
            result["manifestFiles"].append(item)
        except (FileNotFoundError, PermissionError, OSError) as error:
            result["coverageComplete"] = False
            result["manifestError"] = type(error).__name__
    result["namedLockPaths"] = sorted(result["namedLockPaths"])
    result["activeLockPaths"] = sorted(result["activeLockPaths"])
    return result


def declared_rollback_ids(inventory: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for item in inventory.get("manifestFiles", []):
        evidence = item.get("evidence")
        if isinstance(evidence, dict):
            values.update(value for value in evidence.get("rollbackReleaseIds", []) if isinstance(value, str))
    return values


def has_accepted_evidence(inventory: dict[str, Any]) -> bool:
    return any(
        isinstance(item.get("evidence"), dict) and item["evidence"].get("acceptedEvidence") is True
        for item in inventory.get("manifestFiles", [])
    )


def rollback_state_evidence(paths: list[Path], runtime: dict[str, Any]) -> dict[str, Any]:
    """读取 root-only 生命周期状态中的 release ID，不输出状态原文。"""
    result: dict[str, Any] = {"coverageComplete": True, "stateFiles": [], "rollbackReleaseIds": []}
    release_ids: set[str] = set()
    expected_mode = runtime["rollbackStateRequiredMode"]
    for path in paths:
        item: dict[str, Any] = {"path": str(path), "exists": path.exists() or path.is_symlink()}
        if not item["exists"]:
            item["parseState"] = "missing"
            result["coverageComplete"] = False
            result["stateFiles"].append(item)
            continue
        try:
            metadata = path.lstat()
            mode = format(stat.S_IMODE(metadata.st_mode), "04o")
            regular = stat.S_ISREG(metadata.st_mode) and not path.is_symlink()
            item.update({"regularFile": regular, "mode": mode, "uid": metadata.st_uid, "gid": metadata.st_gid})
            if not regular or mode != expected_mode or metadata.st_uid != runtime["rollbackStateRequiredUid"] or metadata.st_gid != runtime["rollbackStateRequiredGid"]:
                item["parseState"] = "unsafe_metadata"
                result["coverageComplete"] = False
                result["stateFiles"].append(item)
                continue
            if metadata.st_size > runtime["maxManifestBytes"]:
                item["parseState"] = "skipped_too_large"
                result["coverageComplete"] = False
                result["stateFiles"].append(item)
                continue
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            item["parseState"] = type(error).__name__
            result["coverageComplete"] = False
            result["stateFiles"].append(item)
            continue
        if not isinstance(document, dict):
            item["parseState"] = "not_object"
            result["coverageComplete"] = False
            result["stateFiles"].append(item)
            continue
        item["parseState"] = "parsed"
        declared: set[str] = set()
        for key in ("release_id", "releaseId", "previous_release_id", "previousReleaseId"):
            value = safe_release_id(document.get(key))
            if value:
                declared.add(value)
        item["releaseIds"] = sorted(declared)
        release_ids.update(declared)
        result["stateFiles"].append(item)
    result["rollbackReleaseIds"] = sorted(release_ids)
    return result


def retention_state(
    inventory: dict[str, Any],
    active: bool,
    rollback: bool,
    accepted_evidence: bool,
    references: list[dict[str, Any]],
    mounts: list[str],
    process_coverage_complete: bool,
    mount_coverage_complete: bool,
    lock_coverage_complete: bool,
    rollback_reference_coverage_complete: bool,
) -> tuple[str, list[str]]:
    protected: list[str] = []
    if active:
        protected.append("active")
    if rollback:
        protected.append("rollback")
    if accepted_evidence:
        protected.append("accepted_evidence")
    if references:
        protected.append("process_referenced")
    if mounts:
        protected.append("mounted")
    if inventory["activeLockPaths"]:
        protected.append("locked")
    if not inventory["coverageComplete"] or not inventory["manifestEvidenceCoverageComplete"] or not process_coverage_complete or not mount_coverage_complete or not lock_coverage_complete or not rollback_reference_coverage_complete:
        protected.append("unknown")
    if inventory["externalHardLinkCount"]:
        protected.append("unknown")
    if not inventory["manifestFiles"]:
        protected.append("unknown")
    if protected:
        return "protected_or_unknown", protected
    return "future_quarantine_candidate", []


def config_metadata(directory: Path, required_mode: str) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(directory), "requiredMode": required_mode, "files": [], "contentsRead": False}
    if not directory.is_dir():
        result.update({"exists": False, "allCompliant": False})
        return result
    result["exists"] = True
    expected = int(required_mode, 8)
    for child in sorted(directory.iterdir(), key=lambda item: item.name):
        if not child.is_file() or child.is_symlink():
            continue
        info = child.stat()
        mode = stat.S_IMODE(info.st_mode)
        result["files"].append({"name": child.name, "mode": format(mode, "04o"), "uid": info.st_uid, "gid": info.st_gid, "compliant": mode == expected})
    result["allCompliant"] = bool(result["files"]) and all(item["compliant"] for item in result["files"])
    return result


def component_discovery(
    component: dict[str, Any],
    policy: dict[str, Any],
    process_snapshot: dict[str, Any],
    mount_snapshot: dict[str, Any],
    lock_snapshot: dict[str, Any],
) -> dict[str, Any]:
    runtime = policy["runtimeGovernance"]
    release_root = Path(component["releaseRoot"])
    link = active_link(Path(component["activeLinkPath"]), release_root)
    result: dict[str, Any] = {
        "name": component["name"],
        "activeLink": link,
        "releaseRoot": str(release_root),
        "releases": [],
    }
    for field in ("routingState", "governanceMode"):
        if field in component:
            result[field] = component[field]
    if not release_root.is_dir():
        result["releaseRootExists"] = False
        result["identityEquation"] = {"state": "not_verified", "reason": "release_root_missing"}
        return result
    result["releaseRootExists"] = True
    active_target = canonical(Path(link["resolvedTarget"])) if link.get("valid") else None
    entries = sorted((entry for entry in release_root.iterdir() if entry.is_dir() and not entry.is_symlink()), key=lambda entry: entry.name)
    discovered: list[dict[str, Any]] = []
    for entry in entries:
        inventory = inventory_object(
            entry,
            set(runtime["manifestFileNames"]),
            runtime["maxEntriesPerObject"],
            runtime["maxManifestBytes"],
            lock_snapshot,
        )
        references = references_for(entry, process_snapshot)
        mounts = mounts_for(entry, mount_snapshot)
        is_active = active_target == canonical(entry)
        discovered.append(
            {
                "releaseId": entry.name,
                "hidden": entry.name.startswith("."),
                "active": is_active,
                "inventory": inventory,
                "processReferences": references,
                "mountPoints": mounts,
            }
    )
    active_release = next((item for item in discovered if item["active"]), None)
    active_rollback_ids = declared_rollback_ids(active_release["inventory"]) if active_release else set()
    state_evidence = rollback_state_evidence([Path(path) for path in component.get("rollbackStatePaths", [])], runtime)
    active_rollback_ids.update(state_evidence["rollbackReleaseIds"])
    known_release_ids = {item["releaseId"] for item in discovered}
    rollback_reference_coverage_complete = bool(
        active_release
        and active_release["inventory"]["manifestFiles"]
        and active_release["inventory"]["manifestEvidenceCoverageComplete"]
        and state_evidence["coverageComplete"]
        and active_rollback_ids <= known_release_ids
    )
    for item in discovered:
        state, protected_classes = retention_state(
            item["inventory"],
            item["active"],
            item["releaseId"] in active_rollback_ids,
            has_accepted_evidence(item["inventory"]),
            item["processReferences"],
            item["mountPoints"],
            process_snapshot["coverageComplete"],
            mount_snapshot["coverageComplete"],
            lock_snapshot["coverageComplete"],
            rollback_reference_coverage_complete,
        )
        result["releases"].append(
            {
                **item,
                "retentionState": state,
                "protectedClasses": protected_classes,
                "quarantineState": "inventory",
                "quarantineAuthorized": False,
                "deleteAuthorized": False,
            }
        )
    result["activeRollbackReleaseIds"] = sorted(active_rollback_ids)
    result["rollbackReferenceCoverageComplete"] = rollback_reference_coverage_complete
    result["rollbackStateEvidence"] = state_evidence
    active_release = next((item for item in result["releases"] if item["active"]), None)
    process_cwd_bound = bool(active_release and any("cwd" in item["referenceKinds"] for item in active_release["processReferences"]))
    manifest_bound = bool(active_release and active_release["inventory"]["manifestFiles"])
    result["identityEquation"] = {
        "activeLinkValid": bool(link.get("valid")),
        "actualProcessCwdBound": process_cwd_bound,
        "releaseManifestDigestBound": manifest_bound,
        "healthChecked": False,
        "healthReason": "本发现器不调用业务 HTTP；健康检查须由独立运行验收绑定同一 release。",
        "state": "not_verified",
    }
    return result


def development_data_discovery(
    item: dict[str, Any],
    policy: dict[str, Any],
    process_snapshot: dict[str, Any],
    mount_snapshot: dict[str, Any],
    lock_snapshot: dict[str, Any],
) -> dict[str, Any]:
    runtime = policy["runtimeGovernance"]
    root = Path(item["path"])
    result: dict[str, Any] = {"name": item["name"], "path": str(root), "exists": root.is_dir(), "objects": [], "rootNonDirectoryEntries": []}
    if not root.is_dir():
        return result
    entries = sorted(root.iterdir(), key=lambda child: child.name)
    for entry in entries:
        if not entry.is_dir() or entry.is_symlink():
            result["rootNonDirectoryEntries"].append(path_metadata(entry))
            continue
        inventory = inventory_object(
            entry,
            set(runtime["manifestFileNames"]),
            runtime["maxEntriesPerObject"],
            runtime["maxManifestBytes"],
            lock_snapshot,
        )
        references = references_for(entry, process_snapshot)
        mounts = mounts_for(entry, mount_snapshot)
        state, protected_classes = retention_state(
            inventory,
            False,
            False,
            False,
            references,
            mounts,
            process_snapshot["coverageComplete"],
            mount_snapshot["coverageComplete"],
            lock_snapshot["coverageComplete"],
            True,
        )
        result["objects"].append(
            {
                "objectId": entry.name,
                "inventory": inventory,
                "processReferences": references,
                "mountPoints": mounts,
                "retentionState": state,
                "protectedClasses": protected_classes,
                "candidateReferenceInspection": "metadata_only_not_proven",
                "quarantineState": "inventory",
                "quarantineAuthorized": False,
                "deleteAuthorized": False,
            }
        )
    return result


def reference_proof(
    initial_inventory: dict[str, Any],
    final_inventory: dict[str, Any] | None,
    initial_references: list[dict[str, Any]],
    final_references: list[dict[str, Any]] | None,
    initial_mounts: list[str],
    final_mounts: list[str] | None,
    *,
    observation_seconds: int,
    coverage_complete: bool,
) -> dict[str, Any]:
    """将两次不读取内容的观察收敛为 S5 的保守结论。

    结论只能说明观测窗口内未见运行时引用，不能说明该数据永远不会再被需要；
    所以它绝不授权移动或删除。
    """
    initial_digest = json_sha256(initial_inventory)
    result: dict[str, Any] = {
        "observationSeconds": observation_seconds,
        "initialInventorySha256": initial_digest,
        "finalInventorySha256": None,
        "inventoryStable": None,
        "initialProcessReferenceCount": len(initial_references),
        "finalProcessReferenceCount": None,
        "initialMountCount": len(initial_mounts),
        "finalMountCount": None,
        "initialActiveLockCount": len(initial_inventory["activeLockPaths"]),
        "finalActiveLockCount": None,
        "coverageComplete": coverage_complete,
    }
    if final_inventory is None or final_references is None or final_mounts is None:
        result["state"] = "not_requested"
        return result

    final_digest = json_sha256(final_inventory)
    result.update(
        {
            "finalInventorySha256": final_digest,
            "inventoryStable": initial_digest == final_digest,
            "finalProcessReferenceCount": len(final_references),
            "finalMountCount": len(final_mounts),
            "finalActiveLockCount": len(final_inventory["activeLockPaths"]),
        }
    )
    if not coverage_complete:
        result["state"] = "coverage_incomplete"
    elif (
        initial_references
        or final_references
        or initial_mounts
        or final_mounts
        or initial_inventory["activeLockPaths"]
        or final_inventory["activeLockPaths"]
        or not result["inventoryStable"]
    ):
        result["state"] = "reference_or_change_observed"
    else:
        result["state"] = "observed_no_live_reference"
    return result


def attach_development_reference_proofs(
    data_results: list[dict[str, Any]],
    policy: dict[str, Any],
    *,
    observation_seconds: int,
    initial_process_snapshot: dict[str, Any],
    initial_mount_snapshot: dict[str, Any],
    initial_lock_snapshot: dict[str, Any],
    final_process_snapshot: dict[str, Any] | None,
    final_mount_snapshot: dict[str, Any] | None,
    final_lock_snapshot: dict[str, Any] | None,
) -> int:
    """为开发数据补充第二次只读观察；返回可进入人工批次确认的数量。"""
    runtime = policy["runtimeGovernance"]
    proof_eligible_count = 0
    for root in data_results:
        for item in root["objects"]:
            initial_inventory = item["inventory"]
            initial_references = item["processReferences"]
            initial_mounts = item["mountPoints"]
            final_inventory: dict[str, Any] | None = None
            final_references: list[dict[str, Any]] | None = None
            final_mounts: list[str] | None = None
            coverage_complete = bool(
                initial_inventory["coverageComplete"]
                and initial_inventory["manifestEvidenceCoverageComplete"]
                and initial_process_snapshot["coverageComplete"]
                and initial_mount_snapshot["coverageComplete"]
                and initial_lock_snapshot["coverageComplete"]
            )
            if final_process_snapshot is not None and final_mount_snapshot is not None and final_lock_snapshot is not None:
                path = Path(initial_inventory["path"])
                final_inventory = inventory_object(
                    path,
                    set(runtime["manifestFileNames"]),
                    runtime["maxEntriesPerObject"],
                    runtime["maxManifestBytes"],
                    final_lock_snapshot,
                )
                final_references = references_for(path, final_process_snapshot)
                final_mounts = mounts_for(path, final_mount_snapshot)
                coverage_complete = bool(
                    coverage_complete
                    and final_inventory["coverageComplete"]
                    and final_inventory["manifestEvidenceCoverageComplete"]
                    and final_process_snapshot["coverageComplete"]
                    and final_mount_snapshot["coverageComplete"]
                    and final_lock_snapshot["coverageComplete"]
                )
            proof = reference_proof(
                initial_inventory,
                final_inventory,
                initial_references,
                final_references,
                initial_mounts,
                final_mounts,
                observation_seconds=observation_seconds,
                coverage_complete=coverage_complete,
            )
            item["referenceProof"] = proof
            item["candidateReferenceInspection"] = proof["state"]
            if item["retentionState"] == "future_quarantine_candidate" and proof["state"] == "observed_no_live_reference":
                proof_eligible_count += 1
    return proof_eligible_count


def observation_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("观察秒数必须是整数") from error
    if seconds < 0 or seconds > 3600:
        raise argparse.ArgumentTypeError("观察秒数必须在 0 到 3600 之间")
    return seconds


def build_discovery(policy: dict[str, Any], *, reference_observation_seconds: int = 0) -> dict[str, Any]:
    runtime = policy["runtimeGovernance"]
    components = runtime["releaseComponents"]
    development_roots = runtime["developmentDataRoots"]
    scan_roots = [Path(item["releaseRoot"]) for item in components] + [Path(item["path"]) for item in development_roots]
    process_snapshot = process_path_snapshot(Path(policy["processRoot"]), scan_roots)
    mount_snapshot = parse_mounts(Path(runtime["mountInfoPath"]))
    lock_snapshot = parse_kernel_locks(Path(policy["processRoot"]) / "locks")
    component_results = [component_discovery(item, policy, process_snapshot, mount_snapshot, lock_snapshot) for item in components]
    data_results = [development_data_discovery(item, policy, process_snapshot, mount_snapshot, lock_snapshot) for item in development_roots]
    final_process_snapshot: dict[str, Any] | None = None
    final_mount_snapshot: dict[str, Any] | None = None
    final_lock_snapshot: dict[str, Any] | None = None
    if reference_observation_seconds:
        time.sleep(reference_observation_seconds)
        final_process_snapshot = process_path_snapshot(Path(policy["processRoot"]), scan_roots)
        final_mount_snapshot = parse_mounts(Path(runtime["mountInfoPath"]))
        final_lock_snapshot = parse_kernel_locks(Path(policy["processRoot"]) / "locks")
    s5_reference_proof_eligible_count = attach_development_reference_proofs(
        data_results,
        policy,
        observation_seconds=reference_observation_seconds,
        initial_process_snapshot=process_snapshot,
        initial_mount_snapshot=mount_snapshot,
        initial_lock_snapshot=lock_snapshot,
        final_process_snapshot=final_process_snapshot,
        final_mount_snapshot=final_mount_snapshot,
        final_lock_snapshot=final_lock_snapshot,
    )
    config = config_metadata(Path(policy["configDirectory"]), policy["requiredConfigMode"])
    interactive_agent = next(
        (item for item in component_results if item["name"] == "interactive_agent_sidecar"),
        None,
    )
    identity_gap = bool(
        interactive_agent
        and interactive_agent["identityEquation"].get("actualProcessCwdBound") is not True
    )
    candidate_count = sum(1 for component in component_results for release in component["releases"] if release["retentionState"] == "future_quarantine_candidate")
    findings: list[dict[str, str]] = [
        {"severity": "block", "code": "read_only_discovery", "message": "本输出只提供 S3--S6 发现证据，不授权迁移、隔离、删除、重启或切换。"},
        {"severity": "warning", "code": "credential_argument_not_inspected", "message": "为避免读取秘密，未检查进程实参或环境；凭证迁移 Gate 仍未满足。"},
    ]
    if identity_gap:
        findings.append(
            {
                "severity": "warning",
                "code": "interactive_agent_runtime_identity_unbound",
                "message": "Interactive Agent 活动链接未与实际进程 cwd 绑定，不能声明运行时身份等式成立。",
            }
        )
    if not process_snapshot["coverageComplete"]:
        findings.append({"severity": "block", "code": "process_reference_coverage_incomplete", "message": "进程路径引用扫描不完整，所有隔离候选保持 unknown。"})
    if not mount_snapshot["coverageComplete"]:
        findings.append({"severity": "block", "code": "mount_reference_coverage_incomplete", "message": "挂载扫描不完整，所有隔离候选保持 unknown。"})
    if not lock_snapshot["coverageComplete"]:
        findings.append({"severity": "block", "code": "active_lock_coverage_incomplete", "message": "活动文件锁扫描不完整，所有隔离候选保持 unknown。"})
    return {
        "schemaVersion": SCHEMA,
        "observedAt": utc_now(),
        "host": socket.gethostname(),
        "mode": "read_only",
        "serverWrites": False,
        "mutationAuthorized": False,
        "policySha256": hashlib.sha256(json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "processPathCoverage": process_snapshot,
        "mountCoverage": mount_snapshot,
        "activeFileLockCoverage": {key: value for key, value in lock_snapshot.items() if key != "_lockedInodes"},
        "credentialSurface": {
            "configMetadata": config,
            "processArgumentInspection": "not_performed_by_contract",
            "processEnvironmentInspection": "not_performed_by_contract",
            "migrationGate": "BLOCK_MUTATION",
        },
        "runtimeComponents": component_results,
        "developmentData": data_results,
        "futureQuarantineCandidateCount": candidate_count,
        "s5ReferenceProofEligibleCandidateCount": s5_reference_proof_eligible_count,
        "continuousGovernance": {
            "installationState": "not_installed",
            "dailyAudit": "required_read_only",
            "weeklyCapacityReview": "required_read_only",
            "monthlyRetentionReview": "required_read_only",
            "automaticDelete": False,
        },
        "findings": findings,
        "gate": {"decision": "BLOCK_MUTATION", "reason": "S3--S6 的任何写入均须独立任务、精确批次清单、恢复路径和相应 Gate。"},
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, help="策略 JSON；省略时读取同目录默认策略")
    parser.add_argument("--compact", action="store_true", help="输出单行 JSON")
    parser.add_argument(
        "--reference-observation-seconds",
        type=observation_seconds,
        default=0,
        help="S5 专用：间隔后第二次只读检查开发数据引用；0 表示不执行二次观察",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        result = build_discovery(load_policy(arguments.policy), reference_observation_seconds=arguments.reference_observation_seconds)
    except (DiscoveryError, OSError) as error:
        print(f"S3--S6 只读发现失败：{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=None if arguments.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
