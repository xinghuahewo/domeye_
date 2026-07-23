#!/usr/bin/env python3
"""伊朗研究闭环 21+1 RIB/UPDATE 对账与总验收 CLI。

公开命令均不读取 MRT、不连接数据库。耗时操作由独立子进程执行，父进程采用
固定 420 秒观察、540 秒 TERM、590 秒 KILL，并在 596 秒前有界退出；
每次 ``run-bounded`` 最多推进一个可恢复 segment，因而不会用单进程重放
完整 1928 槽。
"""

from __future__ import annotations

import argparse
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

from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (  # noqa: E402
    write_canonical_json,
)
from backend.data_pipeline.research.rrc25_country_outage.full_window_finalize import (  # noqa: E402
    _load_json,
)
from backend.data_pipeline.research.rrc25_country_outage import (  # noqa: E402
    iran_research_acceptance as acceptance_backend,
)
from backend.data_pipeline.research.rrc25_country_outage.iran_research_acceptance import (  # noqa: E402
    DEFAULT_KILL_SECONDS,
    DEFAULT_OBSERVATION_SECONDS,
    DEFAULT_PARENT_EXIT_SECONDS,
    DEFAULT_TERM_SECONDS,
    IranResearchAcceptanceError,
    acceptance_workspace_status,
    build_successful_supervision_evidence,
    compute_anchor_verification_candidate,
    compute_overall_acceptance_candidate,
    compute_reconciliation_segment_candidate,
    initialize_acceptance_workspace,
    publish_anchor_verification_gate,
    publish_overall_research_acceptance,
    publish_reconciliation_segment,
    verify_overall_research_acceptance,
)


class IranAcceptanceCliError(RuntimeError):
    """CLI 子进程、候选制品或参数未闭合。"""


MAX_CHILD_OUTPUT_BYTES = 64 * 1024


def _json_output(value: Mapping[str, Any]) -> None:
    print(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2))


def _candidate_path(workspace_root: Path, kind: str) -> Path:
    workspace_root = acceptance_backend._assert_safe_workspace_mutation(
        workspace_root
    )
    parent = workspace_root / "supervisors"
    if not parent.is_dir() or parent.is_symlink():
        raise IranAcceptanceCliError("workspace supervisors 目录不可用")
    return parent / f".{kind}-candidate-{os.getpid()}-{secrets.token_hex(8)}.json"


def _supervise_child(
    command: Sequence[str],
    *,
    observation_seconds: float = DEFAULT_OBSERVATION_SECONDS,
    term_seconds: float = DEFAULT_TERM_SECONDS,
    kill_seconds: float = DEFAULT_KILL_SECONDS,
    monotonic=time.monotonic,
    poll_seconds: float = 0.05,
) -> Mapping[str, Any]:
    """监督一个独立子进程；测试可缩短时间，但验收发布只接受固定策略。"""

    if not command or any(not isinstance(item, str) or not item for item in command):
        raise IranAcceptanceCliError("child command 必须是非空字符串序列")
    observed = float(observation_seconds)
    term = float(term_seconds)
    kill = float(kill_seconds)
    if not 0 < observed < term < kill:
        raise IranAcceptanceCliError("supervisor 必须满足 0 < observe < TERM < KILL")
    started = monotonic()
    observed_crossed = False
    term_sent = False
    kill_sent = False
    reaped = False
    stdout = ""
    stderr = ""
    frozen = (
        observed == DEFAULT_OBSERVATION_SECONDS
        and term == DEFAULT_TERM_SECONDS
        and kill == DEFAULT_KILL_SECONDS
    )
    reap_budget = (
        DEFAULT_PARENT_EXIT_SECONDS - DEFAULT_KILL_SECONDS
        if frozen
        else min(1.0, max(0.10, kill))
    )
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_file:
        process = subprocess.Popen(
            list(command),
            cwd=REPOSITORY_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
            close_fds=True,
        )
        while process.poll() is None:
            elapsed = monotonic() - started
            if elapsed >= observed:
                observed_crossed = True
            if elapsed >= term and not term_sent:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                else:
                    term_sent = True
            if elapsed >= kill and not kill_sent:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                else:
                    kill_sent = True
                break
            time.sleep(min(max(poll_seconds, 0.001), 0.10))
        if process.poll() is None:
            reap_deadline = time.monotonic() + reap_budget
            while process.poll() is None and time.monotonic() < reap_deadline:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                remaining = reap_deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    process.wait(timeout=min(0.10, remaining))
                except subprocess.TimeoutExpired:
                    continue
        if process.poll() is not None:
            reaped = True
        for stream, target in ((stdout_file, "stdout"), (stderr_file, "stderr")):
            stream.flush()
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - MAX_CHILD_OUTPUT_BYTES))
            value = stream.read().decode("utf-8", errors="replace")
            if target == "stdout":
                stdout = value
            else:
                stderr = value
    elapsed = monotonic() - started
    if elapsed >= observed:
        observed_crossed = True
    if frozen and elapsed >= DEFAULT_PARENT_EXIT_SECONDS:
        reaped = False
    return {
        "policy": {
            "observation_seconds": observed,
            "term_seconds": term,
            "kill_seconds": kill,
            "parent_exit_seconds_exclusive": (
                DEFAULT_PARENT_EXIT_SECONDS if frozen else kill + reap_budget
            ),
            "is_frozen_acceptance_policy": frozen,
        },
        "actions": {
            "observation_boundary_crossed": observed_crossed,
            "term_sent": term_sent,
            "kill_sent": kill_sent,
            "child_reaped_within_parent_deadline": reaped,
        },
        "child_exit_code": process.returncode,
        "elapsed_seconds": round(elapsed, 6),
        "stdout": stdout,
        "stderr": stderr,
        "successful": (
            reaped
            and process.returncode == 0
            and not term_sent
            and not kill_sent
            and (not frozen or elapsed < DEFAULT_PARENT_EXIT_SECONDS)
        ),
    }


