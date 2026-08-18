#!/usr/bin/env python3

"""只读核验 S3 进程身份与凭证表面；绝不输出命令行、环境或配置值。"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
from typing import Any


SCHEMA = "domeye.server-s3-redacted-credential-surface/v1"
POLICY_SCHEMA = "domeye.server-directory-policy/v1"
POLICY_ENV = "DOMEYE_SERVER_GOVERNANCE_POLICY_B64"
DEFAULT_POLICY = Path(__file__).with_name("server-directory-policy.json")
IDENTITY_SIGNALS = {"cwd_or_executable", "listener_port", "active_release_argument"}
SENSITIVE_ARGUMENT = re.compile(
    rb"(?i)(token|secret|password|passwd|api[_-]?key|authorization|credential)"
)


class CredentialSurfaceError(RuntimeError):
    """表示 S3 只读验证的策略或采集不能安全完成。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(path: Path) -> Path:
    return path.resolve(strict=False)


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise CredentialSurfaceError(f"策略字段 {field} 必须是非空字符串")
    return value


def require_path(value: Any, field: str) -> Path:
    path = Path(require_string(value, field))
    if not path.is_absolute():
        raise CredentialSurfaceError(f"策略字段 {field} 必须是绝对路径")
    return path


def readonly_run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


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
        raise CredentialSurfaceError("无法读取有效的服务器目录策略") from error
    if not isinstance(policy, dict):
        raise CredentialSurfaceError("服务器目录策略顶层必须是对象")
    validate_policy(policy)
    return policy


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schemaVersion") != POLICY_SCHEMA:
        raise CredentialSurfaceError("服务器目录策略 schemaVersion 不受支持")
    require_string(policy.get("expectedHost"), "expectedHost")
    require_path(policy.get("processRoot"), "processRoot")
    managed = policy.get("managedRoots")
    if not isinstance(managed, list) or not managed:
        raise CredentialSurfaceError("managedRoots 必须是非空数组")
    managed_paths = [require_path(item.get("path"), "managedRoots.path") for item in managed if isinstance(item, dict)]
    if len(managed_paths) != len(managed):
        raise CredentialSurfaceError("managedRoots 项必须是对象")
    protected = policy.get("protectedRoots")
    if not isinstance(protected, list) or not protected:
        raise CredentialSurfaceError("protectedRoots 必须是非空数组")
    protected_paths = [require_path(item.get("path"), "protectedRoots.path") for item in protected if isinstance(item, dict)]
    if len(protected_paths) != len(protected):
        raise CredentialSurfaceError("protectedRoots 项必须是对象")
    for managed_path in managed_paths:
        if any(is_within(canonical(managed_path), canonical(protected_path)) for protected_path in protected_paths):
            raise CredentialSurfaceError(f"受管根不得进入保护树：{managed_path}")

    mutation = policy.get("mutationPolicy")
    if not isinstance(mutation, dict):
        raise CredentialSurfaceError("mutationPolicy 必须是对象")
    for name in ("auditWritesServer", "deleteEnabled", "moveEnabled", "restartEnabled", "productionSwitchEnabled"):
        if mutation.get(name) is not False:
            raise CredentialSurfaceError(f"只读 S3 验证要求 mutationPolicy.{name}=false")

    governance = policy.get("credentialGovernance")
    if not isinstance(governance, dict):
        raise CredentialSurfaceError("credentialGovernance 必须是对象")
    if governance.get("schemaVersion") != "domeye.server-s3-credential-governance/v1":
        raise CredentialSurfaceError("credentialGovernance schemaVersion 不受支持")
    for name in ("requiredConfigUid", "requiredConfigGid"):
        if not isinstance(governance.get(name), int) or governance[name] < 0:
            raise CredentialSurfaceError(f"credentialGovernance.{name} 必须是非负整数")
    if governance.get("requiredConfigMode") != "0600":
        raise CredentialSurfaceError("credentialGovernance.requiredConfigMode 必须固定为 0600")

    config_files = governance.get("configFiles")
    if not isinstance(config_files, list) or not config_files:
        raise CredentialSurfaceError("credentialGovernance.configFiles 必须是非空数组")
    file_ids: set[str] = set()
    for index, item in enumerate(config_files):
        if not isinstance(item, dict):
            raise CredentialSurfaceError(f"configFiles[{index}] 必须是对象")
        identifier = require_string(item.get("id"), f"configFiles[{index}].id")
        if identifier in file_ids or "/" in identifier or identifier in {".", ".."}:
            raise CredentialSurfaceError(f"configFiles id 非法或重复：{identifier}")
        file_ids.add(identifier)
        path = require_path(item.get("path"), f"configFiles[{index}].path")
        if not any(is_within(canonical(path), canonical(root)) for root in managed_paths):
            raise CredentialSurfaceError(f"受控配置必须位于受管根：{path}")
        if any(is_within(canonical(path), canonical(root)) for root in protected_paths):
            raise CredentialSurfaceError(f"受控配置不得进入保护树：{path}")

    components = governance.get("components")
    if not isinstance(components, list) or not components:
        raise CredentialSurfaceError("credentialGovernance.components 必须是非空数组")
    component_names: set[str] = set()
    for index, item in enumerate(components):
        if not isinstance(item, dict):
            raise CredentialSurfaceError(f"components[{index}] 必须是对象")
        name = require_string(item.get("name"), f"components[{index}].name")
        if name in component_names:
            raise CredentialSurfaceError(f"components.name 重复：{name}")
        component_names.add(name)
        active_link = require_path(item.get("activeLinkPath"), f"components[{index}].activeLinkPath")
        release_root = require_path(item.get("releaseRoot"), f"components[{index}].releaseRoot")
        if not any(is_within(canonical(active_link), canonical(root)) for root in managed_paths):
            raise CredentialSurfaceError(f"活动指针必须位于受管根：{active_link}")
        if not any(is_within(canonical(release_root), canonical(root)) for root in managed_paths):
            raise CredentialSurfaceError(f"release 根必须位于受管根：{release_root}")
        if any(is_within(canonical(active_link), canonical(root)) or is_within(canonical(release_root), canonical(root)) for root in protected_paths):
            raise CredentialSurfaceError(f"S3 组件不得进入保护树：{name}")
        port = item.get("listenerPort")
        if port is not None and (not isinstance(port, int) or not 1 <= port <= 65535):
            raise CredentialSurfaceError(f"components[{index}].listenerPort 必须为端口或 null")
        signals = item.get("requiredIdentitySignals")
        if not isinstance(signals, list) or not signals or any(signal not in IDENTITY_SIGNALS for signal in signals):
            raise CredentialSurfaceError(f"components[{index}].requiredIdentitySignals 非法")
        if len(signals) != len(set(signals)):
            raise CredentialSurfaceError(f"components[{index}].requiredIdentitySignals 不得重复")
        if "listener_port" in signals and port is None:
            raise CredentialSurfaceError(f"components[{index}] 要求 listener_port 但未提供端口")
        references = item.get("configFileIds")
        if not isinstance(references, list) or not references or any(reference not in file_ids for reference in references):
            raise CredentialSurfaceError(f"components[{index}].configFileIds 非法")


