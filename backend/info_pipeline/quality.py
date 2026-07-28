"""四个核心 INFO 文件的一期质量探针。"""

from __future__ import annotations

import ast
import csv
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from .catalog import (
    CORE_PHASE_FILE_NAMES,
    CSV_FIELD_SIZE_LIMIT_BYTES,
    QUALITY_GATE_VERSION,
    SPEC_BY_NAME,
)
from .excel import ExcelReadError, iter_first_sheet_values
from .manifest import ManifestError, validate_manifest
from .output import write_text_exclusive


class QualityError(ValueError):
    """质量探针无法完成，而不是普通数据质量告警。"""


_AS_LIST_COLUMNS = (
    "import_as",
    "export_as",
    "sibling_as",
    "v4Upstream",
    "v4Downstream",
    "v4Peer",
    "v6Upstream",
    "v6Downstream",
    "v6Peer",
)
_AS_RELATION_COLUMNS = (
    "sibling_as",
    "v4Upstream",
    "v4Downstream",
    "v4Peer",
    "v6Upstream",
    "v6Downstream",
    "v6Peer",
)


def parse_asn(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("布尔值不是 ASN")
    text = str(value).strip()
    if not text or not text.isdigit():
        raise ValueError("ASN 必须是十进制非负整数")
    result = int(text)
    if result > 4_294_967_295:
        raise ValueError("ASN 超出 32 位无符号范围")
    return result


def parse_literal_list(value: Any, *, field_name: str) -> list[Any]:
    """替代旧代码的 eval，并严格要求最终值为列表。"""

    if value is None:
        return []
    if isinstance(value, float) and math.isnan(value):
        return []
    if isinstance(value, list):
        return value
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"{field_name} 不是安全的 Python 字面量列表") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{field_name} 的最终类型必须是 list")
    return parsed


def _parse_relation_asn(value: Any) -> int:
    text = str(value).strip()
    if text.upper().startswith("AS"):
        text = text[2:]
    return parse_asn(text)


def _rule(
    rule_id: str,
    *,
    blocking: bool,
    passed: bool,
    observed: Any,
    expected: Any,
    evidence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "rule_id": rule_id,
        "rule_version": 1,
        "blocking": blocking,
        "status": "pass" if passed else "fail",
        "observed": observed,
        "expected": expected,
        "evidence": dict(evidence or {}),
    }


