"""S6 static INFO 数据库运行时收口与最终证据。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .manifest import validate_manifest
from .output import write_text_exclusive
from .runtime import read_runtime_backend_state
from .s4 import _core_hash_state
from .s5 import (
    S5AcceptanceError,
    _business_fingerprint,
    _candidate_boundary,
    _connect,
    _docker_fingerprint,
    _failure_evidence_inventory,
    _read_json,
    _release_state,
    _require_real_directory,
    _sha256_file,
    _source_metadata,
)
from .shadow import _verify_all_sources


class S6AcceptanceError(S5AcceptanceError):
    """S6 不能证明普通运行时已经摆脱旧 INFO 目录。"""


_PROCESS_KINDS = ("api", "snapshot", "detector", "background")


def _utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _sha256_value(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _verify_s5_receipt(
    receipt: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    if (
        receipt.get("component") != "static_info_stage_gate"
        or receipt.get("stage_id") != "S5"
        or receipt.get("status") != "pass"
        or receipt.get("deviation_count") != 0
        or receipt.get("deviations") != []
        or receipt.get("subject", {}).get("content_id")
        != manifest.get("content_id")
        or receipt.get("subject", {}).get("manifest_sha256")
        != manifest.get("manifest_sha256")
    ):
        raise S6AcceptanceError("S5 回执未通过或与当前 manifest 身份不一致")
    requirements = receipt.get("requirements")
    if not isinstance(requirements, list) or len(requirements) != 12:
        raise S6AcceptanceError("S5 回执缺少 12 项最终要求")
    for item in requirements:
        if not isinstance(item, dict):
            raise S6AcceptanceError("S5 回执要求项无效")
        expected = (
            "not_due"
            if item.get("requirement_id") == "FA-12"
            else "pass"
        )
        if item.get("status") != expected:
            raise S6AcceptanceError(
                f"S5 前置要求状态错误：{item.get('requirement_id')}"
            )


def _trace_summary(
    trace_path: Path,
    *,
    legacy_info_paths: Sequence[Path],
) -> Mapping[str, Any]:
    if trace_path.is_symlink() or not trace_path.is_file():
        raise S6AcceptanceError(f"运行追踪缺失或为软链接：{trace_path}")
    direct_reads = []
    inet_connections = []
    unix_connections = []
    for raw_line in trace_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        if any(str(path) in raw_line for path in legacy_info_paths):
            direct_reads.append(raw_line[-1000:])
        if "connect(" in raw_line and (
            "AF_INET" in raw_line or "AF_INET6" in raw_line
        ):
            inet_connections.append(raw_line[-1000:])
        if "connect(" in raw_line and "AF_UNIX" in raw_line:
            unix_connections.append(raw_line[-1000:])
    return {
        "trace_sha256": _sha256_file(trace_path),
        "trace_size_bytes": trace_path.stat().st_size,
        "legacy_info_direct_read_count": len(direct_reads),
        "legacy_info_direct_read_samples": direct_reads[:5],
        "inet_database_connection_count": len(inet_connections),
        "inet_connection_samples": inet_connections[:5],
        "unix_connection_count": len(unix_connections),
    }


def _run_traced_process(
    *,
    kind: str,
    round_number: int,
    repository_root: Path,
    state_path: Path,
    detector_evidence: Path,
    core_backend_root: Path,
    db_host: str,
    db_port: int,
    db_name: str,
    db_user: str,
    trace_dir: Path,
    legacy_info_paths: Sequence[Path],
) -> Mapping[str, Any]:
    trace_path = trace_dir / f"{round_number:03d}-{kind}.strace"
    command = [
        "strace",
        "-f",
        "-qq",
        "-s",
        "4096",
        "-e",
        "trace=%file,connect",
        "-o",
        str(trace_path),
        sys.executable,
        "-m",
        "backend.info_pipeline.runtime_process",
        "--kind",
        kind,
        "--state",
        str(state_path),
        "--detector-evidence",
        str(detector_evidence),
        "--core-backend-root",
        str(core_backend_root),
        "--db-host",
        db_host,
        "--db-port",
        str(db_port),
        "--db-name",
        db_name,
        "--db-user",
        db_user,
    ]
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = (
        f"{repository_root}:{core_backend_root}"
        + (
            f":{environment['PYTHONPATH']}"
            if environment.get("PYTHONPATH")
            else ""
        )
    )
    environment["INFO_DIR"] = (
        "/nonexistent/domeye-static-info-file-access-is-forbidden"
    )
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=repository_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        raise S6AcceptanceError(
            f"{kind} 运行探针失败：{completed.stderr[-4000:]}"
        )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise S6AcceptanceError(
            f"{kind} 运行探针输出不是 JSON"
        ) from exc
    if not isinstance(report, dict) or report.get("status") != "pass":
        raise S6AcceptanceError(f"{kind} 运行探针未通过")
    trace = _trace_summary(
        trace_path,
        legacy_info_paths=legacy_info_paths,
    )
    os.chmod(trace_path, 0o600)
    return {
        "round_number": round_number,
        "process_kind": kind,
        "elapsed_seconds": round(elapsed, 6),
        "runtime_report": report,
        "trace": trace,
        "trace_file": str(trace_path.name),
    }


def _state_journal_inventory(state_dir: Path) -> Mapping[str, Any]:
    journal = state_dir / "journal"
    _require_real_directory(journal, "运行状态日志目录")
    files: Dict[str, str] = {}
    for path in sorted(journal.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file():
            raise S6AcceptanceError(f"运行状态日志边界无效：{path}")
        files[path.name] = _sha256_file(path)
    return {
        "entry_count": len(files),
        "entries_sha256": _sha256_value(files),
    }


def run_s6_acceptance(
    *,
    source_dir: Path,
    current_info_artifact_dir: Path,
    manifest: Mapping[str, Any],
    repository_root: Path,
    core_backend_root: Path,
    evidence_root: Path,
    s5_evidence_dir: Path,
    state_dir: Path,
    trace_dir: Path,
    db_host: str,
    db_port: int,
    db_name: str,
    db_reader: str,
    db_admin: str,
    container: str,
    production_containers: Sequence[str],
    minimum_process_runs: int,
    minimum_observation_seconds: int,
) -> Mapping[str, Any]:
    validate_manifest(manifest)
    for path, label in (
        (source_dir, "只读旧 INFO 来源"),
        (repository_root, "隔离仓库"),
        (core_backend_root, "Core 后端目录"),
        (evidence_root, "证据根目录"),
        (s5_evidence_dir, "S5 证据目录"),
        (state_dir, "运行状态目录"),
        (trace_dir, "S6 追踪目录"),
    ):
        _require_real_directory(path, label)
    if minimum_process_runs < 12:
        raise S6AcceptanceError("S6 普通运行进程样本不得少于 12")
    if minimum_observation_seconds < 60:
        raise S6AcceptanceError("S6 观察周期不得短于 60 秒")

    content_id = str(manifest["content_id"])
    manifest_sha256 = str(manifest["manifest_sha256"])
    receipt = _read_json(
        s5_evidence_dir / "stage-gate-S5.json",
        "S5 阶段回执",
    )
    _verify_s5_receipt(receipt, manifest)
    s5_acceptance = _read_json(
        s5_evidence_dir / "static-info-release-acceptance.json",
        "S5 激活回滚证据",
    )
    if (
        s5_acceptance.get("status") != "pass"
        or s5_acceptance.get("active_content_id") != content_id
        or s5_acceptance.get("previous_release_available") is not True
        or s5_acceptance.get("rollback_tested") is not True
    ):
        raise S6AcceptanceError("S5 激活/文件回滚证据不满足 S6 入口")

    _verify_all_sources(source_dir, manifest)
    source_before = _source_metadata(source_dir, manifest)
    current_artifact_available = (
        current_info_artifact_dir.is_dir()
        and not current_info_artifact_dir.is_symlink()
    )
    state_path = state_dir / "backend-state.json"
    state_before = read_runtime_backend_state(state_path)
    if (
        state_before.content_id != content_id
        or state_before.manifest_sha256 != manifest_sha256
    ):
        raise S6AcceptanceError("普通运行状态与 manifest 身份不一致")
    journal_before = _state_journal_inventory(state_dir)
    if journal_before["entry_count"] < 4:
        raise S6AcceptanceError("运行状态日志不足以复核 S5 切换与回滚")

    failures_before = _failure_evidence_inventory(evidence_root)
    core_before = _core_hash_state(core_backend_root)
    production_before = _docker_fingerprint(production_containers)
    candidate = _candidate_boundary(container)
    admin = _connect(
        host=db_host,
        port=db_port,
        db_name=db_name,
        db_user=db_admin,
        read_only=False,
        application_name="domeye_static_info_s6_acceptance",
    )
    try:
        release_before = _release_state(
            admin,
            content_id,
            manifest_sha256,
        )
        admin.rollback()
        business_before = _business_fingerprint(admin)
        admin.rollback()
    finally:
        admin.close()
    if (
        release_before["status"] != "active"
        or release_before["active_profile"] != "core"
        or int(release_before["release_sk"]) != state_before.release_sk
    ):
        raise S6AcceptanceError("S6 入口的数据库活动 release 不健康")

    detector_evidence = (
        evidence_root / "S4" / "static-info-detector-ab.json"
    )
    _read_json(detector_evidence, "S4 六类检测证据")
    observation_started_at = _utc_timestamp()
    observation_started = time.monotonic()
    runs = []
    round_number = 0
    while (
        len(runs) < minimum_process_runs
        or time.monotonic() - observation_started
        < minimum_observation_seconds
    ):
        kind = _PROCESS_KINDS[round_number % len(_PROCESS_KINDS)]
        round_number += 1
        runs.append(
            _run_traced_process(
                kind=kind,
                round_number=round_number,
                repository_root=repository_root,
                state_path=state_path,
                detector_evidence=detector_evidence,
                core_backend_root=core_backend_root,
                db_host=db_host,
                db_port=db_port,
                db_name=db_name,
                db_user=db_reader,
                trace_dir=trace_dir,
                legacy_info_paths=(
                    source_dir,
                    current_info_artifact_dir,
                ),
            )
        )
        elapsed = time.monotonic() - observation_started
        if (
            len(runs) < minimum_process_runs
            or elapsed < minimum_observation_seconds
        ):
            time.sleep(min(5.0, max(0.0, minimum_observation_seconds - elapsed)))
    observation_elapsed = time.monotonic() - observation_started
    observation_finished_at = _utc_timestamp()

    runtime_direct_reads = sum(
        int(item["trace"]["legacy_info_direct_read_count"])
        for item in runs
    )
    legacy_connections = sum(
        int(item["trace"]["inet_database_connection_count"])
        for item in runs
    )
    mixed_content_runs = sum(
        1
        for item in runs
        if item["runtime_report"].get("content_identity_count") != 1
        or item["runtime_report"].get("content_id") != content_id
        or item["runtime_report"].get("release_sk")
        != state_before.release_sk
        or item["runtime_report"].get(
            "request_path_full_table_load_count"
        )
        != 0
    )
    process_kind_counts = {
        kind: sum(1 for item in runs if item["process_kind"] == kind)
        for kind in _PROCESS_KINDS
    }
    process_kinds_complete = all(
        count >= 3 for count in process_kind_counts.values()
    )

    state_after = read_runtime_backend_state(state_path)
    journal_after = _state_journal_inventory(state_dir)
    _verify_all_sources(source_dir, manifest)
    source_after = _source_metadata(source_dir, manifest)
    failures_after = _failure_evidence_inventory(evidence_root)
    core_after = _core_hash_state(core_backend_root)
    production_after = _docker_fingerprint(production_containers)
    admin = _connect(
        host=db_host,
        port=db_port,
        db_name=db_name,
        db_user=db_admin,
        read_only=False,
        application_name="domeye_static_info_s6_final_state",
    )
    try:
        release_after = _release_state(
            admin,
            content_id,
            manifest_sha256,
        )
        admin.rollback()
        business_after = _business_fingerprint(admin)
        admin.rollback()
    finally:
        admin.close()

    state_preserved = state_before == state_after
    journal_preserved = journal_before == journal_after
    source_preserved = source_before == source_after
    failure_evidence_preserved = failures_before == failures_after
    core_unchanged = core_before == core_after
    production_unchanged = production_before == production_after
    business_unchanged = business_before == business_after
    release_preserved = (
        release_before == release_after
        and release_after["status"] == "active"
        and release_after["active_profile"] == "core"
    )
    observation_complete = (
        len(runs) >= minimum_process_runs
        and observation_elapsed >= minimum_observation_seconds
        and process_kinds_complete
        and mixed_content_runs == 0
        and runtime_direct_reads == 0
        and legacy_connections == 0
    )
    current_available = release_preserved and state_preserved
    previous_available = source_preserved
    rollback_artifact_available = (
        source_after["file_count"] == 24
        and s5_acceptance.get("previous_release_available") is True
    )
    referenced_preserved = all(
        (
            current_available,
            previous_available,
            rollback_artifact_available,
            journal_preserved,
            failure_evidence_preserved,
            core_unchanged,
            production_unchanged,
            business_unchanged,
        )
    )
    status = (
        "pass"
        if observation_complete and referenced_preserved
        else "fail"
    )
    report: Dict[str, Any] = {
        "schema_version": 1,
        "component": "static_info_closure",
        "status": status,
        "final_acceptance_status": status,
        "passed_requirement_count": 12 if status == "pass" else 11,
        "content_id": content_id,
        "manifest_sha256": manifest_sha256,
        "release_sk": state_before.release_sk,
        "runtime_direct_info_file_read_count": runtime_direct_reads,
        "legacy_database_connection_count": legacy_connections,
        "current_release_available": current_available,
        "previous_release_available": previous_available,
        "previous_backend_kind": "file",
        "file_rollback_artifact_available": rollback_artifact_available,
        "observation_period_complete": observation_complete,
        "referenced_content_preserved": referenced_preserved,
        "ordinary_runtime": {
            "backend": "database",
            "implicit_file_fallback": False,
            "state_generation": state_before.generation,
            "active_profile": "core",
            "process_kinds": list(_PROCESS_KINDS),
            "process_kind_counts": process_kind_counts,
            "process_kinds_complete": process_kinds_complete,
            "run_count": len(runs),
            "mixed_content_run_count": mixed_content_runs,
            "minimum_run_count": minimum_process_runs,
            "minimum_observation_seconds": minimum_observation_seconds,
            "observed_seconds": round(observation_elapsed, 6),
            "started_at": observation_started_at,
            "finished_at": observation_finished_at,
            "runs": runs,
        },
        "trace_evidence": {
            "trace_directory": str(trace_dir),
            "trace_file_count": len(runs),
            "trace_inventory_sha256": _sha256_value(
                {
                    item["trace_file"]: item["trace"]["trace_sha256"]
                    for item in runs
                }
            ),
            "legacy_paths_monitored": [
                str(source_dir),
                str(current_info_artifact_dir),
            ],
        },
        "maintenance_verification": {
            "source_verification_is_not_ordinary_runtime": True,
            "source_before": source_before,
            "source_after": source_after,
            "current_file_artifact_directory_available": (
                current_artifact_available
            ),
        },
        "release_before": release_before,
        "release_after": release_after,
        "runtime_state_preserved": state_preserved,
        "runtime_journal_preserved": journal_preserved,
        "runtime_journal": journal_after,
        "business_data_unchanged": business_unchanged,
        "failure_evidence_preserved": failure_evidence_preserved,
        "failure_evidence_before": failures_before,
        "failure_evidence_after": failures_after,
        "core_hash_unchanged": core_unchanged,
        "production_side_effect_count": 0 if production_unchanged else 1,
        "candidate_boundary": candidate,
        "cleanup_performed": False,
        "retention_boundary": {
            "current_release": "preserve",
            "previous_file_backend": "preserve",
            "failed_and_incomplete_evidence": "preserve",
            "source_directory": "read_only_preserve",
            "garbage_collection": "requires_independent_review",
        },
        "contact_plaintext_in_evidence": False,
        "checked_at": _utc_timestamp(),
    }
    if status != "pass":
        raise S6AcceptanceError("S6 运行时收口未达到最终验收合同")
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="执行 static INFO S6 数据库运行时收口",
    )
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--current-info-artifact-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--core-backend-root", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--s5-evidence-dir", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--trace-dir", required=True)
    parser.add_argument("--db-host", required=True)
    parser.add_argument("--db-port", type=int, default=5432)
    parser.add_argument("--db-name", required=True)
    parser.add_argument("--db-reader", required=True)
    parser.add_argument("--db-admin", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument(
        "--production-container",
        action="append",
        default=[],
    )
    parser.add_argument("--minimum-process-runs", type=int, default=12)
    parser.add_argument(
        "--minimum-observation-seconds",
        type=int,
        default=60,
    )
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        manifest = _read_json(Path(args.manifest), "static INFO manifest")
        report = run_s6_acceptance(
            source_dir=Path(args.source_dir),
            current_info_artifact_dir=Path(args.current_info_artifact_dir),
            manifest=manifest,
            repository_root=Path(args.repository_root),
            core_backend_root=Path(args.core_backend_root),
            evidence_root=Path(args.evidence_root),
            s5_evidence_dir=Path(args.s5_evidence_dir),
            state_dir=Path(args.state_dir),
            trace_dir=Path(args.trace_dir),
            db_host=args.db_host,
            db_port=args.db_port,
            db_name=args.db_name,
            db_reader=args.db_reader,
            db_admin=args.db_admin,
            container=args.container,
            production_containers=args.production_container,
            minimum_process_runs=args.minimum_process_runs,
            minimum_observation_seconds=args.minimum_observation_seconds,
        )
        write_text_exclusive(
            Path(args.output),
            json.dumps(
                report,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
        )
    except Exception as exc:
        print(f"S6 验收失败：{exc}", file=sys.stderr)
        return 1
    print(f"static INFO S6 最终收口通过；证据：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
