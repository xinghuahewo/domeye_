#!/usr/bin/env python3
"""从已验收 S5/S6 制品构建一次性、不可变的 224-310 生产数据 release。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any
from datetime import datetime, timezone


CANDIDATE_ID = "domeye_data_candidate_v1_ce3aa006fe1f7dd3e723db9b13baf097"
READ_MODEL_DATASET_ID = "read_model_dataset_v1_bad9b4c0bd32f7d026356c82dab1b50e"
SHADOW_DATASET_ID = "shadow_migration_dataset_v1_b2a89ec607d467364a7ea6172a28d494"
WINDOW_START = "2026-02-24T00:00:00Z"
WINDOW_END = "2026-03-11T00:00:00Z"


class BuildError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise BuildError(f"无法读取 JSON：{path}") from error
    if not isinstance(value, dict):
        raise BuildError(f"JSON 必须是对象：{path}")
    return value


def write_json_create_only(path: Path, value: Any) -> None:
    if path.exists():
        raise BuildError(f"create-only 拒绝覆盖：{path}")
    raw = canonical_bytes(value) + b"\n"
    with path.open("xb") as output:
        output.write(raw)


def physical_tree(root: Path) -> tuple[str, list[str]]:
    rows: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        rows.append(f"{sha256(raw)}  {len(raw)}  {relative}")
    content = "\n".join(rows) + "\n"
    return sha256(content.encode("utf-8")), rows


def validate_read_model(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = root / "manifest.json"
    manifest = read_json(manifest_path)
    expected = {
        "schema_version": "rrc25-read-model-store/v1",
        "status": "complete",
        "collector_id": "rrc25",
        "candidate_id": CANDIDATE_ID,
        "dataset_id": READ_MODEL_DATASET_ID,
        "window_start_utc": WINDOW_START,
        "window_end_exclusive_utc": WINDOW_END,
        "state_point_count": 4320,
        "event_count": 81,
        "event_country_count": 43,
        "api_read_semantics": "precompiled_read_model_only",
        "prefix_vp_semantics": "derived_view_not_independent_fact",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise BuildError(f"S5 manifest 字段冲突：{key}")
    for entry in manifest.get("files", []):
        target = root / str(entry.get("path") or "")
        if not target.is_file():
            raise BuildError(f"S5 文件缺失：{target}")
        raw = target.read_bytes()
        if len(raw) != entry.get("size_bytes") or sha256(raw) != entry.get("sha256"):
            raise BuildError(f"S5 文件物理身份冲突：{target}")

    catalog = read_json(root / "prefix-vp" / "catalog.json")
    if (
        catalog.get("schema_version") != "rrc25-prefix-vp-evidence-catalog/v1"
        or catalog.get("status") != "complete"
        or catalog.get("collector_id") != "rrc25"
        or catalog.get("country_count") != 43
        or catalog.get("row_count") != 1527242
    ):
        raise BuildError("Prefix×VP Evidence catalog 身份不一致")
    return manifest, catalog


def build_country_index(root: Path, catalog: dict[str, Any]) -> dict[str, Any]:
    countries: dict[str, Any] = {}
    for country in catalog["countries"]:
        code = country["country_code"]
        origins: set[int] = set()
        peers: set[tuple[str, int]] = set()
        observed_rows = 0
        for page in country["pages"]:
            page_path = root / "prefix-vp" / "pages" / page["path"]
            raw = page_path.read_bytes()
            if len(raw) != page["size_bytes"] or sha256(raw) != page["sha256"]:
                raise BuildError(f"Prefix×VP Evidence page 物理身份冲突：{page_path}")
            try:
                payload = json.loads(gzip.decompress(raw))
            except (OSError, json.JSONDecodeError) as error:
                raise BuildError(f"Prefix×VP Evidence page 不可读取：{page_path}") from error
            if (
                payload.get("schema_version") != "rrc25-prefix-vp-evidence-page/v1"
                or payload.get("country_code") != code
                or payload.get("page") != page["page"]
                or len(payload.get("rows") or []) != page["row_count"]
            ):
                raise BuildError(f"Prefix×VP Evidence page 合同冲突：{page_path}")
            for row in payload["rows"]:
                origin = row.get("baseline_origin_asn")
                if isinstance(origin, int) and origin > 0:
                    origins.add(origin)
                peer_ip = row.get("peer_ip")
                peer_asn = row.get("peer_asn")
                if isinstance(peer_ip, str) and isinstance(peer_asn, int):
                    peers.add((peer_ip, peer_asn))
            observed_rows += len(payload["rows"])
        if observed_rows != country["row_count"]:
            raise BuildError(f"Prefix×VP Evidence 国家人口冲突：{code}")

        series_path = root / "series" / f"{code}.json.gz"
        try:
            series = json.loads(gzip.decompress(series_path.read_bytes()))
        except (OSError, json.JSONDecodeError) as error:
            raise BuildError(f"国家紧凑序列不可读取：{code}") from error
        columns = series.get("columns") or []
        values = series.get("values") or []
        by_name = {name: values[index] for index, name in enumerate(columns)}
        for name in ("baseline_v4", "baseline_v6"):
            if name not in by_name or len(by_name[name]) != 4320:
                raise BuildError(f"国家紧凑序列缺少固定分母：{code}/{name}")
        baseline_v4 = int(by_name["baseline_v4"][0])
        baseline_v6 = int(by_name["baseline_v6"][0])
        if baseline_v4 + baseline_v6 != country["row_count"]:
            raise BuildError(f"国家序列与 Evidence 人口不守恒：{code}")
        cohort_material = canonical_bytes(
            {
                "country_code": code,
                "seed_route_state_id": catalog["seed_route_state_id"],
                "derived_from_route_state_id": catalog["derived_from_route_state_id"],
                "mapping_version": catalog["mapping_version"],
            }
        )
        countries[code] = {
            "cohort_id": "cohort_dl_v1_" + sha256(cohort_material)[:32],
            "prefix_vp_count": country["row_count"],
            "ipv4_prefix_vp_count": baseline_v4,
            "ipv6_prefix_vp_count": baseline_v6,
            "origin_asn_count": len(origins),
            "vantage_point_count": len(peers),
            "evidence_page_count": country["page_count"],
            "evidence_content_sha256": country["content_sha256"],
        }
    return countries


def immutable(path: Path) -> None:
    for item in sorted(path.rglob("*"), reverse=True):
        mode = 0o555 if item.is_dir() else 0o444
        item.chmod(mode)
    path.chmod(0o555)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--s5-root", required=True, type=Path)
    parser.add_argument("--s6-manifest", required=True, type=Path)
    parser.add_argument("--s6-end-to-end", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not re_full_sha(args.source_commit):
        raise BuildError("source commit 必须是完整 40 位 SHA")
    if args.output_root.exists():
        raise BuildError("生产数据 release 已存在；create-only 拒绝覆盖")
    source_manifest, source_catalog = validate_read_model(args.s5_root)
    s6_manifest = read_json(args.s6_manifest)
    s6_receipt = read_json(args.s6_end_to_end)
    if (
        s6_manifest.get("candidate_id") != CANDIDATE_ID
        or s6_manifest.get("dataset_id") != SHADOW_DATASET_ID
        or s6_receipt.get("candidate_id") != CANDIDATE_ID
        or s6_receipt.get("dataset_id") != SHADOW_DATASET_ID
    ):
        raise BuildError("S6 manifest/end-to-end 身份不一致")

    args.output_root.mkdir(parents=True, mode=0o755)
    read_model_root = args.output_root / "read-model"
    shutil.copytree(args.s5_root, read_model_root, symlinks=False)
    lineage = args.output_root / "lineage"
    lineage.mkdir()
    shutil.copy2(args.s6_manifest, lineage / "S6-MANIFEST.json")
    shutil.copy2(args.s6_end_to_end, lineage / "S6-END-TO-END.json")

    copied_manifest, copied_catalog = validate_read_model(read_model_root)
    if copied_manifest != source_manifest or copied_catalog != source_catalog:
        raise BuildError("复制后的 S5 读模型身份漂移")
    tree_sha, tree_rows = physical_tree(read_model_root)
    countries = build_country_index(read_model_root, copied_catalog)
    selected_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    manifest_raw = (read_model_root / "manifest.json").read_bytes()
    index = {
        "schema_version": "domeye_data_layer_production_index_v1",
        "status": "complete",
        "collector_id": "rrc25",
        "candidate_id": CANDIDATE_ID,
        "read_model_dataset_id": READ_MODEL_DATASET_ID,
        "read_model_manifest_sha256": sha256(manifest_raw),
        "read_model_tree_sha256": tree_sha,
        "mapping_version": copied_catalog["mapping_version"],
        "derived_from_route_state_id": copied_catalog["derived_from_route_state_id"],
        "country_count": len(countries),
        "countries": countries,
    }
    index_path = args.output_root / "production-index.json"
    write_json_create_only(index_path, index)
    selection = {
        "schema_version": "domeye_data_layer_production_selection_v1",
        "status": "selected",
        "release_id": args.release_id,
        "selected_at": selected_at,
        "selected_by_production": True,
        "collector_id": "rrc25",
        "window_start_utc": WINDOW_START,
        "window_end_exclusive_utc": WINDOW_END,
        "candidate_id": CANDIDATE_ID,
        "read_model_dataset_id": READ_MODEL_DATASET_ID,
        "shadow_migration_dataset_id": SHADOW_DATASET_ID,
        "source_commit": args.source_commit,
        "read_model_root": "read-model",
        "read_model_manifest_sha256": sha256(manifest_raw),
        "read_model_tree_sha256": tree_sha,
        "production_index_path": "production-index.json",
        "production_index_sha256": sha256(index_path.read_bytes()),
        "s6_manifest_sha256": sha256((lineage / "S6-MANIFEST.json").read_bytes()),
        "s6_end_to_end_sha256": sha256((lineage / "S6-END-TO-END.json").read_bytes()),
        "rollback_release_id": "20260806T054822Z-country-outage-224-310-scope-revert-prod20",
    }
    write_json_create_only(args.output_root / "PRODUCTION-SELECTION.json", selection)
    checksums = args.output_root / "READ-MODEL-SHA256SUMS"
    checksums.write_text("\n".join(tree_rows) + "\n", encoding="utf-8")
    immutable(args.output_root)
    print(canonical_bytes(selection).decode("utf-8"))
    return 0


def re_full_sha(value: str) -> bool:
    return len(value) == 40 and all(character in "0123456789abcdef" for character in value)


if __name__ == "__main__":
    raise SystemExit(main())
