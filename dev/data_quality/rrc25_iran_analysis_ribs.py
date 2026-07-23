#!/usr/bin/env python3
"""RRC25 伊朗 21+1 analysis-RIB anchor 的有界执行入口。

``dry-run`` 不打开 MRT，也不创建输出；``verify`` 只读核验独立 anchor 根。
``execute`` 和 ``resume`` 每次只派生一个独立 segment 子进程，父进程使用单调时钟
实际观察 420 秒边界，并在仍存活时分别于 540 秒发送 TERM、590 秒发送 KILL，
596 秒前有界退出。``run-bounded`` 也冻结为每次只执行一个单段流程；``reconcile``
由下一次独立调用处理上次进程退出留下的 ACTIVE/退役窗口。所有命令都保持
数据库写入为 0。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Optional, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.data_pipeline.research.rrc25_country_outage import (  # noqa: E402
    analysis_rib_anchor as anchor_backend,
)
from backend.data_pipeline.research.rrc25_country_outage.analysis_rib_anchor import (  # noqa: E402
    AnalysisRibAnchorError,
    AnalysisRibDescriptor,
    AnalysisRibPlan,
    AnalysisRibRetentionPolicy,
    AnalysisRibTerminationRequested,
    RawReservationToken,
    build_analysis_rib_plan,
    build_analysis_rib_retention_policy,
    compute_prior_journal_verification_candidate,
    import_full_window_seed_anchor,
    initialize_anchor_workspace,
    load_prior_raw_accounting_from_verification_receipt,
    publish_prior_journal_verification_receipt,
    reserve_raw_read,
    run_analysis_rib_anchor_segment,
    verify_analysis_rib_anchor_root,
)
from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (  # noqa: E402
    canonical_json,
    write_canonical_json,
)


SUPERVISOR_OBSERVATION_SECONDS = 420.0
SUPERVISOR_TERM_SECONDS = 540.0
SUPERVISOR_KILL_SECONDS = 590.0
SUPERVISOR_PARENT_EXIT_SECONDS = 596.0
DEFAULT_MAX_RAW_READ_BYTES = 50_000_000_000
DEFAULT_MAX_TEMPORARY_BYTES = 5_000_000_000
MAX_CHILD_OUTPUT_BYTES = 64 * 1024


class AnalysisRibCliError(ValueError):
    """CLI 元数据、执行调度或父子进程合同不闭合。"""


@dataclass(frozen=True)
class _ExecutionContext:
    selection: Mapping[str, Any]
    profile: Mapping[str, Any]
    bindings: Mapping[str, str]
    retention_policy: AnalysisRibRetentionPolicy
    plan: AnalysisRibPlan


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _load_json(path_value: str | Path, *, maximum_bytes: int) -> Mapping[str, Any]:
    path = Path(path_value)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AnalysisRibCliError(f"无法安全读取元数据：{path}") from error
    chunks = []
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AnalysisRibCliError("元数据必须是非符号链接普通文件")
        while True:
            block = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - size))
            if not block:
                break
            size += len(block)
            if size > maximum_bytes:
                raise AnalysisRibCliError("元数据超过显式读取上限")
            chunks.append(block)
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, name) != getattr(after, name) for name in identity):
            raise AnalysisRibCliError("元数据在读取期间发生变化")
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalysisRibCliError("元数据不是合法 UTF-8 JSON") from error
    if not isinstance(payload, Mapping):
        raise AnalysisRibCliError("元数据顶层必须是对象")
    return dict(payload)


def _regular_file_sha256(path_value: str | Path, *, maximum_bytes: int) -> str:
    path = Path(path_value)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AnalysisRibCliError(f"无法安全读取不可变收据：{path}") from error
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AnalysisRibCliError("不可变收据必须是普通文件")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            size += len(block)
            if size > maximum_bytes:
                raise AnalysisRibCliError("不可变收据超过显式读取上限")
            digest.update(block)
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, name) != getattr(after, name) for name in identity):
            raise AnalysisRibCliError("不可变收据在读取期间发生变化")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _bindings(path: str | Path) -> Mapping[str, str]:
    payload = _load_json(path, maximum_bytes=1024 * 1024)
    required = {
        "profile_sha256",
        "input_selection_sha256",
        "code_sha256",
        "mapping_sha256",
    }
    if set(payload) != required:
        raise AnalysisRibCliError("bindings 必须且只能包含四个冻结 SHA256 字段")
    return {name: str(payload[name]) for name in sorted(payload)}


def _load_context(args: argparse.Namespace) -> _ExecutionContext:
    selection = _load_json(args.selection, maximum_bytes=64 * 1024 * 1024)
    profile = _load_json(args.profile, maximum_bytes=4 * 1024 * 1024)
    bindings = _bindings(args.bindings)
    accounting = load_prior_raw_accounting_from_verification_receipt(
        args.prior_verification_receipt,
        journal_root=args.full_window_journal_root,
        bindings=bindings,
    )
    retention = build_analysis_rib_retention_policy(
        _load_json(args.compatible_mapping, maximum_bytes=64 * 1024 * 1024),
        _load_json(args.revised_mapping, maximum_bytes=16 * 1024 * 1024),
        bindings=bindings,
    )
    plan = build_analysis_rib_plan(
        selection,
        profile,
        prior_raw_accounting=accounting,
        bindings=bindings,
        max_raw_read_bytes=args.max_raw_read_bytes,
    )
    return _ExecutionContext(
        selection=selection,
        profile=profile,
        bindings=bindings,
        retention_policy=retention,
        plan=plan,
    )


def _descriptor_by_index(plan: AnalysisRibPlan, anchor_index: int) -> AnalysisRibDescriptor:
    matches = [item for item in plan.artifacts if item.anchor_index == anchor_index]
    if len(matches) != 1:
        raise AnalysisRibCliError("--anchor-index 未唯一命中冻结的 22-anchor 计划")
    return matches[0]


def _descriptor_by_artifact_id(
    plan: AnalysisRibPlan, artifact_id: str
) -> AnalysisRibDescriptor:
    matches = [item for item in plan.artifacts if item.artifact_id == artifact_id]
    if len(matches) != 1:
        raise AnalysisRibCliError("ACTIVE artifact 未唯一命中冻结的 22-anchor 计划")
    return matches[0]


def _ensure_workspace(args: argparse.Namespace, context: _ExecutionContext) -> bool:
    root = Path(args.anchor_root)
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        initialize_anchor_workspace(
            root,
            artifact_root=args.artifact_root,
            plan=context.plan,
            bindings=context.bindings,
            retention_policy=context.retention_policy,
            max_raw_read_bytes=args.max_raw_read_bytes,
        )
        return True
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise AnalysisRibCliError("anchor_root 已存在但不是非符号链接目录")
    anchor_backend._assert_existing_anchor_mutation_root(
        root, "anchor_root"
    )
    return False


def _active_attempt(
    anchor_root: str | Path, *, bindings: Mapping[str, str]
) -> Optional[Mapping[str, Any]]:
    loader = getattr(anchor_backend, "load_analysis_rib_active_attempt", None)
    if callable(loader):
        value = loader(anchor_root, bindings=bindings)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise AnalysisRibCliError("backend ACTIVE loader 返回值非法")
        return dict(value)
    # 向后兼容正在迁移的 backend；真正执行仍由 backend 在锁内重新核验 ACTIVE。
    path = Path(anchor_root) / "execution" / "ACTIVE.json"
    try:
        payload = _load_json(path, maximum_bytes=4 * 1024 * 1024)
    except FileNotFoundError:
        return None
    if payload.get("bindings") != dict(bindings):
        raise AnalysisRibCliError("ACTIVE bindings 与当前冻结执行不一致")
    return payload


def _reservation_from_path(
    anchor_root: str | Path,
    reservation_path: str,
    *,
    descriptor: AnalysisRibDescriptor,
    expected_attempt_id: str,
) -> RawReservationToken:
    loader = getattr(anchor_backend, "load_analysis_rib_reservation", None)
    if callable(loader):
        token = loader(anchor_root, expected_attempt_id)
        if (
            not isinstance(token, RawReservationToken)
            or token.path != reservation_path
            or token.descriptor != descriptor
        ):
            raise AnalysisRibCliError("backend reservation 与 child 参数不一致")
        return token
    relative = Path(reservation_path)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise AnalysisRibCliError("reservation path 必须是安全相对路径")
    absolute = Path(anchor_root) / relative
    payload = _load_json(absolute, maximum_bytes=1024 * 1024)
    if (
        payload.get("attempt_id") != expected_attempt_id
        or payload.get("artifact") != descriptor.to_dict()
    ):
        raise AnalysisRibCliError("reservation 与 attempt/artifact 不一致")
    sequence = payload.get("sequence")
    reserved = payload.get("reserved_raw_bytes")
    cumulative = payload.get("cumulative_after")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in (sequence, reserved, cumulative)
    ):
        raise AnalysisRibCliError("reservation 数值字段非法")
    return RawReservationToken(
        attempt_id=expected_attempt_id,
        path=relative.as_posix(),
        sha256=_regular_file_sha256(absolute, maximum_bytes=1024 * 1024),
        sequence=sequence,
        descriptor=descriptor,
        reserved_raw_bytes=reserved,
        cumulative_reserved_raw_bytes=cumulative,
    )


def _resume_fields(active: Mapping[str, Any]) -> tuple[str, str, str, Optional[str]]:
    attempt_id = active.get("attempt_id")
    artifact = active.get("artifact")
    checkpoint = active.get("latest_checkpoint_path")
    reservation_path = active.get("reservation_path")
    if not isinstance(attempt_id, str) or not isinstance(artifact, Mapping):
        raise AnalysisRibCliError("ACTIVE attempt/artifact 字段缺失")
    artifact_id = artifact.get("artifact_id")
    if not isinstance(artifact_id, str):
        raise AnalysisRibCliError("ACTIVE artifact_id 缺失")
    if active.get("state") != "checkpointed" or not isinstance(checkpoint, str):
        raise AnalysisRibCliError("resume 只接受 checkpointed ACTIVE")
    if reservation_path is not None and not isinstance(reservation_path, str):
        raise AnalysisRibCliError("ACTIVE reservation_path 非法")
    return attempt_id, artifact_id, checkpoint, reservation_path


def _reconcile_backend(
    anchor_root: str | Path, *, bindings: Mapping[str, str]
) -> Mapping[str, Any]:
    reconcile = getattr(
        anchor_backend, "reconcile_analysis_rib_anchor_workspace", None
    )
    if not callable(reconcile):
        raise AnalysisRibCliError(
            "backend 缺少 reconcile_analysis_rib_anchor_workspace"
        )
    result = reconcile(anchor_root, bindings=bindings)
    if not isinstance(result, Mapping):
        raise AnalysisRibCliError("backend reconcile 返回值非法")
    return dict(result)


def _record_supervisor_receipt(
    anchor_root: str | Path,
    *,
    bindings: Mapping[str, str],
    receipt: Mapping[str, Any],
) -> Mapping[str, Any]:
    recorder = getattr(
        anchor_backend, "record_analysis_rib_supervisor_receipt", None
    )
    if not callable(recorder):
        raise AnalysisRibCliError(
            "backend 缺少 record_analysis_rib_supervisor_receipt"
        )
    policy = receipt.get("policy")
    observations = receipt.get("observations")
    if not isinstance(policy, Mapping) or not isinstance(observations, Mapping):
        raise AnalysisRibCliError("supervisor receipt policy/actions 缺失")
    result = recorder(
        anchor_root,
        bindings=bindings,
        attempt_id=receipt["attempt_id"],
        artifact_id=receipt["artifact_id"],
        child_pid=receipt["child_pid"],
        started_at_utc=receipt["started_at_utc"],
        finished_at_utc=receipt["finished_at_utc"],
        returncode=receipt["returncode"],
        observation_seconds=policy["observation_seconds"],
        term_seconds=policy["term_seconds"],
        kill_seconds=policy["kill_seconds"],
        observed_420=observations["observation_boundary_reached"],
        term_sent=observations["term_sent"],
        kill_sent=observations["kill_sent"],
        reconciliation=receipt["reconciliation"],
    )
    if not isinstance(result, Mapping):
        raise AnalysisRibCliError("backend supervisor receipt 返回值非法")
    return dict(result)


def _bounded_output(value: str) -> str:
    raw = value.encode("utf-8", errors="replace")
    if len(raw) <= MAX_CHILD_OUTPUT_BYTES:
        return raw.decode("utf-8", errors="replace")
    return raw[-MAX_CHILD_OUTPUT_BYTES:].decode("utf-8", errors="replace")


def _child_result(stdout: str) -> Optional[Mapping[str, Any]]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _signal_process_group(process: subprocess.Popen[bytes], signal_number: int) -> bool:
    try:
        os.killpg(process.pid, signal_number)
    except ProcessLookupError:
        return False
    return True


def _temporary_output_tail(stream: Any) -> str:
    stream.flush()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(max(0, size - MAX_CHILD_OUTPUT_BYTES))
    return stream.read().decode("utf-8", errors="replace")


def _run_bounded_process_group(
    command: Sequence[str],
    *,
    observation_seconds: float,
    term_seconds: float,
    kill_seconds: float,
) -> Mapping[str, Any]:
    """监督一个进程组；KILL 后只做有界 reap，绝不调用无界 communicate。"""

    started_at = _utc_now()
    started = time.monotonic()
    process: Optional[subprocess.Popen[bytes]] = None
    spawn_error: Optional[str] = None
    observation_reached = False
    alive_at_observation = False
    term_sent = False
    kill_sent = False
    term_at: Optional[float] = None
    kill_at: Optional[float] = None
    reaped = False
    stdout = ""
    stderr = ""
    returncode: Optional[int] = None
    production_policy = (
        observation_seconds == SUPERVISOR_OBSERVATION_SECONDS
        and term_seconds == SUPERVISOR_TERM_SECONDS
        and kill_seconds == SUPERVISOR_KILL_SECONDS
    )
    reap_budget = (
        SUPERVISOR_PARENT_EXIT_SECONDS - SUPERVISOR_KILL_SECONDS
        if production_policy
        else min(1.0, max(0.10, kill_seconds))
    )
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_file:
        try:
            process = subprocess.Popen(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as error:
            spawn_error = f"{type(error).__name__}: {error}"

        if process is not None:
            while process.poll() is None:
                elapsed = time.monotonic() - started
                if not observation_reached and elapsed >= observation_seconds:
                    observation_reached = True
                    alive_at_observation = process.poll() is None
                if (
                    not term_sent
                    and elapsed >= term_seconds
                    and process.poll() is None
                ):
                    term_sent = _signal_process_group(process, signal.SIGTERM)
                    if term_sent:
                        term_at = elapsed
                if (
                    not kill_sent
                    and elapsed >= kill_seconds
                    and process.poll() is None
                ):
                    kill_sent = _signal_process_group(process, signal.SIGKILL)
                    if kill_sent:
                        kill_at = elapsed
                    break
                future = [
                    value
                    for value in (observation_seconds, term_seconds, kill_seconds)
                    if value > elapsed
                ]
                wait_for = min(future) - elapsed if future else 0.01
                time.sleep(max(0.002, min(0.10, wait_for)))

            if process.poll() is None:
                reap_deadline = time.monotonic() + reap_budget
                while process.poll() is None and time.monotonic() < reap_deadline:
                    _signal_process_group(process, signal.SIGKILL)
                    remaining = reap_deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        process.wait(timeout=min(0.10, remaining))
                    except subprocess.TimeoutExpired:
                        continue
            if process.poll() is not None:
                reaped = True
                returncode = process.returncode
            stdout = _temporary_output_tail(stdout_file)
            stderr = _temporary_output_tail(stderr_file)

    elapsed = time.monotonic() - started
    if elapsed >= observation_seconds:
        observation_reached = True
    if production_policy and elapsed >= SUPERVISOR_PARENT_EXIT_SECONDS:
        # 这是硬失败，不再做 reconciliation 或任何可能延长父进程寿命的工作。
        reaped = False
    return {
        "started_at_utc": started_at,
        "finished_at_utc": _utc_now(),
        "elapsed_seconds": elapsed,
        "process": process,
        "child_pid": None if process is None else process.pid,
        "returncode": returncode,
        "spawn_error": spawn_error,
        "observation_reached": observation_reached,
        "alive_at_observation": alive_at_observation,
        "term_sent": term_sent,
        "term_at": term_at,
        "kill_sent": kill_sent,
        "kill_at": kill_at,
        "reaped": reaped,
        "stdout": stdout,
        "stderr": stderr,
        "production_policy": production_policy,
        "parent_exit_deadline_seconds_exclusive": (
            SUPERVISOR_PARENT_EXIT_SECONDS if production_policy else kill_seconds + reap_budget
        ),
    }


def _prior_supervision_evidence(observed: Mapping[str, Any]) -> Mapping[str, Any]:
    elapsed = float(observed.get("elapsed_seconds", 0.0))
    if (
        observed.get("child_pid") is None
        or observed.get("reaped") is not True
        or observed.get("returncode") != 0
        or observed.get("term_sent") is True
        or observed.get("kill_sent") is True
        or not 0 < elapsed < SUPERVISOR_TERM_SECONDS
        or observed.get("production_policy") is not True
    ):
        raise AnalysisRibCliError(
            "prior journal 深验 child 未在冻结 TERM 前成功并有界回收："
            + _bounded_output(str(observed.get("stderr") or observed))
        )
    return {
        "semantics": (
            "independent_process_group_420_observe_540_term_590_kill_596_exit_v1"
        ),
        "policy": {
            "observation_seconds": SUPERVISOR_OBSERVATION_SECONDS,
            "term_seconds": SUPERVISOR_TERM_SECONDS,
            "kill_seconds": SUPERVISOR_KILL_SECONDS,
            "parent_exit_seconds_exclusive": SUPERVISOR_PARENT_EXIT_SECONDS,
        },
        "actions": {
            "term_sent": False,
            "kill_sent": False,
            "child_reaped_within_parent_deadline": True,
        },
        "child_exit_code": 0,
        "elapsed_seconds": round(elapsed, 6),
        "database_writes": 0,
    }


def _supervise_child(
    command: Sequence[str],
    *,
    anchor_root: str | Path,
    bindings: Mapping[str, str],
    attempt_id: str,
    artifact_id: str,
    command_kind: str,
    observation_seconds: float = SUPERVISOR_OBSERVATION_SECONDS,
    term_seconds: float = SUPERVISOR_TERM_SECONDS,
    kill_seconds: float = SUPERVISOR_KILL_SECONDS,
) -> Mapping[str, Any]:
    """用父进程单调时钟监督一个 segment；时间参数仅供快速 fixture 测试。"""

    if not 0 < observation_seconds < term_seconds < kill_seconds:
        raise AnalysisRibCliError("supervisor 必须保持 0<observe<TERM<KILL")
    observed = _run_bounded_process_group(
        command,
        observation_seconds=observation_seconds,
        term_seconds=term_seconds,
        kill_seconds=kill_seconds,
    )
    stdout = str(observed["stdout"])
    stderr = str(observed["stderr"])
    returncode = observed["returncode"]
    parsed_child = _child_result(stdout)
    child_status = None if parsed_child is None else parsed_child.get("status")
    abnormal = (
        observed["child_pid"] is None
        or observed["reaped"] is not True
        or returncode != 0
        or observed["term_sent"]
        or observed["kill_sent"]
        or child_status not in {"complete", "checkpointed"}
    )
    reconciliation: Optional[Mapping[str, Any]] = None
    reconciliation_error: Optional[str] = None
    if (
        abnormal
        and observed["reaped"] is True
        and observed["term_sent"] is not True
        and observed["kill_sent"] is not True
    ):
        try:
            reconciliation = _reconcile_backend(anchor_root, bindings=bindings)
        except (AnalysisRibAnchorError, AnalysisRibCliError, OSError, ValueError) as error:
            reconciliation_error = f"{type(error).__name__}: {error}"
    elif abnormal and (
        observed["term_sent"] is True or observed["kill_sent"] is True
    ):
        reconciliation_error = (
            "bounded_parent_exit_requires_separate_reconcile_invocation"
        )

    receipt = {
        "schema_version": "rrc25-analysis-rib-process-supervisor-observation/v1",
        "attempt_id": attempt_id,
        "artifact_id": artifact_id,
        "command_kind": command_kind,
        "child_pid": observed["child_pid"],
        "started_at_utc": observed["started_at_utc"],
        "finished_at_utc": observed["finished_at_utc"],
        "elapsed_seconds": observed["elapsed_seconds"],
        "policy": {
            "clock": "parent_process_monotonic",
            "observation_seconds": observation_seconds,
            "term_seconds": term_seconds,
            "kill_seconds": kill_seconds,
            "fixed_production_policy": observed["production_policy"],
            "parent_exit_deadline_seconds_exclusive": observed[
                "parent_exit_deadline_seconds_exclusive"
            ],
        },
        "observations": {
            "observation_boundary_reached": observed["observation_reached"],
            "alive_at_observation_boundary": observed["alive_at_observation"],
            "term_sent": observed["term_sent"],
            "term_sent_at_elapsed_seconds": observed["term_at"],
            "kill_sent": observed["kill_sent"],
            "kill_sent_at_elapsed_seconds": observed["kill_at"],
            "child_reaped_within_parent_deadline": observed["reaped"],
        },
        "returncode": returncode,
        "spawn_error": observed["spawn_error"],
        "child_result": None if parsed_child is None else dict(parsed_child),
        "abnormal_exit": abnormal,
        "reconciliation": None if reconciliation is None else dict(reconciliation),
        "reconciliation_error": reconciliation_error,
        "database_writes": 0,
    }
    receipt_ref: Optional[Mapping[str, Any]] = None
    supervisor_receipt_error: Optional[str] = None
    if observed["child_pid"] is not None and observed["reaped"] is True:
        try:
            receipt_ref = _record_supervisor_receipt(
                anchor_root, bindings=bindings, receipt=receipt
            )
        except (
            AnalysisRibAnchorError,
            AnalysisRibCliError,
            OSError,
            ValueError,
        ) as error:
            supervisor_receipt_error = f"{type(error).__name__}: {error}"
            abnormal = True
    else:
        supervisor_receipt_error = (
            "child_not_spawned_no_supervisor_receipt"
            if observed["child_pid"] is None
            else "child_process_group_not_reaped_before_parent_deadline"
        )
    return {
        "returncode": returncode,
        "abnormal_exit": abnormal,
        "child_result": None if parsed_child is None else dict(parsed_child),
        "supervisor_receipt_ref": receipt_ref,
        "supervisor_receipt_error": supervisor_receipt_error,
        "reconciliation": None if reconciliation is None else dict(reconciliation),
        "reconciliation_error": reconciliation_error,
        "child_stdout": _bounded_output(stdout),
        "child_stderr": _bounded_output(stderr),
    }


def _context_cli_arguments(args: argparse.Namespace) -> list[str]:
    return [
        "--selection",
        str(args.selection),
        "--profile",
        str(args.profile),
        "--bindings",
        str(args.bindings),
        "--full-window-journal-root",
        str(args.full_window_journal_root),
        "--prior-verification-receipt",
        str(args.prior_verification_receipt),
        "--compatible-mapping",
        str(args.compatible_mapping),
        "--revised-mapping",
        str(args.revised_mapping),
        "--artifact-root",
        str(args.artifact_root),
        "--anchor-root",
        str(args.anchor_root),
        "--max-raw-read-bytes",
        str(args.max_raw_read_bytes),
        "--max-temporary-bytes",
        str(args.max_temporary_bytes),
    ]


def _segment_child_command(
    args: argparse.Namespace,
    *,
    descriptor: AnalysisRibDescriptor,
    attempt_id: str,
    reservation_path: Optional[str],
    resume_checkpoint_path: Optional[str],
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_segment-child",
        *_context_cli_arguments(args),
        "--anchor-index",
        str(descriptor.anchor_index),
        "--attempt-id",
        attempt_id,
    ]
    if reservation_path is not None:
        command.extend(("--reservation-path", reservation_path))
    if resume_checkpoint_path is not None:
        command.extend(("--resume-checkpoint", resume_checkpoint_path))
    return command


def _invoke_imported_seed(
    args: argparse.Namespace,
    *,
    context: _ExecutionContext,
    descriptor: AnalysisRibDescriptor,
) -> Any:
    return import_full_window_seed_anchor(
        args.anchor_root,
        descriptor=descriptor,
        bindings=context.bindings,
        retention_policy=context.retention_policy,
        max_temporary_bytes=args.max_temporary_bytes,
        attempt_id=args.attempt_id,
    )


def _segment_child(args: argparse.Namespace) -> int:
    context = _load_context(args)
    descriptor = _descriptor_by_index(context.plan, args.anchor_index)

    def terminate(_signum: int, _frame: Any) -> None:
        raise AnalysisRibTerminationRequested("父 supervisor 已在 540 秒发送 TERM")

    signal.signal(signal.SIGTERM, terminate)
    if descriptor.ingestion_mode == "imported_full_window_seed":
        if args.reservation_path is not None or args.resume_checkpoint is not None:
            raise AnalysisRibCliError("imported seed 禁止 reservation/resume checkpoint")
        result = _invoke_imported_seed(
            args, context=context, descriptor=descriptor
        )
    else:
        if args.reservation_path is None:
            raise AnalysisRibCliError("new_raw segment 缺少 reservation path")
        reservation = _reservation_from_path(
            args.anchor_root,
            args.reservation_path,
            descriptor=descriptor,
            expected_attempt_id=args.attempt_id,
        )
        result = run_analysis_rib_anchor_segment(
            args.anchor_root,
            artifact_root=args.artifact_root,
            descriptor=descriptor,
            bindings=context.bindings,
            reservation=reservation,
            retention_policy=context.retention_policy,
            resume_checkpoint_path=args.resume_checkpoint,
            max_temporary_bytes=args.max_temporary_bytes,
            planned_checkpoint_seconds=SUPERVISOR_OBSERVATION_SECONDS,
            soft_stop_seconds=SUPERVISOR_TERM_SECONDS,
            hard_stop_seconds=SUPERVISOR_KILL_SECONDS,
            process_supervisor_hard_timeout_seconds=SUPERVISOR_KILL_SECONDS,
        )
    print(
        canonical_json(
            {
                "command": "_segment-child",
                "attempt_id": args.attempt_id,
                "status": result.status,
                "reason": result.reason,
                "artifact_id": result.artifact_id,
                "checkpoint_path": result.checkpoint_path,
                "anchor_receipt_path": result.anchor_receipt_path,
                "retirement_receipt_path": result.retirement_receipt_path,
                "next_record_ordinal": result.next_record_ordinal,
                "next_record_offset": result.next_record_offset,
                "process_seconds": result.process_seconds,
                "peak_temporary_bytes": result.peak_temporary_bytes,
                "database_writes": 0,
            }
        )
    )
    return 0


def _run_one_segment(
    args: argparse.Namespace,
    *,
    context: _ExecutionContext,
    descriptor: AnalysisRibDescriptor,
    active: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Any]:
    if active is None:
        if descriptor.ingestion_mode == "imported_full_window_seed":
            attempt_id = "attempt_v1_" + secrets.token_hex(16)
            reservation_path = None
        else:
            reservation = reserve_raw_read(args.anchor_root, descriptor)
            attempt_id = reservation.attempt_id
            reservation_path = reservation.path
        checkpoint_path = None
        command_kind = "execute"
    else:
        attempt_id, artifact_id, checkpoint_path, reservation_path = _resume_fields(
            active
        )
        if artifact_id != descriptor.artifact_id:
            raise AnalysisRibCliError("resume descriptor 与 ACTIVE artifact 不一致")
        if descriptor.ingestion_mode != "new_raw" or reservation_path is None:
            raise AnalysisRibCliError("当前实现不允许 imported seed 从 checkpoint resume")
        command_kind = "resume"
    command = _segment_child_command(
        args,
        descriptor=descriptor,
        attempt_id=attempt_id,
        reservation_path=reservation_path,
        resume_checkpoint_path=checkpoint_path,
    )
    return _supervise_child(
        command,
        anchor_root=args.anchor_root,
        bindings=context.bindings,
        attempt_id=attempt_id,
        artifact_id=descriptor.artifact_id,
        command_kind=command_kind,
    )


def _completed_artifact_ids(anchor_root: str | Path) -> set[str]:
    root = Path(anchor_root) / "receipts"
    completed: set[str] = set()
    if not root.is_dir() or root.is_symlink():
        return completed
    for path in sorted(root.glob("anchor-*.json")):
        payload = _load_json(path, maximum_bytes=64 * 1024 * 1024)
        artifact = payload.get("artifact")
        if payload.get("status") == "complete" and isinstance(artifact, Mapping):
            artifact_id = artifact.get("artifact_id")
            if isinstance(artifact_id, str):
                completed.add(artifact_id)
    return completed


def _execution_order(plan: AnalysisRibPlan) -> tuple[AnalysisRibDescriptor, ...]:
    # imported seed 先建立窗口起点；其后按 analysis 时间推进；baseline 最后只作参考。
    return tuple(
        sorted(
            plan.artifacts,
            key=lambda item: (
                0
                if item.ingestion_mode == "imported_full_window_seed"
                else 2
                if item.role == "baseline_reference_rib"
                else 1,
                item.artifact_time_utc,
            ),
        )
    )


def _retire_prior_candidate(path: Path, *, verification_root: Path) -> None:
    safe_root = anchor_backend._assert_mutation_root(
        verification_root,
        "prior_verification_root",
    )
    resolved = path.expanduser().resolve(strict=False)
    if resolved.parent != safe_root or not resolved.name.startswith(
        ".prior-journal-verification-candidate-"
    ):
        raise AnalysisRibCliError("prior verification candidate 清理目标越界")
    try:
        metadata = resolved.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise AnalysisRibCliError("prior verification candidate 不是普通文件")
    resolved.unlink()
    descriptor = os.open(safe_root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _prior_verification_child(args: argparse.Namespace) -> int:
    bindings = _bindings(args.bindings)
    verification_root = anchor_backend._assert_mutation_root(
        Path(args.verification_root),
        "prior_verification_root",
        source_roots=(Path(args.full_window_journal_root),),
    )
    candidate_path = Path(args.candidate_path).expanduser().resolve(strict=False)
    if candidate_path.parent != verification_root:
        raise AnalysisRibCliError("prior candidate 必须直属 verification_root")
    candidate = compute_prior_journal_verification_candidate(
        args.full_window_journal_root,
        bindings=bindings,
    )
    write_canonical_json(
        candidate_path,
        candidate,
        kind="analysis_rib_prior_journal_verification_candidate",
        mode=0o400,
    )
    print(
        canonical_json(
            {
                "status": "prior_journal_deep_verification_candidate_published",
                "candidate_path": str(candidate_path),
                "database_writes": 0,
            }
        )
    )
    return 0


def _verify_prior(args: argparse.Namespace) -> int:
    verification_root = anchor_backend._assert_mutation_root(
        Path(args.verification_root),
        "prior_verification_root",
        source_roots=(Path(args.full_window_journal_root),),
    )
    candidate_path = verification_root / (
        f".prior-journal-verification-candidate-{os.getpid()}-"
        f"{secrets.token_hex(8)}.json"
    )
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_prior-verification-child",
        "--full-window-journal-root",
        str(args.full_window_journal_root),
        "--bindings",
        str(args.bindings),
        "--verification-root",
        str(verification_root),
        "--candidate-path",
        str(candidate_path),
    ]
    try:
        observed = _run_bounded_process_group(
            command,
            observation_seconds=SUPERVISOR_OBSERVATION_SECONDS,
            term_seconds=SUPERVISOR_TERM_SECONDS,
            kill_seconds=SUPERVISOR_KILL_SECONDS,
        )
        supervision = _prior_supervision_evidence(observed)
        candidate = _load_json(candidate_path, maximum_bytes=4 * 1024 * 1024)
        receipt = publish_prior_journal_verification_receipt(
            verification_root,
            candidate=candidate,
            journal_root=args.full_window_journal_root,
            bindings=_bindings(args.bindings),
            supervision=supervision,
        )
        print(
            canonical_json(
                {
                    "command": "verify-prior",
                    "status": "prior_journal_terminal_deep_verification_sealed",
                    **dict(receipt),
                }
            )
        )
        return 0
    finally:
        _retire_prior_candidate(
            candidate_path, verification_root=verification_root
        )


def _dry_run(args: argparse.Namespace) -> int:
    context = _load_context(args)
    plan = context.plan
    result = {
        **plan.to_dict(),
        "command": "dry-run",
        "status": "fixture_plan_ready" if plan.execution_allowed else "blocked",
        "retention_policy": context.retention_policy.to_dict(),
        "raw_files_opened": 0,
        "files_written": 0,
        "database_writes": 0,
        "execution_ready": False,
        "blocking_reasons": [
            "dry_run_does_not_execute_child_supervision",
            "dry_run_does_not_prove_crash_reconciliation",
        ],
        "limitations_zh": [
            "dry-run 不读取真实 MRT；每份 RIB 的解压大小由执行时 5GB 排他门在线核验。",
            "本入口只计划独立 RIB anchor，不运行、不重置 UPDATE 主曲线。",
        ],
    }
    print(canonical_json(result))
    return 0 if plan.execution_allowed else 3


def _verify(args: argparse.Namespace) -> int:
    selection = _load_json(args.selection, maximum_bytes=64 * 1024 * 1024)
    profile = _load_json(args.profile, maximum_bytes=4 * 1024 * 1024)
    result = verify_analysis_rib_anchor_root(
        args.anchor_root,
        selection=selection,
        profile=profile,
        bindings=_bindings(args.bindings),
    )
    print(canonical_json({**result, "command": "verify", "raw_files_opened": 0}))
    return 0


def _execute(args: argparse.Namespace) -> int:
    context = _load_context(args)
    initialized = _ensure_workspace(args, context)
    if _active_attempt(args.anchor_root, bindings=context.bindings) is not None:
        raise AnalysisRibCliError("存在 ACTIVE；请使用 resume 或 reconcile")
    descriptor = _descriptor_by_index(context.plan, args.anchor_index)
    outcome = _run_one_segment(
        args, context=context, descriptor=descriptor
    )
    result = {
        "command": "execute",
        "workspace_initialized": initialized,
        "anchor_index": descriptor.anchor_index,
        "artifact_id": descriptor.artifact_id,
        "supervision": outcome,
        "database_writes": 0,
    }
    print(canonical_json(result))
    return 0 if not outcome["abnormal_exit"] else 4


def _resume(args: argparse.Namespace) -> int:
    context = _load_context(args)
    active = _active_attempt(args.anchor_root, bindings=context.bindings)
    if active is None:
        raise AnalysisRibCliError("resume 缺少 ACTIVE")
    _attempt_id, artifact_id, _checkpoint, _reservation = _resume_fields(active)
    descriptor = _descriptor_by_artifact_id(context.plan, artifact_id)
    outcome = _run_one_segment(
        args, context=context, descriptor=descriptor, active=active
    )
    print(
        canonical_json(
            {
                "command": "resume",
                "anchor_index": descriptor.anchor_index,
                "artifact_id": descriptor.artifact_id,
                "supervision": outcome,
                "database_writes": 0,
            }
        )
    )
    return 0 if not outcome["abnormal_exit"] else 4


def _reconcile(args: argparse.Namespace) -> int:
    result = _reconcile_backend(args.anchor_root, bindings=_bindings(args.bindings))
    print(canonical_json({"command": "reconcile", **result, "database_writes": 0}))
    return 0


def _run_bounded(args: argparse.Namespace) -> int:
    if args.max_segments != 1:
        raise AnalysisRibCliError(
            "run-bounded 每次 CLI 调用必须且只能推进一个 segment（--max-segments=1）"
        )
    context = _load_context(args)
    initialized = _ensure_workspace(args, context)
    history: list[Mapping[str, Any]] = []
    # 首先收敛上一次进程可能留下的 publish/unlink 窗口；checkpoint ACTIVE 保留。
    initial_reconciliation = _reconcile_backend(
        args.anchor_root, bindings=context.bindings
    )
    ordered = _execution_order(context.plan)
    for _segment_number in range(1, 2):
        active = _active_attempt(args.anchor_root, bindings=context.bindings)
        if active is not None:
            _attempt_id, artifact_id, _checkpoint, _reservation = _resume_fields(active)
            descriptor = _descriptor_by_artifact_id(context.plan, artifact_id)
            outcome = _run_one_segment(
                args, context=context, descriptor=descriptor, active=active
            )
        else:
            completed = _completed_artifact_ids(args.anchor_root)
            pending = [item for item in ordered if item.artifact_id not in completed]
            if not pending:
                verification = verify_analysis_rib_anchor_root(
                    args.anchor_root,
                    selection=context.selection,
                    profile=context.profile,
                    bindings=context.bindings,
                )
                print(
                    canonical_json(
                        {
                            "command": "run-bounded",
                            "status": "all_anchors_complete",
                            "workspace_initialized": initialized,
                            "segments_executed": len(history),
                            "initial_reconciliation": initial_reconciliation,
                            "history": history,
                            "verification": verification,
                            "database_writes": 0,
                        }
                    )
                )
                return 0
            descriptor = pending[0]
            outcome = _run_one_segment(
                args, context=context, descriptor=descriptor
            )
        history.append(
            {
                "anchor_index": descriptor.anchor_index,
                "artifact_id": descriptor.artifact_id,
                "returncode": outcome["returncode"],
                "abnormal_exit": outcome["abnormal_exit"],
                "child_result": outcome["child_result"],
                "supervisor_receipt_ref": outcome["supervisor_receipt_ref"],
                "reconciliation": outcome["reconciliation"],
                "reconciliation_error": outcome["reconciliation_error"],
            }
        )
        if outcome["abnormal_exit"]:
            print(
                canonical_json(
                    {
                        "command": "run-bounded",
                        "status": "stopped_after_abnormal_child",
                        "workspace_initialized": initialized,
                        "segments_executed": len(history),
                        "initial_reconciliation": initial_reconciliation,
                        "history": history,
                        "database_writes": 0,
                    }
                )
            )
            return 4

    completed = _completed_artifact_ids(args.anchor_root)
    print(
        canonical_json(
            {
                "command": "run-bounded",
                "status": "segment_limit_reached",
                "workspace_initialized": initialized,
                "segments_executed": len(history),
                "completed_anchor_count": len(completed),
                "remaining_anchor_count": len(context.plan.artifacts) - len(completed),
                "active_present": _active_attempt(
                    args.anchor_root, bindings=context.bindings
                )
                is not None,
                "initial_reconciliation": initial_reconciliation,
                "history": history,
                "database_writes": 0,
            }
        )
    )
    return 0


def _add_context_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--selection", required=True, help="冻结的完整 selection JSON")
    parser.add_argument("--profile", required=True, help="冻结研究 Profile JSON")
    parser.add_argument("--bindings", required=True, help="四项冻结执行 bindings")
    parser.add_argument(
        "--full-window-journal-root",
        required=True,
        help="已完成且与一次性 prior verification receipt 绑定的 UPDATE journal 根",
    )
    parser.add_argument(
        "--prior-verification-receipt",
        required=True,
        help="verify-prior 独立子进程发布的内容寻址 terminal/deep receipt",
    )
    parser.add_argument("--compatible-mapping", required=True)
    parser.add_argument("--revised-mapping", required=True)
    parser.add_argument(
        "--max-raw-read-bytes",
        type=int,
        default=DEFAULT_MAX_RAW_READ_BYTES,
        help="raw 排他上限，默认十进制 50GB；达到即拒绝",
    )


def _add_execution_arguments(parser: argparse.ArgumentParser) -> None:
    _add_context_arguments(parser)
    parser.add_argument("--artifact-root", required=True, help="只读原始 MRT 制品根")
    parser.add_argument("--anchor-root", required=True, help="独立 analysis anchor 根")
    parser.add_argument(
        "--max-temporary-bytes",
        type=int,
        default=DEFAULT_MAX_TEMPORARY_BYTES,
        help="spool+本 attempt 输出+staging 排他上限，默认十进制 5GB",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RRC25 伊朗 21+1 analysis-RIB anchor 有界执行/核验"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prior = subparsers.add_parser(
        "verify-prior",
        help="独立受监管地深验一次完整 UPDATE journal，并发布内容寻址 receipt",
    )
    prior.add_argument("--full-window-journal-root", required=True)
    prior.add_argument("--bindings", required=True)
    prior.add_argument("--verification-root", required=True)
    prior.set_defaults(handler=_verify_prior)

    dry_run = subparsers.add_parser(
        "dry-run", help="只核对 21+1 selection 与累计 raw 排他门，不打开 MRT"
    )
    _add_context_arguments(dry_run)
    dry_run.set_defaults(handler=_dry_run)

    verify = subparsers.add_parser(
        "verify", help="只读核验 22 个 anchor、分片、raw ledger 和执行收据"
    )
    verify.add_argument("--selection", required=True)
    verify.add_argument("--profile", required=True)
    verify.add_argument("--bindings", required=True)
    verify.add_argument("--anchor-root", required=True)
    verify.set_defaults(handler=_verify)

    execute = subparsers.add_parser(
        "execute", help="create-only 初始化（如需要）并监督执行一个 segment"
    )
    _add_execution_arguments(execute)
    execute.add_argument("--anchor-index", required=True, type=int)
    execute.set_defaults(handler=_execute)

    resume = subparsers.add_parser(
        "resume", help="从 checkpointed ACTIVE 监督恢复一个 segment"
    )
    _add_execution_arguments(resume)
    resume.set_defaults(handler=_resume)

    reconcile = subparsers.add_parser(
        "reconcile", help="锁内收敛 publish/retire/kill 留下的 ACTIVE 窗口"
    )
    reconcile.add_argument("--anchor-root", required=True)
    reconcile.add_argument("--bindings", required=True)
    reconcile.set_defaults(handler=_reconcile)

    bounded = subparsers.add_parser(
        "run-bounded", help="每次调用只监督执行一个独立 segment 子进程"
    )
    _add_execution_arguments(bounded)
    bounded.add_argument(
        "--max-segments",
        type=int,
        choices=(1,),
        default=1,
        help="兼容参数；冻结为 1，禁止一个父进程连续派生多个 worker",
    )
    bounded.set_defaults(handler=_run_bounded)

    return parser


def _build_segment_child_parser() -> argparse.ArgumentParser:
    """父进程专用解析器；不把内部命令暴露在用户帮助中。"""

    child = argparse.ArgumentParser(add_help=False)
    _add_execution_arguments(child)
    child.add_argument("--anchor-index", required=True, type=int)
    child.add_argument("--attempt-id", required=True)
    child.add_argument("--reservation-path")
    child.add_argument("--resume-checkpoint")
    child.set_defaults(handler=_segment_child)
    return child


def _build_prior_verification_child_parser() -> argparse.ArgumentParser:
    child = argparse.ArgumentParser(add_help=False)
    child.add_argument("--full-window-journal-root", required=True)
    child.add_argument("--bindings", required=True)
    child.add_argument("--verification-root", required=True)
    child.add_argument("--candidate-path", required=True)
    child.set_defaults(handler=_prior_verification_child)
    return child


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values[:1] == ["_segment-child"]:
        parser = _build_segment_child_parser()
        args = parser.parse_args(values[1:])
    elif values[:1] == ["_prior-verification-child"]:
        parser = _build_prior_verification_child_parser()
        args = parser.parse_args(values[1:])
    else:
        parser = build_parser()
        args = parser.parse_args(values)
    try:
        return int(args.handler(args))
    except (
        AnalysisRibAnchorError,
        AnalysisRibCliError,
        AnalysisRibTerminationRequested,
        OSError,
        ValueError,
    ) as error:
        print(f"analysis-RIB anchor 失败：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