def file_metadata(path: Path, expected_uid: int, expected_gid: int, expected_mode: str) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.exists() or path.is_symlink()}
    if not result["exists"]:
        result["compliant"] = False
        result["reason"] = "missing"
        return result
    try:
        metadata = path.lstat()
    except OSError as error:
        result.update({"compliant": False, "reason": type(error).__name__})
        return result
    mode = format(stat.S_IMODE(metadata.st_mode), "04o")
    regular = stat.S_ISREG(metadata.st_mode) and not path.is_symlink()
    result.update({"regularFile": regular, "mode": mode, "uid": metadata.st_uid, "gid": metadata.st_gid})
    result["compliant"] = bool(regular and mode == expected_mode and metadata.st_uid == expected_uid and metadata.st_gid == expected_gid)
    return result


def active_release(active_link: Path, release_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(active_link), "isSymlink": active_link.is_symlink()}
    if not active_link.is_symlink():
        result.update({"valid": False, "reason": "not_symlink"})
        return result
    try:
        resolved = canonical(active_link)
    except OSError as error:
        result.update({"valid": False, "reason": type(error).__name__})
        return result
    within_root = is_within(resolved, canonical(release_root))
    result.update({"resolvedTarget": str(resolved), "targetExists": resolved.is_dir(), "withinReleaseRoot": within_root})
    result["valid"] = bool(result["targetExists"] and within_root)
    if not result["valid"]:
        result["reason"] = "missing_or_outside_release_root"
    return result


