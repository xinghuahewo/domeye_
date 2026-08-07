#!/usr/bin/env python3
"""从 S1-S3 正式制品生成国家中断通用页的不可变有界读模型。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any


STORE_SCHEMA = "country-outage-general-read-model-store/v1"
EVENT_SCHEMA = "country-outage-general-event-read-model/v1"
OVERVIEW_SCHEMA = "country-outage-general-overview-artifact/v1"
SERIES_SCHEMA = "country-outage-general-series-artifact/v1"
ASN_ROW_SCHEMA = "country-outage-general-affected-as/v1"
DOWNSTREAM_ROW_SCHEMA = "country-outage-general-path-downstream/v1"
COLLECTOR_ID = "rrc25"
WINDOW_START_UTC = "2026-02-24T00:00:00Z"
WINDOW_END_EXCLUSIVE_UTC = "2026-03-11T00:00:00Z"
INTERVAL_SECONDS = 300
PATH_SAMPLE_LIMIT = 3

TRACK_DEFINITIONS = {
    "interrupted_prefix_count": {
        "label": "前缀路由中断",
        "unit": "prefix",
        "definition": "部分中断与完全中断的固定唯一前缀合计。",
    },
    "completely_interrupted_prefix_count": {
        "label": "完全中断前缀",
        "unit": "prefix",
        "definition": "全部预期独立 peer ASN 方向均不可见的固定唯一前缀。",
    },
    "invisible_direction_count": {
        "label": "不可见独立观察方向",
        "unit": "peer_asn_direction",
        "definition": "按 RRC25 peer ASN 去重的不可见观察方向。",
    },
    "affected_asn_count": {
        "label": "受影响 AS",
        "unit": "asn",
        "definition": "至少一个固定前缀部分或完全中断且没有未知前缀的 AS。",
    },
    "route_interrupted_asn_count": {
        "label": "AS 路由中断",
        "unit": "asn",
        "definition": "全部固定前缀均完全中断且没有未知前缀的 AS。",
    },
    "fixed_visible_ipv4_address_count": {
        "label": "固定前缀可见 IPv4 地址量",
        "unit": "unique_ipv4_address",
        "definition": "规范化、去重并合重叠后的 IPv4 唯一地址并集。",
    },
    "fixed_visible_ipv6_slash48_count": {
        "label": "固定前缀可见 IPv6 /48 等价量",
        "unit": "ipv6_slash48_equivalent",
        "definition": "规范化、去重并合后的 IPv6 /48 等价并集。",
    },
    "new_visible_ipv4_prefix_count": {
        "label": "当前可见新 IPv4 前缀",
        "unit": "prefix",
        "definition": "cohort 冻结后首次出现且当前可见的新 IPv4 前缀。",
    },
    "new_visible_ipv6_prefix_count": {
        "label": "当前可见新 IPv6 前缀",
        "unit": "prefix",
        "definition": "cohort 冻结后首次出现且当前可见的新 IPv6 前缀。",
    },
    "new_visible_ipv4_address_count": {
        "label": "当前可见新 IPv4 地址量",
        "unit": "unique_ipv4_address",
        "definition": "当前可见新 IPv4 前缀的唯一地址并集。",
    },
    "new_visible_ipv6_slash48_count": {
        "label": "当前可见新 IPv6 /48 等价量",
        "unit": "ipv6_slash48_equivalent",
        "definition": "当前可见新 IPv6 前缀的 /48 等价并集。",
    },
    "new_cumulative_ipv4_prefix_count": {
        "label": "累计出现新 IPv4 前缀",
        "unit": "prefix",
        "definition": "事件窗口内曾首次出现的新 IPv4 前缀累计数。",
    },
    "new_cumulative_ipv6_prefix_count": {
        "label": "累计出现新 IPv6 前缀",
        "unit": "prefix",
        "definition": "事件窗口内曾首次出现的新 IPv6 前缀累计数。",
    },
    "new_cumulative_ipv4_address_count": {
        "label": "累计出现新 IPv4 地址量",
        "unit": "unique_ipv4_address",
        "definition": "累计新 IPv4 前缀的唯一地址并集。",
    },
    "new_cumulative_ipv6_slash48_count": {
        "label": "累计出现新 IPv6 /48 等价量",
        "unit": "ipv6_slash48_equivalent",
        "definition": "累计新 IPv6 前缀的 /48 等价并集。",
    },
}


class ReadModelBuildError(RuntimeError):
    """输入身份、人口或不可变输出不满足合同。"""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def content_sha(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("content_sha256", None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def lifecycle_content_sha(value: Mapping[str, Any]) -> str:
    """复现 S1 生命周期快照基于 map 的冻结内容摘要。"""
    payload = dict(value)
    payload.pop("content_sha256", None)
    payload.pop("snapshot_id", None)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def stable_id(prefix: str, value: Mapping[str, Any]) -> str:
    return prefix + hashlib.sha256(canonical_json(value)).hexdigest()[:32]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise ReadModelBuildError(f"无法读取 JSON：{path}") from error
    if not isinstance(value, dict):
        raise ReadModelBuildError(f"JSON 顶层不是对象：{path}")
    return value, raw


def load_twin(root: Path) -> tuple[dict[str, Any], str]:
    manifest, left = read_json(root / "manifest.json")
    complete, right = read_json(root / "COMPLETE.json")
    if left != right or manifest != complete:
        raise ReadModelBuildError(f"manifest 与 COMPLETE 不一致：{root}")
    return manifest, hashlib.sha256(left).hexdigest()


def verified_jsonl(root: Path, meta: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    relative = meta.get("path")
    if not isinstance(relative, str) or not relative:
        raise ReadModelBuildError("JSONL 文件元数据缺少 path")
    path = (root / relative).resolve(strict=True)
    if root.resolve() not in path.parents or path.is_symlink():
        raise ReadModelBuildError(f"JSONL 路径越界或为软链接：{path}")
    if path.stat().st_size != meta.get("size_bytes") or sha256_file(path) != meta.get("sha256"):
        raise ReadModelBuildError(f"JSONL 文件摘要冲突：{path}")
    count = 0
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                count += 1
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ReadModelBuildError(f"JSONL 行不是对象：{path}")
                yield value
    except (OSError, json.JSONDecodeError) as error:
        raise ReadModelBuildError(f"JSONL 无法读取：{path}") from error
    if count != meta.get("row_count"):
        raise ReadModelBuildError(f"JSONL 行数冲突：{path}")


def deterministic_gzip(raw: bytes) -> bytes:
    return gzip.compress(raw, compresslevel=9, mtime=0)


def write_immutable(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise ReadModelBuildError(f"输出文件已存在：{path}") from error


def write_json_gzip(root: Path, relative: str, value: Mapping[str, Any]) -> dict[str, Any]:
    raw = canonical_json(value) + b"\n"
    compressed = deterministic_gzip(raw)
    path = root / relative
    write_immutable(path, compressed)
    return {
        "path": relative,
        "size_bytes": len(compressed),
        "sha256": hashlib.sha256(compressed).hexdigest(),
        "content_sha256": hashlib.sha256(raw).hexdigest(),
    }


def write_jsonl_gzip(
    root: Path,
    relative: str,
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    materialized = list(rows)
    raw = b"".join(canonical_json(row) + b"\n" for row in materialized)
    compressed = deterministic_gzip(raw)
    path = root / relative
    write_immutable(path, compressed)
    return {
        "path": relative,
        "row_count": len(materialized),
        "size_bytes": len(compressed),
        "sha256": hashlib.sha256(compressed).hexdigest(),
        "content_sha256": hashlib.sha256(raw).hexdigest(),
    }


def validate_sources(
    cohort_root: Path,
    metric_root: Path,
    as_path_root: Path,
    *,
    cohort_implementation_id: str,
    metric_implementation_id: str,
    as_path_implementation_id: str,
) -> dict[str, Any]:
    cohorts, cohort_manifest_sha = load_twin(cohort_root)
    metrics, metric_manifest_sha = load_twin(metric_root)
    paths, path_manifest_sha = load_twin(as_path_root)
    expected_roots = (
        (cohorts, "rrc25-event-cohort-store/v1", cohort_implementation_id),
        (metrics, "rrc25-event-metric-store/v1", metric_implementation_id),
        (paths, "rrc25-event-as-path-store/v1", as_path_implementation_id),
    )
    for manifest, schema, implementation in expected_roots:
        if (
            manifest.get("schema_version") != schema
            or manifest.get("status") != "complete"
            or manifest.get("collector_id") != COLLECTOR_ID
            or manifest.get("window_start_utc") != WINDOW_START_UTC
            or manifest.get("window_end_exclusive_utc") != WINDOW_END_EXCLUSIVE_UTC
            or manifest.get("implementation_id") != implementation
        ):
            raise ReadModelBuildError(f"正式输入身份冲突：{schema}")
    if (
        metrics.get("source_event_cohort_dataset_id") != cohorts.get("dataset_id")
        or metrics.get("source_event_cohort_content_sha256") != cohorts.get("content_sha256")
        or metrics.get("source_event_cohort_manifest_sha256") != cohort_manifest_sha
        or paths.get("source_event_cohort_dataset_id") != cohorts.get("dataset_id")
        or paths.get("source_event_cohort_content_sha256") != cohorts.get("content_sha256")
        or paths.get("source_event_cohort_manifest_sha256") != cohort_manifest_sha
        or paths.get("source_event_metric_dataset_id") != metrics.get("dataset_id")
        or paths.get("source_event_metric_content_sha256") != metrics.get("content_sha256")
        or paths.get("source_event_metric_manifest_sha256") != metric_manifest_sha
    ):
        raise ReadModelBuildError("S1、S2、S3 输入链路身份不一致")
    lifecycle_path = cohort_root / "event-lifecycle-snapshot.json"
    lifecycle, lifecycle_raw = read_json(lifecycle_path)
    if (
        lifecycle.get("snapshot_id") != cohorts.get("lifecycle_snapshot_id")
        or lifecycle.get("content_sha256") != cohorts.get("lifecycle_snapshot_content_sha256")
        or lifecycle.get("content_sha256") != lifecycle_content_sha(lifecycle)
        or hashlib.sha256(lifecycle_raw).hexdigest() != cohorts.get("lifecycle_snapshot_file_sha256")
        or lifecycle.get("event_count") != cohorts.get("event_count")
    ):
        raise ReadModelBuildError("事件生命周期快照身份冲突")
    return {
        "cohorts": cohorts,
        "metrics": metrics,
        "paths": paths,
        "lifecycle": lifecycle,
        "cohort_manifest_sha": cohort_manifest_sha,
        "metric_manifest_sha": metric_manifest_sha,
        "path_manifest_sha": path_manifest_sha,
    }


def build_series(
    metric_root: Path,
    event: Mapping[str, Any],
    *,
    event_read_model_id: str,
    publication_id: str,
    incident_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    timestamps: list[str] = []
    tracks = {key: [] for key in TRACK_DEFINITIONS}
    peaks = {
        key: {"value": -1, "state_point_utc": None}
        for key in (
            "interrupted_prefix_count",
            "completely_interrupted_prefix_count",
            "affected_asn_count",
            "route_interrupted_asn_count",
            "invisible_direction_count",
        )
    }
    previous_slot: int | None = None
    last_values: dict[str, Any] | None = None
    for point in verified_jsonl(metric_root, event["series"]):
        if (
            point.get("schema_version") != "rrc25-event-metric-series/v1"
            or point.get("event_metric_id") != event["event_metric_id"]
            or point.get("cohort_id") != event["cohort_id"]
            or point.get("value_state") != "observed"
            or point.get("missing_reason") is not None
            or not isinstance(point.get("values"), dict)
        ):
            raise ReadModelBuildError("S2 series 包含未闭合状态点")
        slot = point.get("state_slot")
        if not isinstance(slot, int) or (previous_slot is not None and slot != previous_slot + 1):
            raise ReadModelBuildError("S2 series 槽位不连续")
        previous_slot = slot
        timestamp = point.get("state_point_utc")
        if not isinstance(timestamp, str):
            raise ReadModelBuildError("S2 series 缺少状态时间")
        timestamps.append(timestamp)
        values = point["values"]
        for key in tracks:
            value = values.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ReadModelBuildError(f"S2 series 指标无效：{key}")
            tracks[key].append(value)
        for key, peak in peaks.items():
            value = values[key]
            if value > peak["value"]:
                peak["value"] = value
                peak["state_point_utc"] = timestamp
        last_values = dict(values)
    if (
        len(timestamps) != event.get("state_point_count")
        or not timestamps
        or timestamps[-1] != event.get("projection_end_state_point_utc")
        or last_values != event.get("final_values")
    ):
        raise ReadModelBuildError("S2 series 终点、人口或 final_values 冲突")
    result: dict[str, Any] = {
        "schema_version": SERIES_SCHEMA,
        "event_read_model_id": event_read_model_id,
        "publication_id": publication_id,
        "incident_id": incident_id,
        "event_metric_id": event["event_metric_id"],
        "cohort_id": event["cohort_id"],
        "window_start_utc": event["window_start_utc"],
        "window_end_utc": event["projection_end_state_point_utc"],
        "interval_seconds": INTERVAL_SECONDS,
        "point_count": len(timestamps),
        "timestamps": timestamps,
        "track_definitions": TRACK_DEFINITIONS,
        "tracks": tracks,
    }
    result["content_sha256"] = content_sha(result)
    return result, peaks, last_values or {}


def build_asn_rows(
    as_path_root: Path,
    path_event: Mapping[str, Any],
    *,
    event_read_model_id: str,
    publication_id: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for rank, source in enumerate(verified_jsonl(as_path_root, path_event["affected_as"]), start=1):
        if (
            source.get("schema_version") != "rrc25-event-affected-as/v1"
            or source.get("event_as_path_id") != path_event["event_as_path_id"]
        ):
            raise ReadModelBuildError("S3 受影响 AS 行身份冲突")
        result.append(
            {
                "schema_version": ASN_ROW_SCHEMA,
                "event_read_model_id": event_read_model_id,
                "publication_id": publication_id,
                "rank": rank,
                **{
                    key: source[key]
                    for key in (
                        "asn",
                        "as_name",
                        "organization",
                        "nature",
                        "name_state",
                        "organization_state",
                        "nature_state",
                        "event_classification",
                        "fixed_prefix_count",
                        "peak_partial_prefix_count",
                        "peak_complete_prefix_count",
                        "peak_invisible_direction_count",
                        "path_downstream_asn_count",
                        "concurrent_downstream_asn_count",
                    )
                },
            }
        )
    if len(result) != path_event.get("affected_as_count"):
        raise ReadModelBuildError("S3 受影响 AS 人口冲突")
    return result


def build_downstream_rows(
    as_path_root: Path,
    path_event: Mapping[str, Any],
    *,
    event_read_model_id: str,
    publication_id: str,
) -> list[dict[str, Any]]:
    samples: dict[tuple[int, int], list[dict[str, Any]]] = {}
    evidence_count = 0
    for evidence in verified_jsonl(as_path_root, path_event["path_evidence"]):
        evidence_count += 1
        if (
            evidence.get("schema_version") != "rrc25-event-path-evidence/v1"
            or evidence.get("event_as_path_id") != path_event["event_as_path_id"]
        ):
            raise ReadModelBuildError("S3 路径证据身份冲突")
        key = (int(evidence["affected_asn"]), int(evidence["downstream_asn"]))
        values = samples.setdefault(key, [])
        if len(values) < PATH_SAMPLE_LIMIT:
            values.append(
                {
                    key: evidence[key]
                    for key in (
                        "prefix",
                        "address_family",
                        "as_path_id",
                        "as_path_canonical",
                        "independent_peer_asns",
                        "route_observation_count",
                    )
                }
            )
    if evidence_count != path_event.get("path_evidence_count"):
        raise ReadModelBuildError("S3 路径证据人口冲突")
    rows: list[dict[str, Any]] = []
    for source in verified_jsonl(as_path_root, path_event["path_downstreams"]):
        key = (int(source["affected_asn"]), int(source["downstream_asn"]))
        if key not in samples or not samples[key]:
            raise ReadModelBuildError("路径关联缺少可读真实路径样本")
        rows.append(
            {
                "schema_version": DOWNSTREAM_ROW_SCHEMA,
                "event_read_model_id": event_read_model_id,
                "publication_id": publication_id,
                **{
                    field: source[field]
                    for field in (
                        "affected_asn",
                        "downstream_asn",
                        "downstream_as_name",
                        "downstream_organization",
                        "downstream_nature",
                        "downstream_name_state",
                        "downstream_organization_state",
                        "downstream_nature_state",
                        "observed_path_count",
                        "associated_fixed_prefix_count",
                        "independent_direction_count",
                        "route_observation_count",
                        "concurrent_state_point_count",
                        "first_concurrent_state_point_utc",
                        "last_concurrent_state_point_utc",
                        "peak_concurrent_interrupted_prefix_count",
                        "peak_concurrent_ipv4_address_count",
                        "peak_concurrent_ipv6_slash48_count",
                    )
                },
                "path_samples": samples[key],
                "relationship_semantics": "observed_ordered_rrc25_path_association_not_dependency_or_cause",
            }
        )
    if len(rows) != path_event.get("path_downstream_relation_count"):
        raise ReadModelBuildError("S3 路径关联人口冲突")
    rows.sort(
        key=lambda row: (
            0 if row["concurrent_state_point_count"] > 0 else 1,
            -row["peak_concurrent_interrupted_prefix_count"],
            -row["associated_fixed_prefix_count"],
            row["affected_asn"],
            row["downstream_asn"],
        )
    )
    return rows


def build_store(arguments: argparse.Namespace) -> dict[str, Any]:
    cohort_root = arguments.event_cohorts.resolve(strict=True)
    metric_root = arguments.event_metrics.resolve(strict=True)
    as_path_root = arguments.event_as_path.resolve(strict=True)
    output = arguments.output.resolve()
    sources = validate_sources(
        cohort_root,
        metric_root,
        as_path_root,
        cohort_implementation_id=arguments.event_cohort_implementation_id,
        metric_implementation_id=arguments.event_metric_implementation_id,
        as_path_implementation_id=arguments.event_as_path_implementation_id,
    )
    identity = {
        "schema_version": STORE_SCHEMA,
        "implementation_id": arguments.implementation_id,
        "cohort_dataset_id": sources["cohorts"]["dataset_id"],
        "cohort_content_sha256": sources["cohorts"]["content_sha256"],
        "metric_dataset_id": sources["metrics"]["dataset_id"],
        "metric_content_sha256": sources["metrics"]["content_sha256"],
        "as_path_dataset_id": sources["paths"]["dataset_id"],
        "as_path_content_sha256": sources["paths"]["content_sha256"],
        "lifecycle_snapshot_id": sources["lifecycle"]["snapshot_id"],
    }
    run_id = stable_id("general_read_model_run_v1_", identity)
    dataset_id = stable_id("general_read_model_dataset_v1_", identity)
    if output.exists():
        if not arguments.resume:
            raise ReadModelBuildError(f"输出目录已存在：{output}")
        manifest, _ = load_twin(output)
        if manifest.get("content_sha256") != content_sha(manifest):
            raise ReadModelBuildError("已完成读模型内容摘要冲突")
        expected_resume_identity = {
            "schema_version": STORE_SCHEMA,
            "status": "complete",
            "run_id": run_id,
            "dataset_id": dataset_id,
            "implementation_id": arguments.implementation_id,
            "source_event_cohort_dataset_id": sources["cohorts"]["dataset_id"],
            "source_event_cohort_content_sha256": sources["cohorts"]["content_sha256"],
            "source_event_metric_dataset_id": sources["metrics"]["dataset_id"],
            "source_event_metric_content_sha256": sources["metrics"]["content_sha256"],
            "source_event_as_path_dataset_id": sources["paths"]["dataset_id"],
            "source_event_as_path_content_sha256": sources["paths"]["content_sha256"],
            "source_lifecycle_snapshot_id": sources["lifecycle"]["snapshot_id"],
            "source_lifecycle_snapshot_content_sha256": sources["lifecycle"]["content_sha256"],
        }
        if any(
            manifest.get(key) != value
            for key, value in expected_resume_identity.items()
        ):
            raise ReadModelBuildError("已完成读模型与本次输入或实现身份冲突")
        return manifest
    temporary = Path(str(output) + ".tmp")
    if temporary.exists():
        raise ReadModelBuildError(f"临时输出已存在：{temporary}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary.mkdir(parents=True)
    failed = True
    try:
        lifecycle_by_reference = {
            event["legacy_reference"]: event
            for event in sources["lifecycle"]["events"]
        }
        path_by_metric = {
            event["event_metric_id"]: event
            for event in sources["paths"]["events"]
        }
        events: list[dict[str, Any]] = []
        totals = {
            "state_point_count": 0,
            "affected_as_count": 0,
            "path_downstream_relation_count": 0,
            "path_sample_count": 0,
        }
        for ordinal, metric in enumerate(sources["metrics"]["events"]):
            if ordinal % 8 == 0:
                print(
                    f"general read model event={ordinal}/{sources['metrics']['event_count']}",
                    file=sys.stderr,
                    flush=True,
                )
            reference = metric["legacy_reference"]
            lifecycle = lifecycle_by_reference.get(reference)
            path_event = path_by_metric.get(metric["event_metric_id"])
            if lifecycle is None or path_event is None:
                raise ReadModelBuildError("事件在生命周期、S2 或 S3 中不能唯一匹配")
            if (
                lifecycle["country_code"] != metric["country_code"]
                or lifecycle["window_start_utc"] != metric["window_start_utc"]
                or lifecycle["projection_end_state_point_utc"] != metric["projection_end_state_point_utc"]
                or path_event["cohort_id"] != metric["cohort_id"]
            ):
                raise ReadModelBuildError("事件窗口或 cohort 身份冲突")
            publication_identity = {
                "dataset_id": dataset_id,
                "incident_id": lifecycle["incident_id"],
                "event_metric_id": metric["event_metric_id"],
                "event_metric_content_sha256": metric["content_sha256"],
                "event_as_path_id": path_event["event_as_path_id"],
                "event_as_path_content_sha256": path_event["content_sha256"],
                "data_through": metric["projection_end_state_point_utc"],
            }
            publication_id = stable_id(
                "country_outage_publication_v1_", publication_identity
            )
            event_read_model_id = stable_id(
                "country_event_read_model_v1_", publication_identity
            )
            directory = (
                f"events/{metric['country_code']}/"
                "slot-"
                f"{metric['window_start_utc'][0:4]}"
                f"{metric['window_start_utc'][5:7]}"
                f"{metric['window_start_utc'][8:10]}"
                f"{metric['window_start_utc'][11:13]}"
                f"{metric['window_start_utc'][14:16]}-"
                f"{event_read_model_id[-12:]}"
            )
            series, peaks, final_values = build_series(
                metric_root,
                metric,
                event_read_model_id=event_read_model_id,
                publication_id=publication_id,
                incident_id=lifecycle["incident_id"],
            )
            asn_rows = build_asn_rows(
                as_path_root,
                path_event,
                event_read_model_id=event_read_model_id,
                publication_id=publication_id,
            )
            downstream_rows = build_downstream_rows(
                as_path_root,
                path_event,
                event_read_model_id=event_read_model_id,
                publication_id=publication_id,
            )
            capabilities = {
                "overview": "available",
                "event_series": "available",
                "affected_as": "available",
                "path_downstreams": "available",
                "full_path_evidence": "audit_only",
            }
            overview: dict[str, Any] = {
                "schema_version": OVERVIEW_SCHEMA,
                "event_read_model_id": event_read_model_id,
                "publication_id": publication_id,
                "revision": 1,
                "publication_state": "published",
                "observation_state": "evidence_complete",
                "quality_state": "complete",
                "missing_slot_count": 0,
                "collector_id": COLLECTOR_ID,
                "incident_id": lifecycle["incident_id"],
                "legacy_reference": reference,
                "country_code": metric["country_code"],
                "detected_at_utc": lifecycle["detected_at_utc"],
                "event_end_at_utc": lifecycle["event_end_at_utc"],
                "event_duration_seconds": lifecycle["event_duration_seconds"],
                "lifecycle_state": lifecycle["lifecycle_state"],
                "is_final_in_data_range": lifecycle["is_final_in_data_range"],
                "window_start_utc": metric["window_start_utc"],
                "window_end_utc": metric["projection_end_state_point_utc"],
                "data_through": metric["projection_end_state_point_utc"],
                "interval_seconds": INTERVAL_SECONDS,
                "state_point_count": metric["state_point_count"],
                "cohort": {
                    "cohort_id": metric["cohort_id"],
                    "fixed_prefix_count": metric["fixed_prefix_count"],
                    "fixed_asn_count": metric["fixed_asn_count"],
                    "independent_direction_relation_count": metric["expected_direction_relation_count"],
                    "new_prefix_count": metric["new_prefix_count"],
                },
                "final_values": final_values,
                "peaks": peaks,
                "affected_as_count": len(asn_rows),
                "route_interrupted_as_count": sum(
                    row["event_classification"] == "route_interrupted"
                    for row in asn_rows
                ),
                "path_downstream_relation_count": len(downstream_rows),
                "concurrent_path_downstream_relation_count": sum(
                    row["concurrent_state_point_count"] > 0
                    for row in downstream_rows
                ),
                "capabilities": capabilities,
                "semantic_boundary": "rrc25_control_plane_observation_not_user_impact_or_cause",
            }
            overview["content_sha256"] = content_sha(overview)
            overview_file = write_json_gzip(
                temporary, f"{directory}/overview.json.gz", overview
            )
            series_file = write_json_gzip(
                temporary, f"{directory}/series.json.gz", series
            )
            asn_file = write_jsonl_gzip(
                temporary, f"{directory}/affected-as.jsonl.gz", asn_rows
            )
            downstream_file = write_jsonl_gzip(
                temporary,
                f"{directory}/path-downstreams.jsonl.gz",
                downstream_rows,
            )
            event_manifest: dict[str, Any] = {
                "schema_version": EVENT_SCHEMA,
                "status": "complete",
                "event_read_model_id": event_read_model_id,
                "publication_id": publication_id,
                "revision": 1,
                "publication_state": "published",
                "incident_id": lifecycle["incident_id"],
                "legacy_reference": reference,
                "country_code": metric["country_code"],
                "cohort_id": metric["cohort_id"],
                "event_metric_id": metric["event_metric_id"],
                "event_as_path_id": path_event["event_as_path_id"],
                "window_start_utc": metric["window_start_utc"],
                "window_end_utc": metric["projection_end_state_point_utc"],
                "data_through": metric["projection_end_state_point_utc"],
                "lifecycle_state": lifecycle["lifecycle_state"],
                "is_final_in_data_range": lifecycle["is_final_in_data_range"],
                "state_point_count": metric["state_point_count"],
                "affected_as_count": len(asn_rows),
                "path_downstream_relation_count": len(downstream_rows),
                "path_sample_count": sum(len(row["path_samples"]) for row in downstream_rows),
                "overview": overview_file,
                "series": series_file,
                "affected_as": asn_file,
                "path_downstreams": downstream_file,
            }
            event_manifest["content_sha256"] = content_sha(event_manifest)
            events.append(event_manifest)
            totals["state_point_count"] += metric["state_point_count"]
            totals["affected_as_count"] += len(asn_rows)
            totals["path_downstream_relation_count"] += len(downstream_rows)
            totals["path_sample_count"] += event_manifest["path_sample_count"]
        if (
            len(events) != sources["metrics"]["event_count"]
            or len(lifecycle_by_reference) != len(events)
            or len(path_by_metric) != len(events)
        ):
            raise ReadModelBuildError("读模型事件人口冲突")
        events.sort(key=lambda row: (row["window_start_utc"], row["legacy_reference"]))
        manifest: dict[str, Any] = {
            "schema_version": STORE_SCHEMA,
            "status": "complete",
            "run_id": run_id,
            "dataset_id": dataset_id,
            "collector_id": COLLECTOR_ID,
            "window_start_utc": WINDOW_START_UTC,
            "window_end_exclusive_utc": WINDOW_END_EXCLUSIVE_UTC,
            "implementation_id": arguments.implementation_id,
            "source_event_cohort_dataset_id": sources["cohorts"]["dataset_id"],
            "source_event_cohort_content_sha256": sources["cohorts"]["content_sha256"],
            "source_event_cohort_manifest_sha256": sources["cohort_manifest_sha"],
            "source_event_metric_dataset_id": sources["metrics"]["dataset_id"],
            "source_event_metric_content_sha256": sources["metrics"]["content_sha256"],
            "source_event_metric_manifest_sha256": sources["metric_manifest_sha"],
            "source_event_as_path_dataset_id": sources["paths"]["dataset_id"],
            "source_event_as_path_content_sha256": sources["paths"]["content_sha256"],
            "source_event_as_path_manifest_sha256": sources["path_manifest_sha"],
            "source_lifecycle_snapshot_id": sources["lifecycle"]["snapshot_id"],
            "source_lifecycle_snapshot_content_sha256": sources["lifecycle"]["content_sha256"],
            "api_read_semantics": "precompiled_event_window_read_model_only",
            "pagination_semantics": "stable_server_side_pages_maximum_60_items",
            "path_evidence_semantics": "bounded_real_path_samples_full_evidence_remains_in_s3_audit_artifact",
            "causal_boundary": "rrc25_path_association_is_not_dependency_propagation_user_impact_or_cause",
            "event_count": len(events),
            **totals,
            "events": events,
        }
        manifest["content_sha256"] = content_sha(manifest)
        raw = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8") + b"\n"
        write_immutable(temporary / "manifest.json", raw)
        write_immutable(temporary / "COMPLETE.json", raw)
        output.parent.mkdir(parents=True, exist_ok=True)
        os.rename(temporary, output)
        failed = False
        return manifest
    finally:
        if failed and temporary.exists():
            failed_path = Path(str(temporary) + ".failed")
            if not failed_path.exists():
                os.rename(temporary, failed_path)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-cohorts", type=Path, required=True)
    parser.add_argument("--event-metrics", type=Path, required=True)
    parser.add_argument("--event-as-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--event-cohort-implementation-id", required=True)
    parser.add_argument("--event-metric-implementation-id", required=True)
    parser.add_argument("--event-as-path-implementation-id", required=True)
    parser.add_argument("--implementation-id", required=True)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if not arguments.implementation_id.startswith("git:") or len(arguments.implementation_id) != 44:
        print(json.dumps({"status": "failed", "error": "implementation_id 必须是 git:<40位SHA>"}, ensure_ascii=False))
        return 1
    try:
        manifest = build_store(arguments)
    except (KeyError, OSError, TypeError, ValueError, ReadModelBuildError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "complete", "result": manifest}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