def _successful_evidence(result: Mapping[str, Any], *, command_kind: str) -> Mapping[str, Any]:
    if result.get("successful") is not True:
        stderr = str(result.get("stderr") or "")[-4000:]
        stdout = str(result.get("stdout") or "")[-2000:]
        raise IranAcceptanceCliError(
            "独立子进程未在 540 秒 TERM 前成功：" + (stderr or stdout or repr(result))
        )
    policy = result["policy"]
    evidence = build_successful_supervision_evidence(
        command_kind=command_kind,
        elapsed_seconds=max(float(result["elapsed_seconds"]), 0.000001),
        observation_seconds=policy["observation_seconds"],
        term_seconds=policy["term_seconds"],
        kill_seconds=policy["kill_seconds"],
    )
    if evidence["actions"]["observation_boundary_crossed"] != result["actions"][
        "observation_boundary_crossed"
    ]:
        raise IranAcceptanceCliError("supervisor observation 动作重算不一致")
    return evidence


def _run_candidate_child(
    *,
    workspace_root: Path,
    hidden_command: str,
    command_kind: str,
    extra_args: Sequence[str] = (),
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    candidate_path = _candidate_path(workspace_root, command_kind.replace("/", "-"))
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        hidden_command,
        "--workspace-root",
        str(workspace_root),
        "--candidate-path",
        str(candidate_path),
        *extra_args,
    ]
    try:
        result = _supervise_child(command)
        supervision = _successful_evidence(result, command_kind=command_kind)
        if not candidate_path.is_file() or candidate_path.is_symlink():
            raise IranAcceptanceCliError("成功 child 未发布规范 candidate")
        if candidate_path.stat().st_size >= 5_000_000_000:
            raise IranAcceptanceCliError("candidate 达到或超过 5GB")
        candidate = _load_json(candidate_path, maximum_bytes=2_000_000_000)
        return candidate, supervision
    finally:
        # candidate 只是父子进程交接临时文件，不是审计证据；正式 segment/gate
        # 由父进程在成功监督后另行 create-only 发布。
        _retire_candidate_file(candidate_path)
        for temporary in candidate_path.parent.glob(f".{candidate_path.name}.tmp-*"):
            _retire_candidate_file(temporary)


def _retire_candidate_file(path: Path) -> None:
    acceptance_backend._assert_safe_mutation_target(
        path, "acceptance candidate cleanup"
    )
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise IranAcceptanceCliError("candidate 临时路径不是普通文件，拒绝自动清理")
    path.unlink()


