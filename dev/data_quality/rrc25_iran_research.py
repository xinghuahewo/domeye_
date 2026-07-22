#!/usr/bin/env python3
"""伊朗 RRC25 研究协调命令。

四个子命令共用同一 Profile/manifest/mapping/code 身份。``dry-run`` 只读取
JSON 元数据；``execute`` 和 ``resume`` 当前只接受显式 fixture executor，避免
CLI 在尚未接入受控 MRT worker 前误读真实数据或连接数据库；``verify`` 只读
校验研究目录的内容哈希、引用和语义指纹。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence

# 允许从仓库根目录直接执行 ``python3 dev/data_quality/...py``。
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.data_pipeline.research.rrc25_country_outage.coordinator import (
    DEFAULT_MAX_ARTIFACTS_PER_CHUNK,
    ExecutionRecord,
    ResearchCoordinatorError,
    build_worker_plan,
    execute_research,
    load_json_metadata,
    prepare_research_plan,
    resume_research,
    verify_research_run,
)


FIXTURE_SCHEMA_VERSION = "rrc25-iran-fixture-executor/v1"


class FixtureExecutor:
    """只消费 JSON 夹具的 executor；不接受路径、DSN 或数据库句柄。"""

    def __init__(self, payload: Mapping[str, Any]):
        if not isinstance(payload, Mapping):
            raise ResearchCoordinatorError("fixture executor 根节点必须是对象")
        if set(payload) != {"schema_version", "records_by_artifact"}:
            raise ResearchCoordinatorError("fixture executor 顶层字段不闭合")
        if payload.get("schema_version") != FIXTURE_SCHEMA_VERSION:
            raise ResearchCoordinatorError("fixture executor schema_version 不支持")
        records = payload.get("records_by_artifact")
        if not isinstance(records, Mapping):
            raise ResearchCoordinatorError("records_by_artifact 必须是对象")
        normalized: dict[str, tuple[ExecutionRecord, ...]] = {}
        for artifact_id in sorted(records):
            values = records[artifact_id]
            if not isinstance(artifact_id, str) or not isinstance(values, list):
                raise ResearchCoordinatorError("fixture artifact 或 records 非法")
            converted = []
            for index, item in enumerate(values):
                if not isinstance(item, Mapping) or set(item) != {
                    "record_ordinal",
                    "output_record",
                    "new_raw_bytes_read",
                    "temporary_bytes",
                    "database_write_operations",
                }:
                    raise ResearchCoordinatorError(
                        f"fixture {artifact_id}[{index}] 字段不闭合"
                    )
                converted.append(
                    ExecutionRecord(
                        artifact_id=artifact_id,
                        record_ordinal=item["record_ordinal"],
                        output_record=item["output_record"],
                        new_raw_bytes_read=item["new_raw_bytes_read"],
                        temporary_bytes=item["temporary_bytes"],
                        database_write_operations=item[
                            "database_write_operations"
                        ],
                    )
                )
            normalized[artifact_id] = tuple(converted)
        self._records = normalized

    def __call__(
        self, artifact: Mapping[str, Any], start_record_ordinal: int
    ) -> Iterable[ExecutionRecord]:
        artifact_id = artifact.get("artifact_id")
        for record in self._records.get(str(artifact_id), ()):
            if record.record_ordinal >= start_record_ordinal:
                yield record


def _add_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", required=True, help="冻结研究 Profile JSON")
    parser.add_argument("--manifest", required=True, help="父 MRT manifest JSON")
    parser.add_argument(
        "--manifest-verification",
        required=True,
        help="父 manifest 只读验证结果 JSON",
    )
    parser.add_argument("--mapping", required=True, help="冻结 AS 国家映射 JSON")
    parser.add_argument("--code-sha256", required=True, help="处理代码 SHA-256")
    parser.add_argument("--output-root", required=True, help="文件型研究输出根目录")
    parser.add_argument(
        "--pilot-end-exclusive",
        help=(
            "可选 bounded pilot UTC 结束边界；不会改写冻结 Profile，"
            "未处理余下区间固定为 blocking incomplete"
        ),
    )
    parser.add_argument(
        "--maximum-artifacts-per-chunk",
        type=int,
        default=DEFAULT_MAX_ARTIFACTS_PER_CHUNK,
        help="每个有界分块最多输入制品数，默认 5",
    )
    parser.add_argument(
        "--estimated-worker-seconds",
        required=True,
        type=float,
        help="dry-run 的单分块 worker 秒数估算；达到 540 秒即不放行",
    )
    parser.add_argument(
        "--estimated-temporary-bytes",
        required=True,
        type=int,
        help="dry-run 的峰值临时字节估算；达到十进制 5GB 即不放行",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="伊朗 RRC25 国家中断研究的有界文件型协调器"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    dry_run = subparsers.add_parser("dry-run", help="只做元数据解析和资源估算")
    _add_plan_arguments(dry_run)
    dry_run.add_argument(
        "--worker-plan-only",
        action="store_true",
        help="只向 stdout 输出可直接交给只读 worker 的内容寻址计划",
    )
    execute = subparsers.add_parser("execute", help="以注入夹具执行新研究 run")
    _add_plan_arguments(execute)
    execute.add_argument(
        "--fixture-executor",
        required=True,
        help="注入式 record executor JSON；当前不接受真实 MRT 路径",
    )
    resume = subparsers.add_parser("resume", help="从完整 record 检查点恢复")
    _add_plan_arguments(resume)
    resume.add_argument("--fixture-executor", required=True)
    verify = subparsers.add_parser("verify", help="只读校验研究 run")
    _add_plan_arguments(verify)
    return parser


def _load_plan(args: argparse.Namespace, *, allow_existing: bool):
    return prepare_research_plan(
        profile=load_json_metadata(args.profile),
        artifact_manifest=load_json_metadata(args.manifest),
        manifest_verification=load_json_metadata(args.manifest_verification),
        mapping_snapshot=load_json_metadata(args.mapping),
        code_sha256=args.code_sha256,
        output_root=args.output_root,
        maximum_artifacts_per_chunk=args.maximum_artifacts_per_chunk,
        allow_existing_run=allow_existing,
        pilot_end_exclusive=args.pilot_end_exclusive,
        estimated_worker_seconds=args.estimated_worker_seconds,
        estimated_temporary_bytes=args.estimated_temporary_bytes,
    )


def _print_json(value: Mapping[str, Any], *, stream: Any = None) -> None:
    if stream is None:
        stream = sys.stdout
    stream.write(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "dry-run":
            plan = _load_plan(args, allow_existing=False)
            _print_json(
                build_worker_plan(plan) if args.worker_plan_only else plan.to_dict()
            )
            return 0 if plan.ready else 3
        if args.command == "execute":
            plan = _load_plan(args, allow_existing=False)
            if not plan.ready:
                _print_json(plan.to_dict(), stream=sys.stderr)
                return 3
            executor = FixtureExecutor(load_json_metadata(args.fixture_executor))
            result = execute_research(plan, executor)
            _print_json(result.to_dict())
            return 0 if result.status == "completed" else 4
        if args.command == "resume":
            plan = _load_plan(args, allow_existing=True)
            if not plan.ready:
                _print_json(plan.to_dict(), stream=sys.stderr)
                return 3
            executor = FixtureExecutor(load_json_metadata(args.fixture_executor))
            result = resume_research(plan, executor)
            _print_json(result.to_dict())
            return 0 if result.status == "completed" else 4
        if args.command == "verify":
            plan = _load_plan(args, allow_existing=True)
            if not plan.ready:
                _print_json(plan.to_dict(), stream=sys.stderr)
                return 3
            result = verify_research_run(
                plan.run_directory, expected_bindings=plan.bindings
            )
            _print_json(result.to_dict())
            return 0
        raise ResearchCoordinatorError("未知子命令")
    except (ResearchCoordinatorError, FileExistsError, OSError, ValueError) as error:
        _print_json(
            {
                "ok": False,
                "error_type": type(error).__name__,
                "message_zh": str(error),
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
