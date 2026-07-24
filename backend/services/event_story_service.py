"""伊朗国家中断研究结果的事件叙事适配。

本模块只读取独立 Go 重放引擎生成的不可变交付包，不触发重放、不连接旧项目，
也不把 RRC25 控制面观测外推为数据面或实际服务中断。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from functools import lru_cache
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


IRAN_LEGACY_REF = "country_outage/2026-02-27 09:12:32/IR/1/r"
DEFAULT_REPLAY_DIRECTORY = Path(
    "/home/bgpdata/Domeye-Core-dev-data/research-runs/"
    "iran-rrc25-full-p0/20260723T094940Z-full-p0/"
    "state-replay-1805-2300-go-v1"
)
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
CONSUMED_DELIVERABLES = (
    "QUALITY.json",
    "asn-states.jsonl.gz",
    "cohort.json",
    "country-snapshots.jsonl.gz",
    "episodes.json",
    "incident.json",
    "input-summary.json",
    "waves.json",
)
WINDOW_START_VANTAGE_POINT_COUNT = 96


class EventStoryUnavailable(RuntimeError):
    """研究交付包不存在、损坏或尚未通过质量门。"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EventStoryUnavailable(f"无法读取研究交付文件：{path.name}") from error
    if not isinstance(value, dict):
        raise EventStoryUnavailable(f"研究交付文件不是 JSON 对象：{path.name}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise EventStoryUnavailable(f"无法校验研究交付文件：{path.name}") from error
    return digest.hexdigest()


def _local_time(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(BUSINESS_TIMEZONE).isoformat(timespec="seconds")


def _jsonl_gzip(path: Path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for ordinal, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise EventStoryUnavailable(
                        f"{path.name} 第 {ordinal} 行不是 JSON 对象"
                    )
                yield value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EventStoryUnavailable(f"无法读取研究状态文件：{path.name}") from error


def _snapshot_view(row: Mapping[str, Any]) -> dict[str, Any]:
    classifications = row.get("dual_stack_classifications")
    if not isinstance(classifications, Mapping):
        classifications = {}
    ipv4 = row.get("ipv4")
    ipv4 = ipv4 if isinstance(ipv4, Mapping) else {}
    ipv6 = row.get("ipv6")
    ipv6 = ipv6 if isinstance(ipv6, Mapping) else {}
    updates = row.get("update_counts")
    updates = updates if isinstance(updates, Mapping) else {}

    def ratio(visible_key: str, baseline_key: str, source: Mapping[str, Any]):
        visible = source.get(visible_key)
        baseline = source.get(baseline_key)
        if not isinstance(visible, int) or not isinstance(baseline, int) or baseline <= 0:
            return None
        return visible / baseline

    return {
        "snapshot_id": row.get("snapshot_id"),
        "observed_at_utc": row.get("observed_at"),
        "observed_at_local": _local_time(row.get("observed_at")),
        "affected_asn_count": row.get("affected_asn_count"),
        "affected_asn_ratio": row.get("affected_asn_ratio"),
        "fully_invisible_asn_count": len(
            classifications.get("fully_invisible", [])
        ),
        "partially_visible_asn_count": len(
            classifications.get("partially_visible", [])
        ),
        "visible_origin_asn_count": row.get("visible_origin_asn_count"),
        "visible_origin_asn_ratio": row.get("visible_origin_asn_ratio"),
        "visible_prefix_vp_count": row.get("visible_prefix_vp_count"),
        "visible_prefix_vp_ratio": row.get("visible_prefix_vp_ratio"),
        "ipv4_visible_prefix_vp_count": ipv4.get("visible_prefix_vp_count"),
        "ipv4_baseline_prefix_vp_count": ipv4.get("baseline_prefix_vp_count"),
        "ipv4_visible_prefix_vp_ratio": ratio(
            "visible_prefix_vp_count", "baseline_prefix_vp_count", ipv4
        ),
        "ipv4_visible_origin_asn_count": ipv4.get("visible_origin_asn_count"),
        "ipv4_baseline_origin_asn_count": ipv4.get(
            "baseline_origin_asn_count"
        ),
        "ipv6_visible_prefix_vp_count": ipv6.get("visible_prefix_vp_count"),
        "ipv6_baseline_prefix_vp_count": ipv6.get("baseline_prefix_vp_count"),
        "ipv6_visible_prefix_vp_ratio": ratio(
            "visible_prefix_vp_count", "baseline_prefix_vp_count", ipv6
        ),
        "ipv6_visible_origin_asn_count": ipv6.get("visible_origin_asn_count"),
        "ipv6_baseline_origin_asn_count": ipv6.get(
            "baseline_origin_asn_count"
        ),
        "announce_count": updates.get("announce"),
        "withdraw_count": updates.get("withdraw"),
    }


def _population_by_asn(cohort: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    members = cohort.get("members")
    if not isinstance(members, list):
        return result
    for member in members:
        if not isinstance(member, Mapping) or not isinstance(member.get("asn"), int):
            continue
        asn = member["asn"]
        current = result.setdefault(
            asn,
            {
                "baseline_prefix_vp_count": 0,
                "prefixes": set(),
                "address_families": set(),
            },
        )
        prefix_vp_count = member.get("prefix_vp_count")
        if isinstance(prefix_vp_count, int):
            current["baseline_prefix_vp_count"] += prefix_vp_count
        prefixes = member.get("prefixes")
        if isinstance(prefixes, list):
            current["prefixes"].update(
                str(prefix) for prefix in prefixes if prefix
            )
        afi = member.get("afi")
        if isinstance(afi, int):
            current["address_families"].add(afi)
    return result


def _persistent_asns(
    package_directory: Path,
    cohort: Mapping[str, Any],
    observation_end_at: str,
) -> list[dict[str, Any]]:
    population = _population_by_asn(cohort)
    states: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "affected_slot_count": 0,
            "fully_invisible_slot_count": 0,
            "partially_visible_slot_count": 0,
            "first_affected_at": None,
            "last_affected_at": None,
            "end_classification": "unknown",
        }
    )
    for row in _jsonl_gzip(package_directory / "asn-states.jsonl.gz"):
        asn = row.get("asn")
        classification = row.get("classification")
        observed_at = row.get("observed_at")
        if not isinstance(asn, int) or not isinstance(classification, str):
            continue
        current = states[asn]
        if classification != "fully_visible":
            current["affected_slot_count"] += 1
            if classification == "fully_invisible":
                current["fully_invisible_slot_count"] += 1
            elif classification == "partially_visible":
                current["partially_visible_slot_count"] += 1
            if current["first_affected_at"] is None:
                current["first_affected_at"] = observed_at
            current["last_affected_at"] = observed_at
        if observed_at == observation_end_at:
            current["end_classification"] = classification

    result = []
    for asn, state in states.items():
        if state["affected_slot_count"] <= 0:
            continue
        source_population = population.get(
            asn,
            {
                "baseline_prefix_vp_count": 0,
                "prefixes": set(),
                "address_families": set(),
            },
        )
        result.append(
            {
                "asn": str(asn),
                **state,
                "first_affected_at_local": _local_time(
                    state["first_affected_at"]
                ),
                "last_affected_at_local": _local_time(
                    state["last_affected_at"]
                ),
                "baseline_prefix_vp_count": source_population[
                    "baseline_prefix_vp_count"
                ],
                "baseline_prefix_count": len(source_population["prefixes"]),
                "address_families": sorted(
                    source_population["address_families"]
                ),
            }
        )
    result.sort(
        key=lambda row: (
            row["fully_invisible_slot_count"],
            row["affected_slot_count"],
            row["baseline_prefix_vp_count"],
            int(row["asn"]),
        ),
        reverse=True,
    )
    return result[:20]


def _find_snapshot(
    snapshots: list[dict[str, Any]], observed_at: str | None
) -> dict[str, Any] | None:
    if not observed_at:
        return None
    return next(
        (
            snapshot
            for snapshot in snapshots
            if snapshot["observed_at_utc"] == observed_at
        ),
        None,
    )


def _legacy_value(detail: Mapping[str, Any], key: str):
    value = detail.get(key)
    return None if value in ("", None) else value


@lru_cache(maxsize=4)
def _load_package(package_directory_text: str) -> dict[str, Any]:
    package_directory = Path(package_directory_text)
    if not package_directory.is_dir():
        raise EventStoryUnavailable("伊朗事件状态重放交付包尚未安装")

    complete = _read_json(package_directory / "COMPLETE.json")
    quality = _read_json(package_directory / "QUALITY.json")
    if complete.get("status") != "complete":
        raise EventStoryUnavailable("伊朗事件状态重放尚未完成")
    if quality.get("status") != "pass" or quality.get("failures") not in (None, []):
        raise EventStoryUnavailable("伊朗事件状态重放未通过质量门")

    recorded_hashes = complete.get("deliverable_sha256")
    if not isinstance(recorded_hashes, Mapping):
        raise EventStoryUnavailable("状态重放交付清单缺少文件哈希")
    verified_hashes = {}
    for filename in CONSUMED_DELIVERABLES:
        expected = recorded_hashes.get(filename)
        if not isinstance(expected, str):
            raise EventStoryUnavailable(f"交付清单缺少 {filename} 哈希")
        actual = _sha256(package_directory / filename)
        if actual != expected:
            raise EventStoryUnavailable(f"研究交付文件哈希不匹配：{filename}")
        verified_hashes[filename] = actual

    incident = _read_json(package_directory / "incident.json")
    cohort = _read_json(package_directory / "cohort.json")
    episodes = _read_json(package_directory / "episodes.json")
    waves = _read_json(package_directory / "waves.json")
    input_summary = _read_json(package_directory / "input-summary.json")
    snapshots = [
        _snapshot_view(row)
        for row in _jsonl_gzip(package_directory / "country-snapshots.jsonl.gz")
    ]
    if not snapshots:
        raise EventStoryUnavailable("伊朗事件状态重放没有国家状态点")
    if len(snapshots) != quality.get("observation_count"):
        raise EventStoryUnavailable("国家状态点数量与质量报告不一致")
    if snapshots[-1]["observed_at_utc"] != quality.get("last_observation_at"):
        raise EventStoryUnavailable("国家状态最后观测时间与质量报告不一致")

    persistent_asns = _persistent_asns(
        package_directory,
        cohort,
        str(incident.get("observation_end_at") or ""),
    )
    return {
        "package_directory": str(package_directory),
        "complete": complete,
        "quality": quality,
        "incident": incident,
        "cohort": cohort,
        "episodes": episodes,
        "waves": waves,
        "input_summary": input_summary,
        "snapshots": snapshots,
        "persistent_asns": persistent_asns,
        "verified_hashes": verified_hashes,
    }


def get_iran_event_story(
    *,
    legacy_reference: str,
    legacy_detail: Mapping[str, Any] | None = None,
    package_directory: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """返回伊朗主事件的产品叙事；其他事件由 Legacy 页面继续承接。"""

    if legacy_reference != IRAN_LEGACY_REF:
        return None
    resolved_directory = Path(
        package_directory
        or os.environ.get("DOMEYE_IRAN_REPLAY_DIRECTORY")
        or DEFAULT_REPLAY_DIRECTORY
    )
    package = _load_package(str(resolved_directory))
    incident = package["incident"]
    cohort = package["cohort"]
    quality = package["quality"]
    complete = package["complete"]
    snapshots = package["snapshots"]
    waves = package["waves"].get("waves")
    waves = waves if isinstance(waves, list) else []
    detail = legacy_detail or {}

    start_snapshot = snapshots[0]
    detected_snapshot = _find_snapshot(snapshots, incident.get("detected_at"))
    peak_snapshot = _find_snapshot(snapshots, incident.get("peak_at"))
    trough_snapshot = _find_snapshot(snapshots, incident.get("trough_at"))
    end_snapshot = snapshots[-1]
    if not all((detected_snapshot, peak_snapshot, trough_snapshot)):
        raise EventStoryUnavailable("事件里程碑无法关联到国家状态点")

    onset_local = _local_time(incident.get("onset_at"))
    detected_local = _local_time(incident.get("detected_at"))
    peak_local = _local_time(incident.get("peak_at"))
    trough_local = _local_time(incident.get("trough_at"))
    observation_end_local = _local_time(incident.get("observation_end_at"))
    legacy_record_time = _legacy_value(detail, "start_time")
    old_affected = _legacy_value(detail, "outage_as_num")
    old_total = _legacy_value(detail, "total_as_num")

    return {
        "schema_version": "event_detail_story_v1",
        "contract_scope": {
            "acceptance_event": True,
            "event_types_covered": ["country_outage"],
            "collector_scope": ["rrc25"],
            "control_plane_only": True,
            "causal_analysis_performed": False,
        },
        "event": {
            "incident_id": incident.get("incident_id"),
            "legacy_reference": legacy_reference,
            "legacy_record_time_local": legacy_record_time,
            "kind": "country_outage",
            "label": "国家路由可见性异常",
            "country_code": "IR",
            "country_name": "伊朗",
            "severity": "high",
            "status": incident.get("recovery_state"),
            "status_label": "观测窗结束，仍未确认恢复",
            "headline": (
                "RRC25 视角下，伊朗固定路由人口的控制面可见性显著下降；"
                "23:00 已有回升，但事件仍处于进行状态。"
            ),
            "scope_statement": (
                "结论仅适用于 RRC25、固定 IR cohort 和北京时间 "
                "2026-02-28 18:05–23:00 的 BGP 控制面观测。"
            ),
            "service_impact_statement": (
                "尚无数据面、流量或服务可用性证据，不能据此断言伊朗全国用户断网。"
            ),
        },
        "observation": {
            "collector_id": incident.get("collector_id"),
            "collector_count": 1,
            "vantage_point_count": WINDOW_START_VANTAGE_POINT_COUNT,
            "vantage_point_count_semantics": (
                "窗口起点 route-states 中的唯一 VP 身份数"
            ),
            "window_start_utc": start_snapshot["observed_at_utc"],
            "window_start_local": start_snapshot["observed_at_local"],
            "window_end_utc": end_snapshot["observed_at_utc"],
            "window_end_local": end_snapshot["observed_at_local"],
            "timezone": "Asia/Shanghai",
            "observation_count": len(snapshots),
            "interval_seconds": 300,
            "left_censored": True,
            "right_censored": True,
            "coverage_state": "complete_within_fixed_window",
            "coverage_statement": (
                "固定窗口 60/60 个状态点完整；事件起点早于或等于窗口起点，"
                "恢复时间晚于窗口终点或仍未知。"
            ),
            "cohort": {
                "cohort_id": cohort.get("cohort_id"),
                "seed_observed_at_utc": cohort.get("seed_observed_at"),
                "seed_observed_at_local": _local_time(
                    cohort.get("seed_observed_at")
                ),
                "baseline_origin_asn_count": cohort.get(
                    "baseline_origin_asn_count"
                ),
                "baseline_prefix_vp_count": cohort.get(
                    "baseline_prefix_vp_count"
                ),
                "mapping_version": cohort.get("mapping_version"),
                "denominator_policy": "fixed_from_complete_rib",
            },
            "data_freshness": {
                "last_observation_at_utc": quality.get("last_observation_at"),
                "last_observation_at_local": _local_time(
                    quality.get("last_observation_at")
                ),
                "replay_completed_at_utc": complete.get("completed_at"),
                "replay_completed_at_local": _local_time(
                    complete.get("completed_at")
                ),
                "quality_status": quality.get("status"),
            },
        },
        "baseline": {
            "state": incident.get("normal_band", {}).get("state"),
            "label": "正常带不可用",
            "reason": (
                "08:00–10:00 UTC 的 catch-up 阶段已经出现连续异常，且可见性波动"
                "超过检测尺度，无法从本次输入构造可信正常带。"
            ),
            "known_population": {
                "origin_asn_count": cohort.get("baseline_origin_asn_count"),
                "prefix_vp_count": cohort.get("baseline_prefix_vp_count"),
            },
            "consequence": (
                "可以描述固定人口在窗口内的实际变化，但不能声称相对长期正常水平"
                "偏离了多少。"
            ),
        },
        "detection": {
            "rule": {
                "metric": "affected_asn_ratio",
                "threshold": 0.03,
                "confirm_observation_count": 2,
                "confirm_duration_seconds": 300,
                "statement": "受影响 ASN 比例连续两个五分钟状态点高于 3%。",
            },
            "onset": {
                "at_utc": incident.get("onset_at"),
                "at_local": onset_local,
                "precision": "left_censored_at_window_start",
                "statement": f"事件不晚于 {onset_local} 已经发生，精确起点未知。",
            },
            "detected": {
                "at_utc": incident.get("detected_at"),
                "at_local": detected_local,
                "snapshot_id": detected_snapshot["snapshot_id"],
            },
            "legacy_record": {
                "at_local": legacy_record_time,
                "semantics": "旧事实记录身份/旧系统检测时间",
                "not_event_onset": True,
            },
        },
        "impact": {
            "peak": peak_snapshot,
            "trough": trough_snapshot,
            "window_start": start_snapshot,
            "window_end": end_snapshot,
            "peak_statement": (
                f"{peak_local} 受影响 ASN 达到 "
                f"{peak_snapshot['affected_asn_count']}/"
                f"{cohort.get('baseline_origin_asn_count')}，其中 "
                f"{peak_snapshot['fully_invisible_asn_count']} 个全不可见、"
                f"{peak_snapshot['partially_visible_asn_count']} 个部分可见。"
            ),
            "trough_statement": (
                f"{trough_local} 固定 Prefix×VP 可见率降至 "
                f"{trough_snapshot['visible_prefix_vp_ratio']:.4%}。"
            ),
            "end_statement": (
                f"{observation_end_local} 可见率回升至 "
                f"{end_snapshot['visible_prefix_vp_ratio']:.4%}，但仍有 "
                f"{end_snapshot['affected_asn_count']}/"
                f"{cohort.get('baseline_origin_asn_count')} 个 ASN 受影响。"
            ),
            "persistent_asns": package["persistent_asns"],
            "ranking_semantics": (
                "按全不可见五分钟槽数、受影响槽数和固定 Prefix×VP 人口排序；"
                "不代表用户规模或商业重要性。"
            ),
        },
        "series": snapshots,
        "lifecycle": {
            "episode_count": len(incident.get("episodes") or []),
            "wave_count": len(waves),
            "wave_causal_relation": (
                waves[0].get("causal_relation") if waves else "not_assessed"
            ),
            "current_state": incident.get("recovery_state"),
            "current_state_label": "进行中",
            "duration_state": incident.get("duration_state"),
            "onset_at_local": onset_local,
            "detected_at_local": detected_local,
            "peak_at_local": peak_local,
            "trough_at_local": trough_local,
            "partial_recovery_at_local": _local_time(
                incident.get("partial_recovery_at")
            ),
            "full_recovery_at_local": _local_time(
                incident.get("full_recovery_at")
            ),
            "observation_end_at_local": observation_end_local,
            "recovery_rule": (
                "部分恢复要求 ASN 与 Prefix×VP 可见率连续六个状态点均达到 99%；"
                "完全恢复还要求回到可信正常带。"
            ),
            "rebound_statement": (
                "窗口末相较谷值出现回升，但未连续六个状态点达到部分恢复门槛，"
                "且正常带不可用。"
            ),
        },
        "precursor": {
            "candidate_time_local": legacy_record_time,
            "relation": "temporal_only",
            "causal_relation": "not_assessed",
            "statement": (
                "旧系统更早记录过伊朗国家中断候选；它可以作为时间上的前候选事件，"
                "但当前证据不能证明其导致了 18:05 后的主事件。"
            ),
        },
        "comparisons": [
            {
                "source": "原事件报告",
                "value": "199/595；73 全不可见、126 部分可见",
                "status": "unverifiable",
                "explanation": "原报告未冻结同一 cohort、快照和双栈分类定义。",
            },
            {
                "source": "旧数据库事实",
                "value": (
                    f"{old_affected}/{old_total}"
                    if old_affected is not None and old_total is not None
                    else "旧事实当前不可用"
                ),
                "status": "internally_consistent",
                "explanation": (
                    "旧事实内部一致，但没有同快照逐 VP 路由状态，不能与新口径直接相减。"
                ),
            },
            {
                "source": "本次 Go 状态重放",
                "value": (
                    f"{peak_snapshot['affected_asn_count']}/"
                    f"{cohort.get('baseline_origin_asn_count')}；"
                    f"{peak_snapshot['fully_invisible_asn_count']} 全不可见、"
                    f"{peak_snapshot['partially_visible_asn_count']} 部分可见"
                ),
                "status": "verified_fixed_cohort",
                "explanation": "固定 RRC25 cohort、同一五分钟状态和双栈联合分类。",
            },
        ],
        "claims": [
            {
                "claim_id": "claim_visibility_decline",
                "level": "fact",
                "confidence": "high_within_rrc25_scope",
                "title": "固定路由人口可见性显著下降",
                "statement": (
                    f"Prefix×VP 可见率从窗口起点 "
                    f"{start_snapshot['visible_prefix_vp_ratio']:.4%} 降至窗口谷值 "
                    f"{trough_snapshot['visible_prefix_vp_ratio']:.4%}。"
                ),
                "scope": "RRC25 · 固定 IR cohort · BGP 控制面",
                "evidence_refs": [
                    start_snapshot["snapshot_id"],
                    trough_snapshot["snapshot_id"],
                    "country-snapshots.jsonl.gz",
                ],
            },
            {
                "claim_id": "claim_peak_impact",
                "level": "fact",
                "confidence": "high_within_rrc25_scope",
                "title": "22:00 达到窗口内 ASN 影响峰值",
                "statement": (
                    f"受影响 ASN 为 {peak_snapshot['affected_asn_count']}/"
                    f"{cohort.get('baseline_origin_asn_count')}。"
                ),
                "scope": "同一固定 ASN 人口和同一五分钟状态",
                "evidence_refs": [
                    peak_snapshot["snapshot_id"],
                    "asn-states.jsonl.gz",
                ],
            },
            {
                "claim_id": "claim_ongoing",
                "level": "derived",
                "confidence": "high_within_window",
                "title": "观察窗结束时仍未确认恢复",
                "statement": (
                    "23:00 虽有回升，但没有满足部分恢复或完全恢复的连续状态规则。"
                ),
                "scope": "截至北京时间 2026-02-28 23:00",
                "evidence_refs": [
                    incident.get("incident_id"),
                    end_snapshot["snapshot_id"],
                    "incident.json",
                ],
            },
            {
                "claim_id": "claim_service_impact_unknown",
                "level": "unknown",
                "confidence": "unsupported_by_current_data",
                "title": "实际用户和服务影响未知",
                "statement": (
                    "当前没有数据面、流量、时延或服务可用性证据。"
                ),
                "scope": "不得由 BGP 控制面直接外推",
                "evidence_refs": ["QUALITY.json"],
            },
        ],
        "unknowns": [
            {
                "question": "18:05 之前的精确异常起点是什么？",
                "reason": "catch-up 阶段已经异常，正式窗口起点发生左删失。",
                "evidence_needed": "更早且稳定的状态窗口，以及可用正常带。",
                "next_action": "仅在需要精确起点时向前扩展状态窗口。",
            },
            {
                "question": "23:00 之后何时部分或完全恢复？",
                "reason": "当前状态窗口在 23:00 结束，事件仍为 ongoing。",
                "evidence_needed": "更晚 UPDATE 和连续恢复状态。",
                "next_action": "从现有 checkpoint 向后扩展恢复观察窗。",
            },
            {
                "question": "其他 collector 是否观察到同样的范围和节奏？",
                "reason": "当前只有 RRC25 单 collector 视角。",
                "evidence_needed": "其他 collector 的同口径固定 cohort 状态。",
                "next_action": "增加多 collector 对照，不直接外推为全球结论。",
            },
            {
                "question": "实际用户流量和服务是否中断？",
                "reason": "BGP 控制面不包含数据面、流量和服务遥测。",
                "evidence_needed": "主动探测、流量、时延和服务可用性数据。",
                "next_action": "补充数据面探测后再判断实际服务影响。",
            },
            {
                "question": "前候选事件是否导致主事件？",
                "reason": "当前只有时间先后，未建立可证明的因果链。",
                "evidence_needed": "跨窗口连续状态及独立外部证据。",
                "next_action": "继续标记为时间相关、因果未评估。",
            },
            {
                "question": "事件的政治、物理或行为意图原因是什么？",
                "reason": "RRC25 路由状态不能证明意图、物理断路或政策行为。",
                "evidence_needed": "运营、物理链路及可信外部调查材料。",
                "next_action": "不在当前事件结论中进行根因归因。",
            },
        ],
        "actions": [
            {
                "priority": 1,
                "label": "继续观察恢复",
                "reason": "23:00 时事件仍为 ongoing。",
            },
            {
                "priority": 2,
                "label": "查看持续受影响 ASN",
                "reason": "优先检查窗口末仍全不可见或部分可见的网络。",
            },
            {
                "priority": 3,
                "label": "核对其他 collector",
                "reason": "确认 RRC25 视角是否具有跨观测点一致性。",
            },
            {
                "priority": 4,
                "label": "补充数据面探测",
                "reason": "控制面变化不能直接回答用户和服务影响。",
            },
            {
                "priority": 5,
                "label": "按需扩展更早窗口",
                "reason": "仅在需要确定精确 onset 时补充更早稳定状态。",
            },
        ],
        "evidence": {
            "engine_version": incident.get("algorithm_version"),
            "package_directory": package["package_directory"],
            "quality_status": quality.get("status"),
            "consumed_deliverable_hashes_verified": True,
            "verified_hashes": package["verified_hashes"],
            "route_state_file": {
                "filename": "route-states.jsonl.gz",
                "recorded_sha256": complete.get("deliverable_sha256", {}).get(
                    "route-states.jsonl.gz"
                ),
                "row_count": 23094550,
                "request_path_hash_reverified": False,
                "statement": (
                    "完整逐 Prefix×VP 状态保留在交付包中；事件页请求不重复读取"
                    "或重新哈希 258 MiB 状态文件。"
                ),
            },
            "input_summary": {
                "rib_count": 1,
                "catch_up_update_count": len(
                    package["input_summary"].get("catch_up_updates") or []
                ),
                "formal_update_count": len(
                    package["input_summary"].get("formal_updates") or []
                ),
                "input_compressed_bytes": quality.get(
                    "input_compressed_bytes"
                ),
                "rib_physical_records": quality.get("rib_physical_records"),
                "rib_entries": quality.get("rib_entries"),
                "update_physical_records": quality.get(
                    "update_physical_records"
                ),
                "update_route_events": quality.get("update_route_events"),
            },
        },
    }
