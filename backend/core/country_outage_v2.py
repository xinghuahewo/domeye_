"""旧实时检测器使用的国家中断结构化 Observation/Incident 状态机。

旧 Core 目前只有国家 ASN 可见/中断集合，没有 Prefix×VP 人口。因此本模块
会诚实写入 ``prefix_vp.measurement_state=unavailable``，允许确认 onset/peak，
但不会把单槽 ASN 恢复伪装成 full recovery。真实状态重放使用研究管线的完整
Observation v2，并可补齐恢复里程碑。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping, Optional


LIVE_OBSERVATION_SCHEMA_VERSION = "country-outage-live-observation/v2"
LIVE_STATE_SCHEMA_VERSION = "country-outage-live-state/v2"
INCIDENT_SCHEMA_VERSION = "country-outage-incident/v2"
EPISODE_SCHEMA_VERSION = "country-outage-episode/v2"
MILESTONE_SCHEMA_VERSION = "country-outage-milestone/v2"
ALGORITHM_VERSION = "country_outage_live_event_model_v2"
BEIJING = timezone(timedelta(hours=8))


class CountryOutageV2Error(ValueError):
    """实时 Observation 或事件状态不闭合。"""


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
        raise CountryOutageV2Error("结构化事件不能规范序列化") from error


def _stable_id(prefix: str, value: Mapping[str, Any]) -> str:
    return prefix + hashlib.sha256(
        _canonical_json(dict(value)).encode("utf-8")
    ).hexdigest()[:24]


def _asns(values: Iterable[Any], field_name: str) -> list[int]:
    if isinstance(values, (str, bytes, Mapping)):
        raise CountryOutageV2Error(f"{field_name} 必须是 ASN 集合")
    result = []
    for value in values:
        if isinstance(value, bool):
            raise CountryOutageV2Error(f"{field_name} 包含非法 ASN")
        try:
            asn = int(value)
        except (TypeError, ValueError) as error:
            raise CountryOutageV2Error(f"{field_name} 包含非法 ASN") from error
        if not 1 <= asn <= 4_294_967_295:
            raise CountryOutageV2Error(f"{field_name} ASN 越界")
        result.append(asn)
    return sorted(set(result))


def _local_to_utc(value: object) -> str:
    if not isinstance(value, str):
        raise CountryOutageV2Error("observed_at_local 必须是字符串")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=BEIJING
        )
    except ValueError as error:
        raise CountryOutageV2Error(
            "observed_at_local 必须为 YYYY-MM-DD HH:MM:SS"
        ) from error
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_live_observation(
    *,
    source: str,
    country_code: str,
    observed_at_local: str,
    outage_asns: Iterable[Any],
    normal_asns: Iterable[Any],
    baseline_asns: Optional[Iterable[Any]] = None,
    collector_id: str = "legacy_live",
) -> dict[str, Any]:
    """从旧 Core 的同周期 ASN 集合生成不可变结构化 Observation。"""

    outages = _asns(outage_asns, "outage_asns")
    normals = _asns(normal_asns, "normal_asns")
    if set(outages) & set(normals):
        raise CountryOutageV2Error("outage_asns 与 normal_asns 不得重叠")
    baseline = (
        sorted(set(outages) | set(normals))
        if baseline_asns is None
        else _asns(baseline_asns, "baseline_asns")
    )
    if not baseline:
        raise CountryOutageV2Error("baseline_asns 不能为空")
    baseline_set = set(baseline)
    affected = sorted(baseline_set & set(outages))
    visible = sorted(baseline_set & set(normals))
    unknown = sorted(baseline_set - set(affected) - set(visible))
    dynamic = sorted((set(outages) | set(normals)) - baseline_set)
    observed_at = _local_to_utc(observed_at_local)
    cohort_id = _stable_id(
        "cohort_live_v2_",
        {
            "source": source,
            "country_code": country_code,
            "collector_id": collector_id,
            "baseline_asns": baseline,
        },
    )
    snapshot_id = _stable_id(
        "snapshot_live_v2_",
        {
            "cohort_id": cohort_id,
            "observed_at": observed_at,
            "affected_asns": affected,
            "visible_asns": visible,
            "unknown_asns": unknown,
        },
    )
    complete = not unknown
    return {
        "schema_version": LIVE_OBSERVATION_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "source": source,
        "country_code": country_code,
        "collector_id": collector_id,
        "observed_at": observed_at,
        "observed_at_local": observed_at_local,
        "continuity_state": (
            "continuous_asn_only" if complete else "unknown_population"
        ),
        "cohort": {
            "cohort_id": cohort_id,
            "baseline_asns": baseline,
            "baseline_asn_count": len(baseline),
            "dynamic_asns": dynamic,
            "denominator_policy": "fixed_at_runtime_stream_initialization",
        },
        "asn_state": {
            "affected_asns": affected,
            "visible_asns": visible,
            "unknown_asns": unknown,
            "affected_asn_count": len(affected) if complete else None,
            "visible_asn_count": len(visible) if complete else None,
            "affected_asn_ratio": (
                len(affected) / len(baseline) if complete else None
            ),
            "visible_asn_ratio": (
                len(visible) / len(baseline) if complete else None
            ),
        },
        "prefix_vp": {
            "measurement_state": "unavailable",
            "baseline_count": None,
            "visible_count": None,
            "visible_ratio": None,
            "unknown_reason": (
                "legacy Core 未保留 collector+VP+AFI/SAFI+prefix 状态人口"
            ),
        },
        "state_result_ref": None,
        "algorithm_version": ALGORITHM_VERSION,
    }


def new_runtime_state(
    *,
    source: str,
    country_code: str,
    collector_id: str,
    baseline_asns: Iterable[Any],
) -> dict[str, Any]:
    baseline = _asns(baseline_asns, "baseline_asns")
    if not baseline:
        raise CountryOutageV2Error("runtime baseline 不能为空")
    return {
        "schema_version": LIVE_STATE_SCHEMA_VERSION,
        "source": source,
        "country_code": country_code,
        "collector_id": collector_id,
        "baseline_asns": baseline,
        "candidate_observations": [],
        "incident": None,
        "episode": None,
        "peak_observation": None,
        "recent_observations": [],
    }


def _milestone(
    observation: Mapping[str, Any],
    *,
    metric: str,
    metric_value: Any,
    algorithm_version: str,
) -> dict[str, Any]:
    return {
        "schema_version": MILESTONE_SCHEMA_VERSION,
        "at": observation["observed_at"],
        "snapshot_id": observation["snapshot_id"],
        "algorithm_version": algorithm_version,
        "metric": metric,
        "metric_value": metric_value,
        "time_precision": "five_minute_runtime_observation",
        "unknown_reason": None,
    }


def reduce_live_observation(
    state: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    damaged_ratio_threshold: float = 0.03,
    detection_confirm_slots: int = 2,
    recovery_confirm_slots: int = 6,
) -> dict[str, Any]:
    """消费一条 Observation；返回新状态、需持久化观察和生命周期动作。"""

    if state.get("schema_version") != LIVE_STATE_SCHEMA_VERSION:
        raise CountryOutageV2Error("runtime state schema 非法")
    if observation.get("schema_version") != LIVE_OBSERVATION_SCHEMA_VERSION:
        raise CountryOutageV2Error("live observation schema 非法")
    current = deepcopy(dict(state))
    if (
        observation.get("source") != current["source"]
        or observation.get("country_code") != current["country_code"]
        or observation.get("collector_id") != current["collector_id"]
        or observation.get("cohort", {}).get("baseline_asns")
        != current["baseline_asns"]
    ):
        raise CountryOutageV2Error("Observation 与 runtime stream 身份不一致")
    ratio = observation["asn_state"]["affected_asn_ratio"]
    anomalous = ratio is not None and float(ratio) > damaged_ratio_threshold
    incident = current.get("incident")
    persist_observations: list[dict[str, Any]] = []
    lifecycle_action = "none"

    if incident is None:
        candidates = list(current["candidate_observations"])
        candidates = (candidates + [deepcopy(dict(observation))])[
            -detection_confirm_slots:
        ]
        if not anomalous:
            candidates = []
        current["candidate_observations"] = candidates
        if (
            len(candidates) == detection_confirm_slots
            and all(
                row["asn_state"]["affected_asn_ratio"] is not None
                and row["asn_state"]["affected_asn_ratio"]
                > damaged_ratio_threshold
                for row in candidates
            )
        ):
            onset_observation = candidates[0]
            detected_at = observation["observed_at"]
            incident_id = _stable_id(
                "incident_v2_",
                {
                    "source": current["source"],
                    "country_code": current["country_code"],
                    "collector_id": current["collector_id"],
                    "detected_at": detected_at,
                },
            )
            episode_id = _stable_id(
                "episode_v2_",
                {
                    "incident_id": incident_id,
                    "ordinal": 1,
                    "onset_snapshot_id": onset_observation["snapshot_id"],
                },
            )
            onset = _milestone(
                onset_observation,
                metric="affected_asn_ratio",
                metric_value=onset_observation["asn_state"][
                    "affected_asn_ratio"
                ],
                algorithm_version="country_outage_live_onset_v2",
            )
            peak = _milestone(
                observation,
                metric="affected_asn_ratio",
                metric_value=ratio,
                algorithm_version="country_outage_live_peak_v2",
            )
            incident = {
                "schema_version": INCIDENT_SCHEMA_VERSION,
                "incident_id": incident_id,
                "source": current["source"],
                "country_code": current["country_code"],
                "collector_id": current["collector_id"],
                "detected_at": detected_at,
                "onset_at": onset["at"],
                "peak_at": peak["at"],
                "trough_at": None,
                "partial_recovery_at": None,
                "full_recovery_at": None,
                "observation_end_at": observation["observed_at"],
                "duration_state": "lower_bound",
                "recovery_state": "unknown",
                "cohort_id": observation["cohort"]["cohort_id"],
                "peak_snapshot_id": peak["snapshot_id"],
                "trough_snapshot_id": None,
                "algorithm_version": ALGORITHM_VERSION,
                "legacy_ref": None,
                "episode_ids": [episode_id],
                "milestones": {
                    "onset": onset,
                    "peak": peak,
                    "trough": None,
                    "partial_recovery": None,
                    "full_recovery": None,
                },
            }
            episode = {
                "schema_version": EPISODE_SCHEMA_VERSION,
                "episode_id": episode_id,
                "incident_id": incident_id,
                "ordinal": 1,
                "source": current["source"],
                "country_code": current["country_code"],
                "collector_id": current["collector_id"],
                "cohort_id": observation["cohort"]["cohort_id"],
                "detected_at": detected_at,
                "onset_at": onset["at"],
                "peak_at": peak["at"],
                "trough_at": None,
                "partial_recovery_at": None,
                "full_recovery_at": None,
                "observation_end_at": observation["observed_at"],
                "duration_state": "lower_bound",
                "recovery_state": "unknown",
                "peak_snapshot_id": peak["snapshot_id"],
                "trough_snapshot_id": None,
                "algorithm_version": ALGORITHM_VERSION,
                "milestones": deepcopy(incident["milestones"]),
                "wave_ids": [],
                "legacy_ref": None,
            }
            current["incident"] = incident
            current["episode"] = episode
            current["peak_observation"] = deepcopy(dict(observation))
            current["candidate_observations"] = []
            persist_observations = candidates
            lifecycle_action = "started"
    else:
        episode = current["episode"]
        peak_observation = current["peak_observation"]
        peak_ratio = peak_observation["asn_state"]["affected_asn_ratio"]
        if ratio is not None and (
            peak_ratio is None or float(ratio) > float(peak_ratio)
        ):
            peak = _milestone(
                observation,
                metric="affected_asn_ratio",
                metric_value=ratio,
                algorithm_version="country_outage_live_peak_v2",
            )
            incident["peak_at"] = peak["at"]
            incident["peak_snapshot_id"] = peak["snapshot_id"]
            incident["milestones"]["peak"] = peak
            episode["peak_at"] = peak["at"]
            episode["peak_snapshot_id"] = peak["snapshot_id"]
            episode["milestones"]["peak"] = deepcopy(peak)
            current["peak_observation"] = deepcopy(dict(observation))
            lifecycle_action = "peak_updated"
        incident["observation_end_at"] = observation["observed_at"]
        episode["observation_end_at"] = observation["observed_at"]
        recent = (list(current["recent_observations"]) + [dict(observation)])[
            -recovery_confirm_slots:
        ]
        current["recent_observations"] = recent
        # 两个指标都必须已观测且连续 6 槽满足恢复，旧 Core 的 Prefix×VP
        # unavailable 因而不会进入此分支，避免过去的单槽 98% 直接闭合。
        full_recovery = (
            len(recent) == recovery_confirm_slots
            and all(
                row["asn_state"]["visible_asn_ratio"] is not None
                and row["asn_state"]["visible_asn_ratio"] >= 0.99
                and row["prefix_vp"]["measurement_state"] == "observed"
                and row["prefix_vp"]["visible_ratio"] is not None
                and row["prefix_vp"]["visible_ratio"] >= 0.99
                for row in recent
            )
        )
        if full_recovery:
            recovered = _milestone(
                recent[0],
                metric="asn_and_prefix_vp_recovery",
                metric_value={
                    "visible_asn_ratio": recent[0]["asn_state"][
                        "visible_asn_ratio"
                    ],
                    "visible_prefix_vp_ratio": recent[0]["prefix_vp"][
                        "visible_ratio"
                    ],
                },
                algorithm_version="country_outage_live_full_recovery_v2",
            )
            for value in (incident, episode):
                value["full_recovery_at"] = recovered["at"]
                value["duration_state"] = "exact"
                value["recovery_state"] = "fully_recovered"
                value["milestones"]["full_recovery"] = deepcopy(recovered)
            lifecycle_action = "fully_recovered"
        current["incident"] = incident
        current["episode"] = episode
        persist_observations = [deepcopy(dict(observation))]
    return {
        "state": current,
        "persist_observations": persist_observations,
        "lifecycle_action": lifecycle_action,
    }


def legacy_peak_projection(
    *,
    incident: Mapping[str, Any],
    peak_observation: Mapping[str, Any],
    country_chinese_name: str,
    outage_level: str,
    outage_level_descr: str,
    outage_id: int,
) -> dict[str, Any]:
    """从同一个 peak snapshot 生成旧字段兼容投影。"""

    snapshot_id = incident.get("peak_snapshot_id")
    if (
        peak_observation.get("snapshot_id") != snapshot_id
        or peak_observation.get("cohort", {}).get("cohort_id")
        != incident.get("cohort_id")
    ):
        raise CountryOutageV2Error("peak Observation 与 Incident 不一致")
    affected = list(peak_observation["asn_state"]["affected_asns"])
    count = peak_observation["asn_state"]["affected_asn_count"]
    total = peak_observation["cohort"]["baseline_asn_count"]
    ratio = peak_observation["asn_state"]["affected_asn_ratio"]
    if (
        count != len(affected)
        or ratio is None
        or abs(float(ratio) - count / total) > 1e-15
    ):
        raise CountryOutageV2Error("旧投影分子分母没有绑定 peak snapshot")
    event_info = (
        f"北京时间 {peak_observation['observed_at_local']}，"
        f"{country_chinese_name} 观测到国家路由状态异常；"
        f"peak snapshot={snapshot_id}，受影响 AS {count}/{total}"
        f"（{ratio:.2%}）。Prefix×VP 状态暂不可用，恢复状态为 unknown。"
    )
    return {
        "s_time": peak_observation["observed_at_local"]
        if incident.get("detected_at") is None
        else datetime.fromisoformat(
            str(incident["detected_at"]).replace("Z", "+00:00")
        )
        .astimezone(BEIJING)
        .strftime("%Y-%m-%d %H:%M:%S"),
        "e_time": (
            None
            if incident.get("recovery_state") != "fully_recovered"
            else datetime.fromisoformat(
                str(incident["full_recovery_at"]).replace("Z", "+00:00")
            )
            .astimezone(BEIJING)
            .strftime("%Y-%m-%d %H:%M:%S")
        ),
        "duration": None,
        "country_chinese_name": country_chinese_name,
        "total_as_num": total,
        "max_outage_as_num": count,
        "max_outage_as_ratio": ratio,
        "outage_level": outage_level,
        "outage_level_descr": outage_level_descr,
        "outage_ases": affected,
        "event_info": event_info,
        "outage_id": outage_id,
        "peak_snapshot_id": snapshot_id,
        "structured_incident": deepcopy(dict(incident)),
    }


__all__ = (
    "ALGORITHM_VERSION",
    "CountryOutageV2Error",
    "LIVE_OBSERVATION_SCHEMA_VERSION",
    "LIVE_STATE_SCHEMA_VERSION",
    "build_live_observation",
    "legacy_peak_projection",
    "new_runtime_state",
    "reduce_live_observation",
)