def listener_pids(port: int) -> dict[str, Any]:
    command = ["ss", "-H", "-ltnp", f"sport = :{port}"]
    try:
        completed = readonly_run(command)
    except OSError as error:
        return {"port": port, "coverageComplete": False, "pids": [], "error": type(error).__name__}
    result: dict[str, Any] = {"port": port, "coverageComplete": completed.returncode == 0, "pids": []}
    if completed.returncode:
        result["error"] = "ss_failed"
        return result
    result["pids"] = sorted({int(value) for value in re.findall(r"pid=(\d+)", completed.stdout)})
    return result


def process_paths(process_root: Path, active_targets: dict[str, Path]) -> dict[str, Any]:
    result: dict[str, Any] = {"coverageComplete": True, "componentPids": {name: [] for name in active_targets}}
    if not process_root.is_dir():
        result.update({"coverageComplete": False, "error": "process_root_missing"})
        return result
    bindings = {name: canonical(target) for name, target in active_targets.items()}
    for process in sorted((item for item in process_root.iterdir() if item.name.isdigit()), key=lambda item: int(item.name)):
        paths: list[Path] = []
        incomplete = False
        for name in ("cwd", "exe"):
            try:
                paths.append((process / name).resolve(strict=True))
            except FileNotFoundError:
                continue
            except (PermissionError, OSError):
                incomplete = True
        if incomplete:
            result["coverageComplete"] = False
        for component, target in bindings.items():
            if any(is_within(path, target) for path in paths):
                result["componentPids"][component].append(int(process.name))
    return result


def process_surface(
    process_root: Path,
    pid: int,
    active_link: Path,
    active_target: Path,
    listener_bound: bool,
) -> dict[str, Any]:
    process = process_root / str(pid)
    result: dict[str, Any] = {"pid": pid, "observable": True, "listenerPortBound": listener_bound}
    prefixes = (str(active_link).encode("utf-8"), str(active_target).encode("utf-8"))
    try:
        command = (process / "comm").read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError):
        command = "unknown"
    result["command"] = command
    path_bound = False
    for name in ("cwd", "exe"):
        try:
            observed = (process / name).resolve(strict=True)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        path_bound = path_bound or is_within(observed, canonical(active_target))
    result["cwdOrExecutableUnderActiveRelease"] = path_bound

    try:
        arguments = (process / "cmdline").read_bytes().split(b"\0")
    except (FileNotFoundError, PermissionError, OSError) as error:
        result.update({"commandLineReadable": False, "commandLineInspectable": False, "commandLineError": type(error).__name__})
        arguments = []
    else:
        arguments = [argument for argument in arguments if argument]
        result.update(
            {
                "commandLineReadable": True,
                "commandLineInspectable": bool(arguments),
                "argumentCount": len(arguments),
                "credentialLikeArgument": any(SENSITIVE_ARGUMENT.search(argument) for argument in arguments),
                "assignmentStyleArgument": any(b"=" in argument for argument in arguments),
                "activeReleaseArgumentBound": any(argument.startswith(prefix) for argument in arguments for prefix in prefixes),
            }
        )
    if "activeReleaseArgumentBound" not in result:
        result["activeReleaseArgumentBound"] = False

    try:
        entries = (process / "environ").read_bytes().split(b"\0")
    except (FileNotFoundError, PermissionError, OSError) as error:
        result.update({"environmentReadable": False, "environmentError": type(error).__name__})
    else:
        keys = [entry.split(b"=", 1)[0] for entry in entries if entry]
        result.update(
            {
                "environmentReadable": True,
                "environmentEntryCount": len(keys),
                "credentialLikeEnvironmentKeyPresent": any(SENSITIVE_ARGUMENT.search(key) for key in keys),
            }
        )
    result["commandLineValuesEmitted"] = False
    result["environmentValuesEmitted"] = False
    return result


