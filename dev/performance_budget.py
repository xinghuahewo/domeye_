#!/usr/bin/env python3
"""顺序测量固定数据开发 API，并按仓库内预算输出可审计 JSON。"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUDGET = ROOT / "config" / "performance-budget.json"


def nearest_rank_percentile(values: list[float], percentile: float) -> float:
    """返回最近秩百分位；输入毫秒值，空列表视为调用错误。"""

    if not values:
        raise ValueError("percentile values must not be empty")
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in (0, 1]")
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def load_budget(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("仅支持 performance budget schema_version=1")
    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        raise ValueError("性能预算必须包含 endpoints")
    endpoint_ids: set[str] = set()
    for endpoint in endpoints:
        endpoint_id = endpoint.get("id")
        if not isinstance(endpoint_id, str) or not endpoint_id:
            raise ValueError("每个性能预算端点都必须有 id")
        if endpoint_id in endpoint_ids:
            raise ValueError(f"性能预算端点 id 重复：{endpoint_id}")
        endpoint_ids.add(endpoint_id)
        if not str(endpoint.get("path", "")).startswith("/api/v1/"):
            raise ValueError(f"性能预算端点路径不在 /api/v1/：{endpoint_id}")
        for key in ("sample_count", "first_sample_max_ms", "warm_p95_max_ms"):
            value = endpoint.get(key)
            if not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{endpoint_id}.{key} 必须为正数")
        if endpoint["sample_count"] < 2:
            raise ValueError(f"{endpoint_id}.sample_count 至少为 2")
    return payload


def request_json(url: str, timeout_seconds: float) -> tuple[dict | list, float]:
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    started = time.perf_counter()
    with urlopen(request, timeout=timeout_seconds) as response:
        body = response.read()
        status = response.status
    elapsed_ms = (time.perf_counter() - started) * 1000
    if status != 200:
        raise RuntimeError(f"GET {url} 返回 HTTP {status}")
    payload = json.loads(body)
    if not isinstance(payload, (dict, list)):
        raise RuntimeError(f"GET {url} 未返回 JSON 对象或数组")
    return payload, elapsed_ms


def measure_endpoint(base_url: str, endpoint: dict, timeout_seconds: float) -> dict:
    url = f"{base_url.rstrip('/')}{endpoint['path']}"
    durations: list[float] = []
    for _ in range(int(endpoint["sample_count"])):
        _, elapsed_ms = request_json(url, timeout_seconds)
        durations.append(elapsed_ms)

    first_sample_ms = durations[0]
    warm_values = durations[1:]
    warm_p95_ms = nearest_rank_percentile(warm_values, 0.95)
    result = {
        "id": endpoint["id"],
        "label": endpoint["label"],
        "path": endpoint["path"],
        "sample_count": len(durations),
        "first_sample_ms": round(first_sample_ms, 3),
        "first_sample_max_ms": endpoint["first_sample_max_ms"],
        "warm_p95_ms": round(warm_p95_ms, 3),
        "warm_p95_max_ms": endpoint["warm_p95_max_ms"],
        "max_ms": round(max(durations), 3),
        "passed": (
            first_sample_ms <= endpoint["first_sample_max_ms"]
            and warm_p95_ms <= endpoint["warm_p95_max_ms"]
        ),
    }
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="例如 http://127.0.0.1:31629")
    parser.add_argument("--budget", type=Path, default=DEFAULT_BUDGET)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        budget = load_budget(args.budget)
        endpoint_results = [
            measure_endpoint(args.base_url, endpoint, float(budget["timeout_seconds"]))
            for endpoint in budget["endpoints"]
        ]
    except (HTTPError, URLError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"性能预算测量失败：{exc}", file=sys.stderr)
        return 2

    passed = all(item["passed"] for item in endpoint_results)
    report = {
        "schema_version": 1,
        "budget_id": budget["id"],
        "data_profile": budget["data_profile"],
        "base_url": args.base_url.rstrip("/"),
        "measurement_semantics": budget["measurement_semantics"],
        "measured_at_unix": int(time.time()),
        "endpoints": endpoint_results,
        "passed": passed,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
