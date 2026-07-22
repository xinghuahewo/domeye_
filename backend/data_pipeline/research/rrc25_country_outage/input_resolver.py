"""从已验证 MRT manifest 解析国家中断研究输入角色。

该模块不扫描目录、不读取 MRT、不写文件。调用方必须先使用现有 artifact
manifest 能力完成文件级哈希与压缩完整性验证，再把 manifest、验证摘要和
严格研究 Profile 注入本模块。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ...route_event.artifacts import ArtifactManifestError, artifact_id_v1


UTC = timezone.utc
SELECTION_SCHEMA_VERSION = "rrc25-country-outage-input-selection/v1"
SELECTION_ID_SCHEMA = "rrc25_country_outage_input_selection_id_v1"
UPDATE_INTERVAL = timedelta(minutes=5)
RIB_INTERVAL = timedelta(hours=8)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ResearchInputError(ValueError):
    """manifest、验证摘要或 Profile 不能形成确定研究输入。"""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ResearchInputError(f"{field} 必须是秒级 UTC Z 时间")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise ResearchInputError(f"{field} 不是合法 UTC 时间") from error
    return parsed


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slots(start: datetime, end: datetime, interval: timedelta) -> Tuple[str, ...]:
    values: List[str] = []
    current = start
    while current < end:
        values.append(_utc_text(current))
        current += interval
    return tuple(values)


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchInputError(f"{field} 必须是对象")
    return value


def _validate_parent(
    manifest: Mapping[str, Any], verification: Mapping[str, Any]
) -> Sequence[Mapping[str, Any]]:
    if manifest.get("schema_version") != 1 or manifest.get("manifest_kind") != "mrt_artifact_manifest":
        raise ResearchInputError("只接受 mrt_artifact_manifest v1")
    fingerprint = manifest.get("manifest_fingerprint_sha256")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ResearchInputError("artifact manifest 缺少稳定 fingerprint")
    if verification.get("verified") is not True:
        raise ResearchInputError("artifact manifest 尚未完整验证")
    if verification.get("manifest_fingerprint_sha256") != fingerprint:
        raise ResearchInputError("manifest 与 verification fingerprint 不一致")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or any(not isinstance(row, Mapping) for row in artifacts):
        raise ResearchInputError("artifact manifest.artifacts 必须是对象数组")
    if verification.get("artifact_count") != len(artifacts):
        raise ResearchInputError("verification artifact_count 不一致")
    return artifacts


def _normalize_artifact(row: Mapping[str, Any], collector: str) -> Dict[str, Any]:
    required = (
        "artifact_id",
        "artifact_type",
        "artifact_time_utc",
        "collector_id",
        "relative_path",
        "file_sha256",
        "size_bytes",
        "compression",
    )
    missing = [field for field in required if field not in row]
    if missing:
        raise ResearchInputError("artifact 缺少字段：" + ",".join(missing))
    if row["collector_id"] != collector:
        raise ResearchInputError("artifact collector 超出研究 Profile")
    if row["artifact_type"] not in {"update", "rib"}:
        raise ResearchInputError("artifact_type 只能是 update/rib")
    artifact_time = _utc(row["artifact_time_utc"], "artifact_time_utc")
    interval = UPDATE_INTERVAL if row["artifact_type"] == "update" else RIB_INTERVAL
    if (artifact_time - datetime(1970, 1, 1, tzinfo=UTC)) % interval:
        raise ResearchInputError("artifact_time_utc 未按制品类型槽位对齐")
    file_sha256 = row["file_sha256"]
    if not isinstance(file_sha256, str) or SHA256_RE.fullmatch(file_sha256) is None:
        raise ResearchInputError("artifact.file_sha256 必须是 64 位小写十六进制")
    try:
        expected_artifact_id = artifact_id_v1(file_sha256)
    except ArtifactManifestError as error:  # 防止下层异常越出研究合同。
        raise ResearchInputError("artifact.file_sha256 不能生成稳定身份") from error
    if row["artifact_id"] != expected_artifact_id:
        raise ResearchInputError("artifact_id 与 file_sha256 不一致")
    relative = row["relative_path"]
    if not isinstance(relative, str):
        raise ResearchInputError("artifact.relative_path 必须是字符串")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not pure.parts
        or ".." in pure.parts
        or pure.parts[0] != collector
    ):
        raise ResearchInputError("artifact.relative_path 越出 collector")
    if row["compression"] != "gz":
        raise ResearchInputError("RRC25 研究输入只接受 gzip 制品")
    if not isinstance(row["size_bytes"], int) or isinstance(row["size_bytes"], bool) or row["size_bytes"] <= 0:
        raise ResearchInputError("artifact.size_bytes 非法")
    return {field: row[field] for field in required}


def _failure(code: str, **details: Any) -> Dict[str, Any]:
    return {"code": code, "details": details}


def resolve_research_inputs(
    artifact_manifest: Mapping[str, Any],
    manifest_verification: Mapping[str, Any],
    research_profile: Mapping[str, Any],
) -> Dict[str, Any]:
    """生成确定性、角色化研究输入 selection。

    内容缺口返回 ``status=incomplete``；合同或身份错误抛出
    :class:`ResearchInputError`。这样调用方可以归档真实缺口，但不能把损坏
    manifest 当作普通缺槽继续处理。
    """

    manifest = _require_mapping(artifact_manifest, "artifact_manifest")
    verification = _require_mapping(manifest_verification, "manifest_verification")
    profile = _require_mapping(research_profile, "research_profile")
    artifacts = _validate_parent(manifest, verification)

    study_id = profile.get("study_id")
    collector = profile.get("collector_id")
    country = profile.get("country_code")
    if not all(isinstance(value, str) and value for value in (study_id, collector, country)):
        raise ResearchInputError("Profile 缺少 study_id/collector_id/country_code")
    window = _require_mapping(profile.get("window"), "profile.window")
    start = _utc(window.get("start_utc"), "window.start_utc")
    end = _utc(window.get("end_exclusive_utc"), "window.end_exclusive_utc")
    if start >= end or (end - start) % UPDATE_INTERVAL:
        raise ResearchInputError("研究窗口必须 start < end 且按五分钟对齐")
    if window.get("granularity_seconds") != 300:
        raise ResearchInputError("伊朗研究只接受五分钟粒度")

    normalized = [_normalize_artifact(row, collector) for row in artifacts]
    identities = [row["artifact_id"] for row in normalized]
    if len(set(identities)) != len(identities):
        raise ResearchInputError("artifact_id 不得重复")
    slot_identities = [
        (row["artifact_type"], row["artifact_time_utc"])
        for row in normalized
    ]
    if len(set(slot_identities)) != len(slot_identities):
        raise ResearchInputError("同一 artifact_type/time 槽不得重复")
    by_type = {
        kind: sorted(
            (row for row in normalized if row["artifact_type"] == kind),
            key=lambda row: (row["artifact_time_utc"], row["artifact_id"]),
        )
        for kind in ("update", "rib")
    }

    rib_before_or_at = [
        row for row in by_type["rib"] if _utc(row["artifact_time_utc"], "rib time") <= start
    ]
    rib_strictly_before = [
        row for row in by_type["rib"] if _utc(row["artifact_time_utc"], "rib time") < start
    ]
    state_seed = rib_before_or_at[-1] if rib_before_or_at else None
    baseline_reference = rib_strictly_before[-1] if rib_strictly_before else None
    analysis_updates = [
        row
        for row in by_type["update"]
        if start <= _utc(row["artifact_time_utc"], "update time") < end
    ]
    analysis_ribs = [
        row
        for row in by_type["rib"]
        if start <= _utc(row["artifact_time_utc"], "rib time") < end
    ]
    catch_up_updates: List[Dict[str, Any]] = []
    if state_seed is not None:
        seed_time = _utc(state_seed["artifact_time_utc"], "state seed time")
        if seed_time < start:
            catch_up_updates = [
                row
                for row in by_type["update"]
                if seed_time <= _utc(row["artifact_time_utc"], "catch-up time") < start
            ]

    expected_update_slots = _slots(start, end, UPDATE_INTERVAL)
    expected_rib_slots = _slots(start, end, RIB_INTERVAL)
    observed_updates = {row["artifact_time_utc"] for row in analysis_updates}
    observed_ribs = {row["artifact_time_utc"] for row in analysis_ribs}
    failures: List[Dict[str, Any]] = []
    if state_seed is None:
        failures.append(_failure("state_seed_rib_missing"))
    if baseline_reference is None:
        failures.append(_failure("baseline_reference_rib_missing"))
    missing_updates = sorted(set(expected_update_slots) - observed_updates)
    extra_updates = sorted(observed_updates - set(expected_update_slots))
    missing_ribs = sorted(set(expected_rib_slots) - observed_ribs)
    extra_ribs = sorted(observed_ribs - set(expected_rib_slots))
    if missing_updates:
        failures.append(_failure("analysis_update_slots_missing", slots=missing_updates))
    if extra_updates:
        failures.append(_failure("analysis_update_slots_unexpected", slots=extra_updates))
    if missing_ribs:
        failures.append(_failure("analysis_rib_slots_missing", slots=missing_ribs))
    if extra_ribs:
        failures.append(_failure("analysis_rib_slots_unexpected", slots=extra_ribs))
    if state_seed is not None and _utc(state_seed["artifact_time_utc"], "seed") < start:
        seed_time = _utc(state_seed["artifact_time_utc"], "seed")
        expected_catchup = set(_slots(seed_time, start, UPDATE_INTERVAL))
        observed_catchup = {row["artifact_time_utc"] for row in catch_up_updates}
        missing_catchup = sorted(expected_catchup - observed_catchup)
        if missing_catchup:
            failures.append(_failure("catch_up_update_slots_missing", slots=missing_catchup))

    roles = {
        "state_seed_rib": state_seed,
        "baseline_reference_rib": baseline_reference,
        "catch_up_updates": catch_up_updates,
        "analysis_updates": analysis_updates,
        "analysis_ribs": analysis_ribs,
    }
    unique_selected = {
        row["artifact_id"]: row
        for value in roles.values()
        for row in ([value] if isinstance(value, Mapping) else value or [])
        if row is not None
    }
    semantic = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "study_id": study_id,
        "collector_id": collector,
        "country_code": country,
        "window": {
            "start_utc": _utc_text(start),
            "end_exclusive_utc": _utc_text(end),
            "interval_semantics": "half_open",
            "granularity_seconds": 300,
        },
        "parent_manifest_fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
        "status": "complete" if not failures else "incomplete",
        "roles": roles,
        "coverage": {
            "analysis_updates": {
                "expected_count": len(expected_update_slots),
                "observed_count": len(analysis_updates),
                "missing_count": len(missing_updates),
            },
            "analysis_ribs": {
                "expected_count": len(expected_rib_slots),
                "observed_count": len(analysis_ribs),
                "missing_count": len(missing_ribs),
            },
            "baseline_reference_rib": {
                "expected_count": 1,
                "observed_count": 1 if baseline_reference is not None else 0,
            },
        },
        "selected_unique_artifact_count": len(unique_selected),
        "selected_unique_size_bytes": sum(row["size_bytes"] for row in unique_selected.values()),
        "failures": failures,
    }
    selection_id = "rsel_v1_" + hashlib.sha256(
        canonical_json({"schema": SELECTION_ID_SCHEMA, "selection": semantic}).encode("utf-8")
    ).hexdigest()[:32]
    return {
        "selection_id": selection_id,
        **semantic,
        "semantic_fingerprint_sha256": hashlib.sha256(
            canonical_json(semantic).encode("utf-8")
        ).hexdigest(),
    }


__all__ = [
    "ResearchInputError",
    "SELECTION_SCHEMA_VERSION",
    "canonical_json",
    "resolve_research_inputs",
]
