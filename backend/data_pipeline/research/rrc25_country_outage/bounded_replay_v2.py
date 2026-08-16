"""固定 RRC25/IR 18:05–23:00 有界状态重放执行器。

真实执行严格读取一个 08:00 bview、25 个 catch-up UPDATE 和 59 个正式
UPDATE。模块没有运行时调参、软超时或数据库写入；所有结果先写同目录临时包，
完成后以原子 rename 发布。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from typing import Any, BinaryIO, Callable, Iterable, Mapping, Optional, Sequence

from ...route_event import ParsedRouteElement
from ...route_event.native_update import (
    make_native_update_record_stream_factory,
)
from .bounded_event_v2 import (
    canonical_json,
    derive_incident_episode_v2,
)
from .bounded_state_v2 import (
    AsnMilestoneTracker,
    FrozenCohort,
    freeze_ir_cohort,
    normal_band_from_catch_up,
    project_country_snapshot,
    summarize_slot_changes,
)
from .country_impact import (
    UNKNOWN,
    build_raw_retention_mapping_union,
    derive_origin_asns,
    mapping_bundle_sha256,
    mapping_view_from_frozen_snapshot,
    mapping_view_from_revised_snapshot,
)
from .rib_adapter import ObservedVpAccumulator, iter_rib_artifact_records
from .state_replay import (
    CONTINUOUS,
    ResearchRouteEvent,
    RouteReplayState,
    apply_streaming_update_batch,
    extend_streaming_rib_seed,
)
from .update_adapter import iter_adapted_update_records


UTC = timezone.utc
PACKAGE_SCHEMA_VERSION = "rrc25-iran-bounded-replay-package/v2"
QUALITY_SCHEMA_VERSION = "rrc25-iran-bounded-replay-quality/v2"
REPORT_FILENAME = "重放观察报告.md"
_WINDOW_START = "2026-02-28T10:05:00Z"
_WINDOW_END = "2026-02-28T15:00:00Z"
_CATCH_UP_START = "2026-02-28T08:00:00Z"
_EXPECTED_RIB_SHA = (
    "036e1a5b4d1554eae083d8b4d9de648f0ed95bfcd0ea781c4d001df68a23159c"
)
_EXPECTED_RIB_BYTES = 426_297_361
_EXPECTED_UPDATE_BYTES = 401_865_192
_EXPECTED_TOTAL_BYTES = 828_162_553
_LEGACY_REF = "country_outage/2026-02-27 09:12:32/IR/1/r"
_DETECTED_AT = "2026-02-27T01:12:32Z"


class BoundedReplayExecutionError(RuntimeError):
    """固定输入、解析、状态或包发布失败。"""


def _parse_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BoundedReplayExecutionError(f"{field_name} 必须是 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise BoundedReplayExecutionError(f"{field_name} 非法") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed) or parsed.microsecond:
        raise BoundedReplayExecutionError(f"{field_name} 必须是秒级 UTC")
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise BoundedReplayExecutionError(f"{field_name} 不是规范 UTC 时间")
    return parsed


def _artifact_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "artifact_id",
        "file_sha256",
        "collector_id",
        "artifact_type",
        "artifact_time_utc",
        "relative_path",
        "compression",
        "size_bytes",
    )
    if not isinstance(value, Mapping) or any(field not in value for field in fields):
        raise BoundedReplayExecutionError("artifact 字段不闭合")
    row = {field: value[field] for field in fields}
    if (
        row["collector_id"] != "rrc25"
        or row["compression"] != "gz"
        or not isinstance(row["size_bytes"], int)
        or isinstance(row["size_bytes"], bool)
        or row["size_bytes"] <= 0
    ):
        raise BoundedReplayExecutionError("artifact collector/compression/size 非法")
    _parse_utc(row["artifact_time_utc"], "artifact_time_utc")
    return row


def select_fixed_inputs(selection: Mapping[str, Any]) -> dict[str, Any]:
    """从既有 full-selection 中只选择固定 1+84 个制品。"""

    if not isinstance(selection, Mapping):
        raise BoundedReplayExecutionError("full-selection 必须是对象")
    roles = selection.get("roles")
    if not isinstance(roles, Mapping):
        raise BoundedReplayExecutionError("full-selection.roles 缺失")
    ribs = roles.get("analysis_ribs")
    updates = roles.get("analysis_updates")
    if not isinstance(ribs, list) or not isinstance(updates, list):
        raise BoundedReplayExecutionError("analysis_ribs/updates 必须是数组")
    rib_candidates = [
        _artifact_projection(row)
        for row in ribs
        if isinstance(row, Mapping)
        and row.get("artifact_time_utc") == _CATCH_UP_START
    ]
    if len(rib_candidates) != 1:
        raise BoundedReplayExecutionError("08:00 state seed bview 必须恰有一个")
    rib = rib_candidates[0]
    if (
        rib["artifact_type"] != "rib"
        or rib["file_sha256"] != _EXPECTED_RIB_SHA
        or rib["size_bytes"] != _EXPECTED_RIB_BYTES
        or rib["relative_path"]
        != "rrc25/2026.02/bview.20260228.0800.gz"
    ):
        raise BoundedReplayExecutionError("08:00 bview 身份不符合冻结需求")

    start = _parse_utc(_CATCH_UP_START, "catch_up_start")
    end = _parse_utc(_WINDOW_END, "window_end")
    selected_updates = sorted(
        (
            _artifact_projection(row)
            for row in updates
            if isinstance(row, Mapping)
            and start
            <= _parse_utc(row.get("artifact_time_utc"), "update time")
            < end
        ),
        key=lambda row: row["artifact_time_utc"],
    )
    expected_times = [
        (start + timedelta(minutes=5 * index)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for index in range(84)
    ]
    if [row["artifact_time_utc"] for row in selected_updates] != expected_times:
        raise BoundedReplayExecutionError("84 个 UPDATE 不连续或角色错误")
    if any(row["artifact_type"] != "update" for row in selected_updates):
        raise BoundedReplayExecutionError("UPDATE 选择混入其他制品类型")
    if sum(row["size_bytes"] for row in selected_updates) != _EXPECTED_UPDATE_BYTES:
        raise BoundedReplayExecutionError("84 个 UPDATE 压缩字节与冻结值不一致")
    catch_up = selected_updates[:25]
    formal = selected_updates[25:]
    if (
        len(catch_up) != 25
        or len(formal) != 59
        or catch_up[-1]["artifact_time_utc"] != "2026-02-28T10:00:00Z"
        or formal[0]["artifact_time_utc"] != _WINDOW_START
        or formal[-1]["artifact_time_utc"] != "2026-02-28T14:55:00Z"
    ):
        raise BoundedReplayExecutionError("catch-up/formal UPDATE 边界错误")
    return {
        "rib": rib,
        "catch_up_updates": catch_up,
        "formal_updates": formal,
    }


class _HashingReader:
    def __init__(self, raw: BinaryIO) -> None:
        self._raw = raw
        self._sha256 = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        block = self._raw.read(size)
        if block:
            self._sha256.update(block)
            self.bytes_read += len(block)
        return block

    def readable(self) -> bool:
        return True

    @property
    def sha256(self) -> str:
        return self._sha256.hexdigest()


def _open_regular_no_follow(path: Path) -> tuple[int, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as error:
        raise BoundedReplayExecutionError(f"输入文件不可读：{path}") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise BoundedReplayExecutionError("输入必须是非符号链接普通文件")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise BoundedReplayExecutionError(f"无法只读打开：{path}") from error
    opened = os.fstat(descriptor)
    identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, name) != getattr(opened, name) for name in identity):
        os.close(descriptor)
        raise BoundedReplayExecutionError("输入文件在打开前发生变化")
    return descriptor, opened


def _seed_from_rib(
    *,
    raw_root: Path,
    artifact: Mapping[str, Any],
    raw_retention_membership: Callable[[int], Optional[bool]],
) -> tuple[RouteReplayState, dict[str, Any]]:
    path = raw_root / str(artifact["relative_path"])
    descriptor, opened = _open_regular_no_follow(path)
    if opened.st_size != artifact["size_bytes"]:
        os.close(descriptor)
        raise BoundedReplayExecutionError("bview size 与 manifest 不一致")
    raw = os.fdopen(descriptor, "rb", closefd=True)
    hashing = _HashingReader(raw)
    accumulator = ObservedVpAccumulator("rrc25")
    state: Optional[RouteReplayState] = None
    batch: list[ResearchRouteEvent] = []
    physical_records = 0
    retained_events = 0
    try:
        with raw:
            with gzip.GzipFile(filename="", mode="rb", fileobj=hashing) as decoded:
                records = iter_rib_artifact_records(
                    decoded,
                    artifact=artifact,
                    origin_asn_predicate=(
                        lambda asn: raw_retention_membership(asn) is not False
                    ),
                    vp_observer=accumulator.observe,
                    include_discarded_element_decisions=False,
                )
                for record in records:
                    physical_records += 1
                    if record.route_events:
                        batch.extend(record.route_events)
                        retained_events += len(record.route_events)
                    if len(batch) >= 25_000:
                        state = extend_streaming_rib_seed(state, tuple(batch))
                        batch.clear()
                if batch:
                    state = extend_streaming_rib_seed(state, tuple(batch))
                    batch.clear()
    except BaseException:
        raise
    if state is None:
        raise BoundedReplayExecutionError("bview 未产生可回放路由")
    after = path.stat()
    identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(opened, name) != getattr(after, name) for name in identity):
        raise BoundedReplayExecutionError("bview 在读取期间发生变化")
    if hashing.bytes_read != artifact["size_bytes"]:
        raise BoundedReplayExecutionError("bview 压缩流未完整读取")
    if hashing.sha256 != artifact["file_sha256"]:
        raise BoundedReplayExecutionError("bview SHA256 与 manifest 不一致")
    return state, {
        "physical_record_count": physical_records,
        "retained_route_event_count": retained_events,
        "compressed_bytes_read": hashing.bytes_read,
        "file_sha256_verified": hashing.sha256,
        "observed_vp_count": accumulator.observed_vp_count,
    }


def _possible_target(
    element: ParsedRouteElement,
    membership: Callable[[int], Optional[bool]],
) -> bool:
    if element.action == "withdraw":
        return False
    if element.as_path is None:
        return True
    resolution = derive_origin_asns(element.as_path)
    if resolution.state == UNKNOWN:
        return True
    return any(membership(asn) is not False for asn in resolution.origins)


def _retention_selector(
    tracked_prefixes: set[str],
    membership: Callable[[int], Optional[bool]],
) -> Callable[[tuple[ParsedRouteElement, ...]], tuple[bool, ...]]:
    def select(elements: tuple[ParsedRouteElement, ...]) -> tuple[bool, ...]:
        canonical = {
            element.prefix: ipaddress.ip_network(
                element.prefix, strict=False
            ).compressed
            for element in elements
        }
        grouped: dict[str, list[ParsedRouteElement]] = {}
        for element in elements:
            grouped.setdefault(canonical[element.prefix], []).append(element)
        retained: set[str] = set()
        for prefix, values in grouped.items():
            possible = any(
                _possible_target(element, membership)
                for element in values
                if element.action == "announce"
            )
            if prefix in tracked_prefixes or possible:
                retained.add(prefix)
            if possible:
                tracked_prefixes.add(prefix)
        return tuple(canonical[element.prefix] in retained for element in elements)

    return select


def _read_update(
    state: RouteReplayState,
    *,
    raw_root: Path,
    artifact: Mapping[str, Any],
    tracked_prefixes: set[str],
    raw_retention_membership: Callable[[int], Optional[bool]],
) -> tuple[RouteReplayState, tuple[Any, ...], dict[str, int], dict[str, Any]]:
    factory = make_native_update_record_stream_factory(
        raw_root,
        [artifact],
        data_profile={
            "window_start_utc": _CATCH_UP_START,
            "window_end_exclusive_utc": _WINDOW_END,
        },
        pilot_limits={
            "max_artifact_count": 1,
            "max_compressed_bytes": int(artifact["size_bytes"]),
            "max_physical_records": 2_000_000,
            "max_route_events": 5_000_000,
            "max_spool_bytes": 16 * 1024 * 1024 * 1024,
        },
    )
    adapted = iter_adapted_update_records(
        factory(artifact),
        artifact=artifact,
        route_element_retention_selector=_retention_selector(
            tracked_prefixes,
            raw_retention_membership,
        ),
    )
    events: list[ResearchRouteEvent] = []
    counts = {
        "announce": 0,
        "withdraw": 0,
        "retained_announce": 0,
        "retained_withdraw": 0,
    }
    physical_records = 0
    for record in adapted:
        physical_records += 1
        counts["announce"] += record.announce_count
        counts["withdraw"] += record.withdraw_count
        events.extend(record.route_events)
    next_state, changes = apply_streaming_update_batch(state, tuple(events))
    retained = summarize_slot_changes(changes)
    counts.update(retained)
    statistics = factory.statistics_by_artifact.get(
        str(artifact["artifact_id"]), {}
    )
    return next_state, changes, counts, {
        "physical_record_count": physical_records,
        "retained_route_event_count": len(events),
        "parser_statistics": statistics,
        "counts": dict(counts),
    }


def _json_load(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BoundedReplayExecutionError(f"无法读取 JSON：{path}") from error


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        stream.write("\n")


class _JsonlGzipWriter:
    def __init__(self, path: Path) -> None:
        self._raw = path.open("xb")
        self._gzip = gzip.GzipFile(
            filename="", mode="wb", compresslevel=9, fileobj=self._raw, mtime=0
        )
        self.count = 0

    def write(self, value: Any) -> None:
        self._gzip.write(canonical_json(value).encode("utf-8") + b"\n")
        self.count += 1

    def close(self) -> None:
        self._gzip.close()
        self._raw.close()

    def __enter__(self) -> "_JsonlGzipWriter":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _catch_up_band(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    pairs = [
        {
            "visible_origin_asn_ratio": row["metrics"][
                "visible_origin_asn_ratio"
            ],
            "visible_prefix_vp_ratio": row["prefix_vp"]["visible_ratio"],
        }
        for row in rows
    ]
    band = normal_band_from_catch_up(pairs)
    affected = [
        row["metrics"]["affected_asn_ratio"]
        for row in rows
        if row["metrics"]["affected_asn_ratio"] is not None
    ]
    confirmed_anomaly = any(
        first > 0.03 and second > 0.03
        for first, second in zip(affected, affected[1:])
    )
    unstable = any(
        max(pair[metric] for pair in pairs)
        - min(pair[metric] for pair in pairs)
        > 0.03
        for metric in (
            "visible_origin_asn_ratio",
            "visible_prefix_vp_ratio",
        )
    )
    if confirmed_anomaly or unstable:
        band = None
    return band, {
        "state": "usable" if band is not None else "unknown",
        "slot_count": len(rows),
        "start_utc": "2026-02-28T08:00:00Z",
        "end_exclusive_utc": "2026-02-28T10:05:00Z",
        "confirmed_anomaly_in_catch_up": confirmed_anomaly,
        "unstable_over_detection_scale": unstable,
        "normal_band": band,
    }


def _dual_classification(row: Mapping[str, Any], asn: int) -> str:
    classes = row["dual_stack"]["classifications"]
    for name in ("fully_visible", "partially_visible", "fully_invisible", "unknown"):
        if asn in classes[name]:
            return name
    raise BoundedReplayExecutionError("双栈分类没有覆盖 cohort ASN")


def _report_context(
    *,
    observations: Sequence[Mapping[str, Any]],
    event_result: Mapping[str, Any],
    cohort: FrozenCohort,
    proxy: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    peak = max(
        observations,
        key=lambda row: (
            -1
            if row["metrics"]["affected_asn_ratio"] is None
            else row["metrics"]["affected_asn_ratio"],
            -observations.index(row),
        ),
    )
    earliest_damage_time = next(
        (
            row["observed_at"]
            for row in observations
            if row["dual_stack"]["affected_asns"]
        ),
        None,
    )
    first_damaged = (
        []
        if earliest_damage_time is None
        else next(
            row["dual_stack"]["affected_asns"]
            for row in observations
            if row["observed_at"] == earliest_damage_time
        )
    )
    recovered: set[int] = set()
    ever_damaged: set[int] = set()
    for row in observations:
        for asn in cohort.baseline_asns:
            state = _dual_classification(row, asn)
            if state in {"partially_visible", "fully_invisible"}:
                ever_damaged.add(asn)
            elif state == "fully_visible" and asn in ever_damaged:
                recovered.add(asn)
    final = observations[-1]
    claim_rows = []
    for label, numerator, denominator, semantics in (
        ("199/595", 199, 595, "报告受影响 ASN 主张"),
        ("73/126", 73, 126, "报告地址族分类主张"),
        ("176/556", 176, 556, "旧数据库峰值摘要"),
    ):
        exact = [
            row["observed_at"]
            for row in observations
            if (
                row["cohort"]["baseline_asn_count"] == denominator
                and row["metrics"]["affected_asn_count"] == numerator
            )
        ]
        claim_rows.append(
            {
                "claim": label,
                "semantics": semantics,
                "ratio": numerator / denominator,
                "same_denominator_as_replay": (
                    len(cohort.baseline_asns) == denominator
                ),
                "exact_affected_snapshot_matches": exact,
                "assessment": (
                    "同口径同快照匹配"
                    if exact
                    else "不等价：分母、分类语义或快照不一致"
                ),
            }
        )
    proxy_comparison: dict[str, Any] | None = None
    if proxy is not None:
        proxy_episodes = proxy.get("episodes")
        proxy_episode = (
            proxy_episodes[0]
            if isinstance(proxy_episodes, list) and proxy_episodes
            else {}
        )
        proxy_comparison = {
            "proxy_onset_at": proxy_episode.get("onset_at_utc"),
            "proxy_trough_at": (
                proxy_episode.get("trough", {}).get("observed_at_utc")
                if isinstance(proxy_episode.get("trough"), Mapping)
                else None
            ),
            "replay_onset_at": event_result["incident"]["onset_at"],
            "replay_peak_at": event_result["incident"]["peak_at"],
            "replay_trough_at": event_result["incident"]["trough_at"],
            "scope_note": (
                "数据库代理是 IPv4 聚合指标，逐点值不能当作 Prefix×VP 状态；"
                "这里只对账里程碑时间。"
            ),
        }
    return {
        "peak": peak,
        "first_damaged_at": earliest_damage_time,
        "first_damaged_asns": first_damaged,
        "peak_fully_invisible_asns": peak["dual_stack"]["classifications"][
            "fully_invisible"
        ],
        "peak_partially_visible_asns": peak["dual_stack"]["classifications"][
            "partially_visible"
        ],
        "recovered_asns": sorted(recovered),
        "unrecovered_asns": final["dual_stack"]["affected_asns"],
        "relapse_observed": len(event_result["waves"]) > len(event_result["episodes"]),
        "claim_reconciliation": claim_rows,
        "database_proxy_comparison": proxy_comparison,
    }


def _markdown_report(
    *,
    context: Mapping[str, Any],
    cohort: FrozenCohort,
    observations: Sequence[Mapping[str, Any]],
    event_result: Mapping[str, Any],
    input_summary: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> str:
    incident = event_result["incident"]
    peak = context["peak"]
    proxy = context.get("database_proxy_comparison")
    lines = [
        "# RRC25 伊朗事件 18:05–23:00 状态重放观察报告",
        "",
        "## 结论",
        "",
        (
            f"本次按固定半开窗口完成 1 个 bview、25 个 catch-up UPDATE 和 "
            f"59 个正式 UPDATE 的状态重放，输出 {len(observations)} 个状态观察点。"
        ),
        (
            f"08:00 bview 冻结的 IR 基线为 {len(cohort.baseline_asns)} 个 origin ASN、"
            f"{cohort.baseline_key_count} 个 Prefix×VP；18:05 起点实际可见 "
            f"{observations[0]['metrics']['visible_origin_asn_count']} 个基线 ASN。"
        ),
        "",
        "## 输入与口径",
        "",
        f"- 北京时间窗口：`[2026-02-28 18:05, 23:00)`。",
        f"- UTC 窗口：`[{_WINDOW_START}, {_WINDOW_END})`。",
        f"- 输入压缩字节：{input_summary['totals']['compressed_bytes']}。",
        f"- cohort：`{cohort.cohort_id}`，动态 IR origin 单独报告，不改变分母。",
        f"- 正常带状态：`{quality['normal_band']['state']}`。",
        "",
        "## 状态变化",
        "",
        f"- 首次观测受损：`{context['first_damaged_at']}`。",
        f"- 最先受损 ASN：`{context['first_damaged_asns']}`。",
        (
            f"- 最大受损快照：`{peak['observed_at']}`，"
            f"{peak['metrics']['affected_asn_count']}/"
            f"{peak['cohort']['baseline_asn_count']}。"
        ),
        f"- peak 完全不可见 ASN：`{context['peak_fully_invisible_asns']}`。",
        f"- peak 部分可见 ASN：`{context['peak_partially_visible_asns']}`。",
        f"- 窗口内曾恢复 ASN：`{context['recovered_asns']}`。",
        f"- 23:00 仍未恢复 ASN：`{context['unrecovered_asns']}`。",
        (
            f"- Episode/Wave：{len(event_result['episodes'])}/"
            f"{len(event_result['waves'])}；恢复后再次下降="
            f"{context['relapse_observed']}。"
        ),
        "",
        "## 结构化事件时间",
        "",
        "| 字段 | 值 |",
        "| --- | --- |",
    ]
    for field in (
        "detected_at",
        "onset_at",
        "peak_at",
        "trough_at",
        "partial_recovery_at",
        "full_recovery_at",
        "observation_end_at",
        "duration_state",
        "recovery_state",
    ):
        lines.append(f"| `{field}` | `{incident.get(field)}` |")
    lines.extend(
        [
            "",
            "## 报告与旧数据库数字对账",
            "",
            "| 主张 | 比例 | 本次评价 |",
            "| --- | ---: | --- |",
        ]
    )
    for row in context["claim_reconciliation"]:
        lines.append(
            f"| {row['claim']} | {row['ratio']:.3%} | {row['assessment']} |"
        )
    lines.extend(["", "## 数据库聚合曲线对账", ""])
    if proxy is None:
        lines.append(
            "本次未绑定数据库代理文件，因此不伪造逐点一致性结论；状态结果仍可用"
            " `observed_at` 与数据库五分钟曲线另行连接。"
        )
    else:
        lines.extend(
            [
                f"- 数据库代理 onset：`{proxy['proxy_onset_at']}`；"
                f"状态 onset：`{proxy['replay_onset_at']}`。",
                f"- 数据库代理 IPv4 trough：`{proxy['proxy_trough_at']}`；"
                f"状态 Prefix×VP trough：`{proxy['replay_trough_at']}`。",
                f"- {proxy['scope_note']}",
            ]
        )
    lines.extend(
        [
            "",
            "## 数据缺口与解释边界",
            "",
            "- 该结果只代表 RRC25 观测点集合，不代表全球所有观测点或实际流量。",
            "- 映射缺失、冲突、AS_SET 和无 origin 均进入 QUALITY，未被补成 IR 或 0。",
            "- 窗口止于 23:00；窗口外恢复不能回填本次 recovery 字段。",
            "- 事件时间先后不证明前兆导致主事件，也不能支持政治、物理线路或行为意图归因。",
            "",
        ]
    )
    return "\n".join(lines)


def run_fixed_replay(
    *,
    raw_root: Path,
    selection_path: Path,
    compatible_mapping_path: Path,
    revised_mapping_path: Path,
    output_directory: Path,
    database_proxy_path: Optional[Path] = None,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """执行一次固定真实重放并原子发布结果包。"""

    if output_directory.exists():
        raise BoundedReplayExecutionError("输出目录已存在；create-only 拒绝覆盖")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}.staging-",
            dir=output_directory.parent,
        )
    )
    started = time.monotonic()
    try:
        selection = _json_load(selection_path)
        selected = select_fixed_inputs(selection)
        compatible_snapshot = _json_load(compatible_mapping_path)
        revised_snapshot = _json_load(revised_mapping_path)
        compatible = mapping_view_from_frozen_snapshot(compatible_snapshot)
        revised = mapping_view_from_revised_snapshot(
            revised_snapshot, compatible_snapshot
        )
        raw_union = build_raw_retention_mapping_union((compatible, revised))
        mapping_version = mapping_bundle_sha256(
            compatible_snapshot, revised_snapshot
        )
        proxy = (
            None
            if database_proxy_path is None
            else _json_load(database_proxy_path)
        )

        progress("解析 08:00 bview 并冻结 IR cohort")
        state, rib_stats = _seed_from_rib(
            raw_root=raw_root,
            artifact=selected["rib"],
            raw_retention_membership=raw_union.raw_retention_membership,
        )
        cohort = freeze_ir_cohort(
            state.entries,
            target_membership=revised.target_membership,
            mapping_version=mapping_version,
            seed_observed_at=_CATCH_UP_START,
        )
        # RIB 是 08:00 状态边界；同秒 UPDATE 按阶段语义必须在 seed 之后
        # 应用，不能拿两个不同制品的 record ordinal 互相比大小。
        if any(
            _parse_utc(
                entry.last_event_time_utc, "RIB entry event time"
            )
            > _parse_utc(_CATCH_UP_START, "RIB boundary")
            for entry in state.entries
        ):
            raise BoundedReplayExecutionError(
                "seed RIB 包含晚于 08:00 状态边界的路由"
            )
        state = replace(state, last_order_key=None)
        tracked_prefixes = {
            key.prefix for key in cohort.baseline_entry_by_key
        }
        update_stats: list[dict[str, Any]] = []
        catch_up_rows: list[dict[str, Any]] = []

        progress("执行 25 个 catch-up UPDATE")
        for index, artifact in enumerate(selected["catch_up_updates"], start=1):
            state, changes, counts, stats = _read_update(
                state,
                raw_root=raw_root,
                artifact=artifact,
                tracked_prefixes=tracked_prefixes,
                raw_retention_membership=raw_union.raw_retention_membership,
            )
            update_stats.append(
                {"artifact": dict(artifact), "role": "catch_up", **stats}
            )
            slot_start = _parse_utc(
                artifact["artifact_time_utc"], "catch-up slot"
            )
            slot_end = slot_start + timedelta(minutes=5)
            observation, _asn, _route = project_country_snapshot(
                state.entries,
                cohort=cohort,
                target_membership=revised.target_membership,
                observed_at=slot_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                slot_start_utc=slot_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                slot_end_exclusive_utc=slot_end.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                slot_role="slot_end",
                continuity_state=state.continuity_state,
                update_counts=counts,
                slot_changes=changes,
                latest_changes=state.latest_changes,
            )
            catch_up_rows.append(observation)
            progress(f"catch-up {index}/25 完成")
        normal_band, normal_band_quality = _catch_up_band(catch_up_rows)

        observations: list[dict[str, Any]] = []
        all_asn_rows: list[dict[str, Any]] = []
        tracker = AsnMilestoneTracker()
        with (
            _JsonlGzipWriter(staging / "country-snapshots.jsonl.gz") as country_writer,
            _JsonlGzipWriter(staging / "asn-states.jsonl.gz") as asn_writer,
            _JsonlGzipWriter(staging / "route-states.jsonl.gz") as route_writer,
        ):
            initial_counts = {
                "announce": 0,
                "withdraw": 0,
                "retained_announce": 0,
                "retained_withdraw": 0,
            }
            initial, asn_rows, route_rows = project_country_snapshot(
                state.entries,
                cohort=cohort,
                target_membership=revised.target_membership,
                observed_at=_WINDOW_START,
                slot_start_utc=_WINDOW_START,
                slot_end_exclusive_utc=_WINDOW_START,
                slot_role="window_start",
                continuity_state=state.continuity_state,
                update_counts=initial_counts,
                milestone_tracker=tracker,
                latest_changes=state.latest_changes,
            )
            observations.append(initial)
            all_asn_rows.extend(asn_rows)
            country_writer.write(initial)
            for row in asn_rows:
                asn_writer.write(row)
            for row in route_rows:
                route_writer.write(row)

            progress("执行 59 个正式 UPDATE 并输出 60 个状态观察点")
            for index, artifact in enumerate(
                selected["formal_updates"], start=1
            ):
                state, changes, counts, stats = _read_update(
                    state,
                    raw_root=raw_root,
                    artifact=artifact,
                    tracked_prefixes=tracked_prefixes,
                    raw_retention_membership=(
                        raw_union.raw_retention_membership
                    ),
                )
                update_stats.append(
                    {"artifact": dict(artifact), "role": "formal", **stats}
                )
                slot_start = _parse_utc(
                    artifact["artifact_time_utc"], "formal slot"
                )
                slot_end = slot_start + timedelta(minutes=5)
                observation, asn_rows, route_rows = project_country_snapshot(
                    state.entries,
                    cohort=cohort,
                    target_membership=revised.target_membership,
                    observed_at=slot_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    slot_start_utc=slot_start.strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    slot_end_exclusive_utc=slot_end.strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    slot_role="slot_end",
                    continuity_state=state.continuity_state,
                    update_counts=counts,
                    milestone_tracker=tracker,
                    slot_changes=changes,
                    latest_changes=state.latest_changes,
                )
                observations.append(observation)
                all_asn_rows.extend(asn_rows)
                country_writer.write(observation)
                for row in asn_rows:
                    asn_writer.write(row)
                for row in route_rows:
                    route_writer.write(row)
                progress(f"正式窗口 {index}/59 完成")
            output_counts = {
                "country_snapshots": country_writer.count,
                "asn_states": asn_writer.count,
                "route_states": route_writer.count,
            }

        source_context = {
            "collector_id": "rrc25",
            "state_seed_rib": selected["rib"],
            "catch_up_update_range": {
                "start_utc": _CATCH_UP_START,
                "end_exclusive_utc": _WINDOW_START,
                "artifact_count": 25,
            },
            "formal_update_range": {
                "start_utc": _WINDOW_START,
                "end_exclusive_utc": _WINDOW_END,
                "artifact_count": 59,
            },
            "mapping_version": mapping_version,
            "normal_band": normal_band_quality,
        }
        event_result = derive_incident_episode_v2(
            observations,
            legacy_ref=_LEGACY_REF,
            detected_at=_DETECTED_AT,
            source="legacy_country_outage",
            country_code="IR",
            collector_id="rrc25",
            source_context=source_context,
            normal_band=normal_band,
        )
        input_summary = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "study_id": "iran-rrc25-1805-2300-state-replay-v1",
            "window": {
                "start_utc": _WINDOW_START,
                "end_exclusive_utc": _WINDOW_END,
                "boundary": "[start,end)",
                "state_observation_count": 60,
            },
            "inputs": {
                "state_seed_rib": selected["rib"],
                "catch_up_updates": selected["catch_up_updates"],
                "formal_updates": selected["formal_updates"],
            },
            "totals": {
                "rib_count": 1,
                "update_count": 84,
                "catch_up_update_count": 25,
                "formal_update_count": 59,
                "compressed_bytes": _EXPECTED_TOTAL_BYTES,
            },
            "parser": {
                "rib": rib_stats,
                "updates": update_stats,
            },
            "mapping": {
                "compatible_path": str(compatible_mapping_path),
                "revised_path": str(revised_mapping_path),
                "mapping_bundle_sha256": mapping_version,
                "statistical_view": "revised",
                "retention_view": "compatible_revised_union",
            },
            "database_proxy_path": (
                None if database_proxy_path is None else str(database_proxy_path)
            ),
        }
        context = _report_context(
            observations=observations,
            event_result=event_result,
            cohort=cohort,
            proxy=proxy,
        )
        quality = {
            "schema_version": QUALITY_SCHEMA_VERSION,
            "status": "pass",
            "input": {
                "rib_count": 1,
                "update_count": 84,
                "compressed_bytes": _EXPECTED_TOTAL_BYTES,
                "all_artifacts_complete": True,
                "continuity_state_at_end": state.continuity_state,
            },
            "output": {
                **output_counts,
                "expected_country_snapshots": 60,
                "last_observed_at": observations[-1]["observed_at"],
                "episode_count": len(event_result["episodes"]),
                "wave_count": len(event_result["waves"]),
            },
            "cohort": cohort.to_json()["quality"],
            "normal_band": normal_band_quality,
            "invariants": {
                "snapshot_count_is_60": len(observations) == 60,
                "last_state_is_2300_beijing": (
                    observations[-1]["observed_at"] == _WINDOW_END
                ),
                "all_continuous": all(
                    row["continuity_state"] == CONTINUOUS
                    for row in observations
                ),
                "same_cohort": len(
                    {row["cohort"]["cohort_id"] for row in observations}
                )
                == 1,
                "no_database_writes": True,
                "no_window_after_2300": True,
            },
            "limitations": [
                "RRC25 观测范围不是全球完整路由或业务流量。",
                "映射未知、冲突、AS_SET 与无 origin 未被补零。",
                "窗口外恢复未进入本次事件事实。",
                "不支持政治、物理线路或行为意图因果归因。",
            ],
            "elapsed_seconds_observed_only": time.monotonic() - started,
        }
        if not all(quality["invariants"].values()):
            raise BoundedReplayExecutionError("最终质量不变量失败")

        _write_json(staging / "input-summary.json", input_summary)
        _write_json(staging / "cohort.json", cohort.to_json())
        _write_json(staging / "incident-v2.json", event_result["incident"])
        with _JsonlGzipWriter(staging / "episodes.jsonl.gz") as writer:
            for row in event_result["episodes"]:
                writer.write(row)
        with _JsonlGzipWriter(staging / "waves.jsonl.gz") as writer:
            for row in event_result["waves"]:
                writer.write(row)
        _write_json(staging / "QUALITY.json", quality)
        _write_json(staging / "reconciliation.json", context)
        report = _markdown_report(
            context=context,
            cohort=cohort,
            observations=observations,
            event_result=event_result,
            input_summary=input_summary,
            quality=quality,
        )
        with (staging / REPORT_FILENAME).open(
            "x", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(report)

        inventory = []
        for path in sorted(staging.iterdir(), key=lambda item: item.name):
            if not path.is_file():
                raise BoundedReplayExecutionError("结果包出现非文件条目")
            inventory.append(
                {
                    "path": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
        manifest = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "status": "complete",
            "created_from_observation_end_at": _WINDOW_END,
            "files": inventory,
        }
        _write_json(staging / "MANIFEST.json", manifest)
        os.rename(staging, output_directory)
        return {
            "status": "complete",
            "output_directory": str(output_directory),
            "incident_id": event_result["incident"]["incident_id"],
            "cohort_id": cohort.cohort_id,
            "observation_count": len(observations),
            "episode_count": len(event_result["episodes"]),
            "wave_count": len(event_result["waves"]),
        }
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


__all__ = (
    "BoundedReplayExecutionError",
    "run_fixed_replay",
    "select_fixed_inputs",
)
