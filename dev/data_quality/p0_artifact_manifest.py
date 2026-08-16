#!/usr/bin/env python3
"""在服务器 staging 上生成并复核 P0 原始 MRT 文件级 manifest。

CLI 不导入当前工作树中的 ``backend`` 包，而是从显式 ``pipeline-root``
加载 scanner。数据 manifest 只由数据档、allowlist 和原始文件决定；CLI、
scanner 与 profile 的代码/配置哈希仅进入旁路中文摘要。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from types import ModuleType
from typing import Any, Dict, Mapping, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCANNER_RELATIVE_PATH = Path("backend/data_pipeline/route_event/artifacts.py")
CLI_RELATIVE_PATH = Path("dev/data_quality/p0_artifact_manifest.py")
MAX_PROFILE_BYTES = 1024 * 1024


class CliError(RuntimeError):
    """staging 参数、输入或输出不满足 P0 发布约束。"""


def _reject_json_constant(value: str) -> None:
    raise CliError(f"data-profile 禁止非有限 JSON 常量：{value}")


def _unique_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CliError(f"data-profile 存在重复 JSON 字段：{key}")
        result[key] = value
    return result


def _lstat_regular_file(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise CliError(f"无法读取{label}：{path}") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise CliError(f"{label}不得是符号链接：{path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise CliError(f"{label}必须是普通文件：{path}")
    return metadata


def _lstat_directory(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise CliError(f"无法读取{label}：{path}") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise CliError(f"{label}不得是符号链接：{path}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise CliError(f"{label}必须是目录：{path}")
    return metadata


def _read_regular_file(path: Path, label: str, *, maximum_bytes: int | None = None) -> bytes:
    initial = _lstat_regular_file(path, label)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CliError(f"无法只读打开{label}：{path}") from error
    chunks = []
    total = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CliError(f"{label}必须是普通文件：{path}")
        immutable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(initial, field) != getattr(before, field) for field in immutable_fields):
            raise CliError(f"打开前{label}发生变化：{path}")
        while True:
            block = os.read(descriptor, 128 * 1024)
            if not block:
                break
            total += len(block)
            if maximum_bytes is not None and total > maximum_bytes:
                raise CliError(f"{label}超过 {maximum_bytes} 字节限制")
            chunks.append(block)
        after = os.fstat(descriptor)
    except OSError as error:
        raise CliError(f"读取{label}失败：{path}") from error
    finally:
        os.close(descriptor)
    if any(getattr(before, field) != getattr(after, field) for field in immutable_fields):
        raise CliError(f"读取期间{label}发生变化：{path}")
    return b"".join(chunks)


def _sha256_file(path: Path, label: str) -> str:
    payload = _read_regular_file(path, label)
    return hashlib.sha256(payload).hexdigest()


def load_data_profile(path: os.PathLike[str] | str) -> Tuple[Dict[str, Any], str]:
    """严格加载普通 JSON 文件，并返回对象及完整文件 SHA256。"""

    profile_path = Path(path)
    payload = _read_regular_file(
        profile_path, "data-profile", maximum_bytes=MAX_PROFILE_BYTES
    )
    digest = hashlib.sha256(payload).hexdigest()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CliError("data-profile 必须使用严格 UTF-8 编码") from error
    try:
        profile = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as error:
        raise CliError(f"data-profile 不是合法 JSON：{error.msg}") from error
    if not isinstance(profile, dict):
        raise CliError("data-profile 顶层必须是 JSON 对象")
    return profile, digest


def load_scanner_module(
    pipeline_root: os.PathLike[str] | str,
) -> Tuple[ModuleType, Path, str]:
    """从 staging 根动态导入 scanner，并核对实际模块文件。"""

    root = Path(pipeline_root)
    _lstat_directory(root, "pipeline-root")
    scanner_path = root / SCANNER_RELATIVE_PATH
    _lstat_regular_file(scanner_path, "artifact scanner")
    expected_path = scanner_path.resolve(strict=True)
    module_name = "domeye_p0_artifacts_" + hashlib.sha256(
        os.fsencode(expected_path)
    ).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(module_name, scanner_path)
    if spec is None or spec.loader is None:
        raise CliError(f"无法创建 artifact scanner 导入规范：{scanner_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise CliError(f"无法从 pipeline-root 导入 artifact scanner：{scanner_path}") from error
    module_file = getattr(module, "__file__", None)
    if not isinstance(module_file, str):
        raise CliError("artifact scanner 未暴露实际模块路径")
    actual_path = Path(module_file).resolve(strict=True)
    if actual_path != expected_path:
        raise CliError("artifact scanner 实际模块路径与 pipeline-root 不一致")
    for function_name in (
        "scan_mrt_artifacts",
        "atomic_write_manifest",
        "verify_artifact_manifest",
        "canonical_json",
    ):
        if not callable(getattr(module, function_name, None)):
            raise CliError(f"artifact scanner 缺少函数：{function_name}")
    return module, scanner_path, _sha256_file(scanner_path, "artifact scanner")


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise CliError(f"无法检查输出路径：{path}") from error
    return True


def _output_paths(output: os.PathLike[str] | str) -> Tuple[Path, Path, Path]:
    manifest_path = Path(output)
    if (
        not manifest_path.name
        or "\n" in manifest_path.name
        or "\r" in manifest_path.name
        or "\\" in manifest_path.name
    ):
        raise CliError("output 文件名非法")
    summary_path = manifest_path.with_name(manifest_path.stem + ".summary.zh.json")
    checksum_path = manifest_path.parent / "SHA256SUMS"
    paths = (manifest_path, summary_path, checksum_path)
    if len(set(paths)) != len(paths):
        raise CliError("output 与摘要或 SHA256SUMS 路径冲突")
    _lstat_directory(manifest_path.parent, "输出目录")
    existing = [str(path) for path in paths if _path_exists(path)]
    if existing:
        raise CliError("输出目标已存在，拒绝覆盖：" + ", ".join(existing))
    return paths


def _atomic_write_new(path: Path, payload: bytes, *, mode: int = 0o640) -> Path:
    """以 hard-link 发布完成写入的临时文件，绝不覆盖已有路径。"""

    _lstat_directory(path.parent, "输出目录")
    if _path_exists(path):
        raise FileExistsError(f"目标已存在，拒绝覆盖：{path}")
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CliError(f"写入输出失败：{path}")
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
    return path


def _canonical_bytes(module: ModuleType, value: Mapping[str, Any]) -> bytes:
    return (module.canonical_json(dict(value)) + "\n").encode("utf-8")


def _build_summary(
    manifest: Mapping[str, Any],
    verification: Mapping[str, Any],
    *,
    profile_name: str,
    profile_sha256: str,
    scanner_sha256: str,
    cli_sha256: str,
    manifest_name: str,
    manifest_sha256: str,
    integrity_workers: int,
) -> Dict[str, Any]:
    summary = manifest["summary"]
    coverage = manifest["coverage"]
    by_type = summary["by_artifact_type"]
    invalid_summary = summary["invalid_in_window"]
    invalid_records = manifest["invalid_in_window"]
    return {
        "schema_version": 1,
        "summary_kind": "p0_raw_artifact_manifest_summary_zh",
        "标题": "P0 原始 MRT 制品文件与压缩完整性摘要",
        "结论": (
            "窗口内原始制品存在内容无效文件，已完整哈希并隔离为 parse_failed；未发现文件的槽保持 source_unavailable"
            if invalid_summary["file_count"]
            else (
                "窗口内原始制品完整"
                if coverage["coverage_status"] == "complete"
                else "窗口内原始制品部分可用，缺槽保持 source_unavailable"
            )
        ),
        "data_profile": {
            "id": manifest["data_profile"]["id"],
            "window_start": manifest["data_profile"]["window_start"],
            "window_end_exclusive": manifest["data_profile"]["window_end_exclusive"],
            "timezone": manifest["data_profile"]["timezone"],
        },
        "provenance": {
            "data_profile": {"file_name": profile_name, "sha256": profile_sha256},
            "scanner": {
                "relative_path": SCANNER_RELATIVE_PATH.as_posix(),
                "sha256": scanner_sha256,
                "module_path_verified": True,
            },
            "cli": {
                "relative_path": CLI_RELATIVE_PATH.as_posix(),
                "sha256": cli_sha256,
            },
            "execution": {
                "integrity_workers": integrity_workers,
                "identity_effect": "none",
            },
        },
        "manifest": {
            "file_name": manifest_name,
            "sha256": manifest_sha256,
            "fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
        },
        "artifact_counts": {
            "total": summary["artifact_count"],
            "update": by_type["update"]["artifact_count"],
            "rib": by_type["rib"]["artifact_count"],
            "size_bytes": summary["size_bytes"],
        },
        "directory_scope": manifest["scan_policy"]["directory_scope"],
        "coverage": coverage,
        "invalid_in_window": {
            "file_count": invalid_summary["file_count"],
            "size_bytes": invalid_summary["size_bytes"],
            "by_missing_reason": invalid_summary["by_missing_reason"],
            "records": invalid_records,
        },
        "excluded_out_of_window": summary["excluded_out_of_window"],
        "verification": dict(verification),
        "说明": [
            "只枚举与固定半开窗口相交的 UTC 月目录；其他月份 excluded_without_inventory，不进入数量或指纹。",
            "所选月份内、精确窗口外的制品不进入 artifact 或可用槽，默认不读取内容、不计算哈希。",
            "窗口内空文件、压缩 magic 不匹配或压缩流无法完整读取到 EOF/CRC 的文件会完整哈希并进入 invalid_in_window，不进入 artifacts 或可用槽。",
            "未发现文件的槽标记 source_unavailable；发现但完整性失败的槽标记 parse_failed；二者都不补成 0。",
            "压缩容器完整性通过不等于 MRT/BGP 语义可解析；本 manifest 不构成因果传播证据。",
        ],
    }


def run(args: argparse.Namespace) -> Dict[str, Any]:
    if not args.verify:
        raise CliError("staging 发布必须显式提供 --verify")
    if len(set(args.collector)) != len(args.collector):
        raise CliError("--collector 不得重复")
    manifest_path, summary_path, checksum_path = _output_paths(args.output)
    profile, profile_sha256 = load_data_profile(args.data_profile)
    scanner, scanner_path, scanner_sha256 = load_scanner_module(args.pipeline_root)
    cli_path = Path(__file__)
    _lstat_regular_file(cli_path, "P0 artifact CLI")
    cli_sha256 = _sha256_file(cli_path, "P0 artifact CLI")

    manifest = scanner.scan_mrt_artifacts(
        args.raw_root,
        profile,
        args.collector,
        strict_out_of_window=args.strict_out_of_window,
        integrity_workers=args.integrity_workers,
    )
    scanner.atomic_write_manifest(manifest_path, manifest)
    verification = scanner.verify_artifact_manifest(
        args.raw_root,
        manifest,
        integrity_workers=args.integrity_workers,
    )
    manifest_sha256 = _sha256_file(manifest_path, "manifest 输出")

    summary = _build_summary(
        manifest,
        verification,
        profile_name=Path(args.data_profile).name,
        profile_sha256=profile_sha256,
        scanner_sha256=scanner_sha256,
        cli_sha256=cli_sha256,
        manifest_name=manifest_path.name,
        manifest_sha256=manifest_sha256,
        integrity_workers=args.integrity_workers,
    )
    _atomic_write_new(summary_path, _canonical_bytes(scanner, summary))
    summary_sha256 = _sha256_file(summary_path, "中文摘要输出")
    checksums = (
        f"{manifest_sha256}  {manifest_path.name}\n"
        f"{summary_sha256}  {summary_path.name}\n"
    ).encode("utf-8")
    _atomic_write_new(checksum_path, checksums)

    # 发布后再次读取两个输出，避免把未落稳的摘要或错误校验和当成成功。
    if _sha256_file(manifest_path, "manifest 输出") != manifest_sha256:
        raise CliError("manifest 输出在发布后发生变化")
    if _sha256_file(summary_path, "中文摘要输出") != summary_sha256:
        raise CliError("中文摘要输出在发布后发生变化")
    return {
        "状态": "通过",
        "manifest": str(manifest_path),
        "中文摘要": str(summary_path),
        "SHA256SUMS": str(checksum_path),
        "manifest_fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
        "artifact_count": manifest["summary"]["artifact_count"],
        "scanner_path": str(scanner_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成并复核 P0 原始 MRT 文件级 manifest")
    parser.add_argument("--raw-root", required=True, help="包含 collector 子目录的只读根目录")
    parser.add_argument("--data-profile", required=True, help="固定数据档 JSON")
    parser.add_argument(
        "--collector",
        action="append",
        required=True,
        help="允许扫描的 collector，可重复提供但值不得重复",
    )
    parser.add_argument("--output", required=True, help="新 manifest JSON 路径")
    parser.add_argument(
        "--pipeline-root",
        default=str(PROJECT_ROOT),
        help="包含 backend/data_pipeline 的 staging 项目根",
    )
    parser.add_argument(
        "--integrity-workers",
        type=int,
        default=1,
        help="压缩流完整性校验并发数，必须为 1..32；只影响执行速度，不进入 manifest 身份",
    )
    parser.add_argument(
        "--strict-out-of-window",
        action="store_true",
        help="遇到合法命名的窗口外文件时直接失败；默认只计数排除",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="强制发布后重新扫描并校验；staging 发布必须显式提供",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except Exception as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
