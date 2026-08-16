#!/usr/bin/env python3
"""核验 RRC25 同期全球状态分发后的全部国家查询包。"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


EXPECTED_TIMES = tuple(
    (
        datetime(2026, 2, 28, 10, 5, tzinfo=timezone.utc)
        + timedelta(minutes=5 * index)
    )
    .isoformat(timespec="seconds")
    .replace("+00:00", "Z")
    for index in range(60)
)
APPEND_TIME = "2026-02-28T15:05:00Z"
SAMPLE_CODES = ("MS", "KG", "US")
IRAN_SNAPSHOT_FIELDS = (
    "snapshot_id",
    "observed_at",
    "slot_start_utc",
    "slot_end_exclusive_utc",
    "slot_role",
    "cohort_id",
    "baseline_asn_count",
    "baseline_prefix_vp_count",
    "visible_prefix_vp_count",
    "visible_prefix_vp_ratio",
    "affected_asn_count",
    "affected_asn_ratio",
    "visible_origin_asn_count",
    "visible_origin_asn_ratio",
    "ipv4",
    "ipv6",
    "dual_stack_classifications",
    "update_counts",
)
IRAN_ASN_FIELDS = (
    "snapshot_id",
    "observed_at",
    "cohort_id",
    "asn",
    "classification",
    "ipv4_invisible_ipv6_visible",
)


class VerificationError(RuntimeError):
    """国家查询包身份、人口或时间轴不闭合。"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"无法读取 JSON：{path}") from error
    if not isinstance(value, dict):
        raise VerificationError(f"JSON 根节点不是对象：{path}")
    return value


