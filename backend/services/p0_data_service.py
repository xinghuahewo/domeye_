"""P0 候选数据制品的只读准入服务。

服务只在显式配置 ``P0_DATA_RELEASE_DIR`` 时读取候选仓库。仓库固定包含
``d2``、``d3``、``d4``、``metric`` 和 ``quality`` 五个平铺组件目录；
每个目录都必须有覆盖其余全部普通文件的 ``SHA256SUMS``。本模块不导入
数据库连接、不写文件、不激活生产，只把已经通过组件准入且引用闭合的
MetricSeries、Evidence Bundle v2 和质量报告投影为 API 数据。

缓存以目录和文件的 device/inode/size/mtime/ctime 以及已校验 SHA256 为
边界。任一元数据变化都会重新加载；读取前后发生变化、软链接、重复 JSON
键、路径穿越、哈希或跨组件身份不一致都会失败关闭，绝不转换为空数据。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import threading
import zlib
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Sequence, Tuple

from data_pipeline.evidence import EvidenceBundleError, validate_reference_closure
from data_pipeline.metrics import METRIC_DEFINITIONS
from data_pipeline.quality import QualityGateInputError, validate_report_semantics


P0_DATA_RELEASE_ENV = "P0_DATA_RELEASE_DIR"
P0_DATA_PRODUCTION_ACTIVE_ENV = "P0_DATA_PRODUCTION_ACTIVE"
COMPONENT_NAMES = ("d2", "d3", "d4", "metric", "quality")
EVENT_TYPES = (
    "hijack",
    "sub_hijack",
    "leak",
    "prefix_outage",
    "as_outage",
    "country_outage",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
INCIDENT_ID_RE = re.compile(r"^inc_v1_[0-9a-f]{24}$")
METRIC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,95}$")
SAFE_FILE_RE = re.compile(r"^[^/\\\x00\r\n]+$")
D3_ARTIFACT_FILE_RE = re.compile(
    r"^(?P<family>updates|bview|rib)\."
    r"(?P<date>[0-9]{8})\."
    r"(?P<time>[0-9]{4})\."
    r"(?P<compression>gz|bz2)$"
)
D3_INVALID_REASONS = (
    "compressed_stream_invalid",
    "compression_magic_mismatch",
    "empty_file",
)
D3_INVALID_RECORD_FIELDS = frozenset(
    {
        "collector_id",
        "artifact_type",
        "artifact_time_utc",
        "relative_path",
        "filename_family",
        "compression",
        "size_bytes",
        "file_sha256",
        "value_state",
        "missing_reason",
    }
)
D3_ARTIFACT_RECORD_FIELDS = frozenset(
    {
        "artifact_id",
        "artifact_id_schema",
        "collector_id",
        "artifact_type",
        "artifact_time_utc",
        "relative_path",
        "filename_family",
        "compression",
        "size_bytes",
        "file_sha256",
    }
)
D3_SLOT_SECONDS = {"update": 300, "rib": 8 * 60 * 60}
METRIC_POINT_FIELDS = frozenset(
    {"time", "value", "value_state", "missing_reason", "formula_inputs"}
)
METRIC_COVERAGE_FIELDS = frozenset(
    {
        "source_coverage_ratio",
        "metric_coverage_ratio",
        "subject_activity_density",
        "source_gap_sample_count",
        "processing_gap_sample_count",
        "classification_complete",
    }
)
D3_ALIGNED_METRICS = frozenset(
    {
        "bgp_announce_record_count",
        "bgp_withdraw_record_count",
        "bgp_update_record_count",
        "bgp_withdraw_ratio",
        "ipv4_24_equivalent_count",
        "ipv6_48_equivalent_count",
        "ipv4_equivalent_address_count",
    }
)
MAX_CHECKSUM_BYTES = 4 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_GZIP_COMPRESSED_BYTES = 512 * 1024 * 1024
MAX_GZIP_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_JSON_LINE_BYTES = 64 * 1024 * 1024
MAX_METRIC_RECORDS = 4096
UTC = timezone.utc

COMPONENT_MAX_TOTAL_BYTES = {
    "d2": 1024 * 1024 * 1024,
    "d3": 128 * 1024 * 1024,
    "d4": 512 * 1024 * 1024,
    "metric": 1024 * 1024 * 1024,
    "quality": 1024 * 1024 * 1024,
}
QUALITY_REQUIRED_FILES = frozenset(
    {
        "data-quality-report.json",
        "失败明细.jsonl.gz",
        "中文摘要.md",
        "输入闭包.json",
        "d2-candidate-manifest.json",
        "d2-original-candidate-manifest.json",
        "d3-artifact-manifest.json",
        "d3-artifact-verification-summary.json",
        "route-event-reconciliation-summary.json",
        "evidence-reconciliation-summary.json",
        "metric-reconciliation-summary.json",
        "reproducibility-summary.json",
        "quality-gate-execution-context.json",
        "data-profile.json",
    }
)
QUALITY_SOURCE_INPUT_FILES = {
    "d2": "d2-candidate-manifest.json",
    "d2_original": "d2-original-candidate-manifest.json",
    "d2_audited": "d2-candidate-manifest.json",
    "d3": "d3-artifact-manifest.json",
    "route": "route-event-reconciliation-summary.json",
    "evidence": "evidence-reconciliation-summary.json",
    "metric": "metric-reconciliation-summary.json",
    "repro": "reproducibility-summary.json",
    "execution": "quality-gate-execution-context.json",
    "d3_verification": "d3-artifact-verification-summary.json",
    "profile": "data-profile.json",
}

METRIC_KEYS = frozenset(
    {
        "schema_version",
        "metric_name",
        "subject",
        "collector_scope",
        "window",
        "granularity_seconds",
        "unit",
        "aggregation",
        "formula",
        "formula_version",
        "expected_sample_count",
        "source_observed_sample_count",
        "metric_observed_sample_count",
        "subject_active_sample_count",
        "coverage",
        "points",
        "source_refs",
        "generated_at",
        "ranking_scope",
    }
)
MISSING_STATES = frozenset(
    {
        "not_observed",
        "source_unavailable",
        "processing_gap",
        "parse_failed",
        "not_retained",
        "not_applicable",
        "legacy_unknown",
        "invalid_identity",
        "legacy_window_contamination",
        "source_fact_collision",
    }
)
EVIDENCE_RECONCILIATION_BLOCKING_COUNTS = (
    "schema_invalid_count",
    "classification_violation_count",
    "causal_conclusion_nonnull_count",
    "evidence_id_conflict_count",
    "unresolved_evidence_reference_count",
    "unresolved_route_event_reference_count",
    "outside_window_record_count",
    "unknown_missing_reason_count",
    "auto_zero_fill_count",
)
EVIDENCE_RECONCILIATION_KEYS = frozenset(
    {
        "schema_version",
        "scope",
        "sample_only",
        "population_coverage_claimed",
        "bundle_count",
        "event_type_count",
        "event_types",
        "bundle_ids",
        "strict_schema_status",
        "schema_sha256",
        "reference_closure_status",
        *EVIDENCE_RECONCILIATION_BLOCKING_COUNTS,
        "legacy_unknown_value_count",
        "classification",
        "causal_conclusion",
        "summary_fingerprint_sha256",
    }
)


class P0DataError(RuntimeError):
    """P0 API 可稳定映射的只读候选错误。"""

    status_code = 500
    error_code = "p0_internal_error"

    def as_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": "p0_error_v1",
            "status": "error",
            "error": {
                "code": self.error_code,
                "message_zh": str(self),
            },
        }


class P0DataBadRequest(P0DataError):
    status_code = 400
    error_code = "invalid_identifier"


class P0DataNotFound(P0DataError):
    status_code = 404
    error_code = "candidate_resource_not_found"


class P0DataConflict(P0DataError):
    status_code = 409
    error_code = "candidate_artifact_conflict"


class P0DataUnavailable(P0DataError):
    status_code = 503
    error_code = "candidate_repository_unavailable"


@dataclass(frozen=True)
class FileStamp:
    mode: int
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class Layout:
    root: Path
    stamps: Tuple[Tuple[str, FileStamp], ...]
    component_files: Tuple[Tuple[str, Tuple[str, ...]], ...]

    def stamp_map(self) -> Dict[str, FileStamp]:
        return dict(self.stamps)

    def files_for(self, component: str) -> Tuple[str, ...]:
        return dict(self.component_files)[component]


@dataclass(frozen=True)
class Component:
    name: str
    directory: Path
    checksums: Mapping[str, str]
    checksum_file_sha256: str


@dataclass(frozen=True)
class ReleaseSnapshot:
    root: Path
    repository_fingerprint_sha256: str
    d2: Mapping[str, Any]
    d3: Mapping[str, Any]
    d4: Mapping[str, Any]
    metric_manifest: Mapping[str, Any]
    quality: Mapping[str, Any]
    metrics: Mapping[str, Mapping[str, Any]]
    evidence_by_incident: Mapping[str, Mapping[str, Any]]
    evidence_files: Mapping[str, str]
    raw_coverage: Mapping[str, Any]


@dataclass(frozen=True)
class D3SlotClosure:
    """D3 固定窗口逐槽分类，仅供同次候选加载中的 Metric 对账。"""

    collectors: Tuple[str, ...]
    update_expected_slots: Tuple[datetime, ...]
    states_by_collector: Mapping[str, Mapping[datetime, str]]


@dataclass(frozen=True)
class CacheEntry:
    layout: Layout
    snapshot: ReleaseSnapshot


_CACHE_LOCK = threading.RLock()
_CACHE: Optional[CacheEntry] = None


def reset_p0_data_cache() -> None:
    """清空进程内只读缓存，供配置切换与测试使用。"""

    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise P0DataConflict("候选 JSON 包含不可序列化或非有限值") from error


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _reject_constant(value: str) -> None:
    raise P0DataConflict("候选 JSON 禁止非有限数值：{}".format(value))


def _unique_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise P0DataConflict("候选 JSON 对象字段重复：{}".format(key))
        result[key] = value
    return result


def _strict_json(payload: bytes, label: str) -> Mapping[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise P0DataConflict("{}必须是严格 UTF-8".format(label)) from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise P0DataConflict("{}不是合法 JSON：{}".format(label, error.msg)) from error
    if not isinstance(value, Mapping):
        raise P0DataConflict("{}顶层必须是 JSON 对象".format(label))
    return value


def _stamp(metadata: os.stat_result) -> FileStamp:
    return FileStamp(
        mode=metadata.st_mode,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
        ctime_ns=metadata.st_ctime_ns,
    )


def _lstat(path: Path, label: str, *, unavailable: bool = False) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        exception = P0DataUnavailable if unavailable else P0DataConflict
        raise exception("{}不可用".format(label)) from error
    if stat.S_ISLNK(metadata.st_mode):
        raise P0DataConflict("{}禁止使用符号链接".format(label))
    return metadata


def _configured_root() -> Path:
    raw = os.environ.get(P0_DATA_RELEASE_ENV, "").strip()
    if not raw:
        raise P0DataUnavailable(
            "未配置 {}，P0 候选数据 API 默认关闭".format(P0_DATA_RELEASE_ENV)
        )
    if "\x00" in raw:
        raise P0DataUnavailable("{} 配置非法".format(P0_DATA_RELEASE_ENV))
    root = Path(raw)
    if not root.is_absolute():
        raise P0DataUnavailable("{} 必须配置绝对路径".format(P0_DATA_RELEASE_ENV))
    metadata = _lstat(root, "P0 候选仓库", unavailable=True)
    if not stat.S_ISDIR(metadata.st_mode):
        raise P0DataUnavailable("P0 候选仓库不是目录")
    return root


def _scan_layout(root: Path) -> Layout:
    root_metadata = _lstat(root, "P0 候选仓库", unavailable=True)
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise P0DataUnavailable("P0 候选仓库不是目录")
    stamps: Dict[str, FileStamp] = {".": _stamp(root_metadata)}
    try:
        root_entries = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise P0DataUnavailable("P0 候选仓库不可读取") from error
    entries = {path.name: path for path in root_entries}
    for path in root_entries:
        metadata = _lstat(path, "P0 候选仓库条目")
        stamps[path.name] = _stamp(metadata)
    unexpected_entries = sorted(set(entries) - set(COMPONENT_NAMES))
    if unexpected_entries:
        raise P0DataConflict(
            "P0 候选仓库存在未准入顶层条目：{}".format(
                ", ".join(unexpected_entries)
            )
        )
    component_files = []
    for component_name in COMPONENT_NAMES:
        directory = entries.get(component_name)
        if directory is None:
            raise P0DataConflict("P0 候选仓库缺少 {} 组件".format(component_name))
        directory_metadata = _lstat(directory, "{} 组件目录".format(component_name))
        if not stat.S_ISDIR(directory_metadata.st_mode):
            raise P0DataConflict("{} 组件必须是普通目录".format(component_name))
        try:
            files = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError as error:
            raise P0DataConflict("{} 组件目录不可读取".format(component_name)) from error
        names = []
        for path in files:
            metadata = _lstat(path, "{} 组件文件".format(component_name))
            if not stat.S_ISREG(metadata.st_mode):
                raise P0DataConflict("{} 组件只允许平铺普通文件".format(component_name))
            if not SAFE_FILE_RE.fullmatch(path.name):
                raise P0DataConflict("{} 组件文件名非法".format(component_name))
            relative = "{}/{}".format(component_name, path.name)
            stamps[relative] = _stamp(metadata)
            names.append(path.name)
        if "SHA256SUMS" not in names:
            raise P0DataConflict("{} 组件缺少 SHA256SUMS".format(component_name))
        component_files.append((component_name, tuple(names)))
    return Layout(
        root=root,
        stamps=tuple(sorted(stamps.items())),
        component_files=tuple(component_files),
    )


@contextmanager
def _open_expected(path: Path, expected: FileStamp, label: str) -> Iterator[Any]:
    current = _lstat(path, label)
    if _stamp(current) != expected or not stat.S_ISREG(current.st_mode):
        raise P0DataConflict("{}在候选加载期间发生变化".format(label))
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise P0DataConflict("{}无法只读打开".format(label)) from error
    stream = None
    try:
        opened = os.fstat(descriptor)
        if _stamp(opened) != expected or not stat.S_ISREG(opened.st_mode):
            raise P0DataConflict("{}打开前发生变化".format(label))
        stream = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        yield stream
        if _stamp(os.fstat(stream.fileno())) != expected:
            raise P0DataConflict("{}读取期间发生变化".format(label))
    finally:
        if stream is not None:
            stream.close()
        elif descriptor >= 0:
            os.close(descriptor)


def _relative_stamp(layout: Layout, component: str, name: str) -> FileStamp:
    stamp = layout.stamp_map().get("{}/{}".format(component, name))
    if stamp is None:
        raise P0DataConflict("{} 组件缺少文件 {}".format(component, name))
    return stamp


def _read_file(
    layout: Layout,
    component: str,
    name: str,
    *,
    maximum: int = MAX_JSON_BYTES,
) -> bytes:
    stamp = _relative_stamp(layout, component, name)
    if stamp.size > maximum:
        raise P0DataConflict("{}/{} 超过读取上限".format(component, name))
    chunks = []
    total = 0
    path = layout.root / component / name
    with _open_expected(path, stamp, "{}/{}".format(component, name)) as stream:
        while True:
            block = stream.read(128 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum:
                raise P0DataConflict("{}/{} 超过读取上限".format(component, name))
            chunks.append(block)
    return b"".join(chunks)


def _sha_file(layout: Layout, component: str, name: str) -> str:
    stamp = _relative_stamp(layout, component, name)
    digest = hashlib.sha256()
    path = layout.root / component / name
    with _open_expected(path, stamp, "{}/{}".format(component, name)) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_checksums(payload: bytes, expected_names: Iterable[str], component: str) -> Dict[str, str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise P0DataConflict("{} SHA256SUMS 必须是 UTF-8".format(component)) from error
    result: Dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        matched = re.fullmatch(r"([0-9a-f]{64})  ([^/\\\r\n]+)", line)
        if matched is None:
            raise P0DataConflict("{} SHA256SUMS 第 {} 行非法".format(component, number))
        digest, name = matched.groups()
        if name == "SHA256SUMS" or name in result:
            raise P0DataConflict("{} SHA256SUMS 文件名重复或自引用".format(component))
        result[name] = digest
    expected = set(expected_names) - {"SHA256SUMS"}
    if set(result) != expected:
        raise P0DataConflict("{} SHA256SUMS 文件闭包不一致".format(component))
    return result


def _load_component(layout: Layout, name: str) -> Component:
    files = layout.files_for(name)
    total_size = sum(
        _relative_stamp(layout, name, filename).size for filename in files
    )
    if total_size > COMPONENT_MAX_TOTAL_BYTES[name]:
        raise P0DataConflict("{} 组件总大小超过准入上限".format(name))
    checksum_payload = _read_file(
        layout,
        name,
        "SHA256SUMS",
        maximum=MAX_CHECKSUM_BYTES,
    )
    checksums = _parse_checksums(checksum_payload, files, name)
    for filename, expected in checksums.items():
        if _sha_file(layout, name, filename) != expected:
            raise P0DataConflict("{} 组件 SHA256 校验失败：{}".format(name, filename))
    return Component(
        name=name,
        directory=layout.root / name,
        checksums=checksums,
        checksum_file_sha256=hashlib.sha256(checksum_payload).hexdigest(),
    )


def _require_component_files(
    component: Component, expected: Iterable[str], label: str
) -> None:
    expected_set = set(expected)
    actual = set(component.checksums)
    if actual != expected_set:
        raise P0DataConflict(
            "{}文件白名单不一致；缺少={} 多出={}".format(
                label,
                sorted(expected_set - actual),
                sorted(actual - expected_set),
            )
        )


def _load_json(layout: Layout, component: str, name: str, label: str) -> Mapping[str, Any]:
    return _strict_json(_read_file(layout, component, name), label)


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise P0DataConflict("{}不是 64 位小写 SHA256".format(field))
    return value


def _count(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise P0DataConflict("{}不是合法计数".format(field))
    return value


def _ratio(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise P0DataConflict("{}不是合法覆盖率".format(field))
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise P0DataConflict("{}超出 0..1".format(field))
    return result


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        raise P0DataConflict("{}必须是带时区时间".format(field))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise P0DataConflict("{}时间非法".format(field)) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond:
        raise P0DataConflict("{}必须带时区且精确到秒".format(field))
    return parsed


def _utc_text(value: Any, field: str) -> str:
    return _parse_time(value, field).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _profile(manifest: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    profile = manifest.get("data_profile")
    if not isinstance(profile, Mapping):
        raise P0DataConflict("{}缺少 data_profile".format(label))
    for field in ("id", "timezone", "window_start", "window_end_exclusive", "snapshot_time"):
        if not isinstance(profile.get(field), str) or not profile.get(field):
            raise P0DataConflict("{} data_profile.{} 非法".format(label, field))
    if profile["timezone"] != "Asia/Shanghai":
        raise P0DataConflict("{}业务时区不一致".format(label))
    if _parse_time(profile["window_start"], label + ".window_start") >= _parse_time(
        profile["window_end_exclusive"], label + ".window_end_exclusive"
    ):
        raise P0DataConflict("{}数据窗口非法".format(label))
    return profile


def _verify_inventory(
    manifest: Mapping[str, Any], component: Component, layout: Layout, label: str
) -> None:
    inventories = manifest.get("files")
    if not isinstance(inventories, Mapping):
        raise P0DataConflict("{}缺少 files inventory".format(label))
    for name, inventory in inventories.items():
        if not isinstance(name, str) or not isinstance(inventory, Mapping):
            raise P0DataConflict("{} files inventory 非法".format(label))
        if inventory.get("name") != name:
            raise P0DataConflict("{} inventory 文件名不一致".format(label))
        if component.checksums.get(name) != inventory.get("sha256"):
            raise P0DataConflict("{} inventory SHA256 不一致：{}".format(label, name))
        stamp = _relative_stamp(layout, component.name, name)
        if inventory.get("size_bytes") != stamp.size:
            raise P0DataConflict("{} inventory size 不一致：{}".format(label, name))


def _d2_fingerprint(manifest: Mapping[str, Any]) -> str:
    """按 D2 runner 的冻结公式复算候选指纹。"""

    source = manifest.get("source")
    if not isinstance(source, Mapping):
        raise P0DataConflict("D2 source 缺失")
    database = source.get("database")
    provenance = source.get("provenance")
    normalizer_hashes = source.get("normalizer_hashes")
    if (
        not isinstance(database, Mapping)
        or not isinstance(provenance, Mapping)
        or not isinstance(normalizer_hashes, Mapping)
        or not normalizer_hashes
    ):
        raise P0DataConflict("D2 指纹来源身份不完整")
    system_identifier = database.get("system_identifier")
    if not isinstance(system_identifier, str) or not system_identifier:
        raise P0DataConflict("D2 system_identifier 非法")
    for name, digest in normalizer_hashes.items():
        if not isinstance(name, str) or not name:
            raise P0DataConflict("D2 normalizer 路径非法")
        _sha(digest, "D2 normalizer_hashes.{}".format(name))
    payload = {
        "schema_version": manifest.get("schema_version"),
        "data_profile": manifest.get("data_profile"),
        "source_release": {
            "release_id": source.get("release_id"),
            "system_identifier": system_identifier,
            "state_sha256": _sha(source.get("state_sha256"), "D2 state SHA256"),
            "manifest_sha256": _sha(
                source.get("manifest_sha256"), "D2 source manifest SHA256"
            ),
            "database_manifest_sha256": _sha(
                source.get("database_manifest_sha256"),
                "D2 database manifest SHA256",
            ),
            "inventory_sha256": _sha(
                source.get("inventory_sha256"), "D2 inventory SHA256"
            ),
        },
        "runner_sha256": _sha(
            provenance.get("probe_sha256"), "D2 runner SHA256"
        ),
        "normalizer_hashes": normalizer_hashes,
        "source_table_counts": manifest.get("source_table_counts"),
        "files": manifest.get("files"),
        "summary": manifest.get("summary"),
        "sample": manifest.get("sample"),
        "classification": manifest.get("classification"),
        "causal_conclusion": manifest.get("causal_conclusion"),
    }
    return _canonical_sha256(payload)


def _load_d2(layout: Layout, component: Component) -> Mapping[str, Any]:
    manifest = _load_json(layout, "d2", "manifest.json", "D2 manifest")
    if (
        manifest.get("schema_version") != "p0_normalization_candidate_v1"
        or manifest.get("candidate_kind") != "readonly_legacy_fact_normalization"
    ):
        raise P0DataConflict("D2 manifest 版本或类型非法")
    fingerprint = _sha(
        manifest.get("candidate_fingerprint_sha256"), "D2 candidate fingerprint"
    )
    if fingerprint != _d2_fingerprint(manifest):
        raise P0DataConflict("D2 candidate fingerprint 不一致")
    if manifest.get("classification") != "observation_only" or manifest.get(
        "causal_conclusion"
    ) is not None:
        raise P0DataConflict("D2 违反 observation_only 边界")
    sample = manifest.get("sample")
    admission = manifest.get("admission")
    if (
        not isinstance(sample, Mapping)
        or sample.get("enabled") is not False
        or sample.get("admissible") is not True
        or not isinstance(admission, Mapping)
        or admission.get("status") != "legacy_candidate_ready"
        or admission.get("eligible_for_release_gate") is not True
        or admission.get("blocking_reasons") != []
    ):
        raise P0DataConflict("D2 不是 full legacy_candidate_ready 候选")
    _profile(manifest, "D2")
    _verify_inventory(manifest, component, layout, "D2")
    inventories = manifest.get("files")
    assert isinstance(inventories, Mapping)
    _require_component_files(
        component,
        set(inventories) | {"manifest.json", "摘要.md"},
        "D2 组件",
    )
    source = manifest.get("source")
    if not isinstance(source, Mapping) or not isinstance(source.get("release_id"), str):
        raise P0DataConflict("D2 source release 缺失")
    provenance = source.get("provenance")
    if not isinstance(provenance, Mapping):
        raise P0DataConflict("D2 provenance 缺失")
    _sha(provenance.get("data_profile_sha256"), "D2 data-profile SHA256")
    return manifest


def _select_d3_names(files: Iterable[str]) -> Tuple[str, str]:
    manifests = sorted(
        name
        for name in files
        if name.endswith("artifact-manifest.json") and ".summary." not in name
    )
    if len(manifests) != 1:
        raise P0DataConflict("D3 必须且只能包含一个 artifact manifest")
    manifest_name = manifests[0]
    summary_name = manifest_name[:-5] + ".summary.zh.json"
    if summary_name not in files:
        raise P0DataConflict("D3 缺少 artifact verification summary")
    return manifest_name, summary_name


def _aligned_expected_slots(
    window_start: datetime, window_end: datetime, interval_seconds: int
) -> Tuple[datetime, ...]:
    """按 Unix epoch 网格复算半开窗口内的期望槽。"""

    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    remainder = int((window_start - epoch).total_seconds()) % interval_seconds
    current = (
        window_start
        if remainder == 0
        else window_start + timedelta(seconds=interval_seconds - remainder)
    )
    slots = []
    while current < window_end:
        slots.append(current)
        current += timedelta(seconds=interval_seconds)
    return tuple(slots)


def _d3_file_identity(
    record: Mapping[str, Any],
    *,
    label: str,
    collectors: set[str],
    expected_by_type: Mapping[str, frozenset[datetime]],
    invalid: bool,
) -> Tuple[str, str, datetime, str, int, str]:
    expected_fields = D3_INVALID_RECORD_FIELDS if invalid else D3_ARTIFACT_RECORD_FIELDS
    if set(record) != expected_fields:
        raise P0DataConflict("{}字段集合非法".format(label))
    collector_id = record.get("collector_id")
    artifact_type = record.get("artifact_type")
    if collector_id not in collectors or artifact_type not in D3_SLOT_SECONDS:
        raise P0DataConflict("{} Collector 或制品类型非法".format(label))
    filename_family = record.get("filename_family")
    if (
        artifact_type == "update"
        and filename_family != "updates"
        or artifact_type == "rib"
        and filename_family not in {"bview", "rib"}
    ):
        raise P0DataConflict("{}制品类型与文件族不一致".format(label))
    relative_path = record.get("relative_path")
    pure = PurePosixPath(relative_path) if isinstance(relative_path, str) else None
    matched = (
        D3_ARTIFACT_FILE_RE.fullmatch(pure.name)
        if pure is not None and pure.name
        else None
    )
    size_bytes = record.get("size_bytes")
    file_sha256 = record.get("file_sha256")
    if (
        pure is None
        or pure.is_absolute()
        or ".." in pure.parts
        or len(pure.parts) < 3
        or pure.parts[0] != collector_id
        or matched is None
        or matched.group("family") != filename_family
        or matched.group("compression") != record.get("compression")
        or isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
        or not isinstance(file_sha256, str)
        or SHA256_RE.fullmatch(file_sha256) is None
    ):
        raise P0DataConflict("{}文件身份或完整性字段非法".format(label))
    event_time = _parse_time(
        record.get("artifact_time_utc"), label + ".artifact_time_utc"
    )
    if event_time.utcoffset() != timedelta(0):
        raise P0DataConflict("{} artifact_time_utc 必须使用 UTC".format(label))
    event_time = event_time.astimezone(UTC)
    try:
        filename_time = datetime.strptime(
            matched.group("date") + matched.group("time"), "%Y%m%d%H%M"
        ).replace(tzinfo=UTC)
    except ValueError as error:
        raise P0DataConflict("{}文件名时间非法".format(label)) from error
    if (
        filename_time != event_time
        or pure.parts[1] != event_time.strftime("%Y.%m")
        or event_time not in expected_by_type[artifact_type]
    ):
        raise P0DataConflict("{}文件名、目录与槽时间不一致或越出固定窗口".format(label))
    if invalid:
        reason = record.get("missing_reason")
        if record.get("value_state") != "parse_failed" or reason not in D3_INVALID_REASONS:
            raise P0DataConflict("{}缺失状态或原因非法".format(label))
        if (
            reason == "empty_file"
            and (size_bytes != 0 or file_sha256 != hashlib.sha256(b"").hexdigest())
            or reason != "empty_file"
            and size_bytes == 0
        ):
            raise P0DataConflict("{}文件身份或完整性字段非法".format(label))
    else:
        expected_artifact_id = "art_v1_" + hashlib.sha256(
            _canonical_json(
                {"schema": "artifact_id_v1", "file_sha256": file_sha256}
            ).encode("utf-8")
        ).hexdigest()[:32]
        if (
            size_bytes <= 0
            or record.get("artifact_id_schema") != "artifact_id_v1"
            or record.get("artifact_id") != expected_artifact_id
        ):
            raise P0DataConflict("{} artifact ID、SHA 或 size 非法".format(label))
    return collector_id, artifact_type, event_time, relative_path, size_bytes, file_sha256


def _d3_missing_slot_states(
    coverage: Mapping[str, Any],
    *,
    collector_id: str,
    artifact_type: str,
    expected_slots: frozenset[datetime],
    expected_missing_count: int,
) -> Tuple[Mapping[datetime, str], list[Mapping[str, Any]]]:
    raw_ranges = coverage.get("missing_ranges")
    if not isinstance(raw_ranges, list):
        raise P0DataConflict("D3 {} missing_ranges 缺失".format(artifact_type.upper()))
    interval_seconds = D3_SLOT_SECONDS[artifact_type]
    states: Dict[datetime, str] = {}
    previous_end: Optional[datetime] = None
    normalized = []
    for index, raw_range in enumerate(raw_ranges):
        label = "D3 missing_ranges[{}:{}:{}]".format(
            collector_id, artifact_type, index
        )
        if not isinstance(raw_range, Mapping) or set(raw_range) != {
            "start_time_utc",
            "end_time_exclusive_utc",
            "slot_count",
            "value_state",
        }:
            raise P0DataConflict("{}字段不符合冻结合同".format(label))
        start = _parse_time(raw_range.get("start_time_utc"), label + ".start")
        end = _parse_time(raw_range.get("end_time_exclusive_utc"), label + ".end")
        if start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0):
            raise P0DataConflict("{}必须使用 UTC".format(label))
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        seconds = int((end - start).total_seconds())
        slot_count = _count(raw_range.get("slot_count"), label + ".slot_count", minimum=1)
        value_state = raw_range.get("value_state")
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        if (
            value_state not in {"source_unavailable", "parse_failed"}
            or seconds <= 0
            or seconds % interval_seconds != 0
            or slot_count != seconds // interval_seconds
            or int((start - epoch).total_seconds()) % interval_seconds != 0
            or previous_end is not None
            and start < previous_end
        ):
            raise P0DataConflict("{}范围、粒度或状态非法".format(label))
        for offset in range(slot_count):
            slot = start + timedelta(seconds=offset * interval_seconds)
            if slot not in expected_slots or slot in states:
                raise P0DataConflict("{}含窗口外或重复槽".format(label))
            states[slot] = value_state
        previous_end = end
        normalized.append(dict(raw_range))
    if len(states) != expected_missing_count:
        raise P0DataConflict(
            "D3 {} missing_ranges 与 missing_slots 不闭合".format(
                artifact_type.upper()
            )
        )
    return states, normalized


def _load_d3(
    layout: Layout, component: Component
) -> Tuple[
    Mapping[str, Any],
    Mapping[str, Any],
    Mapping[str, Any],
    D3SlotClosure,
]:
    manifest_name, summary_name = _select_d3_names(component.checksums)
    _require_component_files(
        component, {manifest_name, summary_name}, "D3 组件"
    )
    manifest = _load_json(layout, "d3", manifest_name, "D3 artifact manifest")
    summary = _load_json(layout, "d3", summary_name, "D3 artifact summary")
    if manifest.get("schema_version") != 1 or manifest.get("manifest_kind") != "mrt_artifact_manifest":
        raise P0DataConflict("D3 artifact manifest 版本或类型非法")
    scan_policy = manifest.get("scan_policy")
    if (
        not isinstance(scan_policy, Mapping)
        or scan_policy.get("invalid_in_window")
        != "full_hash_quarantine_exclude_from_available_slots"
        or scan_policy.get("compression_envelope_validation")
        != "full_stream_to_eof_crc_or_equivalent"
    ):
        raise P0DataConflict("D3 缺少压缩流完整性准入策略")
    payload = dict(manifest)
    fingerprint = payload.pop("manifest_fingerprint_sha256", None)
    _sha(fingerprint, "D3 manifest fingerprint")
    expected = _canonical_sha256(
        {"schema": "mrt_artifact_manifest_fingerprint_v1", "manifest": payload}
    )
    if fingerprint != expected:
        raise P0DataConflict("D3 artifact manifest fingerprint 不一致")
    verification = summary.get("verification")
    summary_manifest = summary.get("manifest")
    if (
        not isinstance(verification, Mapping)
        or verification.get("verified") is not True
        or not isinstance(summary_manifest, Mapping)
        or summary_manifest.get("sha256") != component.checksums[manifest_name]
        or summary_manifest.get("fingerprint_sha256") != fingerprint
    ):
        raise P0DataConflict("D3 artifact verification 未闭合")
    profile = manifest.get("data_profile")
    if not isinstance(profile, Mapping):
        raise P0DataConflict("D3 data_profile 缺失")
    for field in ("id", "timezone", "window_start", "window_end_exclusive"):
        if not isinstance(profile.get(field), str) or not profile.get(field):
            raise P0DataConflict("D3 data_profile.{} 非法".format(field))
    window_start = _parse_time(profile["window_start"], "D3 window_start").astimezone(UTC)
    window_end = _parse_time(
        profile["window_end_exclusive"], "D3 window_end_exclusive"
    ).astimezone(UTC)
    if window_start >= window_end:
        raise P0DataConflict("D3 固定半开窗口非法")
    if (
        profile.get("window_start_utc")
        != window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        or profile.get("window_end_exclusive_utc")
        != window_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    ):
        raise P0DataConflict("D3 data_profile UTC 投影与固定半开窗口不一致")
    expected_by_type = {
        artifact_type: frozenset(
            _aligned_expected_slots(window_start, window_end, interval_seconds)
        )
        for artifact_type, interval_seconds in D3_SLOT_SECONDS.items()
    }
    coverage = manifest.get("coverage")
    if not isinstance(coverage, Mapping):
        raise P0DataConflict("D3 coverage 缺失")
    expected_count = _count(coverage.get("expected_slots"), "D3 expected_slots", minimum=1)
    available_count = _count(coverage.get("available_slots"), "D3 available_slots")
    missing_count = _count(coverage.get("missing_slots"), "D3 missing_slots")
    coverage_ratio = _ratio(coverage.get("coverage_ratio"), "D3 coverage_ratio")
    collectors = manifest.get("collector_allowlist")
    by_collector = coverage.get("by_collector")
    if (
        not isinstance(collectors, list)
        or not collectors
        or any(not isinstance(item, str) or not item for item in collectors)
        or collectors != sorted(set(collectors))
        or not isinstance(by_collector, list)
        or len(by_collector) != len(collectors)
    ):
        raise P0DataConflict("D3 覆盖的 collector 范围非法")
    collector_set = set(collectors)

    artifacts = manifest.get("artifacts")
    invalid_records = manifest.get("invalid_in_window")
    if not isinstance(artifacts, list) or not isinstance(invalid_records, list):
        raise P0DataConflict("D3 artifacts/invalid_in_window 缺失")
    available_coordinates: Dict[Tuple[str, str, datetime], Mapping[str, Any]] = {}
    invalid_coordinates: Dict[Tuple[str, str, datetime], Mapping[str, Any]] = {}
    seen_paths = set()
    seen_artifact_ids = set()
    seen_valid_hashes = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, Mapping):
            raise P0DataConflict("D3 artifacts[{}]不是对象".format(index))
        identity = _d3_file_identity(
            artifact,
            label="D3 artifacts[{}]".format(index),
            collectors=collector_set,
            expected_by_type=expected_by_type,
            invalid=False,
        )
        collector_id, artifact_type, slot, relative_path, _, file_sha256 = identity
        coordinate = (collector_id, artifact_type, slot)
        if (
            coordinate in available_coordinates
            or relative_path in seen_paths
            or artifact["artifact_id"] in seen_artifact_ids
            or file_sha256 in seen_valid_hashes
        ):
            raise P0DataConflict("D3 artifacts 存在重复槽、路径或内容身份")
        available_coordinates[coordinate] = artifact
        seen_paths.add(relative_path)
        seen_artifact_ids.add(artifact["artifact_id"])
        seen_valid_hashes.add(file_sha256)

    invalid_reason_all = {reason: 0 for reason in D3_INVALID_REASONS}
    invalid_reason_update = {reason: 0 for reason in D3_INVALID_REASONS}
    for index, invalid in enumerate(invalid_records):
        if not isinstance(invalid, Mapping):
            raise P0DataConflict("D3 invalid_in_window[{}]不是对象".format(index))
        identity = _d3_file_identity(
            invalid,
            label="D3 invalid_in_window[{}]".format(index),
            collectors=collector_set,
            expected_by_type=expected_by_type,
            invalid=True,
        )
        collector_id, artifact_type, slot, relative_path, _, _ = identity
        coordinate = (collector_id, artifact_type, slot)
        if (
            coordinate in invalid_coordinates
            or coordinate in available_coordinates
            or relative_path in seen_paths
        ):
            raise P0DataConflict(
                "D3 artifacts 与 invalid_in_window 存在重复槽，或槽/路径不互斥"
            )
        invalid_coordinates[coordinate] = invalid
        seen_paths.add(relative_path)
        invalid_reason_all[invalid["missing_reason"]] += 1
        if artifact_type == "update":
            invalid_reason_update[invalid["missing_reason"]] += 1

    update_expected = 0
    update_available = 0
    update_missing_state_counts = {"source_unavailable": 0, "parse_failed": 0}
    seen_collectors = set()
    states_by_collector: Dict[str, Mapping[datetime, str]] = {}
    normalized_missing_ranges = []
    total_available = 0
    total_missing = 0
    total_parse_failed = 0
    for collector_record in by_collector:
        if not isinstance(collector_record, Mapping) or set(collector_record) != {
            "collector_id",
            "by_artifact_type",
        }:
            raise P0DataConflict("D3 collector 覆盖记录非法")
        collector_id = collector_record.get("collector_id")
        if collector_id not in collectors or collector_id in seen_collectors:
            raise P0DataConflict("D3 collector 覆盖记录重复或越界")
        seen_collectors.add(collector_id)
        by_type = collector_record.get("by_artifact_type")
        if not isinstance(by_type, Mapping) or set(by_type) != set(D3_SLOT_SECONDS):
            raise P0DataConflict("D3 collector 必须同时闭合 UPDATE/RIB 覆盖")
        collector_update_states: Dict[datetime, str] = {}
        for artifact_type in sorted(D3_SLOT_SECONDS):
            typed = by_type[artifact_type]
            if not isinstance(typed, Mapping) or set(typed) != {
                "expected_slots",
                "available_slots",
                "missing_slots",
                "coverage_ratio",
                "coverage_status",
                "missing_ranges",
            }:
                raise P0DataConflict("D3 {} 覆盖字段非法".format(artifact_type.upper()))
            expected_slots = expected_by_type[artifact_type]
            collector_expected = _count(
                typed.get("expected_slots"),
                "D3 {} expected_slots".format(artifact_type.upper()),
            )
            if collector_expected != len(expected_slots):
                raise P0DataConflict(
                    "D3 {} expected_slots 未按固定半开窗口和间隔复算".format(
                        artifact_type.upper()
                    )
                )
            collector_available = _count(
                typed.get("available_slots"),
                "D3 {} available_slots".format(artifact_type.upper()),
            )
            collector_missing = _count(
                typed.get("missing_slots"),
                "D3 {} missing_slots".format(artifact_type.upper()),
            )
            collector_ratio = _ratio(
                typed.get("coverage_ratio"),
                "D3 {} coverage_ratio".format(artifact_type.upper()),
            )
            expected_ratio = (
                collector_available / collector_expected
                if collector_expected
                else 1.0
            )
            if (
                collector_available + collector_missing != collector_expected
                or abs(collector_ratio - expected_ratio) > 1e-8
                or typed.get("coverage_status")
                != ("complete" if collector_missing == 0 else "partial")
            ):
                raise P0DataConflict(
                    "D3 {} 覆盖计数不闭合".format(artifact_type.upper())
                )
            missing_states, typed_ranges = _d3_missing_slot_states(
                typed,
                collector_id=collector_id,
                artifact_type=artifact_type,
                expected_slots=expected_slots,
                expected_missing_count=collector_missing,
            )
            available_slots = {
                slot
                for candidate_collector, candidate_type, slot in available_coordinates
                if candidate_collector == collector_id
                and candidate_type == artifact_type
            }
            invalid_slots = {
                slot
                for candidate_collector, candidate_type, slot in invalid_coordinates
                if candidate_collector == collector_id
                and candidate_type == artifact_type
            }
            source_unavailable_slots = {
                slot
                for slot, state in missing_states.items()
                if state == "source_unavailable"
            }
            parse_failed_slots = {
                slot for slot, state in missing_states.items() if state == "parse_failed"
            }
            if (
                len(available_slots) != collector_available
                or invalid_slots != parse_failed_slots
                or available_slots & set(missing_states)
                or available_slots & invalid_slots
                or invalid_slots & source_unavailable_slots
                or available_slots | invalid_slots | source_unavailable_slots
                != set(expected_slots)
            ):
                raise P0DataConflict(
                    "D3 {} artifacts/invalid/missing_ranges 未逐槽互斥穷尽".format(
                        artifact_type.upper()
                    )
                )
            normalized_missing_ranges.extend(
                {
                    "collector_id": collector_id,
                    "artifact_type": artifact_type,
                    **raw_range,
                }
                for raw_range in typed_ranges
            )
            total_available += collector_available
            total_missing += collector_missing
            total_parse_failed += len(parse_failed_slots)
            if artifact_type == "update":
                update_expected += collector_expected
                update_available += collector_available
                update_missing_state_counts["source_unavailable"] += len(
                    source_unavailable_slots
                )
                update_missing_state_counts["parse_failed"] += len(parse_failed_slots)
                collector_update_states.update(
                    {slot: "available" for slot in available_slots}
                )
                collector_update_states.update(missing_states)
        states_by_collector[collector_id] = collector_update_states
    if seen_collectors != set(collectors):
        raise P0DataConflict("D3 覆盖未包含全部 collector")
    aggregate_ranges = coverage.get("missing_ranges")
    if (
        not isinstance(aggregate_ranges, list)
        or sorted(_canonical_json(item) for item in aggregate_ranges)
        != sorted(_canonical_json(item) for item in normalized_missing_ranges)
    ):
        raise P0DataConflict("D3 聚合 missing_ranges 与逐 Collector/类型范围不闭合")

    recomputed_expected = sum(len(slots) for slots in expected_by_type.values()) * len(
        collectors
    )
    expected_missing_state = (
        None
        if total_missing == 0
        else "source_unavailable"
        if total_parse_failed == 0
        else "parse_failed"
        if total_parse_failed == total_missing
        else "mixed"
    )
    if (
        expected_count != recomputed_expected
        or available_count != total_available
        or missing_count != total_missing
        or available_count + missing_count != expected_count
        or abs(coverage_ratio - available_count / expected_count) > 1e-8
        or coverage.get("coverage_status")
        != ("complete" if missing_count == 0 else "partial")
        or coverage.get("missing_value_state") != expected_missing_state
    ):
        raise P0DataConflict("D3 聚合 coverage 未按固定窗口逐槽闭合")

    summary_payload = manifest.get("summary")
    if not isinstance(summary_payload, Mapping):
        raise P0DataConflict("D3 summary 缺失")
    summary_by_type = {
        artifact_type: {
            "artifact_count": sum(
                artifact.get("artifact_type") == artifact_type for artifact in artifacts
            ),
            "size_bytes": sum(
                artifact["size_bytes"]
                for artifact in artifacts
                if artifact.get("artifact_type") == artifact_type
            ),
        }
        for artifact_type in sorted(D3_SLOT_SECONDS)
    }
    summary_by_collector = [
        {
            "collector_id": collector_id,
            "artifact_count": sum(
                artifact.get("collector_id") == collector_id for artifact in artifacts
            ),
            "size_bytes": sum(
                artifact["size_bytes"]
                for artifact in artifacts
                if artifact.get("collector_id") == collector_id
            ),
        }
        for collector_id in collectors
    ]
    expected_invalid_summary = {
        "file_count": len(invalid_records),
        "size_bytes": sum(record["size_bytes"] for record in invalid_records),
        "by_missing_reason": {
            reason: {
                "file_count": invalid_reason_all[reason],
                "size_bytes": sum(
                    record["size_bytes"]
                    for record in invalid_records
                    if record.get("missing_reason") == reason
                ),
            }
            for reason in sorted(D3_INVALID_REASONS)
        },
    }
    if (
        summary_payload.get("artifact_count") != len(artifacts)
        or summary_payload.get("size_bytes")
        != sum(artifact["size_bytes"] for artifact in artifacts)
        or summary_payload.get("by_artifact_type") != summary_by_type
        or summary_payload.get("by_collector") != summary_by_collector
        or summary_payload.get("invalid_in_window") != expected_invalid_summary
    ):
        raise P0DataConflict("D3 summary 与 artifacts/invalid_in_window 不闭合")

    update_missing = update_expected - update_available
    missing_state_counts = update_missing_state_counts
    invalid_reason_counts = invalid_reason_update
    if update_missing == 0:
        aggregate_missing_state = None
    elif missing_state_counts["parse_failed"] == 0:
        aggregate_missing_state = "source_unavailable"
    elif missing_state_counts["source_unavailable"] == 0:
        aggregate_missing_state = "parse_failed"
    else:
        aggregate_missing_state = "mixed"
    raw_coverage = {
        "artifact_type": "update",
        "collector_scope": list(collectors),
        "status": "complete" if update_missing == 0 else "partial",
        "expected_count": update_expected,
        "observed_count": update_available,
        "present_count": update_available + missing_state_counts["parse_failed"],
        "missing_count": update_missing,
        "coverage_ratio": round(update_available / update_expected, 8),
        "presence_ratio": round(
            (update_available + missing_state_counts["parse_failed"])
            / update_expected,
            8,
        ),
        "missing_value_state": aggregate_missing_state,
        "missing_state_counts": dict(missing_state_counts),
        "invalid_reason_counts": dict(invalid_reason_counts),
    }
    slot_closure = D3SlotClosure(
        collectors=tuple(collectors),
        update_expected_slots=tuple(sorted(expected_by_type["update"])),
        states_by_collector=states_by_collector,
    )
    return manifest, summary, raw_coverage, slot_closure


def _iter_gzip_jsonl(
    layout: Layout, component: str, name: str, label: str
) -> Iterator[Mapping[str, Any]]:
    stamp = _relative_stamp(layout, component, name)
    if stamp.size > MAX_GZIP_COMPRESSED_BYTES:
        raise P0DataConflict("{}压缩文件超过上限".format(label))
    total = 0
    path = layout.root / component / name
    try:
        with _open_expected(path, stamp, label) as raw:
            with gzip.GzipFile(fileobj=raw, mode="rb") as compressed:
                for number, line in enumerate(compressed, 1):
                    total += len(line)
                    if total > MAX_GZIP_UNCOMPRESSED_BYTES or len(line) > MAX_JSON_LINE_BYTES:
                        raise P0DataConflict("{}解压内容超过上限".format(label))
                    if line == b"\n" or not line.endswith(b"\n"):
                        raise P0DataConflict("{}第 {} 行不是规范 JSONL".format(label, number))
                    yield _strict_json(line[:-1], "{}第 {} 行".format(label, number))
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as error:
        raise P0DataConflict("{} gzip 解压失败".format(label)) from error


def _same_ratio(actual: float, numerator: int, denominator: int) -> bool:
    expected = numerator / denominator if denominator else 0.0
    return abs(actual - expected) <= 1e-9


def _validate_metric(
    record: Mapping[str, Any],
    *,
    fixed_window_start: datetime,
    fixed_window_end: datetime,
    d3_update_states: Mapping[datetime, str],
    d3_collectors: Sequence[str],
) -> Mapping[str, int]:
    if set(record) != METRIC_KEYS or record.get("schema_version") != "metric-series/v1":
        raise P0DataConflict("MetricSeries 字段或版本非法")
    metric_name = record.get("metric_name")
    definition = METRIC_DEFINITIONS.get(metric_name)
    if definition is None:
        raise P0DataConflict("MetricSeries 指标未进入 P0 冻结清单")
    if (
        record.get("unit") != definition.unit
        or record.get("aggregation") != definition.aggregation
        or record.get("formula") != definition.formula
        or record.get("formula_version") != definition.formula_version
        or record.get("granularity_seconds") != 300
    ):
        raise P0DataConflict("MetricSeries 公式、单位或聚合口径漂移")
    expected_count = _count(record.get("expected_sample_count"), "Metric expected", minimum=1)
    source_count = _count(record.get("source_observed_sample_count"), "Metric source count")
    observed_count = _count(record.get("metric_observed_sample_count"), "Metric observed count")
    active_count = _count(record.get("subject_active_sample_count"), "Metric active count")
    if max(source_count, observed_count, active_count) > expected_count or active_count > source_count:
        raise P0DataConflict("MetricSeries 样本计数超出期望槽")
    window = record.get("window")
    if (
        not isinstance(window, Mapping)
        or window.get("boundary") != "[start,end)"
        or window.get("timezone") != "Asia/Shanghai"
    ):
        raise P0DataConflict("MetricSeries 窗口语义非法")
    start = _parse_time(window.get("start"), "Metric window.start")
    end = _parse_time(window.get("end"), "Metric window.end")
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    if (
        start.utcoffset() != timedelta(0)
        or end.utcoffset() != timedelta(0)
        or start_utc != fixed_window_start
        or end_utc != fixed_window_end
        or int(start_utc.timestamp()) % 300
        or int(end_utc.timestamp()) % 300
        or int((end_utc - start_utc).total_seconds()) != expected_count * 300
    ):
        raise P0DataConflict("MetricSeries 窗口未逐槽覆盖固定半开窗口")
    coverage = record.get("coverage")
    if (
        not isinstance(coverage, Mapping)
        or set(coverage) != METRIC_COVERAGE_FIELDS
        or coverage.get("classification_complete") is not True
    ):
        raise P0DataConflict("MetricSeries 缺口分类不完整")
    points = record.get("points")
    if not isinstance(points, list) or len(points) != expected_count:
        raise P0DataConflict("MetricSeries points 与期望槽不一致")
    point_times = []
    calculated_observed = 0
    calculated_processing = 0
    state_counts: Dict[str, int] = {}
    for index, point in enumerate(points):
        if not isinstance(point, Mapping) or set(point) != METRIC_POINT_FIELDS:
            raise P0DataConflict("MetricSeries point 字段不符合冻结合同")
        observed_at = _parse_time(point.get("time"), "Metric point.time")
        expected_at = start_utc + timedelta(seconds=index * 300)
        if observed_at.utcoffset() != timedelta(0) or observed_at.astimezone(UTC) != expected_at:
            raise P0DataConflict("MetricSeries point 未按固定五分钟窗口唯一穷尽")
        point_times.append(observed_at.astimezone(UTC))
        state = point.get("value_state")
        if isinstance(state, str):
            state_counts[state] = state_counts.get(state, 0) + 1
        value = point.get("value")
        reason = point.get("missing_reason")
        if state == "observed_zero":
            if value != 0 or reason is not None:
                raise P0DataConflict("MetricSeries observed_zero 语义非法")
            calculated_observed += 1
        elif state == "observed_nonzero":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value == 0
                or reason is not None
            ):
                raise P0DataConflict("MetricSeries observed_nonzero 语义非法")
            calculated_observed += 1
        elif state in MISSING_STATES:
            if value is not None or not isinstance(reason, str) or not reason:
                raise P0DataConflict("MetricSeries 缺失值被补零或缺少原因")
            if state != "not_applicable" and reason != state:
                raise P0DataConflict("MetricSeries value_state/missing_reason 不一致")
            if state == "not_applicable" and reason not in {"not_applicable", "denominator_zero"}:
                raise P0DataConflict("MetricSeries not_applicable 原因非法")
            calculated_processing += state == "processing_gap"
        else:
            raise P0DataConflict("MetricSeries value_state 非法")
    calculated_source = (
        expected_count
        - state_counts.get("source_unavailable", 0)
        - state_counts.get("parse_failed", 0)
    )
    if (
        len(set(point_times)) != len(point_times)
        or set(point_times)
        != {
            fixed_window_start + timedelta(seconds=index * 300)
            for index in range(expected_count)
        }
        or calculated_observed != observed_count
        or calculated_source != source_count
        or calculated_processing != coverage.get("processing_gap_sample_count")
        or _count(coverage.get("source_gap_sample_count"), "Metric source gaps")
        != state_counts.get("source_unavailable", 0)
        + state_counts.get("parse_failed", 0)
        or not _same_ratio(
            _ratio(coverage.get("source_coverage_ratio"), "Metric source ratio"),
            calculated_source,
            expected_count,
        )
        or not _same_ratio(
            _ratio(coverage.get("metric_coverage_ratio"), "Metric observed ratio"),
            calculated_observed,
            expected_count,
        )
    ):
        raise P0DataConflict("MetricSeries summary 未由逐点 value_state 闭合")
    activity_density = coverage.get("subject_activity_density")
    if source_count == 0:
        if activity_density is not None or active_count != 0:
            raise P0DataConflict("MetricSeries subject activity summary 不闭合")
    elif (
        not _same_ratio(
            _ratio(activity_density, "Metric subject activity density"),
            active_count,
            source_count,
        )
    ):
        raise P0DataConflict("MetricSeries subject activity summary 不闭合")

    source_refs = record.get("source_refs")
    if not isinstance(source_refs, list) or not source_refs:
        raise P0DataConflict("MetricSeries source_refs 缺失")
    raw_observation_bound = any(
        isinstance(ref, Mapping) and ref.get("source_layer") == "raw_observation"
        for ref in source_refs
    )
    if raw_observation_bound != (metric_name in D3_ALIGNED_METRICS):
        raise P0DataConflict("MetricSeries source_refs 与冻结指标来源不一致")
    if raw_observation_bound:
        collector_scope = record.get("collector_scope")
        collector_ids = (
            collector_scope.get("collector_ids")
            if isinstance(collector_scope, Mapping)
            else None
        )
        if collector_ids != list(d3_collectors):
            raise P0DataConflict("MetricSeries collector_scope 与 D3 不一致")
        for point in points:
            slot = _parse_time(point["time"], "Metric point.time").astimezone(UTC)
            d3_state = d3_update_states.get(slot)
            metric_state = point["value_state"]
            if d3_state in {"source_unavailable", "parse_failed"}:
                if metric_state != d3_state:
                    raise P0DataConflict("MetricSeries 与 D3 UPDATE 缺槽状态逐槽不一致")
            elif d3_state == "available":
                if metric_state in {"source_unavailable", "parse_failed"}:
                    raise P0DataConflict("MetricSeries 把 D3 可用 UPDATE 槽降级为源缺失")
            else:
                raise P0DataConflict("D3 UPDATE 全局槽分类不完整")
    _parse_time(record.get("generated_at"), "Metric generated_at")
    return state_counts


def _metric_fingerprint(manifest: Mapping[str, Any]) -> str:
    files = manifest.get("files")
    expected_files = {
        "metric-series.jsonl.gz",
        "metric-reconciliation-summary.json",
    }
    if not isinstance(files, Mapping) or set(files) != expected_files:
        raise P0DataConflict("Metric manifest 文件 inventory 不完整")
    payload = {
        "schema_version": manifest.get("schema_version"),
        "data_profile": manifest.get("data_profile"),
        "metric_window_utc": manifest.get("metric_window_utc"),
        "generated_at": manifest.get("generated_at"),
        "sources": manifest.get("sources"),
        "files": files,
        "summary": manifest.get("summary"),
        "sample": manifest.get("sample"),
        "classification": manifest.get("classification"),
        "causal_conclusion": manifest.get("causal_conclusion"),
    }
    return _canonical_sha256(payload)


def _validate_zero_difference_map(
    value: Any, metric_names: set[str], label: str
) -> None:
    if not isinstance(value, Mapping) or set(value) != metric_names:
        raise P0DataConflict("{}没有覆盖冻结的十项指标".format(label))
    for metric_name, counts in value.items():
        if not isinstance(counts, Mapping) or any(
            isinstance(count, bool) or not isinstance(count, int) or count != 0
            for count in counts.values()
        ):
            raise P0DataConflict("{}存在差异：{}".format(label, metric_name))


def _validate_metric_reconciliation(
    summary: Mapping[str, Any],
    records: Mapping[str, Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> None:
    payload = dict(summary)
    supplied_fingerprint = payload.pop("summary_fingerprint_sha256", None)
    expected_fingerprint = _canonical_sha256(
        {
            "schema": "metric_reconciliation_summary_fingerprint_v1",
            "summary": payload,
        }
    )
    if supplied_fingerprint != expected_fingerprint:
        raise P0DataConflict("Metric reconciliation fingerprint 不一致")
    metric_names = set(METRIC_DEFINITIONS)
    point_count = sum(len(record["points"]) for record in records.values())
    expected_point_count = sum(
        record["expected_sample_count"] for record in records.values()
    )
    contracts = manifest.get("sources", {}).get("contracts", {})
    schema_sha = contracts.get("contracts/data/metric-series.schema.json")
    if (
        summary.get("schema_version") != "metric_reconciliation_v1"
        or set(records) != metric_names
        or summary.get("series_count") != len(metric_names)
        or summary.get("admitted_metric_count") != len(metric_names)
        or summary.get("formula_contract_coverage_ratio") != 1
        or summary.get("point_count") != point_count
        or expected_point_count != point_count
        or summary.get("strict_schema_status") != "passed"
        or summary.get("schema_invalid_count") != 0
        or summary.get("schema_validated_series_count") != len(metric_names)
        or not isinstance(schema_sha, str)
        or summary.get("schema_sha256") != schema_sha
        or summary.get("source_reconciliation_scope")
        != "independent_readonly_feature_rows_and_sqlite_interval_projection_v1"
        or summary.get("source_reconciliation_expected_metric_count")
        != len(metric_names)
        or summary.get("source_reconciliation_expected_point_count")
        != expected_point_count
        or summary.get("source_reconciliation_actual_point_count") != point_count
        or summary.get("source_reconciliation_invalid_series_count") != 0
        or summary.get("source_reconciliation_difference_count") != 0
        or summary.get("internal_structural_difference_count") != 0
        or summary.get("internal_roundtrip_difference_count") != 0
        or summary.get("reconciliation_difference_count") != 0
        or summary.get("deterministic_summary_match") is not True
        or summary.get("source_reconciliation_difference_count_by_type") != {}
        or summary.get("reconciliation_difference_count_by_type") != {}
        or summary.get("source_reconciliation_failure_samples") != []
        or summary.get("reconciliation_failure_samples") != []
    ):
        raise P0DataConflict("Metric reconciliation 未通过十项独立源对账")
    _validate_zero_difference_map(
        summary.get("source_reconciliation_difference_count_by_metric"),
        metric_names,
        "Metric source reconciliation",
    )
    _validate_zero_difference_map(
        summary.get("reconciliation_difference_count_by_metric"),
        metric_names,
        "Metric reconciliation",
    )
    expected_series_fingerprint = _canonical_sha256(
        {
            "schema": "metric_series_set_fingerprint_v1",
            "series": [dict(records[name]) for name in sorted(records)],
        }
    )
    if summary.get("series_fingerprint_sha256") != expected_series_fingerprint:
        raise P0DataConflict("Metric reconciliation 没有绑定当前 MetricSeries")
    value_state_counts: Dict[str, int] = {}
    missing_reason_counts: Dict[str, int] = {}
    legacy_unknown_by_metric: Dict[str, int] = {}
    for metric_name, record in records.items():
        metric_legacy_unknown = 0
        for point in record["points"]:
            state = point["value_state"]
            value_state_counts[state] = value_state_counts.get(state, 0) + 1
            reason = point["missing_reason"]
            if isinstance(reason, str):
                missing_reason_counts[reason] = missing_reason_counts.get(reason, 0) + 1
            if state == "legacy_unknown":
                metric_legacy_unknown += 1
        if metric_legacy_unknown:
            legacy_unknown_by_metric[metric_name] = metric_legacy_unknown
    if (
        summary.get("value_state_counts") != dict(sorted(value_state_counts.items()))
        or summary.get("missing_reason_counts")
        != dict(sorted(missing_reason_counts.items()))
        or summary.get("legacy_unknown_point_count")
        != value_state_counts.get("legacy_unknown", 0)
        or summary.get("legacy_unknown_point_count_by_metric")
        != dict(sorted(legacy_unknown_by_metric.items()))
        or summary.get("unclassified_gap_count") != 0
        or summary.get("unknown_missing_reason_count") != 0
        or summary.get("confirmed_missing_zero_fill_count") != 0
        or summary.get("outside_window_point_count") != 0
        or summary.get("duplicate_metric_name_count") != 0
    ):
        raise P0DataConflict("Metric reconciliation summary 未由逐点状态闭合")


def _global_d3_update_states(closure: D3SlotClosure) -> Mapping[datetime, str]:
    """将多个 Collector 投影为生成器可消费的单一全局 UPDATE 槽。"""

    result: Dict[datetime, str] = {}
    for slot in closure.update_expected_slots:
        states = []
        for collector_id in closure.collectors:
            collector_states = closure.states_by_collector.get(collector_id)
            state = collector_states.get(slot) if isinstance(collector_states, Mapping) else None
            if state not in {"available", "source_unavailable", "parse_failed"}:
                raise P0DataConflict("D3 UPDATE Collector 逐槽分类不完整")
            states.append(state)
        present_states = [state for state in states if state != "source_unavailable"]
        if len(present_states) > 1:
            raise P0DataConflict("D3 多 Collector UPDATE 槽无法无歧义映射到全局指标")
        result[slot] = present_states[0] if present_states else "source_unavailable"
    return result


def _load_metrics(
    layout: Layout, component: Component, d3_slot_closure: D3SlotClosure
) -> Tuple[Mapping[str, Any], Dict[str, Mapping[str, Any]]]:
    manifest = _load_json(layout, "metric", "manifest.json", "Metric manifest")
    if (
        manifest.get("schema_version") != "p0_metric_candidate_v1"
        or manifest.get("candidate_kind") != "readonly_global_metric_series"
    ):
        raise P0DataConflict("Metric manifest 版本或类型非法")
    profile = _profile(manifest, "Metric")
    fixed_window_start = _parse_time(
        profile["window_start"], "Metric profile.window_start"
    ).astimezone(UTC)
    fixed_window_end = _parse_time(
        profile["window_end_exclusive"], "Metric profile.window_end_exclusive"
    ).astimezone(UTC)
    if (
        int(fixed_window_start.timestamp()) % 300
        or int(fixed_window_end.timestamp()) % 300
        or fixed_window_start >= fixed_window_end
    ):
        raise P0DataConflict("Metric 固定窗口必须对齐五分钟网格")
    expected_metric_slots = tuple(
        fixed_window_start + timedelta(seconds=index * 300)
        for index in range(
            int((fixed_window_end - fixed_window_start).total_seconds()) // 300
        )
    )
    if expected_metric_slots != d3_slot_closure.update_expected_slots:
        raise P0DataConflict("Metric 固定窗口与 D3 UPDATE 期望槽不一致")
    metric_window = manifest.get("metric_window_utc")
    if (
        not isinstance(metric_window, Mapping)
        or _utc_text(metric_window.get("start"), "Metric metric_window_utc.start")
        != fixed_window_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        or _utc_text(
            metric_window.get("end_exclusive"),
            "Metric metric_window_utc.end_exclusive",
        )
        != fixed_window_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    ):
        raise P0DataConflict("Metric manifest 窗口未绑定固定半开窗口")
    d3_update_states = _global_d3_update_states(d3_slot_closure)
    _verify_inventory(manifest, component, layout, "Metric")
    _require_component_files(
        component,
        set(manifest["files"]) | {"manifest.json", "摘要.md"},
        "Metric 组件",
    )
    fingerprint = _sha(
        manifest.get("candidate_fingerprint_sha256"), "Metric candidate fingerprint"
    )
    if fingerprint != _metric_fingerprint(manifest):
        raise P0DataConflict("Metric candidate fingerprint 不一致")
    if manifest.get("classification") != "observation_only" or manifest.get(
        "causal_conclusion"
    ) is not None:
        raise P0DataConflict("Metric 违反 observation_only 边界")
    records: Dict[str, Mapping[str, Any]] = {}
    for ordinal, record in enumerate(
        _iter_gzip_jsonl(layout, "metric", "metric-series.jsonl.gz", "MetricSeries"), 1
    ):
        if ordinal > MAX_METRIC_RECORDS:
            raise P0DataConflict("MetricSeries 记录数超过上限")
        _validate_metric(
            record,
            fixed_window_start=fixed_window_start,
            fixed_window_end=fixed_window_end,
            d3_update_states=d3_update_states,
            d3_collectors=d3_slot_closure.collectors,
        )
        metric_name = record["metric_name"]
        if metric_name in records:
            raise P0DataConflict("MetricSeries metric_name 重复：{}".format(metric_name))
        records[metric_name] = record
    summary = manifest.get("summary")
    if not isinstance(summary, Mapping):
        raise P0DataConflict("Metric summary 缺失")
    generated_names = summary.get("generated_metric_names")
    if (
        not isinstance(generated_names, list)
        or len(generated_names) != len(set(generated_names))
        or set(generated_names) != set(records)
        or summary.get("generated_metric_count") != len(records)
        or summary.get("expected_metric_count") != len(METRIC_DEFINITIONS)
        or summary.get("missing_metric_names") != []
        or set(records) != set(METRIC_DEFINITIONS)
    ):
        raise P0DataConflict("Metric manifest 与 JSONL 指标集合不一致")
    reconciliation = _load_json(
        layout,
        "metric",
        "metric-reconciliation-summary.json",
        "Metric reconciliation",
    )
    _validate_metric_reconciliation(reconciliation, records, manifest)
    return manifest, records


def _d4_fingerprint(manifest: Mapping[str, Any]) -> str:
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping):
        raise P0DataConflict("D4 inputs 缺失")
    d2 = inputs.get("d2")
    d3 = inputs.get("d3_artifacts")
    if not isinstance(d2, Mapping) or not isinstance(d3, Mapping):
        raise P0DataConflict("D4 D2/D3 inputs 缺失")
    payload = {
        "schema_version": manifest.get("schema_version"),
        "candidate_kind": manifest.get("candidate_kind"),
        "data_profile": manifest.get("data_profile"),
        "generated_at": manifest.get("generated_at"),
        "inputs": {
            "d2_manifest_sha256": d2.get("manifest_sha256"),
            "d2_candidate_fingerprint_sha256": d2.get(
                "candidate_fingerprint_sha256"
            ),
            "d3_artifact_manifest_sha256": d3.get("manifest_sha256"),
            "d3_artifact_fingerprint_sha256": d3.get(
                "manifest_fingerprint_sha256"
            ),
        },
        "generator": manifest.get("generator"),
        "selection": manifest.get("selection"),
        "files": manifest.get("files"),
        "registry_entry_count": (manifest.get("registry") or {}).get("entry_count"),
        "classification": manifest.get("classification"),
        "causal_conclusion": manifest.get("causal_conclusion"),
    }
    return _canonical_sha256(payload)


def _evidence_reconciliation_fingerprint(summary: Mapping[str, Any]) -> str:
    payload = dict(summary)
    payload.pop("summary_fingerprint_sha256", None)
    return _canonical_sha256(
        {"schema": "evidence_reconciliation_fingerprint_v1", "summary": payload}
    )


def _load_evidence_reconciliation(
    layout: Layout,
    component: Component,
    manifest: Mapping[str, Any],
) -> Mapping[str, Any]:
    metadata = manifest.get("reconciliation")
    inventories = manifest.get("files")
    if not isinstance(metadata, Mapping) or not isinstance(inventories, Mapping):
        raise P0DataConflict("D4 Evidence 对账元数据缺失")
    name = metadata.get("file")
    if (
        name != "evidence-reconciliation-summary.json"
        or name not in inventories
        or name not in component.checksums
    ):
        raise P0DataConflict("D4 Evidence 对账文件不可解析")
    summary = _load_json(layout, "d4", name, "D4 Evidence reconciliation")
    if set(summary) != EVIDENCE_RECONCILIATION_KEYS:
        raise P0DataConflict("D4 Evidence 对账字段集合非法")
    if (
        summary.get("schema_version") != "evidence_reconciliation_v1"
        or summary.get("scope") != "six_event_contract_investigation_sample"
        or summary.get("sample_only") is not True
        or summary.get("population_coverage_claimed") is not False
        or summary.get("strict_schema_status") != "passed"
        or summary.get("reference_closure_status") != "passed"
        or summary.get("classification") != "observation_only"
        or summary.get("causal_conclusion") is not None
    ):
        raise P0DataConflict("D4 Evidence 对账违反六类样本或观察事实边界")
    fingerprint = _sha(
        summary.get("summary_fingerprint_sha256"),
        "D4 Evidence reconciliation fingerprint",
    )
    if fingerprint != _evidence_reconciliation_fingerprint(summary):
        raise P0DataConflict("D4 Evidence 对账 fingerprint 不一致")
    for field in EVIDENCE_RECONCILIATION_BLOCKING_COUNTS:
        if _count(summary.get(field), "D4 Evidence reconciliation." + field) != 0:
            raise P0DataConflict("D4 Evidence 对账存在阻断计数：{}".format(field))
    _count(
        summary.get("legacy_unknown_value_count"),
        "D4 Evidence reconciliation.legacy_unknown_value_count",
    )
    _count(summary.get("bundle_count"), "D4 Evidence reconciliation.bundle_count", minimum=1)
    _count(
        summary.get("event_type_count"),
        "D4 Evidence reconciliation.event_type_count",
        minimum=1,
    )
    schema_sha = _sha(
        summary.get("schema_sha256"), "D4 Evidence reconciliation schema_sha256"
    )
    generator = manifest.get("generator")
    validation = manifest.get("validation")
    if (
        not isinstance(generator, Mapping)
        or not isinstance(validation, Mapping)
        or generator.get("schema_sha256") != schema_sha
        or validation.get("schema_sha256") != schema_sha
    ):
        raise P0DataConflict("D4 Evidence 对账 Schema 身份不一致")
    for field in (
        "schema_version",
        "scope",
        "sample_only",
        "population_coverage_claimed",
        "summary_fingerprint_sha256",
    ):
        if metadata.get(field) != summary.get(field):
            raise P0DataConflict("D4 Evidence 对账元数据不一致：{}".format(field))
    event_types = summary.get("event_types")
    bundle_ids = summary.get("bundle_ids")
    if (
        not isinstance(event_types, list)
        or not event_types
        or any(not isinstance(item, str) for item in event_types)
        or event_types != sorted(set(event_types))
        or not isinstance(bundle_ids, list)
        or not bundle_ids
        or any(not isinstance(item, str) or not item for item in bundle_ids)
        or bundle_ids != sorted(set(bundle_ids))
    ):
        raise P0DataConflict("D4 Evidence 对账 Bundle 或事件类型集合非法")
    return summary


def _load_evidence(
    layout: Layout, component: Component
) -> Tuple[
    Mapping[str, Any],
    Dict[str, Mapping[str, Any]],
    Dict[str, str],
]:
    manifest = _load_json(layout, "d4", "manifest.json", "D4 manifest")
    if manifest.get("schema_version") != "p0_evidence_candidate_v1":
        raise P0DataConflict("D4 manifest 版本非法")
    _profile(manifest, "D4")
    _verify_inventory(manifest, component, layout, "D4")
    _require_component_files(
        component,
        set(manifest["files"]) | {"manifest.json", "摘要.md"},
        "D4 组件",
    )
    fingerprint = _sha(manifest.get("candidate_fingerprint_sha256"), "D4 fingerprint")
    if fingerprint != _d4_fingerprint(manifest):
        raise P0DataConflict("D4 candidate fingerprint 不一致")
    if manifest.get("classification") != "observation_only" or manifest.get(
        "causal_conclusion"
    ) is not None:
        raise P0DataConflict("D4 违反 observation_only 边界")
    admission = manifest.get("admission")
    if (
        manifest.get("candidate_kind") != "six_event_contract_investigation_sample"
        or not isinstance(admission, Mapping)
        or admission.get("status") != "sample_only_not_full_population"
        or admission.get("represents_full_evidence_population") is not False
        or admission.get("eligible_for_release_gate") is not False
        or admission.get("raw_traceable") is not False
    ):
        raise P0DataConflict("D4 未保持六类样本的非全量准入边界")
    reconciliation = _load_evidence_reconciliation(layout, component, manifest)
    registry_meta = manifest.get("registry")
    if not isinstance(registry_meta, Mapping):
        raise P0DataConflict("D4 registry metadata 缺失")
    registry_name = registry_meta.get("file")
    if not isinstance(registry_name, str) or registry_name not in component.checksums:
        raise P0DataConflict("D4 registry 文件不可解析")
    registry = _load_json(layout, "d4", registry_name, "D4 evidence registry")
    entries = registry.get("entries")
    if (
        registry.get("schema_version") != "p0_evidence_registry_index_v1"
        or not isinstance(entries, Mapping)
        or registry.get("entry_count") != len(entries)
        or registry_meta.get("entry_count") != len(entries)
    ):
        raise P0DataConflict("D4 evidence registry 计数或版本非法")
    inventories = manifest.get("files")
    assert isinstance(inventories, Mapping)
    bundle_names = sorted(
        name
        for name in inventories
        if name.startswith("bundle-") and name.endswith(".json")
    )
    if not bundle_names:
        raise P0DataConflict("D4 没有 Evidence Bundle")
    by_incident: Dict[str, Mapping[str, Any]] = {}
    evidence_files: Dict[str, str] = {}
    reconstructed_entries: Dict[str, Mapping[str, Any]] = {}
    bundle_ids = set()
    bundle_event_types = set()
    for name in bundle_names:
        bundle = _load_json(layout, "d4", name, "Evidence Bundle v2")
        try:
            validate_reference_closure(bundle)
        except (EvidenceBundleError, KeyError, TypeError, AttributeError) as error:
            raise P0DataConflict("Evidence Bundle 结构或引用未闭合") from error
        if (
            bundle.get("bundle_version") != "evidence_bundle_v2"
            or bundle.get("conclusion", {}).get("classification") != "observation_only"
            or bundle.get("conclusion", {}).get("causal_conclusion") is not None
        ):
            raise P0DataConflict("Evidence Bundle 版本或结论边界非法")
        bundle_id = bundle.get("bundle_id")
        incident_id = bundle.get("incident", {}).get("incident_id")
        event_type = bundle.get("incident", {}).get("event_type")
        if not isinstance(bundle_id, str) or bundle_id in bundle_ids:
            raise P0DataConflict("Evidence Bundle ID 重复")
        if not isinstance(incident_id, str) or not INCIDENT_ID_RE.fullmatch(incident_id):
            raise P0DataConflict("Evidence Bundle Incident ID 非法")
        if incident_id in by_incident:
            raise P0DataConflict("Evidence Bundle Incident ID 重复")
        if event_type not in EVENT_TYPES:
            raise P0DataConflict("Evidence Bundle 事件类型非法")
        bundle_ids.add(bundle_id)
        bundle_event_types.add(event_type)
        by_incident[incident_id] = bundle
        evidence_files[incident_id] = name
        bundle_registry = bundle.get("evidence_registry")
        if not isinstance(bundle_registry, list):
            raise P0DataConflict("Evidence Bundle registry 缺失")
        for item in bundle_registry:
            if not isinstance(item, Mapping):
                raise P0DataConflict("Evidence registry item 非对象")
            evidence_id = item.get("evidence_id")
            if not isinstance(evidence_id, str) or evidence_id in reconstructed_entries:
                raise P0DataConflict("Evidence ID 跨 Bundle 重复")
            reconstructed_entries[evidence_id] = {
                "bundle_id": bundle_id,
                "bundle_file": name,
                "registry_item": item,
            }
    if set(entries) != set(reconstructed_entries):
        raise P0DataConflict("D4 evidence registry 与 Bundle 引用不闭合")
    for evidence_id, expected in reconstructed_entries.items():
        if entries[evidence_id] != expected:
            raise P0DataConflict("D4 evidence registry 定位不一致")
    if (
        reconciliation.get("bundle_count") != len(by_incident)
        or reconciliation.get("event_type_count") != len(bundle_event_types)
        or reconciliation.get("bundle_ids") != sorted(bundle_ids)
        or reconciliation.get("event_types") != sorted(bundle_event_types)
        or set(bundle_event_types) != set(EVENT_TYPES)
    ):
        raise P0DataConflict("D4 Evidence 对账与实际六类 Bundle 不一致")
    validation = manifest.get("validation")
    if (
        not isinstance(validation, Mapping)
        or validation.get("strict_schema_status") != "passed"
        or validation.get("bundle_count") != len(by_incident)
        or validation.get("event_type_count") != len(bundle_event_types)
        or validation.get("classification_violation_count") != 0
        or validation.get("causal_conclusion_nonnull_count") != 0
        or validation.get("auto_zero_fill_count") != 0
    ):
        raise P0DataConflict("D4 validation 与实际 Bundle 不一致")
    return manifest, by_incident, evidence_files


def _quality_fingerprint(report: Mapping[str, Any]) -> str:
    payload = dict(report)
    payload.pop("report_fingerprint_sha256", None)
    return _canonical_sha256(
        {"schema": "data_quality_report_fingerprint_v1", "report": payload}
    )


def _load_quality(
    layout: Layout, component: Component
) -> Tuple[Mapping[str, Any], Mapping[str, Any]]:
    _require_component_files(component, QUALITY_REQUIRED_FILES, "Quality 组件")
    report = _load_json(
        layout, "quality", "data-quality-report.json", "P0 data quality report"
    )
    expected_keys = {
        "schema_version",
        "report_id",
        "data_profile",
        "source_release",
        "generator",
        "execution",
        "dimensions",
        "checks",
        "check_summary",
        "gate",
        "generated_at",
        "report_fingerprint_sha256",
    }
    if set(report) != expected_keys or report.get("schema_version") != "data-quality-report/v1":
        raise P0DataConflict("质量报告字段或版本非法")
    fingerprint = _sha(report.get("report_fingerprint_sha256"), "质量报告 fingerprint")
    if fingerprint != _quality_fingerprint(report):
        raise P0DataConflict("质量报告 fingerprint 不一致")
    try:
        validate_report_semantics(report)
    except (QualityGateInputError, KeyError, TypeError, AttributeError) as error:
        raise P0DataConflict("质量报告语义复算失败：{}".format(error)) from error
    execution = report.get("execution")
    if (
        not isinstance(execution, Mapping)
        or execution.get("mode") != "read_only_repeatable_read"
        or execution.get("database_write_operations") != 0
    ):
        raise P0DataConflict("质量报告不是只读执行证据")
    checks = report.get("checks")
    dimensions = report.get("dimensions")
    if not isinstance(checks, list) or not isinstance(dimensions, Mapping) or len(checks) < 10:
        raise P0DataConflict("质量报告检查维度不完整")
    check_ids = []
    for check in checks:
        if not isinstance(check, Mapping) or not isinstance(check.get("check_id"), str):
            raise P0DataConflict("质量报告 check 非法")
        check_ids.append(check["check_id"])
    if len(set(check_ids)) != len(check_ids):
        raise P0DataConflict("质量报告 check_id 重复")
    for name, dimension in dimensions.items():
        if not isinstance(dimension, Mapping) or dimension.get("dimension") != name:
            raise P0DataConflict("质量报告 dimension 非法")
        expected = [check["check_id"] for check in checks if check.get("dimension") == name]
        if dimension.get("check_ids") != expected:
            raise P0DataConflict("质量报告 dimension 引用未闭合")
    summary = report.get("check_summary")
    if not isinstance(summary, Mapping):
        raise P0DataConflict("质量报告 check_summary 缺失")
    calculated = {
        "total_check_count": len(checks),
        "passed_check_count": sum(check.get("status") == "pass" for check in checks),
        "failed_check_count": sum(check.get("status") == "fail" for check in checks),
        "pending_check_count": sum(check.get("status") == "pending" for check in checks),
        "blocking_failed_check_count": sum(
            check.get("status") == "fail" and check.get("severity") == "blocking"
            for check in checks
        ),
        "blocking_pending_check_count": sum(
            check.get("status") == "pending" and check.get("severity") == "blocking"
            for check in checks
        ),
    }
    if summary != calculated:
        raise P0DataConflict("质量报告 check_summary 计数不一致")
    gate = report.get("gate")
    if not isinstance(gate, Mapping):
        raise P0DataConflict("质量报告 gate 缺失")
    known_ids = set(check_ids)
    for field in (
        "blocking_failed_check_ids",
        "blocking_pending_check_ids",
        "warning_check_ids",
    ):
        values = gate.get(field)
        if not isinstance(values, list) or len(values) != len(set(values)) or not set(values) <= known_ids:
            raise P0DataConflict("质量报告 gate 引用未闭合")
    failed_ids = {
        check["check_id"]
        for check in checks
        if check.get("status") == "fail" and check.get("severity") == "blocking"
    }
    pending_ids = {
        check["check_id"]
        for check in checks
        if check.get("status") == "pending" and check.get("severity") == "blocking"
    }
    if set(gate["blocking_failed_check_ids"]) != failed_ids or set(
        gate["blocking_pending_check_ids"]
    ) != pending_ids:
        raise P0DataConflict("质量报告 gate 决定与 checks 不一致")
    if failed_ids:
        expected_status, expected_level = "failed", "not_accepted"
    elif pending_ids:
        expected_status, expected_level = "pending", "not_accepted"
    else:
        expected_status = "passed"
        expected_level = gate.get("admission_level")
        if expected_level not in {"legacy_compatible", "raw_traceable"}:
            raise P0DataConflict("质量报告通过状态缺少合法准入等级")
    if gate.get("status") != expected_status or gate.get("admission_level") != expected_level:
        raise P0DataConflict("质量报告 gate 状态非法")
    profile = report.get("data_profile")
    if not isinstance(profile, Mapping):
        raise P0DataConflict("质量报告 data_profile 缺失")
    _sha(profile.get("profile_sha256"), "质量报告 profile SHA256")
    _parse_time(report.get("generated_at"), "质量报告 generated_at")
    closure = _load_json(layout, "quality", "输入闭包.json", "P0 Quality 输入闭包")
    source_inputs = closure.get("source_inputs")
    programs = closure.get("programs")
    expected_source_keys = {
        "d2_original_manifest_sha256",
        "d2_audited_manifest_sha256",
        *QUALITY_SOURCE_INPUT_FILES,
    }
    if (
        closure.get("schema_version") != "p0_quality_gate_input_closure_v1"
        or closure.get("profile_id") != profile.get("profile_id")
        or closure.get("database_access") != "none"
        or closure.get("database_connection_attempts") != 0
        or closure.get("database_write_operations") != 0
        or closure.get("report_fingerprint_sha256") != fingerprint
        or not isinstance(source_inputs, Mapping)
        or set(source_inputs) != expected_source_keys
        or not isinstance(programs, Mapping)
        or not programs
    ):
        raise P0DataConflict("Quality 输入闭包身份或只读语义非法")
    for field, digest in source_inputs.items():
        _sha(digest, "Quality source_inputs.{}".format(field))
    for name, digest in programs.items():
        if not isinstance(name, str) or not name:
            raise P0DataConflict("Quality programs 路径非法")
        _sha(digest, "Quality programs.{}".format(name))
    expected_archived_inputs = {
        "d2_original_manifest_sha256": component.checksums[
            "d2-original-candidate-manifest.json"
        ],
        "d2_audited_manifest_sha256": component.checksums[
            "d2-candidate-manifest.json"
        ],
        **{
            key: component.checksums[filename]
            for key, filename in QUALITY_SOURCE_INPUT_FILES.items()
        },
    }
    if any(
        source_inputs[key] != digest
        for key, digest in expected_archived_inputs.items()
    ):
        raise P0DataConflict("Quality 输入闭包与归档输入文件 SHA256 不一致")
    return report, closure


def _profile_identity(raw: Mapping[str, Any], label: str) -> Dict[str, str]:
    return {
        "id": str(raw["id"]),
        "timezone": str(raw["timezone"]),
        "window_start_utc": _utc_text(raw["window_start"], label + ".window_start"),
        "window_end_exclusive_utc": _utc_text(
            raw["window_end_exclusive"], label + ".window_end_exclusive"
        ),
        "snapshot_time_utc": _utc_text(raw["snapshot_time"], label + ".snapshot_time"),
    }


def _cross_validate(
    d2: Mapping[str, Any],
    d3: Mapping[str, Any],
    d4: Mapping[str, Any],
    metric: Mapping[str, Any],
    quality: Mapping[str, Any],
    bundles: Mapping[str, Mapping[str, Any]],
    raw_coverage: Mapping[str, Any],
    components: Mapping[str, Component],
    quality_closure: Mapping[str, Any],
) -> None:
    baseline = _profile_identity(_profile(d2, "D2"), "D2")
    for label, manifest in (("D4", d4), ("Metric", metric)):
        if _profile_identity(_profile(manifest, label), label) != baseline:
            raise P0DataConflict("{} 与 D2 data_profile 不一致".format(label))
    d3_profile = d3.get("data_profile")
    if not isinstance(d3_profile, Mapping):
        raise P0DataConflict("D3 data_profile 缺失")
    for field in ("id", "timezone"):
        if d3_profile.get(field) != baseline[field]:
            raise P0DataConflict("D3 与 D2 data_profile 不一致")
    if _utc_text(d3_profile.get("window_start"), "D3 window_start") != baseline[
        "window_start_utc"
    ] or _utc_text(
        d3_profile.get("window_end_exclusive"), "D3 window_end"
    ) != baseline["window_end_exclusive_utc"]:
        raise P0DataConflict("D3 与 D2 数据窗口不一致")
    quality_profile = quality.get("data_profile")
    if (
        not isinstance(quality_profile, Mapping)
        or quality_profile.get("profile_id") != baseline["id"]
    ):
        raise P0DataConflict("质量报告与 D2 data_profile 不一致")
    quality_window = quality_profile.get("window")
    if (
        not isinstance(quality_window, Mapping)
        or _utc_text(quality_window.get("start"), "Quality window.start")
        != baseline["window_start_utc"]
        or _utc_text(quality_window.get("end"), "Quality window.end")
        != baseline["window_end_exclusive_utc"]
        or _utc_text(quality_profile.get("snapshot_time"), "Quality snapshot")
        != baseline["snapshot_time_utc"]
    ):
        raise P0DataConflict("质量报告与 D2 数据窗口不一致")
    profile_sha = d2["source"]["provenance"]["data_profile_sha256"]
    if quality_profile.get("profile_sha256") != profile_sha:
        raise P0DataConflict("质量报告与 D2 data-profile SHA256 不一致")
    metric_profile_sha = metric.get("provenance", {}).get("data_profile_sha256")
    if metric_profile_sha != profile_sha:
        raise P0DataConflict("Metric 与 D2 data-profile SHA256 不一致")
    d2_fingerprint = d2["candidate_fingerprint_sha256"]
    d3_fingerprint = d3["manifest_fingerprint_sha256"]
    d4_inputs = d4.get("inputs", {})
    metric_sources = metric.get("sources", {})
    d3_manifest_name, d3_summary_name = _select_d3_names(
        components["d3"].checksums
    )
    if (
        d4_inputs.get("d2", {}).get("candidate_fingerprint_sha256") != d2_fingerprint
        or d4_inputs.get("d2", {}).get("manifest_sha256")
        != components["d2"].checksums.get("manifest.json")
        or d4_inputs.get("d3_artifacts", {}).get("manifest_fingerprint_sha256")
        != d3_fingerprint
        or d4_inputs.get("d3_artifacts", {}).get("manifest_sha256")
        != components["d3"].checksums.get(d3_manifest_name)
        or d4_inputs.get("d3_artifacts", {}).get("summary_sha256")
        != components["d3"].checksums.get(d3_summary_name)
        or metric_sources.get("d2_normalization", {}).get("fingerprint_sha256")
        != d2_fingerprint
        or metric_sources.get("d2_normalization", {}).get("manifest_sha256")
        != components["d2"].checksums.get("manifest.json")
        or metric_sources.get("d2_normalization", {}).get("checksums_sha256")
        != components["d2"].checksum_file_sha256
        or metric_sources.get("d3_artifacts", {}).get("fingerprint_sha256")
        != d3_fingerprint
        or metric_sources.get("d3_artifacts", {}).get("manifest_sha256")
        != components["d3"].checksums.get(d3_manifest_name)
        or metric_sources.get("d3_artifacts", {}).get("summary_sha256")
        != components["d3"].checksums.get(d3_summary_name)
        or metric_sources.get("d3_artifacts", {}).get("checksums_sha256")
        != components["d3"].checksum_file_sha256
    ):
        raise P0DataConflict("D2/D3 与 D4/Metric 输入身份不一致")
    d4_update_coverage = d4_inputs.get("d3_artifacts", {}).get("update_coverage")
    if (
        not isinstance(d4_update_coverage, Mapping)
        or d4_update_coverage.get("expected_count")
        != raw_coverage.get("expected_count")
        or d4_update_coverage.get("observed_count")
        != raw_coverage.get("observed_count")
    ):
        raise P0DataConflict("D3 与 D4 的 raw UPDATE 覆盖计数不一致")
    metric_summary = metric.get("summary")
    if (
        not isinstance(metric_summary, Mapping)
        or metric_summary.get("feature_source_available_slot_count")
        != raw_coverage.get("observed_count")
        or metric_summary.get("feature_invalid_source_slot_count")
        != raw_coverage.get("missing_state_counts", {}).get("parse_failed")
    ):
        raise P0DataConflict("D3 与 Metric 的原始槽分类不一致")
    source_inputs = quality_closure.get("source_inputs", {})
    quality_component = components["quality"]
    if (
        quality_component.checksums.get("d2-original-candidate-manifest.json")
        != components["d2"].checksums.get("manifest.json")
    ):
        raise P0DataConflict("Quality 归档的 D2 原始 manifest 与当前 D2 组件不一致")
    quality_expected = {
        "d2_original_manifest_sha256": quality_component.checksums.get(
            "d2-original-candidate-manifest.json"
        ),
        "d2_audited_manifest_sha256": quality_component.checksums.get(
            "d2-candidate-manifest.json"
        ),
        "d2": quality_component.checksums.get("d2-candidate-manifest.json"),
        "d2_original": quality_component.checksums.get(
            "d2-original-candidate-manifest.json"
        ),
        "d2_audited": quality_component.checksums.get(
            "d2-candidate-manifest.json"
        ),
        "d3": components["d3"].checksums.get(d3_manifest_name),
        "d3_verification": components["d3"].checksums.get(d3_summary_name),
        "evidence": components["d4"].checksums.get(
            "evidence-reconciliation-summary.json"
        ),
        "metric": components["metric"].checksums.get(
            "metric-reconciliation-summary.json"
        ),
        "profile": profile_sha,
    }
    if any(source_inputs.get(key) != expected for key, expected in quality_expected.items()):
        raise P0DataConflict("Quality 输入闭包没有绑定当前 D2/D3/D4/Metric 组件")
    source_release = quality.get("source_release")
    if (
        not isinstance(source_release, Mapping)
        or source_release.get("data_artifact_sha256")
        != components["d3"].checksums.get(d3_manifest_name)
    ):
        raise P0DataConflict("质量报告 source_release 没有绑定当前 D3 manifest")
    release_id = d2["source"]["release_id"]
    if (
        quality.get("source_release", {}).get("release_id") != release_id
        or metric_sources.get("database", {}).get("release_id") != release_id
    ):
        raise P0DataConflict("D2/Metric/Quality release ID 不一致")
    for bundle in bundles.values():
        snapshot = bundle.get("data_snapshot")
        coverage_summary = bundle.get("coverage_summary", {})
        bundle_raw = coverage_summary.get("raw_source")
        bundle_raw_ratio = (
            _ratio(bundle_raw.get("coverage_ratio"), "Evidence raw coverage ratio")
            if isinstance(bundle_raw, Mapping)
            else None
        )
        if (
            not isinstance(snapshot, Mapping)
            or snapshot.get("profile_id") != baseline["id"]
            or snapshot.get("profile_sha256") != profile_sha
            or snapshot.get("database_release_id") != release_id
            or snapshot.get("raw_source_status")
            != ("complete" if raw_coverage.get("status") == "complete" else "partial")
            or coverage_summary.get("admission_level") != "legacy_compatible"
            or not isinstance(bundle_raw, Mapping)
            or bundle_raw.get("expected_count") != raw_coverage.get("expected_count")
            or bundle_raw.get("observed_count") != raw_coverage.get("observed_count")
            or bundle_raw_ratio is None
            or abs(bundle_raw_ratio - raw_coverage["coverage_ratio"]) > 1e-8
            or bundle_raw.get("status")
            != ("full" if raw_coverage.get("status") == "complete" else "partial")
        ):
            raise P0DataConflict("Evidence Bundle 与候选身份不一致")
    evidence_admission = d4.get("admission")
    gate = quality.get("gate")
    if (
        isinstance(gate, Mapping)
        and gate.get("admission_level") == "raw_traceable"
        and (
            raw_coverage.get("status") != "complete"
            or not isinstance(evidence_admission, Mapping)
            or evidence_admission.get("represents_full_evidence_population") is not True
        )
    ):
        raise P0DataConflict("部分原始覆盖或六类样本不得提升为 raw_traceable")


def _load_repository(layout: Layout) -> ReleaseSnapshot:
    components = {name: _load_component(layout, name) for name in COMPONENT_NAMES}
    d2 = _load_d2(layout, components["d2"])
    d3, _, raw_coverage, d3_slot_closure = _load_d3(
        layout, components["d3"]
    )
    d4, bundles, evidence_files = _load_evidence(layout, components["d4"])
    metric_manifest, metrics = _load_metrics(
        layout, components["metric"], d3_slot_closure
    )
    quality, quality_closure = _load_quality(layout, components["quality"])
    _cross_validate(
        d2,
        d3,
        d4,
        metric_manifest,
        quality,
        bundles,
        raw_coverage,
        components,
        quality_closure,
    )
    repository_fingerprint = _canonical_sha256(
        {
            name: {
                "checksums_sha256": component.checksum_file_sha256,
                "files": dict(sorted(component.checksums.items())),
            }
            for name, component in sorted(components.items())
        }
    )
    return ReleaseSnapshot(
        root=layout.root,
        repository_fingerprint_sha256=repository_fingerprint,
        d2=d2,
        d3=d3,
        d4=d4,
        metric_manifest=metric_manifest,
        quality=quality,
        metrics=metrics,
        evidence_by_incident=bundles,
        evidence_files=evidence_files,
        raw_coverage=raw_coverage,
    )


def _snapshot() -> ReleaseSnapshot:
    global _CACHE
    root = _configured_root()
    with _CACHE_LOCK:
        layout = _scan_layout(root)
        if _CACHE is not None and _CACHE.layout == layout:
            return _CACHE.snapshot
        snapshot = _load_repository(layout)
        final_layout = _scan_layout(root)
        if final_layout != layout:
            raise P0DataConflict("P0 候选仓库在加载期间发生变化")
        _CACHE = CacheEntry(layout=layout, snapshot=snapshot)
        return snapshot


def _metric_admitted(manifest: Mapping[str, Any]) -> bool:
    admission = manifest.get("admission")
    sample = manifest.get("sample")
    return bool(
        isinstance(admission, Mapping)
        and admission.get("status") == "metric_candidate_ready"
        and admission.get("eligible_for_release_gate") is True
        and admission.get("blocking_reasons") == []
        and isinstance(sample, Mapping)
        and sample.get("enabled") is False
        and sample.get("admissible") is True
    )


def _limitation(code: str, severity: str, message: str) -> Dict[str, str]:
    return {"code": code, "severity": severity, "message_zh": message}


def _production_active() -> bool:
    raw = os.environ.get(P0_DATA_PRODUCTION_ACTIVE_ENV, "").strip()
    if raw in ("", "false"):
        return False
    if raw == "true":
        return True
    raise P0DataUnavailable(
        "{} 只能是 true、false 或未配置".format(P0_DATA_PRODUCTION_ACTIVE_ENV)
    )


def _repository_identity() -> Tuple[str, bool]:
    active = _production_active()
    return ("production" if active else "candidate", active)


def _limitations(
    snapshot: ReleaseSnapshot, production_active: bool
) -> list[Dict[str, str]]:
    result = []
    if not production_active:
        result.append(
            _limitation(
                "candidate_not_production_active",
                "info",
                "当前数据只来自显式候选仓库，未执行生产激活。",
            )
        )
    admission = snapshot.d4.get("admission", {})
    if admission.get("represents_full_evidence_population") is not True:
        result.append(
            _limitation(
                "evidence_sample_only",
                "blocking",
                "当前 Evidence 只是调查样本，不代表全量事件证据覆盖。",
            )
        )
    if snapshot.raw_coverage.get("status") != "complete":
        result.append(
            _limitation(
                "partial_raw_coverage",
                "blocking",
                "原始制品没有覆盖完整固定窗口，不能声明 raw_traceable 全量能力。",
            )
        )
    parse_failed = snapshot.raw_coverage.get("missing_state_counts", {}).get(
        "parse_failed", 0
    )
    if isinstance(parse_failed, int) and parse_failed > 0:
        reasons = snapshot.raw_coverage.get("invalid_reason_counts", {})
        result.append(
            _limitation(
                "raw_compression_integrity_failures",
                "blocking",
                "发现 {} 个原始 UPDATE 文件槽不可用（空文件 {}、压缩流 EOF/CRC 失败 {}、magic 错误 {}）；容器完整性通过也不等于全量 MRT 语义解析通过。".format(
                    parse_failed,
                    reasons.get("empty_file", 0),
                    reasons.get("compressed_stream_invalid", 0),
                    reasons.get("compression_magic_mismatch", 0),
                ),
            )
        )
    if not _metric_admitted(snapshot.metric_manifest):
        result.append(
            _limitation(
                "metric_candidate_not_admitted",
                "blocking",
                "Metric manifest 未通过组件准入，指标端点不会发布其中记录。",
            )
        )
    for reason in snapshot.quality.get("gate", {}).get("decision_reasons_zh", []):
        if isinstance(reason, str) and reason:
            result.append(_limitation("quality_gate_reason", "warning", reason))
    return result


def get_p0_status() -> Mapping[str, Any]:
    """返回候选数据身份、覆盖、可用指标和限制。"""

    snapshot = _snapshot()
    d2 = snapshot.d2
    d3 = snapshot.d3
    d4 = snapshot.d4
    metric = snapshot.metric_manifest
    quality = snapshot.quality
    raw_profile = d2["data_profile"]
    admitted = _metric_admitted(metric)
    available_metrics = []
    if admitted:
        for name in sorted(snapshot.metrics):
            record = snapshot.metrics[name]
            available_metrics.append(
                {
                    "metric_name": name,
                    "unit": record["unit"],
                    "aggregation": record["aggregation"],
                    "formula": record["formula"],
                    "formula_version": record["formula_version"],
                    "subject": record["subject"],
                    "window": record["window"],
                    "coverage": record["coverage"],
                }
            )
    gate = quality["gate"]
    evidence_admission = d4.get("admission", {})
    validation = d4.get("validation", {})
    repository_state, production_active = _repository_identity()
    return {
        "schema_version": "p0_data_status_v1",
        "repository_state": repository_state,
        "production_active": production_active,
        "profile": {
            "id": raw_profile["id"],
            "timezone": raw_profile["timezone"],
            "window_start": raw_profile["window_start"],
            "window_end_exclusive": raw_profile["window_end_exclusive"],
            "snapshot_time": raw_profile["snapshot_time"],
            "boundary": "[start,end)",
        },
        "releases": {
            "source_release_id": d2["source"]["release_id"],
            "normalization_candidate_fingerprint_sha256": d2[
                "candidate_fingerprint_sha256"
            ],
            "artifact_manifest_fingerprint_sha256": d3[
                "manifest_fingerprint_sha256"
            ],
            "evidence_candidate_fingerprint_sha256": d4[
                "candidate_fingerprint_sha256"
            ],
            "metric_candidate_fingerprint_sha256": metric[
                "candidate_fingerprint_sha256"
            ],
            "quality_report_id": quality["report_id"],
            "quality_report_fingerprint_sha256": quality[
                "report_fingerprint_sha256"
            ],
            "repository_fingerprint_sha256": snapshot.repository_fingerprint_sha256,
        },
        "quality_decision": {
            "status": gate["status"],
            "admission_level": gate["admission_level"],
            "blocking_failed_check_ids": gate["blocking_failed_check_ids"],
            "blocking_pending_check_ids": gate["blocking_pending_check_ids"],
            "warning_check_ids": gate["warning_check_ids"],
            "decision_reasons_zh": gate["decision_reasons_zh"],
        },
        "available_metrics": available_metrics,
        "evidence_coverage": {
            "candidate_kind": d4.get("candidate_kind"),
            "admission_status": evidence_admission.get("status"),
            "bundle_count": validation.get("bundle_count"),
            "event_type_count": validation.get("event_type_count"),
            "registry_entry_count": d4.get("registry", {}).get("entry_count"),
            "represents_full_evidence_population": evidence_admission.get(
                "represents_full_evidence_population"
            )
            is True,
            "raw_traceable": evidence_admission.get("raw_traceable") is True,
        },
        "raw_coverage": dict(snapshot.raw_coverage),
        "limitations": _limitations(snapshot, production_active),
    }


def get_p0_metric(metric_name: str) -> Mapping[str, Any]:
    """返回一个通过组件准入的 MetricSeries；缺失状态保持 null。"""

    if not isinstance(metric_name, str) or not METRIC_NAME_RE.fullmatch(metric_name):
        raise P0DataBadRequest("metric_name 非法，禁止路径穿越或未冻结名称")
    snapshot = _snapshot()
    if metric_name not in METRIC_DEFINITIONS:
        raise P0DataBadRequest("metric_name 未进入 P0 数据字典")
    if not _metric_admitted(snapshot.metric_manifest):
        raise P0DataNotFound("当前候选没有已准入的指标集合")
    record = snapshot.metrics.get(metric_name)
    if record is None:
        raise P0DataNotFound("合法指标未出现在当前已准入候选中")
    repository_state, production_active = _repository_identity()
    return {
        "schema_version": "p0_metric_response_v1",
        "repository_state": repository_state,
        "production_active": production_active,
        "candidate_fingerprint_sha256": snapshot.metric_manifest[
            "candidate_fingerprint_sha256"
        ],
        "admission_status": snapshot.metric_manifest["admission"]["status"],
        "metric": record,
        "limitations": _limitations(snapshot, production_active),
    }


def get_p0_evidence(incident_id: str) -> Mapping[str, Any]:
    """通过 D4 registry 闭包返回 Evidence Bundle v2。"""

    if not isinstance(incident_id, str) or not INCIDENT_ID_RE.fullmatch(incident_id):
        raise P0DataBadRequest("incident_id 非法，禁止路径穿越或非稳定 ID")
    snapshot = _snapshot()
    bundle = snapshot.evidence_by_incident.get(incident_id)
    if bundle is None:
        admission = snapshot.d4.get("admission", {})
        if admission.get("represents_full_evidence_population") is not True:
            raise P0DataNotFound(
                "当前六类调查样本未收录该 Incident；这不表示全量 Evidence 不存在"
            )
        raise P0DataNotFound("当前全量 Evidence 候选未找到该 Incident")
    admission = snapshot.d4["admission"]
    repository_state, production_active = _repository_identity()
    return {
        "schema_version": "p0_evidence_response_v1",
        "repository_state": repository_state,
        "production_active": production_active,
        "candidate_fingerprint_sha256": snapshot.d4[
            "candidate_fingerprint_sha256"
        ],
        "coverage_scope": (
            "full_population"
            if admission.get("represents_full_evidence_population") is True
            else "sample_only"
        ),
        "represents_full_evidence_population": admission.get(
            "represents_full_evidence_population"
        )
        is True,
        "bundle_file": snapshot.evidence_files[incident_id],
        "bundle": bundle,
        "limitations": _limitations(snapshot, production_active),
    }


def get_p0_quality() -> Mapping[str, Any]:
    """返回通过 SHA 和语义闭包校验的完整 P0 数据质量报告。"""

    snapshot = _snapshot()
    repository_state, production_active = _repository_identity()
    return {
        "schema_version": "p0_quality_response_v1",
        "repository_state": repository_state,
        "production_active": production_active,
        "report": snapshot.quality,
        "limitations": _limitations(snapshot, production_active),
    }


__all__ = (
    "P0DataBadRequest",
    "P0DataConflict",
    "P0DataError",
    "P0DataNotFound",
    "P0DataUnavailable",
    "get_p0_evidence",
    "get_p0_metric",
    "get_p0_quality",
    "get_p0_status",
    "reset_p0_data_cache",
)
