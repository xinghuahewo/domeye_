#!/usr/bin/env python3
"""构造当前 TrendProduct 的确定性测试夹具。

本模块只复用当前趋势编译器和仍被测试消费的冻结输入，不恢复旧 S0-S6
验收脚本、阶段合同或文档。后端合同测试可直接导入构造函数；命令行默认及
``--emit-product`` 都向标准输出写出同一份 canonical JSON，供前端测试消费。
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.country_outage_trend_product import (  # noqa: E402
    CONTEMPORANEOUS_REFERENCE_INPUT_SCHEMA_VERSION,
    compile_contemporaneous_reference_v1,
    compile_trend_product_v1,
)
from services.country_outage_trend_profile import (  # noqa: E402
    align_activity_context_v1,
    analyze_trend_profile_v1,
    compare_address_families_v1,
    compile_asn_state_context_v1,
    compile_trend_profile_v1,
)


FIXTURE_ROOT = REPOSITORY_ROOT / "dev" / "fixtures"
S0_FIXTURE_PATH = FIXTURE_ROOT / "country-outage-trend-analysis-s0-v1.json"
S1_FIXTURE_PATH = FIXTURE_ROOT / "country-outage-trend-analysis-s1-v1.json"
S3_FIXTURE_PATH = FIXTURE_ROOT / "country-outage-trend-analysis-s3-v1.json"
S4_FIXTURE_PATH = FIXTURE_ROOT / "country-outage-trend-analysis-s4-v1.json"
S5_FIXTURE_PATH = FIXTURE_ROOT / "country-outage-trend-analysis-s5-v1.json"


def load_json(path: Path) -> dict[str, Any]:
    """读取一个对象型 JSON 测试输入。"""

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须为对象：{path}")
    return value


def canonical_json(value: Any) -> str:
    """返回稳定、无多余空白的 UTF-8 JSON 文本。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    """计算 canonical JSON 的 SHA-256。"""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _curve_by_id(source: dict[str, Any], curve_id: str) -> dict[str, Any]:
    for curve in source["acceptance_curves"]:
        if curve["id"] == curve_id:
            return curve
    raise KeyError(curve_id)


def _baseline_input(
    baseline_type: str,
    *,
    foundation: dict[str, Any],
) -> dict[str, Any]:
    baseline: dict[str, Any] = {"type": baseline_type}
    if baseline_type == "contemporaneous_reference":
        baseline.update(
            {
                "value": 97,
                "unit": "count",
                "statistical_population": "fixed_prefix_vp",
                "reference_id": "reference_s1_same_grid",
                "reference_time_grid": {
                    "slot_seconds": 300,
                    "expected_slot_count": 12,
                    "window_start_utc": foundation["snapshot_template"]
                    ["window_start_utc"],
                    "window_end_utc": foundation["snapshot_template"]
                    ["window_end_utc"],
                },
            }
        )
    return baseline


