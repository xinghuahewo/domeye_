#!/usr/bin/env python3
"""生成 P0 单份最终候选与 D2 有界 A/B 重放的 assurance 摘要。

本程序不生成候选、不连接数据库、不读取原始 MRT。它只读复核一份最终
D2/D3/D4/Metric/RouteEvent 候选，以及两份由外部执行产生的 D2 64 条样本
候选。最终候选全部执行 SHA256 闭包校验；D3、D4、Metric 与 RouteEvent
执行各自完整的小型语义复核，最终 D2 只读取各 JSONL 的确定性 64 条前缀。
两份 D2 样本则完整流式读取，并比较完整候选字节、稳定 ID、记录数、摘要与
指纹。

候选目录的不同路径或 inode 不能证明独立执行。因此 A/B 每侧必须另附一份
严格 JSON 执行记录，记录绑定候选输出目录和 ``SHA256SUMS``。摘要只把它
表述为“有外部执行记录支撑”，并明确不构成密码学独立性证明。普通文件复制
在没有可信外部执行记录时无法由制品字节本身识别，不能据本摘要宣称独立
重跑。
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dev.data_quality import p0_reproducibility as repro


SCHEMA_VERSION = "p0_single_run_assurance_v1"
ASSURANCE_MODE = "final_single_candidate_plus_d2_bounded_replay_v1"
OUTPUT_JSON = "assurance-summary.json"
OUTPUT_SUMMARY = "摘要.md"
OUTPUT_CHECKSUMS = "SHA256SUMS"
SAMPLE_MAX_EVENTS = 64
EXECUTION_EVIDENCE_SCHEMA = "p0_d2_bounded_replay_execution_v1"
EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
EXECUTION_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "execution_id",
        "started_at",
        "finished_at",
        "exit_code",
        "output_dir",
        "command_argv_sha256",
        "stdout_sha256",
        "stderr_sha256",
        "candidate_sha256sums_sha256",
    }
)


class AssuranceError(repro.ReproducibilityError):
    """输入不足以形成可信的单份候选 assurance。"""


def _canonical_bytes(value: Any) -> bytes:
    return repro._canonical_bytes(value)


def _valid_sha(value: Any, label: str) -> str:
    try:
        return repro._valid_sha(value, label)
    except repro.ReproducibilityError as error:
        raise AssuranceError(str(error)) from error


def _parse_execution_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        raise AssuranceError("{}必须是带时区的 ISO 8601 时间".format(label))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AssuranceError("{}不是有效时间".format(label)) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AssuranceError("{}必须带时区".format(label))
    return parsed


def _load_execution_evidence(
    path: Path,
    *,
    label: str,
    candidate: repro.VerifiedDirectory,
) -> Tuple[Dict[str, Any], Dict[str, Any], Tuple[int, ...]]:
    initial = repro._lstat_regular(path, label)
    payload = repro._read_regular(path, label, 1024 * 1024)
    value = repro._load_json_bytes(payload, label)
    if payload != _canonical_bytes(value):
        raise AssuranceError("{}必须使用规范 JSON 字节".format(label))
    if set(value) != EXECUTION_EVIDENCE_FIELDS:
        raise AssuranceError(
            "{}字段集合非法；缺少={}，多出={}".format(
                label,
                sorted(EXECUTION_EVIDENCE_FIELDS - set(value)),
                sorted(set(value) - EXECUTION_EVIDENCE_FIELDS),
            )
        )
    if value.get("schema_version") != EXECUTION_EVIDENCE_SCHEMA:
        raise AssuranceError("{} schema_version 非法".format(label))
    execution_id = value.get("execution_id")
    if not isinstance(execution_id, str) or EXECUTION_ID_RE.fullmatch(execution_id) is None:
        raise AssuranceError("{} execution_id 非法".format(label))
    started_at = _parse_execution_time(value.get("started_at"), label + " started_at")
    finished_at = _parse_execution_time(value.get("finished_at"), label + " finished_at")
    if finished_at <= started_at:
        raise AssuranceError("{} finished_at 必须晚于 started_at".format(label))
    if value.get("exit_code") != 0:
        raise AssuranceError("{} exit_code 必须为 0".format(label))
    output_dir = value.get("output_dir")
    if not isinstance(output_dir, str) or not output_dir:
        raise AssuranceError("{} output_dir 非法".format(label))
    try:
        evidence_output = Path(output_dir).expanduser().resolve(strict=True)
        candidate_output = candidate.path.resolve(strict=True)
    except OSError as error:
        raise AssuranceError("{} output_dir 无法解析".format(label)) from error
    if evidence_output != candidate_output:
        raise AssuranceError("{} output_dir 未绑定当前样本候选".format(label))
    for field in (
        "command_argv_sha256",
        "stdout_sha256",
        "stderr_sha256",
        "candidate_sha256sums_sha256",
    ):
        _valid_sha(value.get(field), "{} {}".format(label, field))
    if value["candidate_sha256sums_sha256"] != candidate.checksum_sha256:
        raise AssuranceError("{} 未绑定当前样本 SHA256SUMS".format(label))
    public = {
        "evidence_sha256": hashlib.sha256(payload).hexdigest(),
        "execution_id": execution_id,
        "started_at": value["started_at"],
        "finished_at": value["finished_at"],
        "output_dir": str(candidate_output),
        "command_argv_sha256": value["command_argv_sha256"],
        "stdout_sha256": value["stdout_sha256"],
        "stderr_sha256": value["stderr_sha256"],
        "candidate_sha256sums_sha256": value["candidate_sha256sums_sha256"],
    }
    parsed = {
        "started_at": started_at,
        "finished_at": finished_at,
        "output_dir": candidate_output,
    }
    return public, parsed, repro._identity(initial)


def _closure(directory: repro.VerifiedDirectory) -> Dict[str, Any]:
    return {
        "sha256sums_sha256": directory.checksum_sha256,
        "signed_file_count": len(directory.checksums),
        "signed_size_bytes": sum(
            directory.file_identities[name][3] for name in directory.checksums
        ),
        "verified": True,
    }


def _d2_root_input_identity(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """排除抽样策略与输出文件，只比较数据库、代码和固定窗口输入身份。"""

    source_value = repro._require_mapping(manifest.get("source"), "D2 source")
    source = dict(source_value)
    provenance_value = repro._require_mapping(
        source.get("provenance"), "D2 source.provenance"
    )
    provenance = dict(provenance_value)
    # 这些字段只是同一字节程序/数据档在文件系统中的 locator。runner 换一个
    # 解压目录不应伪装成代码变化；对应 SHA256、Git 状态和数据档身份仍保留。
    for field in ("probe_path", "project_root", "data_profile_path"):
        provenance.pop(field, None)
    source["provenance"] = provenance

    return {
        "schema_version": manifest.get("schema_version"),
        "candidate_kind": manifest.get("candidate_kind"),
        "data_profile": manifest.get("data_profile"),
        "window_utc": manifest.get("window_utc"),
        "source": source,
        "source_table_counts": manifest.get("source_table_counts"),
        "materialization_policy": manifest.get("materialization_policy"),
        "classification": manifest.get("classification"),
        "causal_conclusion": manifest.get("causal_conclusion"),
    }


def _require_full_final_d2(manifest: Mapping[str, Any]) -> None:
    sample = repro._require_mapping(manifest.get("sample"), "最终 D2 sample")
    expected = {"enabled": False, "max_events": None, "admissible": True}
    if dict(sample) != expected:
        raise AssuranceError("最终 D2 必须是非抽样候选")


def _require_64_sample(manifest: Mapping[str, Any], label: str) -> None:
    sample = repro._require_mapping(manifest.get("sample"), label + " sample")
    expected = {"enabled": True, "max_events": SAMPLE_MAX_EVENTS, "admissible": False}
    if dict(sample) != expected:
        raise AssuranceError("{}必须是 max_events=64 的不可准入样本".format(label))
    summary = repro._require_mapping(manifest.get("summary"), label + " summary")
    if summary.get("incident_count") != SAMPLE_MAX_EVENTS:
        raise AssuranceError("{}必须实际包含 64 条 Incident".format(label))


def _assert_distinct_directories(
    directories: Mapping[str, repro.VerifiedDirectory],
) -> None:
    seen_paths: Dict[str, str] = {}
    seen_inodes: Dict[Tuple[int, int], str] = {}
    for label, directory in directories.items():
        real_path = str(directory.path.resolve(strict=True))
        inode = directory.directory_identity[:2]
        if real_path in seen_paths:
            raise AssuranceError(
                "候选目录不得复用同一路径：{} 与 {}".format(seen_paths[real_path], label)
            )
        if inode in seen_inodes:
            raise AssuranceError(
                "候选目录不得复用同一 inode：{} 与 {}".format(seen_inodes[inode], label)
            )
        seen_paths[real_path] = label
        seen_inodes[inode] = label


def _assert_sample_files_not_hardlinked(
    first: repro.VerifiedDirectory, second: repro.VerifiedDirectory
) -> None:
    shared = []
    for name in sorted(set(first.names) & set(second.names)):
        if first.file_identities[name][:2] == second.file_identities[name][:2]:
            shared.append(name)
    if shared:
        raise AssuranceError(
            "D2 样本 A/B 不得复用硬链接文件：{}".format(", ".join(shared))
        )


def _sample_component(
    directory: repro.VerifiedDirectory,
    manifest: Mapping[str, Any],
    counts: Mapping[str, int],
    evidence: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "candidate_fingerprint_sha256": manifest["_validated_fingerprint"],
        "manifest_sha256": manifest["_validated_manifest_sha256"],
        "sha256sums_sha256": manifest["_validated_checksums_sha256"],
        "incidents_sha256": manifest["_validated_incidents_sha256"],
        "record_counts": dict(counts),
        "sample": dict(repro._require_mapping(manifest.get("sample"), "D2 sample")),
        "closure": _closure(directory),
        "execution_evidence": dict(evidence),
    }


def _final_identity(
    directories: Mapping[str, repro.VerifiedDirectory],
    d2: Mapping[str, Any],
    d3: Mapping[str, Any],
    d4: Mapping[str, Any],
    metric: Mapping[str, Any],
    route: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "d2": {
            "candidate_fingerprint_sha256": d2["_validated_fingerprint"],
            "manifest_sha256": d2["_validated_manifest_sha256"],
            "sha256sums_sha256": d2["_validated_checksums_sha256"],
            "incidents_sha256": d2["_validated_incidents_sha256"],
        },
        "d3": {
            "manifest_fingerprint_sha256": d3["_validated_fingerprint"],
            "manifest_sha256": d3["_validated_manifest_sha256"],
            "summary_sha256": d3["_validated_summary_sha256"],
            "sha256sums_sha256": d3["_validated_checksums_sha256"],
        },
        "d4": {
            "candidate_fingerprint_sha256": d4["_validated_fingerprint"],
            "manifest_sha256": directories["d4"].checksums["manifest.json"],
            "reconciliation_fingerprint_sha256": d4[
                "_validated_reconciliation_fingerprint"
            ],
            "sha256sums_sha256": directories["d4"].checksum_sha256,
        },
        "metric": {
            "candidate_fingerprint_sha256": metric["_validated_fingerprint"],
            "manifest_sha256": directories["metric"].checksums["manifest.json"],
            "reconciliation_fingerprint_sha256": metric[
                "_validated_reconciliation_fingerprint"
            ],
            "sha256sums_sha256": directories["metric"].checksum_sha256,
        },
        "route_event": {
            "index_fingerprint_sha256": route["_validated_fingerprint"],
            "parent_d3_manifest_fingerprint_sha256": route.get(
                "manifest_fingerprint_sha256"
            ),
            "reconciliation_summary_sha256": directories["route_event"].checksums[
                "route-event-reconciliation-summary.json"
            ],
            "sha256sums_sha256": directories["route_event"].checksum_sha256,
        },
    }


def _build_summary(
    directories: Mapping[str, repro.VerifiedDirectory],
    execution_evidence: Mapping[str, Mapping[str, Any]],
    staging: Path,
) -> Dict[str, Any]:
    final_index = repro.StableIdIndex(staging / ".final-id-audit.sqlite3")
    try:
        d2, d2_counts = repro._validate_d2(
            directories["d2"], "a", final_index, record_limit=SAMPLE_MAX_EVENTS
        )
        d3, d3_counts = repro._validate_d3(directories["d3"], "a", final_index)
        d4, d4_counts, _ = repro._validate_d4(
            directories["d4"], "a", final_index
        )
        metric, metric_counts, _ = repro._validate_metric(
            directories["metric"], "a", final_index
        )
        route, route_counts = repro._validate_route(
            directories["route_event"], "a", final_index
        )
        _require_full_final_d2(d2)
        repro._cross_validate("最终候选", d2, d3, d4, metric)
        if route.get("manifest_fingerprint_sha256") != d3["_validated_fingerprint"]:
            raise AssuranceError("最终 RouteEvent 未绑定当前 D3 manifest")
        route_identity = repro._route_identity(route)
        route_scope = repro._require_mapping(
            route_identity.get("build_scope"), "最终 RouteEvent build_scope"
        )
        route_profile = repro._require_mapping(
            route_scope.get("data_profile"), "最终 RouteEvent data_profile"
        )
        d3_profile = repro._require_mapping(
            d3.get("data_profile"), "最终 D3 data_profile"
        )
        profile_keys = ("id", "timezone", "window_start", "window_end_exclusive")
        if {
            key: route_profile.get(key) for key in profile_keys
        } != {
            key: d3_profile.get(key) for key in profile_keys
        }:
            raise AssuranceError("最终 RouteEvent data_profile 未绑定当前 D3")
    finally:
        final_index.close()

    sample_index = repro.StableIdIndex(staging / ".sample-id-audit.sqlite3")
    try:
        sample_a, sample_counts_a = repro._validate_d2(
            directories["d2_sample_a"], "a", sample_index
        )
        sample_b, sample_counts_b = repro._validate_d2(
            directories["d2_sample_b"], "b", sample_index
        )
        _require_64_sample(sample_a, "D2 样本 A")
        _require_64_sample(sample_b, "D2 样本 B")
        repro._assert_same_input(
            "D2 样本", repro._d2_identity(sample_a), repro._d2_identity(sample_b)
        )
        root_identity = _d2_root_input_identity(d2)
        if _canonical_bytes(_d2_root_input_identity(sample_a)) != _canonical_bytes(
            root_identity
        ) or _canonical_bytes(_d2_root_input_identity(sample_b)) != _canonical_bytes(
            root_identity
        ):
            raise AssuranceError("D2 样本 A/B 未绑定最终 D2 的同一数据库、代码与窗口输入")
        stable_summary = sample_index.summary()
    finally:
        sample_index.close()

    sample_pair = (directories["d2_sample_a"], directories["d2_sample_b"])
    names = set(sample_pair[0].checksums) | set(sample_pair[1].checksums)
    mismatches = [
        name
        for name in sorted(names)
        if sample_pair[0].checksums.get(name) != sample_pair[1].checksums.get(name)
    ]
    byte_match = (
        not mismatches
        and sample_pair[0].checksum_payload == sample_pair[1].checksum_payload
    )
    record_counts = repro._comparison_rows(sample_counts_a, sample_counts_b)
    record_counts_match = all(row["match"] for row in record_counts.values())
    aggregate_summary_match = _canonical_bytes(sample_a.get("summary")) == _canonical_bytes(
        sample_b.get("summary")
    )
    file_inventory_match = _canonical_bytes(sample_a.get("files")) == _canonical_bytes(
        sample_b.get("files")
    )
    fingerprint_match = (
        sample_a["_validated_fingerprint"] == sample_b["_validated_fingerprint"]
    )
    semantic_match = all(
        (
            stable_summary["match_ratio"] == 1,
            record_counts_match,
            aggregate_summary_match,
            file_inventory_match,
            fingerprint_match,
        )
    )
    bounded_status = "passed" if byte_match and semantic_match else "failed"

    final_counts = {
        "d2": d2_counts,
        "d3": d3_counts,
        "d4": d4_counts,
        "metric": metric_counts,
        "route_event": route_counts,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "assurance_mode": ASSURANCE_MODE,
        "execution_scope": {
            "candidates_regenerated_in_this_execution": False,
            "source_database_access": "none",
            "source_database_connection_attempts": 0,
            "source_database_write_operations": 0,
            "raw_mrt_access": "none",
        },
        "final_candidate_integrity": {
            "status": "passed",
            "all_sha256_closures_verified": True,
            "components": {
                name: _closure(directories[name])
                for name in ("d2", "d3", "d4", "metric", "route_event")
            },
            "semantic_validation_scope": {
                "d2": "deterministic_prefix_64_per_jsonl_stream",
                "d3": "full_manifest_metadata",
                "d4": "all_six_event_candidate_bundles",
                "metric": "full_emitted_metric_candidate",
                "route_event": "full_bounded_pilot",
            },
            "record_counts": final_counts,
        },
        "final_candidate_identity": _final_identity(
            directories, d2, d3, d4, metric, route
        ),
        "cross_artifact_binding": {
            "status": "passed",
            "checks": {
                "d4_to_final_d2": True,
                "d4_to_final_d3": True,
                "metric_to_final_d2": True,
                "metric_to_final_d3": True,
                "route_event_to_final_d3": True,
                "shared_data_profile": True,
            },
        },
        "bounded_replay": {
            "component": "d2",
            "requested_max_events": SAMPLE_MAX_EVENTS,
            "final_input_identity_match": True,
            "a": _sample_component(
                directories["d2_sample_a"],
                sample_a,
                sample_counts_a,
                execution_evidence["a"],
            ),
            "b": _sample_component(
                directories["d2_sample_b"],
                sample_b,
                sample_counts_b,
                execution_evidence["b"],
            ),
            "byte_identity": {
                "scope": "full_sample_candidate_closure",
                "all_files_rehashed": True,
                "all_corresponding_files_match": byte_match,
                "sha256sums_bytes_match": sample_pair[0].checksum_payload
                == sample_pair[1].checksum_payload,
                "mismatch_count": len(mismatches),
                "mismatched_files": mismatches,
            },
            "semantic_identity": {
                "scope": "full_sample_candidate_population",
                "all_records_streamed": True,
                "stable_id_scope": stable_summary,
                "record_counts": record_counts,
                "record_count_metadata_match": record_counts_match,
                "aggregate_summary_match": aggregate_summary_match,
                "file_inventory_match": file_inventory_match,
                "fingerprint_match": fingerprint_match,
                "all_results_match": semantic_match,
            },
            "generation_independence": {
                "status": "externally_attested",
                "path_distinct": True,
                "directory_inode_distinct": True,
                "all_corresponding_file_inodes_distinct": True,
                "external_execution_evidence_provided": True,
                "cryptographic_independence_proven": False,
                "evidence_boundary": "two_distinct_execution_records_bound_to_candidate_closures_not_cryptographic_proof",
            },
            "status": bounded_status,
        },
        "cross_run_coverage": {
            "status": "partial",
            "replayed_components": ["d2_bounded_sample"],
            "single_candidate_components": [
                "d2_full",
                "d3",
                "d4",
                "metric",
                "route_event",
            ],
            "population_coverage_claimed": False,
            "full_pipeline_reproducibility_claimed": False,
        },
        "full_semantic_validation": {
            "status": "not_run",
            "reason": "user_requested_bounded_sample",
            "population_coverage_claimed": False,
        },
        "conclusion": {
            "final_artifact_integrity_status": "passed",
            "bounded_d2_replay_status": bounded_status,
            "cross_artifact_binding_status": "passed",
            "cross_run_coverage_status": "partial",
            "full_semantic_reproducibility_status": "not_run",
        },
        "classification": "observation_only",
        "causal_conclusion": None,
    }


def _summary_markdown(summary: Mapping[str, Any]) -> str:
    conclusion = summary["conclusion"]
    independence = summary["bounded_replay"]["generation_independence"]
    return """# P0 单份候选 Assurance 摘要

