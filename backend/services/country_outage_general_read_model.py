"""国家中断通用观测页的不可变事件读模型运行时。"""

from __future__ import annotations

from functools import lru_cache
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
from threading import Lock
from typing import Any, Mapping


READ_MODEL_ENV = "DOMEYE_COUNTRY_OUTAGE_GENERAL_READ_MODEL"
STORE_SCHEMA = "country-outage-general-read-model-store/v1"
EVENT_SCHEMA = "country-outage-general-event-read-model/v1"
OVERVIEW_ARTIFACT_SCHEMA = "country-outage-general-overview-artifact/v1"
SERIES_ARTIFACT_SCHEMA = "country-outage-general-series-artifact/v1"
ASN_ROW_SCHEMA = "country-outage-general-affected-as/v1"
DOWNSTREAM_ROW_SCHEMA = "country-outage-general-path-downstream/v1"
COLLECTOR_ID = "rrc25"
WINDOW_START_UTC = "2026-02-24T00:00:00Z"
WINDOW_END_EXCLUSIVE_UTC = "2026-03-11T00:00:00Z"
REFERENCE_PATTERN = re.compile(
    r"^country_outage/(?P<start>\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2})/"
    r"(?P<country>[A-Za-z]{2})/(?P<event_id>[1-9]\d*)/(?P<source>[A-Za-z0-9_-]+)$"
)


class GeneralReadModelNotConfigured(RuntimeError):
    """当前进程没有选择新版事件读模型。"""


class GeneralReadModelIntegrityError(RuntimeError):
    """读模型身份、摘要、人口或文件合同冲突。"""


class GeneralReadModelPublicationNotFound(LookupError):
    """事件由新版读模型拥有，但请求的不可变 Publication 不存在。"""


