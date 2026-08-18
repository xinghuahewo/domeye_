#!/usr/bin/env python3

"""只读盘点 Domeye-Core 服务器目录、运行指针与保护边界。"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
from typing import Any


AUDIT_SCHEMA = "domeye.server-directory-audit/v1"
POLICY_SCHEMA = "domeye.server-directory-policy/v1"
DEFAULT_POLICY = Path(__file__).with_name("server-directory-policy.json")
POLICY_ENV = "DOMEYE_SERVER_GOVERNANCE_POLICY_B64"


class AuditError(RuntimeError):
    """表示策略或只读采集失败。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AuditError(f"JSON 顶层必须是对象：{path}")
    return value


def load_policy(path: Path | None) -> dict[str, Any]:
    if path is not None:
        policy = read_json(path)
    elif os.environ.get(POLICY_ENV):
        try:
            raw = base64.b64decode(os.environ[POLICY_ENV], validate=True)
            policy = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuditError(f"环境变量 {POLICY_ENV} 不是有效的 base64 JSON") from error
    else:
        policy = read_json(DEFAULT_POLICY)
    validate_policy(policy)
    return policy


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise AuditError(f"策略字段 {field} 必须是非空字符串")
    return value


def require_absolute_path(value: Any, field: str) -> Path:
    path = Path(require_string(value, field))
    if not path.is_absolute():
        raise AuditError(f"策略字段 {field} 必须是绝对路径")
    return path


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schemaVersion") != POLICY_SCHEMA:
        raise AuditError("服务器目录策略 schemaVersion 不受支持")
    require_string(policy.get("expectedHost"), "expectedHost")
    for field in (
        "filesystemPath",
        "processRoot",
        "sourceCheckout",
        "runtimeRoot",
        "configDirectory",
    ):
        require_absolute_path(policy.get(field), field)
    if not isinstance(policy.get("screenSessionsEnabled"), bool):
        raise AuditError("screenSessionsEnabled 必须是布尔值")
    for field in ("protectedRoots", "managedRoots", "activeLinks", "releaseRoots"):
        value = policy.get(field)
        if not isinstance(value, list) or not value:
            raise AuditError(f"策略字段 {field} 必须是非空数组")
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise AuditError(f"策略字段 {field}[{index}] 必须是对象")
            require_absolute_path(item.get("path"), f"{field}[{index}].path")
            if field == "activeLinks":
                require_absolute_path(
                    item.get("allowedTargetPrefix"),
                    f"{field}[{index}].allowedTargetPrefix",
                )
    protected = [Path(item["path"]) for item in policy["protectedRoots"]]
    managed = [Path(item["path"]) for item in policy["managedRoots"]]
    for managed_path in managed:
        for protected_path in protected:
            if managed_path == protected_path or protected_path in managed_path.parents:
                raise AuditError(f"受管路径不得进入保护树：{managed_path}")
    thresholds = policy.get("diskThresholdPercent")
    if not isinstance(thresholds, dict):
        raise AuditError("diskThresholdPercent 必须是对象")
    values = [thresholds.get(name) for name in ("warning", "critical", "stopNewBuilds")]
    if not all(isinstance(value, int) for value in values):
        raise AuditError("磁盘阈值必须是整数")
    if not 0 < values[0] < values[1] < values[2] <= 100:
        raise AuditError("磁盘阈值必须满足 0 < warning < critical < stopNewBuilds <= 100")
    mutation = policy.get("mutationPolicy")
    if not isinstance(mutation, dict):
        raise AuditError("mutationPolicy 必须是对象")
    for field in (
        "auditWritesServer",
        "deleteEnabled",
        "moveEnabled",
        "restartEnabled",
        "productionSwitchEnabled",
    ):
        if mutation.get(field) is not False:
            raise AuditError(f"只读策略要求 mutationPolicy.{field}=false")


