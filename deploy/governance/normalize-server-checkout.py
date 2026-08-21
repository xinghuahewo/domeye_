#!/usr/bin/env python3

"""将 Domeye-Core 服务器源码检出可恢复地归一到指定 GitHub main。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
from typing import Any


EXPECTED_HOST = "buptserver16"
EXPECTED_SOURCE = Path("/home/bgpdata/Domeye-Core")
EXPECTED_ARTIFACT_ROOT = Path("/home/bgpdata/Domeye-Core-artifacts")
EXPECTED_REMOTE = "git@github.com:xinghuahewo/domeye_.git"
EXPECTED_BRANCH = "main"
SSH_PROBE_TIMEOUT_SECONDS = 20
GIT_SSH_COMMAND = "ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes"
SCHEMA = "domeye.server-checkout-normalization/v2"
REPOSITORY_ACCESS_POLICY = "official_ssh_first_v1"
PROTECTED_ROOTS = (
    Path("/home/bgpdata/Domeye"),
    Path("/home/bgpdata/data"),
    Path("/home/bgpdata/AS402425"),
    Path("/home/bgpdata/zhongxin"),
    Path("/home/bgpdata/Domeye-Info-Migration"),
)
ACTIVE_LINKS = (
    Path("/home/bgpdata/Domeye-Core-runtime/current"),
    Path("/home/bgpdata/Domeye-Core-runtime/country-outage-agent/current"),
    Path("/home/bgpdata/Domeye-Core-runtime/country-outage-interactive-agent/current"),
    Path("/home/bgpdata/Domeye-Core-runtime/country-outage-p1-chat/current"),
)


class NormalizationError(RuntimeError):
    """表示前置条件不满足或归一失败。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run(
    arguments: list[str],
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if environment is None and arguments and arguments[0] == "git":
        environment = isolated_git_environment()
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )


def isolated_git_environment(
    ignore_repository_config: bool = False,
) -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_SSH_COMMAND": GIT_SSH_COMMAND,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if ignore_repository_config:
        environment["GIT_CONFIG"] = os.devnull
    return environment


def trusted_non_repository_cwd() -> Path:
    root = Path("/")
    if (root / ".git").exists():
        raise NormalizationError("受信 Git 工作目录意外包含仓库元数据")
    return root


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def canonical(path: Path) -> Path:
    return path.resolve(strict=False)


def safe_operation_id(value: str) -> str:
    if not value or any(char not in "-._0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" for char in value):
        raise NormalizationError("operation-id 只能包含字母、数字、点、下划线和连字符")
    if value in {".", ".."} or len(value) > 160:
        raise NormalizationError("operation-id 不合法")
    return value


def validate_scope(host: str, source: Path, artifact_root: Path, remote: str) -> None:
    if host != EXPECTED_HOST:
        raise NormalizationError(f"主机不匹配：{host} != {EXPECTED_HOST}")
    if canonical(source) != canonical(EXPECTED_SOURCE):
        raise NormalizationError(f"只允许归一指定源码检出：{EXPECTED_SOURCE}")
    if canonical(artifact_root) != canonical(EXPECTED_ARTIFACT_ROOT):
        raise NormalizationError(f"只允许写入指定制品根：{EXPECTED_ARTIFACT_ROOT}")
    if remote != EXPECTED_REMOTE:
        raise NormalizationError("只允许固定的官方 GitHub SSH remote")
    for protected in PROTECTED_ROOTS:
        protected = canonical(protected)
        if is_within(canonical(source), protected) or is_within(canonical(artifact_root), protected):
            raise NormalizationError(f"操作路径与保护根重叠：{protected}")


def git_value(root: Path, *arguments: str) -> str:
    completed = run(["git", *arguments], cwd=root)
    if completed.returncode != 0:
        raise NormalizationError(completed.stderr.strip() or f"git {' '.join(arguments)} 失败")
    return completed.stdout.strip()


def git_lines_allow_absent(
    root: Path, *arguments: str, environment: dict[str, str] | None = None
) -> list[str]:
    completed = run(["git", *arguments], cwd=root, environment=environment)
    if completed.returncode not in {0, 1}:
        raise NormalizationError(
            completed.stderr.strip() or f"git {' '.join(arguments)} 失败"
        )
    return [line for line in completed.stdout.splitlines() if line]


