"""S6 使用的普通数据库运行进程探针。

进程只读取仓库外的 database 状态和候选数据库。文件后端没有隐式回退路径。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .runtime import open_pinned_database_runtime
from .s4 import _evaluate_core_cases, _load_core_classes, _project_info


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


def _read_json(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"运行探针输入缺失或为软链接：{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"运行探针输入顶层必须是对象：{path}")
    return value


def _connect_factory(args):
    def connect():
        import psycopg2

        return psycopg2.connect(
            host=args.db_host,
            port=args.db_port,
            dbname=args.db_name,
            user=args.db_user,
            connect_timeout=10,
            application_name=f"domeye_static_info_s6_{args.kind}",
        )

    return connect


def _probe(
    args,
    detector_evidence: Mapping[str, Any],
) -> Mapping[str, Any]:
    fixture = detector_evidence.get("fixed_input")
    if not isinstance(fixture, dict):
        raise ValueError("S4 检测证据缺少固定输入")
    keys = fixture.get("keys")
    if not isinstance(keys, dict):
        raise ValueError("S4 固定输入缺少键集合")
    with open_pinned_database_runtime(
        args.state,
        _connect_factory(args),
    ) as runtime:
        info = runtime.info
        if args.kind == "api":
            result = {
                "asn": info.as_info[str(keys["origin_as"])],
                "country": info.country[str(keys["country_code"])],
                "prefix": info.prefix_info[str(keys["prefix"])],
            }
            behavior = {
                "query_kind": "api_exact_lookup",
                "result_sha256": _sha256_value(result),
            }
        elif args.kind == "snapshot":
            result = {
                "origin": info.as_info[str(keys["origin_as"])],
                "attacker": info.as_info[str(keys["attacker_as"])],
                "relation": info.as_rel_dict.get(str(keys["origin_as"])),
                "as_prefix": info.as_prefix_dict.get(str(keys["origin_as"])),
            }
            behavior = {
                "query_kind": "release_pinned_snapshot",
                "snapshot_kind": info.snapshot_kind,
                "result_sha256": _sha256_value(result),
            }
        elif args.kind == "detector":
            info.important_domain_dict = (
                runtime.repository.fetch_important_domains_for_prefix(
                    str(keys["prefix"])
                )
            )
            projected = _project_info(info, fixture)
            observed = _evaluate_core_cases(
                projected,
                fixture,
                _load_core_classes(Path(args.core_backend_root)),
            )
            expected = detector_evidence.get("database_backend_results")
            behavior = {
                "query_kind": "six_detector_fixed_input",
                "event_type_count": len(observed),
                "result_sha256": _sha256_value(observed),
                "expected_sha256": _sha256_value(expected),
                "matches_s4_approved_result": observed == expected,
            }
            if observed != expected:
                raise ValueError("数据库检测运行偏离 S4 批准结果")
        elif args.kind == "background":
            triplet = [str(item) for item in keys["triplet"]]
            first = info.triplet_info[triplet[0]]
            second = first[triplet[1]]
            result = {
                "triplet": second[triplet[2]],
                "important_as": info.important_as_dict.get(
                    int(keys["important_as"])
                ),
                "important_domain_count": len(info.important_domain_dict),
            }
            behavior = {
                "query_kind": "background_exact_maintenance",
                "result_sha256": _sha256_value(result),
            }
        else:
            raise ValueError(f"未知运行探针类型：{args.kind}")

        return {
            "schema_version": 1,
            "component": "static_info_runtime_process_probe",
            "status": "pass",
            "process_kind": args.kind,
            "backend": "database",
            "content_id": runtime.content_id,
            "manifest_sha256": runtime.state.manifest_sha256,
            "release_sk": runtime.release_sk,
            "state_generation": runtime.state.generation,
            "active_profile": runtime.repository.active_profile,
            "content_identity_count": 1,
            "request_path_full_table_load_count": (
                info.request_path_full_table_load_count
            ),
            "query_count": runtime.repository.telemetry.query_count,
            "behavior": behavior,
        }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="执行一个不含文件回退的 static INFO 数据库运行探针",
    )
    parser.add_argument(
        "--kind",
        required=True,
        choices=("api", "snapshot", "detector", "background"),
    )
    parser.add_argument("--state", required=True)
    parser.add_argument("--detector-evidence", required=True)
    parser.add_argument("--core-backend-root", required=True)
    parser.add_argument("--db-host", required=True)
    parser.add_argument("--db-port", type=int, default=5432)
    parser.add_argument("--db-name", required=True)
    parser.add_argument("--db-user", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        evidence = _read_json(Path(args.detector_evidence))
        report = _probe(args, evidence)
    except Exception as exc:
        print(f"运行探针失败：{exc}", file=os.sys.stderr)
        return 1
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