def component_report(
    component: dict[str, Any],
    process_root: Path,
    path_pids: list[int],
    config_observations: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    release = active_release(Path(component["activeLinkPath"]), Path(component["releaseRoot"]))
    listener = listener_pids(component["listenerPort"]) if component.get("listenerPort") else None
    observed_pids = set(path_pids)
    if listener:
        observed_pids.update(listener["pids"])
    processes = [
        process_surface(
            process_root,
            pid,
            Path(component["activeLinkPath"]),
            Path(release.get("resolvedTarget", component["releaseRoot"])),
            bool(listener and pid in listener["pids"]),
        )
        for pid in sorted(observed_pids)
    ]
    required = component["requiredIdentitySignals"]
    identity_by_process = []
    for process in processes:
        signals = {
            "cwd_or_executable": process["cwdOrExecutableUnderActiveRelease"],
            "listener_port": process["listenerPortBound"],
            "active_release_argument": process["activeReleaseArgumentBound"],
        }
        identity_by_process.append({"pid": process["pid"], "signals": signals, "satisfiesRequiredSignals": all(signals[name] for name in required)})
    identity_complete = bool(release.get("valid") and any(item["satisfiesRequiredSignals"] for item in identity_by_process))
    command_line_complete = bool(processes) and all(item.get("commandLineInspectable") for item in processes)
    no_credential_like_arguments = bool(command_line_complete and not any(item.get("credentialLikeArgument") for item in processes))
    no_assignment_arguments = bool(command_line_complete and not any(item.get("assignmentStyleArgument") for item in processes))
    referenced_configs = {identifier: config_observations[identifier] for identifier in component["configFileIds"]}
    config_complete = all(item["compliant"] for item in referenced_configs.values())
    blockers: list[str] = []
    if not release.get("valid"):
        blockers.append("active_release_invalid")
    if listener and not listener["coverageComplete"]:
        blockers.append("listener_inspection_incomplete")
    if not identity_complete:
        blockers.append("process_identity_not_verified")
    if not command_line_complete:
        blockers.append("command_line_not_inspectable")
    if command_line_complete and not no_credential_like_arguments:
        blockers.append("credential_like_command_line_argument_detected")
    if command_line_complete and not no_assignment_arguments:
        blockers.append("assignment_style_command_line_argument_detected")
    if not config_complete:
        blockers.append("root_only_config_not_verified")
    return (
        {
            "name": component["name"],
            "activeRelease": release,
            "listener": listener,
            "referencedConfigFiles": referenced_configs,
            "processes": processes,
            "identityByProcess": identity_by_process,
            "identityState": "verified" if identity_complete else "not_verified",
            "commandLineCredentialState": "verified_no_credential_like_arguments" if no_credential_like_arguments else "not_verified",
            "commandLineAssignmentState": "verified_no_assignment_style_arguments" if no_assignment_arguments else "not_verified",
            "configurationState": "root_only_verified" if config_complete else "not_verified",
        },
        blockers,
    )


def build_report(policy: dict[str, Any]) -> dict[str, Any]:
    validate_policy(policy)
    governance = policy["credentialGovernance"]
    config_observations = {
        item["id"]: file_metadata(
            Path(item["path"]),
            governance["requiredConfigUid"],
            governance["requiredConfigGid"],
            governance["requiredConfigMode"],
        )
        for item in governance["configFiles"]
    }
    releases = {
        item["name"]: active_release(Path(item["activeLinkPath"]), Path(item["releaseRoot"]))
        for item in governance["components"]
    }
    valid_targets = {name: Path(item["resolvedTarget"]) for name, item in releases.items() if item.get("valid")}
    path_scan = process_paths(Path(policy["processRoot"]), valid_targets)
    components = []
    blockers: list[str] = []
    for component in governance["components"]:
        result, component_blockers = component_report(
            component,
            Path(policy["processRoot"]),
            path_scan["componentPids"].get(component["name"], []),
            config_observations,
        )
        components.append(result)
        blockers.extend(f"{component['name']}:{reason}" for reason in component_blockers)
    if not path_scan["coverageComplete"]:
        blockers.append("process_path_coverage_incomplete")
    if socket.gethostname() != policy["expectedHost"]:
        blockers.append("unexpected_host")
    result = {
        "schemaVersion": SCHEMA,
        "observedAt": utc_now(),
        "mode": "redacted_read_only",
        "serverWrites": False,
        "oldDomeyeTouched": False,
        "githubCredentialsChanged": False,
        "commandLineValuesEmitted": False,
        "environmentValuesEmitted": False,
        "configurationContentsRead": False,
        "configFiles": config_observations,
        "processPathCoverage": path_scan,
        "components": components,
        "s3VerificationState": "not_verified" if blockers else "observed_no_command_line_credential_surface",
        "gate": {
            "decision": "BLOCK_MUTATION",
            "reasons": ["S3 只读验证不授权服务重启、Screen 迁移、配置写入或凭证迁移", *blockers],
        },
        "mutationAuthorized": False,
    }
    return result


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    try:
        result = build_report(load_policy(args.policy))
    except CredentialSurfaceError as error:
        print(f"S3 脱敏验证失败：{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":") if args.compact else None, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