def run_readonly(arguments: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def path_metadata(path: Path, include_size: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.exists() or path.is_symlink()}
    if not result["exists"]:
        return result
    info = path.lstat()
    result.update(
        {
            "kind": (
                "symlink"
                if stat.S_ISLNK(info.st_mode)
                else "directory"
                if stat.S_ISDIR(info.st_mode)
                else "file"
            ),
            "mode": format(stat.S_IMODE(info.st_mode), "04o"),
            "uid": info.st_uid,
            "gid": info.st_gid,
            "modifiedAt": datetime.fromtimestamp(info.st_mtime, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
    )
    if include_size and path.is_dir() and not path.is_symlink():
        completed = run_readonly(["du", "-sB1", "--one-file-system", str(path)])
        if completed.returncode == 0:
            result["allocatedBytes"] = int(completed.stdout.split()[0])
        else:
            result["sizeError"] = completed.stderr.strip() or "du_failed"
    elif path.is_file():
        result["allocatedBytes"] = info.st_size
    return result


def filesystem_observation(path: Path, thresholds: dict[str, int]) -> dict[str, Any]:
    stats = os.statvfs(path)
    total = stats.f_blocks * stats.f_frsize
    available = stats.f_bavail * stats.f_frsize
    used = total - available
    percent = round((used / total) * 100, 2) if total else 0.0
    if percent >= thresholds["stopNewBuilds"]:
        level = "stop_new_builds"
    elif percent >= thresholds["critical"]:
        level = "critical"
    elif percent >= thresholds["warning"]:
        level = "warning"
    else:
        level = "normal"
    return {
        "path": str(path),
        "totalBytes": total,
        "usedBytes": used,
        "availableBytes": available,
        "usedPercent": percent,
        "level": level,
    }


def top_level_observations(
    root: Path, protected_roots: list[Path], managed_roots: list[Path]
) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    protected = {path.resolve(strict=False) for path in protected_roots}
    managed = {path.resolve(strict=False) for path in managed_roots}
    observations: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda value: value.name):
        resolved = child.resolve(strict=False)
        if resolved in protected:
            classification = "protected"
        elif resolved in managed:
            classification = "managed"
        else:
            classification = "unclassified"
        observations.append(
            {
                **path_metadata(child, include_size=False),
                "name": child.name,
                "classification": classification,
            }
        )
    return observations


def git_observation(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(root), "isGitCheckout": False}
    probe = run_readonly(["git", "rev-parse", "--is-inside-work-tree"], cwd=root)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        result["error"] = probe.stderr.strip() or "not_a_git_checkout"
        return result
    result["isGitCheckout"] = True
    commands = {
        "head": ["git", "rev-parse", "HEAD"],
        "branch": ["git", "branch", "--show-current"],
    }
    for field, command in commands.items():
        completed = run_readonly(command, cwd=root)
        result[field] = completed.stdout.strip() if completed.returncode == 0 else None
    status = run_readonly(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=root
    )
    if status.returncode != 0:
        result["statusError"] = status.stderr.strip() or "git_status_failed"
        return result
    entries = [entry for entry in status.stdout.split("\0") if entry]
    result["modifiedCount"] = sum(entry[:2] != "??" for entry in entries)
    result["untrackedCount"] = sum(entry[:2] == "??" for entry in entries)
    result["changeCount"] = len(entries)
    result["clean"] = not entries
    remote = run_readonly(["git", "remote"], cwd=root)
    result["remoteCount"] = len(remote.stdout.splitlines()) if remote.returncode == 0 else None
    return result


def active_link_observation(item: dict[str, Any]) -> dict[str, Any]:
    link = Path(item["path"])
    prefix = Path(item["allowedTargetPrefix"]).resolve(strict=False)
    result: dict[str, Any] = {"name": item["name"], "path": str(link)}
    result["isSymlink"] = link.is_symlink()
    if not link.is_symlink():
        result.update({"valid": False, "reason": "not_symlink"})
        return result
    raw_target = os.readlink(link)
    resolved = link.resolve(strict=False)
    result.update(
        {
            "rawTarget": raw_target,
            "resolvedTarget": str(resolved),
            "targetExists": resolved.exists(),
            "releaseId": resolved.name,
        }
    )
    within_prefix = resolved == prefix or prefix in resolved.parents
    result["withinAllowedPrefix"] = within_prefix
    result["valid"] = bool(resolved.exists() and within_prefix)
    if not result["valid"]:
        result["reason"] = "missing_or_outside_allowed_prefix"
    return result


def release_root_observation(item: dict[str, Any]) -> dict[str, Any]:
    root = Path(item["path"])
    result: dict[str, Any] = {"name": item["name"], "path": str(root), "exists": root.is_dir()}
    if not root.is_dir():
        return result
    directories = [child for child in root.iterdir() if child.is_dir() and not child.is_symlink()]
    visible = sorted(child.name for child in directories if not child.name.startswith("."))
    hidden = sorted(child.name for child in directories if child.name.startswith("."))
    result.update(
        {
            "visibleDirectoryCount": len(visible),
            "hiddenDirectoryCount": len(hidden),
            "hiddenDirectories": hidden,
        }
    )
    size = path_metadata(root)
    if "allocatedBytes" in size:
        result["allocatedBytes"] = size["allocatedBytes"]
    if visible:
        result["lexicalFirst"] = visible[0]
        result["lexicalLast"] = visible[-1]
    return result


def config_permission_observation(directory: Path, required_mode: str) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(directory), "requiredMode": required_mode, "files": []}
    if not directory.is_dir():
        result["exists"] = False
        result["allCompliant"] = False
        return result
    result["exists"] = True
    expected = int(required_mode, 8)
    for child in sorted(directory.iterdir(), key=lambda value: value.name):
        if not child.is_file() or child.is_symlink():
            continue
        info = child.stat()
        mode = stat.S_IMODE(info.st_mode)
        result["files"].append(
            {
                "name": child.name,
                "mode": format(mode, "04o"),
                "uid": info.st_uid,
                "gid": info.st_gid,
                "compliant": mode == expected,
            }
        )
    result["allCompliant"] = bool(result["files"]) and all(
        item["compliant"] for item in result["files"]
    )
    return result


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def process_references(process_root: Path, roots: list[Path]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    if not process_root.is_dir():
        return references
    canonical_roots = [root.resolve(strict=False) for root in roots]
    for process in sorted(
        (item for item in process_root.iterdir() if item.name.isdigit()),
        key=lambda item: int(item.name),
    ):
        try:
            cwd = (process / "cwd").resolve(strict=True)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        matching = [str(root) for root in canonical_roots if is_within(cwd, root)]
        if not matching:
            continue
        try:
            command = (process / "comm").read_text(encoding="utf-8").strip()
        except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError):
            command = "unknown"
        try:
            executable = str((process / "exe").resolve(strict=True))
        except (FileNotFoundError, PermissionError, OSError):
            executable = None
        references.append(
            {
                "pid": int(process.name),
                "command": command,
                "cwd": str(cwd),
                "executable": executable,
                "matchedRoots": matching,
            }
        )
    return references


def process_identity(process_root: Path, pid: int) -> dict[str, Any]:
    process = process_root / str(pid)
    result: dict[str, Any] = {"pid": pid}
    try:
        result["command"] = (process / "comm").read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError):
        result["command"] = "unknown"
    try:
        result["cwd"] = str((process / "cwd").resolve(strict=True))
    except (FileNotFoundError, PermissionError, OSError):
        result["cwd"] = None
    try:
        result["executable"] = str((process / "exe").resolve(strict=True))
    except (FileNotFoundError, PermissionError, OSError):
        result["executable"] = None
    return result