def _iter_jsonl_gzip(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for ordinal, line in enumerate(stream, start=1):
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise VerificationError(
                        f"{path} 第 {ordinal} 行不是对象"
                    )
                yield value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"无法读取 gzip JSONL：{path}") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise VerificationError(f"无法哈希文件：{path}") from error
    return digest.hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _formal_products(
    global_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_path = global_root / "checkpoints/formal/manifest.json"
    manifest = _read_json(manifest_path)
    if (
        manifest.get("phase") != "formal"
        or manifest.get("processed_update_count") != 84
        or manifest.get("formal_observation_count") != 60
        or manifest.get("data_through") != EXPECTED_TIMES[-1]
    ):
        raise VerificationError("正式 checkpoint 身份或数量不闭合")
    references = [
        row
        for row in manifest.get("products", [])
        if isinstance(row, Mapping) and row.get("phase") == "formal"
    ]
    if len(references) != 60:
        raise VerificationError("正式产品引用不是 60 个")
    products: list[dict[str, Any]] = []
    for index, reference in enumerate(references):
        path = global_root / str(reference["path"])
        if _sha256(path) != reference.get("sha256"):
            raise VerificationError(f"正式产品哈希不一致：{path}")
        try:
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                product = json.load(stream)
        except (OSError, json.JSONDecodeError) as error:
            raise VerificationError(f"无法读取正式产品：{path}") from error
        if (
            not isinstance(product, dict)
            or product.get("observed_at") != EXPECTED_TIMES[index]
            or product.get("phase") != "formal"
        ):
            raise VerificationError(f"正式产品 {index} 时间或身份不一致")
        products.append(product)
    return products, manifest


def _validate_family(
    family: Mapping[str, Any],
    *,
    code: str,
    observed_at: str,
) -> None:
    baseline = family.get("baseline_prefix_vp_count")
    visible = family.get("visible_prefix_vp_count")
    if (
        not isinstance(baseline, int)
        or not isinstance(visible, int)
        or visible < 0
        or visible > baseline
    ):
        raise VerificationError(
            f"{code} {observed_at} 地址族 Prefix×VP 不闭合"
        )
    groups = [
        family.get("fully_visible_asns"),
        family.get("partially_visible_asns"),
        family.get("fully_invisible_asns"),
    ]
    if not all(isinstance(group, list) for group in groups):
        raise VerificationError(f"{code} {observed_at} 地址族 ASN 分类缺失")
    flattened = [value for group in groups for value in group]
    if (
        len(flattened) != len(set(flattened))
        or len(flattened) != family.get("baseline_origin_asn_count")
    ):
        raise VerificationError(
            f"{code} {observed_at} 地址族 ASN 人口不闭合"
        )


def _validate_country_package(
    package_root: Path,
    catalog_entry: Mapping[str, Any],
    expected_times: tuple[str, ...],
) -> dict[str, Any]:
    code = str(catalog_entry["country_code"])
    directory = package_root / str(catalog_entry["package_path"])
    complete = _read_json(directory / "COMPLETE.json")
    if (
        complete.get("status") != "complete"
        or complete.get("country_code") != code
        or complete.get("observation_count") != len(expected_times)
        or complete.get("last_observation_at") != expected_times[-1]
        or _sha256(directory / "COMPLETE.json")
        != catalog_entry.get("complete_sha256")
    ):
        raise VerificationError(f"{code} COMPLETE 身份不闭合")
    hashes = complete.get("deliverable_sha256")
    if not isinstance(hashes, Mapping):
        raise VerificationError(f"{code} 缺少交付哈希")
    for filename, expected in hashes.items():
        if _sha256(directory / str(filename)) != expected:
            raise VerificationError(f"{code} {filename} 哈希不一致")

    cohort = _read_json(directory / "cohort.json")
    if (
        cohort.get("country_code") != code
        or cohort.get("cohort_id") != catalog_entry.get("cohort_id")
        or cohort.get("baseline_origin_asn_count")
        != catalog_entry.get("baseline_origin_asn_count")
        or cohort.get("baseline_prefix_vp_count")
        != catalog_entry.get("baseline_prefix_vp_count")
    ):
        raise VerificationError(f"{code} cohort 与目录不一致")
    members = cohort.get("members")
    if not isinstance(members, list):
        raise VerificationError(f"{code} cohort members 缺失")
    member_population = sum(
        int(member.get("prefix_vp_count", -1))
        for member in members
        if isinstance(member, Mapping)
    )
    if (
        member_population + int(cohort.get("unknown_origin_prefix_vp_count", 0))
        != cohort["baseline_prefix_vp_count"]
        or cohort.get("baseline_ipv4_prefix_vp_count", 0)
        + cohort.get("baseline_ipv6_prefix_vp_count", 0)
        != cohort["baseline_prefix_vp_count"]
    ):
        raise VerificationError(f"{code} cohort Prefix×VP 不守恒")
    baseline_asns = cohort.get("baseline_origin_asns")
    if (
        not isinstance(baseline_asns, list)
        or len(baseline_asns) != cohort["baseline_origin_asn_count"]
        or len(set(baseline_asns)) != len(baseline_asns)
    ):
        raise VerificationError(f"{code} cohort ASN 人口不守恒")

    snapshots = list(
        _iter_jsonl_gzip(directory / "country-snapshots.jsonl.gz")
    )
    if (
        len(snapshots) != len(expected_times)
        or tuple(row.get("observed_at") for row in snapshots) != expected_times
    ):
        raise VerificationError(
            f"{code} 时间轴不是共同 {len(expected_times)} 点"
        )
    snapshot_ids: dict[str, str] = {}
    expected_classifications: dict[str, dict[int, str]] = {}
    for snapshot in snapshots:
        observed_at = str(snapshot["observed_at"])
        if (
            snapshot.get("country_code") != code
            or snapshot.get("cohort_id") != cohort["cohort_id"]
            or snapshot.get("baseline_asn_count")
            != cohort["baseline_origin_asn_count"]
            or snapshot.get("baseline_prefix_vp_count")
            != cohort["baseline_prefix_vp_count"]
            or not isinstance(snapshot.get("visible_prefix_vp_count"), int)
            or snapshot["visible_prefix_vp_count"] < 0
            or snapshot["visible_prefix_vp_count"]
            > snapshot["baseline_prefix_vp_count"]
        ):
            raise VerificationError(f"{code} {observed_at} 国家人口不闭合")
        ipv4 = snapshot.get("ipv4")
        ipv6 = snapshot.get("ipv6")
        if not isinstance(ipv4, Mapping) or not isinstance(ipv6, Mapping):
            raise VerificationError(f"{code} {observed_at} 地址族缺失")
        _validate_family(ipv4, code=code, observed_at=observed_at)
        _validate_family(ipv6, code=code, observed_at=observed_at)
        if (
            ipv4["baseline_prefix_vp_count"]
            + ipv6["baseline_prefix_vp_count"]
            != snapshot["baseline_prefix_vp_count"]
            or ipv4["visible_prefix_vp_count"]
            + ipv6["visible_prefix_vp_count"]
            != snapshot["visible_prefix_vp_count"]
        ):
            raise VerificationError(
                f"{code} {observed_at} 国家与地址族人口不闭合"
            )
        dual = snapshot.get("dual_stack_classifications")
        if not isinstance(dual, Mapping):
            raise VerificationError(f"{code} {observed_at} 双栈分类缺失")
        groups = {
            state: dual.get(state)
            for state in (
                "fully_visible",
                "partially_visible",
                "fully_invisible",
            )
        }
        if not all(isinstance(group, list) for group in groups.values()):
            raise VerificationError(f"{code} {observed_at} 双栈分类无效")
        flattened = [
            int(asn)
            for group in groups.values()
            for asn in group
        ]
        if (
            len(flattened) != len(set(flattened))
            or set(flattened) != set(int(value) for value in baseline_asns)
            or len(groups["partially_visible"])
            + len(groups["fully_invisible"])
            != snapshot.get("affected_asn_count")
            or len(flattened) - len(groups["fully_invisible"])
            != snapshot.get("visible_origin_asn_count")
        ):
            raise VerificationError(f"{code} {observed_at} ASN 分类不闭合")
        snapshot_ids[observed_at] = str(snapshot["snapshot_id"])
        expected_classifications[observed_at] = {
            int(asn): state
            for state, group in groups.items()
            for asn in group
        }

    asn_counts = Counter()
    seen: dict[str, set[int]] = defaultdict(set)
    for row in _iter_jsonl_gzip(directory / "asn-states.jsonl.gz"):
        observed_at = str(row.get("observed_at"))
        asn = row.get("asn")
        if (
            observed_at not in snapshot_ids
            or not isinstance(asn, int)
            or asn in seen[observed_at]
            or row.get("snapshot_id") != snapshot_ids[observed_at]
            or row.get("cohort_id") != cohort["cohort_id"]
            or row.get("classification")
            != expected_classifications[observed_at].get(asn)
        ):
            raise VerificationError(
                f"{code} {observed_at} ASN 状态行不闭合"
            )
        seen[observed_at].add(asn)
        asn_counts[observed_at] += 1
    expected_asn_rows = (
        len(expected_times) * cohort["baseline_origin_asn_count"]
    )
    if (
        sum(asn_counts.values()) != expected_asn_rows
        or any(
            asn_counts[observed_at] != cohort["baseline_origin_asn_count"]
            for observed_at in expected_times
        )
    ):
        raise VerificationError(
            f"{code} ASN {len(expected_times)} 点人口不闭合"
        )
    return {
        "code": code,
        "cohort_id": cohort["cohort_id"],
        "baseline_origin_asn_count": cohort["baseline_origin_asn_count"],
        "baseline_prefix_vp_count": cohort["baseline_prefix_vp_count"],
        "baseline_ipv4_prefix_vp_count": cohort[
            "baseline_ipv4_prefix_vp_count"
        ],
        "baseline_ipv6_prefix_vp_count": cohort[
            "baseline_ipv6_prefix_vp_count"
        ],
        "unknown_origin_prefix_vp_count": cohort[
            "unknown_origin_prefix_vp_count"
        ],
        "snapshot_count": len(snapshots),
        "asn_state_count": sum(asn_counts.values()),
        "snapshots": snapshots,
    }


def _compare_iran(
    generated: Mapping[str, Any],
    baseline_root: Path,
) -> dict[str, Any]:
    generated_root = Path(str(generated["directory"]))
    generated_cohort = _read_json(generated_root / "cohort.json")
    baseline_cohort = _read_json(baseline_root / "cohort.json")
    for field in (
        "cohort_id",
        "mapping_version",
        "baseline_origin_asn_count",
        "baseline_prefix_vp_count",
        "baseline_origin_asns",
        "members",
    ):
        if _canonical(generated_cohort.get(field)) != _canonical(
            baseline_cohort.get(field)
        ):
            raise VerificationError(f"伊朗 cohort 字段不一致：{field}")
    generated_snapshots = list(
        _iter_jsonl_gzip(generated_root / "country-snapshots.jsonl.gz")
    )
    baseline_snapshots = list(
        _iter_jsonl_gzip(baseline_root / "country-snapshots.jsonl.gz")
    )
    if len(generated_snapshots) < len(baseline_snapshots):
        raise VerificationError("伊朗新增国家包缺少既有 60 点基线")
    for index, (current, baseline) in enumerate(
        zip(
            generated_snapshots[: len(baseline_snapshots)],
            baseline_snapshots,
        )
    ):
        for field in IRAN_SNAPSHOT_FIELDS:
            if _canonical(current.get(field)) != _canonical(baseline.get(field)):
                raise VerificationError(
                    f"伊朗状态点 {index} 字段不一致：{field}"
                )
    generated_asns_all = {
        (row["observed_at"], int(row["asn"])): row
        for row in _iter_jsonl_gzip(generated_root / "asn-states.jsonl.gz")
    }
    generated_asns = {
        identity: row
        for identity, row in generated_asns_all.items()
        if identity[0] in EXPECTED_TIMES
    }
    baseline_asns = {
        (row["observed_at"], int(row["asn"])): row
        for row in _iter_jsonl_gzip(baseline_root / "asn-states.jsonl.gz")
    }
    if generated_asns.keys() != baseline_asns.keys():
        raise VerificationError("伊朗 ASN 状态键集合不一致")
    for identity in generated_asns:
        for field in IRAN_ASN_FIELDS:
            if _canonical(generated_asns[identity].get(field)) != _canonical(
                baseline_asns[identity].get(field)
            ):
                raise VerificationError(
                    f"伊朗 ASN 状态 {identity} 字段不一致：{field}"
                )
    return {
        "status": "pass",
        "cohort_id": generated_cohort["cohort_id"],
        "baseline_snapshot_count": len(baseline_snapshots),
        "generated_snapshot_count": len(generated_snapshots),
        "baseline_asn_state_count": len(baseline_asns),
        "generated_asn_state_count": len(generated_asns_all),
        "snapshot_fields_compared": list(IRAN_SNAPSHOT_FIELDS),
        "asn_fields_compared": list(IRAN_ASN_FIELDS),
    }


def verify(
    package_root: Path,
    global_root: Path,
    iran_baseline: Path,
    append_root: Path | None = None,
) -> dict[str, Any]:
    package_root = package_root.resolve()
    global_root = global_root.resolve()
    iran_baseline = iran_baseline.resolve()
    append_root = append_root.resolve() if append_root is not None else None
    root_complete = _read_json(package_root / "COMPLETE.json")
    catalog = _read_json(package_root / "catalog.json")
    if (
        root_complete.get("status") != "complete"
        or _sha256(package_root / "catalog.json")
        != root_complete.get("catalog_sha256")
        or catalog.get("country_count") != len(catalog.get("countries", []))
        or catalog.get("observation_count") not in {60, 61}
    ):
        raise VerificationError("国家包根 COMPLETE 或 catalog 不闭合")
    products, formal_manifest = _formal_products(global_root)
    expected_times = EXPECTED_TIMES
    append_summary: dict[str, Any] | None = None
    if catalog.get("observation_count") == 61:
        if append_root is None:
            raise VerificationError("61 点国家包缺少 --append-root")
        append_summary = _read_json(append_root / "append-summary.json")
        append_product_path = append_root / str(
            append_summary.get("product_path")
        )
        if _sha256(append_product_path) != append_summary.get(
            "product_sha256"
        ):
            raise VerificationError("连续追加产品哈希不一致")
        try:
            with gzip.open(
                append_product_path, "rt", encoding="utf-8"
            ) as stream:
                append_product = json.load(stream)
        except (OSError, json.JSONDecodeError) as error:
            raise VerificationError("无法读取连续追加产品") from error
        if (
            not isinstance(append_product, dict)
            or append_product.get("phase") != "append"
            or append_product.get("product_sequence") != 86
            or append_product.get("observed_at") != APPEND_TIME
            or append_summary.get("previous_data_through")
            != EXPECTED_TIMES[-1]
            or append_summary.get("data_through") != APPEND_TIME
            or append_summary.get("loaded_rib") is not False
            or append_summary.get("reapplied_prior_update_count") != 0
        ):
            raise VerificationError("连续追加身份或无 RIB 恢复声明不闭合")
        products.append(append_product)
        expected_times = EXPECTED_TIMES + (APPEND_TIME,)
    entries = catalog["countries"]
    codes = [str(entry["country_code"]) for entry in entries]
    if len(codes) != len(set(codes)) or "__UNKNOWN__" not in codes:
        raise VerificationError("国家目录重复或缺少显式未知桶")

    country_results: dict[str, dict[str, Any]] = {}
    for ordinal, entry in enumerate(entries, start=1):
        result = _validate_country_package(
            package_root, entry, expected_times
        )
        result["directory"] = str(
            package_root / str(entry["package_path"])
        )
        country_results[result["code"]] = result
        print(
            f"已核验国家包 {ordinal}/{len(entries)}：{result['code']}",
            flush=True,
        )

    for index, product in enumerate(products):
        by_code = {
            str(row["country_code"]): row
            for row in product.get("countries", [])
        }
        if by_code.keys() != country_results.keys():
            raise VerificationError(f"全局产品 {index} 国家集合不一致")
        baseline_sum = sum(
            result["snapshots"][index]["baseline_prefix_vp_count"]
            for result in country_results.values()
        )
        visible_sum = sum(
            result["snapshots"][index]["visible_prefix_vp_count"]
            for result in country_results.values()
        )
        current_sum = sum(
            result["snapshots"][index]["current_prefix_vp_count"]
            for result in country_results.values()
        )
        conservation = product.get("conservation") or {}
        if (
            baseline_sum != conservation.get("global_baseline_prefix_vp")
            or visible_sum != conservation.get("global_visible_prefix_vp")
            or current_sum != conservation.get("global_current_prefix_vp")
        ):
            raise VerificationError(f"全局产品 {index} 国家人口和不守恒")
        country_announces = sum(
            result["snapshots"][index]["country_update_counts"]["announce"]
            for result in country_results.values()
        )
        country_withdraws = sum(
            result["snapshots"][index]["country_update_counts"]["withdraw"]
            for result in country_results.values()
        )
        global_updates = product.get("activity", {}).get("global", {})
        if (
            country_announces != global_updates.get("announce")
            or country_withdraws != global_updates.get("withdraw")
        ):
            raise VerificationError(f"全局产品 {index} 国家 UPDATE 不守恒")
        for code, source in by_code.items():
            packaged = country_results[code]["snapshots"][index]
            for field in (
                "observed_at",
                "cohort_id",
                "baseline_asn_count",
                "baseline_prefix_vp_count",
                "visible_prefix_vp_count",
                "visible_prefix_vp_ratio",
                "affected_asn_count",
                "affected_asn_ratio",
                "visible_origin_asn_count",
                "visible_origin_asn_ratio",
                "ipv4",
                "ipv6",
                "dual_stack_classifications",
                "update_counts",
                "country_update_counts",
                "current_prefix_vp_count",
                "global_state_digest",
            ):
                source_field = (
                    "global_state_digest"
                    if field == "global_state_digest"
                    else field
                )
                if _canonical(packaged.get(field)) != _canonical(
                    source.get(source_field)
                ):
                    raise VerificationError(
                        f"{code} 产品 {index} 字段不一致：{field}"
                    )

    iran = _compare_iran(country_results["IR"], iran_baseline)
    samples = {
        code: {
            key: country_results[code][key]
            for key in (
                "cohort_id",
                "baseline_origin_asn_count",
                "baseline_prefix_vp_count",
                "baseline_ipv4_prefix_vp_count",
                "baseline_ipv6_prefix_vp_count",
                "snapshot_count",
                "asn_state_count",
            )
        }
        for code in SAMPLE_CODES
    }
    for result in country_results.values():
        result.pop("snapshots", None)
        result.pop("directory", None)
    return {
        "schema_version": "rrc25-global-country-package-verification/v1",
        "status": "pass",
        "run_id": catalog.get("run_id"),
        "dataset_id": catalog.get("dataset_id"),
        "revision": catalog.get("revision"),
        "mapping_version": catalog.get("mapping_version"),
        "formal_manifest_sha256": _sha256(
            global_root / "checkpoints/formal/manifest.json"
        ),
        "formal_state_digest": formal_manifest.get("state_digest"),
        "append_state_digest": (
            None
            if append_summary is None
            else append_summary.get("state_digest")
        ),
        "country_count": len(country_results),
        "observation_count_per_country": len(expected_times),
        "country_snapshot_count": len(expected_times) * len(country_results),
        "asn_state_count": sum(
            result["asn_state_count"] for result in country_results.values()
        ),
        "global_baseline_prefix_vp": formal_manifest.get(
            "conservation", {}
        ).get("global_baseline_prefix_vp"),
        "iran_baseline_comparison": iran,
        "non_iran_samples": samples,
        "checks": {
            "all_deliverable_hashes": "pass",
            "shared_country_timeline": "pass",
            "continuation_without_rib_or_prior_update_replay": (
                "not_applicable" if append_summary is None else "pass"
            ),
            "country_and_address_family_conservation": "pass",
            "asn_classification_conservation": "pass",
            "global_country_unknown_conservation": "pass",
            "country_update_activity_conservation": "pass",
            "global_product_projection_identity": "pass",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--global-root", type=Path, required=True)
    parser.add_argument("--iran-baseline", type=Path, required=True)
    parser.add_argument("--append-root", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = verify(
        arguments.package_root,
        arguments.global_root,
        arguments.iran_baseline,
        arguments.append_root,
    )
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    if arguments.output:
        arguments.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