def _request_for_curve(
    curves: dict[str, Any],
    foundation: dict[str, Any],
    curve_id: str,
) -> dict[str, Any]:
    curve = _curve_by_id(curves, curve_id)
    start = datetime.fromisoformat(
        foundation["snapshot_template"]["window_start_utc"].replace(
            "Z", "+00:00"
        )
    )
    metric = deepcopy(foundation["metric_template"])
    metric["denominator"] = {
        "value": curve["denominator"],
        "unit": "count",
        "statistical_population": metric["statistical_population"],
    }
    slots = []
    for slot in curve["slots"]:
        observed_at = start + timedelta(seconds=slot["offset_seconds"])
        slots.append(
            {
                "index": slot["index"],
                "observed_at_utc": observed_at.astimezone(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "state": slot["state"],
                "value": slot["value"],
                "source_ref": (
                    "dev/fixtures/country-outage-trend-analysis-s0-v1.json#"
                    f"/acceptance_curves/{int(curve_id[-2:]) - 1}/slots/"
                    f"{slot['index']}"
                ),
            }
        )
    return {
        "schema_version": "country_outage_trend_profile_input_v1",
        "snapshot": deepcopy(foundation["snapshot_template"]),
        "metric": metric,
        "time_grid": {"slot_seconds": 300, "expected_slot_count": 12},
        "baseline": _baseline_input(
            curve["baseline_type"], foundation=foundation
        ),
        "slots": slots,
    }


def _analyzed_curve(
    curves: dict[str, Any],
    foundation: dict[str, Any],
    curve_id: str,
    *,
    scale: int = 1,
    population: str = "fixed_prefix_vp",
) -> dict[str, Any]:
    request = _request_for_curve(curves, foundation, curve_id)
    request["metric"]["statistical_population"] = population
    request["metric"]["denominator"]["statistical_population"] = population
    request["metric"]["denominator"]["value"] *= scale
    for slot in request["slots"]:
        if slot["value"] is not None:
            slot["value"] *= scale
    return analyze_trend_profile_v1(compile_trend_profile_v1(request))


def _build_address_family_profiles(
    curves: dict[str, Any],
    foundation: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    profiles = []
    for family in ("ipv4", "ipv6"):
        case = context["address_family_case"][family]
        profiles.append(
            _analyzed_curve(
                curves,
                foundation,
                case["curve_id"],
                scale=case["scale"],
                population=case["statistical_population"],
            )
        )
    return profiles[0], profiles[1]


def _build_asn_rows(context: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in context["asn_case"]["rows"]:
        rows.append(
            {
                "asn": row["asn"],
                "address_family": row["address_family"],
                "baseline_prefix_vp_count": row[
                    "baseline_prefix_vp_count"
                ],
                "slots": [
                    {
                        "index": index,
                        "state": state,
                        "source_ref": (
                            "dev/fixtures/"
                            "country-outage-trend-analysis-s3-v1.json#"
                            f"/asn/{row['asn']}/{row['address_family']}/{index}"
                        ),
                    }
                    for index, state in enumerate(row["states"])
                ],
            }
        )
    return rows


def _build_activity_tracks(context: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = []
    for track in context["activity_case"]["tracks"]:
        tracks.append(
            {
                "track_id": track["track_id"],
                "metric_id": track["metric_id"],
                "unit": "count",
                "statistical_population": track["statistical_population"],
                "slots": [
                    {
                        "index": index,
                        "state": "observed",
                        "value": value,
                        "source_ref": (
                            "dev/fixtures/"
                            "country-outage-trend-analysis-s3-v1.json#"
                            f"/activity/{track['track_id']}/{index}"
                        ),
                    }
                    for index, value in enumerate(track["values"])
                ],
            }
        )
    return tracks


def build_contemporaneous_reference_input(
    profile: dict[str, Any],
    fixture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造与给定 TrendProfile 同身份、同时间网格的同期参照输入。"""

    frozen = fixture or load_json(S5_FIXTURE_PATH)
    times = [slot["observed_at_utc"] for slot in profile["slots"]]
    projections = []
    for item in frozen["projections"]:
        slots = [
            {
                "index": index,
                "observed_at_utc": observed_at,
                "state": (
                    "observed" if value is not None else "processing_gap"
                ),
                "visible_prefix_vp_count": value,
            }
            for index, (observed_at, value) in enumerate(
                zip(times, item["values"])
            )
        ]
        projections.append(
            {
                key: deepcopy(value)
                for key, value in item.items()
                if key != "values"
            }
            | {
                "slots": slots,
                "source_refs": [
                    "rrc25-global-country-packages:/countries/"
                    f"{item['country_code']}/country-snapshots.jsonl.gz"
                ],
            }
        )
    return {
        "schema_version": CONTEMPORANEOUS_REFERENCE_INPUT_SCHEMA_VERSION,
        "identity": deepcopy(frozen["reference_identity"]),
        "time_grid": {
            "slot_seconds": profile["time_grid"]["slot_seconds"],
            "expected_slot_count": profile["time_grid"][
                "expected_slot_count"
            ],
            "observed_at_utc": times,
        },
        "target_country_code": profile["snapshot"]["country_code"],
        "country_and_unknown_bucket_count": len(projections),
        "projections": projections,
    }


def build_country_outage_trend_product() -> dict[str, Any]:
    """构造当前 API、后端合同测试与前端组件共享的 TrendProduct。"""

    curves = load_json(S0_FIXTURE_PATH)
    foundation = load_json(S1_FIXTURE_PATH)
    context = load_json(S3_FIXTURE_PATH)
    product_fixture = load_json(S4_FIXTURE_PATH)
    reference_fixture = load_json(S5_FIXTURE_PATH)

    profile = _analyzed_curve(
        curves,
        foundation,
        product_fixture["profile_curve_id"],
    )
    ipv4, ipv6 = _build_address_family_profiles(
        curves, foundation, context
    )
    base = compile_trend_product_v1(
        profile,
        address_family_context=compare_address_families_v1(ipv4, ipv6),
        asn_context=compile_asn_state_context_v1(
            profile, _build_asn_rows(context)
        ),
        activity_context=align_activity_context_v1(
            profile, _build_activity_tracks(context)
        ),
    )
    reference = compile_contemporaneous_reference_v1(
        base["profile"],
        build_contemporaneous_reference_input(
            base["profile"], reference_fixture
        ),
    )
    return compile_trend_product_v1(
        base["profile"],
        address_family_context=base["contexts"]["address_family"],
        asn_context=base["contexts"]["asn"],
        activity_context=base["contexts"]["activity"],
        contemporaneous_reference_context=reference,
    )


def build_country_outage_trend_resources() -> tuple[
    dict[str, Any], dict[str, Any], list[dict[str, Any]]
]:
    """构造资源适配器测试使用的 overview、series 与 ASN 页面。"""

    profile = build_country_outage_trend_product()["profile"]
    snapshot = profile["snapshot"]
    common = {
        "incident_id": snapshot["incident_id"],
        "publication_id": snapshot["publication_id"],
        "revision": snapshot["revision"],
        "data_through": snapshot["data_through"],
        "cohort_id": "cohort-current-trend-api",
        "window_start_utc": snapshot["window_start_utc"],
        "window_end_utc": snapshot["window_end_utc"],
        "is_final": snapshot["is_final"],
    }
    overview = {
        "schema_version": "country_outage_overview_v2",
        **common,
        "event_identity": {
            "incident_id": snapshot["incident_id"],
            "legacy_reference": snapshot["event_reference"],
            "event_type": "country_outage",
            "country_code": snapshot["country_code"],
            "country_name": "验收国家",
            "display_name": "验收国家国家中断",
        },
        "observation_scope": {
            "collector_id": "rrc25",
            "collector_ids": ["rrc25"],
            "collector_count": 1,
            "window_start_utc": snapshot["window_start_utc"],
            "window_end_utc": snapshot["window_end_utc"],
            "timezone": snapshot["timezone"],
            "interval_seconds": profile["time_grid"]["slot_seconds"],
        },
        "cohort": {
            "cohort_id": "cohort-current-trend-api",
            "prefix_vp_count": profile["metric"]["denominator"]["value"],
        },
        "capabilities": {"trend_analysis": {"state": "available"}},
    }
    series = {
        "schema_version": "country_outage_series_v2",
        **common,
        "interval_seconds": profile["time_grid"]["slot_seconds"],
        "series": [
            {
                "observed_at_utc": slot["observed_at_utc"],
                "slot_state": slot["state"],
                "visible_prefix_vp_count": slot["value"],
                "update_total": index * 10,
                "announce_count": index * 7,
                "withdraw_count": index * 3,
            }
            for index, slot in enumerate(profile["slots"])
        ],
    }
    slot_count = len(profile["slots"])
    asn_page = {
        "schema_version": "country_outage_asn_page_v2",
        **common,
        "page": 1,
        "page_count": 1,
        "items": [
            {
                "asn": "64500",
                "address_families": [4, 6],
                "baseline_prefix_count": 2,
                "baseline_prefix_vp_count": 8,
                "states": [0, *([1] * (slot_count - 2)), 0],
            }
        ],
    }
    return overview, series, [asn_page]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="输出当前测试共享的确定性 TrendProduct"
    )
    parser.add_argument(
        "--emit-product",
        action="store_true",
        help="兼容调用方；默认也输出 canonical TrendProduct JSON",
    )
    parser.parse_args(argv)
    print(canonical_json(build_country_outage_trend_product()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
