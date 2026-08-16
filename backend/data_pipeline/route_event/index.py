"""P0 RouteEvent 的不可变、可重放磁盘索引核心。

本模块刻意不直接解析 MRT UPDATE/bview。P0 UPDATE 只能由同包中已证明身份、
AFI/SAFI 与 AS_PATH segment 保真的 ``bgpdump`` pilot 或冻结的原生 BGP4MP
研究工厂注入；RIB ordinal/属性保真仍未验收。因此把任意文件路径直接提升为
``raw_traceable`` 始终失败关闭。

调用方只能注入已经由获准解析器展开的 :class:`ParsedMrtRecord` 流。本模块
负责校验 MRT physical frame、生成稳定 ID、规范 RouteEvent、构建不可变
SQLite 候选以及仅表达“时间窗口 + 对象精确相交”的 Incident 关联索引。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sqlite3
import stat
import struct
from typing import Any, BinaryIO, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote
import zlib

from .artifacts import (
    ArtifactIntegrityError,
    SELECTION_FINGERPRINT_SCHEMA,
    artifact_id_v1,
    canonical_json,
    verify_update_pilot_selection,
)


UTC = timezone.utc

ROUTE_EVENT_SCHEMA_VERSION = "route_event_v1"
ROUTE_EVENT_ID_SCHEMA = "route_event_id_v1"
VP_ID_SCHEMA = "vp_id_v1"
IMPORT_RUN_ID_SCHEMA = "import_run_id_v1"
INDEX_SCHEMA_VERSION = "route_event_index_v1"
INDEX_FINGERPRINT_SCHEMA = "route_event_index_fingerprint_v1"
MANIFEST_FINGERPRINT_SCHEMA = "mrt_artifact_manifest_fingerprint_v1"
PARSER_ATTESTATION_FINGERPRINT_SCHEMA = "parser_attestation_fingerprint_v1"
PARSER_STATISTICS_FINGERPRINT_SCHEMA = "parser_statistics_fingerprint_v1"
RAW_AUDIT_MAX_FRAME_BYTES = 64 * 1024 * 1024
_BGPDUMP_PARSER_NAME = "bgpdump"
_BGPDUMP_EXECUTION_POLICY = "verified_open_fd_exec"
_NATIVE_UPDATE_PARSER_NAME = "native_bgp4mp_update"
_NATIVE_UPDATE_EXECUTION_POLICY = "verified_in_process_source"
_NATIVE_UPDATE_COMMAND_TOKEN = "in_process_native_bgp4mp_v1"

ROUTE_EVENT_ID_RE = re.compile(r"^rte_v1_[0-9a-f]{32}$")
INCIDENT_ID_RE = re.compile(r"^inc_v1_[0-9a-f]{24}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMPONENT_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

ALLOWED_ACTIONS = frozenset(("announce", "withdraw", "rib_snapshot"))
ALLOWED_MRT_TYPES_BY_ARTIFACT = {
    "update": frozenset((16, 17)),  # BGP4MP / BGP4MP_ET
    "rib": frozenset((12, 13)),  # TABLE_DUMP / TABLE_DUMP_V2
}
ALLOWED_SEGMENT_TYPES = frozenset(
    ("as_sequence", "as_set", "confederation_sequence", "confederation_set")
)
ALLOWED_QUALITY_FLAGS = frozenset(
    (
        "as_set_present",
        "confederation_segment_present",
        "origin_ambiguous",
        "empty_as_path",
        "parser_warning",
        "record_time_anomaly",
        "raw_reference_unavailable",
        "legacy_vp_identity_unavailable",
        "legacy_observation_only",
    )
)
RAW_TRACEABLE_FORBIDDEN_FLAGS = frozenset(
    (
        "raw_reference_unavailable",
        "legacy_vp_identity_unavailable",
        "legacy_observation_only",
    )
)


class RouteEventIndexError(RuntimeError):
    """RouteEvent 索引无法安全生成或复核。"""


class RouteEventInputError(RouteEventIndexError, ValueError):
    """注入的 manifest、record 或 route element 不符合冻结语义。"""


class MrtParserUnavailableError(RouteEventIndexError):
    """当前仓库没有获准的 RIPE MRT 文件解析器。"""


class RouteEventIndexIntegrityError(RouteEventIndexError):
    """已发布索引的结构或内容 fingerprint 不一致。"""


@dataclass(frozen=True)
class AsPathSegment:
    """保留 segment 类型的 AS_PATH 片段。"""

    segment_type: str
    asns: Tuple[int, ...]


@dataclass(frozen=True)
class ParsedRouteElement:
    """由外部获准解析器注入的单个 route element。

    ``as_path`` 对 announce/RIB 必须是 tuple；空 tuple 表示已观测到空路径。
    withdraw 必须传 ``None``，由索引核心写入 ``not_applicable``。
    """

    event_time_utc: str
    peer_ip: str
    peer_asn: int
    action: str
    prefix: str
    afi_safi: str
    as_path: Optional[Tuple[AsPathSegment, ...]]
    quality_flags: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedMrtRecord:
    """一个完整 MRT physical record 及其确定顺序的 route elements。"""

    record_ordinal: int
    record_offset: int
    raw_record: bytes
    elements: Tuple[ParsedRouteElement, ...]


@dataclass(frozen=True)
class IncidentObject:
    """参与非因果关联的 Incident 对象。"""

    object_type: str
    object_id: str


@dataclass(frozen=True)
class IncidentObservation:
    """Incident 的时间与对象匹配输入，不包含任何因果假设。"""

    incident_id: str
    event_time_utc: str
    affected_objects: Tuple[IncidentObject, ...]
    window_before_seconds: int
    window_after_seconds: int


@dataclass(frozen=True)
class ImportProvenance:
    """冻结的解析/导入血缘；processing_time 必须由运行制品显式提供。"""

    parser_name: str
    parser_version: str
    importer_name: str
    importer_version: str
    processing_time_utc: str
    config: Mapping[str, Any]
    source: str = "ripe_ris"


@dataclass(frozen=True)
class IndexBuildResult:
    """已发布候选索引的位置与可重放摘要。"""

    path: Path
    summary: Mapping[str, Any]


RecordStreamFactory = Callable[[Mapping[str, Any]], Iterable[ParsedMrtRecord]]


def builtin_mrt_parser_capability() -> Dict[str, Any]:
    """返回仓库内置解析边界；不能被解释为运行环境自动探测结果。"""

    return {
        "available": False,
        "capability": "injected_record_stream_only",
        "value_state": "processing_gap",
        "missing_reason": (
            "索引核心没有直接文件解析入口；UPDATE 仅允许显式 attested pilot，"
            "RIB 仍未验收"
        ),
    }


def parse_mrt_artifact(*_args: Any, **_kwargs: Any) -> Iterable[ParsedMrtRecord]:
    """拒绝把文件路径直接提升为 RouteEvent。

    获准 UPDATE 必须经 ``BgpdumpRecordStreamFactory`` 注入；只有 RIB ordinal、
    segment 保真与异常样本门禁全部冻结后，才考虑扩展这一失败关闭入口。
    """

    raise MrtParserUnavailableError(
        "当前仅支持注入已验收 ParsedMrtRecord 流，禁止从 MRT 路径伪造 raw_traceable"
    )


def _stable_id(prefix: str, identity: Mapping[str, Any], length: int = 32) -> str:
    digest = hashlib.sha256(canonical_json(dict(identity)).encode("utf-8")).hexdigest()
    return prefix + digest[:length]


def route_event_id_v1(
    file_sha256: str, record_ordinal: int, element_ordinal: int
) -> str:
    """严格按冻结的不可变文件/record/element 坐标生成 RouteEvent ID。"""

    _require_sha256(file_sha256, "file_sha256")
    _require_nonnegative_int(record_ordinal, "record_ordinal")
    _require_nonnegative_int(element_ordinal, "element_ordinal")
    return _stable_id(
        "rte_v1_",
        {
            "schema": ROUTE_EVENT_ID_SCHEMA,
            "file_sha256": file_sha256,
            "record_ordinal": record_ordinal,
            "element_ordinal": element_ordinal,
        },
    )


def vp_id_v1(collector_id: str, peer_ip: str, peer_asn: int) -> str:
    """由 collector、规范 peer IP 和 peer ASN 生成稳定 VP 身份。"""

    collector = _normalize_collector(collector_id)
    address = _normalize_ip(peer_ip)
    asn = _normalize_asn(peer_asn, "peer_asn")
    return _stable_id(
        "vp_v1_",
        {
            "schema": VP_ID_SCHEMA,
            "collector_id": collector,
            "peer_ip": address,
            "peer_asn": asn,
        },
    )


def import_run_id_v1(
    manifest_fingerprint_sha256: str, provenance: ImportProvenance
) -> Tuple[str, str]:
    """生成导入运行 ID，并返回参与 identity 的 config SHA256。"""

    _require_sha256(manifest_fingerprint_sha256, "manifest_fingerprint_sha256")
    normalized = _normalize_provenance(provenance)
    config_sha256 = hashlib.sha256(
        canonical_json(normalized["config"]).encode("utf-8")
    ).hexdigest()
    run_id = _stable_id(
        "run_v1_",
        {
            "schema": IMPORT_RUN_ID_SCHEMA,
            "manifest_fingerprint_sha256": manifest_fingerprint_sha256,
            "parser_name": normalized["parser_name"],
            "parser_version": normalized["parser_version"],
            "importer_name": normalized["importer_name"],
            "importer_version": normalized["importer_version"],
            "config_sha256": config_sha256,
        },
    )
    return run_id, config_sha256


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise RouteEventInputError(f"{field} 必须是 64 位小写十六进制")
    return value


def _require_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RouteEventInputError(f"{field} 必须是非负整数")
    return value


def _normalize_asn(value: Any, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > 4_294_967_295
    ):
        raise RouteEventInputError(f"{field} 必须是 0 到 2^32-1 的整数 ASN")
    return value


def _normalize_collector(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or not re.fullmatch(r"^[a-z0-9][a-z0-9._-]{0,63}$", value)
    ):
        raise RouteEventInputError("collector_id 非法")
    return value


def _normalize_component(value: Any, field: str) -> str:
    if not isinstance(value, str) or not COMPONENT_RE.fullmatch(value):
        raise RouteEventInputError(f"{field} 不是稳定组件名")
    return value


def _normalize_semver(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SEMVER_RE.fullmatch(value):
        raise RouteEventInputError(f"{field} 必须是 SemVer 2.0.0")
    return value


def _normalize_utc(value: Any, field: str) -> Tuple[str, int]:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RouteEventInputError(f"{field} 必须是以 Z 结尾的 UTC 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise RouteEventInputError(f"{field} 不是有效 UTC 时间") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RouteEventInputError(f"{field} 必须是 UTC 时间")
    parsed = parsed.astimezone(UTC)
    if parsed.microsecond:
        normalized = parsed.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0") + "Z"
    else:
        normalized = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = parsed - epoch
    epoch_microseconds = (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )
    return normalized, epoch_microseconds


def _normalize_ip(value: Any) -> str:
    if not isinstance(value, str):
        raise RouteEventInputError("peer_ip 必须是字符串")
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError as error:
        raise RouteEventInputError("peer_ip 不是有效 IP") from error


def _normalize_prefix(value: Any) -> Tuple[str, str]:
    if not isinstance(value, str):
        raise RouteEventInputError("prefix 必须是 CIDR 字符串")
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as error:
        raise RouteEventInputError("prefix 不是有效 CIDR") from error
    return network.compressed, "ipv4_unicast" if network.version == 4 else "ipv6_unicast"


def _normalize_provenance(provenance: ImportProvenance) -> Dict[str, Any]:
    if not isinstance(provenance, ImportProvenance):
        raise RouteEventInputError("provenance 必须是 ImportProvenance")
    source = _normalize_component(provenance.source, "source")
    processing_time, _ = _normalize_utc(
        provenance.processing_time_utc, "processing_time_utc"
    )
    if not isinstance(provenance.config, Mapping):
        raise RouteEventInputError("provenance.config 必须是 JSON 对象")
    try:
        config = json.loads(canonical_json(dict(provenance.config)))
    except (TypeError, ValueError) as error:
        raise RouteEventInputError("provenance.config 必须是有限 JSON") from error
    return {
        "parser_name": _normalize_component(provenance.parser_name, "parser_name"),
        "parser_version": _normalize_semver(
            provenance.parser_version, "parser_version"
        ),
        "importer_name": _normalize_component(
            provenance.importer_name, "importer_name"
        ),
        "importer_version": _normalize_semver(
            provenance.importer_version, "importer_version"
        ),
        "processing_time_utc": processing_time,
        "config": config,
        "source": source,
    }


def _validate_manifest(
    manifest: Mapping[str, Any], verification: Mapping[str, Any]
) -> Tuple[str, Tuple[Dict[str, Any], ...]]:
    if not isinstance(manifest, Mapping):
        raise RouteEventInputError("manifest 必须是对象")
    payload = dict(manifest)
    fingerprint = payload.pop("manifest_fingerprint_sha256", None)
    _require_sha256(fingerprint, "manifest_fingerprint_sha256")
    expected = hashlib.sha256(
        canonical_json(
            {"schema": MANIFEST_FINGERPRINT_SCHEMA, "manifest": payload}
        ).encode("utf-8")
    ).hexdigest()
    if fingerprint != expected:
        raise RouteEventInputError("manifest fingerprint 校验失败")
    if payload.get("manifest_kind") != "mrt_artifact_manifest":
        raise RouteEventInputError("manifest_kind 不受支持")
    if payload.get("schema_version") != 1:
        raise RouteEventInputError("manifest schema_version 不受支持")
    if not isinstance(verification, Mapping) or verification.get("verified") is not True:
        raise RouteEventInputError("必须提供 verify_artifact_manifest 的成功结果")
    if verification.get("manifest_fingerprint_sha256") != fingerprint:
        raise RouteEventInputError("manifest verification fingerprint 不一致")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise RouteEventInputError("manifest.artifacts 必须是数组")
    if verification.get("artifact_count") != len(artifacts):
        raise RouteEventInputError("manifest verification artifact_count 不一致")

    normalized = []
    seen_ids = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise RouteEventInputError("manifest artifact 必须是对象")
        file_hash = _require_sha256(artifact.get("file_sha256"), "file_sha256")
        artifact_id = artifact.get("artifact_id")
        if artifact_id != artifact_id_v1(file_hash):
            raise RouteEventInputError("artifact_id 与 file_sha256 不一致")
        if artifact_id in seen_ids:
            raise RouteEventInputError("manifest artifact_id 重复")
        seen_ids.add(artifact_id)
        artifact_type = artifact.get("artifact_type")
        if artifact_type not in {"update", "rib"}:
            raise RouteEventInputError("artifact_type 仅允许 update/rib")
        normalized.append(
            {
                "artifact_id": artifact_id,
                "file_sha256": file_hash,
                "collector_id": _normalize_collector(artifact.get("collector_id")),
                "artifact_type": artifact_type,
            }
        )
    normalized.sort(key=lambda row: row["artifact_id"])
    return fingerprint, tuple(normalized)


def _validate_pilot_selection(
    manifest: Mapping[str, Any],
    manifest_verification: Mapping[str, Any],
    selection: Mapping[str, Any],
    selection_verification: Mapping[str, Any],
) -> Tuple[str, Tuple[Dict[str, Any], ...], Dict[str, Any]]:
    try:
        expected_verification = verify_update_pilot_selection(
            manifest, manifest_verification, selection
        )
    except (ArtifactIntegrityError, ValueError) as error:
        raise RouteEventInputError("UPDATE pilot selection 验证失败") from error
    if (
        not isinstance(selection_verification, Mapping)
        or canonical_json(dict(selection_verification))
        != canonical_json(expected_verification)
    ):
        raise RouteEventInputError("selection_verification 与复算结果不一致")
    selected = selection.get("selected_artifacts")
    if not isinstance(selected, list) or not selected:
        raise RouteEventInputError("UPDATE pilot selection 不得为空")
    normalized = []
    for artifact in selected:
        if not isinstance(artifact, Mapping):
            raise RouteEventInputError("selected artifact 必须是对象")
        file_hash = _require_sha256(artifact.get("file_sha256"), "file_sha256")
        artifact_id = artifact.get("artifact_id")
        if artifact_id != artifact_id_v1(file_hash):
            raise RouteEventInputError("selected artifact ID/SHA256 不一致")
        if artifact.get("artifact_type") != "update":
            raise RouteEventInputError("pilot selection 只允许 UPDATE")
        size_bytes = artifact.get("size_bytes")
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes <= 0:
            raise RouteEventInputError("selected artifact size_bytes 非法")
        for field in ("artifact_time_utc", "relative_path", "compression"):
            if not isinstance(artifact.get(field), str) or not artifact.get(field):
                raise RouteEventInputError(f"selected artifact {field} 非法")
        normalized.append(
            {
                "artifact_id": artifact_id,
                "file_sha256": file_hash,
                "collector_id": _normalize_collector(artifact.get("collector_id")),
                "artifact_type": "update",
                "artifact_time_utc": artifact["artifact_time_utc"],
                "relative_path": artifact["relative_path"],
                "compression": artifact["compression"],
                "size_bytes": size_bytes,
            }
        )
    normalized.sort(key=lambda row: row["artifact_id"])
    scope = {
        "scope_mode": "explicit_update_pilot",
        "pilot_only": True,
        "production_complete": False,
        "selection_fingerprint_sha256": selection[
            "selection_fingerprint_sha256"
        ],
        "parent_manifest_fingerprint_sha256": selection[
            "parent_manifest_fingerprint_sha256"
        ],
        "selection_summary": selection["selection_summary"],
        "limits": selection["limits"],
        "data_profile": selection["data_profile"],
        "raw_reference_contract": selection["raw_reference_contract"],
        "coverage_semantics": selection["coverage_semantics"],
        "limitations": selection["limitations"],
    }
    return (
        manifest["manifest_fingerprint_sha256"],
        tuple(normalized),
        scope,
    )


def _parser_attestation_fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(
            {"schema": PARSER_ATTESTATION_FINGERPRINT_SCHEMA, "attestation": payload}
        ).encode("utf-8")
    ).hexdigest()


def _validate_parser_attestation(
    record_stream_factory: RecordStreamFactory,
    provenance: ImportProvenance,
    *,
    required: bool,
    expected_pilot_limits: Optional[Mapping[str, Any]] = None,
    expected_data_profile: Optional[Mapping[str, Any]] = None,
    expected_raw_reference_contract: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    attestation = getattr(record_stream_factory, "parser_attestation", None)
    if attestation is None:
        if required:
            raise RouteEventInputError("UPDATE pilot record factory 缺少 parser attestation")
        return None
    if not isinstance(attestation, Mapping):
        raise RouteEventInputError("parser attestation 必须是对象")
    payload = dict(attestation)
    fingerprint = payload.pop("attestation_fingerprint_sha256", None)
    if not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint):
        raise RouteEventInputError("parser attestation fingerprint 非法")
    if _parser_attestation_fingerprint(payload) != fingerprint:
        raise RouteEventInputError("parser attestation fingerprint 校验失败")
    if payload.get("schema_version") != "parser_attestation_v1":
        raise RouteEventInputError("parser attestation schema 不受支持")
    parser_name = payload.get("parser_name")
    parser_version = payload.get("parser_version")
    _normalize_component(parser_name, "attestation.parser_name")
    _normalize_semver(parser_version, "attestation.parser_version")
    _require_sha256(
        payload.get("parser_binary_sha256"), "attestation.parser_binary_sha256"
    )
    _require_sha256(
        payload.get("adapter_source_sha256"), "attestation.adapter_source_sha256"
    )
    _normalize_component(payload.get("adapter_name"), "attestation.adapter_name")
    _normalize_semver(payload.get("adapter_version"), "attestation.adapter_version")
    configuration_sha256 = _require_sha256(
        payload.get("configuration_sha256"),
        "attestation.configuration_sha256",
    )
    configuration = payload.get("configuration")
    if not isinstance(configuration, Mapping) or hashlib.sha256(
        canonical_json(dict(configuration)).encode("utf-8")
    ).hexdigest() != configuration_sha256:
        raise RouteEventInputError("attestation.configuration 与 SHA256 不一致")
    execution_policy = payload.get("binary_execution_policy")
    if not isinstance(execution_policy, str) or not COMPONENT_RE.fullmatch(
        execution_policy
    ):
        raise RouteEventInputError("attestation.binary_execution_policy 非法")
    security_boundary = payload.get("security_boundary")
    if (
        not isinstance(security_boundary, str)
        or not security_boundary
        or len(security_boundary) > 1024
    ):
        raise RouteEventInputError("attestation.security_boundary 非法")
    if (
        provenance.parser_name != parser_name
        or provenance.parser_version != parser_version
    ):
        raise RouteEventInputError("ImportProvenance 与实际 parser attestation 不一致")
    if expected_pilot_limits is not None:
        if parser_name == _BGPDUMP_PARSER_NAME:
            if execution_policy != _BGPDUMP_EXECUTION_POLICY:
                raise RouteEventInputError(
                    "bgpdump UPDATE pilot parser 必须执行已打开并校验的 binary fd"
                )
        elif parser_name == _NATIVE_UPDATE_PARSER_NAME:
            if execution_policy != _NATIVE_UPDATE_EXECUTION_POLICY:
                raise RouteEventInputError(
                    "native UPDATE pilot parser 必须使用已绑定 source/runtime SHA 的进程内执行"
                )
        else:
            raise RouteEventInputError("UPDATE pilot parser_name 未在冻结 allowlist")
        if canonical_json(payload.get("pilot_limits")) != canonical_json(
            dict(expected_pilot_limits)
        ):
            raise RouteEventInputError(
                "parser attestation pilot_limits 与 selection 不一致"
            )
        common_configuration_valid = (
            configuration.get("binary_execution_policy") == execution_policy
            and canonical_json(configuration.get("pilot_limits"))
            == canonical_json(dict(expected_pilot_limits))
        )
        if parser_name == _BGPDUMP_PARSER_NAME:
            parser_configuration_valid = configuration.get("command_arguments") == [
                "-m",
                "-p",
                "-v",
                "/dev/stdin",
            ]
        else:
            parser_configuration_valid = (
                configuration.get("command_arguments")
                == [_NATIVE_UPDATE_COMMAND_TOKEN]
                and configuration.get("module_source_sha256")
                == payload.get("adapter_source_sha256")
                and configuration.get("python_runtime_sha256")
                == payload.get("parser_binary_sha256")
                and configuration.get("spool_mode") == "not_used_in_process"
                and configuration.get("unknown_attribute_policy") == "fail_closed"
            )
        if not common_configuration_valid or not parser_configuration_valid:
            raise RouteEventInputError(
                "parser attestation configuration 与 pilot 执行合同不一致"
            )
        if (
            not isinstance(expected_data_profile, Mapping)
            or not isinstance(expected_raw_reference_contract, Mapping)
            or configuration.get("window_start_utc")
            != expected_data_profile.get("window_start_utc")
            or configuration.get("window_end_exclusive_utc")
            != expected_data_profile.get("window_end_exclusive_utc")
            or configuration.get("max_frame_bytes")
            != expected_raw_reference_contract.get("max_frame_bytes")
            or configuration.get("max_spool_bytes")
            != expected_pilot_limits.get("max_spool_bytes")
        ):
            raise RouteEventInputError(
                "parser attestation configuration 与 selection 窗口/raw_ref 合同不一致"
            )
    return {**payload, "attestation_fingerprint_sha256": fingerprint}


def _normalize_segments(
    value: Tuple[AsPathSegment, ...],
) -> Tuple[Dict[str, Any], Optional[int], Tuple[str, ...], Optional[str]]:
    if not isinstance(value, tuple):
        raise RouteEventInputError("announce/RIB 的 as_path 必须是 tuple")
    if len(value) > 4096:
        raise RouteEventInputError("AS_PATH segments 超过合同上限 4096")
    normalized_segments = []
    canonical_parts = []
    derived_flags = set()
    for segment in value:
        if not isinstance(segment, AsPathSegment):
            raise RouteEventInputError("as_path 元素必须是 AsPathSegment")
        if segment.segment_type not in ALLOWED_SEGMENT_TYPES:
            raise RouteEventInputError("AS_PATH segment_type 非法")
        if not isinstance(segment.asns, tuple) or not segment.asns:
            raise RouteEventInputError("AS_PATH segment 至少包含一个 ASN")
        if len(segment.asns) > 4096:
            raise RouteEventInputError("AS_PATH segment ASN 超过合同上限 4096")
        asns = tuple(_normalize_asn(asn, "as_path.asn") for asn in segment.asns)
        normalized_segments.append(
            {"segment_type": segment.segment_type, "asns": list(asns)}
        )
        if segment.segment_type == "as_sequence":
            canonical_parts.append(" ".join(str(asn) for asn in asns))
        elif segment.segment_type == "as_set":
            canonical_parts.append("{" + ",".join(str(asn) for asn in asns) + "}")
            derived_flags.add("as_set_present")
        elif segment.segment_type == "confederation_sequence":
            canonical_parts.append("(" + " ".join(str(asn) for asn in asns) + ")")
            derived_flags.add("confederation_segment_present")
        else:
            canonical_parts.append("[" + ",".join(str(asn) for asn in asns) + "]")
            derived_flags.add("confederation_segment_present")

    path = {
        "semantics": "route_observation_path_snapshot",
        "causal_conclusion": None,
        "canonical": " ".join(canonical_parts),
        "segments": normalized_segments,
    }
    if len(path["canonical"]) > 65535:
        raise RouteEventInputError("AS_PATH canonical 超过合同上限 65535")
    if not normalized_segments:
        derived_flags.add("empty_as_path")
        return path, None, tuple(sorted(derived_flags)), "not_observed"

    final = normalized_segments[-1]
    if final["segment_type"] != "as_sequence":
        derived_flags.add("origin_ambiguous")
        return path, None, tuple(sorted(derived_flags)), "not_observed"
    return path, final["asns"][-1], tuple(sorted(derived_flags)), None


def _normalize_element(
    element: ParsedRouteElement,
    *,
    collector_id: str,
    artifact_type: str,
    record_timestamp: int,
) -> Dict[str, Any]:
    if not isinstance(element, ParsedRouteElement):
        raise RouteEventInputError("record element 必须是 ParsedRouteElement")
    event_time, event_epoch_us = _normalize_utc(
        element.event_time_utc, "event_time_utc"
    )
    peer_ip = _normalize_ip(element.peer_ip)
    peer_asn = _normalize_asn(element.peer_asn, "peer_asn")
    if element.action not in ALLOWED_ACTIONS:
        raise RouteEventInputError("action 非法")
    if artifact_type == "update" and element.action == "rib_snapshot":
        raise RouteEventInputError("UPDATE 制品不能生成 rib_snapshot")
    if artifact_type == "rib" and element.action != "rib_snapshot":
        raise RouteEventInputError("RIB 制品只能生成 rib_snapshot")
    if artifact_type == "update" and event_epoch_us // 1_000_000 != record_timestamp:
        raise RouteEventInputError("UPDATE element 时间与 MRT common header 不一致")
    prefix, prefix_afi_safi = _normalize_prefix(element.prefix)
    if element.afi_safi not in {"ipv4_unicast", "ipv6_unicast"}:
        raise RouteEventInputError("afi_safi 必须由获准解析器显式给出且限于单播")
    if element.afi_safi != prefix_afi_safi:
        raise RouteEventInputError("显式 afi_safi 与规范 prefix 地址族不一致")
    afi_safi = element.afi_safi
    if not isinstance(element.quality_flags, tuple):
        raise RouteEventInputError("quality_flags 必须是 tuple")
    quality_flags = set(element.quality_flags)
    if len(quality_flags) != len(element.quality_flags):
        raise RouteEventInputError("quality_flags 不得重复")
    if not quality_flags.issubset(ALLOWED_QUALITY_FLAGS):
        raise RouteEventInputError("quality_flags 含未冻结值")
    if quality_flags & RAW_TRACEABLE_FORBIDDEN_FLAGS:
        raise RouteEventInputError("raw_traceable 不得携带历史不可追溯质量标记")

    missing_reasons: Dict[str, str] = {}
    if element.action == "withdraw":
        if element.as_path is not None:
            raise RouteEventInputError("withdraw 不得携带 AS_PATH")
        as_path = None
        origin_asn = None
        missing_reasons.update(
            {"as_path": "not_applicable", "origin_asn": "not_applicable"}
        )
    else:
        if element.as_path is None:
            raise RouteEventInputError(
                "announce/RIB 缺少结构化 AS_PATH，拒绝提升为 raw_traceable"
            )
        as_path, origin_asn, derived_flags, origin_missing = _normalize_segments(
            element.as_path
        )
        quality_flags.update(derived_flags)
        if origin_missing is not None:
            missing_reasons["origin_asn"] = origin_missing

    return {
        "event_time_utc": event_time,
        "event_epoch_us": event_epoch_us,
        "peer_ip": peer_ip,
        "peer_asn": peer_asn,
        "vp_id": vp_id_v1(collector_id, peer_ip, peer_asn),
        "action": element.action,
        "afi_safi": afi_safi,
        "prefix": prefix,
        "as_path": as_path,
        "origin_asn": origin_asn,
        "quality_flags": sorted(quality_flags),
        "missing_reasons": missing_reasons,
    }


def _normalize_incidents(
    incidents: Sequence[IncidentObservation],
) -> Tuple[Dict[str, Any], ...]:
    if isinstance(incidents, (str, bytes)) or not isinstance(incidents, Sequence):
        raise RouteEventInputError("incidents 必须是 IncidentObservation 序列")
    normalized = []
    seen = set()
    for incident in incidents:
        if not isinstance(incident, IncidentObservation):
            raise RouteEventInputError("incidents 含非 IncidentObservation")
        if not INCIDENT_ID_RE.fullmatch(incident.incident_id):
            raise RouteEventInputError("incident_id 不符合 incident_id_v1")
        if incident.incident_id in seen:
            raise RouteEventInputError("incident_id 重复")
        seen.add(incident.incident_id)
        event_time, event_epoch_us = _normalize_utc(
            incident.event_time_utc, "incident.event_time_utc"
        )
        before = _require_nonnegative_int(
            incident.window_before_seconds, "window_before_seconds"
        )
        after = _require_nonnegative_int(
            incident.window_after_seconds, "window_after_seconds"
        )
        if not isinstance(incident.affected_objects, tuple):
            raise RouteEventInputError("affected_objects 必须是 tuple")
        objects = []
        object_keys = set()
        for item in incident.affected_objects:
            if not isinstance(item, IncidentObject):
                raise RouteEventInputError("affected_objects 含非 IncidentObject")
            if item.object_type == "prefix":
                object_id, _afi = _normalize_prefix(item.object_id)
                association_state = "eligible_exact"
                missing_reason = None
            elif item.object_type == "asn":
                if not isinstance(item.object_id, str) or not re.fullmatch(
                    r"^(0|[1-9][0-9]*)$", item.object_id
                ):
                    raise RouteEventInputError("Incident ASN 必须是不带 AS 的十进制字符串")
                asn = int(item.object_id)
                _normalize_asn(asn, "incident ASN")
                object_id = str(asn)
                association_state = "eligible_exact"
                missing_reason = None
            elif item.object_type == "country":
                if not isinstance(item.object_id, str) or not re.fullmatch(
                    r"^[A-Z]{2}$", item.object_id
                ):
                    raise RouteEventInputError("Incident country 必须是 ISO alpha-2")
                object_id = item.object_id
                association_state = "not_applicable"
                missing_reason = "not_applicable"
            else:
                raise RouteEventInputError("Incident 对象仅允许 prefix/asn/country")
            key = (item.object_type, object_id)
            if key in object_keys:
                continue
            object_keys.add(key)
            objects.append(
                {
                    "object_type": item.object_type,
                    "object_id": object_id,
                    "association_state": association_state,
                    "missing_reason": missing_reason,
                }
            )
        objects.sort(key=lambda row: (row["object_type"], row["object_id"]))
        normalized.append(
            {
                "incident_id": incident.incident_id,
                "event_time_utc": event_time,
                "event_epoch_us": event_epoch_us,
                "window_before_seconds": before,
                "window_after_seconds": after,
                "affected_objects": objects,
            }
        )
    normalized.sort(key=lambda row: row["incident_id"])
    return tuple(normalized)


SCHEMA_SQL = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE artifact (
    artifact_id TEXT PRIMARY KEY,
    file_sha256 TEXT NOT NULL UNIQUE,
    collector_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL CHECK (artifact_type IN ('update', 'rib'))
) WITHOUT ROWID;

CREATE TABLE raw_record (
    artifact_id TEXT NOT NULL REFERENCES artifact(artifact_id),
    record_ordinal INTEGER NOT NULL CHECK (record_ordinal >= 0),
    record_offset INTEGER NOT NULL CHECK (record_offset >= 0),
    record_length INTEGER NOT NULL CHECK (record_length >= 12),
    record_hash TEXT NOT NULL,
    mrt_timestamp INTEGER NOT NULL CHECK (mrt_timestamp >= 0),
    event_epoch_us INTEGER NOT NULL CHECK (event_epoch_us >= 0),
    mrt_type INTEGER NOT NULL CHECK (mrt_type >= 0),
    mrt_subtype INTEGER NOT NULL CHECK (mrt_subtype >= 0),
    element_count INTEGER NOT NULL CHECK (element_count >= 0),
    PRIMARY KEY (artifact_id, record_ordinal),
    UNIQUE (artifact_id, record_offset)
) WITHOUT ROWID;

CREATE TABLE vantage_point (
    vp_id TEXT PRIMARY KEY,
    collector_id TEXT NOT NULL,
    peer_ip TEXT NOT NULL,
    peer_asn INTEGER NOT NULL,
    UNIQUE (collector_id, peer_ip, peer_asn)
) WITHOUT ROWID;

CREATE TABLE as_path (
    path_id TEXT PRIMARY KEY,
    canonical TEXT NOT NULL,
    segments_json TEXT NOT NULL,
    UNIQUE (canonical, segments_json)
) WITHOUT ROWID;

CREATE TABLE route_event (
    route_event_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    record_ordinal INTEGER NOT NULL,
    element_ordinal INTEGER NOT NULL CHECK (element_ordinal >= 0),
    event_time_utc TEXT NOT NULL,
    event_epoch_us INTEGER NOT NULL,
    vp_id TEXT NOT NULL REFERENCES vantage_point(vp_id),
    action TEXT NOT NULL CHECK (action IN ('announce', 'withdraw', 'rib_snapshot')),
    afi_safi TEXT NOT NULL CHECK (afi_safi IN ('ipv4_unicast', 'ipv6_unicast')),
    prefix TEXT NOT NULL,
    path_id TEXT REFERENCES as_path(path_id),
    origin_asn INTEGER,
    quality_flags_json TEXT NOT NULL,
    missing_reasons_json TEXT NOT NULL,
    FOREIGN KEY (artifact_id, record_ordinal)
        REFERENCES raw_record(artifact_id, record_ordinal),
    UNIQUE (artifact_id, record_ordinal, element_ordinal)
) WITHOUT ROWID;

CREATE TABLE incident_observation (
    incident_id TEXT PRIMARY KEY,
    event_time_utc TEXT NOT NULL,
    event_epoch_us INTEGER NOT NULL,
    window_before_seconds INTEGER NOT NULL CHECK (window_before_seconds >= 0),
    window_after_seconds INTEGER NOT NULL CHECK (window_after_seconds >= 0),
    affected_objects_json TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification = 'observation_only'),
    causal_conclusion TEXT CHECK (causal_conclusion IS NULL)
) WITHOUT ROWID;

CREATE TABLE incident_route_event_link (
    incident_id TEXT NOT NULL REFERENCES incident_observation(incident_id),
    route_event_id TEXT NOT NULL REFERENCES route_event(route_event_id),
    object_type TEXT NOT NULL CHECK (object_type IN ('prefix', 'asn')),
    object_id TEXT NOT NULL,
    match_basis TEXT NOT NULL CHECK (
        match_basis IN ('time_window+prefix_exact', 'time_window+origin_asn_exact')
    ),
    event_time_delta_microseconds INTEGER NOT NULL,
    classification TEXT NOT NULL CHECK (classification = 'observation_only'),
    causal_conclusion TEXT CHECK (causal_conclusion IS NULL),
    PRIMARY KEY (incident_id, route_event_id, object_type, object_id)
) WITHOUT ROWID;
"""


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)


