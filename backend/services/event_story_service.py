"""伊朗事件研究交付包的只读观测数据适配。

本模块只读取独立 Go 重放引擎生成的不可变交付包，不触发重放、不连接旧项目，
也不把 RRC25 控制面观测外推为生命周期、因果、数据面或实际服务结论。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


LEGACY_STORY_REFERENCE = "country_outage/2026-02-27 09:12:32/IR/1/r"
LEGACY_STORY_REPLAY_DIRECTORY = Path(
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
LEGACY_STORY_VANTAGE_POINT_COUNT = 96
ASN_STATE_CODES = {
    "unknown": -1,
    "fully_visible": 0,
    "partially_visible": 1,
    "fully_invisible": 2,
}


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


def _timezone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Asia/Shanghai")
    except (KeyError, ValueError) as error:
        raise EventStoryUnavailable(f"无法识别展示时区：{name}") from error


def _local_time(value: Any, timezone_name: str = "Asia/Shanghai") -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(_timezone(timezone_name)).isoformat(timespec="seconds")


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


def _snapshot_view(
    row: Mapping[str, Any],
    timezone_name: str = "Asia/Shanghai",
) -> dict[str, Any]:
    classifications = row.get("dual_stack_classifications")
    if not isinstance(classifications, Mapping):
        classifications = {}
    ipv4 = row.get("ipv4")
    ipv4 = ipv4 if isinstance(ipv4, Mapping) else {}
    ipv6 = row.get("ipv6")
    ipv6 = ipv6 if isinstance(ipv6, Mapping) else {}
    updates = row.get("update_counts")
    updates = updates if isinstance(updates, Mapping) else {}
    country_updates = row.get("country_update_counts")
    country_updates = (
        country_updates if isinstance(country_updates, Mapping) else {}
    )

    def ratio(visible_key: str, baseline_key: str, source: Mapping[str, Any]):
        visible = source.get(visible_key)
        baseline = source.get(baseline_key)
        if not isinstance(visible, int) or not isinstance(baseline, int) or baseline <= 0:
            return None
        return visible / baseline

    return {
        "snapshot_id": row.get("snapshot_id"),
        "observed_at_utc": row.get("observed_at"),
        "observed_at_local": _local_time(row.get("observed_at"), timezone_name),
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
        "country_announce_count": country_updates.get("announce"),
        "country_withdraw_count": country_updates.get("withdraw"),
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
    timezone_name: str = "Asia/Shanghai",
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
                    state["first_affected_at"], timezone_name
                ),
                "last_affected_at_local": _local_time(
                    state["last_affected_at"], timezone_name
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


def _combined_asn_classification(
    classifications: list[str],
    *,
    expected_family_count: int,
) -> str:
    if (
        not classifications
        or len(classifications) < expected_family_count
        or any(value not in ASN_STATE_CODES for value in classifications)
        or "unknown" in classifications
    ):
        return "unknown"
    if all(value == "fully_visible" for value in classifications):
        return "fully_visible"
    if all(value == "fully_invisible" for value in classifications):
        return "fully_invisible"
    return "partially_visible"


def _longest_run(values: list[int], expected: int) -> int:
    longest = 0
    current = 0
    for value in values:
        if value == expected:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _asn_state_timelines(
    package_directory: Path,
    cohort: Mapping[str, Any],
    observed_times: list[str],
) -> list[dict[str, Any]]:
    population = _population_by_asn(cohort)
    time_index = {
        observed_at: index for index, observed_at in enumerate(observed_times)
    }
    per_family: dict[tuple[int, int], list[str]] = defaultdict(list)
    for row in _jsonl_gzip(package_directory / "asn-states.jsonl.gz"):
        asn = row.get("asn")
        observed_at = row.get("observed_at")
        classification = row.get("classification")
        if (
            not isinstance(asn, int)
            or not isinstance(observed_at, str)
            or observed_at not in time_index
            or not isinstance(classification, str)
        ):
            continue
        per_family[(asn, time_index[observed_at])].append(classification)

    result: list[dict[str, Any]] = []
    for asn, source_population in sorted(population.items()):
        address_families = sorted(source_population["address_families"])
        expected_family_count = max(1, len(address_families))
        states = [
            ASN_STATE_CODES[
                _combined_asn_classification(
                    per_family.get((asn, index), []),
                    expected_family_count=expected_family_count,
                )
            ]
            for index in range(len(observed_times))
        ]
        counts = {
            "fully_visible": states.count(ASN_STATE_CODES["fully_visible"]),
            "partially_visible": states.count(
                ASN_STATE_CODES["partially_visible"]
            ),
            "fully_invisible": states.count(
                ASN_STATE_CODES["fully_invisible"]
            ),
            "unknown": states.count(ASN_STATE_CODES["unknown"]),
        }
        result.append(
            {
                "asn": str(asn),
                "address_families": address_families,
                "baseline_prefix_count": len(source_population["prefixes"]),
                "baseline_prefix_vp_count": source_population[
                    "baseline_prefix_vp_count"
                ],
                "states": states,
                "state_slot_counts": counts,
                "longest_fully_visible_slots": _longest_run(
                    states, ASN_STATE_CODES["fully_visible"]
                ),
                "longest_partially_visible_slots": _longest_run(
                    states, ASN_STATE_CODES["partially_visible"]
                ),
                "longest_fully_invisible_slots": _longest_run(
                    states, ASN_STATE_CODES["fully_invisible"]
                ),
            }
        )
    return result


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


@lru_cache(maxsize=8)
def _load_package(
    package_directory_text: str,
    timezone_name: str = "Asia/Shanghai",
) -> dict[str, Any]:
    package_directory = Path(package_directory_text)
    if not package_directory.is_dir():
        raise EventStoryUnavailable("国家事件状态重放交付包尚未安装")

    complete = _read_json(package_directory / "COMPLETE.json")
    quality = _read_json(package_directory / "QUALITY.json")
    if complete.get("status") != "complete":
        raise EventStoryUnavailable("国家事件状态重放尚未完成")
    if quality.get("status") != "pass" or quality.get("failures") not in (None, []):
        raise EventStoryUnavailable("国家事件状态重放未通过质量门")

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
        _snapshot_view(row, timezone_name)
        for row in _jsonl_gzip(package_directory / "country-snapshots.jsonl.gz")
    ]
    if not snapshots:
        raise EventStoryUnavailable("国家事件状态重放没有国家状态点")
    if len(snapshots) != quality.get("observation_count"):
        raise EventStoryUnavailable("国家状态点数量与质量报告不一致")
    if snapshots[-1]["observed_at_utc"] != quality.get("last_observation_at"):
        raise EventStoryUnavailable("国家状态最后观测时间与质量报告不一致")

    persistent_asns = _persistent_asns(
        package_directory,
        cohort,
        str(incident.get("observation_end_at") or ""),
        timezone_name,
    )
    asn_state_timelines = _asn_state_timelines(
        package_directory,
        cohort,
        [str(snapshot["observed_at_utc"]) for snapshot in snapshots],
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
        "asn_state_timelines": asn_state_timelines,
        "verified_hashes": verified_hashes,
    }


def inspect_country_outage_package(
    package_directory: Path,
) -> dict[str, Any]:
    """完整核验在线查询会消费的制品，并返回发布门所需身份。"""

    resolved = package_directory.expanduser().resolve()
    package = _load_package(str(resolved))
    slot_fingerprints: list[dict[str, str]] = []
    timelines = package["asn_state_timelines"]
    for index, snapshot in enumerate(package["snapshots"]):
        slot_payload = {
            "snapshot": snapshot,
            "asn_states": [
                [timeline["asn"], timeline["states"][index]]
                for timeline in timelines
            ],
        }
        encoded = json.dumps(
            slot_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        slot_fingerprints.append(
            {
                "observed_at": snapshot["observed_at_utc"],
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
    cohort_encoded = json.dumps(
        package["cohort"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "incident_id": package["incident"].get("incident_id"),
        "cohort_id": package["cohort"].get("cohort_id"),
        "cohort_sha256": hashlib.sha256(cohort_encoded).hexdigest(),
        "observation_count": len(package["snapshots"]),
        "first_observation_at": package["snapshots"][0]["observed_at_utc"],
        "last_observation_at": package["snapshots"][-1]["observed_at_utc"],
        "slot_fingerprints": slot_fingerprints,
        "completed_at": package["complete"].get("completed_at"),
        "quality_status": package["quality"].get("status"),
        "verified_hashes": dict(package["verified_hashes"]),
    }


def _numeric_delta(current: Any, previous: Any) -> int | float | None:
    if (
        isinstance(current, (int, float))
        and not isinstance(current, bool)
        and isinstance(previous, (int, float))
        and not isinstance(previous, bool)
    ):
        return current - previous
    return None


def _observation_series(
    snapshots: list[dict[str, Any]],
    *,
    baseline_origin_asn_count: int,
    baseline_prefix_vp_count: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for snapshot in snapshots:
        visible_prefix_vp_count = snapshot.get("visible_prefix_vp_count")
        visible_prefix_vp_ratio = snapshot.get("visible_prefix_vp_ratio")
        visible_origin_asn_count = snapshot.get("visible_origin_asn_count")
        fully_invisible_asn_count = snapshot.get("fully_invisible_asn_count")
        partially_visible_asn_count = snapshot.get(
            "partially_visible_asn_count"
        )
        announce_count = snapshot.get("announce_count")
        withdraw_count = snapshot.get("withdraw_count")
        country_announce_count = snapshot.get("country_announce_count")
        country_withdraw_count = snapshot.get("country_withdraw_count")
        invisible_prefix_vp_count = (
            baseline_prefix_vp_count - visible_prefix_vp_count
            if isinstance(visible_prefix_vp_count, int)
            else None
        )
        fully_visible_asn_count = (
            baseline_origin_asn_count
            - fully_invisible_asn_count
            - partially_visible_asn_count
            if isinstance(fully_invisible_asn_count, int)
            and isinstance(partially_visible_asn_count, int)
            else None
        )
        non_fully_visible_asn_count = (
            fully_invisible_asn_count + partially_visible_asn_count
            if isinstance(fully_invisible_asn_count, int)
            and isinstance(partially_visible_asn_count, int)
            else None
        )
        update_total = (
            announce_count + withdraw_count
            if isinstance(announce_count, int)
            and isinstance(withdraw_count, int)
            else None
        )
        withdraw_ratio = (
            withdraw_count / update_total
            if isinstance(withdraw_count, int)
            and isinstance(update_total, int)
            and update_total > 0
            else None
        )
        country_update_total = (
            country_announce_count + country_withdraw_count
            if isinstance(country_announce_count, int)
            and isinstance(country_withdraw_count, int)
            else None
        )
        country_withdraw_ratio = (
            country_withdraw_count / country_update_total
            if isinstance(country_withdraw_count, int)
            and isinstance(country_update_total, int)
            and country_update_total > 0
            else None
        )
        point = {
            **snapshot,
            "slot_state": "observed",
            "missing_reason": None,
            "invisible_prefix_vp_count": invisible_prefix_vp_count,
            "fully_visible_asn_count": fully_visible_asn_count,
            "non_fully_visible_asn_count": non_fully_visible_asn_count,
            "update_total": update_total,
            "withdraw_ratio": withdraw_ratio,
            "country_update_total": country_update_total,
            "country_withdraw_ratio": country_withdraw_ratio,
            "visible_prefix_vp_delta": _numeric_delta(
                visible_prefix_vp_count,
                None if previous is None else previous["visible_prefix_vp_count"],
            ),
            "visible_prefix_vp_ratio_delta_pp": (
                None
                if previous is None
                else (
                    _numeric_delta(
                        visible_prefix_vp_ratio,
                        previous["visible_prefix_vp_ratio"],
                    )
                    * 100
                    if _numeric_delta(
                        visible_prefix_vp_ratio,
                        previous["visible_prefix_vp_ratio"],
                    )
                    is not None
                    else None
                )
            ),
            "visible_origin_asn_delta": _numeric_delta(
                visible_origin_asn_count,
                None if previous is None else previous["visible_origin_asn_count"],
            ),
            "announce_delta": _numeric_delta(
                announce_count,
                None if previous is None else previous["announce_count"],
            ),
            "withdraw_delta": _numeric_delta(
                withdraw_count,
                None if previous is None else previous["withdraw_count"],
            ),
            "country_announce_delta": _numeric_delta(
                country_announce_count,
                (
                    None
                    if previous is None
                    else previous["country_announce_count"]
                ),
            ),
            "country_withdraw_delta": _numeric_delta(
                country_withdraw_count,
                (
                    None
                    if previous is None
                    else previous["country_withdraw_count"]
                ),
            ),
            "ipv4_visible_prefix_vp_delta": _numeric_delta(
                snapshot.get("ipv4_visible_prefix_vp_count"),
                (
                    None
                    if previous is None
                    else previous["ipv4_visible_prefix_vp_count"]
                ),
            ),
            "ipv6_visible_prefix_vp_delta": _numeric_delta(
                snapshot.get("ipv6_visible_prefix_vp_count"),
                (
                    None
                    if previous is None
                    else previous["ipv6_visible_prefix_vp_count"]
                ),
            ),
        }
        result.append(point)
        previous = point
    return result


def _series_with_declared_gaps(
    series: list[dict[str, Any]],
    *,
    interval_seconds: int,
    missing_slots: Any,
    timezone_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not series:
        return series, []
    raw_missing = [] if missing_slots is None else missing_slots
    if not isinstance(raw_missing, list):
        raise EventStoryUnavailable("missing_slots 必须是数组")

    observed_by_time = {
        str(point["observed_at_utc"]): point for point in series
    }
    start = datetime.fromisoformat(
        str(series[0]["observed_at_utc"]).replace("Z", "+00:00")
    )
    end = datetime.fromisoformat(
        str(series[-1]["observed_at_utc"]).replace("Z", "+00:00")
    )
    expected_times: list[str] = []
    cursor = start
    while cursor <= end:
        expected_times.append(
            cursor.isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        cursor += timedelta(seconds=interval_seconds)
    expected_missing = set(expected_times) - set(observed_by_time)

    declared: dict[str, dict[str, str]] = {}
    allowed_states = {
        "source_unavailable",
        "processing_gap",
        "parse_failed",
        "not_observed",
    }
    for raw in raw_missing:
        if not isinstance(raw, Mapping):
            raise EventStoryUnavailable("missing_slots 条目必须是对象")
        observed_at = raw.get("observed_at")
        slot_state = raw.get("slot_state")
        reason = raw.get("missing_reason")
        if (
            not isinstance(observed_at, str)
            or observed_at not in expected_missing
            or observed_at in declared
            or slot_state not in allowed_states
            or not isinstance(reason, str)
            or not reason
        ):
            raise EventStoryUnavailable("missing_slots 时间、状态或原因无效")
        declared[observed_at] = {
            "observed_at": observed_at,
            "slot_state": str(slot_state),
            "missing_reason": reason,
        }
    if set(declared) != expected_missing:
        raise EventStoryUnavailable("时间轴缺槽没有被完整、唯一地声明")

    template = series[0]
    merged: list[dict[str, Any]] = []
    for observed_at in expected_times:
        observed = observed_by_time.get(observed_at)
        if observed is not None:
            merged.append(dict(observed))
            continue
        metadata = declared[observed_at]
        gap = {
            key: None
            for key in template
            if key
            not in {
                "snapshot_id",
                "observed_at_utc",
                "observed_at_local",
                "slot_state",
                "missing_reason",
            }
        }
        gap.update(
            {
                "snapshot_id": f"gap:{observed_at}",
                "observed_at_utc": observed_at,
                "observed_at_local": _local_time(
                    observed_at,
                    timezone_name,
                ),
                "slot_state": metadata["slot_state"],
                "missing_reason": metadata["missing_reason"],
            }
        )
        merged.append(gap)

    delta_keys = {
        key
        for point in series
        for key in point
        if key.endswith("_delta") or key.endswith("_delta_pp")
    }
    for index, point in enumerate(merged):
        if (
            point.get("slot_state") == "observed"
            and index > 0
            and merged[index - 1].get("slot_state") != "observed"
        ):
            for key in delta_keys:
                point[key] = None
    return merged, [declared[key] for key in sorted(declared)]


def _asn_timelines_with_gaps(
    timelines: list[dict[str, Any]],
    *,
    actual_times: list[str],
    published_times: list[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for timeline in timelines:
        state_by_time = dict(zip(actual_times, timeline["states"]))
        states = [
            state_by_time.get(observed_at, ASN_STATE_CODES["unknown"])
            for observed_at in published_times
        ]
        counts = {
            label: states.count(code)
            for label, code in ASN_STATE_CODES.items()
        }
        result.append(
            {
                **timeline,
                "states": states,
                "state_slot_counts": counts,
                "longest_fully_visible_slots": _longest_run(
                    states,
                    ASN_STATE_CODES["fully_visible"],
                ),
                "longest_partially_visible_slots": _longest_run(
                    states,
                    ASN_STATE_CODES["partially_visible"],
                ),
                "longest_fully_invisible_slots": _longest_run(
                    states,
                    ASN_STATE_CODES["fully_invisible"],
                ),
            }
        )
    return result


def _extreme(
    series: list[dict[str, Any]],
    metric: str,
    *,
    mode: str,
) -> dict[str, Any] | None:
    values = [
        point
        for point in series
        if isinstance(point.get(metric), (int, float))
        and not isinstance(point.get(metric), bool)
    ]
    if not values:
        return None
    selected = (min if mode == "min" else max)(
        values, key=lambda point: point[metric]
    )
    return {
        "metric": metric,
        "observed_at_utc": selected["observed_at_utc"],
        "observed_at_local": selected["observed_at_local"],
        "value": selected[metric],
    }


def _metric_extrema(
    series: list[dict[str, Any]],
) -> dict[str, dict[str, Any] | None]:
    metrics = (
        "visible_prefix_vp_count",
        "visible_prefix_vp_ratio",
        "visible_prefix_vp_delta",
        "visible_prefix_vp_ratio_delta_pp",
        "visible_origin_asn_count",
        "visible_origin_asn_delta",
        "fully_visible_asn_count",
        "partially_visible_asn_count",
        "fully_invisible_asn_count",
        "announce_count",
        "withdraw_count",
        "update_total",
        "withdraw_ratio",
        "announce_delta",
        "withdraw_delta",
        "country_announce_count",
        "country_withdraw_count",
        "country_update_total",
        "country_withdraw_ratio",
        "country_announce_delta",
        "country_withdraw_delta",
        "ipv4_visible_prefix_vp_count",
        "ipv6_visible_prefix_vp_count",
        "ipv4_visible_prefix_vp_ratio",
        "ipv6_visible_prefix_vp_ratio",
        "ipv4_visible_prefix_vp_delta",
        "ipv6_visible_prefix_vp_delta",
    )
    return {
        metric: {
            "min": _extreme(series, metric, mode="min"),
            "max": _extreme(series, metric, mode="max"),
        }
        for metric in metrics
    }


def _feature_time(
    value: Any,
    timezone_name: str = "Asia/Shanghai",
) -> tuple[str, str] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    display_timezone = _timezone(timezone_name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=display_timezone)
    local = parsed.astimezone(display_timezone)
    utc = parsed.astimezone(ZoneInfo("UTC"))
    return (
        utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        local.isoformat(timespec="seconds"),
    )


def _resource_series(
    raw_series: list[Mapping[str, Any]] | None,
    timezone_name: str = "Asia/Shanghai",
) -> list[dict[str, Any]]:
    if not raw_series:
        return []
    result: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for raw in sorted(raw_series, key=lambda item: str(item.get("time") or "")):
        observed = _feature_time(raw.get("time"), timezone_name)
        if observed is None:
            raise EventStoryUnavailable("Core 国家资源时序包含无法识别的时间")
        values = {
            "ipv4_24_equivalent_count": raw.get("v4Prefix_num"),
            "ipv6_48_equivalent_count": raw.get("v6Prefix_num"),
            "ipv4_address_count": raw.get("v4IP_num"),
            "announce_count": raw.get("announce"),
            "withdraw_count": raw.get("withdraw"),
        }
        if any(
            not isinstance(value, int) or isinstance(value, bool)
            for value in values.values()
        ):
            raise EventStoryUnavailable("Core 国家资源时序包含非整数指标")
        update_total = values["announce_count"] + values["withdraw_count"]
        point = {
            "observed_at_utc": observed[0],
            "observed_at_local": observed[1],
            **values,
            "update_total": update_total,
            "withdraw_ratio": (
                values["withdraw_count"] / update_total
                if update_total > 0
                else None
            ),
            "ipv4_24_equivalent_delta": _numeric_delta(
                values["ipv4_24_equivalent_count"],
                (
                    None
                    if previous is None
                    else previous["ipv4_24_equivalent_count"]
                ),
            ),
            "ipv6_48_equivalent_delta": _numeric_delta(
                values["ipv6_48_equivalent_count"],
                (
                    None
                    if previous is None
                    else previous["ipv6_48_equivalent_count"]
                ),
            ),
            "ipv4_address_delta": _numeric_delta(
                values["ipv4_address_count"],
                None if previous is None else previous["ipv4_address_count"],
            ),
            "announce_delta": _numeric_delta(
                values["announce_count"],
                None if previous is None else previous["announce_count"],
            ),
            "withdraw_delta": _numeric_delta(
                values["withdraw_count"],
                None if previous is None else previous["withdraw_count"],
            ),
        }
        result.append(point)
        previous = point
    return result


def _resource_extrema(
    series: list[dict[str, Any]],
) -> dict[str, dict[str, Any] | None]:
    metrics = (
        "ipv4_24_equivalent_count",
        "ipv6_48_equivalent_count",
        "ipv4_address_count",
        "announce_count",
        "withdraw_count",
        "update_total",
        "withdraw_ratio",
        "ipv4_24_equivalent_delta",
        "ipv6_48_equivalent_delta",
        "ipv4_address_delta",
        "announce_delta",
        "withdraw_delta",
    )
    return {
        metric: {
            "min": _extreme(series, metric, mode="min"),
            "max": _extreme(series, metric, mode="max"),
        }
        for metric in metrics
    }


def _country_update_series(
    series: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将共享状态流中的国家归属 UPDATE 活动投影为独立报文轨道。"""

    result: list[dict[str, Any]] = []
    for point in series:
        announce = point.get("country_announce_count")
        withdraw = point.get("country_withdraw_count")
        if not isinstance(announce, int) or not isinstance(withdraw, int):
            continue
        result.append(
            {
                "observed_at_utc": point["observed_at_utc"],
                "observed_at_local": point["observed_at_local"],
                "announce_count": announce,
                "withdraw_count": withdraw,
                "update_total": point.get("country_update_total"),
                "withdraw_ratio": point.get("country_withdraw_ratio"),
                "announce_delta": point.get("country_announce_delta"),
                "withdraw_delta": point.get("country_withdraw_delta"),
            }
        )
    return result


