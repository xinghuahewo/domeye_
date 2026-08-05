#!/usr/bin/env python3
"""为 S6 浏览器验收提供同一冻结候选的只读本地 API。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = REPOSITORY_ROOT / "dev" / "verify_country_outage_trend_analysis_s6.py"


def load_verifier():
    specification = importlib.util.spec_from_file_location(
        "s6_verifier_for_browser", VERIFIER_PATH
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("无法加载 S6 验收器")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def shanghai_time(utc_text: str) -> str:
    value = datetime.fromisoformat(utc_text.replace("Z", "+00:00"))
    return value.astimezone(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def acceptance_resources() -> dict[str, Any]:
    verifier = load_verifier()
    product = verifier.build_candidate()
    profile = product["profile"]
    snapshot = product["snapshot"]
    cohort_id = "cohort-trend-s6-browser"
    common = {
        "revision": snapshot["revision"],
        "publication_id": snapshot["publication_id"],
        "publication_state": "published",
        "observation_state": "state_complete",
        "data_mode": "replay",
        "data_through": snapshot["data_through"],
        "updated_at": snapshot["data_through"],
        "is_final": True,
        "processing_status": {
            "state": "final",
            "updated_at": snapshot["data_through"],
            "attempted_through": snapshot["data_through"],
            "reason": None,
            "last_complete_data_through": snapshot["data_through"],
        },
        "missing_slot_count": 0,
        "incident_id": snapshot["incident_id"],
        "cohort_id": cohort_id,
        "window_start_utc": snapshot["window_start_utc"],
        "window_end_utc": snapshot["window_end_utc"],
        "capability_contract_version": "country_outage_capabilities_v1",
    }
    values = [slot["value"] for slot in profile["slots"]]
    series = []
    for index, slot in enumerate(profile["slots"]):
        previous = values[index - 1] if index else None
        series.append(
            {
                "observed_at_utc": slot["observed_at_utc"],
                "observed_at_local": shanghai_time(slot["observed_at_utc"]),
                "slot_state": "observed",
                "missing_reason": None,
                "visible_prefix_vp_count": slot["value"],
                "visible_prefix_vp_ratio": slot["value"] / 100,
                "visible_prefix_vp_delta": (
                    None if previous is None else slot["value"] - previous
                ),
                "visible_prefix_vp_ratio_delta_pp": (
                    None if previous is None else slot["value"] - previous
                ),
            }
        )
    minimum_index = min(range(len(series)), key=lambda index: series[index]["visible_prefix_vp_count"])
    maximum_index = max(range(len(series)), key=lambda index: series[index]["visible_prefix_vp_count"])

    def extrema_point(metric: str, index: int) -> dict[str, Any]:
        slot = series[index]
        return {
            "metric": metric,
            "value": slot[metric],
            "observed_at_utc": slot["observed_at_utc"],
            "observed_at_local": slot["observed_at_local"],
            "slot_index": index,
        }

    overview = {
        "schema_version": "country_outage_overview_v2",
        **common,
        "event_identity": {
            "incident_id": snapshot["incident_id"],
            "legacy_reference": profile["snapshot"]["event_reference"],
            "event_type": "country_outage",
            "country_code": snapshot["country_code"],
            "country_name": "验收国家",
            "display_name": "验收国家 RRC25 路由观测",
        },
        "observation_scope": {
            "collector_id": "rrc25",
            "collector_ids": ["rrc25"],
            "collector_count": 1,
            "window_start_utc": snapshot["window_start_utc"],
            "window_start_local": shanghai_time(snapshot["window_start_utc"]),
            "window_end_utc": snapshot["window_end_utc"],
            "window_end_local": shanghai_time(snapshot["window_end_utc"]),
            "timezone": "Asia/Shanghai",
            "interval_seconds": profile["time_grid"]["slot_seconds"],
            "observation_count": len(series),
            "expected_observation_count": len(series),
            "missing_observation_count": 0,
            "quality_status": "pass",
            "last_observation_at_utc": snapshot["data_through"],
            "last_observation_at_local": shanghai_time(snapshot["data_through"]),
            "right_boundary": "窗口结束后无本页同口径状态",
        },
        "cohort": {
            "cohort_id": cohort_id,
            "denominator_policy": "fixed_prefix_vp",
            "origin_asn_count": 5,
            "prefix_vp_count": 100,
            "ipv4_prefix_vp_count": 90,
            "ipv6_prefix_vp_count": 10,
        },
        "normal_band": {"state": "unavailable", "reason": "没有历史正常基线"},
        "rule_marker": None,
        "capabilities": {
            "fixed_cohort": {"state": "available"},
            "asn_matrix": {"state": "unavailable", "reason": "S6 页面只验收冻结趋势制品"},
            "address_families": {"state": "unavailable", "reason": "由趋势制品上下文呈现"},
            "update_activity": {"state": "unavailable", "reason": "由趋势制品上下文呈现"},
            "country_resources": {"state": "unavailable", "reason": "不是本次页面入口"},
            "normal_band": {"state": "unavailable", "reason": "没有历史正常基线"},
        },
        "legacy_summary": None,
        "limitations": [
            "仅描述 RRC25 BGP 控制面观测。",
            "不判断原因、攻击、用户影响、责任或窗口外完全恢复。",
        ],
    }
    series_resource = {
        "schema_version": "country_outage_series_v2",
        **common,
        "interval_seconds": profile["time_grid"]["slot_seconds"],
        "metric_definitions": [
            {
                "metric": "visible_prefix_vp_count",
                "label": "固定人口可见 Prefix×VP 数量",
                "unit": "count",
                "statistical_population": "fixed_prefix_vp",
            }
        ],
        "series": series,
        "metric_extrema": {
            "visible_prefix_vp_count": {
                "min": extrema_point("visible_prefix_vp_count", minimum_index),
                "max": extrema_point("visible_prefix_vp_count", maximum_index),
            },
            "visible_prefix_vp_ratio": {
                "min": extrema_point("visible_prefix_vp_ratio", minimum_index),
                "max": extrema_point("visible_prefix_vp_ratio", maximum_index),
            },
        },
        "resource_series": [],
        "resource_metric_extrema": {},
        "country_update_series": [],
        "country_update_metric_extrema": {},
        "annotations": [],
    }
    asns = {
        "schema_version": "country_outage_asn_page_v2",
        **common,
        "page": 1,
        "page_size": 60,
        "page_count": 0,
        "total": 0,
        "observed_at_utc": [slot["observed_at_utc"] for slot in series],
        "observed_at_local": [slot["observed_at_local"] for slot in series],
        "state_codes": {
            "fully_visible": 0,
            "partially_visible": 1,
            "fully_invisible": 2,
            "unknown": 3,
        },
        "duration_histogram": {},
        "items": [],
    }
    audit = {
        "schema_version": "country_outage_audit_v2",
        **common,
        "engine_version": "country-outage-trend-s6-browser-v1",
        "algorithm_version": product["algorithm_version"],
        "mapping_version": "synthetic-s6",
        "quality_status": "pass",
        "source_system": "s6_frozen_acceptance",
        "source_table": "none",
        "source_reference": str(verifier.FIXTURE_PATH.relative_to(REPOSITORY_ROOT)),
        "evidence_level": "deterministic_frozen_candidate",
        "consumed_deliverable_hashes_verified": True,
        "verified_hashes": {"trend_product.json": verifier.canonical_sha256(product)},
        "route_state_file": {"state": "not_used", "reason": "冻结合成候选"},
        "input_summary": {"slot_count": len(series), "collector_id": "rrc25"},
        "missing_slots": [],
    }
    resolution = {
        "schema_version": "country_outage_resolution_v2",
        "incident_id": snapshot["incident_id"],
        "publication_id": snapshot["publication_id"],
        "legacy_reference": profile["snapshot"]["event_reference"],
        "event_type": "country_outage",
        "observation_state": "state_complete",
        "latest_revision": snapshot["revision"],
        "data_mode": "replay",
        "data_through": snapshot["data_through"],
        "is_final": True,
        "missing_slot_count": 0,
        "capability_contract_version": "country_outage_capabilities_v1",
        "capabilities": overview["capabilities"],
    }
    return {
        "resolution": resolution,
        "overview": overview,
        "series": series_resource,
        "asns": asns,
        "audit": audit,
        "trend": product,
    }


RESOURCES = acceptance_resources()


class AcceptanceHandler(BaseHTTPRequestHandler):
    server_version = "DomeyeTrendS6Acceptance/1"

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        if path in {"/healthz", "/api/v1/healthz"}:
            self.respond(
                {
                    "status": "ok",
                    "time": datetime.now(timezone.utc).isoformat(),
                    "product_id": RESOURCES["trend"]["product_id"],
                }
            )
            return
        if path == "/api/v2/events/resolve":
            self.respond(RESOURCES["resolution"])
            return
        suffix_map = {
            "/overview": "overview",
            "/series": "series",
            "/asns": "asns",
            "/audit": "audit",
            "/trend": "trend",
        }
        if path.startswith("/api/v2/country-outages/"):
            for suffix, key in suffix_map.items():
                if path.endswith(suffix):
                    self.respond(RESOURCES[key])
                    return
        self.respond({"status": False, "msg": "S6 本地验收端点不存在"}, status=404)

    def respond(self, payload: Any, *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[S6 API] " + format % args + "\n")


def main() -> int:
    host = "127.0.0.1"
    port = 28573
    server = ThreadingHTTPServer((host, port), AcceptanceHandler)
    print(
        json.dumps(
            {
                "status": "serving",
                "url": f"http://{host}:{port}",
                "product_id": RESOURCES["trend"]["product_id"],
                "scope": "S6 隔离候选；不是生产",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