def _insert_metadata(connection: sqlite3.Connection, key: str, value: Any) -> None:
    connection.execute(
        "INSERT INTO metadata(key, value_json) VALUES (?, ?)",
        (key, canonical_json(value)),
    )


def _path_id(path: Mapping[str, Any]) -> str:
    return _stable_id(
        "path_v1_", {"schema": "as_path_snapshot_id_v1", "path": dict(path)}
    )


def _insert_artifact_records(
    connection: sqlite3.Connection,
    artifact: Mapping[str, Any],
    records: Iterable[ParsedMrtRecord],
    *,
    remaining_physical_records: Optional[int] = None,
    remaining_route_events: Optional[int] = None,
) -> Tuple[int, int, int]:
    if isinstance(records, (str, bytes, bytearray, os.PathLike)):
        raise RouteEventInputError("record_stream_factory 必须返回 ParsedMrtRecord 流")
    expected_ordinal = 0
    expected_offset = 0
    event_count = 0
    state_event_not_materialized_count = 0
    try:
        iterator = iter(records)
    except TypeError as error:
        raise RouteEventInputError("record stream 不可迭代") from error

    for record in iterator:
        if (
            remaining_physical_records is not None
            and expected_ordinal >= remaining_physical_records
        ):
            raise RouteEventInputError("RouteEvent pilot 超过 physical record 硬上限")
        if not isinstance(record, ParsedMrtRecord):
            raise RouteEventInputError("record stream 含非 ParsedMrtRecord")
        if record.record_ordinal != expected_ordinal:
            raise RouteEventInputError("MRT record_ordinal 必须从 0 连续递增")
        if record.record_offset != expected_offset:
            raise RouteEventInputError("MRT record_offset 必须覆盖连续解压字节流")
        if not isinstance(record.raw_record, bytes) or len(record.raw_record) < 12:
            raise RouteEventInputError("raw_record 必须包含完整 MRT common header+payload")
        mrt_timestamp, mrt_type, mrt_subtype, payload_length = struct.unpack(
            "!IHHI", record.raw_record[:12]
        )
        if payload_length + 12 != len(record.raw_record):
            raise RouteEventInputError("MRT common header length 与 raw_record 不一致")
        if mrt_type not in ALLOWED_MRT_TYPES_BY_ARTIFACT[artifact["artifact_type"]]:
            raise RouteEventInputError("MRT common header type 与制品类型不一致")
        microseconds = 0
        if mrt_type == 17:
            if payload_length < 4:
                raise RouteEventInputError("BGP4MP_ET raw_record 缺少扩展微秒")
            microseconds = struct.unpack("!I", record.raw_record[12:16])[0]
            if microseconds > 999_999:
                raise RouteEventInputError("BGP4MP_ET raw_record 扩展微秒非法")
        if not isinstance(record.elements, tuple):
            raise RouteEventInputError("record.elements 必须是确定顺序 tuple")
        if (
            remaining_route_events is not None
            and event_count + len(record.elements) > remaining_route_events
        ):
            raise RouteEventInputError("RouteEvent pilot 超过 route event 硬上限")
        if mrt_type in {16, 17} and mrt_subtype in {0, 5}:
            if record.elements:
                raise RouteEventInputError("STATE_CHANGE 不得伪造成 RouteEvent")
            state_event_not_materialized_count += 1
        record_hash = hashlib.sha256(record.raw_record).hexdigest()
        connection.execute(
            """
            INSERT INTO raw_record(
                artifact_id, record_ordinal, record_offset, record_length,
                record_hash, mrt_timestamp, event_epoch_us, mrt_type, mrt_subtype,
                element_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact["artifact_id"],
                record.record_ordinal,
                record.record_offset,
                len(record.raw_record),
                record_hash,
                mrt_timestamp,
                mrt_timestamp * 1_000_000 + microseconds,
                mrt_type,
                mrt_subtype,
                len(record.elements),
            ),
        )

        for element_ordinal, element in enumerate(record.elements):
            normalized = _normalize_element(
                element,
                collector_id=artifact["collector_id"],
                artifact_type=artifact["artifact_type"],
                record_timestamp=mrt_timestamp,
            )
            route_event_id = route_event_id_v1(
                artifact["file_sha256"], record.record_ordinal, element_ordinal
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO vantage_point(
                    vp_id, collector_id, peer_ip, peer_asn
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    normalized["vp_id"],
                    artifact["collector_id"],
                    normalized["peer_ip"],
                    normalized["peer_asn"],
                ),
            )
            stored_vp = connection.execute(
                "SELECT collector_id, peer_ip, peer_asn FROM vantage_point WHERE vp_id = ?",
                (normalized["vp_id"],),
            ).fetchone()
            if stored_vp != (
                artifact["collector_id"],
                normalized["peer_ip"],
                normalized["peer_asn"],
            ):
                raise RouteEventInputError("vp_id 内容哈希碰撞或身份不一致")
            path_id = None
            if normalized["as_path"] is not None:
                path_id = _path_id(normalized["as_path"])
                connection.execute(
                    """
                    INSERT OR IGNORE INTO as_path(path_id, canonical, segments_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        path_id,
                        normalized["as_path"]["canonical"],
                        canonical_json(normalized["as_path"]["segments"]),
                    ),
                )
                stored_path = connection.execute(
                    "SELECT canonical, segments_json FROM as_path WHERE path_id = ?",
                    (path_id,),
                ).fetchone()
                expected_path = (
                    normalized["as_path"]["canonical"],
                    canonical_json(normalized["as_path"]["segments"]),
                )
                if stored_path != expected_path:
                    raise RouteEventInputError("path_id 内容哈希碰撞或路径不一致")
            try:
                connection.execute(
                    """
                    INSERT INTO route_event(
                        route_event_id, artifact_id, record_ordinal,
                        element_ordinal, event_time_utc, event_epoch_us, vp_id,
                        action, afi_safi, prefix, path_id, origin_asn,
                        quality_flags_json, missing_reasons_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        route_event_id,
                        artifact["artifact_id"],
                        record.record_ordinal,
                        element_ordinal,
                        normalized["event_time_utc"],
                        normalized["event_epoch_us"],
                        normalized["vp_id"],
                        normalized["action"],
                        normalized["afi_safi"],
                        normalized["prefix"],
                        path_id,
                        normalized["origin_asn"],
                        canonical_json(normalized["quality_flags"]),
                        canonical_json(normalized["missing_reasons"]),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RouteEventInputError(
                    "重复 RouteEvent ID 或原始坐标，拒绝静默去重"
                ) from error
            event_count += 1
        expected_ordinal += 1
        expected_offset += len(record.raw_record)

    if expected_ordinal == 0:
        raise RouteEventInputError("获准 MRT 制品不得产生空 physical record 流")
    return expected_ordinal, event_count, state_event_not_materialized_count


def _insert_incidents_and_links(
    connection: sqlite3.Connection, incidents: Tuple[Dict[str, Any], ...]
) -> Tuple[int, int]:
    unsupported_objects = 0
    link_count = 0
    for incident in incidents:
        connection.execute(
            """
            INSERT INTO incident_observation(
                incident_id, event_time_utc, event_epoch_us,
                window_before_seconds, window_after_seconds,
                affected_objects_json, classification, causal_conclusion
            ) VALUES (?, ?, ?, ?, ?, ?, 'observation_only', NULL)
            """,
            (
                incident["incident_id"],
                incident["event_time_utc"],
                incident["event_epoch_us"],
                incident["window_before_seconds"],
                incident["window_after_seconds"],
                canonical_json(incident["affected_objects"]),
            ),
        )
        lower = incident["event_epoch_us"] - incident["window_before_seconds"] * 1_000_000
        upper = incident["event_epoch_us"] + incident["window_after_seconds"] * 1_000_000
        for affected in incident["affected_objects"]:
            if affected["association_state"] == "not_applicable":
                unsupported_objects += 1
                continue
            if affected["object_type"] == "prefix":
                rows = connection.execute(
                    """
                    SELECT route_event_id, event_epoch_us
                    FROM route_event
                    WHERE prefix = ? AND event_epoch_us BETWEEN ? AND ?
                    ORDER BY event_epoch_us, route_event_id
                    """,
                    (affected["object_id"], lower, upper),
                )
                match_basis = "time_window+prefix_exact"
            else:
                rows = connection.execute(
                    """
                    SELECT route_event_id, event_epoch_us
                    FROM route_event
                    WHERE origin_asn = ? AND event_epoch_us BETWEEN ? AND ?
                    ORDER BY event_epoch_us, route_event_id
                    """,
                    (int(affected["object_id"]), lower, upper),
                )
                match_basis = "time_window+origin_asn_exact"
            for route_event_id, event_epoch_us in rows:
                connection.execute(
                    """
                    INSERT INTO incident_route_event_link(
                        incident_id, route_event_id, object_type, object_id,
                        match_basis, event_time_delta_microseconds,
                        classification, causal_conclusion
                    ) VALUES (?, ?, ?, ?, ?, ?, 'observation_only', NULL)
                    """,
                    (
                        incident["incident_id"],
                        route_event_id,
                        affected["object_type"],
                        affected["object_id"],
                        match_basis,
                        event_epoch_us - incident["event_epoch_us"],
                    ),
                )
                link_count += 1
    return link_count, unsupported_objects


def _count_by(connection: sqlite3.Connection, column: str) -> Dict[str, int]:
    if column not in {"action", "afi_safi"}:
        raise AssertionError("内部聚合列非法")
    return {
        key: count
        for key, count in connection.execute(
            f"SELECT {column}, COUNT(*) FROM route_event GROUP BY {column} ORDER BY {column}"
        )
    }


def _record_hash_chain(
    connection: sqlite3.Connection, artifact_id: str
) -> Tuple[str, int]:
    """由已落索引坐标和 record hash 复算适配器的一次读取链。"""

    digest = hashlib.sha256()
    expected_ordinal = 0
    expected_offset = 0
    for ordinal, offset, length, record_hash in connection.execute(
        """
        SELECT record_ordinal, record_offset, record_length, record_hash
        FROM raw_record
        WHERE artifact_id = ?
        ORDER BY record_ordinal
        """,
        (artifact_id,),
    ):
        if ordinal != expected_ordinal or offset != expected_offset:
            raise RouteEventIndexIntegrityError(
                "raw_record ordinal/offset 不连续，无法复算 record hash chain"
            )
        if not isinstance(record_hash, str) or not SHA256_RE.fullmatch(record_hash):
            raise RouteEventIndexIntegrityError("raw_record record_hash 非法")
        digest.update(struct.pack("!QQQ", ordinal, offset, length))
        digest.update(bytes.fromhex(record_hash))
        expected_ordinal += 1
        expected_offset += length
    if expected_ordinal == 0:
        raise RouteEventIndexIntegrityError("artifact 缺少 raw_record")
    return digest.hexdigest(), expected_ordinal


def _pilot_parser_statistics(
    connection: sqlite3.Connection,
    record_stream_factory: RecordStreamFactory,
    artifacts: Tuple[Dict[str, Any], ...],
    parser_attestation: Mapping[str, Any],
) -> Tuple[Dict[str, Any], str]:
    """复核并冻结每个 UPDATE 流的事实统计，绑定原始 hash chain。"""

    statistics = getattr(record_stream_factory, "statistics_by_artifact", None)
    if not isinstance(statistics, Mapping):
        raise RouteEventInputError("UPDATE pilot factory 缺少 parser statistics")
    expected_ids = {artifact["artifact_id"] for artifact in artifacts}
    if set(statistics) != expected_ids:
        raise RouteEventInputError("parser statistics artifact 集合与 selection 不一致")
    normalized: Dict[str, Any] = {}
    for artifact in artifacts:
        artifact_id = artifact["artifact_id"]
        row = statistics.get(artifact_id)
        if not isinstance(row, Mapping):
            raise RouteEventInputError("parser statistics artifact 记录非法")
        record_hash_chain, physical_count = _record_hash_chain(
            connection, artifact_id
        )
        route_record_count, route_element_count, state_count = connection.execute(
            """
            SELECT
                SUM(CASE WHEN element_count > 0 THEN 1 ELSE 0 END),
                SUM(element_count),
                SUM(CASE WHEN mrt_type IN (16,17) AND mrt_subtype IN (0,5)
                         THEN 1 ELSE 0 END)
            FROM raw_record
            WHERE artifact_id = ?
            """,
            (artifact_id,),
        ).fetchone()
        action_counts = dict(
            connection.execute(
                """
                SELECT action, COUNT(*)
                FROM route_event
                WHERE artifact_id = ?
                GROUP BY action
                """,
                (artifact_id,),
            )
        )
        required_equalities = {
            "artifact_id": artifact_id,
            "status": "complete",
            "physical_record_count": physical_count,
            "route_record_count": route_record_count or 0,
            "state_change_record_count": state_count or 0,
            "keepalive_record_count": physical_count
            - (route_record_count or 0)
            - (state_count or 0),
            "route_element_count": route_element_count or 0,
            "announce_count": action_counts.get("announce", 0),
            "withdraw_count": action_counts.get("withdraw", 0),
            "record_hash_chain_sha256": record_hash_chain,
            "compressed_file_sha256": artifact["file_sha256"],
            "compressed_size_bytes": artifact["size_bytes"],
            "compressed_read_passes": 1,
            "parser_version": parser_attestation["parser_version"],
            "parser_binary_sha256": parser_attestation[
                "parser_binary_sha256"
            ],
        }
        for field, expected in required_equalities.items():
            if row.get(field) != expected:
                raise RouteEventInputError(
                    f"parser statistics.{artifact_id}.{field} 与索引事实不一致"
                )
        transitions = row.get("state_change_transitions")
        transition_total = 0
        transition_keys = set()
        transitions_valid = isinstance(transitions, list)
        if transitions_valid:
            for transition in transitions:
                if not isinstance(transition, Mapping):
                    transitions_valid = False
                    break
                old_state = transition.get("old_state")
                new_state = transition.get("new_state")
                count = transition.get("count")
                key = (old_state, new_state)
                if (
                    any(
                        isinstance(value, bool)
                        or not isinstance(value, int)
                        or value < 0
                        or value > 65_535
                        for value in (old_state, new_state)
                    )
                    or isinstance(count, bool)
                    or not isinstance(count, int)
                    or count <= 0
                    or key in transition_keys
                ):
                    transitions_valid = False
                    break
                transition_keys.add(key)
                transition_total += count
        if not transitions_valid or transition_total != (state_count or 0):
            raise RouteEventInputError("parser statistics STATE transition 计数不一致")
        try:
            normalized[artifact_id] = json.loads(canonical_json(dict(row)))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise RouteEventInputError("parser statistics 不是规范 JSON") from error
    fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": PARSER_STATISTICS_FINGERPRINT_SCHEMA,
                "statistics_by_artifact": normalized,
            }
        ).encode("utf-8")
    ).hexdigest()
    return normalized, fingerprint


class _IndependentHashingReader:
    """供 post-build raw-ref 审计使用的独立压缩字节哈希读取器。"""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._digest = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        block = self._stream.read(size)
        if block:
            self._digest.update(block)
            self.bytes_read += len(block)
        return block

    def tell(self) -> int:
        return self.bytes_read

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def _read_exact_raw(
    stream: BinaryIO, length: int, *, allow_clean_eof: bool
) -> Optional[bytes]:
    payload = bytearray()
    while len(payload) < length:
        block = stream.read(length - len(payload))
        if not block:
            if not payload and allow_clean_eof:
                return None
            raise RouteEventIndexIntegrityError(
                "post-build MRT 解压流截断"
            )
        payload.extend(block)
    return bytes(payload)


def _open_relative_regular_file(root: Path, relative_value: Any) -> int:
    """用 openat+O_NOFOLLOW 固定 raw-root 下每一层路径。"""

    if not isinstance(relative_value, str) or any(
        marker in relative_value for marker in ("\x00", "\n", "\r", "\\")
    ):
        raise RouteEventIndexIntegrityError("selected artifact relative_path 非法")
    relative = PurePosixPath(relative_value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RouteEventIndexIntegrityError("selected artifact relative_path 越界")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, directory_flags | nofollow)
        descriptors.append(current)
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise RouteEventIndexIntegrityError("raw_root 必须是普通目录")
        for part in relative.parts[:-1]:
            current = os.open(
                part,
                directory_flags | nofollow,
                dir_fd=current,
            )
            descriptors.append(current)
            if not stat.S_ISDIR(os.fstat(current).st_mode):
                raise RouteEventIndexIntegrityError("raw artifact 中间路径不是目录")
        descriptor = os.open(
            relative.parts[-1], os.O_RDONLY | nofollow, dir_fd=current
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise RouteEventIndexIntegrityError("raw artifact 不是普通文件")
        return descriptor
    except OSError as error:
        raise RouteEventIndexIntegrityError(
            "无法安全打开 post-build raw artifact"
        ) from error
    finally:
        for descriptor_to_close in reversed(descriptors):
            os.close(descriptor_to_close)


def _post_build_raw_reference_audit(
    connection: sqlite3.Connection,
    raw_root: os.PathLike[str] | str,
    artifact_selection: Mapping[str, Any],
    build_scope: Mapping[str, Any],
) -> Dict[str, Any]:
    """独立第二遍读取 selected gzip UPDATE，并逐 frame 复核 SQLite raw_ref。"""

    root = Path(raw_root)
    try:
        root_metadata = root.lstat()
    except OSError as error:
        raise RouteEventIndexIntegrityError("post-build raw_root 不可读") from error
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(
        root_metadata.st_mode
    ):
        raise RouteEventIndexIntegrityError("post-build raw_root 必须是非链接目录")
    if not isinstance(artifact_selection, Mapping):
        raise RouteEventIndexIntegrityError("post-build selection 必须是对象")
    selection_payload = dict(artifact_selection)
    selection_fingerprint = selection_payload.pop(
        "selection_fingerprint_sha256", None
    )
    try:
        recalculated_selection_fingerprint = hashlib.sha256(
            canonical_json(
                {
                    "schema": SELECTION_FINGERPRINT_SCHEMA,
                    "selection": selection_payload,
                }
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError) as error:
        raise RouteEventIndexIntegrityError(
            "post-build selection 不是规范 JSON"
        ) from error
    if (
        selection_fingerprint != recalculated_selection_fingerprint
        or selection_fingerprint != build_scope.get("selection_fingerprint_sha256")
        or canonical_json(artifact_selection.get("raw_reference_contract"))
        != canonical_json(build_scope.get("raw_reference_contract"))
    ):
        raise RouteEventIndexIntegrityError(
            "post-build selection 与 index build_scope 不一致"
        )
    contract = artifact_selection.get("raw_reference_contract")
    expected_contract = {
        "record_ordinal_basis": (
            "zero_based_physical_mrt_record_in_decompressed_stream"
        ),
        "record_offset_basis": "decompressed_mrt_stream",
        "record_length_basis": "complete_mrt_common_header_plus_payload",
        "record_hash_algorithm": "sha256_complete_mrt_record_bytes",
        "compressed_file_identity_algorithm": "sha256_compressed_file_bytes",
        "post_build_verification": "independent_second_read_required",
        "max_frame_bytes": RAW_AUDIT_MAX_FRAME_BYTES,
    }
    if canonical_json(contract) != canonical_json(expected_contract):
        raise RouteEventIndexIntegrityError("raw_reference_contract 不受支持")
    artifacts = artifact_selection.get("selected_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RouteEventIndexIntegrityError("post-build selection artifacts 非法")
    index_artifacts = {
        row[0]: (row[1], row[2], row[3])
        for row in connection.execute(
            """
            SELECT artifact_id, file_sha256, collector_id, artifact_type
            FROM artifact
            ORDER BY artifact_id
            """
        )
    }
    selected_ids = {
        row.get("artifact_id") for row in artifacts if isinstance(row, Mapping)
    }
    if len(selected_ids) != len(artifacts) or selected_ids != set(index_artifacts):
        raise RouteEventIndexIntegrityError(
            "post-build selection artifact 集合与 index 不一致"
        )

    total_checked = 0
    total_compressed_bytes = 0
    by_artifact: Dict[str, Any] = {}
    for artifact in sorted(artifacts, key=lambda row: row["artifact_id"]):
        if not isinstance(artifact, Mapping):
            raise RouteEventIndexIntegrityError("post-build artifact 非对象")
        artifact_id = artifact["artifact_id"]
        file_sha256 = artifact.get("file_sha256")
        size_bytes = artifact.get("size_bytes")
        if (
            index_artifacts[artifact_id]
            != (file_sha256, artifact.get("collector_id"), "update")
            or not isinstance(file_sha256, str)
            or not SHA256_RE.fullmatch(file_sha256)
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes <= 0
        ):
            raise RouteEventIndexIntegrityError(
                "post-build artifact identity 与 index 不一致"
            )
        relative = PurePosixPath(artifact.get("relative_path", ""))
        if not relative.parts or relative.parts[0] != artifact.get("collector_id"):
            raise RouteEventIndexIntegrityError(
                "post-build artifact 路径与 collector 不一致"
            )
        descriptor = _open_relative_regular_file(
            root, artifact.get("relative_path")
        )
        try:
            before = os.fstat(descriptor)
            if before.st_size != size_bytes:
                raise RouteEventIndexIntegrityError(
                    "post-build compressed artifact size 与 selection 不一致"
                )
            with os.fdopen(descriptor, "rb", buffering=0) as compressed:
                descriptor = -1
                hashing = _IndependentHashingReader(compressed)
                indexed_rows = iter(
                    connection.execute(
                        """
                        SELECT record_ordinal, record_offset, record_length,
                               record_hash, mrt_timestamp, event_epoch_us,
                               mrt_type, mrt_subtype
                        FROM raw_record
                        WHERE artifact_id = ?
                        ORDER BY record_ordinal
                        """,
                        (artifact_id,),
                    )
                )
                ordinal = 0
                offset = 0
                try:
                    with gzip.GzipFile(fileobj=hashing, mode="rb") as decoded:
                        while True:
                            header = _read_exact_raw(
                                decoded, 12, allow_clean_eof=True
                            )
                            if header is None:
                                break
                            mrt_timestamp, mrt_type, mrt_subtype, payload_length = (
                                struct.unpack("!IHHI", header)
                            )
                            length = 12 + payload_length
                            if length > RAW_AUDIT_MAX_FRAME_BYTES:
                                raise RouteEventIndexIntegrityError(
                                    "post-build MRT frame 超过审计硬上限"
                                )
                            payload = _read_exact_raw(
                                decoded,
                                payload_length,
                                allow_clean_eof=False,
                            )
                            assert payload is not None
                            raw_record = header + payload
                            microseconds = 0
                            if mrt_type == 17:
                                if payload_length < 4:
                                    raise RouteEventIndexIntegrityError(
                                        "post-build BGP4MP_ET 缺少扩展微秒"
                                    )
                                microseconds = struct.unpack("!I", payload[:4])[0]
                                if microseconds > 999_999:
                                    raise RouteEventIndexIntegrityError(
                                        "post-build BGP4MP_ET 扩展微秒非法"
                                    )
                            event_epoch_us = mrt_timestamp * 1_000_000 + microseconds
                            try:
                                indexed = next(indexed_rows)
                            except StopIteration as error:
                                raise RouteEventIndexIntegrityError(
                                    "post-build raw stream 多于 index raw_record"
                                ) from error
                            expected = (
                                ordinal,
                                offset,
                                length,
                                hashlib.sha256(raw_record).hexdigest(),
                                mrt_timestamp,
                                event_epoch_us,
                                mrt_type,
                                mrt_subtype,
                            )
                            if indexed != expected:
                                raise RouteEventIndexIntegrityError(
                                    "post-build raw frame 与 index raw_record 不一致"
                                )
                            ordinal += 1
                            offset += length
                    try:
                        next(indexed_rows)
                    except StopIteration:
                        pass
                    else:
                        raise RouteEventIndexIntegrityError(
                            "post-build index raw_record 多于 raw stream"
                        )
                    while hashing.read(1024 * 1024):
                        pass
                except (gzip.BadGzipFile, EOFError, zlib.error) as error:
                    raise RouteEventIndexIntegrityError(
                        "post-build gzip 独立复核失败"
                    ) from error
                after = os.fstat(compressed.fileno())
                immutable = (
                    "st_dev",
                    "st_ino",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
                if any(
                    getattr(before, field) != getattr(after, field)
                    for field in immutable
                ):
                    raise RouteEventIndexIntegrityError(
                        "post-build raw artifact 读取期间发生变化"
                    )
                if (
                    hashing.bytes_read != size_bytes
                    or hashing.hexdigest != file_sha256
                ):
                    raise RouteEventIndexIntegrityError(
                        "post-build compressed file SHA/size 复核失败"
                    )
                if ordinal == 0:
                    raise RouteEventIndexIntegrityError(
                        "post-build UPDATE artifact 没有 physical MRT record"
                    )
                total_checked += ordinal
                total_compressed_bytes += hashing.bytes_read
                by_artifact[artifact_id] = {
                    "relative_path": artifact["relative_path"],
                    "compressed_file_sha256": hashing.hexdigest,
                    "compressed_size_bytes": hashing.bytes_read,
                    "physical_record_checked_count": ordinal,
                    "record_hash_checked_count": ordinal,
                    "raw_reference_failure_count": 0,
                    "record_hash_verification_failed_count": 0,
                }
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    return {
        "schema_version": "route_event_raw_reference_audit_v1",
        "verification_mode": "independent_post_build_second_read",
        **expected_contract,
        "compressed_artifact_checked_count": len(artifacts),
        "compressed_file_verification_failed_count": 0,
        "compressed_bytes_checked": total_compressed_bytes,
        "physical_record_checked_count": total_checked,
        "record_hash_checked_count": total_checked,
        "raw_reference_failure_count": 0,
        "record_hash_verification_failed_count": 0,
        "by_artifact": by_artifact,
    }


def _index_fingerprint(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    digest.update(
        (canonical_json({"schema": INDEX_FINGERPRINT_SCHEMA}) + "\n").encode("utf-8")
    )
    table_queries = (
        (
            "sqlite_schema",
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name",
        ),
        ("metadata", "SELECT key, value_json FROM metadata WHERE key <> 'index_fingerprint_sha256' ORDER BY key"),
        ("artifact", "SELECT * FROM artifact ORDER BY artifact_id"),
        ("raw_record", "SELECT * FROM raw_record ORDER BY artifact_id, record_ordinal"),
        ("vantage_point", "SELECT * FROM vantage_point ORDER BY vp_id"),
        ("as_path", "SELECT * FROM as_path ORDER BY path_id"),
        ("route_event", "SELECT * FROM route_event ORDER BY route_event_id"),
        ("incident_observation", "SELECT * FROM incident_observation ORDER BY incident_id"),
        (
            "incident_route_event_link",
            "SELECT * FROM incident_route_event_link ORDER BY incident_id, route_event_id, object_type, object_id",
        ),
    )
    for table, query in table_queries:
        digest.update((table + "\n").encode("utf-8"))
        for row in connection.execute(query):
            digest.update((canonical_json(list(row)) + "\n").encode("utf-8"))
    return digest.hexdigest()


def _prepare_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), isolation_level=None)
    connection.execute("PRAGMA page_size = 4096")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    return connection


def _publish_no_overwrite(temporary: Path, target: Path, mode: int) -> None:
    descriptor = os.open(temporary, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(temporary, mode)
    os.link(temporary, target, follow_symlinks=False)
    directory_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    temporary.unlink()


def build_route_event_index(
    destination: os.PathLike[str] | str,
    *,
    manifest: Mapping[str, Any],
    manifest_verification: Mapping[str, Any],
    provenance: ImportProvenance,
    record_stream_factory: RecordStreamFactory,
    incidents: Sequence[IncidentObservation] = (),
    artifact_selection: Optional[Mapping[str, Any]] = None,
    selection_verification: Optional[Mapping[str, Any]] = None,
    mode: int = 0o440,
) -> IndexBuildResult:
    """构建并原子发布新的只读 SQLite RouteEvent 候选。

    目标必须不存在。任何 manifest、frame、元素、关联或 SQLite 约束失败都会
    删除临时文件，不留下可被误认成已准入的半成品。
    """

    target = Path(destination)
    try:
        target.parent.lstat()
    except OSError as error:
        raise RouteEventIndexError("索引目标父目录不可读") from error
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise RouteEventIndexError("索引目标父目录必须是非链接目录")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"RouteEvent 索引已存在，拒绝覆盖：{target}")
    if not isinstance(mode, int) or mode & ~0o777:
        raise RouteEventInputError("mode 必须是 Unix 权限位")
    if mode & 0o222 or not mode & 0o444:
        raise RouteEventInputError("RouteEvent 候选必须发布为不可写且至少可读")
    if not callable(record_stream_factory):
        raise RouteEventInputError("必须注入 record_stream_factory")

    if artifact_selection is None:
        if selection_verification is not None:
            raise RouteEventInputError("没有 artifact_selection 时不得提供 selection_verification")
        manifest_fingerprint, artifacts = _validate_manifest(
            manifest, manifest_verification
        )
        build_scope: Dict[str, Any] = {
            "scope_mode": "full_manifest_compatibility",
            "pilot_only": False,
            "production_complete": None,
        }
    else:
        if selection_verification is None:
            raise RouteEventInputError("UPDATE pilot 必须提供 selection_verification")
        manifest_fingerprint, artifacts, build_scope = _validate_pilot_selection(
            manifest,
            manifest_verification,
            artifact_selection,
            selection_verification,
        )
    normalized_provenance = _normalize_provenance(provenance)
    normalized_incidents = _normalize_incidents(incidents)
    parser_attestation = _validate_parser_attestation(
        record_stream_factory,
        provenance,
        required=artifact_selection is not None,
        expected_pilot_limits=build_scope.get("limits")
        if artifact_selection is not None
        else None,
        expected_data_profile=build_scope.get("data_profile")
        if artifact_selection is not None
        else None,
        expected_raw_reference_contract=build_scope.get(
            "raw_reference_contract"
        )
        if artifact_selection is not None
        else None,
    )
    import_run_id, config_sha256 = import_run_id_v1(
        manifest_fingerprint, provenance
    )

    temporary = target.parent / (
        f".{target.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    connection: Optional[sqlite3.Connection] = None
    try:
        connection = _prepare_connection(temporary)
        # sqlite3.executescript 会在执行 DDL 前结束当前事务；先创建仅存在于临时
        # 文件的空 schema，再用一个显式事务写入全部候选内容。
        _create_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        _insert_metadata(connection, "schema_version", INDEX_SCHEMA_VERSION)
        _insert_metadata(
            connection, "route_event_schema_version", ROUTE_EVENT_SCHEMA_VERSION
        )
        _insert_metadata(connection, "route_event_id_schema", ROUTE_EVENT_ID_SCHEMA)
        _insert_metadata(
            connection, "manifest_fingerprint_sha256", manifest_fingerprint
        )
        _insert_metadata(connection, "import_run_id", import_run_id)
        _insert_metadata(connection, "config_sha256", config_sha256)
        _insert_metadata(connection, "provenance", normalized_provenance)
        _insert_metadata(connection, "build_scope", build_scope)
        if parser_attestation is not None:
            _insert_metadata(connection, "parser_attestation", parser_attestation)
        attested_parser_name = (
            parser_attestation.get("parser_name")
            if isinstance(parser_attestation, Mapping)
            else None
        )
        if artifact_selection is None or parser_attestation is None:
            parser_capability = "injected_record_stream_only"
        elif attested_parser_name == _BGPDUMP_PARSER_NAME:
            parser_capability = "bgpdump_1_6_2_update_pilot"
        elif attested_parser_name == _NATIVE_UPDATE_PARSER_NAME:
            parser_capability = "native_bgp4mp_update_v1_research_pilot"
        else:  # pragma: no cover - attestation validator 已失败关闭。
            raise RouteEventInputError("parser capability 缺少冻结映射")
        _insert_metadata(
            connection,
            "parser_capability",
            {
                "capability": parser_capability,
                "built_in_mrt_parser": (
                    attested_parser_name == _NATIVE_UPDATE_PARSER_NAME
                ),
                "parser_attested": parser_attestation is not None,
                "pilot_only": artifact_selection is not None,
            },
        )

        record_count = 0
        route_event_count = 0
        state_event_not_materialized_count = 0
        limits = build_scope.get("limits", {})
        max_records = limits.get("max_physical_records")
        max_events = limits.get("max_route_events")
        for artifact in artifacts:
            connection.execute(
                """
                INSERT INTO artifact(
                    artifact_id, file_sha256, collector_id, artifact_type
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    artifact["artifact_id"],
                    artifact["file_sha256"],
                    artifact["collector_id"],
                    artifact["artifact_type"],
                ),
            )
            records = record_stream_factory(dict(artifact))
            added_records, added_events, added_state_events = _insert_artifact_records(
                connection,
                artifact,
                records,
                remaining_physical_records=None
                if max_records is None
                else max_records - record_count,
                remaining_route_events=None
                if max_events is None
                else max_events - route_event_count,
            )
            record_count += added_records
            route_event_count += added_events
            state_event_not_materialized_count += added_state_events

        parser_statistics_fingerprint = None
        if artifact_selection is not None:
            assert parser_attestation is not None
            parser_statistics, parser_statistics_fingerprint = (
                _pilot_parser_statistics(
                    connection,
                    record_stream_factory,
                    artifacts,
                    parser_attestation,
                )
            )
            _insert_metadata(
                connection,
                "parser_statistics_by_artifact",
                parser_statistics,
            )

        connection.execute(
            "CREATE INDEX route_event_time_prefix_idx ON route_event(event_epoch_us, prefix)"
        )
        connection.execute(
            "CREATE INDEX route_event_time_origin_idx ON route_event(event_epoch_us, origin_asn)"
        )
        connection.execute(
            "CREATE INDEX route_event_artifact_idx ON route_event(artifact_id, record_ordinal, element_ordinal)"
        )
        link_count, unsupported_objects = _insert_incidents_and_links(
            connection, normalized_incidents
        )
        summary = {
            "schema_version": "route_event_index_summary_v1",
            "manifest_fingerprint_sha256": manifest_fingerprint,
            "import_run_id": import_run_id,
            "artifact_count": len(artifacts),
            "raw_record_count": record_count,
            "route_event_count": route_event_count,
            "incident_count": len(normalized_incidents),
            "incident_route_event_link_count": link_count,
            "unsupported_incident_object_count": unsupported_objects,
            "state_event_not_materialized_count": state_event_not_materialized_count,
            "by_action": _count_by(connection, "action"),
            "by_afi_safi": _count_by(connection, "afi_safi"),
            "lineage_status": "raw_traceable",
            "classification": "observation_only",
            "causal_conclusion": None,
            "parser_capability": parser_capability,
            "build_scope": build_scope,
            "parser_attestation_fingerprint_sha256": None
            if parser_attestation is None
            else parser_attestation["attestation_fingerprint_sha256"],
            "parser_statistics_fingerprint_sha256": parser_statistics_fingerprint,
            "limitations": [
                "STATE_CHANGE 仅保留 raw_record 与计数，未伪造成 RouteEvent"
            ]
            + list(build_scope.get("limitations", [])),
        }
        _insert_metadata(connection, "summary", summary)
        fingerprint = _index_fingerprint(connection)
        _insert_metadata(connection, "index_fingerprint_sha256", fingerprint)
        check = connection.execute("PRAGMA quick_check").fetchone()
        if check != ("ok",):
            raise RouteEventIndexIntegrityError("SQLite quick_check 失败")
        connection.execute("COMMIT")
        connection.close()
        connection = None
        _publish_no_overwrite(temporary, target, mode)
        return IndexBuildResult(
            path=target,
            summary={**summary, "index_fingerprint_sha256": fingerprint},
        )
    except BaseException:
        if connection is not None:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            connection.close()
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


