#!/usr/bin/env python3

"""安装可回滚的 Domeye 服务器目录只读审计 systemd 定时器。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
from typing import Any


EXPECTED_HOST = "buptserver16"
EXPECTED_REPOSITORY = Path("/home/bgpdata/Domeye-Core")
GOVERNANCE_ROOT = Path("/home/bgpdata/Domeye-Core-governance")
SYSTEMD_ROOT = Path("/etc/systemd/system")
RELEASES_ROOT = GOVERNANCE_ROOT / "directory-audit" / "releases"
CURRENT_LINK = GOVERNANCE_ROOT / "directory-audit" / "current"
WRAPPER = GOVERNANCE_ROOT / "bin" / "run-server-directory-audit"
INSTALLATIONS = GOVERNANCE_ROOT / "installations"
ROLLBACKS = GOVERNANCE_ROOT / "directory-audit" / "rollbacks"
SYSTEMD_SERVICE = "domeye-server-directory-audit@.service"
TIMER_NAMES = ("domeye-server-directory-audit-daily.timer", "domeye-server-directory-audit-weekly.timer", "domeye-server-directory-audit-monthly.timer")
SOURCE_FILES = ("audit-server-layout.py", "audit-server-runtime-governance.py", "server-directory-policy.json")


class ScheduleError(RuntimeError):
    """表示 S6 定时器的前置条件、安装或读回失败。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run(arguments: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, cwd=cwd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_operation_id(value: str) -> str:
    allowed = "-._0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    if not value or value in {".", ".."} or len(value) > 160 or any(char not in allowed for char in value):
        raise ScheduleError("operation-id 不合法")
    return value


def safe_commit(value: str) -> str:
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise ScheduleError("expected-main 必须是 40 位小写提交 SHA")
    return value


def git_value(repository: Path, *arguments: str) -> str:
    result = run(["git", *arguments], repository)
    if result.returncode:
        raise ScheduleError(result.stderr.strip() or f"git {' '.join(arguments)} 失败")
    return result.stdout.strip()


def validate_source(source_dir: Path, expected_main: str) -> dict[str, Any]:
    repository = EXPECTED_REPOSITORY
    safe_commit(expected_main)
    if socket.gethostname() != EXPECTED_HOST:
        raise ScheduleError("主机不匹配")
    if os.geteuid() != 0:
        raise ScheduleError("只允许 root 安装 S6 定时器")
    if not repository.is_dir() or repository.is_symlink():
        raise ScheduleError("服务器 checkout 必须是实际目录")
    if source_dir.resolve() != (repository / "deploy/governance").resolve():
        raise ScheduleError("安装来源必须是服务器 checkout 中的版本化 governance 目录")
    if git_value(repository, "branch", "--show-current") != "main":
        raise ScheduleError("服务器 checkout 不是 main")
    head = git_value(repository, "rev-parse", "HEAD")
    origin_main = git_value(repository, "rev-parse", "origin/main")
    if head != expected_main or origin_main != expected_main:
        raise ScheduleError("服务器 checkout 身份不等于冻结 main")
    if git_value(repository, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise ScheduleError("服务器 checkout 非干净，拒绝安装")
    sources: dict[str, dict[str, Any]] = {}
    for name in SOURCE_FILES:
        path = source_dir / name
        if not path.is_file() or path.is_symlink():
            raise ScheduleError(f"缺少版本化 S6 来源：{path}")
        sources[name] = {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
    if run(["systemctl", "--version"]).returncode:
        raise ScheduleError("systemd 不可用，拒绝以其他调度器降级安装")
    return {"repository": str(repository), "head": head, "originMain": origin_main, "sources": sources}


def wrapper_text() -> str:
    return f"""#!/usr/bin/env bash
set -Eeuo pipefail
mode="${{1:-}}"
case "$mode" in
  daily) script='audit-server-layout.py' ;;
  weekly|monthly) script='audit-server-runtime-governance.py' ;;
  *) printf '未知审计模式：%s\\n' "$mode" >&2; exit 2 ;;
esac
release='{CURRENT_LINK}'
reports='{GOVERNANCE_ROOT}/directory-audit/reports/'"$mode"
install -d -m 0700 "$reports"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
temporary="$(mktemp "$reports/.${{timestamp}}.XXXXXX")"
trap 'rm -f -- "$temporary"' EXIT
/usr/bin/python3 "$release/$script" --policy "$release/server-directory-policy.json" --compact >"$temporary"
chmod 0600 "$temporary"
mv -f -- "$temporary" "$reports/${{timestamp}}.json"
trap - EXIT
"""


def service_text() -> str:
    return f"""[Unit]
Description=Domeye 服务器目录只读审计（%i）
After=network.target

[Service]
Type=oneshot
User=root
Group=root
ExecStart={WRAPPER} %i
NoNewPrivileges=true
PrivateTmp=true
"""


def timer_text(mode: str, calendar: str) -> str:
    return f"""[Unit]
Description=Domeye 服务器目录只读审计定时器（{mode}）

[Timer]
OnCalendar={calendar}
Persistent=true
RandomizedDelaySec=15m
Unit=domeye-server-directory-audit@{mode}.service

[Install]
WantedBy=timers.target
"""


def unit_payloads() -> dict[Path, str]:
    return {
        SYSTEMD_ROOT / SYSTEMD_SERVICE: service_text(),
        SYSTEMD_ROOT / TIMER_NAMES[0]: timer_text("daily", "*-*-* 03:15:00"),
        SYSTEMD_ROOT / TIMER_NAMES[1]: timer_text("weekly", "Mon *-*-* 04:15:00"),
        SYSTEMD_ROOT / TIMER_NAMES[2]: timer_text("monthly", "*-*-01 05:15:00"),
    }


def write_atomic(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def copy_atomic(source: Path, target: Path, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".new")
    shutil.copyfile(source, temporary)
    os.chmod(temporary, mode)
    os.replace(temporary, target)


def snapshot_target(path: Path, backup_root: Path) -> dict[str, Any]:
    record = {"path": str(path), "present": path.exists() or path.is_symlink()}
    if record["present"]:
        record["mode"] = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            record["kind"] = "symlink"
            record["target"] = os.readlink(path)
        elif path.is_file():
            backup = backup_root / path.name
            copy_atomic(path, backup, stat.S_IRUSR | stat.S_IWUSR)
            record.update({"kind": "file", "backup": str(backup), "sha256": sha256_file(path)})
        else:
            raise ScheduleError(f"既有目标不是普通文件或软链接：{path}")
    return record


def restore_target(record: dict[str, Any]) -> None:
    path = Path(record["path"])
    if record["present"]:
        if record["kind"] == "file":
            copy_atomic(Path(record["backup"]), path, int(record["mode"]))
        else:
            temporary = path.with_name(path.name + ".restore")
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()
            os.symlink(record["target"], temporary)
            os.replace(temporary, path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def readback() -> dict[str, Any]:
    result: dict[str, Any] = {"timers": {}}
    for timer in TIMER_NAMES:
        enabled = run(["systemctl", "is-enabled", timer])
        active = run(["systemctl", "is-active", timer])
        shown = run(["systemctl", "show", timer, "--property=NextElapseUSecRealtime", "--value"])
        next_elapse = shown.stdout.strip()
        if enabled.returncode or active.returncode or active.stdout.strip() != "active" or shown.returncode or not next_elapse or next_elapse == "n/a":
            raise ScheduleError(f"systemd timer 读回失败：{timer}")
        result["timers"][timer] = {"enabled": enabled.stdout.strip(), "active": active.stdout.strip(), "nextElapse": next_elapse}
    return result


def install(operation_id: str, expected_main: str, source_dir: Path) -> dict[str, Any]:
    safe_operation_id(operation_id)
    before = validate_source(source_dir, expected_main)
    release_root = RELEASES_ROOT / operation_id
    backup_root = GOVERNANCE_ROOT / "directory-audit" / "backups" / operation_id
    receipt = INSTALLATIONS / f"{operation_id}.json"
    if release_root.exists() or receipt.exists():
        raise ScheduleError("operation-id 已存在，拒绝覆盖既有安装证据")
    backup_root.mkdir(parents=True, mode=0o700)
    targets = [WRAPPER, CURRENT_LINK, *unit_payloads().keys()]
    snapshots = [snapshot_target(path, backup_root) for path in targets]
    applied = False
    try:
        release_root.mkdir(parents=True, mode=0o700)
        for name in SOURCE_FILES:
            copy_atomic(source_dir / name, release_root / name, stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if name.endswith(".py") else 0))
        write_atomic(WRAPPER, wrapper_text(), stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        for path, content in unit_payloads().items():
            write_atomic(path, content, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        temporary_link = CURRENT_LINK.with_name("current.new")
        if temporary_link.exists() or temporary_link.is_symlink():
            temporary_link.unlink()
        os.symlink(release_root, temporary_link)
        os.replace(temporary_link, CURRENT_LINK)
        reloaded = run(["systemctl", "daemon-reload"])
        if reloaded.returncode:
            raise ScheduleError(reloaded.stderr.strip() or "systemd daemon-reload 失败")
        enabled = run(["systemctl", "enable", "--now", *TIMER_NAMES])
        if enabled.returncode:
            raise ScheduleError(enabled.stderr.strip() or "systemd timer enable 失败")
        applied = True
        after = readback()
        result = {"schemaVersion": "domeye.server-directory-audit-schedule-installation/v1", "operationId": operation_id, "installedAt": utc_now(), "source": before, "releaseRoot": str(release_root), "wrapper": {"path": str(WRAPPER), "sha256": sha256_file(WRAPPER)}, "units": {str(path): hashlib.sha256(content.encode("utf-8")).hexdigest() for path, content in unit_payloads().items()}, "readback": after, "oldDomeyeTouched": False, "serviceMigrationPerformed": False, "automaticDelete": False, "rollbackBackupRoot": str(backup_root), "snapshots": snapshots}
        INSTALLATIONS.mkdir(parents=True, exist_ok=True)
        write_atomic(receipt, json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", stat.S_IRUSR | stat.S_IWUSR)
        result["receiptPath"] = str(receipt)
        return result
    except Exception as error:
        if applied:
            run(["systemctl", "disable", "--now", *TIMER_NAMES])
        for record in reversed(snapshots):
            restore_target(record)
        run(["systemctl", "daemon-reload"])
        raise ScheduleError("S6 安装失败，已恢复安装前目标") from error


def read_receipt(operation_id: str) -> dict[str, Any]:
    receipt = INSTALLATIONS / f"{safe_operation_id(operation_id)}.json"
    if not receipt.is_file() or receipt.is_symlink():
        raise ScheduleError("找不到可回滚的安装回执")
    if stat.S_IMODE(receipt.stat().st_mode) != 0o600:
        raise ScheduleError("安装回执权限不符合 root-only 要求")
    try:
        content = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScheduleError("安装回执不可读取或格式非法") from error
    if content.get("schemaVersion") != "domeye.server-directory-audit-schedule-installation/v1":
        raise ScheduleError("安装回执 schema 不匹配")
    if content.get("operationId") != operation_id or not isinstance(content.get("snapshots"), list):
        raise ScheduleError("安装回执身份不匹配")
    return content


def rollback(operation_id: str) -> dict[str, Any]:
    safe_operation_id(operation_id)
    if socket.gethostname() != EXPECTED_HOST:
        raise ScheduleError("主机不匹配")
    if os.geteuid() != 0:
        raise ScheduleError("只允许 root 回滚 S6 定时器")
    receipt = read_receipt(operation_id)
    release_root = Path(str(receipt.get("releaseRoot", "")))
    if release_root.parent != RELEASES_ROOT or not release_root.is_dir():
        raise ScheduleError("安装回执中的 release 根不受管或不存在")
    if not CURRENT_LINK.is_symlink() or CURRENT_LINK.resolve() != release_root.resolve():
        raise ScheduleError("当前 S6 版本已变化，拒绝覆盖其他安装")
    if not WRAPPER.is_file() or sha256_file(WRAPPER) != receipt.get("wrapper", {}).get("sha256"):
        raise ScheduleError("当前 S6 wrapper 已变化，拒绝覆盖其他安装")
    for path, digest in receipt.get("units", {}).items():
        unit = Path(path)
        if unit.parent != SYSTEMD_ROOT or not unit.is_file() or sha256_file(unit) != digest:
            raise ScheduleError(f"当前 S6 unit 已变化，拒绝覆盖：{unit}")
    stopped = run(["systemctl", "disable", "--now", *TIMER_NAMES])
    if stopped.returncode:
        raise ScheduleError(stopped.stderr.strip() or "停止 S6 timer 失败")
    try:
        for record in reversed(receipt["snapshots"]):
            restore_target(record)
        reloaded = run(["systemctl", "daemon-reload"])
        if reloaded.returncode:
            raise ScheduleError(reloaded.stderr.strip() or "systemd daemon-reload 失败")
    except Exception as error:
        raise ScheduleError("S6 回滚不完整；定时器已停止，请依据安装回执人工恢复") from error
    result = {"schemaVersion": "domeye.server-directory-audit-schedule-rollback/v1", "operationId": operation_id, "rolledBackAt": utc_now(), "installationReceipt": str(INSTALLATIONS / f"{operation_id}.json"), "oldDomeyeTouched": False, "serviceMigrationPerformed": False, "automaticDelete": False, "timersStopped": list(TIMER_NAMES)}
    ROLLBACKS.mkdir(parents=True, exist_ok=True)
    rollback_receipt = ROLLBACKS / f"{operation_id}.json"
    if rollback_receipt.exists():
        raise ScheduleError("该 operation-id 已有回滚回执，拒绝覆盖")
    write_atomic(rollback_receipt, json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", stat.S_IRUSR | stat.S_IWUSR)
    result["rollbackReceiptPath"] = str(rollback_receipt)
    return result


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--expected-main")
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    try:
        if args.apply and args.rollback:
            raise ScheduleError("--apply 与 --rollback 不能同时使用")
        if args.rollback:
            result = rollback(args.operation_id)
        else:
            if not args.expected_main:
                raise ScheduleError("预检或安装必须提供 --expected-main")
            result = install(args.operation_id, args.expected_main, args.source_dir) if args.apply else {"schemaVersion": "domeye.server-directory-audit-schedule-preflight/v1", "mode": "read_only", "source": validate_source(args.source_dir, args.expected_main), "units": {str(path): hashlib.sha256(content.encode("utf-8")).hexdigest() for path, content in unit_payloads().items()}}
    except (ScheduleError, OSError) as error:
        print(f"S6 审计定时器失败：{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
