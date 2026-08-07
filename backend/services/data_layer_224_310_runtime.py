"""RRC25 224-310 生产只读数据层适配器。

该模块只读取已被生产选择清单绑定的不可变 S5 读模型。没有生产选择时完全禁用；
选择或摘要不一致时失败关闭，不回退到旧库冒充同一 Publication。
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
from threading import Lock
from typing import Any, Mapping


SELECTION_ENV = "DOMEYE_DATA_LAYER_224_310_SELECTION"
SELECTION_SCHEMA = "domeye_data_layer_production_selection_v1"
READ_MODEL_SCHEMA = "rrc25-read-model-store/v1"
EVENT_SCHEMA = "rrc25-event-read-model/v1"
SERIES_SCHEMA = "rrc25-compact-country-series/v1"
INDEX_SCHEMA = "domeye_data_layer_production_index_v1"
COLLECTOR_ID = "rrc25"
WINDOW_START = "2026-02-24T00:00:00Z"
FIRST_STATE_POINT = "2026-02-24T00:05:00Z"
WINDOW_END = "2026-03-11T00:00:00Z"
STATE_POINT_COUNT = 4320
BUSINESS_TIMEZONE = timezone(timedelta(hours=8))
REFERENCE_PATTERN = re.compile(
    r"^country_outage/(?P<start>[^/]+)/(?P<country>[A-Za-z]{2})/"
    r"(?P<event_id>[1-9][0-9]*)/(?P<source>[A-Za-z0-9_-]+)$"
)


class DataLayerNotConfigured(RuntimeError):
    """当前运行制品没有生产数据层选择。"""


class DataLayerIntegrityError(RuntimeError):
    """生产选择、制品或跨层身份不一致。"""


class DataLayerPublicationNotFound(LookupError):
    """数据层拥有事件，但请求的 Publication 不存在。"""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DataLayerIntegrityError(f"无法读取生产数据层 JSON：{path}") from error
    if not isinstance(value, dict):
        raise DataLayerIntegrityError(f"生产数据层 JSON 必须是对象：{path}")
    return value


def _canonical_reference(value: str) -> str | None:
    match = REFERENCE_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    try:
        start = datetime.strptime(
            match.group("start").replace("+", " "),
            "%Y-%m-%d %H:%M:%S",
        ).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return "/".join(
        (
            "country_outage",
            start,
            match.group("country").upper(),
            match.group("event_id"),
            match.group("source"),
        )
    )


def _local_time(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(BUSINESS_TIMEZONE).isoformat(timespec="seconds")


def _stable_id(prefix: str, value: Mapping[str, Any]) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return prefix + _sha256(raw)[:32]


def _read_tsv_gzip(path: Path) -> list[dict[str, str]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
            return list(csv.DictReader(source, delimiter="\t"))
    except (OSError, csv.Error) as error:
        raise DataLayerIntegrityError(f"无法读取生产读模型：{path}") from error


def _tree_sha256(root: Path) -> str:
    rows: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        raw = path.read_bytes()
        rows.append(
            f"{_sha256(raw)}  {len(raw)}  {path.relative_to(root).as_posix()}"
        )
    return _sha256(("\n".join(rows) + "\n").encode("utf-8"))


class DataLayer224310Runtime:
    """在进程内建立 S5 读模型的只读索引。"""

    def __init__(self, selection_path: Path) -> None:
        self.selection_path = selection_path.resolve(strict=True)
        self.release_root = self.selection_path.parent
        self.selection = _json(self.selection_path)
        self._validate_selection()

        self.read_model_root = (
            self.release_root / str(self.selection["read_model_root"])
        ).resolve(strict=True)
        if self.release_root not in self.read_model_root.parents:
            raise DataLayerIntegrityError("生产读模型路径越过数据层 release")
        if _tree_sha256(self.read_model_root) != self.selection["read_model_tree_sha256"]:
            raise DataLayerIntegrityError("生产读模型目录摘要不一致")

        manifest_path = self.read_model_root / "manifest.json"
        manifest_raw = manifest_path.read_bytes()
        if _sha256(manifest_raw) != self.selection["read_model_manifest_sha256"]:
            raise DataLayerIntegrityError("生产读模型 manifest 摘要不一致")
        self.manifest = json.loads(manifest_raw)
        self._validate_manifest()

        index_path = self.release_root / str(self.selection["production_index_path"])
        index_raw = index_path.read_bytes()
        if _sha256(index_raw) != self.selection["production_index_sha256"]:
            raise DataLayerIntegrityError("生产读模型索引摘要不一致")
        self.index = json.loads(index_raw)
        self._validate_index()

        self.events: dict[str, dict[str, Any]] = {}
        self.events_by_reference: dict[str, str] = {}
        for row in _read_tsv_gzip(self.read_model_root / "event-read-model.tsv.gz"):
            try:
                payload = json.loads(row["payload"])
            except (KeyError, json.JSONDecodeError) as error:
                raise DataLayerIntegrityError("事件读模型行无效") from error
            if payload.get("schema_version") != EVENT_SCHEMA:
                raise DataLayerIntegrityError("事件读模型 schema 不一致")
            incident = payload.get("incident") or {}
            incident_id = str(incident.get("incident_id") or "")
            reference = _canonical_reference(str(incident.get("legacy_reference") or ""))
            if not incident_id or reference is None or incident_id in self.events:
                raise DataLayerIntegrityError("事件读模型身份缺失或重复")
            self.events[incident_id] = payload
            self.events_by_reference[reference] = incident_id
        if len(self.events) != self.manifest["event_count"]:
            raise DataLayerIntegrityError("事件读模型人口与 manifest 不一致")

    def _validate_selection(self) -> None:
        expected = {
            "schema_version": SELECTION_SCHEMA,
            "status": "selected",
            "selected_by_production": True,
            "collector_id": COLLECTOR_ID,
            "window_start_utc": WINDOW_START,
            "window_end_exclusive_utc": WINDOW_END,
        }
        for key, value in expected.items():
            if self.selection.get(key) != value:
                raise DataLayerIntegrityError(f"生产选择字段冲突：{key}")
        for key in (
            "candidate_id",
            "read_model_dataset_id",
            "shadow_migration_dataset_id",
            "read_model_root",
            "read_model_manifest_sha256",
            "read_model_tree_sha256",
            "production_index_path",
            "production_index_sha256",
        ):
            if not isinstance(self.selection.get(key), str) or not self.selection[key]:
                raise DataLayerIntegrityError(f"生产选择缺少字段：{key}")

    def _validate_manifest(self) -> None:
        expected = {
            "schema_version": READ_MODEL_SCHEMA,
            "status": "complete",
            "collector_id": COLLECTOR_ID,
            "candidate_id": self.selection["candidate_id"],
            "dataset_id": self.selection["read_model_dataset_id"],
            "window_start_utc": WINDOW_START,
            "window_end_exclusive_utc": WINDOW_END,
            "state_point_count": STATE_POINT_COUNT,
            "api_read_semantics": "precompiled_read_model_only",
            "prefix_vp_semantics": "derived_view_not_independent_fact",
        }
        for key, value in expected.items():
            if self.manifest.get(key) != value:
                raise DataLayerIntegrityError(f"读模型 manifest 字段冲突：{key}")

    def _validate_index(self) -> None:
        expected = {
            "schema_version": INDEX_SCHEMA,
            "status": "complete",
            "collector_id": COLLECTOR_ID,
            "candidate_id": self.selection["candidate_id"],
            "read_model_dataset_id": self.selection["read_model_dataset_id"],
            "read_model_manifest_sha256": self.selection[
                "read_model_manifest_sha256"
            ],
            "read_model_tree_sha256": self.selection["read_model_tree_sha256"],
        }
        for key, value in expected.items():
            if self.index.get(key) != value:
                raise DataLayerIntegrityError(f"生产读模型索引字段冲突：{key}")
        countries = self.index.get("countries")
        if not isinstance(countries, dict) or len(countries) != 43:
            raise DataLayerIntegrityError("生产读模型国家索引人口错误")

    def _event(
        self,
        incident_id: str,
        publication_id: str | None,
    ) -> dict[str, Any] | None:
        event = self.events.get(incident_id)
        if event is None:
            return None
        actual = str(event["observation_publication"]["publication_id"])
        if publication_id and publication_id != actual:
            raise DataLayerPublicationNotFound(publication_id)
        return event

    def owns_incident(self, incident_id: str) -> bool:
        return incident_id in self.events

    def resolve(self, reference: str) -> dict[str, Any] | None:
        canonical = _canonical_reference(reference)
        if canonical is None:
            return None
        incident_id = self.events_by_reference.get(canonical)
        if incident_id is None:
            return None
        event = self.events[incident_id]
        publication = event["observation_publication"]
        analysis = event["analysis_publication"]
        return {
            "schema_version": "country_outage_resolution_v2",
            "incident_id": incident_id,
            "publication_id": publication["publication_id"],
            "latest_revision": publication["revision"],
            "observation_state": "evidence_complete",
            "data_mode": "replay",
            "data_through": publication["data_through"],
            "analysis_publication_id": analysis["publication_id"],
            "analysis_data_through": analysis["data_through"],
            "is_final": True,
            "collector_id": COLLECTOR_ID,
            "read_model_dataset_id": self.manifest["dataset_id"],
            "selected_by_production": True,
        }

    def _metadata(self, event: Mapping[str, Any]) -> dict[str, Any]:
        incident = event["incident"]
        publication = event["observation_publication"]
        country = self.index["countries"][incident["country_code"]]
        return {
            "revision": publication["revision"],
            "publication_id": publication["publication_id"],
            "publication_state": "published",
            "observation_state": "evidence_complete",
            "data_mode": "replay",
            "data_through": publication["data_through"],
            "updated_at": self.selection.get("selected_at"),
            "is_final": True,
            "processing_status": {
                "state": "final",
                "updated_at": self.selection.get("selected_at"),
                "attempted_through": publication["data_through"],
                "reason": None,
                "last_complete_data_through": publication["data_through"],
            },
            "missing_slot_count": 0,
            "incident_id": incident["incident_id"],
            "cohort_id": country["cohort_id"],
            "window_start_utc": FIRST_STATE_POINT,
            "window_end_utc": WINDOW_END,
            "capability_contract_version": "country_outage_capabilities_v1",
        }

    def overview(
        self,
        incident_id: str,
        publication_id: str | None,
    ) -> dict[str, Any] | None:
        event = self._event(incident_id, publication_id)
        if event is None:
            return None
        incident = event["incident"]
        country = self.index["countries"][incident["country_code"]]
        facts = event["fact_set"]
        metadata = self._metadata(event)
        return {
            "schema_version": "country_outage_overview_v2",
            **metadata,
            "event_identity": {
                "incident_id": incident_id,
                "legacy_reference": incident["legacy_reference"],
                "legacy_record_time_local": incident["legacy_reference"].split("/")[1],
                "event_type": "country_outage",
                "country_code": incident["country_code"],
                "country_name": incident["country_name"],
                "display_name": f"{incident['country_name']} BGP 路由观测",
            },
            "observation_scope": {
                "collector_id": COLLECTOR_ID,
                "collector_ids": [COLLECTOR_ID],
                "collector_count": 1,
                "vantage_point_count": country["vantage_point_count"],
                "vantage_point_semantics": "RRC25 RouteState 中的唯一 VP/peer 身份",
                "window_start_utc": FIRST_STATE_POINT,
                "window_start_local": _local_time(FIRST_STATE_POINT),
                "window_end_utc": WINDOW_END,
                "window_end_local": _local_time(WINDOW_END),
                "timezone": "Asia/Shanghai",
                "interval_seconds": 300,
                "observation_count": STATE_POINT_COUNT,
                "expected_observation_count": STATE_POINT_COUNT,
                "missing_observation_count": 0,
                "quality_status": "pass",
                "last_observation_at_utc": WINDOW_END,
                "last_observation_at_local": _local_time(WINDOW_END),
                "replay_completed_at_utc": self.selection.get("selected_at"),
                "replay_completed_at_local": _local_time(
                    self.selection.get("selected_at")
                ),
                "left_boundary": "源窗口起点为 2026-02-24T00:00:00Z；首个状态点为五分钟槽结束时刻",
                "right_boundary": "源窗口采用半开区间，最后状态点为 2026-03-11T00:00:00Z",
            },
            "cohort": {
                "cohort_id": country["cohort_id"],
                "seed_observed_at_utc": WINDOW_START,
                "seed_observed_at_local": _local_time(WINDOW_START),
                "origin_asn_count": country["origin_asn_count"],
                "prefix_vp_count": country["prefix_vp_count"],
                "ipv4_prefix_vp_count": country["ipv4_prefix_vp_count"],
                "ipv6_prefix_vp_count": country["ipv6_prefix_vp_count"],
                "mapping_version": self.index["mapping_version"],
                "denominator_policy": "fixed_from_seed_rib_route_state",
            },
            "normal_band": {
                "state": "unavailable",
                "label": "正常带不可用",
                "reason": "冻结窗口没有可信长期正常参照；只展示窗口内确定性观测。",
            },
            "rule_marker": None,
            "capabilities": {
                "legacy_summary": {"state": "available"},
                "fixed_cohort": {"state": "available"},
                "country_resources": {
                    "state": "unavailable",
                    "reason": "统一读模型没有 Core 国家资源聚合轨道",
                },
                "update_activity": {"state": "available"},
                "address_families": {"state": "available"},
                "asn_matrix": {
                    "state": "unavailable",
                    "reason": "首页不加载全量 ASN 状态；Prefix×VP 证据按需下钻",
                },
                "prefix_vp_evidence": {"state": "available"},
                "audit": {"state": "available"},
                "normal_band": {
                    "state": "unavailable",
                    "reason": "固定窗口没有可信长期正常参照",
                },
                "trend_analysis": {
                    "state": "available",
                    "reason": "读取不可变 Analysis Publication 的预计算 TrendProfile",
                },
            },
            "legacy_summary": None,
            "annotations": [
                {
                    "kind": "event_stage",
                    "metric": "combined_fixed_cohort_visibility_ratio",
                    "observed_at_utc": fact["observed_at"],
                    "observed_at_local": _local_time(fact["observed_at"]),
                    "label": fact["stage"],
                    "value": None,
                    "unit": "stage",
                }
                for fact in facts
            ],
            "limitations": event["limitations"],
        }

    def series(
        self,
        incident_id: str,
        publication_id: str | None,
    ) -> dict[str, Any] | None:
        event = self._event(incident_id, publication_id)
        if event is None:
            return None
        relative = Path(event["series_ref"]["artifact_uri"])
        target = (self.read_model_root / relative).resolve(strict=True)
        if self.read_model_root not in target.parents:
            raise DataLayerIntegrityError("紧凑序列路径越过读模型根目录")
        raw = target.read_bytes()
        if _sha256(raw) != event["series_ref"]["artifact_sha256"]:
            raise DataLayerIntegrityError("紧凑序列物理摘要不一致")
        try:
            payload = json.loads(gzip.decompress(raw))
        except (OSError, json.JSONDecodeError) as error:
            raise DataLayerIntegrityError("紧凑序列不可读取") from error
        if (
            payload.get("schema_version") != SERIES_SCHEMA
            or payload.get("candidate_id") != self.selection["candidate_id"]
            or payload.get("point_count") != STATE_POINT_COUNT
            or payload.get("content_sha256") != event["series_ref"]["content_sha256"]
        ):
            raise DataLayerIntegrityError("紧凑序列身份不一致")
        return {
            "schema_version": "country_outage_compact_series_v1",
            **self._metadata(event),
            "series_contract": payload,
            "metric_definitions": [
                {
                    "key": "visible_prefix_vp_count",
                    "label": "固定 cohort 可见 Prefix×VP",
                    "unit": "Prefix×VP",
                    "population": "Seed RIB 固定 Prefix×VP cohort",
                    "definition": "cohort_visible_v4 与 cohort_visible_v6 之和。",
                },
                {
                    "key": "visible_prefix_vp_ratio",
                    "label": "固定 cohort 可见率",
                    "unit": "%",
                    "population": "Seed RIB 固定 Prefix×VP cohort",
                    "definition": "固定 cohort 可见 Prefix×VP 除以固定分母。",
                },
                {
                    "key": "country_update_counts",
                    "label": "国家投影 UPDATE",
                    "unit": "条/300秒",
                    "population": "同一 RRC25 RouteState 投影的国家五分钟指标",
                    "definition": "ANNOUNCE/WITHDRAW 均来自登记 RouteState 的确定性投影。",
                },
            ],
            "resource_series": [],
            "resource_metric_extrema": {},
        }

    def empty_asn_page(
        self,
        incident_id: str,
        publication_id: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any] | None:
        event = self._event(incident_id, publication_id)
        if event is None:
            return None
        return {
            "schema_version": "country_outage_asn_page_v2",
            **self._metadata(event),
            "page": page,
            "page_size": page_size,
            "page_count": 1,
            "total": 0,
            "observed_at_utc": [],
            "observed_at_local": [],
            "state_codes": {},
            "duration_histogram": {
                "fully_visible": {},
                "partially_visible": {},
                "fully_invisible": {},
            },
            "items": [],
        }

    def audit(
        self,
        incident_id: str,
        publication_id: str | None,
    ) -> dict[str, Any] | None:
        event = self._event(incident_id, publication_id)
        if event is None:
            return None
        incident = event["incident"]
        country = self.index["countries"][incident["country_code"]]
        evidence = event["evidence_refs"][0]
        return {
            "schema_version": "country_outage_audit_v2",
            **self._metadata(event),
            "missing_slots": [],
            "run_id": self.manifest["run_id"],
            "artifact_set_id": self.manifest["dataset_id"],
            "engine_version": "domeye-data-layer-224-310/1",
            "algorithm_version": self.manifest["implementation_id"],
            "mapping_version": self.index["mapping_version"],
            "quality_status": "pass",
            "source_system": "immutable_rrc25_read_model",
            "source_table": "event-read-model.tsv.gz + compact series + Prefix×VP Evidence View",
            "source_reference": event["snapshot_id"],
            "evidence_level": "precomputed_read_model_with_route_state_lineage",
            "consumed_deliverable_hashes_verified": True,
            "verified_hashes": {
                "read-model-manifest.json": self.selection[
                    "read_model_manifest_sha256"
                ],
                event["series_ref"]["artifact_uri"]: event["series_ref"][
                    "artifact_sha256"
                ],
                "event-snapshot": event["snapshot_sha256"],
                "prefix-vp-evidence": evidence["content_sha256"],
                "production-index.json": self.selection[
                    "production_index_sha256"
                ],
            },
            "route_state_file": {
                "filename": evidence["derived_from_route_state_id"],
                "recorded_sha256": self.manifest[
                    "source_route_state_content_sha256"
                ],
                "row_count": country["prefix_vp_count"],
                "request_path_scanned": False,
            },
            "input_summary": {
                "rib_count": None,
                "catch_up_update_count": None,
                "formal_update_count": STATE_POINT_COUNT,
                "input_compressed_bytes": None,
                "rib_physical_records": None,
                "rib_entries": None,
                "update_physical_records": None,
                "update_route_events": None,
            },
            "revision_history": [
                {
                    "publication_id": event["observation_publication"][
                        "publication_id"
                    ],
                    "revision": event["observation_publication"]["revision"],
                    "data_through": event["observation_publication"][
                        "data_through"
                    ],
                    "publication_state": "published",
                    "publication_kind": "unified_route_state_projection",
                }
            ],
            "supersedes_publication_id": None,
            "correction_reason": None,
        }

    def trend(
        self,
        incident_id: str,
        publication_id: str | None,
    ) -> dict[str, Any] | None:
        event = self._event(incident_id, publication_id)
        if event is None:
            return None
        incident = event["incident"]
        publication = event["observation_publication"]
        analysis = event["analysis_publication"]
        profile = analysis["trend_profile"]
        country = self.index["countries"][incident["country_code"]]
        snapshot = {
            "incident_id": incident_id,
            "publication_id": publication["publication_id"],
            "revision": publication["revision"],
            "data_through": publication["data_through"],
            "window_start_utc": FIRST_STATE_POINT,
            "window_end_utc": WINDOW_END,
            "collector_id": COLLECTOR_ID,
            "analysis_publication_id": analysis["publication_id"],
        }
        identity = {
            "snapshot": snapshot,
            "analysis_content_sha256": analysis["content_sha256"],
        }
        product_id = _stable_id("trend_product_v1_", identity)
        graph_id = _stable_id("evidence_graph_v1_", identity)
        profile_id = _stable_id("trend_profile_v1_", identity)
        analysis_id = analysis["publication_id"]
        claim_id = _stable_id("claim_v1_", identity)
        evidence_id = _stable_id("evidence_v1_", identity)
        limitation_id = _stable_id("limitation_v1_", identity)
        unknown_id = _stable_id("unknown_v1_", identity)
        direction = {"down": "下降", "up": "上升"}.get(
            profile["direction"], "变化"
        )
        claim_text = (
            f"在冻结的 RRC25 224-310 窗口内，固定 cohort 可见率总体{direction}；"
            "该结论只描述控制面观测。"
        )

        def slot(value: str) -> int:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            start = datetime.fromisoformat(WINDOW_START.replace("Z", "+00:00"))
            return max(1, min(STATE_POINT_COUNT, int((parsed - start).total_seconds() // 300)))

        phases = []
        for fact in event["fact_set"]:
            phase_slot = slot(fact["observed_at"])
            phases.append(
                {
                    "phase_id": fact["fact_id"],
                    "kind": fact["stage"],
                    "start_slot_index": phase_slot,
                    "end_slot_index": phase_slot,
                    "start_value": None,
                    "end_value": None,
                }
            )
        derived_facts = [
            {
                "fact_id": "window_start_ratio",
                "metric": "窗口起点可见率",
                "value": profile["start"],
                "unit": "ratio",
                "formula": "TrendProfile.start",
            },
            {
                "fact_id": "window_end_ratio",
                "metric": "窗口终点可见率",
                "value": profile["end"],
                "unit": "ratio",
                "formula": "TrendProfile.end",
            },
            {
                "fact_id": "window_change_ratio",
                "metric": "窗口变化",
                "value": profile["change"],
                "unit": "ratio",
                "formula": "end - start",
            },
            {
                "fact_id": "window_minimum_ratio",
                "metric": "窗口最小值",
                "value": profile["minimum"]["value"],
                "unit": "ratio",
                "formula": "min(compact_series)",
            },
        ]
        nodes = [
            {
                "node_id": claim_id,
                "node_type": "Claim",
                "claim_kind": "window_trend",
                "text": claim_text,
                "evidence_refs": [evidence_id],
                "limitation_refs": [limitation_id],
                "unknown_refs": [unknown_id],
                "conclusion_level": "rrc25_control_plane_observation",
            },
            {
                "node_id": evidence_id,
                "node_type": "Evidence",
                "evidence_kind": "immutable_analysis_publication",
                "label": "预计算 TrendProfile 与 Prefix×VP Evidence View",
                "snapshot_ref": snapshot,
                "source_refs": [
                    analysis["publication_id"],
                    event["series_ref"]["series_id"],
                    event["evidence_refs"][0]["evidence_view_id"],
                ],
            },
            {
                "node_id": limitation_id,
                "node_type": "Limitation",
                "code": "control_plane_only",
                "text": event["limitations"][0],
            },
            {
                "node_id": unknown_id,
                "node_type": "Unknown",
                "code": "cause_and_user_impact_unknown",
                "text": "原因、用户影响、业务影响和窗口外真实恢复均未知。",
            },
        ]
        edges = [
            {"from": claim_id, "relation": "supported_by", "to": evidence_id},
            {"from": claim_id, "relation": "limited_by", "to": limitation_id},
            {"from": claim_id, "relation": "unknown_about", "to": unknown_id},
        ]
        return {
            "schema_version": "country_outage_trend_product_v1",
            "product_id": product_id,
            "graph_id": graph_id,
            "profile_id": profile_id,
            "analysis_id": analysis_id,
            "snapshot": snapshot,
            "profile": {
                "schema_version": "country_outage_trend_profile_v1",
                "profile_id": profile_id,
                "quality": {
                    "status": "complete",
                    "observed_slot_count": STATE_POINT_COUNT,
                    "expected_slot_count": STATE_POINT_COUNT,
                },
                "metric": {
                    "label": "固定 cohort Prefix×VP 可见率",
                    "statistical_population": "RRC25 Seed RIB 固定 Prefix×VP cohort",
                    "denominator": {
                        "value": country["prefix_vp_count"],
                        "statistical_population": "固定 Prefix×VP cohort",
                    },
                },
                "baseline": {"type": "fixed_seed_rib_population"},
                "analysis": {
                    "source_trend_profile": profile,
                    "phases": phases,
                    "derived_facts": derived_facts,
                },
            },
            "contexts": {
                "address_family": None,
                "asn": None,
                "activity": None,
                "contemporaneous_reference": None,
            },
            "evidence_graph": {
                "schema_version": "country_outage_evidence_graph_v1",
                "algorithm_version": "domeye-data-layer-224-310/1",
                "graph_id": graph_id,
                "profile_id": profile_id,
                "analysis_id": analysis_id,
                "snapshot": snapshot,
                "nodes": nodes,
                "edges": edges,
                "node_types": ["Claim", "Evidence", "Limitation", "Unknown"],
                "relation_types": ["supported_by", "limited_by", "unknown_about"],
                "hypothesis_nodes_allowed": False,
                "causal_relations_allowed": False,
            },
            "reading_journey": ["profile", "claim", "evidence", "limitation", "unknown"],
            "claim_ids": [claim_id],
            "qa_rule_version": "rrc25-control-plane-only/v1",
            "render_contract": {
                "source_product_id": product_id,
                "surfaces": ["page", "report", "qa", "json_download"],
                "model_may_rewrite_deterministic_values": False,
            },
            "event_identity": incident,
            "observation_scope": snapshot,
            "capabilities": {"trend_analysis": {"state": "available"}},
        }


_runtime: DataLayer224310Runtime | None = None
_runtime_key: str | None = None
_runtime_lock = Lock()


def _selection_path() -> Path:
    configured = os.environ.get(SELECTION_ENV, "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(__file__).resolve().parents[2] / "data-layer" / "PRODUCTION-SELECTION.json"
    if not path.is_file():
        raise DataLayerNotConfigured(str(path))
    return path


def data_layer_runtime() -> DataLayer224310Runtime:
    global _runtime, _runtime_key
    path = _selection_path()
    key = str(path.resolve())
    with _runtime_lock:
        if _runtime is None or _runtime_key != key:
            _runtime = DataLayer224310Runtime(path)
            _runtime_key = key
        return _runtime


def reset_data_layer_runtime_for_tests() -> None:
    global _runtime, _runtime_key
    with _runtime_lock:
        _runtime = None
        _runtime_key = None
