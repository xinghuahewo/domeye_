"""为 RRC25 国家中断研究构造显式、不可冒充全量的小样本 selection。

本模块只在已经由 :mod:`input_resolver` 生成的完整窗口 selection 上做纯数据
投影，不扫描目录、不读取 MRT、也不修改 resolver 或 bounded worker。调用方
必须显式给出 1..5 个 ``analysis_updates`` 制品身份；输出继续使用
``rrc25-country-outage-input-selection/v1`` 身份算法，但始终标为 ``incomplete``，
并用独立 failure 说明哪些缺口来自有意稀疏采样。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .input_resolver import (
    SELECTION_ID_SCHEMA,
    SELECTION_SCHEMA_VERSION,
    canonical_json,
)


UTC = timezone.utc
UPDATE_INTERVAL = timedelta(minutes=5)
RIB_INTERVAL = timedelta(hours=8)
MAXIMUM_ANALYSIS_UPDATES = 5
SPARSE_SAMPLING_FAILURE_CODE = "analysis_updates_sparse_sampling"


class PilotSamplingError(ValueError):
    """完整 selection 或显式小样本白名单不满足严格合同。"""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PilotSamplingError(f"{field} 必须是对象")
    return value


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PilotSamplingError(f"{field} 必须是秒级 UTC Z 时间")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise PilotSamplingError(f"{field} 不是合法 UTC 时间") from error
    return parsed


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slots(start: datetime, end: datetime, interval: timedelta) -> Tuple[str, ...]:
    values: List[str] = []
    cursor = start
    while cursor < end:
        values.append(_utc_text(cursor))
        cursor += interval
    return tuple(values)


def _failure(code: str, **details: Any) -> Dict[str, Any]:
    return {"code": code, "details": details}


def _verify_selection_identity(selection: Mapping[str, Any]) -> None:
    if selection.get("schema_version") != SELECTION_SCHEMA_VERSION:
        raise PilotSamplingError(
            "selection 必须是 rrc25-country-outage-input-selection/v1"
        )
    semantic = {
        key: value
        for key, value in selection.items()
        if key not in {"selection_id", "semantic_fingerprint_sha256"}
    }
    semantic_hash = hashlib.sha256(
        canonical_json(semantic).encode("utf-8")
    ).hexdigest()
    if selection.get("semantic_fingerprint_sha256") != semantic_hash:
        raise PilotSamplingError("selection semantic fingerprint 不一致")
    expected_id = "rsel_v1_" + hashlib.sha256(
        canonical_json(
            {"schema": SELECTION_ID_SCHEMA, "selection": semantic}
        ).encode("utf-8")
    ).hexdigest()[:32]
    if selection.get("selection_id") != expected_id:
        raise PilotSamplingError("selection_id 与冻结语义不一致")


def _artifact(
    value: Any,
    field: str,
    *,
    expected_type: str,
    collector_id: str,
) -> Mapping[str, Any]:
    row = _mapping(value, field)
    artifact_id = row.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise PilotSamplingError(f"{field}.artifact_id 非法")
    if row.get("artifact_type") != expected_type:
        raise PilotSamplingError(f"{field} 不是 {expected_type} 制品")
    if row.get("collector_id") != collector_id:
        raise PilotSamplingError(f"{field}.collector_id 与 selection 不一致")
    _utc(row.get("artifact_time_utc"), f"{field}.artifact_time_utc")
    size_bytes = row.get("size_bytes")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes <= 0
    ):
        raise PilotSamplingError(f"{field}.size_bytes 非法")
    return row


def _artifact_list(
    value: Any,
    field: str,
    *,
    expected_type: str,
    collector_id: str,
) -> List[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise PilotSamplingError(f"{field} 必须是数组")
    rows = [
        _artifact(
            row,
            f"{field}[{index}]",
            expected_type=expected_type,
            collector_id=collector_id,
        )
        for index, row in enumerate(value)
    ]
    identities = [row["artifact_id"] for row in rows]
    if len(set(identities)) != len(identities):
        raise PilotSamplingError(f"{field} artifact_id 不得重复")
    slots = [row["artifact_time_utc"] for row in rows]
    if len(set(slots)) != len(slots):
        raise PilotSamplingError(f"{field} 时间槽不得重复")
    expected_order = sorted(
        rows,
        key=lambda row: (row["artifact_time_utc"], row["artifact_id"]),
    )
    if [row["artifact_id"] for row in rows] != [
        row["artifact_id"] for row in expected_order
    ]:
        raise PilotSamplingError(f"{field} 必须按时间和 artifact_id 排序")
    return rows


def _selected_artifacts(roles: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    for role in (
        "state_seed_rib",
        "baseline_reference_rib",
        "catch_up_updates",
        "analysis_updates",
        "analysis_ribs",
    ):
        value = roles[role]
        rows: Iterable[Mapping[str, Any]]
        if value is None:
            rows = ()
        elif isinstance(value, list):
            rows = value
        else:
            rows = (value,)
        for row in rows:
            artifact_id = row["artifact_id"]
            previous = result.get(artifact_id)
            if previous is not None and canonical_json(previous) != canonical_json(row):
                raise PilotSamplingError(
                    f"同一 artifact_id 在不同角色中的内容不一致：{artifact_id}"
                )
            result[artifact_id] = row
    return result


def _source_semantics(
    roles: Mapping[str, Any],
    *,
    start: datetime,
    end: datetime,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    expected_update_slots = _slots(start, end, UPDATE_INTERVAL)
    expected_rib_slots = _slots(start, end, RIB_INTERVAL)
    observed_updates = {
        row["artifact_time_utc"] for row in roles["analysis_updates"]
    }
    observed_ribs = {row["artifact_time_utc"] for row in roles["analysis_ribs"]}
    missing_updates = sorted(set(expected_update_slots) - observed_updates)
    extra_updates = sorted(observed_updates - set(expected_update_slots))
    missing_ribs = sorted(set(expected_rib_slots) - observed_ribs)
    extra_ribs = sorted(observed_ribs - set(expected_rib_slots))

    failures: List[Dict[str, Any]] = []
    if roles["state_seed_rib"] is None:
        failures.append(_failure("state_seed_rib_missing"))
    if roles["baseline_reference_rib"] is None:
        failures.append(_failure("baseline_reference_rib_missing"))
    if missing_updates:
        failures.append(_failure("analysis_update_slots_missing", slots=missing_updates))
    if extra_updates:
        failures.append(_failure("analysis_update_slots_unexpected", slots=extra_updates))
    if missing_ribs:
        failures.append(_failure("analysis_rib_slots_missing", slots=missing_ribs))
    if extra_ribs:
        failures.append(_failure("analysis_rib_slots_unexpected", slots=extra_ribs))

    seed = roles["state_seed_rib"]
    if seed is not None:
        seed_time = _utc(seed["artifact_time_utc"], "state_seed_rib time")
        if seed_time < start:
            expected_catch_up = set(_slots(seed_time, start, UPDATE_INTERVAL))
            observed_catch_up = {
                row["artifact_time_utc"] for row in roles["catch_up_updates"]
            }
            missing_catch_up = sorted(expected_catch_up - observed_catch_up)
            if missing_catch_up:
                failures.append(
                    _failure("catch_up_update_slots_missing", slots=missing_catch_up)
                )

    coverage = {
        "analysis_updates": {
            "expected_count": len(expected_update_slots),
            "observed_count": len(roles["analysis_updates"]),
            "missing_count": len(missing_updates),
        },
        "analysis_ribs": {
            "expected_count": len(expected_rib_slots),
            "observed_count": len(roles["analysis_ribs"]),
            "missing_count": len(missing_ribs),
        },
        "baseline_reference_rib": {
            "expected_count": 1,
            "observed_count": 1
            if roles["baseline_reference_rib"] is not None
            else 0,
        },
    }
    return coverage, failures


def _validated_full_selection(
    selection: Mapping[str, Any],
) -> Tuple[Mapping[str, Any], datetime, datetime]:
    _verify_selection_identity(selection)
    collector_id = selection.get("collector_id")
    if not isinstance(collector_id, str) or not collector_id:
        raise PilotSamplingError("selection.collector_id 非法")
    window = _mapping(selection.get("window"), "selection.window")
    if (
        window.get("interval_semantics") != "half_open"
        or window.get("granularity_seconds") != 300
    ):
        raise PilotSamplingError("selection.window 必须是五分钟半开窗口")
    start = _utc(window.get("start_utc"), "selection.window.start_utc")
    end = _utc(
        window.get("end_exclusive_utc"),
        "selection.window.end_exclusive_utc",
    )
    if start >= end or (end - start) % UPDATE_INTERVAL:
        raise PilotSamplingError("selection.window 必须是按五分钟对齐的非空窗口")

    source_roles = _mapping(selection.get("roles"), "selection.roles")
    required_roles = {
        "state_seed_rib",
        "baseline_reference_rib",
        "catch_up_updates",
        "analysis_updates",
        "analysis_ribs",
    }
    if set(source_roles) != required_roles:
        raise PilotSamplingError("selection.roles 必须精确包含五个输入角色")

    roles: Dict[str, Any] = {}
    for role in ("state_seed_rib", "baseline_reference_rib"):
        value = source_roles[role]
        roles[role] = (
            None
            if value is None
            else _artifact(
                value,
                f"selection.roles.{role}",
                expected_type="rib",
                collector_id=collector_id,
            )
        )
    roles["catch_up_updates"] = _artifact_list(
        source_roles["catch_up_updates"],
        "selection.roles.catch_up_updates",
        expected_type="update",
        collector_id=collector_id,
    )
    roles["analysis_updates"] = _artifact_list(
        source_roles["analysis_updates"],
        "selection.roles.analysis_updates",
        expected_type="update",
        collector_id=collector_id,
    )
    roles["analysis_ribs"] = _artifact_list(
        source_roles["analysis_ribs"],
        "selection.roles.analysis_ribs",
        expected_type="rib",
        collector_id=collector_id,
    )

    seed = roles["state_seed_rib"]
    baseline = roles["baseline_reference_rib"]
    if seed is not None and _utc(seed["artifact_time_utc"], "seed time") > start:
        raise PilotSamplingError("state_seed_rib 必须位于窗口起点或之前")
    if baseline is not None and _utc(
        baseline["artifact_time_utc"], "baseline time"
    ) >= start:
        raise PilotSamplingError("baseline_reference_rib 必须严格早于窗口起点")
    for row in roles["analysis_updates"] + roles["analysis_ribs"]:
        artifact_time = _utc(row["artifact_time_utc"], "analysis artifact time")
        if not start <= artifact_time < end:
            raise PilotSamplingError("analysis 制品越出完整研究窗口")
    if seed is None and roles["catch_up_updates"]:
        raise PilotSamplingError("缺少 state_seed_rib 时不得出现 catch_up_updates")
    if seed is not None:
        seed_time = _utc(seed["artifact_time_utc"], "seed time")
        for row in roles["catch_up_updates"]:
            artifact_time = _utc(row["artifact_time_utc"], "catch-up time")
            if not seed_time <= artifact_time < start:
                raise PilotSamplingError("catch_up_updates 越出 seed 到窗口起点范围")

    selected = _selected_artifacts(roles)
    expected_coverage, expected_failures = _source_semantics(
        roles,
        start=start,
        end=end,
    )
    if canonical_json(selection.get("coverage")) != canonical_json(expected_coverage):
        raise PilotSamplingError("selection.coverage 与完整角色不一致")
    if canonical_json(selection.get("failures")) != canonical_json(expected_failures):
        raise PilotSamplingError("selection.failures 与完整角色不一致")
    expected_status = "complete" if not expected_failures else "incomplete"
    if selection.get("status") != expected_status:
        raise PilotSamplingError("selection.status 与完整角色不一致")
    if selection.get("selected_unique_artifact_count") != len(selected):
        raise PilotSamplingError("selection.selected_unique_artifact_count 不一致")
    selected_size = sum(row["size_bytes"] for row in selected.values())
    if selection.get("selected_unique_size_bytes") != selected_size:
        raise PilotSamplingError("selection.selected_unique_size_bytes 不一致")
    return roles, start, end


def build_sparse_pilot_selection(
    full_selection: Mapping[str, Any],
    analysis_update_artifact_ids: Sequence[str],
) -> Dict[str, Any]:
    """返回绑定完整窗口、但只读取显式 UPDATE 白名单的小样本 selection。

    白名单次序不影响结果；输出按 ``artifact_time_utc``、``artifact_id`` 排序。
    即使白名单碰巧覆盖输入里的全部 UPDATE，输出也会保留稀疏研究 failure，
    防止调用方把小样本运行误报成完整事件验收。
    """

    selection = _mapping(full_selection, "full_selection")
    roles, start, end = _validated_full_selection(selection)
    if isinstance(analysis_update_artifact_ids, (str, bytes)) or not isinstance(
        analysis_update_artifact_ids, Sequence
    ):
        raise PilotSamplingError("analysis_update_artifact_ids 必须是 1..5 个字符串")
    requested = list(analysis_update_artifact_ids)
    if not 1 <= len(requested) <= MAXIMUM_ANALYSIS_UPDATES:
        raise PilotSamplingError("analysis_update_artifact_ids 数量必须是 1..5")
    if any(not isinstance(value, str) or not value for value in requested):
        raise PilotSamplingError("analysis_update_artifact_ids 必须是非空字符串")
    if len(set(requested)) != len(requested):
        raise PilotSamplingError("analysis_update_artifact_ids 不得重复")

    analysis_by_id = {
        row["artifact_id"]: row for row in roles["analysis_updates"]
    }
    all_selected = _selected_artifacts(roles)
    for artifact_id in requested:
        if artifact_id in analysis_by_id:
            continue
        if artifact_id in all_selected:
            raise PilotSamplingError(
                f"artifact_id 不是 analysis UPDATE：{artifact_id}"
            )
        raise PilotSamplingError(f"未知 artifact_id：{artifact_id}")

    sampled_updates = sorted(
        (analysis_by_id[artifact_id] for artifact_id in requested),
        key=lambda row: (row["artifact_time_utc"], row["artifact_id"]),
    )
    sampled_roles = {
        "state_seed_rib": deepcopy(roles["state_seed_rib"]),
        "baseline_reference_rib": deepcopy(roles["baseline_reference_rib"]),
        "catch_up_updates": deepcopy(roles["catch_up_updates"]),
        "analysis_updates": deepcopy(sampled_updates),
        "analysis_ribs": deepcopy(roles["analysis_ribs"]),
    }

    source_coverage, source_failures = _source_semantics(
        roles,
        start=start,
        end=end,
    )
    expected_update_count = source_coverage["analysis_updates"]["expected_count"]
    source_observed_count = source_coverage["analysis_updates"]["observed_count"]
    sampled_count = len(sampled_updates)
    sparse_failure = _failure(
        SPARSE_SAMPLING_FAILURE_CODE,
        sampling_policy="explicit_analysis_update_artifact_id_allowlist",
        source_selection_id=selection["selection_id"],
        source_semantic_fingerprint_sha256=selection[
            "semantic_fingerprint_sha256"
        ],
        source_expected_count=expected_update_count,
        source_observed_count=source_observed_count,
        source_missing_count=expected_update_count - source_observed_count,
        selected_count=sampled_count,
        sampled_out_count=source_observed_count - sampled_count,
        coverage_missing_count=expected_update_count - sampled_count,
        selected_artifact_ids=[row["artifact_id"] for row in sampled_updates],
        selected_slots=[row["artifact_time_utc"] for row in sampled_updates],
    )
    coverage = deepcopy(source_coverage)
    coverage["analysis_updates"] = {
        "expected_count": expected_update_count,
        "observed_count": sampled_count,
        "missing_count": expected_update_count - sampled_count,
    }
    failures = [deepcopy(row) for row in source_failures] + [sparse_failure]
    unique_selected = _selected_artifacts(sampled_roles)

    semantic = deepcopy(
        {
            key: value
            for key, value in selection.items()
            if key not in {"selection_id", "semantic_fingerprint_sha256"}
        }
    )
    semantic.update(
        {
            "status": "incomplete",
            "roles": sampled_roles,
            "coverage": coverage,
            "selected_unique_artifact_count": len(unique_selected),
            "selected_unique_size_bytes": sum(
                row["size_bytes"] for row in unique_selected.values()
            ),
            "failures": failures,
        }
    )
    semantic_fingerprint = hashlib.sha256(
        canonical_json(semantic).encode("utf-8")
    ).hexdigest()
    selection_id = "rsel_v1_" + hashlib.sha256(
        canonical_json(
            {"schema": SELECTION_ID_SCHEMA, "selection": semantic}
        ).encode("utf-8")
    ).hexdigest()[:32]
    return {
        "selection_id": selection_id,
        **semantic,
        "semantic_fingerprint_sha256": semantic_fingerprint,
    }


__all__ = [
    "MAXIMUM_ANALYSIS_UPDATES",
    "PilotSamplingError",
    "SPARSE_SAMPLING_FAILURE_CODE",
    "build_sparse_pilot_selection",
]
