#!/usr/bin/env python3
"""从完整已验证 MRT manifest 构建有硬上限的 UPDATE RouteEvent pilot。

该命令只生成旁路制品，不连接数据库、不修改原始 MRT，也不把 RIB 或未选择
UPDATE 伪装成已处理数据。输出始终标记 ``pilot_only``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any, Dict, Mapping, Sequence, Tuple


MAX_JSON_BYTES = 128 * 1024 * 1024


class PilotCliError(RuntimeError):
    """pilot 输入、staging 血缘或输出边界非法。"""


def _unique_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PilotCliError(f"JSON 存在重复字段：{key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise PilotCliError(f"JSON 禁止非有限常量：{value}")


def _regular_file(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PilotCliError(f"无法读取{label}：{path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PilotCliError(f"{label}必须是非符号链接普通文件：{path}")
    return metadata


def _directory(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PilotCliError(f"无法读取{label}：{path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PilotCliError(f"{label}必须是非符号链接目录：{path}")
    return metadata


def _read_bytes(path: Path, label: str, maximum: int = MAX_JSON_BYTES) -> bytes:
    initial = _regular_file(path, label)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PilotCliError(f"无法只读打开{label}：{path}") from error
    chunks = []
    total = 0
    try:
        before = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(initial, name) != getattr(before, name) for name in fields):
            raise PilotCliError(f"打开前{label}发生变化")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum:
                raise PilotCliError(f"{label}超过 {maximum} 字节上限")
            chunks.append(block)
        after = os.fstat(descriptor)
        if any(getattr(before, name) != getattr(after, name) for name in fields):
            raise PilotCliError(f"读取期间{label}发生变化")
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _sha256_regular_file(path: Path, label: str) -> str:
    """流式计算普通文件 SHA256，并拒绝链接及读取期间变化。"""

    initial = _regular_file(path, label)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PilotCliError(f"无法只读打开{label}：{path}") from error
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(initial, name) != getattr(before, name) for name in fields):
            raise PilotCliError(f"打开前{label}发生变化")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
        if any(getattr(before, name) != getattr(after, name) for name in fields):
            raise PilotCliError(f"读取期间{label}发生变化")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _load_json(path: Path, label: str) -> Tuple[Dict[str, Any], str]:
    payload = _read_bytes(path, label)
    digest = hashlib.sha256(payload).hexdigest()
    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PilotCliError(f"{label}不是严格 UTF-8 JSON") from error
    if not isinstance(parsed, dict):
        raise PilotCliError(f"{label}顶层必须是对象")
    return parsed, digest


def _atomic_write_new(path: Path, payload: bytes, mode: int = 0o440) -> None:
    _directory(path.parent, "输出目录")
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise PilotCliError(f"输出已存在，拒绝覆盖：{path}")
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    descriptor = None
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PilotCliError(f"写入失败：{path}")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary, path, follow_symlinks=False)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_pipeline(root: Path):
    _directory(root, "pipeline-root")
    expected = (root / "backend/data_pipeline/route_event/__init__.py").resolve(
        strict=True
    )
    sys.path.insert(0, str(root))
    try:
        import backend.data_pipeline.route_event as route_event
    except Exception as error:
        raise PilotCliError("无法从 pipeline-root 导入 RouteEvent 实现") from error
    actual = Path(route_event.__file__).resolve(strict=True)
    if actual != expected:
        raise PilotCliError("实际 RouteEvent 模块不属于显式 pipeline-root")
    return route_event


def _verification_from_summary(
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    summary: Mapping[str, Any],
) -> Mapping[str, Any]:
    manifest_record = summary.get("manifest")
    verification = summary.get("verification")
    if not isinstance(manifest_record, Mapping) or not isinstance(
        verification, Mapping
    ):
        raise PilotCliError("manifest 中文摘要缺少 manifest/verification")
    if manifest_record.get("sha256") != manifest_sha256:
        raise PilotCliError("manifest 文件 SHA256 与已验证中文摘要不一致")
    if manifest_record.get("fingerprint_sha256") != manifest.get(
        "manifest_fingerprint_sha256"
    ):
        raise PilotCliError("manifest fingerprint 与已验证中文摘要不一致")
    if (
        verification.get("verified") is not True
        or verification.get("manifest_fingerprint_sha256")
        != manifest.get("manifest_fingerprint_sha256")
        or verification.get("artifact_count")
        != len(manifest.get("artifacts", ()))
    ):
        raise PilotCliError("中文摘要中的完整 manifest verification 非法")
    return verification


def _output_paths(output_dir: Path) -> Dict[str, Path]:
    return {
        "selection": output_dir / "p0-update-pilot-selection.json",
        "index": output_dir / "p0-route-event-pilot.sqlite3",
        "reconciliation": output_dir
        / "route-event-reconciliation-summary.json",
        "summary": output_dir / "p0-route-event-pilot.summary.zh.json",
        "checksums": output_dir / "SHA256SUMS",
    }


def _run_in_output_dir(args: argparse.Namespace) -> Dict[str, Any]:
    pipeline_root = Path(args.pipeline_root).resolve(strict=True)
    route_event = _load_pipeline(pipeline_root)
    output_dir = Path(args.output_dir)
    _directory(output_dir, "输出目录")
    outputs = _output_paths(output_dir)
    for path in outputs.values():
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise PilotCliError(f"输出已存在，拒绝覆盖：{path}")

    manifest, manifest_sha = _load_json(Path(args.manifest), "完整 artifact manifest")
    manifest_summary, _summary_sha = _load_json(
        Path(args.manifest_summary), "artifact manifest 中文摘要"
    )
    verification = _verification_from_summary(
        manifest, manifest_sha, manifest_summary
    )
    selected_paths = tuple(args.select_relative_path)
    if not selected_paths or len(set(selected_paths)) != len(selected_paths):
        raise PilotCliError("--select-relative-path 必须显式给出且不得重复")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise PilotCliError("完整 manifest artifacts 非数组")
    if any(not isinstance(row, Mapping) for row in artifacts):
        raise PilotCliError("完整 manifest artifacts 含非对象")
    path_map = {}
    for row in artifacts:
        path = row.get("relative_path")
        if not isinstance(path, str) or not path or path in path_map:
            raise PilotCliError("完整 manifest relative_path 非法或重复")
        path_map[path] = row.get("artifact_id")
    missing_paths = sorted(set(selected_paths) - set(path_map))
    if missing_paths:
        raise PilotCliError("选择路径不在完整 manifest：" + ",".join(missing_paths))
    selected_ids = tuple(path_map[path] for path in selected_paths)
    selection = route_event.derive_update_pilot_selection(
        manifest,
        verification,
        selected_ids,
        max_artifact_count=args.max_artifacts,
        max_compressed_bytes=args.max_compressed_bytes,
        max_physical_records=args.max_physical_records,
        max_route_events=args.max_route_events,
        max_spool_bytes=args.max_spool_bytes,
    )
    selection_verification = route_event.verify_update_pilot_selection(
        manifest, verification, selection
    )
    factory = route_event.BgpdumpRecordStreamFactory(
        args.raw_root,
        selection["selected_artifacts"],
        data_profile=selection["data_profile"],
        pilot_limits=selection["limits"],
        bgpdump_path=args.bgpdump_path,
        expected_version=route_event.BGPDUMP_APPROVED_VERSION,
        allowed_binary_sha256=tuple(args.bgpdump_sha256),
        idle_timeout_seconds=args.idle_timeout_seconds,
        exit_timeout_seconds=args.exit_timeout_seconds,
    )
    result = route_event.build_route_event_index(
        outputs["index"],
        manifest=manifest,
        manifest_verification=verification,
        provenance=route_event.ImportProvenance(
            parser_name="bgpdump",
            parser_version=route_event.BGPDUMP_APPROVED_VERSION,
            importer_name="domeye_route_ingest",
            importer_version="1.0.0",
            processing_time_utc=args.processing_time_utc,
            config={
                "mode": "-m -p -v /dev/stdin",
                "strict": True,
                "selection_fingerprint_sha256": selection[
                    "selection_fingerprint_sha256"
                ],
            },
        ),
        record_stream_factory=factory,
        artifact_selection=selection,
        selection_verification=selection_verification,
    )
    with route_event.RouteEventIndex(result.path) as index:
        index_verification = index.verify()
        reconciliation = index.reconciliation_summary(
            raw_root=args.raw_root,
            artifact_selection=selection,
        )
    statistics = factory.statistics_by_artifact
    if len(statistics) != len(selection["selected_artifacts"]) or any(
        row.get("status") != "complete" for row in statistics.values()
    ):
        raise PilotCliError("并非所有 selected UPDATE 都完成 bgpdump 适配")
    max_spool_bytes = selection["limits"]["max_spool_bytes"]
    peak_spool_bytes_by_artifact = {}
    for artifact_id, row in sorted(statistics.items()):
        peak = row.get("peak_spool_bytes")
        if (
            isinstance(peak, bool)
            or not isinstance(peak, int)
            or peak <= 0
            or peak > max_spool_bytes
            or row.get("spool_persistence") != "anonymous_unlinked_fd"
            or row.get("compressed_read_passes") != 1
        ):
            raise PilotCliError(
                "bgpdump 匿名 spool 峰值/持久化边界或 gzip 单遍读取证据非法"
            )
        peak_spool_bytes_by_artifact[artifact_id] = peak

    canonical = lambda value: (route_event.canonical_json(value) + "\n").encode(
        "utf-8"
    )
    _atomic_write_new(outputs["selection"], canonical(selection))
    quality_fields = (
        "raw_reference_unresolved_count",
        "processing_lineage_missing_count",
        "record_hash_verification_failed_count",
        "vp_identity_missing_count",
        "route_event_id_conflict_count",
        "invalid_asn_count",
        "invalid_prefix_count",
        "outside_window_record_count",
    )
    if any(reconciliation.get(field) != 0 for field in quality_fields):
        raise PilotCliError("RouteEvent post-build reconciliation 未通过零缺陷门禁")
    _atomic_write_new(outputs["reconciliation"], canonical(reconciliation))
    emitted_reconciliation, _ = _load_json(
        outputs["reconciliation"], "RouteEvent reconciliation 输出"
    )
    with route_event.RouteEventIndex(result.path) as index:
        index.verify_reconciliation_summary(
            emitted_reconciliation,
            raw_root=args.raw_root,
            artifact_selection=selection,
        )
    summary = {
        "schema_version": 1,
        "summary_kind": "p0_route_event_update_pilot_summary_zh",
        "标题": "P0 RouteEvent UPDATE 有界 Pilot 摘要",
        "状态": "通过",
        "pilot_only": True,
        "production_complete": False,
        "parent_manifest": {
            "sha256": manifest_sha,
            "fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
            "verification": dict(verification),
        },
        "selection_verification": selection_verification,
        "selection_summary": selection["selection_summary"],
        "limits": selection["limits"],
        "anonymous_spool": {
            "persistence": "anonymous_unlinked_fd",
            "max_spool_bytes_per_artifact": max_spool_bytes,
            "peak_spool_bytes_by_artifact": peak_spool_bytes_by_artifact,
            "compressed_read_passes_required": 1,
        },
        "index": index_verification,
        "route_event_reconciliation": {
            "file": outputs["reconciliation"].name,
            "schema_version": reconciliation["schema_version"],
            "index_fingerprint_sha256": reconciliation[
                "index_fingerprint_sha256"
            ],
            "quality_counts": {
                field: reconciliation[field] for field in quality_fields
            },
            "raw_reference_audit": reconciliation[
                "raw_reference_audit"
            ],
        },
        "parser_attestation": factory.parser_attestation,
        "adapter_statistics_by_artifact": statistics,
        "限制": selection["limitations"]
        + [
            "STATE_CHANGE 仅保留 raw_record 与计数，未物化 peer_session_event",
            "AS_PATH 是 route observation snapshot，不构成因果传播结论",
        ],
    }
    _atomic_write_new(outputs["summary"], canonical(summary))

    hashes = {}
    for name in ("selection", "index", "reconciliation", "summary"):
        hashes[outputs[name].name] = _sha256_regular_file(
            outputs[name], f"输出 {name}"
        )
    checksum_text = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(hashes.items())
    ).encode("utf-8")
    _atomic_write_new(outputs["checksums"], checksum_text)
    return {
        "状态": "通过",
        "pilot_only": True,
        "selection_fingerprint_sha256": selection[
            "selection_fingerprint_sha256"
        ],
        "index_fingerprint_sha256": result.summary["index_fingerprint_sha256"],
        "outputs": {key: str(value) for key, value in outputs.items()},
    }


def _same_inode(first: Path, second: Path) -> bool:
    try:
        first_metadata = first.lstat()
        second_metadata = second.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(first_metadata.st_mode)
        and stat.S_ISREG(second_metadata.st_mode)
        and first_metadata.st_dev == second_metadata.st_dev
        and first_metadata.st_ino == second_metadata.st_ino
    )


def run(args: argparse.Namespace) -> Dict[str, Any]:
    """先在隐藏 staging 完成全部复核，再以 SHA256SUMS 作为最后完成标记。"""

    output_dir = Path(args.output_dir)
    _directory(output_dir, "输出目录")
    try:
        existing_entries = tuple(output_dir.iterdir())
    except OSError as error:
        raise PilotCliError("无法枚举 pilot 输出目录") from error
    if existing_entries:
        raise PilotCliError("pilot 输出目录必须为空，拒绝混入未签名文件")
    final_outputs = _output_paths(output_dir)
    for path in final_outputs.values():
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        raise PilotCliError(f"输出已存在，拒绝覆盖：{path}")

    staging = output_dir / (
        f".p0-route-event-pilot.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    try:
        os.mkdir(staging, 0o700)
    except OSError as error:
        raise PilotCliError("无法创建 pilot 隐藏 staging 目录") from error
    staged_outputs = _output_paths(staging)
    published: list[Tuple[Path, Path]] = []
    try:
        staged_args = argparse.Namespace(
            **{**vars(args), "output_dir": str(staging)}
        )
        result = _run_in_output_dir(staged_args)
        for key in (
            "selection",
            "index",
            "reconciliation",
            "summary",
            "checksums",
        ):
            source = staged_outputs[key]
            target = final_outputs[key]
            try:
                os.link(source, target, follow_symlinks=False)
            except OSError as error:
                raise PilotCliError(f"无法发布 pilot 输出：{target}") from error
            published.append((source, target))
        directory_fd = os.open(
            output_dir,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {
            **result,
            "outputs": {
                key: str(value) for key, value in final_outputs.items()
            },
        }
    except BaseException:
        for source, target in reversed(published):
            if _same_inode(source, target):
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
        raise
    finally:
        for path in staged_outputs.values():
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        try:
            staging.rmdir()
        except OSError:
            pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline-root", required=True)
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-summary", required=True)
    parser.add_argument("--select-relative-path", action="append", required=True)
    parser.add_argument("--bgpdump-path", required=True)
    parser.add_argument("--bgpdump-sha256", action="append", required=True)
    parser.add_argument("--processing-time-utc", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-artifacts", type=int, required=True)
    parser.add_argument("--max-compressed-bytes", type=int, required=True)
    parser.add_argument("--max-physical-records", type=int, required=True)
    parser.add_argument("--max-route-events", type=int, required=True)
    parser.add_argument("--max-spool-bytes", type=int, required=True)
    parser.add_argument("--idle-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--exit-timeout-seconds", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = run(parse_args(argv))
    except Exception as error:
        print(f"P0 RouteEvent pilot 失败：{error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