## 结论

- 最终候选 SHA256 闭包与内部语义：`{integrity}`
- D2 64 条样本 A/B 字节及语义：`{bounded}`
- 最终候选跨制品绑定：`{binding}`
- 跨运行覆盖：`{coverage}`
- 全量语义复现：`{full}`
- 样本执行独立性记录：`{independence}`

## 证据边界

最终 D2/D3/D4/Metric/RouteEvent 均逐文件重算 SHA256；最终 D2 的逐行语义
仅复核各 JSONL 的确定性前 64 条。D2 样本 A/B 各自完整流式读取，并由两份
不同的外部执行记录绑定输出目录与 `SHA256SUMS`。这些记录支撑“两次执行有
记录”，但不是密码学独立性证明；仅凭目录、inode 或相同结果无法排除普通
文件复制。

本摘要没有重跑完整 D2，也没有对 D3、D4、Metric、RouteEvent 做全链路 A/B
重放，因此 `cross_run_coverage=partial`、`full_semantic_validation=not_run`；不得
据此声明完整流水线全量可复现、原始证据全覆盖或因果结论。
""".format(
        integrity=conclusion["final_artifact_integrity_status"],
        bounded=conclusion["bounded_d2_replay_status"],
        binding=conclusion["cross_artifact_binding_status"],
        coverage=conclusion["cross_run_coverage_status"],
        full=conclusion["full_semantic_reproducibility_status"],
        independence=independence["status"],
    )


def run(args: argparse.Namespace) -> Dict[str, Any]:
    target = Path(args.output_dir).absolute()
    if target.exists() or target.is_symlink():
        raise AssuranceError("输出目录必须不存在，拒绝覆盖：{}".format(target))
    parent = target.parent
    repro._lstat_directory(parent, "输出父目录")
    staging = parent / ".{}.tmp.{}.{}".format(
        target.name, os.getpid(), secrets.token_hex(4)
    )
    staging.mkdir(mode=0o750)
    completed = False
    directories: Dict[str, repro.VerifiedDirectory] = {}
    evidence_files = []
    try:
        paths = {
            "d2": args.d2_final,
            "d3": args.d3_final,
            "d4": args.d4_final,
            "metric": args.metric_final,
            "route_event": args.route_final,
            "d2_sample_a": args.d2_sample_a,
            "d2_sample_b": args.d2_sample_b,
        }
        for name, raw_path in paths.items():
            directories[name] = repro.VerifiedDirectory(
                Path(raw_path).absolute(), name
            )
        _assert_distinct_directories(directories)
        _assert_sample_files_not_hardlinked(
            directories["d2_sample_a"], directories["d2_sample_b"]
        )

        evidence_paths = {
            "a": Path(args.d2_sample_a_execution_evidence).absolute(),
            "b": Path(args.d2_sample_b_execution_evidence).absolute(),
        }
        evidence_realpaths = {
            side: str(path.resolve(strict=True)) for side, path in evidence_paths.items()
        }
        if evidence_realpaths["a"] == evidence_realpaths["b"]:
            raise AssuranceError("D2 样本 A/B 不得复用同一执行证据文件")
        execution_evidence = {}
        parsed_evidence = {}
        evidence_inodes = {}
        for side in ("a", "b"):
            public, parsed, inode = _load_execution_evidence(
                evidence_paths[side],
                label="D2 样本 {} 执行证据".format(side.upper()),
                candidate=directories["d2_sample_{}".format(side)],
            )
            execution_evidence[side] = public
            parsed_evidence[side] = parsed
            evidence_inodes[side] = inode[:2]
            evidence_files.append((evidence_paths[side], inode))
        if evidence_inodes["a"] == evidence_inodes["b"]:
            raise AssuranceError("D2 样本 A/B 不得复用同一执行证据 inode")
        for field in ("execution_id", "started_at", "finished_at", "output_dir"):
            if execution_evidence["a"][field] == execution_evidence["b"][field]:
                raise AssuranceError("D2 样本 A/B 执行证据 {} 必须不同".format(field))
        for field in ("started_at", "finished_at"):
            if parsed_evidence["a"][field] == parsed_evidence["b"][field]:
                raise AssuranceError(
                    "D2 样本 A/B 执行证据 {} 时刻必须不同".format(field)
                )
        if (
            execution_evidence["a"]["evidence_sha256"]
            == execution_evidence["b"]["evidence_sha256"]
        ):
            raise AssuranceError("D2 样本 A/B 执行证据内容必须不同")

        summary = _build_summary(directories, execution_evidence, staging)
        for directory in directories.values():
            directory.verify_unchanged()
        for path, expected_identity in evidence_files:
            if repro._identity(repro._lstat_regular(path, "执行证据复核")) != expected_identity:
                raise AssuranceError("执行证据在 assurance 生成期间发生变化：{}".format(path))

        for temporary_name in (".final-id-audit.sqlite3", ".sample-id-audit.sqlite3"):
            try:
                (staging / temporary_name).unlink()
            except FileNotFoundError:
                pass
        json_payload = _canonical_bytes(summary)
        markdown_payload = _summary_markdown(summary).encode("utf-8")
        repro._atomic_write_new(staging / OUTPUT_JSON, json_payload)
        repro._atomic_write_new(staging / OUTPUT_SUMMARY, markdown_payload)
        checksum_payload = (
            "{}  {}\n{}  {}\n".format(
                hashlib.sha256(json_payload).hexdigest(),
                OUTPUT_JSON,
                hashlib.sha256(markdown_payload).hexdigest(),
                OUTPUT_SUMMARY,
            )
        ).encode("utf-8")
        repro._atomic_write_new(staging / OUTPUT_CHECKSUMS, checksum_payload)
        for path in staging.iterdir():
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise AssuranceError("输出 staging 出现非普通文件")
            path.chmod(0o440)
        if target.exists() or target.is_symlink():
            raise AssuranceError("发布前输出目录已出现，拒绝覆盖")
        staging.rename(target)
        repro._fsync_directory(parent)
        completed = True
        return summary
    finally:
        if not completed and staging.exists():
            shutil.rmtree(staging)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成 P0 单份最终候选与 D2 64 条 A/B 样本 assurance"
    )
    for component in ("d2", "d3", "d4", "metric", "route"):
        parser.add_argument("--{}-final".format(component), required=True)
    parser.add_argument("--d2-sample-a", required=True)
    parser.add_argument("--d2-sample-b", required=True)
    parser.add_argument("--d2-sample-a-execution-evidence", required=True)
    parser.add_argument("--d2-sample-b-execution-evidence", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except Exception as error:
        print("错误：{}".format(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "最终候选完整性": result["conclusion"][
                    "final_artifact_integrity_status"
                ],
                "D2 有界 A/B 重放": result["conclusion"][
                    "bounded_d2_replay_status"
                ],
                "跨运行覆盖": result["conclusion"]["cross_run_coverage_status"],
                "全量语义复现": result["conclusion"][
                    "full_semantic_reproducibility_status"
                ],
                "schema_version": result["schema_version"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