class RouteEventIndex:
    """以 SQLite immutable/只读模式复核候选索引。"""

    def __init__(self, path: os.PathLike[str] | str):
        self.path = Path(path)
        if not self.path.is_file() or self.path.is_symlink():
            raise RouteEventIndexIntegrityError("RouteEvent 索引必须是普通文件")
        uri = "file:{}?mode=ro&immutable=1".format(
            quote(self.path.resolve().as_posix(), safe="/")
        )
        self._connection = sqlite3.connect(uri, uri=True)
        self._connection.execute("PRAGMA query_only = ON")
        schema_version = self._metadata("schema_version")
        if schema_version != INDEX_SCHEMA_VERSION:
            self.close()
            raise RouteEventIndexIntegrityError("RouteEvent index schema 不受支持")

    def _metadata(self, key: str) -> Any:
        row = self._connection.execute(
            "SELECT value_json FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            raise RouteEventIndexIntegrityError(f"索引 metadata 缺失：{key}")
        try:
            return json.loads(row[0])
        except (TypeError, json.JSONDecodeError) as error:
            raise RouteEventIndexIntegrityError("索引 metadata JSON 损坏") from error

    def verify(self) -> Dict[str, Any]:
        check = self._connection.execute("PRAGMA quick_check").fetchone()
        if check != ("ok",):
            raise RouteEventIndexIntegrityError("SQLite quick_check 失败")
        expected = self._metadata("index_fingerprint_sha256")
        actual = _index_fingerprint(self._connection)
        if expected != actual:
            raise RouteEventIndexIntegrityError("RouteEvent index fingerprint 不一致")
        return {
            "verified": True,
            "index_fingerprint_sha256": actual,
            "summary": self.summary(),
        }

    def summary(self) -> Dict[str, Any]:
        summary = self._metadata("summary")
        if not isinstance(summary, dict):
            raise RouteEventIndexIntegrityError("索引 summary 非对象")
        return {
            **summary,
            "index_fingerprint_sha256": self._metadata(
                "index_fingerprint_sha256"
            ),
        }

    def _quality_counts(self, raw_audit: Mapping[str, Any]) -> Dict[str, int]:
        """逐行复核 D5 所需质量计数；不从发布摘要抄写零值。"""

        summary = self.summary()
        build_scope = self._metadata("build_scope")
        if (
            not isinstance(build_scope, Mapping)
            or build_scope.get("scope_mode") != "explicit_update_pilot"
            or build_scope.get("pilot_only") is not True
        ):
            raise RouteEventIndexIntegrityError(
                "RouteEvent reconciliation 仅支持显式 UPDATE pilot"
            )
        profile = build_scope.get("data_profile")
        if not isinstance(profile, Mapping):
            raise RouteEventIndexIntegrityError("pilot index 缺少 data_profile")
        raw_reference_contract = build_scope.get("raw_reference_contract")
        if not isinstance(raw_reference_contract, Mapping):
            raise RouteEventIndexIntegrityError(
                "pilot index 缺少 raw_reference_contract"
            )
        try:
            _, window_start = _normalize_utc(
                profile.get("window_start_utc"), "data_profile.window_start_utc"
            )
            _, window_end = _normalize_utc(
                profile.get("window_end_exclusive_utc"),
                "data_profile.window_end_exclusive_utc",
            )
        except RouteEventInputError as error:
            raise RouteEventIndexIntegrityError("pilot data_profile 时间非法") from error
        if window_start >= window_end:
            raise RouteEventIndexIntegrityError("pilot data_profile 窗口非法")

        raw_record_count = self._connection.execute(
            "SELECT COUNT(*) FROM raw_record"
        ).fetchone()[0]
        route_event_count = self._connection.execute(
            "SELECT COUNT(*) FROM route_event"
        ).fetchone()[0]
        counts = {
            "raw_reference_unresolved_count": 0,
            "processing_lineage_missing_count": 0,
            "record_hash_verification_failed_count": 0,
            "vp_identity_missing_count": 0,
            "route_event_id_conflict_count": 0,
            "invalid_asn_count": 0,
            "invalid_prefix_count": 0,
            "outside_window_record_count": 0,
        }
        if (
            not isinstance(raw_audit, Mapping)
            or raw_audit.get("schema_version")
            != "route_event_raw_reference_audit_v1"
            or raw_audit.get("verification_mode")
            != "independent_post_build_second_read"
        ):
            raise RouteEventIndexIntegrityError(
                "post-build raw reference audit 非法"
            )
        for field in (
            "compressed_artifact_checked_count",
            "compressed_file_verification_failed_count",
            "compressed_bytes_checked",
            "physical_record_checked_count",
            "record_hash_checked_count",
            "raw_reference_failure_count",
            "record_hash_verification_failed_count",
        ):
            value = raw_audit.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RouteEventIndexIntegrityError(
                    f"post-build raw reference audit.{field} 非法"
                )
        if (
            raw_audit["physical_record_checked_count"] != raw_record_count
            or raw_audit["record_hash_checked_count"] != raw_record_count
        ):
            raise RouteEventIndexIntegrityError(
                "post-build raw reference audit 未覆盖全部 raw_record"
            )
        counts["raw_reference_unresolved_count"] += (
            raw_audit["raw_reference_failure_count"]
            + raw_audit["compressed_file_verification_failed_count"]
        )
        counts["record_hash_verification_failed_count"] = raw_audit[
            "record_hash_verification_failed_count"
        ]

        statistics = self._metadata("parser_statistics_by_artifact")
        if not isinstance(statistics, Mapping):
            raise RouteEventIndexIntegrityError("pilot index parser statistics 非对象")
        statistics_fingerprint = hashlib.sha256(
            canonical_json(
                {
                    "schema": PARSER_STATISTICS_FINGERPRINT_SCHEMA,
                    "statistics_by_artifact": dict(statistics),
                }
            ).encode("utf-8")
        ).hexdigest()
        if summary.get("parser_statistics_fingerprint_sha256") != statistics_fingerprint:
            raise RouteEventIndexIntegrityError("parser statistics fingerprint 不一致")

        artifact_rows = tuple(
            self._connection.execute(
                "SELECT artifact_id, file_sha256 FROM artifact ORDER BY artifact_id"
            )
        )
        if set(statistics) != {row[0] for row in artifact_rows}:
            raise RouteEventIndexIntegrityError(
                "parser statistics artifact 集合与索引不一致"
            )
        for artifact_id, file_sha256 in artifact_rows:
            stats = statistics.get(artifact_id)
            raw_rows = self._connection.execute(
                """
                SELECT record_ordinal, record_offset, record_length, record_hash,
                       mrt_timestamp, event_epoch_us
                FROM raw_record
                WHERE artifact_id = ?
                ORDER BY record_ordinal
                """,
                (artifact_id,),
            )
            digest = hashlib.sha256()
            expected_ordinal = 0
            expected_offset = 0
            for ordinal, offset, length, record_hash, mrt_timestamp, event_epoch_us in raw_rows:
                record_valid = True
                if ordinal != expected_ordinal or offset != expected_offset:
                    record_valid = False
                    counts["raw_reference_unresolved_count"] += 1
                if not isinstance(record_hash, str) or not SHA256_RE.fullmatch(
                    record_hash
                ):
                    record_valid = False
                if (
                    isinstance(event_epoch_us, bool)
                    or not isinstance(event_epoch_us, int)
                    or event_epoch_us // 1_000_000 != mrt_timestamp
                ):
                    record_valid = False
                    counts["raw_reference_unresolved_count"] += 1
                elif not window_start <= event_epoch_us < window_end:
                    counts["outside_window_record_count"] += 1
                if record_valid:
                    digest.update(struct.pack("!QQQ", ordinal, offset, length))
                    digest.update(bytes.fromhex(record_hash))
                else:
                    raise RouteEventIndexIntegrityError(
                        "index raw_record 无法参与 parser hash-chain 复核"
                    )
                expected_ordinal += 1
                expected_offset = offset + length
            if expected_ordinal == 0:
                raise RouteEventIndexIntegrityError("index artifact 缺少 raw_record")
            if (
                not isinstance(file_sha256, str)
                or not SHA256_RE.fullmatch(file_sha256)
                or artifact_id != artifact_id_v1(file_sha256)
            ):
                counts["raw_reference_unresolved_count"] += expected_ordinal or 1
            if not isinstance(stats, Mapping):
                raise RouteEventIndexIntegrityError(
                    "index artifact 缺少 parser statistics"
                )
            if (
                stats.get("physical_record_count") != expected_ordinal
                or stats.get("record_hash_chain_sha256") != digest.hexdigest()
                or stats.get("compressed_file_sha256") != file_sha256
                or stats.get("compressed_read_passes") != 1
                or stats.get("status") != "complete"
            ):
                raise RouteEventIndexIntegrityError(
                    "parser statistics 与 index hash-chain 事实不一致"
                )

        # 每个 raw_record 的 element_count 必须与实际 RouteEvent 坐标闭合。
        unresolved_by_cardinality = self._connection.execute(
            """
            SELECT COALESCE(SUM(ABS(r.element_count - COALESCE(e.actual, 0))), 0)
            FROM raw_record r
            LEFT JOIN (
                SELECT artifact_id, record_ordinal, COUNT(*) AS actual
                FROM route_event
                GROUP BY artifact_id, record_ordinal
            ) e
              ON e.artifact_id = r.artifact_id
             AND e.record_ordinal = r.record_ordinal
            """
        ).fetchone()[0]
        counts["raw_reference_unresolved_count"] += unresolved_by_cardinality

        route_rows = self._connection.execute(
            """
            SELECT
                e.route_event_id, e.artifact_id, e.record_ordinal,
                e.element_ordinal, e.event_epoch_us, e.vp_id, e.action,
                e.afi_safi, e.prefix, e.origin_asn, e.path_id,
                a.file_sha256, a.collector_id,
                r.event_epoch_us, r.element_count,
                v.collector_id, v.peer_ip, v.peer_asn,
                p.canonical, p.segments_json
            FROM route_event e
            LEFT JOIN artifact a ON a.artifact_id = e.artifact_id
            LEFT JOIN raw_record r
              ON r.artifact_id = e.artifact_id
             AND r.record_ordinal = e.record_ordinal
            LEFT JOIN vantage_point v ON v.vp_id = e.vp_id
            LEFT JOIN as_path p ON p.path_id = e.path_id
            ORDER BY e.route_event_id
            """
        )
        for row in route_rows:
            (
                route_event_id,
                artifact_id,
                record_ordinal,
                element_ordinal,
                event_epoch_us,
                stored_vp_id,
                action,
                afi_safi,
                prefix,
                origin_asn,
                path_id,
                file_sha256,
                artifact_collector,
                raw_event_epoch_us,
                element_count,
                vp_collector,
                peer_ip,
                peer_asn,
                path_canonical,
                segments_json,
            ) = row
            if (
                raw_event_epoch_us is None
                or element_count is None
                or element_ordinal < 0
                or element_ordinal >= element_count
                or event_epoch_us != raw_event_epoch_us
            ):
                counts["raw_reference_unresolved_count"] += 1
            try:
                expected_event_id = route_event_id_v1(
                    file_sha256, record_ordinal, element_ordinal
                )
            except RouteEventInputError:
                expected_event_id = None
            if route_event_id != expected_event_id:
                counts["route_event_id_conflict_count"] += 1

            vp_invalid = False
            try:
                expected_vp_id = vp_id_v1(vp_collector, peer_ip, peer_asn)
            except RouteEventInputError:
                expected_vp_id = None
                vp_invalid = True
            if (
                expected_vp_id != stored_vp_id
                or vp_collector != artifact_collector
            ):
                vp_invalid = True
            if vp_invalid:
                counts["vp_identity_missing_count"] += 1

            asn_invalid = False
            try:
                _normalize_asn(peer_asn, "vp.peer_asn")
                if origin_asn is not None:
                    _normalize_asn(origin_asn, "route_event.origin_asn")
            except RouteEventInputError:
                asn_invalid = True
            if action == "withdraw":
                if path_id is not None or origin_asn is not None:
                    asn_invalid = True
            else:
                if path_id is None or path_canonical is None or segments_json is None:
                    asn_invalid = True
                else:
                    try:
                        parsed_segments = json.loads(segments_json)
                        if not isinstance(parsed_segments, list):
                            raise ValueError("segments 不是数组")
                        segments = tuple(
                            AsPathSegment(
                                item["segment_type"], tuple(item["asns"])
                            )
                            for item in parsed_segments
                            if isinstance(item, Mapping)
                        )
                        if len(segments) != len(parsed_segments):
                            raise ValueError("segments 含非对象")
                        normalized_path, expected_origin, _, _ = _normalize_segments(
                            segments
                        )
                        if (
                            normalized_path["canonical"] != path_canonical
                            or expected_origin != origin_asn
                        ):
                            raise ValueError("AS_PATH canonical/origin 不一致")
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError, RouteEventInputError):
                        asn_invalid = True
            if asn_invalid:
                counts["invalid_asn_count"] += 1

            try:
                normalized_prefix, expected_afi_safi = _normalize_prefix(prefix)
            except RouteEventInputError:
                normalized_prefix = expected_afi_safi = None
            if prefix != normalized_prefix or afi_safi != expected_afi_safi:
                counts["invalid_prefix_count"] += 1

        lineage_valid = True
        try:
            provenance_value = self._metadata("provenance")
            if not isinstance(provenance_value, Mapping):
                raise RouteEventInputError("provenance 非对象")
            provenance = ImportProvenance(
                parser_name=provenance_value.get("parser_name"),
                parser_version=provenance_value.get("parser_version"),
                importer_name=provenance_value.get("importer_name"),
                importer_version=provenance_value.get("importer_version"),
                processing_time_utc=provenance_value.get("processing_time_utc"),
                config=provenance_value.get("config"),
                source=provenance_value.get("source"),
            )
            normalized_provenance = _normalize_provenance(provenance)
            if canonical_json(normalized_provenance) != canonical_json(
                dict(provenance_value)
            ):
                raise RouteEventInputError("provenance 未规范化")
            manifest_fingerprint = self._metadata(
                "manifest_fingerprint_sha256"
            )
            expected_run, expected_config = import_run_id_v1(
                manifest_fingerprint, provenance
            )
            if (
                self._metadata("import_run_id") != expected_run
                or self._metadata("config_sha256") != expected_config
            ):
                raise RouteEventInputError("import run/config 血缘不一致")
            attestation = self._metadata("parser_attestation")
            if not isinstance(attestation, Mapping):
                raise RouteEventInputError("parser attestation 非对象")
            attestation_payload = dict(attestation)
            attestation_fingerprint = attestation_payload.pop(
                "attestation_fingerprint_sha256", None
            )
            attested_configuration = attestation_payload.get("configuration")
            attested_configuration_sha256 = attestation_payload.get(
                "configuration_sha256"
            )
            attested_parser_name = attestation_payload.get("parser_name")
            execution_policy = attestation_payload.get("binary_execution_policy")
            bgpdump_execution_valid = (
                attested_parser_name == _BGPDUMP_PARSER_NAME
                and execution_policy == _BGPDUMP_EXECUTION_POLICY
                and isinstance(attested_configuration, Mapping)
                and attested_configuration.get("command_arguments")
                == ["-m", "-p", "-v", "/dev/stdin"]
            )
            native_execution_valid = (
                attested_parser_name == _NATIVE_UPDATE_PARSER_NAME
                and execution_policy == _NATIVE_UPDATE_EXECUTION_POLICY
                and isinstance(attested_configuration, Mapping)
                and attested_configuration.get("command_arguments")
                == [_NATIVE_UPDATE_COMMAND_TOKEN]
                and attested_configuration.get("module_source_sha256")
                == attestation_payload.get("adapter_source_sha256")
                and attested_configuration.get("python_runtime_sha256")
                == attestation_payload.get("parser_binary_sha256")
                and attested_configuration.get("spool_mode")
                == "not_used_in_process"
                and attested_configuration.get("unknown_attribute_policy")
                == "fail_closed"
            )
            if (
                attestation_fingerprint
                != _parser_attestation_fingerprint(attestation_payload)
                or attested_parser_name != provenance.parser_name
                or attestation_payload.get("parser_version")
                != provenance.parser_version
                or not (bgpdump_execution_valid or native_execution_valid)
                or not isinstance(attested_configuration, Mapping)
                or not isinstance(attested_configuration_sha256, str)
                or hashlib.sha256(
                    canonical_json(dict(attested_configuration)).encode("utf-8")
                ).hexdigest()
                != attested_configuration_sha256
                or canonical_json(attested_configuration.get("pilot_limits"))
                != canonical_json(build_scope.get("limits"))
                or attested_configuration.get("window_start_utc")
                != profile.get("window_start_utc")
                or attested_configuration.get("window_end_exclusive_utc")
                != profile.get("window_end_exclusive_utc")
                or attested_configuration.get("max_frame_bytes")
                != raw_reference_contract.get("max_frame_bytes")
                or attested_configuration.get("max_spool_bytes")
                != build_scope.get("limits", {}).get("max_spool_bytes")
            ):
                raise RouteEventInputError("parser attestation 血缘不一致")
        except (RouteEventIndexError, TypeError, ValueError):
            lineage_valid = False
        if not lineage_valid:
            counts["processing_lineage_missing_count"] = max(
                route_event_count, raw_record_count, 1
            )
        return counts

    def reconciliation_summary(
        self,
        *,
        raw_root: os.PathLike[str] | str,
        artifact_selection: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """生成与当前 SQLite 内容严格一致的 D5 RouteEvent 机器摘要。"""

        verification = self.verify()
        summary = verification["summary"]
        build_scope = self._metadata("build_scope")
        raw_audit = _post_build_raw_reference_audit(
            self._connection,
            raw_root,
            artifact_selection,
            build_scope,
        )
        counts = self._quality_counts(raw_audit)
        return {**summary, **counts, "raw_reference_audit": raw_audit}

    def verify_reconciliation_summary(
        self,
        candidate: Mapping[str, Any],
        *,
        raw_root: os.PathLike[str] | str,
        artifact_selection: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """拒绝任何计数误报、字段遗漏、额外声明或索引指纹漂移。"""

        if not isinstance(candidate, Mapping):
            raise RouteEventIndexIntegrityError("RouteEvent reconciliation 必须是对象")
        expected = self.reconciliation_summary(
            raw_root=raw_root,
            artifact_selection=artifact_selection,
        )
        if canonical_json(dict(candidate)) != canonical_json(expected):
            raise RouteEventIndexIntegrityError(
                "RouteEvent reconciliation 与逐行复核事实不一致"
            )
        return {
            "verified": True,
            "index_fingerprint_sha256": expected[
                "index_fingerprint_sha256"
            ],
            "quality_counts": {
                key: expected[key]
                for key in (
                    "raw_reference_unresolved_count",
                    "processing_lineage_missing_count",
                    "record_hash_verification_failed_count",
                    "vp_identity_missing_count",
                    "route_event_id_conflict_count",
                    "invalid_asn_count",
                    "invalid_prefix_count",
                    "outside_window_record_count",
                )
            },
        }

    def get_route_event(self, route_event_id: str) -> Optional[Dict[str, Any]]:
        if not isinstance(route_event_id, str) or not ROUTE_EVENT_ID_RE.fullmatch(
            route_event_id
        ):
            raise RouteEventInputError("route_event_id 非法")
        row = self._connection.execute(
            """
            SELECT
                e.route_event_id, a.collector_id, e.event_time_utc,
                v.vp_id, v.peer_ip, v.peer_asn, e.action, e.afi_safi,
                e.prefix, p.canonical, p.segments_json, e.origin_asn,
                a.artifact_id, a.file_sha256, e.record_ordinal,
                e.element_ordinal, r.record_offset, r.record_length,
                r.record_hash, e.quality_flags_json, e.missing_reasons_json
            FROM route_event e
            JOIN artifact a ON a.artifact_id = e.artifact_id
            JOIN raw_record r
              ON r.artifact_id = e.artifact_id
             AND r.record_ordinal = e.record_ordinal
            JOIN vantage_point v ON v.vp_id = e.vp_id
            LEFT JOIN as_path p ON p.path_id = e.path_id
            WHERE e.route_event_id = ?
            """,
            (route_event_id,),
        ).fetchone()
        if row is None:
            return None
        provenance = self._metadata("provenance")
        import_run_id = self._metadata("import_run_id")
        as_path = None
        if row[9] is not None:
            as_path = {
                "semantics": "route_observation_path_snapshot",
                "causal_conclusion": None,
                "canonical": row[9],
                "segments": json.loads(row[10]),
            }
        return {
            "schema_version": ROUTE_EVENT_SCHEMA_VERSION,
            "record_kind": "route_event",
            "route_event_id_schema": ROUTE_EVENT_ID_SCHEMA,
            "route_event_id": row[0],
            "source": provenance["source"],
            "collector_id": row[1],
            "source_timezone": "UTC",
            "event_time_utc": row[2],
            "ingest_time_utc": provenance["processing_time_utc"],
            "parse_time_utc": provenance["processing_time_utc"],
            "vp_id": row[3],
            "vp_peer_ip": row[4],
            "vp_asn": row[5],
            "action": row[6],
            "afi_safi": row[7],
            "prefix": row[8],
            "as_path": as_path,
            "origin_asn": row[11],
            "raw_ref": {
                "artifact_id": row[12],
                "file_sha256": row[13],
                "record_ordinal": row[14],
                "element_ordinal": row[15],
                "record_offset": row[16],
                "record_length": row[17],
                "record_hash": row[18],
            },
            "parser_name": provenance["parser_name"],
            "parser_version": provenance["parser_version"],
            "importer_name": provenance["importer_name"],
            "importer_version": provenance["importer_version"],
            "import_run_id": import_run_id,
            "lineage_status": "raw_traceable",
            "quality_flags": json.loads(row[19]),
            "missing_reasons": json.loads(row[20]),
        }

    def links_for_incident(self, incident_id: str) -> Tuple[Dict[str, Any], ...]:
        if not isinstance(incident_id, str) or not INCIDENT_ID_RE.fullmatch(incident_id):
            raise RouteEventInputError("incident_id 非法")
        rows = self._connection.execute(
            """
            SELECT route_event_id, object_type, object_id, match_basis,
                   event_time_delta_microseconds, classification,
                   causal_conclusion
            FROM incident_route_event_link
            WHERE incident_id = ?
            ORDER BY route_event_id, object_type, object_id
            """,
            (incident_id,),
        )
        return tuple(
            {
                "incident_id": incident_id,
                "route_event_id": row[0],
                "object_type": row[1],
                "object_id": row[2],
                "match_basis": row[3],
                "event_time_delta_microseconds": row[4],
                "classification": row[5],
                "causal_conclusion": row[6],
            }
            for row in rows
        )

    def get_incident_observation(self, incident_id: str) -> Optional[Dict[str, Any]]:
        """返回关联输入及不可匹配对象的显式状态，不添加检测结论。"""

        if not isinstance(incident_id, str) or not INCIDENT_ID_RE.fullmatch(incident_id):
            raise RouteEventInputError("incident_id 非法")
        row = self._connection.execute(
            """
            SELECT event_time_utc, window_before_seconds, window_after_seconds,
                   affected_objects_json, classification, causal_conclusion
            FROM incident_observation
            WHERE incident_id = ?
            """,
            (incident_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "incident_id": incident_id,
            "event_time_utc": row[0],
            "window_before_seconds": row[1],
            "window_after_seconds": row[2],
            "affected_objects": json.loads(row[3]),
            "classification": row[4],
            "causal_conclusion": row[5],
        }

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "RouteEventIndex":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


__all__ = (
    "AsPathSegment",
    "ImportProvenance",
    "IncidentObject",
    "IncidentObservation",
    "IndexBuildResult",
    "MrtParserUnavailableError",
    "ParsedMrtRecord",
    "ParsedRouteElement",
    "RouteEventIndex",
    "RouteEventIndexError",
    "RouteEventIndexIntegrityError",
    "RouteEventInputError",
    "build_route_event_index",
    "builtin_mrt_parser_capability",
    "import_run_id_v1",
    "parse_mrt_artifact",
    "route_event_id_v1",
    "vp_id_v1",
)