class GeneralReadModelInvalidQuery(ValueError):
    """新版事件读模型拥有该事件，但筛选参数不属于冻结合同。"""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _content_sha(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("content_sha256", None)
    return _sha256(_canonical_json(payload))


def _canonical_reference(value: str) -> str | None:
    match = REFERENCE_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    start = match.group("start").replace("+", " ")
    return "/".join(
        (
            "country_outage",
            start,
            match.group("country").upper(),
            match.group("event_id"),
            match.group("source"),
        )
    )


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise GeneralReadModelIntegrityError("新版国家中断读模型 JSON 不可读取") from error
    if not isinstance(value, dict):
        raise GeneralReadModelIntegrityError("新版国家中断读模型 JSON 顶层无效")
    return value, raw


@lru_cache(maxsize=256)
def _read_gzip_json_cached(
    path_text: str,
    size_bytes: int,
    sha256: str,
    content_sha256: str,
) -> dict[str, Any]:
    path = Path(path_text)
    try:
        compressed = path.read_bytes()
        raw = gzip.decompress(compressed)
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise GeneralReadModelIntegrityError("新版国家中断对象制品不可读取") from error
    if (
        len(compressed) != size_bytes
        or _sha256(compressed) != sha256
        or _sha256(raw) != content_sha256
        or not isinstance(value, dict)
    ):
        raise GeneralReadModelIntegrityError("新版国家中断对象制品摘要冲突")
    return value


@lru_cache(maxsize=256)
def _read_gzip_jsonl_cached(
    path_text: str,
    row_count: int,
    size_bytes: int,
    sha256: str,
    content_sha256: str,
) -> tuple[dict[str, Any], ...]:
    path = Path(path_text)
    try:
        compressed = path.read_bytes()
        raw = gzip.decompress(compressed)
        rows = tuple(json.loads(line) for line in raw.splitlines())
    except (OSError, json.JSONDecodeError) as error:
        raise GeneralReadModelIntegrityError("新版国家中断列表制品不可读取") from error
    if (
        len(compressed) != size_bytes
        or _sha256(compressed) != sha256
        or _sha256(raw) != content_sha256
        or len(rows) != row_count
        or not all(isinstance(row, dict) for row in rows)
    ):
        raise GeneralReadModelIntegrityError("新版国家中断列表制品摘要或人口冲突")
    return rows


class CountryOutageGeneralReadModelRuntime:
    """只读加载一个完整候选，不跨候选或旧读模型拼接结果。"""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise GeneralReadModelIntegrityError("新版国家中断读模型根目录无效")
        self.manifest, left = _read_json(self.root / "manifest.json")
        complete, right = _read_json(self.root / "COMPLETE.json")
        if left != right or self.manifest != complete:
            raise GeneralReadModelIntegrityError("新版读模型 manifest 与 COMPLETE 不一致")
        self.manifest_sha256 = _sha256(left)
        self._validate_manifest()
        self.events: dict[str, dict[str, Any]] = {}
        self.events_by_reference: dict[str, str] = {}
        totals = {
            "state_point_count": 0,
            "affected_as_count": 0,
            "path_downstream_relation_count": 0,
            "path_sample_count": 0,
        }
        for event in self.manifest["events"]:
            self._validate_event(event)
            incident_id = event["incident_id"]
            reference = _canonical_reference(event["legacy_reference"])
            if (
                reference is None
                or incident_id in self.events
                or reference in self.events_by_reference
            ):
                raise GeneralReadModelIntegrityError("新版读模型事件身份重复或无效")
            self.events[incident_id] = event
            self.events_by_reference[reference] = incident_id
            for key in totals:
                totals[key] += event[key]
        if len(self.events) != self.manifest["event_count"]:
            raise GeneralReadModelIntegrityError("新版读模型事件人口冲突")
        for key, value in totals.items():
            if value != self.manifest[key]:
                raise GeneralReadModelIntegrityError(f"新版读模型根人口冲突：{key}")

    def _validate_manifest(self) -> None:
        expected = {
            "schema_version": STORE_SCHEMA,
            "status": "complete",
            "collector_id": COLLECTOR_ID,
            "window_start_utc": WINDOW_START_UTC,
            "window_end_exclusive_utc": WINDOW_END_EXCLUSIVE_UTC,
            "api_read_semantics": "precompiled_event_window_read_model_only",
            "pagination_semantics": "stable_server_side_pages_maximum_60_items",
            "path_evidence_semantics": "bounded_real_path_samples_full_evidence_remains_in_s3_audit_artifact",
            "causal_boundary": "rrc25_path_association_is_not_dependency_propagation_user_impact_or_cause",
        }
        for key, value in expected.items():
            if self.manifest.get(key) != value:
                raise GeneralReadModelIntegrityError(f"新版读模型字段冲突：{key}")
        for key in (
            "run_id",
            "dataset_id",
            "implementation_id",
            "source_event_cohort_dataset_id",
            "source_event_cohort_content_sha256",
            "source_event_metric_dataset_id",
            "source_event_metric_content_sha256",
            "source_event_as_path_dataset_id",
            "source_event_as_path_content_sha256",
            "source_lifecycle_snapshot_id",
        ):
            if not isinstance(self.manifest.get(key), str) or not self.manifest[key]:
                raise GeneralReadModelIntegrityError(f"新版读模型缺少身份：{key}")
        if (
            not isinstance(self.manifest.get("events"), list)
            or self.manifest.get("content_sha256") != _content_sha(self.manifest)
        ):
            raise GeneralReadModelIntegrityError("新版读模型根内容摘要冲突")

    def _validate_event(self, event: Mapping[str, Any]) -> None:
        if (
            event.get("schema_version") != EVENT_SCHEMA
            or event.get("status") != "complete"
            or event.get("revision") != 1
            or event.get("publication_state") != "published"
            or event.get("content_sha256") != _content_sha(event)
        ):
            raise GeneralReadModelIntegrityError("新版读模型事件合同冲突")
        for key in (
            "event_read_model_id",
            "publication_id",
            "incident_id",
            "legacy_reference",
            "country_code",
            "cohort_id",
            "event_metric_id",
            "event_as_path_id",
            "window_start_utc",
            "window_end_utc",
            "data_through",
            "lifecycle_state",
        ):
            if not isinstance(event.get(key), str) or not event[key]:
                raise GeneralReadModelIntegrityError(f"新版事件缺少字段：{key}")
        if event["window_end_utc"] != event["data_through"]:
            raise GeneralReadModelIntegrityError("新版事件截止点与窗口终点冲突")
        for key in (
            "state_point_count",
            "affected_as_count",
            "path_downstream_relation_count",
            "path_sample_count",
        ):
            if not isinstance(event.get(key), int) or event[key] < 0:
                raise GeneralReadModelIntegrityError(f"新版事件人口无效：{key}")
        for key in ("overview", "series", "affected_as", "path_downstreams"):
            self._artifact_path(event[key])

    def _artifact_path(self, meta: Mapping[str, Any]) -> Path:
        if not isinstance(meta, Mapping):
            raise GeneralReadModelIntegrityError("新版事件文件元数据无效")
        for key in ("path", "size_bytes", "sha256", "content_sha256"):
            if key not in meta:
                raise GeneralReadModelIntegrityError(f"新版事件文件缺少字段：{key}")
        try:
            path = (self.root / str(meta["path"])).resolve(strict=True)
        except OSError as error:
            raise GeneralReadModelIntegrityError("新版事件文件不存在") from error
        if self.root not in path.parents or path.is_symlink() or not path.is_file():
            raise GeneralReadModelIntegrityError("新版事件文件越界或类型无效")
        return path

    def _object(self, event: Mapping[str, Any], key: str) -> dict[str, Any]:
        meta = event[key]
        path = self._artifact_path(meta)
        return _read_gzip_json_cached(
            str(path),
            int(meta["size_bytes"]),
            str(meta["sha256"]),
            str(meta["content_sha256"]),
        )

    def _rows(self, event: Mapping[str, Any], key: str) -> tuple[dict[str, Any], ...]:
        meta = event[key]
        path = self._artifact_path(meta)
        return _read_gzip_jsonl_cached(
            str(path),
            int(meta["row_count"]),
            int(meta["size_bytes"]),
            str(meta["sha256"]),
            str(meta["content_sha256"]),
        )

    def _event(
        self,
        incident_id: str,
        publication_id: str | None,
    ) -> dict[str, Any] | None:
        event = self.events.get(incident_id)
        if event is None:
            return None
        if publication_id and publication_id != event["publication_id"]:
            raise GeneralReadModelPublicationNotFound(publication_id)
        return event

    def owns_incident(self, incident_id: str) -> bool:
        return incident_id in self.events

    @staticmethod
    def _metadata(event: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "revision": event["revision"],
            "publication_id": event["publication_id"],
            "publication_state": event["publication_state"],
            "observation_state": "evidence_complete",
            "data_mode": "replay",
            "data_through": event["data_through"],
            "is_final_in_data_range": event["is_final_in_data_range"],
            "lifecycle_state": event["lifecycle_state"],
            "quality_state": "complete",
            "missing_slot_count": 0,
            "collector_id": COLLECTOR_ID,
            "incident_id": event["incident_id"],
            "cohort_id": event["cohort_id"],
            "window_start_utc": event["window_start_utc"],
            "window_end_utc": event["window_end_utc"],
        }

    def resolve(self, reference: str) -> dict[str, Any] | None:
        canonical = _canonical_reference(reference)
        if canonical is None:
            return None
        incident_id = self.events_by_reference.get(canonical)
        if incident_id is None:
            return None
        event = self.events[incident_id]
        overview = self._object(event, "overview")
        return {
            "schema_version": "country_outage_general_resolution_v1",
            **self._metadata(event),
            "legacy_reference": canonical,
            "event_type": "country_outage",
            "country_code": event["country_code"],
            "latest_revision": event["revision"],
            "capabilities": overview["capabilities"],
        }

    def overview(
        self,
        incident_id: str,
        publication_id: str | None,
    ) -> dict[str, Any] | None:
        event = self._event(incident_id, publication_id)
        if event is None:
            return None
        source = self._object(event, "overview")
        if (
            source.get("schema_version") != OVERVIEW_ARTIFACT_SCHEMA
            or source.get("event_read_model_id") != event["event_read_model_id"]
            or source.get("publication_id") != event["publication_id"]
            or source.get("content_sha256") != _content_sha(source)
        ):
            raise GeneralReadModelIntegrityError("新版 overview 身份冲突")
        return {
            "schema_version": "country_outage_general_overview_v1",
            **self._metadata(event),
            "event": {
                key: source[key]
                for key in (
                    "legacy_reference",
                    "country_code",
                    "detected_at_utc",
                    "event_end_at_utc",
                    "event_duration_seconds",
                )
            },
            "interval_seconds": source["interval_seconds"],
            "state_point_count": source["state_point_count"],
            "cohort": source["cohort"],
            "current": source["final_values"],
            "peaks": source["peaks"],
            "affected_as_count": source["affected_as_count"],
            "route_interrupted_as_count": source["route_interrupted_as_count"],
            "path_downstream_relation_count": source["path_downstream_relation_count"],
            "concurrent_path_downstream_relation_count": source[
                "concurrent_path_downstream_relation_count"
            ],
            "capabilities": source["capabilities"],
            "semantic_boundary": source["semantic_boundary"],
        }

    def series(
        self,
        incident_id: str,
        publication_id: str | None,
    ) -> dict[str, Any] | None:
        event = self._event(incident_id, publication_id)
        if event is None:
            return None
        source = self._object(event, "series")
        if (
            source.get("schema_version") != SERIES_ARTIFACT_SCHEMA
            or source.get("event_read_model_id") != event["event_read_model_id"]
            or source.get("publication_id") != event["publication_id"]
            or source.get("point_count") != event["state_point_count"]
            or source.get("content_sha256") != _content_sha(source)
        ):
            raise GeneralReadModelIntegrityError("新版 series 身份或人口冲突")
        timestamps = source.get("timestamps")
        tracks = source.get("tracks")
        if (
            not isinstance(timestamps, list)
            or not isinstance(tracks, dict)
            or len(timestamps) != source["point_count"]
            or any(not isinstance(values, list) or len(values) != len(timestamps) for values in tracks.values())
        ):
            raise GeneralReadModelIntegrityError("新版 series 轨道人口冲突")
        return {
            "schema_version": "country_outage_general_series_v1",
            **self._metadata(event),
            "interval_seconds": source["interval_seconds"],
            "point_count": source["point_count"],
            "timestamps": timestamps,
            "track_definitions": source["track_definitions"],
            "tracks": tracks,
        }

    @staticmethod
    def _page(
        rows: list[dict[str, Any]],
        *,
        page: int,
        page_size: int,
    ) -> tuple[int, int, int, list[dict[str, Any]]]:
        page = max(1, page)
        page_size = min(60, max(1, page_size))
        total = len(rows)
        page_count = max(1, (total + page_size - 1) // page_size)
        page = min(page, page_count)
        start = (page - 1) * page_size
        return page, page_size, page_count, rows[start : start + page_size]

    def affected_asns(
        self,
        incident_id: str,
        publication_id: str | None,
        *,
        page: int,
        page_size: int,
        query: str = "",
        classification: str = "all",
        sort: str = "default",
    ) -> dict[str, Any] | None:
        event = self._event(incident_id, publication_id)
        if event is None:
            return None
        if classification not in {"all", "affected", "route_interrupted"}:
            raise GeneralReadModelInvalidQuery("classification 参数无效")
        if sort not in {"default", "asn_asc"}:
            raise GeneralReadModelInvalidQuery("sort 参数无效")
        rows = []
        normalized = query.strip().lower().removeprefix("as")
        for source in self._rows(event, "affected_as"):
            if (
                source.get("schema_version") != ASN_ROW_SCHEMA
                or source.get("event_read_model_id") != event["event_read_model_id"]
                or source.get("publication_id") != event["publication_id"]
            ):
                raise GeneralReadModelIntegrityError("新版受影响 AS 行身份冲突")
            if classification in {"affected", "route_interrupted"} and source["event_classification"] != classification:
                continue
            haystack = " ".join(
                str(source.get(key) or "").lower()
                for key in ("asn", "as_name", "organization", "nature")
            )
            if normalized and normalized not in haystack:
                continue
            rows.append(
                {
                    key: source[key]
                    for key in (
                        "rank",
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
                }
            )
        if sort == "asn_asc":
            rows.sort(key=lambda row: row["asn"])
        page, page_size, page_count, items = self._page(
            rows, page=page, page_size=page_size
        )
        return {
            "schema_version": "country_outage_general_affected_as_page_v1",
            **self._metadata(event),
            "page": page,
            "page_size": page_size,
            "page_count": page_count,
            "total": len(rows),
            "classification": classification,
            "query": query,
            "sort": sort,
            "items": items,
        }

    def path_downstreams(
        self,
        incident_id: str,
        publication_id: str | None,
        *,
        page: int,
        page_size: int,
        affected_asn: int | None = None,
        scope: str = "all",
        query: str = "",
    ) -> dict[str, Any] | None:
        event = self._event(incident_id, publication_id)
        if event is None:
            return None
        rows = []
        normalized = query.strip().lower().removeprefix("as")
        for source in self._rows(event, "path_downstreams"):
            if (
                source.get("schema_version") != DOWNSTREAM_ROW_SCHEMA
                or source.get("event_read_model_id") != event["event_read_model_id"]
                or source.get("publication_id") != event["publication_id"]
            ):
                raise GeneralReadModelIntegrityError("新版路径关联行身份冲突")
            if affected_asn is not None and source["affected_asn"] != affected_asn:
                continue
            if scope == "concurrent" and source["concurrent_state_point_count"] == 0:
                continue
            haystack = " ".join(
                str(source.get(key) or "").lower()
                for key in (
                    "affected_asn",
                    "downstream_asn",
                    "downstream_as_name",
                    "downstream_organization",
                    "downstream_nature",
                )
            )
            if normalized and normalized not in haystack:
                continue
            rows.append(
                {
                    key: source[key]
                    for key in (
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
                        "path_samples",
                        "relationship_semantics",
                    )
                }
            )
        page, page_size, page_count, items = self._page(
            rows, page=page, page_size=page_size
        )
        return {
            "schema_version": "country_outage_general_path_downstream_page_v1",
            **self._metadata(event),
            "page": page,
            "page_size": page_size,
            "page_count": page_count,
            "total": len(rows),
            "affected_asn": affected_asn,
            "scope": scope,
            "query": query,
            "relationship_semantics": "observed_ordered_rrc25_path_association_not_dependency_or_cause",
            "items": items,
        }

    def audit(
        self,
        incident_id: str,
        publication_id: str | None,
    ) -> dict[str, Any] | None:
        event = self._event(incident_id, publication_id)
        if event is None:
            return None
        return {
            "schema_version": "country_outage_general_audit_v1",
            **self._metadata(event),
            "run_id": self.manifest["run_id"],
            "dataset_id": self.manifest["dataset_id"],
            "implementation_id": self.manifest["implementation_id"],
            "manifest_sha256": self.manifest_sha256,
            "event_read_model_id": event["event_read_model_id"],
            "event_content_sha256": event["content_sha256"],
            "source_identities": {
                key: self.manifest[key]
                for key in (
                    "source_event_cohort_dataset_id",
                    "source_event_cohort_content_sha256",
                    "source_event_cohort_manifest_sha256",
                    "source_event_metric_dataset_id",
                    "source_event_metric_content_sha256",
                    "source_event_metric_manifest_sha256",
                    "source_event_as_path_dataset_id",
                    "source_event_as_path_content_sha256",
                    "source_event_as_path_manifest_sha256",
                    "source_lifecycle_snapshot_id",
                    "source_lifecycle_snapshot_content_sha256",
                )
            },
            "files": {
                key: event[key]
                for key in ("overview", "series", "affected_as", "path_downstreams")
            },
            "causal_boundary": self.manifest["causal_boundary"],
        }


_runtime: CountryOutageGeneralReadModelRuntime | None = None
_runtime_key: str | None = None
_runtime_lock = Lock()


def _root() -> Path:
    configured = os.environ.get(READ_MODEL_ENV, "").strip()
    if not configured:
        raise GeneralReadModelNotConfigured(READ_MODEL_ENV)
    path = Path(configured)
    if not path.is_absolute() or not path.is_dir():
        raise GeneralReadModelIntegrityError(
            "已选择的新版国家中断读模型目录不存在或不是绝对目录"
        )
    return path


def country_outage_general_read_model() -> CountryOutageGeneralReadModelRuntime:
    global _runtime, _runtime_key
    root = _root().resolve()
    key = str(root)
    with _runtime_lock:
        if _runtime is None or _runtime_key != key:
            _runtime = CountryOutageGeneralReadModelRuntime(root)
            _runtime_key = key
        return _runtime


def reset_country_outage_general_read_model_for_tests() -> None:
    global _runtime, _runtime_key
    with _runtime_lock:
        _runtime = None
        _runtime_key = None
        _read_gzip_json_cached.cache_clear()
        _read_gzip_jsonl_cached.cache_clear()