def validate_official_origin(source: Path) -> str:
    remote_names = git_lines_allow_absent(source, "remote")
    raw_fetch_urls = git_lines_allow_absent(
        source, "config", "--local", "--get-all", "remote.origin.url"
    )
    raw_push_urls = git_lines_allow_absent(
        source, "config", "--local", "--get-all", "remote.origin.pushurl"
    )
    isolated = isolated_git_environment()
    effective_fetch_urls = git_lines_allow_absent(
        source, "remote", "get-url", "--all", "origin", environment=isolated
    )
    effective_push_urls = git_lines_allow_absent(
        source,
        "remote",
        "get-url",
        "--push",
        "--all",
        "origin",
        environment=isolated,
    )
    if (
        remote_names != ["origin"]
        or raw_fetch_urls != [EXPECTED_REMOTE]
        or raw_push_urls
        or effective_fetch_urls != [EXPECTED_REMOTE]
        or effective_push_urls != [EXPECTED_REMOTE]
    ):
        raise NormalizationError(
            "checkout remote 不是唯一且不可改写的官方 GitHub SSH origin"
        )
    return EXPECTED_REMOTE


def git_snapshot(source: Path) -> dict[str, Any]:
    if git_value(source, "rev-parse", "--is-inside-work-tree") != "true":
        raise NormalizationError("source 不是 Git worktree")
    status = git_value(source, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries = [entry for entry in status.split("\0") if entry]
    remotes = [line for line in git_value(source, "remote").splitlines() if line]
    return {
        "path": str(source),
        "head": git_value(source, "rev-parse", "HEAD"),
        "branch": git_value(source, "branch", "--show-current"),
        "remoteNames": remotes,
        "changeCount": len(entries),
        "modifiedCount": sum(entry[:2] != "??" for entry in entries),
        "untrackedCount": sum(entry[:2] == "??" for entry in entries),
    }


def active_link_snapshot() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for link in ACTIVE_LINKS:
        if not link.is_symlink():
            raise NormalizationError(f"活动指针不是软链接：{link}")
        resolved = link.resolve(strict=False)
        if not resolved.exists():
            raise NormalizationError(f"活动指针目标不存在：{link}")
        if is_within(resolved, EXPECTED_SOURCE):
            raise NormalizationError(f"活动指针错误引用源码 checkout：{link}")
        result.append(
            {
                "path": str(link),
                "rawTarget": os.readlink(link),
                "resolvedTarget": str(resolved),
            }
        )
    return result


def source_process_references(source: Path, process_root: Path = Path("/proc")) -> list[dict[str, Any]]:
    """只读取 cwd、exe 与 fd 的路径，不读取命令行、环境或文件内容。"""
    references: list[dict[str, Any]] = []
    source = canonical(source)
    if not process_root.is_dir():
        return references
    for process in sorted((item for item in process_root.iterdir() if item.name.isdigit()), key=lambda item: int(item.name)):
        matches: dict[str, str] = {}
        for field in ("cwd", "exe"):
            try:
                target = (process / field).resolve(strict=True)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if is_within(target, source):
                matches[field] = str(target)
        descriptor_directory = process / "fd"
        if descriptor_directory.is_dir():
            for descriptor in descriptor_directory.iterdir():
                try:
                    target = descriptor.resolve(strict=True)
                except (FileNotFoundError, PermissionError, OSError):
                    continue
                if is_within(target, source):
                    matches[f"fd:{descriptor.name}"] = str(target)
        if not matches:
            continue
        try:
            command = (process / "comm").read_text(encoding="utf-8").strip()
        except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError):
            command = "unknown"
        references.append({"pid": int(process.name), "command": command, "references": matches})
    return references


def source_mount_references(source: Path, mountinfo: Path = Path("/proc/self/mountinfo")) -> list[str]:
    if not mountinfo.is_file():
        return []
    source = canonical(source)
    matches: list[str] = []
    for line in mountinfo.read_text(encoding="utf-8").splitlines():
        pieces = line.split(" - ", 1)[0].split()
        if len(pieces) < 5:
            continue
        mount_point = Path(pieces[4].replace("\\040", " ").replace("\\011", "\t"))
        if is_within(canonical(mount_point), source):
            matches.append(str(mount_point))
    return matches