def _write_candidate(path: Path, value: Mapping[str, Any]) -> None:
    acceptance_backend._assert_safe_mutation_target(
        path, "acceptance child candidate"
    )
    artifact = write_canonical_json(path, value, kind="iran-acceptance-child-candidate", mode=0o400)
    os.chmod(artifact.path, 0o400)


def _prepare_child(args: argparse.Namespace) -> Mapping[str, Any]:
    return initialize_acceptance_workspace(
        args.workspace_root,
        update_acceptance_receipt_path=args.update_acceptance_receipt,
        analysis_rib_anchor_root=args.analysis_rib_anchor_root,
    )


def _prepare(args: argparse.Namespace) -> Mapping[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_prepare-child",
        "--workspace-root",
        str(args.workspace_root),
        "--update-acceptance-receipt",
        str(args.update_acceptance_receipt),
        "--analysis-rib-anchor-root",
        str(args.analysis_rib_anchor_root),
    ]
    result = _supervise_child(command)
    supervision = _successful_evidence(
        result, command_kind="acceptance-workspace-prepare"
    )
    try:
        child = json.loads(str(result.get("stdout") or ""))
    except json.JSONDecodeError as error:
        raise IranAcceptanceCliError("prepare child 未返回规范 JSON") from error
    expected = str(Path(args.workspace_root).expanduser().absolute().resolve())
    if not isinstance(child, Mapping) or child.get("workspace_root") != expected:
        raise IranAcceptanceCliError("prepare child 返回的 workspace 身份不一致")
    return {**dict(child), "prepare_supervision": dict(supervision)}


def _anchor_gate(args: argparse.Namespace) -> Mapping[str, Any]:
    root = Path(args.workspace_root).absolute()
    candidate, supervision = _run_candidate_child(
        workspace_root=root,
        hidden_command="_anchor-child",
        command_kind="analysis-rib-deep-verify",
    )
    gate = publish_anchor_verification_gate(
        root, candidate=candidate, supervision=supervision
    )
    return {
        "status": "analysis_rib_deep_verification_gate_published",
        "workspace_root": str(root),
        "anchor_set_semantic_sha256": gate["verification"][
            "anchor_set_semantic_sha256"
        ],
        "execution_ready": gate["verification"]["execution_ready"],
    }


def _run_bounded(args: argparse.Namespace) -> Mapping[str, Any]:
    root = Path(args.workspace_root).absolute()
    status = acceptance_workspace_status(root)
    index = status["next_segment_index"] if args.segment_index is None else args.segment_index
    if index is None:
        return {**status, "status": "all_segments_already_complete"}
    if index != status["next_segment_index"]:
        raise IranAcceptanceCliError(
            f"只能推进下一个连续 segment：{status['next_segment_index']}"
        )
    candidate, supervision = _run_candidate_child(
        workspace_root=root,
        hidden_command="_segment-child",
        command_kind=f"reconciliation-segment-{index:02d}",
        extra_args=("--segment-index", str(index)),
    )
    segment = publish_reconciliation_segment(
        root, candidate=candidate, supervision=supervision
    )
    return {
        "status": "segment_published",
        "workspace_root": str(root),
        "segment_index": index,
        "boundary_at_utc": segment["plan"]["boundary_at_utc"],
        "has_baseline_reference": segment["baseline_reference"] is not None,
        "evidence_resolution_count": len(segment["evidence_resolutions"]),
        "next_segment_index": (
            index + 1 if index + 1 < 22 else None
        ),
    }


def _finalize(args: argparse.Namespace) -> Mapping[str, Any]:
    root = Path(args.workspace_root).absolute()
    status = acceptance_workspace_status(root)
    if status["ready_to_finalize"] is not True:
        raise IranAcceptanceCliError("workspace 尚未完成 anchor gate 与 22 个 segment")
    candidate, supervision = _run_candidate_child(
        workspace_root=root,
        hidden_command="_finalize-child",
        command_kind="overall-acceptance-finalize",
    )
    receipt = publish_overall_research_acceptance(
        root,
        output_receipt_path=args.output_receipt,
        candidate=candidate,
        supervision=supervision,
    )
    return {
        "status": "overall_research_acceptance_published",
        "receipt_path": str(Path(args.output_receipt).absolute()),
        "receipt_sha256": receipt["receipt_sha256"],
        "acceptance_semantics": receipt["acceptance_semantics"],
    }


