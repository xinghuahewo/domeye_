#!/usr/bin/env python3
"""P0 数据基础 R 轨 R3：原始槽补齐工具。

默认 **dry-run**：只生成确定性缺口清单并抽样核验上游可得性，不下载任何字节。
实际补取必须显式传入 `--execute`，且由项目方授权后执行（约 60 GB 外部下载）。

槽位口径来自 `config/data-profile.json`：窗口为
`2026-02-01T00:00:00+08:00 <= t < 2026-04-01T00:00:00+08:00`，UPDATE 每 5 分钟一槽、
RIB 每 8 小时一槽。RIPE RIS 文件名使用 UTC。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from typing import Any

BASE_URL = "https://data.ris.ripe.net/rrc25"
BEIJING = dt.timezone(dt.timedelta(hours=8))
UPDATE_STEP_MINUTES = 5
RIB_STEP_HOURS = 8


def _profile(repo_root: str) -> dict[str, Any]:
    with open(os.path.join(repo_root, "config", "data-profile.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _parse(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


def enumerate_slots(window_start: dt.datetime, window_end: dt.datetime) -> dict[str, list[str]]:
    """按 UTC 生成窗口内全部 UPDATE / RIB 槽的规范文件名。"""
    updates: list[str] = []
    ribs: list[str] = []

    cur = window_start.astimezone(dt.timezone.utc)
    end = window_end.astimezone(dt.timezone.utc)
    # UPDATE：对齐到 5 分钟边界
    cur = cur.replace(minute=(cur.minute // UPDATE_STEP_MINUTES) * UPDATE_STEP_MINUTES,
                      second=0, microsecond=0)
    while cur < end:
        updates.append(f"{cur:%Y.%m}/updates.{cur:%Y%m%d.%H%M}.gz")
        cur += dt.timedelta(minutes=UPDATE_STEP_MINUTES)

    cur = window_start.astimezone(dt.timezone.utc).replace(
        hour=(window_start.astimezone(dt.timezone.utc).hour // RIB_STEP_HOURS) * RIB_STEP_HOURS,
        minute=0, second=0, microsecond=0,
    )
    while cur < end:
        ribs.append(f"{cur:%Y.%m}/bview.{cur:%Y%m%d.%H%M}.gz")
        cur += dt.timedelta(hours=RIB_STEP_HOURS)

    return {"updates": updates, "ribs": ribs}


def local_present(data_root: str, relative: str) -> bool:
    return os.path.isfile(os.path.join(data_root, relative))


def head(url: str, timeout: int = 20) -> tuple[int, int | None]:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            return response.status, int(length) if length else None
    except Exception:  # 网络或 404 均视为不可得，交由调用方登记
        return 0, None


def main() -> int:
    parser = argparse.ArgumentParser(description="R3 原始槽补齐（默认 dry-run）")
    parser.add_argument("--repo-root", default=".", help="core-work 仓库根")
    parser.add_argument("--data-root", default="/home/bgpdata/data/ripe/rrc25")
    parser.add_argument("--sample", type=int, default=12, help="dry-run 时抽样核验的槽数")
    parser.add_argument("--output", default="/tmp/p0_r3_gap_manifest.json")
    parser.add_argument(
        "--execute", action="store_true",
        help="实际下载缺口制品（约 60 GB）。未经项目方授权不得使用。",
    )
    args = parser.parse_args()

    profile = _profile(args.repo_root)
    start = _parse(profile["window_start"])
    end = _parse(profile["window_end_exclusive"])
    slots = enumerate_slots(start, end)

    missing = {
        "updates": [s for s in slots["updates"] if not local_present(args.data_root, s)],
        "ribs": [s for s in slots["ribs"] if not local_present(args.data_root, s)],
    }

    report: dict[str, Any] = {
        "schema_version": "domeye_p0_raw_slot_backfill/v1",
        "data_profile_id": profile["id"],
        "window_start": profile["window_start"],
        "window_end_exclusive": profile["window_end_exclusive"],
        "expected": {"updates": len(slots["updates"]), "ribs": len(slots["ribs"])},
        "missing": {"updates": len(missing["updates"]), "ribs": len(missing["ribs"])},
        "mode": "execute" if args.execute else "dry_run",
        "downloaded_bytes": 0,
    }

    if not args.execute:
        # 抽样跨越整个缺口区间，避免只验证首尾。
        probes = []
        pool = missing["updates"]
        if pool:
            step = max(1, len(pool) // max(1, args.sample))
            for relative in pool[::step][: args.sample]:
                status, length = head(f"{BASE_URL}/{relative}")
                probes.append({"slot": relative, "status": status, "content_length": length})
        report["upstream_probe"] = probes
        available = [p for p in probes if p["status"] == 200]
        report["upstream_available_ratio"] = (
            len(available) / len(probes) if probes else None
        )
        sizes = [p["content_length"] for p in available if p["content_length"]]
        if sizes:
            mean = sum(sizes) / len(sizes)
            report["estimated_update_bytes"] = int(mean * len(missing["updates"]))
        report["note"] = (
            "dry-run：未下载任何字节。实际补取需项目方授权并显式传入 --execute。"
        )
    else:
        report["note"] = "execute 模式需在授权后由运维执行；本工具不自动提权或改写已准入槽。"
        sys.stderr.write(
            "拒绝执行：--execute 需要项目方显式授权，并应在受控运维流程中运行。\n"
        )
        return 3

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    sys.stderr.write(f"缺口清单已写入 {args.output}\n")
    print(json.dumps({k: v for k, v in report.items() if k != "upstream_probe"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
