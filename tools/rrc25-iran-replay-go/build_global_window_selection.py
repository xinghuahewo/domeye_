#!/usr/bin/env python3
"""为 RRC25 长窗 runner 构建 create-only 隔离输入与 selection。"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from typing import Any, Dict, Mapping


UTC = timezone.utc
SLOT = timedelta(minutes=5)
MAX_JSON_BYTES = 128 * 1024 * 1024


class SelectionBuildError(RuntimeError):
    """输入身份、连续性或 create-only 边界不成立。"""


def _regular(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as error:
        raise SelectionBuildError(f"无法读取{label}：{path}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SelectionBuildError(f"{label}必须是非符号链接普通文件：{path}")
    return info


def _read_json(path: Path, label: str) -> tuple[Dict[str, Any], str]:
    info = _regular(path, label)
    if info.st_size > MAX_JSON_BYTES:
        raise SelectionBuildError(f"{label}超过大小上限")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SelectionBuildError(f"{label}不是严格 UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise SelectionBuildError(f"{label}顶层必须是对象")
    return value, hashlib.sha256(raw).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _artifact_id(file_sha256: str) -> str:
    identity = json.dumps(
        {"schema": "artifact_id_v1", "file_sha256": file_sha256},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "art_v1_" + hashlib.sha256(identity).hexdigest()[:32]


def _utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SelectionBuildError(f"{field}不是有效 UTC 时间") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise SelectionBuildError(f"{field}必须为 UTC")
    parsed = parsed.astimezone(UTC)
    if parsed.second or parsed.microsecond or parsed.minute % 5:
        raise SelectionBuildError(f"{field}必须按五分钟对齐")
    return parsed


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _relative(kind: str, value: datetime) -> str:
    month = value.strftime("%Y.%m")
    stamp = value.strftime("%Y%m%d.%H%M")
    family = "bview" if kind == "rib" else "updates"
    return f"rrc25/{month}/{family}.{stamp}.gz"


def _verify_repair_gzip(path: Path) -> None:
    try:
        with gzip.open(path, "rb") as source:
            while source.read(1024 * 1024):
                pass
    except (OSError, EOFError) as error:
        raise SelectionBuildError(f"修复文件 gzip 校验失败：{path}") from error


def _manifest_artifacts(manifest: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    rows = manifest.get("artifacts")
    if not isinstance(rows, list):
        raise SelectionBuildError("源 manifest 缺少 artifacts")
    result: Dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise SelectionBuildError("源 manifest artifacts 含非对象")
        relative = row.get("relative_path")
        if not isinstance(relative, str) or relative in result:
            raise SelectionBuildError("源 manifest relative_path 非法或重复")
        result[relative] = row
    return result


def _copy_artifact(row: Mapping[str, Any]) -> Dict[str, Any]:
    keys = (
        "artifact_id",
        "artifact_time_utc",
        "artifact_type",
        "collector_id",
        "compression",
        "file_sha256",
        "relative_path",
        "size_bytes",
    )
    result = {key: row.get(key) for key in keys}
    if (
        result["collector_id"] != "rrc25"
        or result["compression"] != "gz"
        or not isinstance(result["size_bytes"], int)
        or result["size_bytes"] <= 0
    ):
        raise SelectionBuildError(f"源 artifact 字段非法：{result}")
    return result


def build(args: argparse.Namespace) -> Mapping[str, Any]:
    raw_root = Path(args.raw_root).resolve(strict=True)
    repair_root = Path(args.repair_root).resolve(strict=True)
    output_root = Path(args.output_root)
    selection_output = Path(args.selection_output)
    if output_root.exists() or selection_output.exists():
        raise SelectionBuildError("输出根或 selection 已存在，拒绝覆盖")
    start = _utc(args.window_start_utc, "window-start-utc")
    end = _utc(args.window_end_exclusive_utc, "window-end-exclusive-utc")
    if not start < end:
        raise SelectionBuildError("窗口必须为正半开区间")
    manifest, manifest_sha = _read_json(Path(args.source_manifest), "源 manifest")
    artifacts = _manifest_artifacts(manifest)
    rib_relative = _relative("rib", start)
    rib_source = raw_root / rib_relative
    rib_row = artifacts.get(rib_relative)
    if rib_row is None or rib_row.get("artifact_type") != "rib":
        raise SelectionBuildError("源 manifest 缺少窗口起点 RIB")

    temporary = output_root.with_name(output_root.name + f".tmp-{os.getpid()}")
    if temporary.exists():
        raise SelectionBuildError(f"临时输出已存在：{temporary}")
    temporary.mkdir(parents=True, mode=0o750)
    repaired = []
    updates = []
    try:
        cursor = start
        expected_rows = [("rib", cursor, rib_relative, rib_row, rib_source)]
        while cursor < end:
            relative = _relative("update", cursor)
            row = artifacts.get(relative)
            source = raw_root / relative
            if row is None:
                source = repair_root / Path(relative).name
                info = _regular(source, "修复 UPDATE")
                _verify_repair_gzip(source)
                file_sha = _sha256(source)
                row = {
                    "artifact_id": _artifact_id(file_sha),
                    "artifact_time_utc": _utc_text(cursor),
                    "artifact_type": "update",
                    "collector_id": "rrc25",
                    "compression": "gz",
                    "file_sha256": file_sha,
                    "relative_path": relative,
                    "size_bytes": info.st_size,
                }
                repaired.append(relative)
            expected_rows.append(("update", cursor, relative, row, source))
            cursor += SLOT

        for kind, at, relative, row, source in expected_rows:
            info = _regular(source, "冻结输入")
            artifact = _copy_artifact(row)
            if (
                artifact["artifact_type"] != kind
                or artifact["artifact_time_utc"] != _utc_text(at)
                or artifact["relative_path"] != relative
                or artifact["size_bytes"] != info.st_size
            ):
                raise SelectionBuildError(f"artifact 身份与窗口不一致：{relative}")
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            os.link(source, target)
            if kind == "rib":
                rib = artifact
            else:
                updates.append(artifact)

        output_root.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        temporary.rename(output_root)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    selection = {
        "schema_version": "rrc25-global-window-selection/v1",
        "collector_id": "rrc25",
        "window_start_utc": _utc_text(start),
        "window_end_exclusive_utc": _utc_text(end),
        "timezone": "UTC",
        "source_manifest_sha256": manifest_sha,
        "source_manifest_status": "verified_manifest_plus_isolated_repairs",
        "repair_artifact_count": len(repaired),
        "rib": rib,
        "updates": updates,
        "input_notes": [
            "原始 MRT 目录保持只读，本次输入由同文件系统硬链接冻结。",
            "源 manifest 未准入的损坏槽使用隔离下载并重新计算 artifact 身份。",
            "运行解析器仍会流式核验 selection 中记录的完整 SHA-256。",
        ],
    }
    selection_output.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    raw = (
        json.dumps(selection, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(selection_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {
        "status": "complete",
        "input_root": str(output_root),
        "selection": str(selection_output),
        "selection_sha256": hashlib.sha256(raw).hexdigest(),
        "source_manifest_sha256": manifest_sha,
        "rib_count": 1,
        "update_count": len(updates),
        "repair_artifact_count": len(repaired),
        "repaired_relative_paths": repaired,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--repair-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--selection-output", required=True)
    parser.add_argument("--window-start-utc", required=True)
    parser.add_argument("--window-end-exclusive-utc", required=True)
    args = parser.parse_args()
    try:
        result = build(args)
    except SelectionBuildError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