def direct_child_pids(process_root: Path, parent_pid: int) -> list[int]:
    children: list[int] = []
    if not process_root.is_dir():
        return children
    for process in process_root.iterdir():
        if not process.name.isdigit():
            continue
        try:
            status_text = (process / "status").read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError):
            continue
        match = re.search(r"^PPid:\s+(\d+)$", status_text, flags=re.MULTILINE)
        if match and int(match.group(1)) == parent_pid:
            children.append(int(process.name))
    return sorted(children)


def screen_session_observations(process_root: Path) -> list[dict[str, Any]]:
    completed = run_readonly(["screen", "-ls"])
    listing = "\n".join((completed.stdout, completed.stderr))
    sessions: list[dict[str, Any]] = []
    for match in re.finditer(r"^\s*(\d+)\.([^\s(]+)\s+\(", listing, flags=re.MULTILINE):
        pid = int(match.group(1))
        sessions.append(
            {
                "session": match.group(2),
                "process": process_identity(process_root, pid),
                "directChildren": [
                    process_identity(process_root, child)
                    for child in direct_child_pids(process_root, pid)
                ],
            }
        )
    return sessions


def build_audit(policy: dict[str, Any]) -> dict[str, Any]:
    hostname = socket.gethostname()
    protected_paths = [Path(item["path"]) for item in policy["protectedRoots"]]
    managed_paths = [Path(item["path"]) for item in policy["managedRoots"]]
    filesystem = filesystem_observation(
        Path(policy["filesystemPath"]), policy["diskThresholdPercent"]
    )
    top_level = top_level_observations(
        Path(policy["filesystemPath"]), protected_paths, managed_paths
    )
    source = git_observation(Path(policy["sourceCheckout"]))
    links = [active_link_observation(item) for item in policy["activeLinks"]]
    releases = [release_root_observation(item) for item in policy["releaseRoots"]]
    config = config_permission_observation(
        Path(policy["configDirectory"]), policy["requiredConfigMode"]
    )
    protected_processes = process_references(Path(policy["processRoot"]), protected_paths)
    managed_processes = process_references(Path(policy["processRoot"]), managed_paths)
    screen_sessions = (
        screen_session_observations(Path(policy["processRoot"]))
        if policy["screenSessionsEnabled"]
        else []
    )
    findings: list[dict[str, str]] = []
    if hostname != policy["expectedHost"]:
        findings.append(
            {
                "severity": "block",
                "code": "unexpected_host",
                "message": f"主机身份不匹配：{hostname} != {policy['expectedHost']}",
            }
        )
    if not source.get("clean", False):
        findings.append(
            {
                "severity": "warning",
                "code": "source_checkout_dirty",
                "message": "服务器源码 checkout 不是干净的 main，禁止用作发布来源。",
            }
        )
    invalid_links = [item["name"] for item in links if not item["valid"]]
    if invalid_links:
        findings.append(
            {
                "severity": "block",
                "code": "invalid_active_links",
                "message": "活动指针无效：" + ", ".join(invalid_links),
            }
        )
    hidden_count = sum(item.get("hiddenDirectoryCount", 0) for item in releases)
    if hidden_count:
        findings.append(
            {
                "severity": "warning",
                "code": "hidden_release_directories",
                "message": f"发现 {hidden_count} 个隐藏 release/build 目录，只能先分类和隔离。",
            }
        )
    if filesystem["level"] != "normal":
        findings.append(
            {
                "severity": "block" if filesystem["level"] == "stop_new_builds" else "warning",
                "code": "disk_usage_threshold",
                "message": (
                    f"文件系统使用率 {filesystem['usedPercent']}%，级别 {filesystem['level']}。"
                ),
            }
        )
    unclassified = [item for item in top_level if item["classification"] == "unclassified"]
    if unclassified:
        findings.append(
            {
                "severity": "warning",
                "code": "unclassified_top_level_entries",
                "message": f"发现 {len(unclassified)} 个顶层对象尚未纳入受管或保护分类。",
            }
        )
    if not config["allCompliant"]:
        findings.append(
            {
                "severity": "block",
                "code": "config_permission_mismatch",
                "message": "运行配置权限不满足策略。",
            }
        )
    return {
        "schemaVersion": AUDIT_SCHEMA,
        "observedAt": utc_now(),
        "host": hostname,
        "expectedHost": policy["expectedHost"],
        "mode": "read_only",
        "mutationAuthorized": False,
        "policySchemaVersion": policy["schemaVersion"],
        "policySha256": hashlib.sha256(
            json.dumps(
                policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
        "filesystem": filesystem,
        "topLevelEntries": top_level,
        "sourceCheckout": source,
        "protectedRoots": [
            {**item, **path_metadata(Path(item["path"]))} for item in policy["protectedRoots"]
        ],
        "managedRoots": [
            {**item, **path_metadata(Path(item["path"]))} for item in policy["managedRoots"]
        ],
        "activeLinks": links,
        "releaseRoots": releases,
        "configPermissions": config,
        "protectedProcessReferences": protected_processes,
        "managedProcessReferences": managed_processes,
        "screenSessions": screen_sessions,
        "retentionPolicy": policy["retention"],
        "mutationPolicy": policy["mutationPolicy"],
        "findings": findings,
        "gate": {
            "decision": "BLOCK_MUTATION" if findings or protected_processes else "READ_ONLY_CLEAR",
            "reason": (
                "存在风险项或保护进程；本审计不授权任何服务器写入。"
                if findings or protected_processes
                else "只读盘点未发现阻断项；服务器写入仍需独立任务授权。"
            ),
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, help="策略 JSON；省略时读取同目录默认策略")
    parser.add_argument("--compact", action="store_true", help="输出单行 JSON")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        audit = build_audit(load_policy(arguments.policy))
    except (AuditError, OSError) as error:
        print(f"服务器目录只读审计失败：{error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            audit,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if arguments.compact else 2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
