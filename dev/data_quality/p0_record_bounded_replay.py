#!/usr/bin/env python3
"""执行一次 D2 64 条只读样本生成，并落盘外部执行记录。

本工具不用 shell 解释命令，不读取 ``database.env``，也不记录进程环境。
它只允许调用 ``p0_normalize_candidate.py --max-events 64``，将 stdout/stderr
写入全新普通文件，并在子进程成功且候选 ``SHA256SUMS`` 可读后生成规范 JSON
执行证据。执行证据是可审计记录，不是“两次运行相互独立”的密码学证明。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Dict, Optional, Sequence


SCHEMA_VERSION = "p0_d2_bounded_replay_execution_v1"
EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RecordedRunError(RuntimeError):
    """命令范围、输出安全边界或执行结果不满足证据要求。"""


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _new_path(path: Path, label: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise RecordedRunError("无法检查{}：{}".format(label, path)) from error
    else:
        raise RecordedRunError("{}必须不存在：{}".format(label, path))
    try:
        parent = path.parent.lstat()
    except OSError as error:
        raise RecordedRunError("{}父目录不可读：{}".format(label, path.parent)) from error
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise RecordedRunError("{}父路径必须是真实目录".format(label))


def _sha256(path: Path, label: str) -> str:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RecordedRunError("无法读取{}：{}".format(label, path)) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RecordedRunError("{}必须是普通文件".format(label))
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _option_value(command: Sequence[str], option: str) -> str:
    positions = [index for index, value in enumerate(command) if value == option]
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        raise RecordedRunError("命令必须恰好提供一次 {}".format(option))
    return command[positions[0] + 1]


def _validate_command(command: Sequence[str], candidate_dir: Path) -> None:
    if len(command) < 3 or Path(command[1]).name != "p0_normalize_candidate.py":
        raise RecordedRunError("只允许调用 p0_normalize_candidate.py")
    if _option_value(command, "--max-events") != "64":
        raise RecordedRunError("有界重放必须固定 --max-events 64")
    command_output = Path(_option_value(command, "--output-dir")).absolute()
    if command_output != candidate_dir:
        raise RecordedRunError("命令 --output-dir 未绑定候选目录")


def _atomic_write_new(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o440)
    try:
        total = 0
        while total < len(payload):
            total += os.write(descriptor, payload[total:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run(args: argparse.Namespace) -> Dict[str, Any]:
    execution_id = args.execution_id
    if not isinstance(execution_id, str) or EXECUTION_ID_RE.fullmatch(execution_id) is None:
        raise RecordedRunError("execution_id 非法")
    candidate_dir = Path(args.candidate_dir).absolute()
    stdout_path = Path(args.stdout_log).absolute()
    stderr_path = Path(args.stderr_log).absolute()
    evidence_path = Path(args.evidence_out).absolute()
    if len({candidate_dir, stdout_path, stderr_path, evidence_path}) != 4:
        raise RecordedRunError("候选、日志和执行证据路径必须互不相同")
    for path, label in (
        (candidate_dir, "候选目录"),
        (stdout_path, "stdout 日志"),
        (stderr_path, "stderr 日志"),
        (evidence_path, "执行证据"),
    ):
        _new_path(path, label)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    _validate_command(command, candidate_dir)
    command_hash = hashlib.sha256(_canonical_bytes(command)).hexdigest()
    started_at = _utc_now()
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        completed = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
        stdout.flush()
        stderr.flush()
        os.fsync(stdout.fileno())
        os.fsync(stderr.fileno())
    finished_at = _utc_now()
    stdout_path.chmod(0o440)
    stderr_path.chmod(0o440)
    if completed.returncode != 0:
        raise RecordedRunError(
            "D2 样本生成失败，exit_code={}；未生成执行证据".format(
                completed.returncode
            )
        )
    checksum_path = candidate_dir / "SHA256SUMS"
    checksum_sha = _sha256(checksum_path, "样本候选 SHA256SUMS")
    if SHA256_RE.fullmatch(checksum_sha) is None:
        raise RecordedRunError("样本候选 SHA256SUMS 哈希非法")
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "execution_id": execution_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": completed.returncode,
        "output_dir": str(candidate_dir.resolve(strict=True)),
        "command_argv_sha256": command_hash,
        "stdout_sha256": _sha256(stdout_path, "stdout 日志"),
        "stderr_sha256": _sha256(stderr_path, "stderr 日志"),
        "candidate_sha256sums_sha256": checksum_sha,
    }
    _atomic_write_new(evidence_path, _canonical_bytes(evidence))
    return evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="记录一次 D2 64 条有界真实重放")
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--stdout-log", required=True)
    parser.add_argument("--stderr-log", required=True)
    parser.add_argument("--evidence-out", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except Exception as error:
        print("错误：{}".format(error), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