def _verify(args: argparse.Namespace) -> Mapping[str, Any]:
    # verify 不再重放 ancestry，但仍在独立 child 中执行，防止异常文件系统读取
    # 让当前 CLI 进程越过十分钟边界。
    receipt = Path(args.receipt).absolute()
    parent = receipt.parent
    candidate_path = parent / f".{receipt.name}.verify-{os.getpid()}-{secrets.token_hex(8)}.json"
    acceptance_backend._assert_safe_mutation_target(
        candidate_path, "overall acceptance verify candidate"
    )
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_verify-child",
        "--receipt",
        str(receipt),
        "--candidate-path",
        str(candidate_path),
    ]
    try:
        result = _supervise_child(command)
        _successful_evidence(result, command_kind="overall-acceptance-verify")
        return _load_json(candidate_path, maximum_bytes=64 * 1024 * 1024)
    finally:
        _retire_candidate_file(candidate_path)


def _hidden(args: argparse.Namespace) -> Mapping[str, Any]:
    if args.command == "_anchor-child":
        value = compute_anchor_verification_candidate(args.workspace_root)
    elif args.command == "_segment-child":
        value = compute_reconciliation_segment_candidate(
            args.workspace_root, segment_index=args.segment_index
        )
    elif args.command == "_finalize-child":
        value = compute_overall_acceptance_candidate(args.workspace_root)
    elif args.command == "_verify-child":
        value = verify_overall_research_acceptance(args.receipt)
    else:  # pragma: no cover
        raise IranAcceptanceCliError("未知内部命令")
    _write_candidate(Path(args.candidate_path).absolute(), value)
    return {"status": "candidate_published", "candidate_path": args.candidate_path}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="create-only 初始化 22-segment workspace")
    prepare.add_argument("--workspace-root", required=True)
    prepare.add_argument("--update-acceptance-receipt", required=True)
    prepare.add_argument("--analysis-rib-anchor-root", required=True)

    gate = sub.add_parser("anchor-gate", help="独立 child 深验 22 张 analysis RIB anchor")
    gate.add_argument("--workspace-root", required=True)

    run = sub.add_parser("run-bounded", help="最多推进一个可恢复 UPDATE/RIB 对账 segment")
    run.add_argument("--workspace-root", required=True)
    run.add_argument("--segment-index", type=int)

    status = sub.add_parser("status", help="只读查看 workspace 进度")
    status.add_argument("--workspace-root", required=True)

    finalize = sub.add_parser("finalize", help="总装 create-only overall research acceptance")
    finalize.add_argument("--workspace-root", required=True)
    finalize.add_argument("--output-receipt", required=True)

    verify = sub.add_parser("verify", help="离线核验 overall receipt，不重放 ancestry")
    verify.add_argument("--receipt", required=True)

    prepare_child = sub.add_parser("_prepare-child", help=argparse.SUPPRESS)
    prepare_child.add_argument("--workspace-root", required=True)
    prepare_child.add_argument("--update-acceptance-receipt", required=True)
    prepare_child.add_argument("--analysis-rib-anchor-root", required=True)

    for name in ("_anchor-child", "_segment-child", "_finalize-child"):
        hidden = sub.add_parser(name, help=argparse.SUPPRESS)
        hidden.add_argument("--workspace-root", required=True)
        hidden.add_argument("--candidate-path", required=True)
        if name == "_segment-child":
            hidden.add_argument("--segment-index", required=True, type=int)
    hidden_verify = sub.add_parser("_verify-child", help=argparse.SUPPRESS)
    hidden_verify.add_argument("--receipt", required=True)
    hidden_verify.add_argument("--candidate-path", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = _prepare(args)
        elif args.command == "anchor-gate":
            result = _anchor_gate(args)
        elif args.command == "run-bounded":
            result = _run_bounded(args)
        elif args.command == "status":
            result = acceptance_workspace_status(args.workspace_root)
        elif args.command == "finalize":
            result = _finalize(args)
        elif args.command == "verify":
            result = _verify(args)
        elif args.command == "_prepare-child":
            result = _prepare_child(args)
        else:
            result = _hidden(args)
    except (
        IranResearchAcceptanceError,
        IranAcceptanceCliError,
        FileExistsError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        print(f"伊朗研究总验收失败：{error}", file=sys.stderr)
        return 2
    _json_output(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