def source_lock_files(source: Path) -> list[str]:
    candidates = (
        source / ".git/index.lock",
        source / ".git/HEAD.lock",
        source / ".git/config.lock",
        source / ".git/shallow.lock",
    )
    return [str(path) for path in candidates if path.exists()]


def same_filesystem(source: Path, artifact_root: Path) -> bool:
    return source.stat().st_dev == artifact_root.stat().st_dev


def tree_summary(source: Path) -> dict[str, int]:
    files = 0
    directories = 0
    symlinks = 0
    bytes_total = 0
    for root, directory_names, file_names in os.walk(source, followlinks=False):
        directories += 1
        root_path = Path(root)
        for name in directory_names:
            path = root_path / name
            if path.is_symlink():
                symlinks += 1
        for name in file_names:
            path = root_path / name
            if path.is_symlink():
                symlinks += 1
            else:
                files += 1
                try:
                    bytes_total += path.stat().st_size
                except FileNotFoundError:
                    pass
    return {"fileCount": files, "directoryCount": directories, "symlinkCount": symlinks, "logicalBytes": bytes_total}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, path)


def preflight(source: Path, artifact_root: Path, expected_source_head: str) -> dict[str, Any]:
    snapshot = git_snapshot(source)
    if snapshot["branch"] != EXPECTED_BRANCH:
        raise NormalizationError(f"source 分支不是 {EXPECTED_BRANCH}：{snapshot['branch']}")
    if snapshot["head"] != expected_source_head:
        raise NormalizationError(f"source HEAD 漂移：{snapshot['head']} != {expected_source_head}")
    if snapshot["remoteNames"]:
        raise NormalizationError("旧 source checkout 存在 remote，拒绝改变其凭证或 remote 状态")
    process_references = source_process_references(source)
    if process_references:
        raise NormalizationError("source checkout 仍被进程引用：" + json.dumps(process_references, ensure_ascii=False))
    mount_references = source_mount_references(source)
    if mount_references:
        raise NormalizationError("source checkout 存在挂载：" + ", ".join(mount_references))
    locks = source_lock_files(source)
    if locks:
        raise NormalizationError("source checkout 存在 Git 锁：" + ", ".join(locks))
    if not same_filesystem(source, artifact_root):
        raise NormalizationError("source 与 artifact root 不在同一文件系统，拒绝非原子隔离")
    return {
        "checkedAt": utc_now(),
        "sourceCheckout": snapshot,
        "treeSummary": tree_summary(source),
        "activeLinks": active_link_snapshot(),
        "processReferenceCount": 0,
        "mountReferenceCount": 0,
        "lockFileCount": 0,
    }


def validate_bundle_path(operation_id: str, artifact_root: Path, bundle_path: Path) -> Path:
    expected = artifact_root / "incoming" / f"{operation_id}.bundle"
    if canonical(bundle_path) != canonical(expected) or bundle_path.is_symlink() or not bundle_path.is_file():
        raise NormalizationError(f"bundle 必须是受管输入文件：{expected}")
    return bundle_path


def ssh_failure_reason(stderr: str) -> str:
    message = stderr.lower()
    if "permission denied" in message or "publickey" in message:
        return "authentication_failed"
    if "host key verification failed" in message or "remote host identification has changed" in message:
        return "host_key_verification_failed"
    if "could not resolve hostname" in message or "name or service not known" in message:
        return "dns_or_network_unavailable"
    if "timed out" in message or "timeout" in message:
        return "timeout"
    if "connection refused" in message or "network is unreachable" in message or "no route to host" in message:
        return "dns_or_network_unavailable"
    return "transport_failed"


