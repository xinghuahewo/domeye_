#!/usr/bin/env python3

"""以可恢复的本机 Git bundle 刷新 Domeye-Core 服务器源码 checkout。"""

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
EXPECTED_REMOTE = "https://github.com/xinghuahewo/domeye_.git"
EXPECTED_BRANCH = "main"
PROTECTED_ROOTS = (
    Path("/home/bgpdata/Domeye"), Path("/home/bgpdata/data"), Path("/home/bgpdata/AS402425"),
    Path("/home/bgpdata/zhongxin"), Path("/home/bgpdata/Domeye-Info-Migration"),
)
ACTIVE_LINKS = (
    Path("/home/bgpdata/Domeye-Core-runtime/current"),
    Path("/home/bgpdata/Domeye-Core-runtime/country-outage-agent/current"),
    Path("/home/bgpdata/Domeye-Core-runtime/country-outage-interactive-agent/current"),
    Path("/home/bgpdata/Domeye-Core-runtime/country-outage-p1-chat/current"),
)
BOOTSTRAP_ABSENT_ACTIVE_LINK = Path(
    "/home/bgpdata/Domeye-Core-runtime/country-outage-interactive-agent/current"
)
SCHEMA = "domeye.server-checkout-refresh/v1"


class RefreshError(RuntimeError):
    """表示刷新前置条件或恢复动作失败。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(path: Path) -> Path:
    return path.resolve(strict=False)


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def run(arguments: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, cwd=cwd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def git_value(source: Path, *arguments: str) -> str:
    result = run(["git", *arguments], source)
    if result.returncode:
        raise RefreshError(result.stderr.strip() or f"git {' '.join(arguments)} 失败")
    return result.stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_operation_id(value: str) -> str:
    if not value or len(value) > 160 or value in {".", ".."} or any(char not in "-._0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" for char in value):
        raise RefreshError("operation-id 不合法")
    return value


def validate_scope(host: str, source: Path, artifact_root: Path) -> None:
    if host != EXPECTED_HOST:
        raise RefreshError(f"主机不匹配：{host} != {EXPECTED_HOST}")
    if canonical(source) != canonical(EXPECTED_SOURCE):
        raise RefreshError(f"只允许刷新指定源码 checkout：{EXPECTED_SOURCE}")
    if canonical(artifact_root) != canonical(EXPECTED_ARTIFACT_ROOT):
        raise RefreshError(f"只允许使用指定制品根：{EXPECTED_ARTIFACT_ROOT}")
    for protected in PROTECTED_ROOTS:
        if is_within(canonical(source), canonical(protected)) or is_within(canonical(artifact_root), canonical(protected)):
            raise RefreshError(f"操作路径与保护根重叠：{protected}")


def source_references(source: Path, process_root: Path = Path("/proc")) -> list[dict[str, Any]]:
    source = canonical(source)
    references: list[dict[str, Any]] = []
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
        try:
            descriptors = list((process / "fd").iterdir())
        except (FileNotFoundError, PermissionError, OSError):
            descriptors = []
        for descriptor in descriptors:
            try:
                target = descriptor.resolve(strict=True)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if is_within(target, source):
                matches[f"fd:{descriptor.name}"] = str(target)
        if matches:
            references.append({"pid": int(process.name), "references": matches})
    return references


def mount_references(source: Path, mountinfo: Path = Path("/proc/self/mountinfo")) -> list[str]:
    if not mountinfo.is_file():
        raise RefreshError("无法读取 mountinfo，拒绝刷新 checkout")
    source = canonical(source)
    matches: list[str] = []
    for line in mountinfo.read_text(encoding="utf-8").splitlines():
        fields = line.split(" - ", 1)[0].split()
        if len(fields) < 5:
            raise RefreshError("mountinfo 格式异常，拒绝刷新 checkout")
        mount = canonical(Path(fields[4].replace("\\040", " ").replace("\\011", "\t")))
        if is_within(mount, source):
            matches.append(str(mount))
    return matches


def git_locks(source: Path) -> list[str]:
    return [str(item) for item in (source / ".git/index.lock", source / ".git/HEAD.lock", source / ".git/config.lock", source / ".git/shallow.lock") if item.exists()]


def active_links() -> list[dict[str, str]]:
    snapshot: list[dict[str, str]] = []
    for link in ACTIVE_LINKS:
        if not os.path.lexists(link):
            if link != BOOTSTRAP_ABSENT_ACTIVE_LINK:
                raise RefreshError(f"活动指针不存在：{link}")
            snapshot.append({"path": str(link), "state": "absent"})
            continue
        if not link.is_symlink():
            raise RefreshError(f"活动指针不是软链接：{link}")
        target = canonical(link)
        if not target.exists():
            raise RefreshError(f"活动指针目标不存在：{link}")
        if is_within(target, canonical(EXPECTED_SOURCE)):
            raise RefreshError(f"活动指针错误引用源码 checkout：{link}")
        snapshot.append({"path": str(link), "state": "symlink", "rawTarget": os.readlink(link), "resolvedTarget": str(target)})
    return snapshot


def source_snapshot(source: Path) -> dict[str, Any]:
    if git_value(source, "rev-parse", "--is-inside-work-tree") != "true":
        raise RefreshError("source 不是 Git worktree")
    status = git_value(source, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if status:
        raise RefreshError("source checkout 非干净，拒绝刷新")
    remote_names = [name for name in git_value(source, "remote").splitlines() if name]
    if remote_names != ["origin"] or git_value(source, "remote", "get-url", "origin") != EXPECTED_REMOTE:
        raise RefreshError("source remote 不是唯一的无凭证公开 GitHub origin")
    branch = git_value(source, "branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        raise RefreshError(f"source 分支不是 {EXPECTED_BRANCH}：{branch}")
    return {"head": git_value(source, "rev-parse", "HEAD"), "originMain": git_value(source, "rev-parse", "origin/main"), "branch": branch, "clean": True}


def bundle_head(bundle: Path) -> str:
    listed = run(["git", "bundle", "list-heads", str(bundle)])
    if listed.returncode:
        raise RefreshError(listed.stderr.strip() or "bundle 无法列出 refs")
    for line in listed.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] in {"main", "refs/heads/main"}:
            return fields[0]
    raise RefreshError("bundle 未包含 main")


def validate_bundle(bundle: Path, operation_id: str, artifact_root: Path, expected_sha256: str, expected_main: str) -> dict[str, Any]:
    expected_path = artifact_root / "incoming" / f"{operation_id}.bundle"
    if canonical(bundle) != canonical(expected_path) or bundle.is_symlink() or not bundle.is_file():
        raise RefreshError(f"bundle 必须是受管输入文件：{expected_path}")
    actual_sha256 = sha256_file(bundle)
    if actual_sha256 != expected_sha256:
        raise RefreshError("bundle SHA-256 与冻结值不一致")
    head = bundle_head(bundle)
    if head != expected_main:
        raise RefreshError(f"bundle main 漂移：{head} != {expected_main}")
    return {"path": str(bundle), "sha256": actual_sha256, "main": head, "bytes": bundle.stat().st_size}


def preflight(operation_id: str, expected_current: str, expected_main: str, bundle: Path, expected_bundle_sha256: str) -> dict[str, Any]:
    source, artifact_root = EXPECTED_SOURCE, EXPECTED_ARTIFACT_ROOT
    validate_scope(socket.gethostname(), source, artifact_root)
    safe_operation_id(operation_id)
    if any(len(value) != 40 for value in (expected_current, expected_main)):
        raise RefreshError("expected SHA 必须为完整 40 位提交")
    snapshot = source_snapshot(source)
    if snapshot["head"] != expected_current or snapshot["originMain"] != expected_current:
        raise RefreshError("source 当前身份不等于冻结的刷新前提交")
    references = source_references(source)
    if references:
        raise RefreshError("source checkout 仍被进程引用")
    mounts = mount_references(source)
    if mounts:
        raise RefreshError("source checkout 存在子挂载")
    locks = git_locks(source)
    if locks:
        raise RefreshError("source checkout 存在 Git 锁")
    return {"checkedAt": utc_now(), "source": snapshot, "activeLinks": active_links(), "bundle": validate_bundle(bundle, operation_id, artifact_root, expected_bundle_sha256, expected_main)}


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, path)


def refresh(operation_id: str, expected_current: str, expected_main: str, bundle: Path, expected_bundle_sha256: str) -> dict[str, Any]:
    before = preflight(operation_id, expected_current, expected_main, bundle, expected_bundle_sha256)
    source, artifact_root = EXPECTED_SOURCE, EXPECTED_ARTIFACT_ROOT
    operation_root = artifact_root / "quarantine" / "checkout-refresh" / operation_id
    if operation_root.exists():
        raise RefreshError(f"operation 已存在：{operation_root}")
    operation_root.mkdir(parents=True, mode=0o700)
    receipt = operation_root / "refresh-receipt.json"
    retained_bundle = operation_root / "source-main.bundle"
    rollback_ref = f"refs/domeye-governance/checkout-refresh/{operation_id}"
    changed = False
    try:
        os.rename(bundle, retained_bundle)
        git_value(source, "update-ref", rollback_ref, before["source"]["head"])
        fetched = run(["git", "fetch", "--no-tags", str(retained_bundle), "main"], source)
        if fetched.returncode or git_value(source, "rev-parse", "FETCH_HEAD") != expected_main:
            raise RefreshError(fetched.stderr.strip() or "bundle fetch 身份不满足")
        git_value(source, "update-ref", "refs/remotes/origin/main", expected_main, before["source"]["originMain"])
        git_value(source, "reset", "--hard", expected_main)
        changed = True
        after = source_snapshot(source)
        after_links = active_links()
        if after["head"] != expected_main or after["originMain"] != expected_main or after_links != before["activeLinks"]:
            raise RefreshError("刷新后 checkout 或活动指针身份不一致")
        if source_references(source) or mount_references(source) or git_locks(source):
            raise RefreshError("刷新后 source checkout 出现运行引用、挂载或锁")
        result = {"schemaVersion": SCHEMA, "operationId": operation_id, "completedAt": utc_now(), "sourceBefore": before["source"], "sourceAfter": after, "inputBundle": {**before["bundle"], "path": str(retained_bundle)}, "rollbackRef": {"ref": rollback_ref, "head": before["source"]["head"]}, "activeLinksBefore": before["activeLinks"], "activeLinksAfter": after_links, "oldDomeyeTouched": False, "serverGitHubCredentialsChanged": False, "productionSwitchPerformed": False, "rollback": {"priorHead": before["source"]["head"], "state": "available"}}
        write_json(receipt, result)
        result["receiptPath"] = str(receipt)
        return result
    except Exception as error:
        if changed:
            restored = run(["git", "reset", "--hard", before["source"]["head"]], source)
            restored_ref = run(["git", "update-ref", "refs/remotes/origin/main", before["source"]["originMain"]], source)
            if restored.returncode or restored_ref.returncode:
                raise RefreshError("刷新失败且自动恢复 checkout 身份失败") from error
        if operation_root.exists() and not receipt.exists():
            write_json(receipt, {"schemaVersion": SCHEMA, "operationId": operation_id, "failedAt": utc_now(), "state": "failed_or_rolled_back"})
        raise


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--expected-current", required=True)
    parser.add_argument("--expected-main", required=True)
    parser.add_argument("--bundle-path", type=Path, required=True)
    parser.add_argument("--expected-bundle-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        result = refresh(arguments.operation_id, arguments.expected_current, arguments.expected_main, arguments.bundle_path, arguments.expected_bundle_sha256) if arguments.apply else {"schemaVersion": SCHEMA + "-preflight", "mode": "read_only", "preflight": preflight(arguments.operation_id, arguments.expected_current, arguments.expected_main, arguments.bundle_path, arguments.expected_bundle_sha256)}
    except (RefreshError, OSError) as error:
        print(f"checkout 刷新失败：{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
