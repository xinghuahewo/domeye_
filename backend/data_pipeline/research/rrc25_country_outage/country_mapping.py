"""RRC25 国家中断研究使用的 AS→国家映射冻结器。

本模块只读取调用方显式提供的 CSV 普通文件，不实例化 ``BGPInfo``、不访问
数据库，也不写输出目录。兼容视图刻意复现旧核心 ``drop_duplicates(...,
keep='first')`` 的语义，同时把重复冲突和缺失国家保留为质量事实。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


SNAPSHOT_SCHEMA_VERSION = "as-country-mapping-snapshot/v1"
SNAPSHOT_ID_SCHEMA = "as_country_mapping_snapshot_id_v1"
MAX_MAPPING_BYTES = 512 * 1024 * 1024
ASN_RE = re.compile(r"^(?:AS)?([0-9]{1,10})$", re.IGNORECASE)
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


class CountryMappingError(ValueError):
    """映射源或字段不满足可复现冻结要求。"""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _stable_id(identity: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return "asmap_v1_" + digest[:32]


def _read_regular_file(path: Path, maximum_bytes: int) -> Tuple[bytes, os.stat_result]:
    try:
        initial = path.lstat()
    except OSError as error:
        raise CountryMappingError(f"无法读取 AS 国家映射：{path}") from error
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise CountryMappingError("AS 国家映射必须是非符号链接普通文件")
    if initial.st_size > maximum_bytes:
        raise CountryMappingError(f"AS 国家映射超过 {maximum_bytes} 字节限制")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CountryMappingError("无法只读打开 AS 国家映射") from error
    chunks: List[bytes] = []
    total = 0
    try:
        before = os.fstat(descriptor)
        identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(initial, field) != getattr(before, field) for field in identity_fields):
            raise CountryMappingError("AS 国家映射在打开前发生变化")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum_bytes:
                raise CountryMappingError(f"AS 国家映射超过 {maximum_bytes} 字节限制")
            chunks.append(block)
        after = os.fstat(descriptor)
        if any(getattr(before, field) != getattr(after, field) for field in identity_fields):
            raise CountryMappingError("AS 国家映射在读取期间发生变化")
    finally:
        os.close(descriptor)
    return b"".join(chunks), before


def _normalize_asn(value: Any) -> int:
    text = str(value).strip()
    matched = ASN_RE.fullmatch(text)
    if matched is None:
        raise CountryMappingError("ASN 格式非法")
    asn = int(matched.group(1))
    if asn <= 0 or asn > 4_294_967_295:
        raise CountryMappingError("ASN 越出 1..4294967295")
    return asn


def _normalize_country(value: Any) -> str | None:
    text = str(value).strip().upper()
    if not text:
        return None
    if COUNTRY_RE.fullmatch(text) is None:
        raise CountryMappingError("国家代码必须是两位大写字母")
    return text


def freeze_as_country_mapping(
    path: str | os.PathLike[str],
    *,
    target_country: str = "IR",
    maximum_bytes: int = MAX_MAPPING_BYTES,
) -> Dict[str, Any]:
    """读取旧映射并返回可直接规范序列化的确定性快照。

    同一 ASN 首条合法记录进入 ``rows``。后续相同国家计为重复；后续不同
    国家计为冲突，但兼容视图仍保留第一条，匹配旧系统行为。非法 ASN、非法
    国家和空国家都不会被静默丢弃，分别进入计数与有界样本。
    """

    if not isinstance(maximum_bytes, int) or isinstance(maximum_bytes, bool) or maximum_bytes <= 0:
        raise CountryMappingError("maximum_bytes 必须是正整数")
    target = _normalize_country(target_country)
    if target is None:
        raise CountryMappingError("target_country 不能为空")

    source = Path(path)
    payload, metadata = _read_regular_file(source, maximum_bytes)
    source_sha256 = hashlib.sha256(payload).hexdigest()
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CountryMappingError("AS 国家映射必须是 UTF-8 CSV") from error

    reader = csv.DictReader(io.StringIO(text, newline=""))
    fieldnames = reader.fieldnames
    if not fieldnames or len(fieldnames) != len(set(fieldnames)):
        raise CountryMappingError("CSV 表头缺失或字段重复")
    required = {"asn", "as_country"}
    if not required.issubset(fieldnames):
        raise CountryMappingError("CSV 必须包含 asn 和 as_country")

    first_mapping: Dict[int, str | None] = {}
    first_line: Dict[int, int] = {}
    duplicate_same_count = 0
    conflicts: List[Dict[str, Any]] = []
    invalid_samples: List[Dict[str, Any]] = []
    invalid_count = 0

    for line_number, row in enumerate(reader, start=2):
        if None in row:
            invalid_count += 1
            if len(invalid_samples) < 20:
                invalid_samples.append({"line_number": line_number, "reason": "extra_columns"})
            continue
        try:
            asn = _normalize_asn(row.get("asn", ""))
            country = _normalize_country(row.get("as_country", ""))
        except CountryMappingError as error:
            invalid_count += 1
            if len(invalid_samples) < 20:
                invalid_samples.append({"line_number": line_number, "reason": str(error)})
            continue

        if asn not in first_mapping:
            first_mapping[asn] = country
            first_line[asn] = line_number
            continue
        if first_mapping[asn] == country:
            duplicate_same_count += 1
            continue
        conflicts.append(
            {
                "asn": asn,
                "kept_country": first_mapping[asn],
                "kept_line_number": first_line[asn],
                "conflicting_country": country,
                "conflicting_line_number": line_number,
            }
        )

    rows = [
        {
            "asn": asn,
            "country_code": first_mapping[asn],
            "value_state": "observed" if first_mapping[asn] is not None else "mapping_missing",
            "source_line_number": first_line[asn],
        }
        for asn in sorted(first_mapping)
    ]
    conflict_asns = sorted({row["asn"] for row in conflicts})
    semantic = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source_file_sha256": source_sha256,
        "compatibility_policy": "first_valid_row_wins",
        "target_country": target,
        "rows": rows,
        "conflicts": sorted(
            conflicts,
            key=lambda row: (row["asn"], row["conflicting_line_number"]),
        ),
        "invalid": {
            "count": invalid_count,
            "samples": invalid_samples,
            "samples_truncated": invalid_count > len(invalid_samples),
        },
        "summary": {
            "unique_asn_count": len(rows),
            "target_country_asn_count": sum(
                1 for row in rows if row["country_code"] == target
            ),
            "missing_country_count": sum(
                1 for row in rows if row["country_code"] is None
            ),
            "duplicate_same_count": duplicate_same_count,
            "conflict_record_count": len(conflicts),
            "conflict_asn_count": len(conflict_asns),
        },
    }
    snapshot_id = _stable_id(
        {
            "schema": SNAPSHOT_ID_SCHEMA,
            "mapping": semantic,
        }
    )
    return {
        "snapshot_id": snapshot_id,
        **semantic,
        "source_metadata": {
            "size_bytes": metadata.st_size,
            "basename": source.name,
        },
        "semantic_fingerprint_sha256": hashlib.sha256(
            canonical_json(semantic).encode("utf-8")
        ).hexdigest(),
    }


__all__ = [
    "CountryMappingError",
    "MAX_MAPPING_BYTES",
    "SNAPSHOT_SCHEMA_VERSION",
    "canonical_json",
    "freeze_as_country_mapping",
]
