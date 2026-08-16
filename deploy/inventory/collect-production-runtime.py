#!/usr/bin/env python3
"""只读采集 Domeye 当前生产运行身份，并仅向标准输出写出带摘要的 JSON。

本脚本设计为由受信操作者在 10.99.8.16 本机终端执行。它不发起网络请求，
不执行 Git fetch，不读取进程环境、.env、认证文件或密钥文件，也不创建临时
文件。输出摘要绑定 ``inventory`` 的 UTF-8、排序键、紧凑 JSON 字节串。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = "domeye_production_runtime_inventory_v1"
HASH_CONTRACT = "sha256(canonical_compact_sorted_utf8(inventory))"

PROJECT_ROOT = Path("/home/bgpdata/Domeye-Core")
RUNTIME_ROOT = Path("/home/bgpdata/Domeye-Core-runtime")
RUNTIME_RELEASES = RUNTIME_ROOT / "releases"
RUNTIME_STATE = RUNTIME_ROOT / "state"
BACKEND_PORT = 28473
FRONTEND_PORT = 28471
SIDECAR_PORT = 28474
CANARY_PORT = 31631
NGINX_CONFIG = Path("/etc/nginx/conf.d/domeye-core.conf")

EXPECTED_RUNTIME_RELEASE = re.compile(
    r"^/home/bgpdata/Domeye-Core-runtime/releases/"
    r"([0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9._-]{0,95})/backend$"
)
PID_PATTERN = re.compile(r"\bpid=(\d+)\b")

SENSITIVE_COMPONENTS = {
    ".env",
    "auth",
    "authentication",
    "credential",
    "credentials",
    "env",
    "key",
    "keys",
    "secret",
    "secrets",
    "token",
    "tokens",
}
SENSITIVE_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
}

IDENTITY_FILENAMES = {
    "ACCEPTANCE.json",
    "CORE-SHA256",
    "GIT-CLOSURE.md",
    "RELEASE-MANIFEST.json",
    "RUNTIME-METADATA.json",
    "SHA256SUMS",
    "active.json",
    "frontend-build.json",
    "frontend-current",
    "frontend-rollback.json",
    "manifest.json",
    "runtime-metadata.json",
}
ARCHIVE_SUFFIXES = (
    ".tar",
    ".tar.gz",
    ".tar.zst",
    ".tgz",
    ".zip",
)
IDENTITY_SUFFIXES = (
    ".sha256",
    ".sha256sum",
    ".sha256sums",
)
MANAGER_NAME = re.compile(
    r"^(?:manage|manager|rollback|start|stop|status|activate|prepare)"
    r"[A-Za-z0-9._-]*\.sh$"
)

KNOWN_MANAGER_SCRIPTS = (
    PROJECT_ROOT / "deploy/manage-fixed-backend.sh",
    PROJECT_ROOT / "dev/backend/manage-dev-api.sh",
    PROJECT_ROOT / "deploy/start-backend.sh",
    PROJECT_ROOT / "deploy/stop-backend.sh",
    PROJECT_ROOT / "deploy/status.sh",
    PROJECT_ROOT / "deploy/release/prepare.sh",
    PROJECT_ROOT / "deploy/release/activate.sh",
    PROJECT_ROOT / "deploy/release/rollback.sh",
    PROJECT_ROOT / "deploy/country-outage-agent/manage.sh",
)

KNOWN_NODE_CANDIDATES = (
    Path("/home/bgpdata/.local/node-v22.23.1-linux-x64/bin/node"),
    Path("/usr/local/bin/node"),
    Path("/usr/bin/node"),
)

KNOWN_PYTHON_CANDIDATES = (
    PROJECT_ROOT / "backend/.venv/bin/python",
    RUNTIME_ROOT / "country-outage-agent/current/pdf-venv/bin/python",
    Path("/usr/local/bin/python3"),
    Path("/usr/bin/python3"),
)

KNOWN_FONT_ROOTS = (
    Path("/opt/domeye/fonts"),
    Path("/usr/share/fonts/opentype/noto"),
)


@dataclass(frozen=True)
class CommandResult:
    available: bool
    returncode: int | None
    stdout: str


def iso_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def command_path(command: str) -> str | None:
    """只读解析 PATH；不调用 shell 或可能联网的包管理器。"""

    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / command
        try:
            metadata = candidate.stat()
        except OSError:
            continue
        if stat.S_ISREG(metadata.st_mode) and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def run_command(
    arguments: Sequence[str],
    *,
    timeout_seconds: int = 10,
    environment: dict[str, str] | None = None,
) -> CommandResult:
    """执行固定只读命令；失败时不回传 stderr，避免旁路泄露配置。"""

    if not arguments:
        return CommandResult(False, None, "")
    executable = arguments[0]
    resolved = executable if "/" in executable else command_path(executable)
    if resolved is None:
        return CommandResult(False, None, "")
    merged_environment = {
        "PATH": os.environ.get(
            "PATH",
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        ),
        "LC_ALL": "C",
        "LANG": "C",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    if environment:
        merged_environment.update(environment)
    try:
        completed = subprocess.run(
            [resolved, *arguments[1:]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout_seconds,
            env=merged_environment,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return CommandResult(True, None, "")
    return CommandResult(
        True,
        completed.returncode,
        completed.stdout.strip(),
    )


def permission_text(mode: int) -> str:
    return f"{stat.S_IMODE(mode):04o}"


def is_sensitive_path(path: Path) -> bool:
    for component in path.parts:
        lowered = component.lower()
        if lowered in SENSITIVE_COMPONENTS:
            return True
        if any(lowered.endswith(suffix) for suffix in SENSITIVE_SUFFIXES):
            return True
        words = set(filter(None, re.split(r"[^a-z0-9]+", lowered)))
        if words & SENSITIVE_COMPONENTS:
            return True
    return False


def safe_lstat(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError:
        return {
            "path": str(path),
            "exists": False,
        }
    kind = (
        "symlink"
        if stat.S_ISLNK(metadata.st_mode)
        else "regular_file"
        if stat.S_ISREG(metadata.st_mode)
        else "directory"
        if stat.S_ISDIR(metadata.st_mode)
        else "other"
    )
    return {
        "path": str(path),
        "exists": True,
        "kind": kind,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": permission_text(metadata.st_mode),
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
    }


def stable_file_sha256(path: Path) -> dict[str, Any]:
    """对安全普通文件做稳定哈希；认证类路径和符号链接直接拒绝读取。"""

    base = safe_lstat(path)
    if not base.get("exists"):
        return base
    if is_sensitive_path(path):
        return {
            **base,
            "sha256_status": "excluded_sensitive_path",
        }
    if base.get("kind") != "regular_file":
        return {
            **base,
            "sha256_status": "not_regular_file",
        }
    try:
        before = path.lstat()
        digest = hashlib.sha256()
        with path.open("rb", buffering=0) as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        after = path.lstat()
    except OSError:
        return {
            **base,
            "sha256_status": "read_failed",
        }
    immutable_fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if any(getattr(before, field) != getattr(after, field) for field in immutable_fields):
        return {
            **base,
            "sha256_status": "changed_during_read",
        }
    return {
        **base,
        "sha256_status": "verified",
        "sha256": digest.hexdigest(),
    }


def resolved_path(path: Path) -> str | None:
    try:
        return str(path.resolve(strict=True))
    except OSError:
        return None


def socket_inventory(port: int) -> dict[str, Any]:
    result = run_command(
        ["ss", "-H", "-ltnp", f"sport = :{port}"],
        timeout_seconds=5,
    )
    if not result.available:
        return {
            "port": port,
            "status": "ss_unavailable",
            "listening": None,
            "pids": [],
        }
    if result.returncode != 0:
        return {
            "port": port,
            "status": "query_failed",
            "listening": None,
            "pids": [],
        }
    pids = sorted({int(value) for value in PID_PATTERN.findall(result.stdout)})
    return {
        "port": port,
        "status": "observed",
        "listening": bool(result.stdout),
        "pids": pids,
        "process_visibility": "available" if pids else "not_visible",
    }


def fixed_screen_inventory() -> dict[str, Any]:
    result = run_command(["screen", "-ls"], timeout_seconds=5)
    expected_names = (
        "domeye_country_outage_agent",
        "domeye_core_p0_canary",
    )
    sessions: dict[str, list[str]] = {name: [] for name in expected_names}
    if not result.available:
        return {
            "status": "screen_unavailable",
            "sessions": sessions,
        }
    if result.returncode not in {0, 1}:
        return {
            "status": "query_failed",
            "sessions": sessions,
        }
    for line in result.stdout.splitlines():
        token = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        for name in expected_names:
            if re.fullmatch(rf"\d+\.{re.escape(name)}", token):
                sessions[name].append(token)
    return {
        "status": "observed",
        "sessions": {
            name: sorted(values)
            for name, values in sessions.items()
        },
    }


def process_inventory(pid: int) -> dict[str, Any]:
    proc = Path("/proc") / str(pid)
    cwd_link = proc / "cwd"
    exe_link = proc / "exe"
    cwd = resolved_path(cwd_link)
    executable = resolved_path(exe_link)
    ps = run_command(
        [
            "ps",
            "-p",
            str(pid),
            "-o",
            "pid=,ppid=,lstart=,etime=,comm=",
        ],
        timeout_seconds=5,
    )
    return {
        "pid": pid,
        "proc_exists": proc.is_dir(),
        "cwd": cwd,
        "executable": executable,
        "executable_identity": (
            stable_file_sha256(Path(executable)) if executable else None
        ),
        "ps_identity": (
            ps.stdout if ps.returncode == 0 and ps.stdout else None
        ),
        "environment_read": False,
        "command_line_read": False,
    }


def verify_core_hashes(backend_directory: Path) -> dict[str, Any]:
    manifest = backend_directory / "core.sha256"
    manifest_identity = stable_file_sha256(manifest)
    if manifest_identity.get("sha256_status") != "verified":
        return {
            "manifest": manifest_identity,
            "status": "manifest_unavailable",
            "expected": 0,
            "verified": 0,
            "mismatches": [],
        }
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {
            "manifest": manifest_identity,
            "status": "manifest_read_failed",
            "expected": 0,
            "verified": 0,
            "mismatches": [],
        }
    expected = 0
    verified = 0
    mismatches: list[str] = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})[ *](.+)", line)
        if not match:
            mismatches.append("invalid_manifest_line")
            continue
        relative = match.group(2)
        candidate = backend_directory / relative
        try:
            candidate_relative = candidate.resolve(strict=True).relative_to(
                backend_directory.resolve(strict=True)
            )
        except (OSError, ValueError):
            mismatches.append(relative)
            continue
        if is_sensitive_path(candidate_relative):
            mismatches.append(relative)
            continue
        expected += 1
        identity = stable_file_sha256(candidate)
        if identity.get("sha256") == match.group(1):
            verified += 1
        else:
            mismatches.append(relative)
    return {
        "manifest": manifest_identity,
        "status": (
            "verified"
            if expected > 0 and verified == expected and not mismatches
            else "mismatch"
        ),
        "expected": expected,
        "verified": verified,
        "mismatches": sorted(set(mismatches)),
    }


def release_inventory(processes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    matching: list[tuple[str, str]] = []
    for process in processes:
        cwd = process.get("cwd")
        if not isinstance(cwd, str):
            continue
        match = EXPECTED_RUNTIME_RELEASE.fullmatch(cwd)
        if match:
            matching.append((match.group(1), cwd))
    release_ids = sorted({release_id for release_id, _ in matching})
    if len(release_ids) != 1:
        return {
            "status": "not_uniquely_resolved",
            "release_ids": release_ids,
            "backend_cwds": sorted({cwd for _, cwd in matching}),
        }
    release_id = release_ids[0]
    release_root = RUNTIME_RELEASES / release_id
    backend_directory = release_root / "backend"
    return {
        "status": "resolved_from_28473_process_cwd",
        "release_id": release_id,
        "release_root": str(release_root),
        "release_root_realpath": resolved_path(release_root),
        "release_root_metadata": safe_lstat(release_root),
        "backend_directory": str(backend_directory),
        "backend_core": verify_core_hashes(backend_directory),
        "identity_files": collect_identity_files(
            release_root,
            maximum_depth=2,
        ),
    }


def sanitized_git_url(value: str) -> str:
    without_parameters = re.split(r"[?#]", value, maxsplit=1)[0]
    without_url_userinfo = re.sub(
        r"(?<=://)[^/@\s]+@",
        "<redacted>@",
        without_parameters,
    )
    return re.sub(
        r"^[^/@:\s]+@(?=[^/\s]+:)",
        "<redacted>@",
        without_url_userinfo,
    )


def git_value(arguments: Sequence[str]) -> str | None:
    result = run_command(
        ["git", "-C", str(PROJECT_ROOT), *arguments],
        timeout_seconds=10,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.splitlines()[0]


def commit_identity(reference: str) -> dict[str, Any] | None:
    commit = git_value(["rev-parse", "--verify", f"{reference}^{{commit}}"])
    if not commit or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        return None
    tree = git_value(["show", "-s", "--format=%T", commit])
    committed_at = git_value(["show", "-s", "--format=%cI", commit])
    return {
        "reference": reference,
        "commit": commit,
        "tree": tree,
        "committed_at": committed_at,
    }


def git_inventory() -> dict[str, Any]:
    git_dir = git_value(["rev-parse", "--git-dir"])
    origin = git_value(["remote", "get-url", "origin"])
    return {
        "project_root": str(PROJECT_ROOT),
        "git_directory": git_dir,
        "origin_url": sanitized_git_url(origin) if origin else None,
        "head": commit_identity("HEAD"),
        "main": commit_identity("refs/heads/main"),
        "origin_main": commit_identity("refs/remotes/origin/main"),
        "network_refresh_performed": False,
        "worktree_status_read": False,
    }


def parse_nginx_config(path: Path) -> dict[str, Any]:
    identity = stable_file_sha256(path)
    result: dict[str, Any] = {
        "config": identity,
        "listen_ports": [],
        "roots": [],
        "proxy_pass": [],
    }
    if identity.get("sha256_status") != "verified":
        return result
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return result
    sanitized = re.sub(r"#.*$", "", text, flags=re.MULTILINE)
    listen_ports = {
        int(match)
        for match in re.findall(r"\blisten\s+(?:\[[^\]]+\]:)?(\d+)\b", sanitized)
    }
    roots = {
        value.rstrip(";")
        for value in re.findall(r"(?m)^\s*root\s+([^\s;]+)\s*;", sanitized)
        if value.startswith("/")
    }
    proxies = {
        value.rstrip(";")
        for value in re.findall(
            r"(?m)^\s*proxy_pass\s+([^\s;]+)\s*;",
            sanitized,
        )
    }
    result["listen_ports"] = sorted(listen_ports)
    result["roots"] = sorted(roots)
    result["proxy_pass"] = sorted(proxies)
    return result


def frontend_tree_identity(root: Path) -> dict[str, Any]:
    metadata = safe_lstat(root)
    if metadata.get("kind") != "directory":
        return {
            "root": metadata,
            "status": "root_unavailable",
        }
    if resolved_path(root) != str(root):
        return {
            "root": metadata,
            "status": "root_alias_or_symlink",
            "realpath": resolved_path(root),
        }
    files: list[Path] = []
    symlinks: list[str] = []
    others: list[str] = []
    try:
        for current_root, directory_names, file_names in os.walk(
            root,
            followlinks=False,
        ):
            current = Path(current_root)
            kept_directories: list[str] = []
            for name in sorted(directory_names):
                candidate = current / name
                if candidate.is_symlink():
                    symlinks.append(str(candidate.relative_to(root)))
                else:
                    kept_directories.append(name)
            directory_names[:] = kept_directories
            for name in sorted(file_names):
                candidate = current / name
                relative = candidate.relative_to(root)
                if candidate.is_symlink():
                    symlinks.append(str(relative))
                    continue
                try:
                    mode = candidate.lstat().st_mode
                except OSError:
                    others.append(str(relative))
                    continue
                if not stat.S_ISREG(mode):
                    others.append(str(relative))
                    continue
                if is_sensitive_path(relative):
                    others.append(str(relative))
                    continue
                files.append(candidate)
    except OSError:
        return {
            "root": metadata,
            "status": "walk_failed",
        }
    digest = hashlib.sha256()
    failed: list[str] = []
    for candidate in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = candidate.relative_to(root).as_posix()
        identity = stable_file_sha256(candidate)
        sha256 = identity.get("sha256")
        if not isinstance(sha256, str):
            failed.append(relative)
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256.encode("ascii"))
        digest.update(b"\0")
    index = stable_file_sha256(root / "index.html")
    status_value = (
        "verified"
        if not symlinks
        and not others
        and not failed
        and index.get("sha256_status") == "verified"
        else "unsafe_or_incomplete"
    )
    return {
        "root": metadata,
        "realpath": resolved_path(root),
        "status": status_value,
        "file_count": len(files),
        "tree_sha256": digest.hexdigest() if not failed else None,
        "index": index,
        "symlinks": sorted(symlinks),
        "excluded_or_non_regular": sorted(others),
        "hash_failures": sorted(failed),
    }


def frontend_inventory() -> dict[str, Any]:
    nginx = parse_nginx_config(NGINX_CONFIG)
    roots = [
        Path(value)
        for value in nginx.get("roots", [])
        if isinstance(value, str) and value.startswith("/")
    ]
    systemd = run_command(
        ["systemctl", "is-active", "nginx"],
        timeout_seconds=5,
    )
    version = run_command(["nginx", "-v"], timeout_seconds=5)
    # nginx -v 写入 stderr；为避免回传任意 stderr，这里只登记可执行文件身份。
    nginx_binary = command_path("nginx")
    return {
        "port": socket_inventory(FRONTEND_PORT),
        "nginx": {
            **nginx,
            "systemd_state": (
                systemd.stdout if systemd.returncode == 0 else "not_active_or_unknown"
            ),
            "binary": (
                stable_file_sha256(Path(nginx_binary))
                if nginx_binary
                else None
            ),
            "version_command_available": version.available,
        },
        "served_roots": [
            frontend_tree_identity(root)
            for root in sorted(set(roots), key=str)
        ],
    }


def identity_candidate(path: Path) -> bool:
    name = path.name
    lowered = name.lower()
    return (
        name in IDENTITY_FILENAMES
        or lowered.endswith(ARCHIVE_SUFFIXES)
        or lowered.endswith(IDENTITY_SUFFIXES)
        or MANAGER_NAME.fullmatch(name) is not None
        or ("manifest" in lowered and lowered.endswith(".json"))
    )


def collect_identity_files(
    root: Path,
    *,
    maximum_depth: int,
) -> list[dict[str, Any]]:
    metadata = safe_lstat(root)
    if metadata.get("kind") != "directory":
        return []
    found: list[Path] = []
    try:
        for current_root, directory_names, file_names in os.walk(
            root,
            followlinks=False,
        ):
            current = Path(current_root)
            try:
                depth = len(current.relative_to(root).parts)
            except ValueError:
                continue
            if depth >= maximum_depth:
                directory_names[:] = []
            else:
                directory_names[:] = [
                    name
                    for name in directory_names
                    if not (current / name).is_symlink()
                ]
            for name in file_names:
                candidate = current / name
                if (
                    not candidate.is_symlink()
                    and not is_sensitive_path(candidate.relative_to(root))
                    and identity_candidate(candidate)
                ):
                    found.append(candidate)
    except OSError:
        return []
    return [
        stable_file_sha256(path)
        for path in sorted(set(found), key=str)
    ]


def release_evidence_inventory(release_id: str | None) -> dict[str, Any]:
    state_directory = RUNTIME_STATE / release_id if release_id else None
    return {
        "state_directory": (
            safe_lstat(state_directory) if state_directory else None
        ),
        "state_identity_files": (
            collect_identity_files(state_directory, maximum_depth=3)
            if state_directory
            else []
        ),
        "project_manager_scripts": [
            stable_file_sha256(path) for path in KNOWN_MANAGER_SCRIPTS
        ],
    }


def runtime_binary(path: Path, version_arguments: Sequence[str]) -> dict[str, Any]:
    realpath = resolved_path(path)
    identity_path = Path(realpath) if realpath else path
    identity = stable_file_sha256(identity_path)
    version = (
        run_command([str(identity_path), *version_arguments], timeout_seconds=5)
        if identity.get("sha256_status") == "verified"
        else CommandResult(False, None, "")
    )
    return {
        "configured_path": str(path),
        "realpath": realpath,
        "identity": identity,
        "version": (
            version.stdout.splitlines()[0]
            if version.returncode == 0 and version.stdout
            else None
        ),
    }


def font_inventory() -> list[dict[str, Any]]:
    candidates: set[Path] = set()
    for root in KNOWN_FONT_ROOTS:
        try:
            if root.is_symlink() or not root.is_dir():
                continue
            for entry in root.iterdir():
                if (
                    not entry.is_symlink()
                    and entry.is_file()
                    and entry.suffix.lower() in {".otf", ".ttf", ".ttc"}
                ):
                    candidates.add(entry)
        except OSError:
            continue
    return [
        stable_file_sha256(path)
        for path in sorted(candidates, key=str)
    ]


def dependency_inventory(
    backend_processes: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    python_candidates = set(KNOWN_PYTHON_CANDIDATES)
    for process in backend_processes:
        executable = process.get("executable")
        if isinstance(executable, str):
            python_candidates.add(Path(executable))
        cwd = process.get("cwd")
        if isinstance(cwd, str):
            python_candidates.add(Path(cwd) / ".venv/bin/python")
    return {
        "node": [
            runtime_binary(path, ["--version"])
            for path in dict.fromkeys(KNOWN_NODE_CANDIDATES)
        ],
        "python": [
            runtime_binary(path, ["--version"])
            for path in sorted(python_candidates, key=str)
        ],
        "fonts": font_inventory(),
        "runtime_config_files_read": False,
        "authentication_files_read": False,
    }


def host_inventory() -> dict[str, Any]:
    hostname = run_command(["hostname"], timeout_seconds=5)
    uname = run_command(["uname", "-srmo"], timeout_seconds=5)
    return {
        "hostname": hostname.stdout if hostname.returncode == 0 else None,
        "kernel": uname.stdout if uname.returncode == 0 else None,
        "effective_uid": os.geteuid(),
        "collected_at_utc": iso_utc_now(),
    }


def collect_inventory() -> dict[str, Any]:
    backend_socket = socket_inventory(BACKEND_PORT)
    backend_processes = [
        process_inventory(pid)
        for pid in backend_socket.get("pids", [])
        if isinstance(pid, int)
    ]
    release = release_inventory(backend_processes)
    release_id = (
        release.get("release_id")
        if isinstance(release.get("release_id"), str)
        else None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "collection_mode": "local_read_only_stdout_only",
        "host": host_inventory(),
        "boundaries": {
            "remote_execution_performed": False,
            "network_requests_performed": False,
            "filesystem_mutations_performed": False,
            "git_refresh_performed": False,
            "process_environment_read": False,
            "runtime_config_content_read": False,
            "authentication_file_content_read": False,
            "database_accessed": False,
        },
        "backend": {
            "expected_port": BACKEND_PORT,
            "socket": backend_socket,
            "processes": backend_processes,
            "release": release,
        },
        "reserved_runtime": {
            "sidecar": {
                "expected_port": SIDECAR_PORT,
                "socket": socket_inventory(SIDECAR_PORT),
            },
            "canary": {
                "expected_port": CANARY_PORT,
                "socket": socket_inventory(CANARY_PORT),
            },
            "fixed_screen_sessions": fixed_screen_inventory(),
        },
        "frontend": frontend_inventory(),
        "release_evidence": release_evidence_inventory(release_id),
        "runtime_dependencies": dependency_inventory(backend_processes),
        "server_git": git_inventory(),
    }


def canonical_inventory_bytes(inventory: dict[str, Any]) -> bytes:
    return json.dumps(
        inventory,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def inventory_envelope(inventory: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(canonical_inventory_bytes(inventory)).hexdigest()
    return {
        "schema_version": f"{SCHEMA_VERSION}_envelope",
        "hash_contract": HASH_CONTRACT,
        "inventory_sha256": digest,
        "inventory": inventory,
    }


def main() -> int:
    if len(sys.argv) != 1:
        inventory = {
            "schema_version": SCHEMA_VERSION,
            "collection_mode": "local_read_only_stdout_only",
            "collection_error": "本脚本不接受路径、URL 或远程目标参数",
            "host": host_inventory(),
        }
        print(
            json.dumps(
                inventory_envelope(inventory),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
        return 2
    try:
        inventory = collect_inventory()
    except Exception as error:  # noqa: BLE001 - 仍需返回可校验失败 JSON
        inventory = {
            "schema_version": SCHEMA_VERSION,
            "collection_mode": "local_read_only_stdout_only",
            "collection_error": type(error).__name__,
            "host": host_inventory(),
        }
    print(
        json.dumps(
            inventory_envelope(inventory),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0 if "collection_error" not in inventory else 1


if __name__ == "__main__":
    raise SystemExit(main())
