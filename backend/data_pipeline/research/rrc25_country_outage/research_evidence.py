"""研究型国家中断 Evidence Bundle v2 与严格 sidecar 组装器。

现有 Evidence Bundle v2 继续负责 Incident、旧事实、RouteEvent、原始 MRT
坐标和处理血缘。本模块不重新解释其字段，而是为 episode、wave、五分钟样本、
恢复候选与连续性未知补充一个最小研究 sidecar，并校验两者之间的引用闭合。

本模块是纯函数层：不读取文件、数据库或网络，不修改输入，也不会把未知状态
转换成零。RouteEvent 和 AS_PATH 只表示 RRC25 路由观测，不生成机制、根因或
意图结论。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ...evidence.bundle import (
    EvidenceBundleError,
    build_evidence_bundle_v2,
    canonical_evidence_bundle_bytes,
    validate_reference_closure,
)


SIDECAR_SCHEMA_VERSION = "research-evidence-sidecar/v1"

_SIDECAR_ID_RE = re.compile(r"^research_sidecar_v1_[0-9a-f]{24}$")
_RUN_ID_RE = re.compile(r"^research_run_v1_[0-9a-f]{24}$")
_INCIDENT_ID_RE = re.compile(r"^inc_v1_[0-9a-f]{24}$")
_BUNDLE_ID_RE = re.compile(r"^eb_v2_[0-9a-f]{32}$")
_EPISODE_ID_RE = re.compile(r"^episode_v1_[0-9a-f]{24}$")
_WAVE_ID_RE = re.compile(r"^wave_v1_[0-9a-f]{24}$")
_SAMPLE_ID_RE = re.compile(r"^sample_v1_[0-9a-f]{24}$")
_SNAPSHOT_ID_RE = re.compile(r"^snapshot_v1_[0-9a-f]{24}$")
_ROUTE_ID_RE = re.compile(r"^rte_v1_[0-9a-f]{32}$")
_RAW_ID_RE = re.compile(r"^raw_v1_[0-9a-f]{32}$")
_ARTIFACT_ID_RE = re.compile(r"^art_v1_[0-9a-f]{32}$")
_VP_ID_RE = re.compile(r"^vp_v1_[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REASON_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


class ResearchEvidenceError(ValueError):
    """研究证据输入不完整、引用未闭合或越过观测边界。"""


def _json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ResearchEvidenceError("禁止输出非有限浮点数")
        return 0 if value == 0 else value
    if isinstance(value, Mapping):
        return {
            str(key): _json_ready(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_ready(item) for item in value), key=_canonical_json)
    raise ResearchEvidenceError("输入含不可序列化对象：{}".format(type(value).__name__))


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_research_sidecar_bytes(sidecar: Mapping[str, Any]) -> bytes:
    """返回 sidecar 的稳定 UTF-8 JSON 字节。"""

    if not isinstance(sidecar, Mapping):
        raise ResearchEvidenceError("research sidecar 必须是对象")
    return _canonical_json(sidecar).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _record_sha256(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _stable_id(prefix: str, identity: Mapping[str, Any], length: int = 24) -> str:
    return prefix + _record_sha256(identity)[:length]


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchEvidenceError(f"{field} 必须是对象")
    return value


def _sequence(value: object, field: str) -> Tuple[Any, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise ResearchEvidenceError(f"{field} 必须是序列")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise ResearchEvidenceError(f"{field} 必须可迭代") from error


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ResearchEvidenceError(f"{field} 必须是首尾无空白的非空字符串")
    return value


def _chinese(value: object, field: str) -> str:
    text = _text(value, field)
    if _HAN_RE.search(text) is None:
        raise ResearchEvidenceError(f"{field} 必须包含中文说明")
    return text


def _pattern(value: object, pattern: re.Pattern[str], field: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ResearchEvidenceError(f"{field} 格式非法")
    return value


def _sha256(value: object, field: str) -> str:
    return _pattern(value, _SHA256_RE, field)


def _utc(value: object, field: str) -> str:
    if not isinstance(value, str) or "T" not in value:
        raise ResearchEvidenceError(f"{field} 必须是带时区的 ISO 8601 时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ResearchEvidenceError(f"{field} 不是合法时间") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond:
        raise ResearchEvidenceError(f"{field} 必须带时区且精确到秒")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unique_records(
    values: Iterable[Mapping[str, Any]], *, id_field: str, field: str
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for index, raw in enumerate(values):
        item = dict(_mapping(raw, f"{field}[{index}]"))
        identifier = _text(item.get(id_field), f"{field}[{index}].{id_field}")
        previous = result.get(identifier)
        if previous is not None and _canonical_json(previous) != _canonical_json(item):
            raise ResearchEvidenceError(f"同一 {id_field} 对应不同内容: {identifier}")
        if previous is not None:
            raise ResearchEvidenceError(f"{field} 不得重复 {id_field}: {identifier}")
        result[identifier] = item
    return result


def _normalize_research_records(
    *,
    episode: Mapping[str, Any],
    waves: Iterable[Mapping[str, Any]],
    samples: Iterable[Mapping[str, Any]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    episode_value = dict(_mapping(episode, "episode"))
    if episode_value.get("schema_version") != "country-outage-episode/v1":
        raise ResearchEvidenceError("episode.schema_version 非法")
    episode_id = _pattern(episode_value.get("episode_id"), _EPISODE_ID_RE, "episode_id")
    run_id = _pattern(episode_value.get("run_id"), _RUN_ID_RE, "episode.run_id")
    # 合同将这些字段定义为 uniqueItems 集合；规范化顺序后再计算记录哈希，
    # 避免调用方枚举顺序改变内容寻址身份。
    wave_ids_input = tuple(
        _pattern(value, _WAVE_ID_RE, "episode.wave_ids[]")
        for value in _sequence(episode_value.get("wave_ids"), "episode.wave_ids")
    )
    sample_ids_input = tuple(
        _pattern(value, _SAMPLE_ID_RE, "episode.supporting_sample_ids[]")
        for value in _sequence(
            episode_value.get("supporting_sample_ids"),
            "episode.supporting_sample_ids",
        )
    )
    normalized_incident_mappings = []
    for raw_mapping in _sequence(
        episode_value.get("incident_mappings"), "episode.incident_mappings"
    ):
        item = dict(_mapping(raw_mapping, "episode.incident_mappings[]"))
        evidence_ids = tuple(
            _pattern(value, _SAMPLE_ID_RE, "incident_mapping.evidence_sample_ids[]")
            for value in _sequence(
                item.get("evidence_sample_ids"),
                "incident_mapping.evidence_sample_ids",
            )
        )
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ResearchEvidenceError("incident_mapping.evidence_sample_ids 不得重复")
        item["evidence_sample_ids"] = sorted(evidence_ids)
        normalized_incident_mappings.append(_json_ready(item))
    incident_mapping_input = tuple(normalized_incident_mappings)
    if len(wave_ids_input) != len(set(wave_ids_input)):
        raise ResearchEvidenceError("episode.wave_ids 不得重复")
    if len(sample_ids_input) != len(set(sample_ids_input)):
        raise ResearchEvidenceError("episode.supporting_sample_ids 不得重复")
    if len(incident_mapping_input) != len(
        {_canonical_json(item) for item in incident_mapping_input}
    ):
        raise ResearchEvidenceError("episode.incident_mappings 不得重复")
    episode_value["wave_ids"] = sorted(wave_ids_input)
    episode_value["supporting_sample_ids"] = sorted(sample_ids_input)
    episode_value["incident_mappings"] = sorted(
        incident_mapping_input, key=_canonical_json
    )

    wave_by_id = _unique_records(waves, id_field="wave_id", field="waves")
    for wave_id, wave in wave_by_id.items():
        _pattern(wave_id, _WAVE_ID_RE, "wave_id")
        if wave.get("schema_version") != "country-outage-wave/v1":
            raise ResearchEvidenceError(f"{wave_id}.schema_version 非法")
        if wave.get("episode_id") != episode_id or wave.get("run_id") != run_id:
            raise ResearchEvidenceError(f"{wave_id} 未绑定当前 episode/run")
        wave_sample_ids = tuple(
            _pattern(value, _SAMPLE_ID_RE, f"{wave_id}.supporting_sample_ids[]")
            for value in _sequence(
                wave.get("supporting_sample_ids"),
                f"{wave_id}.supporting_sample_ids",
            )
        )
        if len(wave_sample_ids) != len(set(wave_sample_ids)):
            raise ResearchEvidenceError(f"{wave_id}.supporting_sample_ids 不得重复")
        wave["supporting_sample_ids"] = sorted(wave_sample_ids)

    sample_by_id = _unique_records(samples, id_field="sample_id", field="samples")
    for sample_id, sample in sample_by_id.items():
        _pattern(sample_id, _SAMPLE_ID_RE, "sample_id")
        _pattern(sample.get("snapshot_id"), _SNAPSHOT_ID_RE, f"{sample_id}.snapshot_id")
        if sample.get("schema_version") != "country-outage-sample/v1":
            raise ResearchEvidenceError(f"{sample_id}.schema_version 非法")
        if sample.get("run_id") != run_id:
            raise ResearchEvidenceError(f"{sample_id} 未绑定当前 run")
        if sample.get("continuity_state") not in {"continuous", "unknown_after_gap"}:
            raise ResearchEvidenceError(f"{sample_id}.continuity_state 非法")

    expected_waves = set(_sequence(episode_value.get("wave_ids"), "episode.wave_ids"))
    expected_samples = set(
        _sequence(episode_value.get("supporting_sample_ids"), "episode.supporting_sample_ids")
    )
    if set(wave_by_id) != expected_waves:
        raise ResearchEvidenceError("episode.wave_ids 与 wave 记录集合不闭合")
    if set(sample_by_id) != expected_samples:
        raise ResearchEvidenceError("episode.supporting_sample_ids 与 sample 记录集合不闭合")
    for wave_id, wave in wave_by_id.items():
        supporting = set(
            _sequence(wave.get("supporting_sample_ids"), f"{wave_id}.supporting_sample_ids")
        )
        if not supporting or not supporting <= set(sample_by_id):
            raise ResearchEvidenceError(f"{wave_id} 的 supporting_sample_ids 未闭合")

    wave_values = [wave_by_id[key] for key in sorted(wave_by_id)]
    sample_values = [sample_by_id[key] for key in sorted(sample_by_id)]
    return episode_value, wave_values, sample_values


def _normalize_candidates(
    values: Iterable[Mapping[str, Any]], sample_ids: set[str]
) -> List[Dict[str, Any]]:
    result = []
    for index, raw in enumerate(values):
        item = _mapping(raw, f"recovery_candidates[{index}]")
        required = {"kind", "start_at", "supporting_sample_ids", "confirmed", "reason_code"}
        if set(item) != required:
            raise ResearchEvidenceError(
                f"recovery_candidates[{index}] 字段必须精确为 {sorted(required)}"
            )
        if item["kind"] not in {"partial", "full"}:
            raise ResearchEvidenceError("recovery candidate kind 非法")
        supporting = sorted(
            set(_sequence(item["supporting_sample_ids"], "candidate.supporting_sample_ids"))
        )
        if not supporting or not set(supporting) <= sample_ids:
            raise ResearchEvidenceError("recovery candidate 的 sample 引用未闭合")
        if not isinstance(item["confirmed"], bool):
            raise ResearchEvidenceError("recovery candidate confirmed 必须是布尔值")
        reason = _pattern(item["reason_code"], _REASON_RE, "candidate.reason_code")
        result.append(
            {
                "kind": item["kind"],
                "start_at": _utc(item["start_at"], "candidate.start_at"),
                "supporting_sample_ids": supporting,
                "confirmed": item["confirmed"],
                "reason_code": reason,
            }
        )
    result.sort(key=_canonical_json)
    if len({_canonical_json(item) for item in result}) != len(result):
        raise ResearchEvidenceError("recovery_candidates 不得重复")
    return result


def _merge_bundle_records(
    bundles: Sequence[Mapping[str, Any]], field: str, id_field: str
) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for bundle in bundles:
        for raw in bundle[field]:
            item = dict(raw)
            identifier = item[id_field]
            previous = merged.get(identifier)
            if previous is not None and _canonical_json(previous) != _canonical_json(item):
                raise ResearchEvidenceError(f"多个 Bundle 中的 {identifier} 内容不一致")
            merged[identifier] = item
    return merged


def _incident_episode_links(
    incidents: Sequence[Mapping[str, Any]],
    bundles: Sequence[Mapping[str, Any]],
    episode: Mapping[str, Any],
    sample_ids: set[str],
) -> List[Dict[str, Any]]:
    mappings_raw = _sequence(episode.get("incident_mappings"), "episode.incident_mappings")
    mappings: Dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(mappings_raw):
        item = _mapping(raw, f"episode.incident_mappings[{index}]")
        ref = _text(item.get("incident_ref"), "incident_mapping.incident_ref")
        if ref in mappings:
            raise ResearchEvidenceError("episode.incident_mappings 不得重复 incident_ref")
        if item.get("causal") is not False:
            raise ResearchEvidenceError("Incident 与 episode 的映射必须显式 causal=false")
        evidence_samples = set(
            _sequence(item.get("evidence_sample_ids"), "incident_mapping.evidence_sample_ids")
        )
        if not evidence_samples <= sample_ids:
            raise ResearchEvidenceError("incident_mapping 的 sample 引用未闭合")
        mappings[ref] = item

    if not incidents:
        if not mappings or any(item.get("relation") != "no_correspondence" for item in mappings.values()):
            raise ResearchEvidenceError("零 Incident 映射必须以 no_correspondence 显式表达")
        return []

    bundle_by_incident = {bundle["incident"]["incident_id"]: bundle for bundle in bundles}
    result = []
    for incident in incidents:
        incident_id = _pattern(incident.get("incident_id"), _INCIDENT_ID_RE, "incident_id")
        detail_ref = _text(incident.get("detail_reference"), "incident.detail_reference")
        mapping = mappings.get(detail_ref)
        if mapping is None or mapping.get("relation") == "no_correspondence":
            raise ResearchEvidenceError(f"Incident {incident_id} 未在 episode 中建立非因果映射")
        evidence_samples = sorted(
            set(_sequence(mapping.get("evidence_sample_ids"), "evidence_sample_ids"))
        )
        if not evidence_samples:
            raise ResearchEvidenceError("已映射 Incident 必须至少引用一个支持样本")
        result.append(
            {
                "incident_id": incident_id,
                "bundle_id": bundle_by_incident[incident_id]["bundle_id"],
                "episode_id": episode["episode_id"],
                "relation": mapping["relation"],
                "causal": False,
                "evidence_sample_ids": evidence_samples,
            }
        )
    return sorted(result, key=lambda item: item["incident_id"])


def _normalize_sample_route_links(
    values: Iterable[Mapping[str, Any]],
    *,
    sample_ids: set[str],
    route_ids: set[str],
    mapped: bool,
) -> List[Dict[str, Any]]:
    if not mapped:
        supplied = _sequence(values, "sample_route_event_links")
        if supplied:
            raise ResearchEvidenceError("零 Incident 映射不得伪造 sample→RouteEvent 引用")
        return [
            {
                "sample_id": sample_id,
                "link_state": "not_applicable",
                "route_event_ids": [],
                "missing_reason_zh": "未关联 Incident，因此没有组装 Evidence Bundle RouteEvent 引用。",
            }
            for sample_id in sorted(sample_ids)
        ]

    rows = _sequence(values, "sample_route_event_links")
    by_sample = {}
    for index, raw in enumerate(rows):
        item = _mapping(raw, f"sample_route_event_links[{index}]")
        required = {"sample_id", "link_state", "route_event_ids", "missing_reason_zh"}
        if set(item) != required:
            raise ResearchEvidenceError(
                f"sample_route_event_links[{index}] 字段必须精确为 {sorted(required)}"
            )
        sample_id = _pattern(item["sample_id"], _SAMPLE_ID_RE, "link.sample_id")
        if sample_id not in sample_ids or sample_id in by_sample:
            raise ResearchEvidenceError("sample_route_event_links 含未知或重复 sample_id")
        route_refs = sorted(set(_sequence(item["route_event_ids"], "link.route_event_ids")))
        unresolved = set(route_refs) - route_ids
        if unresolved:
            raise ResearchEvidenceError("sample→RouteEvent 引用未闭合：{}".format(",".join(sorted(unresolved))))
        state = item["link_state"]
        if state == "linked":
            if not route_refs or item["missing_reason_zh"] is not None:
                raise ResearchEvidenceError("linked 样本必须有 RouteEvent 且 missing_reason_zh=null")
            missing = None
        elif state == "unknown":
            if route_refs:
                raise ResearchEvidenceError("unknown 样本不得伪造 RouteEvent 引用")
            missing = _chinese(item["missing_reason_zh"], "link.missing_reason_zh")
        else:
            raise ResearchEvidenceError("映射场景 link_state 只能是 linked 或 unknown")
        by_sample[sample_id] = {
            "sample_id": sample_id,
            "link_state": state,
            "route_event_ids": route_refs,
            "missing_reason_zh": missing,
        }
    if set(by_sample) != sample_ids:
        raise ResearchEvidenceError("sample_route_event_links 必须精确覆盖全部研究样本")
    used_routes = {
        route_id for item in by_sample.values() for route_id in item["route_event_ids"]
    }
    if used_routes != route_ids:
        raise ResearchEvidenceError("每个 Bundle RouteEvent 必须至少由一个研究样本引用")
    return [by_sample[key] for key in sorted(by_sample)]


def _sidecar_identity_payload(sidecar: Mapping[str, Any]) -> Dict[str, Any]:
    payload = dict(sidecar)
    payload.pop("sidecar_id", None)
    return payload


def validate_research_sidecar_reference_closure(
    sidecar: Mapping[str, Any], *, bundles: Iterable[Mapping[str, Any]] = ()
) -> None:
    """校验 Incident→研究记录→RouteEvent→raw→artifact 的全部引用。"""

    payload = _mapping(sidecar, "sidecar")
    if payload.get("schema_version") != SIDECAR_SCHEMA_VERSION:
        raise ResearchEvidenceError("sidecar.schema_version 非法")
    expected_sidecar_id = _stable_id(
        "research_sidecar_v1_", _sidecar_identity_payload(payload)
    )
    if payload.get("sidecar_id") != expected_sidecar_id:
        raise ResearchEvidenceError("sidecar_id 与规范内容不一致")

    bundle_refs = _unique_records(payload.get("bundle_refs", ()), id_field="bundle_id", field="bundle_refs")
    incident_links = _unique_records(
        payload.get("incident_episode_links", ()),
        id_field="incident_id",
        field="incident_episode_links",
    )
    wave_refs = _unique_records(payload.get("wave_refs", ()), id_field="wave_id", field="wave_refs")
    sample_refs = _unique_records(payload.get("sample_refs", ()), id_field="sample_id", field="sample_refs")
    route_refs = _unique_records(
        payload.get("route_event_refs", ()), id_field="route_event_id", field="route_event_refs"
    )
    raw_refs = _unique_records(
        payload.get("raw_record_refs", ()), id_field="raw_record_ref_id", field="raw_record_refs"
    )
    artifact_refs = _unique_records(
        payload.get("artifact_refs", ()), id_field="artifact_id", field="artifact_refs"
    )

    mapping = _mapping(payload.get("mapping"), "mapping")
    incident_ids = set(mapping.get("incident_ids", ()))
    bundle_ids = set(mapping.get("bundle_ids", ()))
    mapping_state = mapping.get("mapping_state")
    expected_mapping_state = (
        "unmapped" if not incident_ids else "exact" if len(incident_ids) == 1 else "multiple"
    )
    if mapping_state != expected_mapping_state or len(bundle_ids) != len(incident_ids):
        raise ResearchEvidenceError("mapping_state 或 Incident/Bundle 基数不一致")
    if incident_ids != set(incident_links) or bundle_ids != set(bundle_refs):
        raise ResearchEvidenceError("mapping 与 bundle/incident link 集合不一致")
    if {item["incident_id"] for item in bundle_refs.values()} != incident_ids:
        raise ResearchEvidenceError("bundle_refs.incident_id 未闭合")
    if {item["bundle_id"] for item in incident_links.values()} != bundle_ids:
        raise ResearchEvidenceError("incident_episode_links.bundle_id 未闭合")

    episode_ref = _mapping(payload.get("episode_ref"), "episode_ref")
    if set(episode_ref.get("wave_ids", ())) != set(wave_refs):
        raise ResearchEvidenceError("episode→wave 引用未闭合")
    if set(episode_ref.get("supporting_sample_ids", ())) != set(sample_refs):
        raise ResearchEvidenceError("episode→sample 引用未闭合")
    if any(item.get("episode_id") != episode_ref.get("episode_id") for item in incident_links.values()):
        raise ResearchEvidenceError("Incident→episode 引用未闭合")
    for wave in wave_refs.values():
        if not set(wave.get("supporting_sample_ids", ())) <= set(sample_refs):
            raise ResearchEvidenceError("wave→sample 引用未闭合")

    link_by_sample = _unique_records(
        payload.get("sample_route_event_links", ()),
        id_field="sample_id",
        field="sample_route_event_links",
    )
    if set(link_by_sample) != set(sample_refs):
        raise ResearchEvidenceError("sample→RouteEvent link 未覆盖全部样本")
    linked_route_ids = {
        route_id for item in link_by_sample.values() for route_id in item.get("route_event_ids", ())
    }
    if linked_route_ids != set(route_refs):
        raise ResearchEvidenceError("sample→RouteEvent 引用未闭合")
    for item in link_by_sample.values():
        state = item.get("link_state")
        route_ids_for_sample = item.get("route_event_ids", ())
        if state == "linked" and (
            not route_ids_for_sample or item.get("missing_reason_zh") is not None
        ):
            raise ResearchEvidenceError("linked 样本的引用状态不一致")
        if state in {"unknown", "not_applicable"} and (
            route_ids_for_sample or item.get("missing_reason_zh") is None
        ):
            raise ResearchEvidenceError("未知/不适用样本不得伪造 RouteEvent 引用")
        if state not in {"linked", "unknown", "not_applicable"}:
            raise ResearchEvidenceError("sample RouteEvent link_state 非法")

    linked_raw_ids = {
        raw_id for item in route_refs.values() for raw_id in item.get("raw_record_ref_ids", ())
    }
    if linked_raw_ids != set(raw_refs):
        raise ResearchEvidenceError("RouteEvent→raw 引用未闭合")
    linked_artifact_ids = {item.get("artifact_id") for item in raw_refs.values()}
    if linked_artifact_ids != set(artifact_refs):
        raise ResearchEvidenceError("raw→artifact 引用未闭合")
    for raw_id, raw in raw_refs.items():
        file_hash = _sha256(raw.get("file_sha256"), f"{raw_id}.file_sha256")
        expected_artifact = _stable_id(
            "art_v1_",
            {"schema": "artifact_id_v1", "file_sha256": file_hash},
            32,
        )
        if raw.get("artifact_id") != expected_artifact:
            raise ResearchEvidenceError("artifact_id 与 file_sha256 内容寻址结果不一致")
        expected_raw = _stable_id(
            "raw_v1_",
            {
                "schema": "raw_record_ref_id_v1",
                "file_sha256": file_hash,
                "record_ordinal": raw.get("record_ordinal"),
                "element_ordinal": raw.get("element_ordinal"),
            },
            32,
        )
        if raw_id != expected_raw:
            raise ResearchEvidenceError("raw_record_ref_id 与原始坐标内容寻址结果不一致")
    for route_id, route in route_refs.items():
        raw_ids_for_route = route.get("raw_record_ref_ids", ())
        if len(raw_ids_for_route) != 1:
            raise ResearchEvidenceError("研究 RouteEvent 必须精确定位一个 raw record element")
        raw = raw_refs[raw_ids_for_route[0]]
        expected_route = _stable_id(
            "rte_v1_",
            {
                "schema": "route_event_id_v1",
                "file_sha256": raw["file_sha256"],
                "record_ordinal": raw["record_ordinal"],
                "element_ordinal": raw["element_ordinal"],
            },
            32,
        )
        if route_id != expected_route:
            raise ResearchEvidenceError("route_event_id 与原始坐标内容寻址结果不一致")
        if not set(route.get("bundle_ids", ())) <= bundle_ids:
            raise ResearchEvidenceError("RouteEvent→Bundle 引用未闭合")
    for artifact_id, artifact in artifact_refs.items():
        expected_raw = sorted(
            raw_id
            for raw_id, raw in raw_refs.items()
            if raw.get("artifact_id") == artifact_id
        )
        if artifact.get("raw_record_ref_ids") != expected_raw:
            raise ResearchEvidenceError("artifact.raw_record_ref_ids 反向引用不闭合")
        if artifact.get("file_sha256") != raw_refs[expected_raw[0]]["file_sha256"]:
            raise ResearchEvidenceError("artifact.file_sha256 与 raw 引用不一致")

    recovery = _mapping(payload.get("recovery_assessment"), "recovery_assessment")
    for candidate in recovery.get("candidates", ()):
        if not set(candidate.get("supporting_sample_ids", ())) <= set(sample_refs):
            raise ResearchEvidenceError("恢复候选的 sample 引用未闭合")

    legacy = payload.get("legacy_source_fact_refs", ())
    for item in legacy:
        if item.get("bundle_id") not in bundle_refs or item.get("incident_id") not in incident_ids:
            raise ResearchEvidenceError("legacy source fact 的 Bundle/Incident 引用未闭合")

    closure = _mapping(payload.get("reference_closure"), "reference_closure")
    if closure.get("unresolved_refs") != []:
        raise ResearchEvidenceError("reference_closure.unresolved_refs 必须为空")
    has_unknown_link = any(
        item.get("link_state") == "unknown" for item in link_by_sample.values()
    )
    if mapping_state == "unmapped":
        expected_closure = {
            "incident_episode": "not_applicable_unmapped",
            "episode_wave_sample": "passed",
            "sample_route_event": "not_applicable_unmapped",
            "route_raw_artifact": "not_applicable_unmapped",
            "overall": "passed_with_explicit_unmapped",
            "unresolved_refs": [],
        }
    else:
        expected_closure = {
            "incident_episode": "passed",
            "episode_wave_sample": "passed",
            "sample_route_event": "explicit_unknown" if has_unknown_link else "passed",
            "route_raw_artifact": "passed",
            "overall": "passed_with_explicit_unknown" if has_unknown_link else "passed",
            "unresolved_refs": [],
        }
    if dict(closure) != expected_closure:
        raise ResearchEvidenceError("reference_closure 状态与实际引用不一致")

    supplied_bundles = tuple(bundles)
    if supplied_bundles:
        actual_bundles = _unique_records(supplied_bundles, id_field="bundle_id", field="bundles")
        if set(actual_bundles) != set(bundle_refs):
            raise ResearchEvidenceError("sidecar bundle_refs 与实际 Bundle 集合不一致")
        actual_legacy = set()
        actual_route_ids = set()
        actual_raw_ids = set()
        for bundle_id, bundle in actual_bundles.items():
            validate_reference_closure(bundle)
            ref = bundle_refs[bundle_id]
            if ref["incident_id"] != bundle["incident"]["incident_id"]:
                raise ResearchEvidenceError("bundle_ref Incident 不一致")
            actual_hash = _sha256_bytes(canonical_evidence_bundle_bytes(bundle))
            if ref["bundle_sha256"] != actual_hash:
                raise ResearchEvidenceError("bundle_ref 内容哈希不一致")
            for route in bundle["route_event_refs"]:
                actual_route_ids.add(route["route_event_id"])
                sidecar_route = route_refs.get(route["route_event_id"])
                if sidecar_route is None or sidecar_route["route_event_ref_sha256"] != _record_sha256(route):
                    raise ResearchEvidenceError("Bundle RouteEvent 未被 sidecar 原样引用")
                if bundle_id not in sidecar_route["bundle_ids"]:
                    raise ResearchEvidenceError("RouteEvent 缺少所属 Bundle 反向引用")
            for raw in bundle["raw_record_refs"]:
                actual_raw_ids.add(raw["raw_record_ref_id"])
            for fact in bundle["source_fact_mapping"]["source_facts"]:
                actual_legacy.add((bundle_id, bundle["incident"]["incident_id"], fact["source_fact_id"]))
        if actual_route_ids != set(route_refs) or actual_raw_ids != set(raw_refs):
            raise ResearchEvidenceError("Bundle 与 sidecar 的 RouteEvent/raw 集合不一致")
        sidecar_legacy = {
            (item["bundle_id"], item["incident_id"], item["source_fact_id"])
            for item in legacy
        }
        if sidecar_legacy != actual_legacy:
            raise ResearchEvidenceError("legacy source fact 未完整保留")


def build_research_evidence_package(
    *,
    incidents: Iterable[Mapping[str, Any]],
    episode: Mapping[str, Any],
    waves: Iterable[Mapping[str, Any]],
    samples: Iterable[Mapping[str, Any]],
    recovery_candidates: Iterable[Mapping[str, Any]],
    sample_route_event_links: Iterable[Mapping[str, Any]] = (),
    evidence_bundle_parameters: Optional[Mapping[str, Any]] = None,
    mapping_missing_reason_zh: Optional[str] = None,
    limitations_zh: Iterable[str] = (),
) -> Dict[str, Any]:
    """组装零、一个或多个 Incident 对应的 Bundle v2 与研究 sidecar。

    有 Incident 时，每个 Incident 都通过既有 ``build_evidence_bundle_v2``
    独立组装，并要求达到 ``raw_traceable``，从而形成 RouteEvent、原始 MRT
    坐标和制品 SHA256 闭环。零 Incident 时不伪造 Bundle，sidecar 以
    ``unmapped`` 和 ``not_applicable`` 显式保留。
    """

    episode_value, wave_values, sample_values = _normalize_research_records(
        episode=episode, waves=waves, samples=samples
    )
    run_id = episode_value["run_id"]
    sample_ids = {sample["sample_id"] for sample in sample_values}
    candidates = _normalize_candidates(recovery_candidates, sample_ids)

    incident_values = [dict(_mapping(item, "incidents[]")) for item in _sequence(incidents, "incidents")]
    incident_values.sort(key=lambda item: str(item.get("incident_id", "")))
    incident_ids = []
    detail_refs = []
    for incident in incident_values:
        incident_ids.append(_pattern(incident.get("incident_id"), _INCIDENT_ID_RE, "incident_id"))
        detail_refs.append(_text(incident.get("detail_reference"), "incident.detail_reference"))
    if len(incident_ids) != len(set(incident_ids)) or len(detail_refs) != len(set(detail_refs)):
        raise ResearchEvidenceError("incidents 不得重复 incident_id 或 detail_reference")

    parameters = {} if evidence_bundle_parameters is None else dict(
        _mapping(evidence_bundle_parameters, "evidence_bundle_parameters")
    )
    if "incident" in parameters:
        raise ResearchEvidenceError("evidence_bundle_parameters 不得包含 incident")
    if incident_values and not parameters:
        raise ResearchEvidenceError("有 Incident 时必须提供 Evidence Bundle v2 组装参数")

    bundles = []
    for incident in incident_values:
        try:
            bundle = build_evidence_bundle_v2(incident, **deepcopy(parameters))
        except (EvidenceBundleError, TypeError) as error:
            raise ResearchEvidenceError("Evidence Bundle v2 组装失败：{}".format(error)) from error
        validate_reference_closure(bundle)
        if bundle["coverage_summary"]["admission_level"] != "raw_traceable":
            raise ResearchEvidenceError("研究型映射 Bundle 必须达到 raw_traceable")
        bundles.append(bundle)
    bundles.sort(key=lambda item: item["incident"]["incident_id"])

    incident_links = _incident_episode_links(
        incident_values, bundles, episode_value, sample_ids
    )
    route_by_id = _merge_bundle_records(bundles, "route_event_refs", "route_event_id")
    raw_by_id = _merge_bundle_records(bundles, "raw_record_refs", "raw_record_ref_id")
    if incident_values and (not route_by_id or not raw_by_id):
        raise ResearchEvidenceError("已映射研究必须具有 RouteEvent→raw MRT 完整链")

    sample_links = _normalize_sample_route_links(
        sample_route_event_links,
        sample_ids=sample_ids,
        route_ids=set(route_by_id),
        mapped=bool(incident_values),
    )

    bundle_refs = [
        {
            "bundle_id": bundle["bundle_id"],
            "incident_id": bundle["incident"]["incident_id"],
            "bundle_sha256": _sha256_bytes(canonical_evidence_bundle_bytes(bundle)),
        }
        for bundle in bundles
    ]
    legacy_refs = []
    for bundle in bundles:
        for fact in bundle["source_fact_mapping"]["source_facts"]:
            legacy_refs.append(
                {
                    "source_fact_id": fact["source_fact_id"],
                    "bundle_id": bundle["bundle_id"],
                    "incident_id": bundle["incident"]["incident_id"],
                    "table_name": fact["table_name"],
                    "fact_locator": fact["fact_locator"],
                    "record_hash": fact["record_hash"],
                    "source_fact_sha256": _record_sha256(fact),
                }
            )
    legacy_refs.sort(key=_canonical_json)

    episode_ref = {
        "episode_id": episode_value["episode_id"],
        "record_sha256": _record_sha256(episode_value),
        "wave_ids": sorted(episode_value["wave_ids"]),
        "supporting_sample_ids": sorted(episode_value["supporting_sample_ids"]),
    }
    wave_refs = [
        {
            "wave_id": wave["wave_id"],
            "record_sha256": _record_sha256(wave),
            "supporting_sample_ids": sorted(wave["supporting_sample_ids"]),
        }
        for wave in wave_values
    ]
    sample_refs = [
        {
            "sample_id": sample["sample_id"],
            "snapshot_id": sample["snapshot_id"],
            "continuity_state": sample["continuity_state"],
            "record_sha256": _record_sha256(sample),
        }
        for sample in sample_values
    ]

    route_refs = []
    bundle_ids_by_route: Dict[str, List[str]] = {}
    for bundle in bundles:
        for route in bundle["route_event_refs"]:
            bundle_ids_by_route.setdefault(route["route_event_id"], []).append(bundle["bundle_id"])
    for route_id in sorted(route_by_id):
        route = route_by_id[route_id]
        route_refs.append(
            {
                "route_event_id": route_id,
                "route_event_ref_sha256": _record_sha256(route),
                "observed_at": route["observed_at"],
                "collector_id": route["collector_id"],
                "vp_id": route["vp_id"],
                "vp_asn": route["vp_asn"],
                "semantics": route["semantics"],
                "raw_record_ref_ids": sorted(route["raw_record_ref_ids"]),
                "bundle_ids": sorted(set(bundle_ids_by_route[route_id])),
            }
        )

    raw_refs = []
    artifacts: Dict[str, Dict[str, Any]] = {}
    for raw_id in sorted(raw_by_id):
        raw = raw_by_id[raw_id]
        if raw["verification_status"] != "verified":
            raise ResearchEvidenceError("研究型 raw MRT 引用必须已验证")
        raw_refs.append(
            {
                "raw_record_ref_id": raw_id,
                "artifact_id": raw["artifact_id"],
                "file_sha256": raw["file_sha256"],
                "record_offset": raw["record_offset"],
                "record_length": raw["record_length"],
                "record_hash": raw["record_hash"],
                "record_ordinal": raw["record_ordinal"],
                "element_ordinal": raw["element_ordinal"],
                "verification_status": "verified",
            }
        )
        artifact = artifacts.setdefault(
            raw["artifact_id"],
            {
                "artifact_id": raw["artifact_id"],
                "file_sha256": raw["file_sha256"],
                "raw_record_ref_ids": [],
            },
        )
        if artifact["file_sha256"] != raw["file_sha256"]:
            raise ResearchEvidenceError("同一 artifact_id 对应不同 file_sha256")
        artifact["raw_record_ref_ids"].append(raw_id)
    artifact_refs = []
    for artifact_id in sorted(artifacts):
        artifact = artifacts[artifact_id]
        artifact["raw_record_ref_ids"] = sorted(artifact["raw_record_ref_ids"])
        artifact_refs.append(artifact)

    if not incident_values:
        mapping_state = "unmapped"
        mapping_reason = _chinese(
            mapping_missing_reason_zh or "没有可关联的 legacy Incident，未伪造事件身份。",
            "mapping_missing_reason_zh",
        )
    elif len(incident_values) == 1:
        mapping_state = "exact"
        mapping_reason = None
    else:
        mapping_state = "multiple"
        mapping_reason = None

    continuity_status = (
        "unknown"
        if any(sample["continuity_state"] == "unknown_after_gap" for sample in sample_values)
        else "continuous"
    )
    recovery = {
        "recovery_state": episode_value["recovery_state"],
        "partial_recovery_at": episode_value["partial_recovery_at"],
        "full_recovery_at": episode_value["full_recovery_at"],
        "duration": deepcopy(episode_value["duration"]),
        "continuity_status": continuity_status,
        "candidates": candidates,
    }

    supplied_limitations = [
        _chinese(value, "limitations_zh[]") for value in _sequence(limitations_zh, "limitations_zh")
    ]
    mandatory = [
        "RouteEvent 与 AS_PATH 仅表示 RRC25 路由观测，不能证明全网传播、物理机制、根因或意图。"
    ]
    if not incident_values:
        mandatory.append("研究 episode 未关联 legacy Incident，因此没有伪造 Evidence Bundle v2。")
    if continuity_status == "unknown":
        mandatory.append("输入存在连续性缺口，持续时间与恢复判断必须保留未知或区间语义。")
    limitations = sorted(set(supplied_limitations + mandatory))

    has_unknown_link = any(item["link_state"] == "unknown" for item in sample_links)
    if not incident_values:
        closure = {
            "incident_episode": "not_applicable_unmapped",
            "episode_wave_sample": "passed",
            "sample_route_event": "not_applicable_unmapped",
            "route_raw_artifact": "not_applicable_unmapped",
            "overall": "passed_with_explicit_unmapped",
            "unresolved_refs": [],
        }
    else:
        closure = {
            "incident_episode": "passed",
            "episode_wave_sample": "passed",
            "sample_route_event": "explicit_unknown" if has_unknown_link else "passed",
            "route_raw_artifact": "passed",
            "overall": "passed_with_explicit_unknown" if has_unknown_link else "passed",
            "unresolved_refs": [],
        }

    payload = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "run_id": run_id,
        "mapping": {
            "mapping_state": mapping_state,
            "incident_ids": sorted(incident_ids),
            "bundle_ids": sorted(bundle["bundle_id"] for bundle in bundles),
            "missing_reason_zh": mapping_reason,
        },
        "bundle_refs": bundle_refs,
        "legacy_source_fact_refs": legacy_refs,
        "incident_episode_links": incident_links,
        "episode_ref": episode_ref,
        "wave_refs": wave_refs,
        "sample_refs": sample_refs,
        "sample_route_event_links": sample_links,
        "route_event_refs": route_refs,
        "raw_record_refs": raw_refs,
        "artifact_refs": artifact_refs,
        "recovery_assessment": recovery,
        "reference_closure": closure,
        "limitations_zh": limitations,
        "conclusion": {
            "classification": "observation_only",
            "causal_conclusion": None,
        },
    }
    sidecar = {
        "schema_version": payload["schema_version"],
        "sidecar_id": _stable_id("research_sidecar_v1_", payload),
        **{key: value for key, value in payload.items() if key != "schema_version"},
    }
    validate_research_sidecar_reference_closure(sidecar, bundles=bundles)
    canonical_research_sidecar_bytes(sidecar)
    return {"bundles": bundles, "sidecar": sidecar}


__all__ = (
    "ResearchEvidenceError",
    "SIDECAR_SCHEMA_VERSION",
    "build_research_evidence_package",
    "canonical_research_sidecar_bytes",
    "validate_research_sidecar_reference_closure",
)
