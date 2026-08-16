#!/usr/bin/env python3
"""独立核验两次单槽连续追加的产品与末状态是否确定一致。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


SUMMARY_FIELDS = (
    "schema_version",
    "engine_version",
    "status",
    "run_id",
    "dataset_id",
    "revision",
    "previous_data_through",
    "data_through",
    "product_sequence",
    "artifact_index",
    "input_artifact",
    "previous_checkpoint_sha256",
    "product_path",
    "route_state_rows",
    "state_digest",
    "country_observation_count",
    "asn_state_count",
    "activity",
    "conservation",
    "malformed_otc_attributes",
    "treat_as_withdraw_route_events",
    "loaded_rib",
    "reapplied_prior_update_count",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"JSON 顶层不是对象：{path}")
    return value


def read_product(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise AssertionError(f"产品顶层不是对象：{path}")
    return value


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_run(
    root: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    summary_path = root / "append-summary.json"
    summary = read_json(summary_path)
    assert summary["status"] == "complete"
    assert summary["loaded_rib"] is False
    assert summary["reapplied_prior_update_count"] == 0
    assert summary["conservation"]["status"] == "pass"

    product_path = root / str(summary["product_path"])
    assert product_path.is_file()
    assert sha256_file(product_path) == summary["product_sha256"]
    product = read_product(product_path)
    assert product["spool_manifest_sha256"] == summary[
        "append_spool_manifest_sha256"
    ]

    spool_manifest_path = root / "spool" / "append-manifest.json"
    spool_manifest = read_json(spool_manifest_path)
    assert sha256_file(spool_manifest_path) == summary[
        "append_spool_manifest_sha256"
    ]

    checkpoint_root = Path(str(summary["checkpoint_path"]))
    manifest_path = checkpoint_root / "manifest.json"
    manifest = read_json(manifest_path)
    assert sha256_file(manifest_path) == summary["checkpoint_sha256"]
    assert manifest["restore_requires_rib"] is False
    assert manifest["restore_requires_prior_updates"] is False
    assert manifest["state_digest"] == summary["state_digest"]
    assert manifest["record_count"] == summary["route_state_rows"]
    assert manifest["conservation"] == summary["conservation"]

    for shard in manifest["shards"]:
        shard_path = checkpoint_root / str(shard["path"])
        assert shard_path.is_file()
        assert sha256_file(shard_path) == shard["sha256"]

    return summary, manifest, product, spool_manifest


def comparable_checkpoint_manifest(
    manifest: dict[str, Any],
) -> dict[str, Any]:
    comparable = dict(manifest)
    comparable["schema_version"] = "<normalized>"
    comparable.pop("created_at", None)
    comparable.pop("identity_time", None)
    comparable["previous_product_sha256"] = "<normalized>"
    comparable["source_checkpoint_sha256"] = "<normalized>"
    comparable["shards"] = [
        {
            "shard": shard["shard"],
            "path": shard["path"],
            "record_count": shard["record_count"],
        }
        for shard in manifest["shards"]
    ]
    return comparable


def comparable_product(product: dict[str, Any]) -> dict[str, Any]:
    comparable = dict(product)
    comparable["spool_manifest_sha256"] = "<normalized>"
    return comparable


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", required=True, type=Path)
    parser.add_argument("--repeat", required=True, type=Path)
    parser.add_argument("--corrected", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.output.exists():
        raise FileExistsError(f"拒绝覆盖既有验证结果：{arguments.output}")

    (
        primary_summary,
        primary_manifest,
        primary_product,
        primary_spool,
    ) = verify_run(arguments.primary)
    (
        repeat_summary,
        repeat_manifest,
        repeat_product,
        repeat_spool,
    ) = verify_run(arguments.repeat)
    (
        corrected_summary,
        corrected_manifest,
        corrected_product,
        corrected_spool,
    ) = verify_run(arguments.corrected)

    primary_identity = {
        field: primary_summary[field]
        for field in SUMMARY_FIELDS
    }
    repeat_identity = {
        field: repeat_summary[field]
        for field in SUMMARY_FIELDS
    }
    assert primary_identity == repeat_identity
    corrected_identity = {
        field: corrected_summary[field]
        for field in SUMMARY_FIELDS
    }
    assert primary_identity == corrected_identity
    assert primary_summary["product_sha256"] != repeat_summary["product_sha256"]
    assert primary_spool["created_at"] != repeat_spool["created_at"]
    assert set(primary_spool) == {
        "schema_version",
        "engine_version",
        "created_at",
        "slot",
    }
    assert set(repeat_spool) == set(primary_spool)
    assert corrected_spool["schema_version"] == "rrc25-global-append-spool/v2"
    assert corrected_spool["data_through"] == corrected_summary["data_through"]
    assert "created_at" not in corrected_spool
    assert corrected_manifest["schema_version"] == (
        "rrc25-global-route-state-checkpoint/v2"
    )
    assert corrected_manifest["identity_time"] == corrected_summary["data_through"]
    assert "created_at" not in corrected_manifest

    comparable_primary_product = comparable_product(primary_product)
    assert comparable_primary_product == comparable_product(repeat_product)
    assert comparable_primary_product == comparable_product(corrected_product)
    assert comparable_checkpoint_manifest(primary_manifest) == (
        comparable_checkpoint_manifest(repeat_manifest)
    )
    assert comparable_checkpoint_manifest(primary_manifest) == (
        comparable_checkpoint_manifest(corrected_manifest)
    )
    assert all(
        primary["record_count"] == repeat["record_count"]
        for primary, repeat in zip(
            primary_manifest["shards"],
            repeat_manifest["shards"],
        )
    )
    assert all(
        primary["record_count"] == corrected["record_count"]
        for primary, corrected in zip(
            primary_manifest["shards"],
            corrected_manifest["shards"],
        )
    )
    legacy_equal_checkpoint_shards = sum(
        primary["sha256"] == repeat["sha256"]
        for primary, repeat in zip(
            primary_manifest["shards"],
            repeat_manifest["shards"],
        )
    )
    assert legacy_equal_checkpoint_shards < primary_manifest["shard_count"]

    result = {
        "schema_version": "rrc25-global-append-repeat-verification/v2",
        "status": "pass",
        "run_id": corrected_summary["run_id"],
        "dataset_id": corrected_summary["dataset_id"],
        "revision": corrected_summary["revision"],
        "previous_data_through": corrected_summary["previous_data_through"],
        "data_through": corrected_summary["data_through"],
        "product_sequence": corrected_summary["product_sequence"],
        "corrected_product_sha256": corrected_summary["product_sha256"],
        "normalized_product_content_sha256": canonical_sha256(
            comparable_primary_product
        ),
        "state_digest": corrected_summary["state_digest"],
        "route_state_rows": corrected_summary["route_state_rows"],
        "country_observation_count": corrected_summary[
            "country_observation_count"
        ],
        "loaded_rib": corrected_summary["loaded_rib"],
        "reapplied_prior_update_count": corrected_summary[
            "reapplied_prior_update_count"
        ],
        "checkpoint_sha256": corrected_summary["checkpoint_sha256"],
        "checkpoint_shard_count": corrected_manifest["shard_count"],
        "checkpoint_semantic_identity": "pass",
        "checkpoint_shard_population_identity": "pass",
        "corrected_checkpoint_shards_verified": "pass",
        "product_semantic_identity": "pass",
        "deterministic_identity_correction": {
            "legacy_primary_product_sha256": primary_summary["product_sha256"],
            "legacy_repeat_product_sha256": repeat_summary["product_sha256"],
            "legacy_difference": "append spool manifest 的墙钟 created_at",
            "legacy_equal_checkpoint_shard_hashes": (
                legacy_equal_checkpoint_shards
            ),
            "legacy_checkpoint_shard_count": primary_manifest["shard_count"],
            "checkpoint_difference": "Go map 遍历产生的 shard 内记录顺序",
            "corrected_spool_schema": corrected_spool["schema_version"],
            "corrected_checkpoint_schema": corrected_manifest["schema_version"],
            "corrected_identity_time": corrected_manifest["identity_time"],
        },
        "summary_identity_fields": list(SUMMARY_FIELDS),
        "primary_append_summary_sha256": sha256_file(
            arguments.primary / "append-summary.json"
        ),
        "repeat_append_summary_sha256": sha256_file(
            arguments.repeat / "append-summary.json"
        ),
        "corrected_append_summary_sha256": sha256_file(
            arguments.corrected / "append-summary.json"
        ),
        "note": (
            "两次旧输出用于定位墙钟字段偏离；修正版以 data_through "
            "形成确定性 spool 与 checkpoint 身份，三次状态、活动、国家、ASN "
            "及 64 个 shard 人口结构一致；修正版 64 个 shard 的实际哈希均已核验。"
        ),
    }
    arguments.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
