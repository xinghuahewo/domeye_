#!/usr/bin/env python3
"""RRC25 伊朗完整窗口的纯派生最终化、验真与独立复现入口。

本入口不读取 MRT、不连接数据库，也不创建或推进 full-window journal。调用方
必须提供上游已经冻结并完成的 selection、bindings、代码身份、两套映射和
journal。输出目标必须最初不存在；模块会在同父目录 staging 完成核验后再
create-only 原子发布。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (  # noqa: E402
    canonical_json,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (  # noqa: E402
    CountryImpactError,
    mapping_view_from_frozen_snapshot,
    mapping_view_from_revised_snapshot,
)
from backend.data_pipeline.research.rrc25_country_outage.full_window_finalize import (  # noqa: E402
    FullWindowFinalizeError,
    finalize_full_window_package,
    reproduce_semantics,
    verify_finalization_resource_receipt,
    verify_finalized_package,
    verify_reproduction_acceptance_receipt,
)
from backend.data_pipeline.research.rrc25_country_outage.full_window_finalize_workspace import (  # noqa: E402
    DEFAULT_CHILD_PLANNED_STOP_SECONDS,
    DEFAULT_PARENT_KILL_SECONDS,
    DEFAULT_PARENT_TERM_SECONDS,
    FullWindowFinalizeWorkspaceError,
    assemble_finalized_package_from_workspace,
    assemble_workspace_reproduction,
    initialize_finalization_workspace,
    reconcile_finalization_workspace,
    reconcile_workspace_publication,
    run_finalization_workspace_segment,
    seal_finalization_workspace,
    verify_workspace_assembled_package,
    verify_finalization_workspace,
)


SOFT_SECONDS = DEFAULT_PARENT_TERM_SECONDS
HARD_SECONDS = DEFAULT_PARENT_KILL_SECONDS
CHILD_PLANNED_STOP_SECONDS = DEFAULT_CHILD_PLANNED_STOP_SECONDS
BOUNDED_REAP_SECONDS = 4.0
MAX_CHILD_OUTPUT_BYTES = 1_048_576


class FinalizeCliError(ValueError):
    """最终化 CLI 参数或监管边界非法。"""


class FinalizeHardTimeout(TimeoutError):
    """最终化达到 590 秒 KILL 边界。"""


class _ChildPlannedStopGuard:
    """assembly 子进程在 420 秒后拒绝开始下一项可恢复工作。"""

    def __init__(self, *, monotonic: Any = time.monotonic) -> None:
        self._monotonic = monotonic
        self._started = monotonic()

    def hook(self, phase: str, _path: Path) -> None:
        elapsed = self._monotonic() - self._started
        if elapsed < 0:
            raise FinalizeCliError("assembly 子进程单调时钟倒退")
        if elapsed >= CHILD_PLANNED_STOP_SECONDS:
            raise FinalizeCliError(
                f"assembly 子进程在 {phase} 前达到 420 秒计划停点；"
                "已保留可恢复 staging/ACTIVE"
            )


def _load(path: str, *, maximum_bytes: int = 256 * 1024 * 1024) -> Mapping[str, Any]:
    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except OSError as error:
        raise FinalizeCliError(f"冻结 JSON 不可读：{path}") from error
    if len(raw) > maximum_bytes:
        raise FinalizeCliError(f"冻结 JSON 超过大小限制：{path}")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FinalizeCliError(f"冻结 JSON 非法：{path}") from error
    if not isinstance(value, Mapping):
        raise FinalizeCliError(f"冻结 JSON 顶层必须是对象：{path}")
    return dict(value)


def _input_values(args: argparse.Namespace) -> Mapping[str, Mapping[str, Any]]:
    return {
        "profile": _load(args.profile),
        "source_fact_snapshot": _load(args.source_fact),
        "incident_policy": _load(args.incident_policy),
        "compatible_mapping_snapshot": _load(args.compatible_mapping),
        "revised_mapping_snapshot": _load(args.revised_mapping),
        "code_identity": _load(args.code_identity),
        "input_selection": _load(args.selection),
        "claim_inventory": _load(args.claim_inventory),
        "bindings": _load(args.bindings),
    }


def _workspace_product_values(
    args: argparse.Namespace,
) -> Mapping[str, Mapping[str, Any]]:
    """装配子进程必须独立加载并核验完整冻结业务输入。

    ``journal_root`` 由 sealed workspace 的 GENESIS 唯一绑定，不能由装配调用方
    另行覆盖；其余九项业务输入则必须在每个受监管 child 内重新读取，确保两个
    空目录各自完成 segment adapter + package plan，而不是复用父进程内存结果。
    """

    return {
        "profile": _load(args.profile),
        "source_fact_snapshot": _load(args.source_fact),
        "incident_policy": _load(args.incident_policy),
        "compatible_mapping_snapshot": _load(args.compatible_mapping),
        "revised_mapping_snapshot": _load(args.revised_mapping),
        "code_identity": _load(args.code_identity),
        "input_selection": _load(args.selection),
        "claim_inventory": _load(args.claim_inventory),
        "bindings": _load(args.bindings),
    }


def _workspace_mapping_views(args: argparse.Namespace) -> tuple[Any, Any]:
    compatible_snapshot = _load(args.compatible_mapping)
    revised_snapshot = _load(args.revised_mapping)
    compatible = mapping_view_from_frozen_snapshot(compatible_snapshot)
    revised = mapping_view_from_revised_snapshot(
        revised_snapshot, compatible_snapshot
    )
    return compatible, revised


class _Supervisor:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.soft_crossed = threading.Event()
        self._timer: Optional[threading.Timer] = None
        self._previous_handler: Any = None

    def __enter__(self) -> "_Supervisor":
        self._timer = threading.Timer(SOFT_SECONDS, self.soft_crossed.set)
        self._timer.daemon = True
        self._timer.start()
        if threading.current_thread() is threading.main_thread():
            self._previous_handler = signal.getsignal(signal.SIGALRM)

            def hard_timeout(_signum: int, _frame: Any) -> None:
                raise FinalizeHardTimeout("最终化达到 600 秒硬边界")

            signal.signal(signal.SIGALRM, hard_timeout)
            signal.setitimer(signal.ITIMER_REAL, HARD_SECONDS)
        return self

    def hook(self, phase: str, _path: Path) -> None:
        elapsed = time.monotonic() - self.started
        if elapsed >= HARD_SECONDS:
            raise FinalizeHardTimeout("最终化达到 600 秒硬边界")
        # 540 秒是 soft stop：不再启动新的最终化；已经完成 staging verify 的
        # 单次原子发布可以在 600 秒硬边界前收口，receipt 会披露 crossed=true。
        if self.soft_crossed.is_set() and phase == "after_content_publish":
            raise FinalizeCliError("最终化在内容阶段越过 540 秒 soft stop，保留 staging 并停止")

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        if self._timer is not None:
            self._timer.cancel()
        if threading.current_thread() is threading.main_thread():
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self._previous_handler)


def _add_frozen_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--journal-root", required=True, help="已经完成的 full-window journal")
    parser.add_argument("--profile", required=True, help="冻结研究 Profile JSON")
    parser.add_argument("--source-fact", required=True, help="冻结旧 Incident 事实 JSON")
    parser.add_argument("--incident-policy", required=True, help="Incident→Episode 非因果策略 JSON")
    parser.add_argument("--compatible-mapping", required=True, help="compatible 映射快照")
    parser.add_argument("--revised-mapping", required=True, help="revised 映射快照")
    parser.add_argument("--code-identity", required=True, help="上游回放代码身份")
    parser.add_argument("--selection", required=True, help="完整冻结 input selection")
    parser.add_argument("--claim-inventory", required=True, help="报告主张清单")
    parser.add_argument("--bindings", required=True, help="journal 四项 SHA256 bindings")


def _add_workspace_product_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", required=True, help="冻结研究 Profile JSON")
    parser.add_argument("--source-fact", required=True, help="冻结旧 Incident 事实 JSON")
    parser.add_argument("--incident-policy", required=True, help="Incident→Episode 非因果策略 JSON")
    parser.add_argument("--compatible-mapping", required=True, help="compatible 映射快照")
    parser.add_argument("--revised-mapping", required=True, help="revised 映射快照")
    parser.add_argument("--code-identity", required=True, help="上游回放代码身份")
    parser.add_argument("--selection", required=True, help="完整冻结 input selection")
    parser.add_argument("--claim-inventory", required=True, help="报告主张清单")
    parser.add_argument("--bindings", required=True, help="journal 四项 SHA256 bindings")


def _add_workspace_run_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace-root", required=True, help="create-only 最终化 workspace")
    parser.add_argument("--compatible-mapping", required=True, help="compatible 映射快照")
    parser.add_argument("--revised-mapping", required=True, help="revised 映射快照")
    parser.add_argument(
        "--max-slots",
        type=int,
        default=1,
        help="本次最多提交的槽数；子进程 420 秒后不再开新槽",
    )


def _workspace_run_result(value: Any) -> Mapping[str, Any]:
    return {
        "workspace_root": str(value.workspace_root),
        "completed_slots": value.completed_slots,
        "total_slots": value.total_slots,
        "segment_slots_committed": value.segment_slots_committed,
        "stop_reason": value.stop_reason,
        "sealed": value.sealed,
        "terminal_path": str(value.terminal_path) if value.terminal_path else None,
        "deep_verification_path": (
            str(value.deep_verification_path)
            if value.deep_verification_path
            else None
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    finalize = commands.add_parser("finalize", help="纯派生并原子发布首包（pending/not_accepted）")
    _add_frozen_inputs(finalize)
    finalize.add_argument("--output-root", required=True, help="必须最初不存在的最终包目标")
    finalize.add_argument("--resource-receipt", help="create-only 资源 receipt；默认位于包同级")

    verify = commands.add_parser("verify-only", help="离线核验已发布最终包")
    verify.add_argument("--package-root", required=True)
    verify.add_argument("--resource-receipt")

    reproduce = commands.add_parser("reproduce", help="第二目录复现并发布独立 accepted receipt")
    _add_frozen_inputs(reproduce)
    reproduce.add_argument("--reference-package-root", required=True)
    reproduce.add_argument("--output-root", required=True, help="必须最初不存在的第二包目标")
    reproduce.add_argument("--acceptance-receipt", required=True)
    reproduce.add_argument("--resource-receipt", help="第二包 create-only 资源 receipt")

    receipt = commands.add_parser(
        "verify-acceptance",
        help="重新核验双目录 accepted receipt（v2 日常为 receipt-only）",
    )
    receipt.add_argument("--acceptance-receipt", required=True)

    workspace_init = commands.add_parser(
        "workspace-init",
        aliases=["init-workspace"],
        help="create-only 初始化逐槽最终化 workspace",
    )
    workspace_init.add_argument("--workspace-root", required=True)
    workspace_init.add_argument("--journal-root", required=True)
    workspace_init.add_argument("--bindings", required=True)
    workspace_init.add_argument("--code-identity", required=True)
    workspace_init.add_argument(
        "--study-id", default="rrc25-iran-country-outage-20260227"
    )
    workspace_init.add_argument(
        "--incident-ref",
        default="country_outage/2026-02-27+09:12:32/IR/1/r",
    )

    workspace_start = commands.add_parser(
        "workspace-start", aliases=["start"], help="由父进程监督首个有界槽段"
    )
    _add_workspace_run_inputs(workspace_start)
    workspace_resume = commands.add_parser(
        "workspace-resume", aliases=["resume"], help="由父进程监督恢复下一个有界槽段"
    )
    _add_workspace_run_inputs(workspace_resume)

    workspace_reconcile = commands.add_parser(
        "workspace-reconcile",
        aliases=["reconcile"],
        help="收口已发布 receipt 或退役未提交槽",
    )
    workspace_reconcile.add_argument("--workspace-root", required=True)
    workspace_seal = commands.add_parser(
        "workspace-seal", aliases=["seal"], help="封存已完成的 workspace"
    )
    workspace_seal.add_argument("--workspace-root", required=True)
    workspace_verify = commands.add_parser(
        "workspace-verify",
        aliases=["verify", "verify-workspace"],
        help="不解压 segment/observation 的 sealed receipt-only 验真",
    )
    workspace_verify.add_argument("--workspace-root", required=True)

    workspace_assemble = commands.add_parser(
        "workspace-assemble",
        aliases=["assemble-workspace"],
        help="仅从 sealed segments 装配最终包",
    )
    workspace_assemble.add_argument("--workspace-root", required=True)
    workspace_assemble.add_argument("--output-root", required=True)
    workspace_assemble.add_argument("--resource-receipt")
    _add_workspace_product_inputs(workspace_assemble)

    workspace_reproduction = commands.add_parser(
        "workspace-reproduce",
        aliases=["reproduce-workspace"],
        help="从同一 verified segment index 独立装配双目录",
    )
    workspace_reproduction.add_argument("--workspace-root", required=True)
    workspace_reproduction.add_argument("--reference-output-root", required=True)
    workspace_reproduction.add_argument("--reproduction-output-root", required=True)
    workspace_reproduction.add_argument("--acceptance-receipt", required=True)
    _add_workspace_product_inputs(workspace_reproduction)

    publication_reconcile = commands.add_parser(
        "workspace-reconcile-publication",
        aliases=["reconcile-workspace-publication"],
        help="协调 assembly rename 后中断或历史 staging",
    )
    publication_reconcile.add_argument("--workspace-root", required=True)
    _add_workspace_product_inputs(publication_reconcile)

    workspace_package_verify = commands.add_parser(
        "workspace-verify-package",
        aliases=["verify-workspace-package"],
        help="核验 segment 装配包及 deep/resource receipt",
    )
    workspace_package_verify.add_argument("--package-root", required=True)
    workspace_package_verify.add_argument("--resource-receipt")

    workspace_child = commands.add_parser("_workspace-child", help=argparse.SUPPRESS)
    _add_workspace_run_inputs(workspace_child)
    workspace_assemble_child = commands.add_parser(
        "_workspace-assemble-child", help=argparse.SUPPRESS
    )
    workspace_assemble_child.add_argument("--workspace-root", required=True)
    workspace_assemble_child.add_argument("--output-root", required=True)
    workspace_assemble_child.add_argument("--resource-receipt")
    _add_workspace_product_inputs(workspace_assemble_child)
    workspace_reproduce_child = commands.add_parser(
        "_workspace-reproduce-child", help=argparse.SUPPRESS
    )
    workspace_reproduce_child.add_argument("--workspace-root", required=True)
    workspace_reproduce_child.add_argument(
        "--reference-output-root", required=True
    )
    workspace_reproduce_child.add_argument(
        "--reproduction-output-root", required=True
    )
    workspace_reproduce_child.add_argument(
        "--acceptance-receipt", required=True
    )
    _add_workspace_product_inputs(workspace_reproduce_child)
    return parser


def _workspace_child_argv(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "_workspace-child",
        "--workspace-root",
        args.workspace_root,
        "--compatible-mapping",
        args.compatible_mapping,
        "--revised-mapping",
        args.revised_mapping,
        "--max-slots",
        str(args.max_slots),
    ]


def _decode_child_result(stdout: str) -> Mapping[str, Any]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise FinalizeCliError("最终化子进程未返回 JSON")
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise FinalizeCliError("最终化子进程返回非法 JSON") from error
    if not isinstance(value, Mapping):
        raise FinalizeCliError("最终化子进程 JSON 顶层必须是对象")
    return dict(value)


def _bounded_child_output(
    stdout_file: Any,
    stderr_file: Any,
) -> tuple[str, str]:
    def _tail(stream: Any) -> str:
        stream.flush()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - MAX_CHILD_OUTPUT_BYTES))
        return stream.read().decode("utf-8", errors="replace")

    return _tail(stdout_file), _tail(stderr_file)


def _signal_child_process_group(child: subprocess.Popen[Any], sig: int) -> None:
    try:
        os.killpg(child.pid, sig)
    except ProcessLookupError:
        pass


def _supervise_argv(
    argv: Sequence[str],
    *,
    operation: str,
) -> Mapping[str, Any]:
    """真实父进程监管：420 秒观察、540 秒 TERM、590 秒 KILL。"""

    with (
        tempfile.TemporaryFile(mode="w+b") as stdout_file,
        tempfile.TemporaryFile(mode="w+b") as stderr_file,
    ):
        try:
            child = subprocess.Popen(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as error:
            raise FinalizeCliError(f"无法启动{operation}子进程") from error
        try:
            child.wait(timeout=CHILD_PLANNED_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                child.wait(
                    timeout=SOFT_SECONDS - CHILD_PLANNED_STOP_SECONDS
                )
            except subprocess.TimeoutExpired:
                _signal_child_process_group(child, signal.SIGTERM)
                try:
                    child.wait(timeout=HARD_SECONDS - SOFT_SECONDS)
                except subprocess.TimeoutExpired:
                    _signal_child_process_group(child, signal.SIGKILL)
                    try:
                        child.wait(timeout=BOUNDED_REAP_SECONDS)
                    except subprocess.TimeoutExpired:
                        raise FinalizeHardTimeout(
                            f"{operation}子进程已在 590 秒 KILL，"
                            "但未在有界 reap 窗口退出"
                        )
                    raise FinalizeHardTimeout(
                        f"{operation}子进程在 540 秒 TERM 后仍未退出，"
                        "已在 590 秒 KILL 并完成有界 reap"
                    )
                raise FinalizeCliError(
                    f"{operation}子进程越过 540 秒，已 TERM；"
                    "下次执行将先 reconcile 并仅重做未提交工作"
                )
        stdout, stderr = _bounded_child_output(stdout_file, stderr_file)
        if child.returncode != 0:
            detail = (
                stderr.strip().splitlines()[-1]
                if stderr.strip()
                else "未提供错误详情"
            )
            raise FinalizeCliError(
                f"{operation}子进程失败（exit={child.returncode}）：{detail}"
            )
        return _decode_child_result(stdout)


def _supervise_workspace_child(args: argparse.Namespace) -> Mapping[str, Any]:
    return _supervise_argv(
        _workspace_child_argv(args), operation="逐槽最终化"
    )


def _workspace_assemble_child_argv(args: argparse.Namespace) -> list[str]:
    argv = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_workspace-assemble-child",
        "--workspace-root",
        args.workspace_root,
        "--output-root",
        args.output_root,
    ]
    if args.resource_receipt:
        argv.extend(("--resource-receipt", args.resource_receipt))
    argv.extend(_workspace_product_child_argv(args))
    return argv


def _workspace_product_child_argv(args: argparse.Namespace) -> list[str]:
    return [
        "--profile",
        args.profile,
        "--source-fact",
        args.source_fact,
        "--incident-policy",
        args.incident_policy,
        "--compatible-mapping",
        args.compatible_mapping,
        "--revised-mapping",
        args.revised_mapping,
        "--code-identity",
        args.code_identity,
        "--selection",
        args.selection,
        "--claim-inventory",
        args.claim_inventory,
        "--bindings",
        args.bindings,
    ]


def _workspace_reproduce_child_argv(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "_workspace-reproduce-child",
        "--workspace-root",
        args.workspace_root,
        "--reference-output-root",
        args.reference_output_root,
        "--reproduction-output-root",
        args.reproduction_output_root,
        "--acceptance-receipt",
        args.acceptance_receipt,
        *_workspace_product_child_argv(args),
    ]


def _supervise_workspace_assembly_child(
    args: argparse.Namespace,
) -> Mapping[str, Any]:
    return _supervise_argv(
        _workspace_assemble_child_argv(args),
        operation="segment assembly",
    )


def _supervise_workspace_reproduction_child(
    args: argparse.Namespace,
) -> Mapping[str, Any]:
    return _supervise_argv(
        _workspace_reproduce_child_argv(args),
        operation="双目录 segment assembly",
    )


def _run(args: argparse.Namespace) -> Mapping[str, Any]:
    command = {
        "init-workspace": "workspace-init",
        "start": "workspace-start",
        "resume": "workspace-resume",
        "reconcile": "workspace-reconcile",
        "seal": "workspace-seal",
        "verify": "workspace-verify",
        "verify-workspace": "workspace-verify",
        "assemble-workspace": "workspace-assemble",
        "reproduce-workspace": "workspace-reproduce",
        "reconcile-workspace-publication": "workspace-reconcile-publication",
        "verify-workspace-package": "workspace-verify-package",
    }.get(args.command, args.command)
    if command in {"workspace-start", "workspace-resume"}:
        return _supervise_workspace_child(args)
    if command == "workspace-assemble":
        return _supervise_workspace_assembly_child(args)
    if command == "workspace-reproduce":
        return _supervise_workspace_reproduction_child(args)
    if command == "_workspace-child":
        compatible, revised = _workspace_mapping_views(args)
        return _workspace_run_result(
            run_finalization_workspace_segment(
                args.workspace_root,
                compatible_mapping=compatible,
                revised_mapping=revised,
                max_slots=args.max_slots,
                planned_stop_seconds=CHILD_PLANNED_STOP_SECONDS,
            )
        )
    if command == "_workspace-assemble-child":
        guard = _ChildPlannedStopGuard()
        return dict(
            assemble_finalized_package_from_workspace(
                args.workspace_root,
                args.output_root,
                resource_receipt_path=args.resource_receipt,
                publication_hook=guard.hook,
                **_workspace_product_values(args),
            )
        )
    if command == "_workspace-reproduce-child":
        guard = _ChildPlannedStopGuard()
        return dict(
            assemble_workspace_reproduction(
                args.workspace_root,
                reference_output_root=args.reference_output_root,
                reproduction_output_root=args.reproduction_output_root,
                acceptance_receipt_path=args.acceptance_receipt,
                publication_hook=guard.hook,
                **_workspace_product_values(args),
            )
        )
    with _Supervisor() as supervisor:
        if command == "workspace-init":
            return dict(
                initialize_finalization_workspace(
                    args.workspace_root,
                    journal_root=args.journal_root,
                    bindings=_load(args.bindings),
                    code_identity=_load(args.code_identity),
                    study_id=args.study_id,
                    incident_ref=args.incident_ref,
                )
            )
        if command == "workspace-reconcile":
            return dict(reconcile_finalization_workspace(args.workspace_root))
        if command == "workspace-seal":
            terminal, deep = seal_finalization_workspace(args.workspace_root)
            return {
                "sealed": True,
                "terminal_path": str(terminal),
                "deep_verification_path": str(deep),
            }
        if command == "workspace-verify":
            return dict(verify_finalization_workspace(args.workspace_root))
        if command == "workspace-reconcile-publication":
            return dict(
                reconcile_workspace_publication(
                    args.workspace_root,
                    **_workspace_product_values(args),
                )
            )
        if command == "workspace-verify-package":
            return dict(
                verify_workspace_assembled_package(
                    args.package_root,
                    resource_receipt_path=args.resource_receipt,
                )
            )
        if command == "verify-only":
            result = dict(verify_finalized_package(args.package_root))
            if args.resource_receipt:
                verify_finalization_resource_receipt(
                    args.package_root, args.resource_receipt
                )
                result["finalization_resource_receipt_verified"] = True
            return result
        if command == "verify-acceptance":
            receipt = verify_reproduction_acceptance_receipt(
                args.acceptance_receipt
            )
            return {
                "verified": True,
                "acceptance_state": receipt["acceptance_state"],
                "semantic_core_sha256": receipt["semantic_core_sha256"],
                **(
                    {
                        "business_semantic_core_sha256": receipt[
                            "business_semantic_core_sha256"
                        ],
                        "finalization_segment_core_sha256": receipt[
                            "finalization_segment_core_sha256"
                        ],
                    }
                    if receipt.get("schema_version")
                    == "rrc25-full-window-reproduction-acceptance/v2"
                    else {}
                ),
            }
        inputs = _input_values(args)
        if command == "finalize":
            package = finalize_full_window_package(
                journal_root=args.journal_root,
                output_root=args.output_root,
                publication_hook=supervisor.hook,
                resource_receipt_path=args.resource_receipt,
                **inputs,
            )
            return {
                "package_root": str(package.root),
                "release_id": package.manifest["release_id"],
                "semantic_core_sha256": package.semantic_core_sha256,
                "acceptance_state": "not_accepted",
                "reproduction_state": "pending",
                "resource_receipt_path": str(package.resource_receipt_path),
            }
        return reproduce_semantics(
            reference_package_root=args.reference_package_root,
            output_root=args.output_root,
            acceptance_receipt_path=args.acceptance_receipt,
            journal_root=args.journal_root,
            publication_hook=supervisor.hook,
            second_resource_receipt_path=args.resource_receipt,
            **inputs,
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        result = _run(parser.parse_args(argv))
    except (
        CountryImpactError,
        FinalizeCliError,
        FinalizeHardTimeout,
        FullWindowFinalizeError,
        FullWindowFinalizeWorkspaceError,
        FileExistsError,
    ) as error:
        parser.error(str(error))
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
