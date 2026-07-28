"""static INFO 清点、质量探针和候选库导入命令。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .loader import DockerPsql, LoadError, load_core_files
from .full_loader import load_full_files
from .manifest import ManifestError, build_manifest, write_manifest
from .output import write_text_exclusive
from .quality import QualityError, probe_core_files, write_quality_report
from .shadow import compare_shadow_backends


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_SQL = (
    REPOSITORY_ROOT / "deploy" / "database" / "sql" / "info-schema-v1.sql"
)
DEFAULT_FULL_SCHEMA_SQL = (
    REPOSITORY_ROOT / "deploy" / "database" / "sql" / "info-schema-v2.sql"
)


def _read_json(path: os.PathLike[str] | str, label: str) -> Mapping[str, Any]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"{label} 必须是普通文件且禁止软链接：{source}")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} 读取失败：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} 顶层必须是 JSON 对象")
    return value


def _write_result(value: Mapping[str, Any], output: Optional[str]) -> None:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if output:
        write_text_exclusive(output, text)
    else:
        sys.stdout.write(text)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.info_pipeline",
        description="Domeye static INFO 离线清点与候选库导入工具",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest_parser = subparsers.add_parser(
        "manifest",
        help="严格清点 24 个数据文件并生成内容 manifest",
    )
    manifest_parser.add_argument("--source-dir", required=True)
    manifest_parser.add_argument("--source-release-label", required=True)
    manifest_parser.add_argument("--output", required=True)

    probe_parser = subparsers.add_parser(
        "probe",
        help="对四个一期核心文件执行阻断与披露质量规则",
    )
    probe_parser.add_argument("--source-dir", required=True)
    probe_parser.add_argument("--manifest", required=True)
    probe_parser.add_argument("--output", required=True)

    load_parser = subparsers.add_parser(
        "load-core",
        help="把四个一期核心文件导入离线候选 PostgreSQL 容器",
    )
    load_parser.add_argument("--source-dir", required=True)
    load_parser.add_argument("--manifest", required=True)
    load_parser.add_argument("--quality-report", required=True)
    load_parser.add_argument("--container", required=True)
    load_parser.add_argument("--db-user", required=True)
    load_parser.add_argument("--db-name", required=True)
    load_parser.add_argument(
        "--schema-sql",
        default=str(DEFAULT_SCHEMA_SQL),
    )
    load_parser.add_argument("--code-commit")
    load_parser.add_argument("--result")

    full_parser = subparsers.add_parser(
        "load-full",
        help="在同一 S1 shadow release 上装载其余 20 文件并闭合全部 24 文件",
    )
    full_parser.add_argument("--source-dir", required=True)
    full_parser.add_argument("--manifest", required=True)
    full_parser.add_argument("--container", required=True)
    full_parser.add_argument("--db-user", required=True)
    full_parser.add_argument("--db-name", required=True)
    full_parser.add_argument(
        "--schema-sql",
        default=str(DEFAULT_FULL_SCHEMA_SQL),
    )
    full_parser.add_argument("--quality-output", required=True)
    full_parser.add_argument("--result", required=True)

    shadow_parser = subparsers.add_parser(
        "shadow-diff",
        help="对同一 S2 content_id 执行文件/数据库查询与快照全量语义对账",
    )
    shadow_parser.add_argument("--source-dir", required=True)
    shadow_parser.add_argument("--manifest", required=True)
    shadow_parser.add_argument("--container", required=True)
    shadow_parser.add_argument("--db-user", required=True)
    shadow_parser.add_argument("--db-name", required=True)
    shadow_parser.add_argument("--output", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "manifest":
            manifest = build_manifest(
                args.source_dir,
                source_release_label=args.source_release_label,
            )
            write_manifest(manifest, args.output)
            _write_result(
                {
                    "status": "completed",
                    "content_id": manifest["content_id"],
                    "manifest_sha256": manifest["manifest_sha256"],
                    "file_count": manifest["file_count"],
                    "output": str(Path(args.output)),
                },
                None,
            )
            return 0

        if args.command == "probe":
            manifest = _read_json(args.manifest, "static INFO manifest")
            report = probe_core_files(args.source_dir, manifest)
            write_quality_report(report, args.output)
            _write_result(
                {
                    "status": report["status"],
                    "content_id": report["content_id"],
                    "blocking_failure_count": report[
                        "blocking_failure_count"
                    ],
                    "output": str(Path(args.output)),
                },
                None,
            )
            return 0 if report["status"] == "pass" else 1

        if args.command == "load-core":
            manifest = _read_json(args.manifest, "static INFO manifest")
            quality = _read_json(args.quality_report, "static INFO 质量报告")
            database = DockerPsql(args.container, args.db_user, args.db_name)
            result = load_core_files(
                args.source_dir,
                manifest,
                quality,
                database,
                schema_sql=args.schema_sql,
                code_commit=args.code_commit,
            )
            _write_result(result, args.result)
            return 0

        if args.command == "load-full":
            quality_output = Path(args.quality_output)
            result_output = Path(args.result)
            if quality_output.resolve(strict=False) == result_output.resolve(
                strict=False
            ):
                raise ValueError("S2 质量报告和导入结果不能使用同一路径")
            for label, path in (
                ("S2 质量报告", quality_output),
                ("S2 导入结果", result_output),
            ):
                if path.exists() or path.is_symlink():
                    raise ValueError(f"{label}已存在，拒绝覆盖：{path}")
                if not path.parent.is_dir() or path.parent.is_symlink():
                    raise ValueError(f"{label}父目录无效：{path.parent}")
            manifest = _read_json(args.manifest, "static INFO manifest")
            database = DockerPsql(args.container, args.db_user, args.db_name)
            result, quality = load_full_files(
                args.source_dir,
                manifest,
                database,
                schema_sql=args.schema_sql,
            )
            _write_result(quality, args.quality_output)
            _write_result(result, args.result)
            return 0 if result["status"] in {
                "completed",
                "already_completed",
            } else 1
        if args.command == "shadow-diff":
            manifest = _read_json(args.manifest, "static INFO manifest")
            database = DockerPsql(args.container, args.db_user, args.db_name)
            report = compare_shadow_backends(
                args.source_dir,
                manifest,
                database,
            )
            _write_result(report, args.output)
            return 0 if report["status"] == "pass" else 1
        raise AssertionError(f"未知命令：{args.command}")
    except (ManifestError, QualityError, LoadError, OSError, ValueError) as exc:
        sys.stderr.write(f"错误：{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