def probe_official_ssh_main(
    expected_main: str, timeout_seconds: int = SSH_PROBE_TIMEOUT_SECONDS
) -> dict[str, str | None]:
    reference = f"refs/heads/{EXPECTED_BRANCH}"
    try:
        completed = subprocess.run(
            ["git", "ls-remote", "--exit-code", EXPECTED_REMOTE, reference],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            env=isolated_git_environment(ignore_repository_config=True),
            cwd=trusted_non_repository_cwd(),
        )
    except subprocess.TimeoutExpired as error:
        raise NormalizationError("官方 SSH 预检失败：timeout") from error
    if completed.returncode != 0:
        raise NormalizationError(
            f"官方 SSH 预检失败：{ssh_failure_reason(completed.stderr)}"
        )
    heads = [
        fields[0]
        for line in completed.stdout.splitlines()
        if len(fields := line.split()) == 2 and fields[1] == reference
    ]
    if heads != [expected_main]:
        raise NormalizationError("官方 SSH 预检失败：main_identity_mismatch")
    return {
        "status": "passed",
        "checkedAt": utc_now(),
        "ref": reference,
        "expectedCommit": expected_main,
        "observedCommit": expected_main,
        "reasonCode": None,
    }


def clone_clean_checkout(
    source: Path, expected_main: str, bundle_path: Path | None = None
) -> dict[str, Any]:
    clone_source = str(bundle_path) if bundle_path is not None else EXPECTED_REMOTE
    clone_environment = isolated_git_environment(ignore_repository_config=True)
    completed = subprocess.run(
        ["git", "-c", "credential.helper=", "clone", "--branch", EXPECTED_BRANCH, "--single-branch", "--origin", "origin", clone_source, str(source)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=clone_environment,
        cwd=trusted_non_repository_cwd(),
    )
    if completed.returncode != 0:
        if bundle_path is not None:
            raise NormalizationError("本机 Git bundle clone 失败")
        raise NormalizationError(
            f"官方 SSH clone 失败：{ssh_failure_reason(completed.stderr)}"
        )
    if bundle_path is not None:
        remote_update = run(["git", "remote", "set-url", "origin", EXPECTED_REMOTE], cwd=source)
        if remote_update.returncode != 0:
            raise NormalizationError(remote_update.stderr.strip() or "无法固定 checkout 的 origin")
    head = git_value(source, "rev-parse", "HEAD")
    origin_main = git_value(source, "rev-parse", "origin/main")
    branch = git_value(source, "branch", "--show-current")
    remote = validate_official_origin(source)
    status = git_value(source, "status", "--porcelain=v1", "-z")
    if head != expected_main or origin_main != expected_main or branch != EXPECTED_BRANCH or remote != EXPECTED_REMOTE or status:
        raise NormalizationError("新 checkout 身份或干净状态不满足冻结目标")
    return {"path": str(source), "head": head, "originMain": origin_main, "branch": branch, "remote": remote, "clean": True}


def normalize(
    operation_id: str,
    expected_source_head: str,
    expected_main: str,
    bundle_path: Path | None = None,
) -> dict[str, Any]:
    source = EXPECTED_SOURCE
    artifact_root = EXPECTED_ARTIFACT_ROOT
    validate_scope(socket.gethostname(), source, artifact_root, EXPECTED_REMOTE)
    safe_operation_id(operation_id)
    if len(expected_source_head) != 40 or len(expected_main) != 40:
        raise NormalizationError("expected SHA 必须是完整 40 位提交 SHA")
    if bundle_path is not None:
        bundle_path = validate_bundle_path(operation_id, artifact_root, bundle_path)
    before = preflight(source, artifact_root, expected_source_head)
    authority_probe = (
        probe_official_ssh_main(expected_main) if bundle_path is None else None
    )
    operation_root = artifact_root / "quarantine" / "checkouts" / operation_id
    original = operation_root / "original-Domeye-Core"
    archive = operation_root / "original-Domeye-Core.tar.gz"
    receipt = operation_root / "normalization-receipt.json"
    if operation_root.exists():
        raise NormalizationError(f"operation 目录已存在：{operation_root}")
    operation_root.mkdir(parents=True, mode=0o700)
    source_quarantined = False
    try:
        archive_result = run([
            "tar", "-C", str(source.parent), "--acls", "--xattrs", "--numeric-owner", "-czf", str(archive), source.name,
        ])
        if archive_result.returncode != 0 or not archive.is_file():
            raise NormalizationError(archive_result.stderr.strip() or "source archive 创建失败")
        archive_sha256 = sha256_file(archive)
        os.rename(source, original)
        source_quarantined = True
        bundle_sha256 = sha256_file(bundle_path) if bundle_path is not None else None
        after_checkout = clone_clean_checkout(source, expected_main, bundle_path)
        after_links = active_link_snapshot()
        if before["activeLinks"] != after_links:
            raise NormalizationError("活动 release 指针在归一期间漂移；保留原 checkout 与新 checkout 供人工处置")
        after_references = source_process_references(source)
        if after_references:
            raise NormalizationError("新 checkout 被进程意外引用：" + json.dumps(after_references, ensure_ascii=False))
        retained_bundle = None
        if bundle_path is not None:
            retained_bundle = operation_root / "source-main.bundle"
            os.rename(bundle_path, retained_bundle)
        acquisition = "local_immutable_git_bundle" if bundle_path is not None else "official_ssh"
        result = {
            "schemaVersion": SCHEMA,
            "operationId": operation_id,
            "completedAt": utc_now(),
            "host": socket.gethostname(),
            "sourceBefore": before,
            "archive": {"path": str(archive), "sha256": archive_sha256, "bytes": archive.stat().st_size},
            "inputBundle": (
                {"path": str(retained_bundle), "sha256": bundle_sha256, "bytes": retained_bundle.stat().st_size}
                if retained_bundle is not None
                else None
            ),
            "quarantine": {"path": str(original), "state": "retained"},
            "newCheckout": after_checkout,
            "repositoryAccess": {
                "policy": REPOSITORY_ACCESS_POLICY,
                "origin": EXPECTED_REMOTE,
                "acquisition": acquisition,
                "networkGitHubAccessPerformed": bundle_path is None,
                "sshPreflight": authority_probe
                if authority_probe is not None
                else {
                    "status": "not_required_bundle_input",
                    "checkedAt": None,
                    "ref": f"refs/heads/{EXPECTED_BRANCH}",
                    "expectedCommit": expected_main,
                    "observedCommit": None,
                    "reasonCode": None,
                },
                "httpsFallback": {"performed": False, "receiptSha256": None},
            },
            "activeLinksBefore": before["activeLinks"],
            "activeLinksAfter": after_links,
            "oldDomeyeTouched": False,
            "serverGitHubCredentialsChanged": False,
            "checkoutAcquisition": acquisition,
            "productionSwitchPerformed": False,
        }
        write_json(receipt, result)
        result["receiptPath"] = str(receipt)
        return result
    except Exception as error:
        if source_quarantined:
            failed_checkout = operation_root / "failed-new-Domeye-Core"
            try:
                if source.exists() and not failed_checkout.exists():
                    os.rename(source, failed_checkout)
                if original.exists() and not source.exists():
                    os.rename(original, source)
            except OSError as rollback_error:
                raise NormalizationError(
                    f"归一失败且自动恢复原路径失败：{rollback_error}"
                ) from error
        if operation_root.exists() and not receipt.exists():
            write_json(receipt, {"schemaVersion": SCHEMA, "operationId": operation_id, "failedAt": utc_now(), "state": "failed_or_rolled_back"})
        raise


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--expected-source-head", required=True)
    parser.add_argument("--expected-main", required=True)
    parser.add_argument("--bundle-path", type=Path, help="受管输入 Git bundle；省略时使用官方 SSH")
    parser.add_argument("--apply", action="store_true", help="执行归档、隔离、clone 与回执写入；默认只读预检")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        validate_scope(socket.gethostname(), EXPECTED_SOURCE, EXPECTED_ARTIFACT_ROOT, EXPECTED_REMOTE)
        safe_operation_id(arguments.operation_id)
        if arguments.bundle_path is not None:
            validate_bundle_path(arguments.operation_id, EXPECTED_ARTIFACT_ROOT, arguments.bundle_path)
        if arguments.apply:
            result = normalize(
                arguments.operation_id,
                arguments.expected_source_head,
                arguments.expected_main,
                arguments.bundle_path,
            )
        else:
            result = {
                "schemaVersion": "domeye.server-checkout-normalization-preflight/v2",
                "operationId": arguments.operation_id,
                "mode": "read_only",
                "preflight": preflight(EXPECTED_SOURCE, EXPECTED_ARTIFACT_ROOT, arguments.expected_source_head),
                "expectedMain": arguments.expected_main,
                "bundlePath": str(arguments.bundle_path) if arguments.bundle_path is not None else None,
            }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (NormalizationError, OSError) as error:
        print(f"服务器 checkout 归一失败：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