def _manifest_file_map(manifest: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {str(item["name"]): item for item in manifest["files"]}


def _open_dict_rows(path: Path, delimiter: str = ","):
    encoding = SPEC_BY_NAME[path.name].encoding
    if encoding is None:
        raise QualityError(f"{path.name} 文本编码合同缺失")
    stream = path.open("r", encoding=encoding, newline="")
    try:
        csv.field_size_limit(CSV_FIELD_SIZE_LIMIT_BYTES)
        reader = csv.DictReader(stream, delimiter=delimiter, strict=True)
        if reader.fieldnames is None:
            raise QualityError(f"{path.name} 缺少表头")
        yield reader
    finally:
        stream.close()


def _probe_as_entity(path: Path) -> Dict[str, Any]:
    logical_count = 0
    invalid_asn = 0
    duplicate_asn = 0
    malformed_list = 0
    invalid_relation_target = 0
    seen = set()
    samples: list[dict[str, Any]] = []
    try:
        for reader in _open_dict_rows(path):
            for source_row_no, row in enumerate(reader, start=2):
                logical_count += 1
                try:
                    asn = parse_asn(row.get("asn"))
                except ValueError as exc:
                    invalid_asn += 1
                    if len(samples) < 20:
                        samples.append(
                            {
                                "source_row_no": source_row_no,
                                "reason": "invalid_asn",
                                "detail": str(exc),
                            }
                        )
                    continue
                if asn in seen:
                    duplicate_asn += 1
                else:
                    seen.add(asn)
                for field_name in _AS_LIST_COLUMNS:
                    try:
                        parsed_list = parse_literal_list(
                            row.get(field_name),
                            field_name=field_name,
                        )
                    except ValueError as exc:
                        malformed_list += 1
                        if len(samples) < 20:
                            samples.append(
                                {
                                    "source_row_no": source_row_no,
                                    "natural_key": str(asn),
                                    "reason": "unsafe_list",
                                    "field": field_name,
                                    "detail": str(exc),
                                }
                            )
                        continue
                    if field_name in _AS_RELATION_COLUMNS:
                        for target in parsed_list:
                            try:
                                _parse_relation_asn(target)
                            except ValueError:
                                invalid_relation_target += 1
                                if len(samples) < 20:
                                    samples.append(
                                        {
                                            "source_row_no": source_row_no,
                                            "natural_key": str(asn),
                                            "reason": "invalid_relation_target",
                                            "field": field_name,
                                            "value_hash": hashlib.sha256(
                                                str(target).encode("utf-8")
                                            ).hexdigest(),
                                        }
                                    )
    except (UnicodeDecodeError, csv.Error) as exc:
        raise QualityError(f"{path.name} CSV 解析失败：{exc}") from exc
    return {
        "logical_record_count": logical_count,
        "unique_valid_asn_count": len(seen),
        "invalid_asn_count": invalid_asn,
        "duplicate_asn_count": duplicate_asn,
        "unsafe_list_count": malformed_list,
        "invalid_relation_target_count": invalid_relation_target,
        "samples": samples,
    }


def _probe_important_as(path: Path) -> Dict[str, Any]:
    logical_count = 0
    invalid_asn = 0
    duplicate_asn = 0
    seen = set()
    try:
        for reader in _open_dict_rows(path):
            for row in reader:
                logical_count += 1
                try:
                    asn = parse_asn(row.get("aut-num"))
                except ValueError:
                    invalid_asn += 1
                    continue
                if asn in seen:
                    duplicate_asn += 1
                else:
                    seen.add(asn)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise QualityError(f"{path.name} CSV 解析失败：{exc}") from exc
    return {
        "logical_record_count": logical_count,
        "unique_valid_asn_count": len(seen),
        "invalid_asn_count": invalid_asn,
        "duplicate_asn_count": duplicate_asn,
    }


def _probe_prefix(path: Path) -> Dict[str, Any]:
    logical_count = 0
    empty_prefix = 0
    invalid_prefix = 0
    duplicate_raw = 0
    noncanonical = 0
    malformed_domain_list = 0
    invalid_domain_item = 0
    seen = set()
    samples: list[dict[str, Any]] = []
    try:
        for reader in _open_dict_rows(path):
            for source_row_no, row in enumerate(reader, start=2):
                logical_count += 1
                raw = (row.get("prefix") or "").strip()
                if not raw:
                    empty_prefix += 1
                    continue
                try:
                    parsed = ipaddress.ip_network(raw, strict=False)
                except ValueError:
                    invalid_prefix += 1
                    if len(samples) < 20:
                        samples.append(
                            {
                                "source_row_no": source_row_no,
                                "natural_key": raw,
                                "reason": "invalid_prefix",
                            }
                        )
                    continue
                if raw in seen:
                    duplicate_raw += 1
                else:
                    seen.add(raw)
                if str(parsed) != raw:
                    noncanonical += 1
                for field_name in ("domain", "domain_auth"):
                    try:
                        domain_values = parse_literal_list(
                            row.get(field_name),
                            field_name=field_name,
                        )
                    except ValueError as exc:
                        malformed_domain_list += 1
                        if len(samples) < 20:
                            samples.append(
                                {
                                    "source_row_no": source_row_no,
                                    "natural_key": raw,
                                    "reason": "unsafe_list",
                                    "field": field_name,
                                    "detail": str(exc),
                                }
                            )
                        continue
                    for domain_value in domain_values:
                        if not isinstance(domain_value, str) or not domain_value.strip():
                            invalid_domain_item += 1
    except (UnicodeDecodeError, csv.Error) as exc:
        raise QualityError(f"{path.name} CSV 解析失败：{exc}") from exc
    return {
        "logical_record_count": logical_count,
        "unique_valid_prefix_count": len(seen),
        "empty_prefix_count": empty_prefix,
        "invalid_prefix_count": invalid_prefix,
        "duplicate_raw_prefix_count": duplicate_raw,
        "noncanonical_prefix_count": noncanonical,
        "unsafe_domain_list_count": malformed_domain_list,
        "invalid_domain_item_count": invalid_domain_item,
        "samples": samples,
    }


def _probe_country(path: Path) -> Dict[str, Any]:
    try:
        rows = iter_first_sheet_values(path)
        header = ["" if value is None else str(value).strip() for value in next(rows)]
        positions = {name: index for index, name in enumerate(header)}
        logical_count = 0
        missing_alpha2 = 0
        invalid_alpha2 = 0
        duplicate_alpha2 = 0
        missing_coordinate = 0
        seen = set()
        for row in rows:
            logical_count += 1
            code_value = row[positions["two_letter_code"]]
            code = "" if code_value is None else str(code_value).strip().upper()
            if not code:
                missing_alpha2 += 1
            elif len(code) != 2 or not code.isalpha():
                invalid_alpha2 += 1
            elif code in seen:
                duplicate_alpha2 += 1
            else:
                seen.add(code)
            latitude = row[positions["latitude"]]
            longitude = row[positions["longitude"]]
            if latitude in (None, "") or longitude in (None, ""):
                missing_coordinate += 1
    except (KeyError, StopIteration) as exc:
        raise QualityError(f"{path.name} 表头或数据为空：{exc}") from exc
    except ExcelReadError as exc:
        raise QualityError(f"{path.name} Excel 解析失败：{exc}") from exc
    return {
        "logical_record_count": logical_count,
        "unique_alpha2_count": len(seen),
        "missing_alpha2_count": missing_alpha2,
        "invalid_alpha2_count": invalid_alpha2,
        "duplicate_alpha2_count": duplicate_alpha2,
        "missing_coordinate_count": missing_coordinate,
    }


def probe_core_files(
    source_dir: os.PathLike[str] | str,
    manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    """流式检查四个一期文件，并输出机器可读的阻断/披露规则。"""

    try:
        validate_manifest(manifest)
    except ManifestError as exc:
        raise QualityError(f"manifest 自校验失败：{exc}") from exc
    root = Path(source_dir)
    if root.is_symlink() or not root.is_dir():
        raise QualityError(f"质量探针来源必须是实际目录：{root}")

    file_map = _manifest_file_map(manifest)
    probes = {
        "as_entity.csv": _probe_as_entity(root / "as_entity.csv"),
        "important_as.csv": _probe_important_as(root / "important_as.csv"),
        "ip_bgp_entity.csv": _probe_prefix(root / "ip_bgp_entity.csv"),
        "country.xlsx": _probe_country(root / "country.xlsx"),
    }
    rules = []
    for name in CORE_PHASE_FILE_NAMES:
        observed = probes[name]["logical_record_count"]
        expected = file_map[name]["logical_record_count"]
        rules.append(
            _rule(
                f"core.logical_record_count.{name}",
                blocking=True,
                passed=observed == expected,
                observed=observed,
                expected=expected,
            )
        )

    as_probe = probes["as_entity.csv"]
    rules.extend(
        [
            _rule(
                "core.as_entity.valid_asn",
                blocking=True,
                passed=as_probe["invalid_asn_count"] == 0,
                observed=as_probe["invalid_asn_count"],
                expected=0,
            ),
            _rule(
                "core.as_entity.unique_asn",
                blocking=True,
                passed=as_probe["duplicate_asn_count"] == 0,
                observed=as_probe["duplicate_asn_count"],
                expected=0,
            ),
            _rule(
                "core.as_entity.safe_literal_lists",
                blocking=True,
                passed=as_probe["unsafe_list_count"] == 0,
                observed=as_probe["unsafe_list_count"],
                expected=0,
                evidence={"samples": as_probe["samples"]},
            ),
            _rule(
                "core.as_entity.valid_relation_targets",
                blocking=True,
                passed=as_probe["invalid_relation_target_count"] == 0,
                observed=as_probe["invalid_relation_target_count"],
                expected=0,
                evidence={"samples": as_probe["samples"]},
            ),
        ]
    )
    important_probe = probes["important_as.csv"]
    rules.extend(
        [
            _rule(
                "core.important_as.valid_asn",
                blocking=True,
                passed=important_probe["invalid_asn_count"] == 0,
                observed=important_probe["invalid_asn_count"],
                expected=0,
            ),
            _rule(
                "core.important_as.unique_asn",
                blocking=True,
                passed=important_probe["duplicate_asn_count"] == 0,
                observed=important_probe["duplicate_asn_count"],
                expected=0,
            ),
        ]
    )
    prefix_probe = probes["ip_bgp_entity.csv"]
    rules.extend(
        [
            _rule(
                "core.prefix.valid_natural_key",
                blocking=True,
                passed=(
                    prefix_probe["empty_prefix_count"] == 0
                    and prefix_probe["invalid_prefix_count"] == 0
                ),
                observed={
                    "empty": prefix_probe["empty_prefix_count"],
                    "invalid": prefix_probe["invalid_prefix_count"],
                },
                expected={"empty": 0, "invalid": 0},
            ),
            _rule(
                "core.prefix.unique_raw_key",
                blocking=True,
                passed=prefix_probe["duplicate_raw_prefix_count"] == 0,
                observed=prefix_probe["duplicate_raw_prefix_count"],
                expected=0,
            ),
            _rule(
                "core.prefix.safe_domain_lists",
                blocking=True,
                passed=(
                    prefix_probe["unsafe_domain_list_count"] == 0
                    and prefix_probe["invalid_domain_item_count"] == 0
                ),
                observed={
                    "unsafe_list": prefix_probe["unsafe_domain_list_count"],
                    "invalid_item": prefix_probe["invalid_domain_item_count"],
                },
                expected={"unsafe_list": 0, "invalid_item": 0},
                evidence={"samples": prefix_probe["samples"]},
            ),
            _rule(
                "core.prefix.noncanonical_disclosure",
                blocking=False,
                passed=True,
                observed=prefix_probe["noncanonical_prefix_count"],
                expected="显式披露并同时保留 raw/canonical",
            ),
        ]
    )
    country_probe = probes["country.xlsx"]
    rules.extend(
        [
            _rule(
                "core.country.unique_valid_alpha2",
                blocking=True,
                passed=(
                    country_probe["invalid_alpha2_count"] == 0
                    and country_probe["duplicate_alpha2_count"] == 0
                ),
                observed={
                    "invalid": country_probe["invalid_alpha2_count"],
                    "duplicate": country_probe["duplicate_alpha2_count"],
                },
                expected={"invalid": 0, "duplicate": 0},
            ),
            _rule(
                "core.country.completeness_disclosure",
                blocking=False,
                passed=True,
                observed={
                    "missing_alpha2": country_probe["missing_alpha2_count"],
                    "missing_coordinate": country_probe["missing_coordinate_count"],
                },
                expected="允许缺失，但必须披露且不得伪造",
            ),
        ]
    )

    blocking_failures = [
        rule["rule_id"]
        for rule in rules
        if rule["blocking"] and rule["status"] != "pass"
    ]
    return {
        "schema_version": 1,
        "component": "static_info_quality",
        "quality_gate_version": QUALITY_GATE_VERSION,
        "content_id": manifest["content_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "scope": "core_four_files",
        "status": "pass" if not blocking_failures else "fail",
        "blocking_failure_count": len(blocking_failures),
        "blocking_failures": blocking_failures,
        "files": probes,
        "rules": rules,
    }


def write_quality_report(
    report: Mapping[str, Any],
    output: os.PathLike[str] | str,
) -> None:
    write_text_exclusive(
        output,
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