def get_country_outage_observation(
    *,
    registration: Mapping[str, Any],
    legacy_detail: Mapping[str, Any] | None = None,
    package_directory: str | os.PathLike[str] | None = None,
    resource_series: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """按注册记录返回国家事件的中性观测数据。"""

    legacy_reference = str(registration["legacy_reference"])
    country = registration.get("country")
    if not isinstance(country, Mapping):
        raise EventStoryUnavailable("国家事件注册记录缺少国家身份")
    country_code = str(country.get("code") or "")
    country_name = str(country.get("name") or country_code)
    timezone_name = str(
        registration.get("display_timezone") or "Asia/Shanghai"
    )
    package_uri = str(registration.get("package_uri") or "")
    if package_uri.startswith("file://"):
        package_uri = package_uri[7:]
    resolved_directory = Path(package_directory or package_uri)
    package = _load_package(str(resolved_directory), timezone_name)
    incident = package["incident"]
    cohort = package["cohort"]
    quality = package["quality"]
    complete = package["complete"]
    snapshots = package["snapshots"]
    detail = legacy_detail or {}
    registered_incident_id = registration.get("incident_id")
    if (
        registered_incident_id
        and incident.get("incident_id") != registered_incident_id
    ):
        raise EventStoryUnavailable("注册 incident ID 与交付包不一致")
    if incident.get("country_code") not in (None, "", country_code):
        raise EventStoryUnavailable("注册国家代码与交付包不一致")

    configured_collectors = registration.get("collector_ids")
    collector_ids = (
        [str(value) for value in configured_collectors if value]
        if isinstance(configured_collectors, list)
        else [str(incident.get("collector_id") or "")]
    )
    collector_ids = [value for value in collector_ids if value]
    interval_seconds = registration.get("interval_seconds")
    if not isinstance(interval_seconds, int) or interval_seconds <= 0:
        if len(snapshots) >= 2:
            first = datetime.fromisoformat(
                str(snapshots[0]["observed_at_utc"]).replace("Z", "+00:00")
            )
            second = datetime.fromisoformat(
                str(snapshots[1]["observed_at_utc"]).replace("Z", "+00:00")
            )
            interval_seconds = int((second - first).total_seconds())
        else:
            interval_seconds = 300

    baseline_origin_asn_count = cohort.get("baseline_origin_asn_count")
    baseline_prefix_vp_count = cohort.get("baseline_prefix_vp_count")
    if not isinstance(baseline_origin_asn_count, int) or not isinstance(
        baseline_prefix_vp_count, int
    ):
        raise EventStoryUnavailable("固定人口缺少 ASN 或 Prefix×VP 数量")

    actual_series = _observation_series(
        snapshots,
        baseline_origin_asn_count=baseline_origin_asn_count,
        baseline_prefix_vp_count=baseline_prefix_vp_count,
    )
    series, declared_missing_slots = _series_with_declared_gaps(
        actual_series,
        interval_seconds=interval_seconds,
        missing_slots=registration.get("missing_slots"),
        timezone_name=timezone_name,
    )
    start_snapshot = series[0]
    end_snapshot = series[-1]
    detected_snapshot = _find_snapshot(series, incident.get("detected_at"))
    extrema = _metric_extrema(series)
    resources = _resource_series(resource_series, timezone_name)
    resource_extrema = _resource_extrema(resources)
    country_updates = _country_update_series(series)
    country_update_extrema = _resource_extrema(country_updates)

    metric_definitions = [
        {
            "key": "visible_prefix_vp_count",
            "label": "可见 Prefix×VP",
            "unit": "Prefix×VP",
            "population": f"固定 {country_code} Prefix×VP cohort",
            "definition": "当前状态点仍可见的固定 Prefix×VP 成员数量。",
        },
        {
            "key": "visible_prefix_vp_ratio",
            "label": "Prefix×VP 可见率",
            "unit": "%",
            "population": f"固定 {country_code} Prefix×VP cohort",
            "definition": "可见 Prefix×VP 数量除以固定 Prefix×VP 人口。",
        },
        {
            "key": "visible_origin_asn_count",
            "label": "可见 origin ASN",
            "unit": "ASN",
            "population": f"固定 {country_code} origin ASN cohort",
            "definition": "当前状态点至少存在一条可见成员路由的固定 origin ASN 数量。",
        },
        {
            "key": "asn_visibility_state",
            "label": "ASN 可见状态",
            "unit": "五分钟状态槽",
            "population": f"固定 {country_code} origin ASN cohort",
            "definition": "同一 ASN 的地址族状态组合为全可见、部分可见、全不可见或未知。",
        },
        {
            "key": "ipv4_24_equivalent_count",
            "label": "IPv4 /24 等价资源块",
            "unit": "/24 等价资源块",
            "population": f"Core BGPFeature {country_name}国家资源聚合",
            "definition": "规范化、去重后的 IPv4 前缀覆盖的唯一 /24 等价资源块数量。",
        },
        {
            "key": "ipv6_48_equivalent_count",
            "label": "IPv6 /48 等价资源块",
            "unit": "/48 等价资源块",
            "population": f"Core BGPFeature {country_name}国家资源聚合",
            "definition": "规范化、去重后的 IPv6 前缀覆盖的唯一 /48 等价资源块数量。",
        },
        {
            "key": "ipv4_address_count",
            "label": "IPv4 地址资源量",
            "unit": "IPv4 地址",
            "population": f"Core BGPFeature {country_name}国家资源聚合",
            "definition": "IPv4 /24 等价资源块数量乘以 256。",
        },
        {
            "key": "country_update_counts",
            "label": f"{country_name}国家聚合 UPDATE 报文",
            "unit": f"条/{interval_seconds}秒",
            "population": (
                f"共享 RRC25 RouteState 中归属 {country_name} 的路由变化"
            ),
            "definition": (
                "ANNOUNCE 按新 origin ASN 的国家归属计数；"
                "WITHDRAW 按撤回前共享状态中的国家归属计数。"
                "该轨道不使用固定 cohort 作为报文筛选条件。"
            ),
        },
        {
            "key": "replay_update_counts",
            "label": "collector 全量 UPDATE 报文",
            "unit": f"条/{interval_seconds}秒",
            "population": (
                "Go 重放输入中的 "
                + "、".join(collector_ids)
                + " 槽内全部 UPDATE"
            ),
            "definition": (
                f"不使用固定 {country_code} cohort 作为分母，"
                "仅保留为独立观测轨道。"
            ),
        },
    ]

    annotations = [
        {
            "kind": "window_start",
            "metric": "visible_prefix_vp_count",
            "observed_at_utc": start_snapshot["observed_at_utc"],
            "observed_at_local": start_snapshot["observed_at_local"],
            "label": "观察窗口开始",
            "value": start_snapshot["visible_prefix_vp_count"],
            "unit": "Prefix×VP",
        },
        {
            "kind": "rule_first_met",
            "metric": "non_fully_visible_asn_count",
            "observed_at_utc": (
                None
                if detected_snapshot is None
                else detected_snapshot["observed_at_utc"]
            ),
            "observed_at_local": (
                None
                if detected_snapshot is None
                else detected_snapshot["observed_at_local"]
            ),
            "label": "规则首次满足",
            "value": (
                None
                if detected_snapshot is None
                else detected_snapshot["non_fully_visible_asn_count"]
            ),
            "unit": "ASN",
        },
        {
            "kind": "window_min",
            "metric": "visible_prefix_vp_count",
            **(extrema["visible_prefix_vp_count"]["min"] or {}),
            "label": "窗口最小值",
            "unit": "Prefix×VP",
        },
        {
            "kind": "largest_slot_drop",
            "metric": "visible_prefix_vp_delta",
            **(extrema["visible_prefix_vp_delta"]["min"] or {}),
            "label": "最大单槽下降",
            "unit": "Prefix×VP/五分钟",
        },
        {
            "kind": "window_end",
            "metric": "visible_prefix_vp_count",
            "observed_at_utc": end_snapshot["observed_at_utc"],
            "observed_at_local": end_snapshot["observed_at_local"],
            "label": "观察窗口结束",
            "value": end_snapshot["visible_prefix_vp_count"],
            "unit": "Prefix×VP",
        },
    ]
    configured_capabilities = registration.get("capabilities")
    configured_capabilities = (
        configured_capabilities
        if isinstance(configured_capabilities, Mapping)
        else {}
    )
    resource_capability = configured_capabilities.get("country_resources")
    if not isinstance(resource_capability, Mapping):
        resource_source = registration.get("resource_source")
        resource_capability = (
            {"state": "available"}
            if isinstance(resource_source, Mapping)
            and resource_source.get("state", "available") == "available"
            else {
                "state": "unavailable",
                "reason": "当前发布版本未配置国家资源轨道",
            }
        )
    capabilities: dict[str, dict[str, Any]] = {
        "legacy_summary": {
            "state": "available",
            "reason": "保留旧事实身份与口径对照",
        },
        "fixed_cohort": {"state": "available"},
        "country_resources": dict(resource_capability),
        "update_activity": {"state": "available"},
        "address_families": {"state": "available"},
        "asn_matrix": {"state": "available"},
        "audit": {"state": "available"},
        "normal_band": {
            "state": "unavailable",
            "reason": "缺少可信正常参照",
        },
    }
    for capability, configured in configured_capabilities.items():
        if (
            isinstance(capability, str)
            and isinstance(configured, Mapping)
            and configured.get("state")
            in {
                "available",
                "building",
                "unavailable",
                "not_applicable",
            }
        ):
            capabilities[capability] = dict(configured)

    return {
        "schema_version": "country_outage_observation_v2",
        "revision": int(registration.get("revision") or 1),
        "publication_state": str(
            registration.get("publication_state") or "published"
        ),
        "observation_state": str(
            registration.get("observation_state") or "state_complete"
        ),
        "data_mode": str(registration.get("data_mode") or "replay"),
        "data_through": (
            registration.get("data_through")
            or quality.get("last_observation_at")
        ),
        "updated_at": (
            registration.get("updated_at")
            or complete.get("completed_at")
        ),
        "is_final": bool(registration.get("is_final", True)),
        "capability_contract_version": "country_outage_capabilities_v1",
        "event_identity": {
            "incident_id": incident.get("incident_id"),
            "legacy_reference": legacy_reference,
            "legacy_record_time_local": _legacy_value(detail, "start_time"),
            "event_type": "country_outage",
            "country_code": country_code,
            "country_name": country_name,
            "display_name": str(
                registration.get("display_name")
                or f"{country_name} BGP 路由观测"
            ),
        },
        "observation_scope": {
            "collector_id": "、".join(collector_ids),
            "collector_ids": collector_ids,
            "collector_count": len(collector_ids),
            "vantage_point_count": registration.get(
                "vantage_point_count"
            ),
            "vantage_point_semantics": str(
                registration.get("vantage_point_semantics")
                or "窗口起点 route-state 中的唯一 VP 身份数"
            ),
            "window_start_utc": start_snapshot["observed_at_utc"],
            "window_start_local": start_snapshot["observed_at_local"],
            "window_end_utc": end_snapshot["observed_at_utc"],
            "window_end_local": end_snapshot["observed_at_local"],
            "timezone": timezone_name,
            "interval_seconds": interval_seconds,
            "observation_count": len(actual_series),
            "expected_observation_count": len(series),
            "missing_observation_count": len(declared_missing_slots),
            "quality_status": (
                "published_with_declared_gaps"
                if declared_missing_slots
                else quality.get("status")
            ),
            "last_observation_at_utc": quality.get("last_observation_at"),
            "last_observation_at_local": _local_time(
                quality.get("last_observation_at"), timezone_name
            ),
            "replay_completed_at_utc": complete.get("completed_at"),
            "replay_completed_at_local": _local_time(
                complete.get("completed_at"), timezone_name
            ),
            "left_boundary": "窗口开始前无本页同口径状态",
            "right_boundary": "窗口结束后无本页同口径状态",
        },
        "cohort": {
            "cohort_id": cohort.get("cohort_id"),
            "seed_observed_at_utc": cohort.get("seed_observed_at"),
            "seed_observed_at_local": _local_time(
                cohort.get("seed_observed_at"), timezone_name
            ),
            "origin_asn_count": baseline_origin_asn_count,
            "prefix_vp_count": baseline_prefix_vp_count,
            "ipv4_prefix_vp_count": start_snapshot.get(
                "ipv4_baseline_prefix_vp_count"
            ),
            "ipv6_prefix_vp_count": start_snapshot.get(
                "ipv6_baseline_prefix_vp_count"
            ),
            "mapping_version": cohort.get("mapping_version"),
            "denominator_policy": "fixed_from_complete_rib",
        },
        "normal_band": {
            "state": "unavailable",
            "label": "正常带不可用",
            "reason": "本次输入无法提供可信长期正常参照；页面只做窗口内统计。",
        },
        "rule_marker": {
            "metric": "non_fully_visible_asn_ratio",
            "threshold": 0.03,
            "consecutive_observation_count": 2,
            "interval_seconds": interval_seconds,
            "first_met_at_utc": incident.get("detected_at"),
            "first_met_at_local": _local_time(
                incident.get("detected_at"), timezone_name
            ),
        },
        "capabilities": capabilities,
        "metric_definitions": metric_definitions,
        "series": series,
        "metric_extrema": extrema,
        "resource_series": resources,
        "resource_metric_extrema": resource_extrema,
        "country_update_series": country_updates,
        "country_update_metric_extrema": country_update_extrema,
        "annotations": annotations,
        "asn_state": {
            "state_codes": {
                "-1": "unknown",
                "0": "fully_visible",
                "1": "partially_visible",
                "2": "fully_invisible",
            },
            "observed_at_utc": [
                point["observed_at_utc"] for point in series
            ],
            "observed_at_local": [
                point["observed_at_local"] for point in series
            ],
            "timelines": _asn_timelines_with_gaps(
                package["asn_state_timelines"],
                actual_times=[
                    point["observed_at_utc"] for point in actual_series
                ],
                published_times=[
                    point["observed_at_utc"] for point in series
                ],
            ),
        },
        "limitations": [
            (
                "仅代表 "
                + "、".join(collector_ids)
                + " 的 BGP 控制面观测。"
            ),
            "不包含用户流量、时延或服务可用性数据。",
            "正常带不可用，只展示当前观察窗口内的数值与排序。",
            "Core 资源时序是 /24、/48 等价资源块与 IPv4 地址量，不是唯一 BGP 前缀条目数。",
            "Prefix×VP 固定人口状态与 Core 国家资源聚合使用不同口径，不直接相减。",
            "窗口开始前和结束后的同口径状态不在本页覆盖范围内。",
            *(
                [
                    "时间轴中的缺槽已显式置空并断线；缺槽不表示零、正常或恢复。"
                ]
                if declared_missing_slots
                else []
            ),
        ],
        "audit": {
            "missing_slots": declared_missing_slots,
            "run_id": registration.get("run_id"),
            "artifact_set_id": (
                registration.get("artifact_set_id")
                or complete.get("artifact_set_id")
                or incident.get("incident_id")
            ),
            "engine_version": incident.get("algorithm_version"),
            "algorithm_version": incident.get("algorithm_version"),
            "mapping_version": cohort.get("mapping_version"),
            "quality_status": quality.get("status"),
            "source_system": "country_outage_observation_package",
            "source_table": (
                "country-snapshots.jsonl.gz + asn-states.jsonl.gz"
            ),
            "source_reference": (
                registration.get("artifact_set_id")
                or complete.get("artifact_set_id")
                or incident.get("incident_id")
            ),
            "evidence_level": "aggregated_route_state_with_artifact_hashes",
            "consumed_deliverable_hashes_verified": True,
            "verified_hashes": package["verified_hashes"],
            "route_state_file": {
                "filename": "route-states.jsonl.gz",
                "recorded_sha256": complete.get(
                    "deliverable_sha256", {}
                ).get("route-states.jsonl.gz"),
                "row_count": registration.get("route_state_row_count"),
                "request_path_scanned": False,
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


def get_iran_event_story(
    *,
    legacy_reference: str,
    legacy_detail: Mapping[str, Any] | None = None,
    package_directory: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """返回伊朗主事件的产品叙事；其他事件由 Legacy 页面继续承接。"""

    if legacy_reference != LEGACY_STORY_REFERENCE:
        return None
    resolved_directory = Path(
        package_directory
        or os.environ.get("DOMEYE_LEGACY_STORY_REPLAY_DIRECTORY")
        or LEGACY_STORY_REPLAY_DIRECTORY
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
            "vantage_point_count": LEGACY_STORY_VANTAGE_POINT_COUNT,
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
