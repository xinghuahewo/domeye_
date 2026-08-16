#!/usr/bin/env python3
"""为隔离候选生成 RRC25 全局国家观测注册表。

该脚本只把已经闭合的 60 点与连续追加国家包注册为不可变 publication，
不写旧数据库，也不把非伊朗同期状态验收引用加入真实事件列表。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 不是 JSON 对象")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publication_id(
    incident_id: str,
    data_through: str,
    complete_sha256: str,
) -> str:
    value = f"{incident_id}|{data_through}|{complete_sha256}".encode()
    return "publication_global_v1_" + hashlib.sha256(value).hexdigest()[:24]


def package_identity(root: Path, country_code: str) -> dict[str, Any]:
    directory = root / "countries" / country_code
    complete_path = directory / "COMPLETE.json"
    complete = read_json(complete_path)
    incident = read_json(directory / "incident.json")
    cohort = read_json(directory / "cohort.json")
    if (
        complete.get("status") != "complete"
        or complete.get("country_code") != country_code
        or cohort.get("country_code") != country_code
        or complete.get("cohort_id") != cohort.get("cohort_id")
        or not isinstance(incident.get("incident_id"), str)
    ):
        raise ValueError(f"{country_code} 国家包身份不闭合")
    hashes = complete.get("deliverable_sha256")
    if not isinstance(hashes, dict):
        raise ValueError(f"{country_code} 国家包缺少交付哈希")
    for filename, expected in hashes.items():
        if (
            not isinstance(filename, str)
            or not isinstance(expected, str)
            or sha256_file(directory / filename) != expected
        ):
            raise ValueError(f"{country_code}/{filename} 哈希不闭合")
    return {
        "directory": str(directory),
        "complete": complete,
        "complete_sha256": sha256_file(complete_path),
        "incident_id": incident["incident_id"],
        "cohort_id": cohort["cohort_id"],
    }


def publication(
    identity: dict[str, Any],
    *,
    data_mode: str,
    publication_kind: str,
    supersedes: str | None = None,
) -> dict[str, Any]:
    complete = identity["complete"]
    data_through = str(complete["last_observation_at"])
    current_id = publication_id(
        str(identity["incident_id"]),
        data_through,
        str(identity["complete_sha256"]),
    )
    result: dict[str, Any] = {
        "publication_id": current_id,
        "package_uri": identity["directory"],
        "revision": 2,
        "publication_state": "published",
        "observation_state": "state_complete",
        "data_mode": data_mode,
        "data_through": data_through,
        "updated_at": complete.get("completed_at"),
        "is_final": False,
        "publication_kind": publication_kind,
        "processing_status": {
            "state": "idle",
            "updated_at": complete.get("completed_at"),
            "attempted_through": data_through,
            "reason": None,
            "last_complete_data_through": data_through,
        },
    }
    if supersedes:
        result["supersedes_publication_id"] = supersedes
    return result


def observation(
    base_root: Path,
    append_root: Path,
    *,
    country_code: str,
    country_name: str,
    legacy_reference: str,
    real_event: bool,
) -> dict[str, Any]:
    baseline_identity = package_identity(base_root, country_code)
    append_identity = package_identity(append_root, country_code)
    if (
        baseline_identity["incident_id"] != append_identity["incident_id"]
        or baseline_identity["cohort_id"] != append_identity["cohort_id"]
    ):
        raise ValueError(f"{country_code} 连续追加改变事件或 cohort 身份")
    baseline = publication(
        baseline_identity,
        data_mode="replay",
        publication_kind="baseline",
    )
    appended = publication(
        append_identity,
        data_mode="mixed",
        publication_kind="append",
        supersedes=str(baseline["publication_id"]),
    )
    legacy_state = (
        {"state": "available", "reason": "旧事实身份保留"}
        if real_event
        else {
            "state": "unavailable",
            "reason": "该引用只用于隔离同期状态验收，不表示真实中断事件",
        }
    )
    return {
        "incident_id": append_identity["incident_id"],
        "legacy_reference": legacy_reference,
        "country": {"code": country_code, "name": country_name},
        "display_name": (
            f"{country_name} BGP 路由观测"
            if real_event
            else f"{country_name}同期状态验收"
        ),
        "collector_ids": ["rrc25"],
        "vantage_point_count": None,
        "vantage_point_semantics": "RRC25 RouteState 中的唯一 VP 身份",
        "display_timezone": "Asia/Shanghai",
        "interval_seconds": 300,
        "revision": 2,
        "publication_state": "published",
        "observation_state": "state_complete",
        "data_mode": "mixed",
        "data_through": appended["data_through"],
        "updated_at": appended["updated_at"],
        "is_final": False,
        "package_uri": appended["package_uri"],
        "resource_source": {"state": "unavailable"},
        "capabilities": {
            "legacy_summary": legacy_state,
            "fixed_cohort": {"state": "available"},
            "country_resources": {
                "state": "unavailable",
                "reason": "本次全局 RouteState 包不提供地址空间去重资源轨道",
            },
            "update_activity": {"state": "available"},
            "address_families": {"state": "available"},
            "asn_matrix": {"state": "available"},
            "audit": {"state": "available"},
            "normal_band": {
                "state": "unavailable",
                "reason": "固定研究窗口没有可信长期正常参照",
            },
        },
        "processing_status": appended["processing_status"],
        "publications": [baseline, appended],
        "current_publication_id": appended["publication_id"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成隔离候选使用的 RRC25 全局国家观测注册表。",
    )
    parser.add_argument("--base-package-root", required=True, type=Path)
    parser.add_argument("--append-package-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise ValueError("输出注册表已经存在，拒绝覆盖")
    payload = {
        "schema_version": "country_outage_observation_registry_v1",
        "scope": "isolated_rrc25_global_state_acceptance",
        "observations": [
            observation(
                args.base_package_root,
                args.append_package_root,
                country_code="IR",
                country_name="伊朗",
                legacy_reference=(
                    "country_outage/2026-02-27 09:12:32/IR/1/r"
                ),
                real_event=True,
            ),
            observation(
                args.base_package_root,
                args.append_package_root,
                country_code="MS",
                country_name="蒙特塞拉特",
                legacy_reference=(
                    "country_outage/2026-02-28 18:05:00/MS/1/rrc25"
                ),
                real_event=False,
            ),
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "output": str(args.output),
                "sha256": sha256_file(args.output),
                "observation_count": len(payload["observations"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(
            json.dumps(
                {"status": "failed", "error": str(error)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)
