"""把 S1 release 扩展为全部 24 个 INFO 文件的可追溯候选数据集。"""

from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, TextIO, Tuple

from .catalog import (
    CORE_PHASE_FILE_NAMES,
    CSV_FIELD_SIZE_LIMIT_BYTES,
    DATA_FILE_SPECS,
    FULL_IMPORTER_VERSION,
    FULL_PHASE_FILE_NAMES,
    PARSER_VERSION,
    SPEC_BY_NAME,
    DataFileSpec,
)
from .excel import ExcelReadError, iter_first_sheet_values
from .loader import (
    DockerPsql,
    LoadError,
    _create_stage_spool,
    _is_source_loaded,
    _json_literal,
    _optional_bool,
    _optional_float,
    _optional_int,
    _source_file_identity,
    _sql_literal,
    _verify_source_file,
)
from .manifest import validate_manifest
from .quality import parse_asn, parse_literal_list
from .stream_json import iter_top_level_object


@dataclass(frozen=True)
class FullRecord:
    record_kind: str
    natural_key: str
    disposition: str
    reason_code: Optional[str]
    payload: Mapping[str, Any]


class RecordQuarantine(ValueError):
    """单条来源记录可解释地进入隔离，而不是让整个文件静默跳过。"""

    def __init__(self, reason_code: str, natural_key: str = "") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.natural_key = natural_key


def _validate_spool_capacity(manifest: Mapping[str, Any]) -> None:
    configured = os.environ.get("DOMEYE_CORE_INFO_SPOOL_DIR")
    spool_root = Path(configured) if configured else Path(tempfile.gettempdir())
    if spool_root.is_symlink() or not spool_root.is_dir():
        raise LoadError(f"S2 临时盘目录无效或为软链接：{spool_root}")
    file_map = {
        str(item["name"]): item
        for item in manifest.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    try:
        largest_source = max(
            int(file_map[name]["size_bytes"])
            for name in FULL_PHASE_FILE_NAMES
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LoadError("S2 manifest 缺少临时盘预算所需的文件大小") from exc
    required_free = largest_source * 4 + 2 * 1024**3
    available_free = shutil.disk_usage(spool_root).free
    if available_free < required_free:
        raise LoadError(
            "S2 临时盘空间不足："
            f"available={available_free} required={required_free} "
            f"spool={spool_root}；"
            "请把 DOMEYE_CORE_INFO_SPOOL_DIR 指向受控的大容量文件系统"
        )


def _safe_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    return str(value)


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        _safe_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _quality_flag(code: str, *, blocking: bool) -> Mapping[str, Any]:
    return {"code": code, "blocking": blocking}


def _accepted(
    record_kind: str,
    natural_key: str,
    payload: Mapping[str, Any],
) -> FullRecord:
    return FullRecord(record_kind, natural_key, "accepted", None, payload)


def _network_or_none(value: Any) -> Tuple[Optional[str], Optional[int]]:
    text = str(value or "").strip()
    if not text:
        return None, None
    try:
        network = ipaddress.ip_network(text, strict=False)
    except ValueError:
        return None, None
    return str(network), network.version


def _asn_or_none(value: Any) -> Optional[int]:
    try:
        return parse_asn(value)
    except ValueError:
        return None


def _domain_record(row: Mapping[str, Any], spec: DataFileSpec) -> FullRecord:
    domain_key = "" if row.get("url") is None else str(row.get("url"))
    if not domain_key.strip():
        raise RecordQuarantine("empty_domain_key")
    addresses = []
    for role, field in (
        ("resolved", "ip"),
        ("resolved_prefix", "ip_prefix"),
        ("authoritative", "auth_ip"),
    ):
        raw = "" if row.get(field) is None else str(row.get(field))
        if not raw.strip():
            continue
        ip_value = None
        prefix_cidr = None
        quality_status = "unparsed"
        try:
            if role == "resolved_prefix":
                prefix_cidr = str(ipaddress.ip_network(raw.strip(), strict=False))
            else:
                ip_value = str(ipaddress.ip_address(raw.strip()))
            quality_status = "valid"
        except ValueError:
            pass
        addresses.append(
            {
                "address_role": role,
                "ordinal": 1,
                "value_raw": raw,
                "ip_value": ip_value,
                "prefix_cidr": prefix_cidr,
                "quality_status": quality_status,
            }
        )
    return _accepted(
        "domain",
        domain_key,
        {
            "domain_key_raw": domain_key,
            "normalized_key": domain_key,
            "title": row.get("title") or None,
            "industry": row.get("industry") or None,
            "ip_raw": row.get("ip") or None,
            "ip_prefix_raw": row.get("ip_prefix") or None,
            "authoritative_ip_raw": row.get("auth_ip") or None,
            "source_priority": spec.source_priority,
            "addresses": addresses,
            "quality_flags": [],
            "attributes": {},
        },
    )


def _pfx2as_record(raw: Mapping[str, Any]) -> FullRecord:
    key = str(raw["key"])
    asn = _asn_or_none(key)
    if asn is None:
        raise RecordQuarantine("invalid_pfx2as_asn", key)
    value = raw["value"]
    if not isinstance(value, dict):
        raise RecordQuarantine("invalid_pfx2as_mapping", key)
    prefixes = []
    flags = []
    for ordinal, (prefix_raw, source_value) in enumerate(value.items(), start=1):
        prefix_text = str(prefix_raw)
        prefix_cidr, _afi = _network_or_none(prefix_text)
        quality_status = "valid" if prefix_cidr else "invalid_prefix"
        if prefix_cidr is None:
            flags.append(_quality_flag("invalid_pfx2as_prefix", blocking=False))
        prefixes.append(
            {
                "ordinal": ordinal,
                "prefix_raw": prefix_text,
                "prefix_cidr": prefix_cidr,
                "source_value": _safe_json(source_value),
                "quality_status": quality_status,
            }
        )
    return _accepted(
        "as_prefix_history",
        key,
        {
            "asn": asn,
            "prefixes": prefixes,
            "source_active": True,
            "quality_flags": flags,
        },
    )


def _relation_record(
    raw: Mapping[str, Any],
    *,
    source_active: bool,
) -> FullRecord:
    key = str(raw["key"])
    asn = _asn_or_none(key)
    if asn is None:
        raise RecordQuarantine("invalid_relation_source_asn", key)
    value = raw["value"]
    if not isinstance(value, dict):
        raise RecordQuarantine("invalid_relation_mapping", key)
    relations = []
    flags = []
    relation_ordinal = 0
    for field_name, relation_kind in (
        ("provider", "provider"),
        ("customer", "customer"),
        ("peers", "peer"),
        ("peer", "peer"),
        ("sibling", "sibling"),
    ):
        if field_name not in value:
            continue
        targets = value[field_name]
        if not isinstance(targets, list):
            flags.append(
                _quality_flag(
                    f"relation_field_not_list:{field_name}",
                    blocking=source_active,
                )
            )
            continue
        for token in targets:
            relation_ordinal += 1
            target_asn = _asn_or_none(token)
            if target_asn is None:
                flags.append(
                    _quality_flag(
                        f"invalid_relation_target:{field_name}",
                        blocking=source_active,
                    )
                )
                continue
            relations.append(
                {
                    "target_asn": target_asn,
                    "relation_kind": relation_kind,
                    "afi": 0,
                    "ordinal": relation_ordinal,
                    "source_field": field_name,
                }
            )
    if (
        source_active
        and "peer" in value
        and "peers" in value
        and value["peer"] != value["peers"]
    ):
        flags.append(_quality_flag("peer_peers_mismatch", blocking=True))
    return _accepted(
        "as_relation",
        key,
        {
            "source_asn": asn,
            "relations": relations,
            "source_active": source_active,
            "quality_flags": flags,
        },
    )


def _important_domain_record(raw: Mapping[str, Any]) -> FullRecord:
    key = str(raw["key"])
    if not key.strip():
        raise RecordQuarantine("empty_important_domain")
    return _accepted(
        "important_domain",
        key,
        {
            "domain_key_raw": key,
            "source_value": _safe_json(raw["value"]),
            "source_active": True,
            "quality_flags": [],
        },
    )


def _private_as_record(
    raw: Mapping[str, Any],
    *,
    source_active: bool,
) -> FullRecord:
    public_raw = str(raw["key"])
    public_asn = _asn_or_none(public_raw)
    value = raw["value"]
    if not isinstance(value, dict):
        raise RecordQuarantine("invalid_private_as_mapping", public_raw)
    locations = []
    flags = []
    for ordinal, (private_raw_value, location) in enumerate(value.items(), start=1):
        private_raw = str(private_raw_value)
        private_asn = _asn_or_none(private_raw)
        valid = public_asn is not None and private_asn is not None
        if not valid:
            flags.append(_quality_flag("invalid_private_asn", blocking=False))
        location_value = location if isinstance(location, dict) else {}
        if not isinstance(location, dict):
            flags.append(_quality_flag("invalid_private_location", blocking=False))
        locations.append(
            {
                "ordinal": ordinal,
                "public_asn_raw": public_raw,
                "public_asn": public_asn,
                "private_asn_raw": private_raw,
                "private_asn": private_asn,
                "ip_num": _safe_json(location_value.get("ip_num")),
                "city": (
                    None
                    if location_value.get("city") is None
                    else str(location_value.get("city"))
                ),
                "quality_status": "valid" if valid else "invalid_asn",
            }
        )
    return _accepted(
        "private_as_location",
        public_raw,
        {
            "locations": locations,
            "source_active": source_active,
            "quality_flags": flags,
        },
    )


def _triplet_record(
    row: Mapping[str, Any],
    *,
    source_active: bool,
) -> FullRecord:
    try:
        first_as = parse_asn(row.get("first_as"))
        second_as = parse_asn(row.get("second_as"))
        third_as = parse_asn(row.get("third_as"))
        stability = float(str(row.get("stability")).strip())
        if not math.isfinite(stability):
            raise ValueError("stability 必须有限")
        appear_num = _optional_int(row.get("appear_num"))
        is_leak = _optional_bool(row.get("is_leak"))
    except (TypeError, ValueError) as exc:
        natural_key = "|".join(
            str(row.get(name) or "")
            for name in ("first_as", "second_as", "third_as")
        )
        raise RecordQuarantine("invalid_route_triplet", natural_key) from exc
    natural_key = f"{first_as}|{second_as}|{third_as}"
    return _accepted(
        "route_triplet",
        natural_key,
        {
            "first_as": first_as,
            "second_as": second_as,
            "third_as": third_as,
            "appear_time_raw": row.get("appear_time") or None,
            "appear_num": appear_num,
            "stability": stability,
            "is_leak": is_leak,
            "source_active": source_active,
            "quality_flags": [],
            "attributes": {},
        },
    )


def _important_prefix_record(
    row: Mapping[str, Any],
    *,
    afi: int,
    source_active: bool,
) -> FullRecord:
    prefix_raw = str(row.get("prefix") or "")
    prefix_cidr, parsed_afi = _network_or_none(prefix_raw)
    if not prefix_raw.strip():
        raise RecordQuarantine("empty_important_prefix")
    if prefix_cidr is None or parsed_afi != afi:
        raise RecordQuarantine("invalid_important_prefix", prefix_raw)
    return _accepted(
        "important_prefix",
        prefix_raw,
        {
            "prefix_raw": prefix_raw,
            "prefix_cidr": prefix_cidr,
            "afi": afi,
            "number_raw": (
                None if row.get("number") is None else str(row.get("number"))
            ),
            "host_raw": None if row.get("host") is None else str(row.get("host")),
            "source_active": source_active,
            "quality_flags": [],
            "attributes": {},
        },
    )


def _dns_csv_record(row: Mapping[str, Any]) -> FullRecord:
    domain = None if row.get("domain") is None else str(row.get("domain"))
    ip_raw = None if row.get("ipaddress") is None else str(row.get("ipaddress"))
    index_value = next(
        (
            str(value)
            for key, value in row.items()
            if key not in {"domain", "ipaddress"} and value not in (None, "")
        ),
        None,
    )
    natural_key = f"{domain or ''}|{ip_raw or ''}|{index_value or ''}"
    return _accepted(
        "dns_observation",
        natural_key,
        {
            "dataset_kind": "top_nx",
            "domain_raw": domain,
            "ip_raw": ip_raw,
            "source_index_raw": index_value,
            "raw_line": None,
            "quality_status": (
                "valid" if domain and domain.strip() and ip_raw and ip_raw.strip()
                else "incomplete"
            ),
            "quality_flags": [],
        },
    )


def _dns_line_record(raw: Mapping[str, Any]) -> FullRecord:
    line = str(raw["line"])
    parts = line.split()
    domain = parts[0] if parts else None
    ip_raw = parts[1] if len(parts) > 1 else None
    return _accepted(
        "dns_observation",
        line,
        {
            "dataset_kind": "top_ip",
            "domain_raw": domain,
            "ip_raw": ip_raw,
            "source_index_raw": None,
            "raw_line": line,
            "quality_status": "valid" if len(parts) == 2 else "incomplete",
            "quality_flags": [],
        },
    )


def _as_rank_record(raw: Mapping[str, Any]) -> FullRecord:
    key = str(raw["key"])
    asn = _asn_or_none(key)
    if asn is None:
        raise RecordQuarantine("invalid_as_rank_asn", key)
    value = raw["value"]
    attributes = value if isinstance(value, dict) else {"value": value}
    country_code = attributes.get("country") or attributes.get("country_code")
    asn_degree = attributes.get("asnDegree")
    if country_code is None and isinstance(asn_degree, dict):
        country_code = asn_degree.get("country")
    return _accepted(
        "as_rank",
        key,
        {
            "asn": asn,
            "rank_value": attributes.get("rank"),
            "country_code": country_code,
            "organization_name": (
                attributes.get("organization")
                or attributes.get("org_name")
                or attributes.get("organization_name")
            ),
            "as_name": attributes.get("as_name") or attributes.get("asnName"),
            "as_type": attributes.get("type") or attributes.get("as_type"),
            "quality_flags": [],
            "attributes": _safe_json(attributes),
        },
    )


def _organization_record(row: Mapping[str, Any]) -> FullRecord:
    org_key = str(row.get("uuid") or "")
    if not org_key.strip():
        raise RecordQuarantine("empty_organization_key")
    try:
        sibling_values = parse_literal_list(
            row.get("sibling_as"),
            field_name="sibling_as",
        )
        v4_values = parse_literal_list(
            row.get("v4Prefixes"),
            field_name="v4Prefixes",
        )
        v6_values = parse_literal_list(
            row.get("v6Prefixes"),
            field_name="v6Prefixes",
        )
    except ValueError as exc:
        raise RecordQuarantine("unsafe_organization_list", org_key) from exc
    try:
        sibling_as_count = _optional_int(row.get("sibling_as_num"))
        v4_prefix_count = _optional_int(row.get("v4Prefixes_num"))
        v6_prefix_count = _optional_int(row.get("v6Prefixes_num"))
    except (TypeError, ValueError) as exc:
        raise RecordQuarantine(
            "invalid_organization_count",
            org_key,
        ) from exc
    sibling_as = []
    for ordinal, token in enumerate(sibling_values, start=1):
        parsed = _asn_or_none(token)
        sibling_as.append(
            {
                "ordinal": ordinal,
                "asn_token": str(token),
                "asn": parsed,
                "quality_status": "valid" if parsed is not None else "invalid_asn",
            }
        )
    prefixes = []
    for afi, values in ((4, v4_values), (6, v6_values)):
        for ordinal, token in enumerate(values, start=1):
            prefix_raw = str(token)
            prefix_cidr, parsed_afi = _network_or_none(prefix_raw)
            prefixes.append(
                {
                    "afi": afi,
                    "ordinal": ordinal,
                    "prefix_raw": prefix_raw,
                    "prefix_cidr": prefix_cidr if parsed_afi == afi else None,
                    "quality_status": (
                        "valid"
                        if prefix_cidr is not None and parsed_afi == afi
                        else "invalid_prefix"
                    ),
                }
            )
    return _accepted(
        "organization",
        org_key,
        {
            "org_key": org_key,
            "country_code": row.get("org_country") or None,
            "country_name_cn": row.get("org_country_cn") or None,
            "org_name": row.get("org_name") or None,
            "org_name_cn": row.get("org_name_cn") or None,
            "sibling_as_count": sibling_as_count,
            "v4_prefix_count": v4_prefix_count,
            "v6_prefix_count": v6_prefix_count,
            "sibling_as": sibling_as,
            "prefixes": prefixes,
            "quality_flags": [],
            "attributes": {},
        },
    )


def _legacy_record(
    raw: Mapping[str, Any],
    spec: DataFileSpec,
) -> FullRecord:
    if "key" in raw:
        natural_key = str(raw["key"])
    else:
        natural_key = next(
            (
                str(value)
                for key, value in raw.items()
                if key.lower() in {"asn", "aut-num", "uuid", "id"}
                and value not in (None, "")
            ),
            "",
        )
    safe_raw = _safe_json(raw)
    return _accepted(
        "legacy",
        natural_key,
        {
            "dataset_kind": spec.dataset_kind,
            "payload_sha256": _canonical_sha256(safe_raw),
            "source_active": False,
            "quality_flags": [],
        },
    )


def _transform(
    name: str,
    raw: Mapping[str, Any],
) -> FullRecord:
    spec = SPEC_BY_NAME[name]
    if name in {"website_entity.csv", "domain_cn.csv"}:
        return _domain_record(raw, spec)
    if name == "pfx2as_dict.txt":
        return _pfx2as_record(raw)
    if name in {"as_rel_dict.txt", "as_rel_dict_old.txt"}:
        return _relation_record(raw, source_active=name == "as_rel_dict.txt")
    if name == "domain_cn_center.txt":
        return _important_domain_record(raw)
    if name in {"private_as_dict_new.json", "private_as_dict.json"}:
        return _private_as_record(
            raw,
            source_active=name == "private_as_dict_new.json",
        )
    if name in {"triplet_20days.csv", "triplet_20days_1.csv"}:
        return _triplet_record(
            raw,
            source_active=name == "triplet_20days.csv",
        )
    if name == "ipv4_all_prefix.xls":
        return _important_prefix_record(raw, afi=4, source_active=False)
    if name == "ipv6_all_prefix.xls":
        return _important_prefix_record(raw, afi=6, source_active=False)
    if name == "top_nx.csv":
        return _dns_csv_record(raw)
    if name == "top_ip.txt":
        return _dns_line_record(raw)
    if name == "as_rank.json":
        return _as_rank_record(raw)
    if name == "org_entity.csv":
        return _organization_record(raw)
    return _legacy_record(raw, spec)


def _iter_csv(path: Path, spec: DataFileSpec) -> Iterator[Tuple[int, Mapping[str, Any]]]:
    if spec.encoding is None:
        raise LoadError(f"{path.name} 文本编码合同缺失")
    with path.open("r", encoding=spec.encoding, newline="") as stream:
        csv.field_size_limit(CSV_FIELD_SIZE_LIMIT_BYTES)
        reader = csv.DictReader(
            stream,
            delimiter=spec.delimiter or ",",
            strict=True,
        )
        if reader.fieldnames is None:
            raise LoadError(f"{path.name} 缺少 CSV 表头")
        for ordinal, row in enumerate(reader, start=1):
            yield ordinal, row


def _iter_json(
    path: Path,
    spec: DataFileSpec,
) -> Iterator[Tuple[int, Mapping[str, Any]]]:
    if spec.encoding is None:
        raise LoadError(f"{path.name} 文本编码合同缺失")
    with path.open("r", encoding=spec.encoding, newline="") as stream:
        for ordinal, key, value in iter_top_level_object(stream):
            yield ordinal, {"key": key, "value": value}


def _iter_lines(
    path: Path,
    spec: DataFileSpec,
) -> Iterator[Tuple[int, Mapping[str, Any]]]:
    ordinal = 0
    if spec.encoding is None:
        raise LoadError(f"{path.name} 文本编码合同缺失")
    with path.open("r", encoding=spec.encoding, newline="") as stream:
        for line in stream:
            stripped = line.strip()
            if not stripped:
                continue
            ordinal += 1
            yield ordinal, {"line": stripped}


def _iter_xlsx(path: Path) -> Iterator[Tuple[int, Mapping[str, Any]]]:
    try:
        rows = iter_first_sheet_values(path)
        try:
            header = [str(value or "").strip() for value in next(rows)]
        except StopIteration as exc:
            raise LoadError(f"{path.name} 工作表为空") from exc
        for ordinal, values in enumerate(rows, start=1):
            yield ordinal, dict(zip(header, values))
    except ExcelReadError as exc:
        raise LoadError(f"{path.name} Excel 解析失败：{exc}") from exc


def _iter_xls(path: Path) -> Iterator[Tuple[int, Mapping[str, Any]]]:
    yield from _iter_xlsx(path)


def _iter_source(
    path: Path,
    spec: DataFileSpec,
) -> Iterator[Tuple[int, Mapping[str, Any]]]:
    if spec.file_format == "csv":
        return _iter_csv(path, spec)
    if spec.file_format == "json":
        return _iter_json(path, spec)
    if spec.file_format == "line_text":
        return _iter_lines(path, spec)
    if spec.file_format == "xlsx":
        return _iter_xlsx(path)
    if spec.file_format == "xls":
        return _iter_xls(path)
    raise LoadError(f"{path.name} 使用未知文件格式：{spec.file_format}")


def _spool_full_file(path: Path, spec: DataFileSpec) -> Tuple[TextIO, int, int, int]:
    spool, writer = _create_stage_spool()
    logical_count = 0
    accepted_count = 0
    quarantined_count = 0
    try:
        for logical_count, raw in _iter_source(path, spec):
            safe_raw = _safe_json(raw)
            record_sha = _canonical_sha256(safe_raw)
            try:
                record = _transform(spec.name, safe_raw)
            except RecordQuarantine as exc:
                record = FullRecord(
                    record_kind=spec.dataset_kind,
                    natural_key=exc.natural_key,
                    disposition="quarantined",
                    reason_code=exc.reason_code,
                    payload={
                        "quality_flags": [
                            _quality_flag(exc.reason_code, blocking=False)
                        ],
                    },
                )
            if record.disposition == "accepted":
                accepted_count += 1
            else:
                quarantined_count += 1
            stage_payload = dict(record.payload)
            stage_payload["_source_raw"] = safe_raw
            writer.writerow(
                [
                    logical_count,
                    record.natural_key,
                    record_sha,
                    record.disposition,
                    record.reason_code or "",
                    record.record_kind,
                    json.dumps(
                        _safe_json(stage_payload),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                ]
            )
        spool.flush()
        return spool, logical_count, accepted_count, quarantined_count
    except BaseException:
        spool.close()
        raise


_FULL_COPY_HEAD = """\
BEGIN;
SET LOCAL standard_conforming_strings = on;
CREATE TEMP TABLE info_full_import_stage (
    source_row_no bigint NOT NULL,
    natural_key text NOT NULL,
    source_record_sha256 char(64) NOT NULL,
    disposition text NOT NULL,
    reason_code text NOT NULL,
    record_kind text NOT NULL,
    payload jsonb NOT NULL
) ON COMMIT DROP;
COPY info_full_import_stage(
    source_row_no, natural_key, source_record_sha256,
    disposition, reason_code, record_kind, payload
) FROM STDIN WITH (
    FORMAT csv,
    DELIMITER E'\\t',
    QUOTE '"',
    ESCAPE '"',
    ENCODING 'UTF8'
);
"""


def _full_tail(
    release_sk: int,
    source_file_sk: int,
    spec: DataFileSpec,
    logical_count: int,
    accepted_count: int,
    quarantined_count: int,
) -> str:
    domain_winner_sql = ""
    if spec.name == "domain_cn.csv":
        domain_winner_sql = f"""
UPDATE info.domain_record
SET source_active = false
WHERE release_sk = {release_sk};

WITH winner AS (
    SELECT DISTINCT ON (domain_key_raw)
        source_file_sk,
        source_row_no
    FROM info.domain_record
    WHERE release_sk = {release_sk}
    ORDER BY domain_key_raw, source_priority, source_row_no, source_file_sk
)
UPDATE info.domain_record AS domain
SET source_active = true
FROM winner
WHERE domain.release_sk = {release_sk}
  AND domain.source_file_sk = winner.source_file_sk
  AND domain.source_row_no = winner.source_row_no;
"""
    return f"""
INSERT INTO info.source_record(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    dataset_kind, record_kind, natural_key, disposition, reason_code,
    quality_flags, restricted_payload
)
SELECT
    {release_sk}, {source_file_sk}, source_row_no, source_record_sha256,
    {_sql_literal(spec.dataset_kind)}, record_kind, NULLIF(natural_key, ''),
    disposition, NULLIF(reason_code, ''),
    coalesce(payload->'quality_flags', '[]'::jsonb),
    payload->'_source_raw'
FROM info_full_import_stage;

INSERT INTO info.quarantine(
    release_sk, source_file_sk, source_row_no, natural_key,
    reason_code, raw_record_sha256, restricted_payload
)
SELECT
    {release_sk}, {source_file_sk}, source_row_no, NULLIF(natural_key, ''),
    reason_code, source_record_sha256, payload->'_source_raw'
FROM info_full_import_stage
WHERE disposition = 'quarantined';

INSERT INTO info.mapping_record(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    mapping_kind, natural_key, item_count, source_active
)
SELECT
    {release_sk}, {source_file_sk}, source_row_no, source_record_sha256,
    record_kind, natural_key,
    CASE record_kind
        WHEN 'as_prefix_history'
            THEN jsonb_array_length(payload->'prefixes')
        WHEN 'as_relation'
            THEN jsonb_array_length(payload->'relations')
        WHEN 'private_as_location'
            THEN jsonb_array_length(payload->'locations')
    END,
    (payload->>'source_active')::boolean
FROM info_full_import_stage
WHERE disposition = 'accepted'
  AND record_kind IN (
      'as_prefix_history', 'as_relation', 'private_as_location'
  );

INSERT INTO info.domain_record(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    domain_key_raw, normalized_key, title, industry,
    ip_raw, ip_prefix_raw, authoritative_ip_raw,
    source_priority, source_active, attributes
)
SELECT
    {release_sk}, {source_file_sk}, source_row_no, source_record_sha256,
    payload->>'domain_key_raw', payload->>'normalized_key',
    payload->>'title', payload->>'industry', payload->>'ip_raw',
    payload->>'ip_prefix_raw', payload->>'authoritative_ip_raw',
    (payload->>'source_priority')::integer, false, payload->'attributes'
FROM info_full_import_stage
WHERE disposition = 'accepted' AND record_kind = 'domain';

INSERT INTO info.domain_address(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    domain_key_raw, address_role, ordinal, value_raw,
    ip_value, prefix_cidr, quality_status
)
SELECT
    {release_sk}, {source_file_sk}, stage.source_row_no,
    stage.source_record_sha256, stage.payload->>'domain_key_raw',
    address->>'address_role', (address->>'ordinal')::integer,
    address->>'value_raw', (address->>'ip_value')::inet,
    (address->>'prefix_cidr')::cidr, address->>'quality_status'
FROM info_full_import_stage AS stage
CROSS JOIN LATERAL jsonb_array_elements(stage.payload->'addresses') AS address
WHERE stage.disposition = 'accepted' AND stage.record_kind = 'domain';

INSERT INTO info.as_prefix_history(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    asn, prefix_raw, prefix_cidr, source_value,
    quality_status, source_active, ordinal
)
SELECT
    {release_sk}, {source_file_sk}, stage.source_row_no,
    stage.source_record_sha256, (stage.payload->>'asn')::bigint,
    prefix->>'prefix_raw', (prefix->>'prefix_cidr')::cidr,
    prefix->'source_value', prefix->>'quality_status',
    (stage.payload->>'source_active')::boolean,
    (prefix->>'ordinal')::integer
FROM info_full_import_stage AS stage
CROSS JOIN LATERAL jsonb_array_elements(stage.payload->'prefixes') AS prefix
WHERE stage.disposition = 'accepted'
  AND stage.record_kind = 'as_prefix_history';

INSERT INTO info.prefix_origin(
    release_sk, prefix_raw, asn, origin_source, ordinal, source_value,
    source_file_sk, source_row_no, source_record_sha256
)
SELECT
    {release_sk}, prefix->>'prefix_raw', (stage.payload->>'asn')::bigint,
    'pfx2as', (prefix->>'ordinal')::integer, prefix->'source_value',
    {source_file_sk}, stage.source_row_no, stage.source_record_sha256
FROM info_full_import_stage AS stage
CROSS JOIN LATERAL jsonb_array_elements(stage.payload->'prefixes') AS prefix
WHERE stage.disposition = 'accepted'
  AND stage.record_kind = 'as_prefix_history'
  AND prefix->>'quality_status' = 'valid';

INSERT INTO info.as_relation(
    release_sk, source_asn, target_asn, relation_kind, afi, ordinal,
    source_field, source_file_sk, source_row_no, source_record_sha256,
    source_active
)
SELECT
    {release_sk}, (stage.payload->>'source_asn')::bigint,
    (relation->>'target_asn')::bigint, relation->>'relation_kind',
    (relation->>'afi')::smallint, (relation->>'ordinal')::integer,
    relation->>'source_field', {source_file_sk}, stage.source_row_no,
    stage.source_record_sha256,
    (stage.payload->>'source_active')::boolean
FROM info_full_import_stage AS stage
CROSS JOIN LATERAL jsonb_array_elements(stage.payload->'relations') AS relation
WHERE stage.disposition = 'accepted' AND stage.record_kind = 'as_relation';

INSERT INTO info.important_domain(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    domain_key_raw, source_value, source_active
)
SELECT
    {release_sk}, {source_file_sk}, source_row_no, source_record_sha256,
    payload->>'domain_key_raw', payload->'source_value',
    (payload->>'source_active')::boolean
FROM info_full_import_stage
WHERE disposition = 'accepted' AND record_kind = 'important_domain';

INSERT INTO info.private_as_location(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    public_asn_raw, public_asn, private_asn_raw, private_asn,
    ip_num, city, quality_status, source_active, ordinal
)
SELECT
    {release_sk}, {source_file_sk}, stage.source_row_no,
    stage.source_record_sha256, location->>'public_asn_raw',
    (location->>'public_asn')::bigint, location->>'private_asn_raw',
    (location->>'private_asn')::bigint, location->'ip_num',
    location->>'city', location->>'quality_status',
    (stage.payload->>'source_active')::boolean,
    (location->>'ordinal')::integer
FROM info_full_import_stage AS stage
CROSS JOIN LATERAL jsonb_array_elements(stage.payload->'locations') AS location
WHERE stage.disposition = 'accepted'
  AND stage.record_kind = 'private_as_location';

INSERT INTO info.route_triplet_baseline(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    first_as, second_as, third_as, appear_time_raw, appear_num,
    stability, is_leak, source_active, attributes
)
SELECT
    {release_sk}, {source_file_sk}, source_row_no, source_record_sha256,
    (payload->>'first_as')::bigint, (payload->>'second_as')::bigint,
    (payload->>'third_as')::bigint, payload->>'appear_time_raw',
    (payload->>'appear_num')::bigint,
    (payload->>'stability')::double precision,
    (payload->>'is_leak')::boolean,
    (payload->>'source_active')::boolean, payload->'attributes'
FROM info_full_import_stage
WHERE disposition = 'accepted' AND record_kind = 'route_triplet';

INSERT INTO info.important_prefix(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    prefix_raw, prefix_cidr, afi, number_raw, host_raw,
    source_active, attributes
)
SELECT
    {release_sk}, {source_file_sk}, source_row_no, source_record_sha256,
    payload->>'prefix_raw', (payload->>'prefix_cidr')::cidr,
    (payload->>'afi')::smallint, payload->>'number_raw',
    payload->>'host_raw', (payload->>'source_active')::boolean,
    payload->'attributes'
FROM info_full_import_stage
WHERE disposition = 'accepted' AND record_kind = 'important_prefix';

INSERT INTO info.dns_observation(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    dataset_kind, domain_raw, ip_raw, source_index_raw,
    raw_line, quality_status
)
SELECT
    {release_sk}, {source_file_sk}, source_row_no, source_record_sha256,
    payload->>'dataset_kind', payload->>'domain_raw', payload->>'ip_raw',
    payload->>'source_index_raw', payload->>'raw_line',
    payload->>'quality_status'
FROM info_full_import_stage
WHERE disposition = 'accepted' AND record_kind = 'dns_observation';

INSERT INTO info.as_rank(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    asn, rank_value, country_code, organization_name,
    as_name, as_type, attributes
)
SELECT
    {release_sk}, {source_file_sk}, source_row_no, source_record_sha256,
    (payload->>'asn')::bigint, payload->>'rank_value',
    payload->>'country_code', payload->>'organization_name',
    payload->>'as_name', payload->>'as_type', payload->'attributes'
FROM info_full_import_stage
WHERE disposition = 'accepted' AND record_kind = 'as_rank';

INSERT INTO info.organization(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    org_key, country_code, country_name_cn, org_name, org_name_cn,
    sibling_as_count, v4_prefix_count, v6_prefix_count, attributes
)
SELECT
    {release_sk}, {source_file_sk}, source_row_no, source_record_sha256,
    payload->>'org_key', payload->>'country_code',
    payload->>'country_name_cn', payload->>'org_name',
    payload->>'org_name_cn', (payload->>'sibling_as_count')::integer,
    (payload->>'v4_prefix_count')::integer,
    (payload->>'v6_prefix_count')::integer, payload->'attributes'
FROM info_full_import_stage
WHERE disposition = 'accepted' AND record_kind = 'organization';

INSERT INTO info.organization_as(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    org_key, ordinal, asn_token, asn, quality_status
)
SELECT
    {release_sk}, {source_file_sk}, stage.source_row_no,
    stage.source_record_sha256, stage.payload->>'org_key',
    (member->>'ordinal')::integer, member->>'asn_token',
    (member->>'asn')::bigint, member->>'quality_status'
FROM info_full_import_stage AS stage
CROSS JOIN LATERAL jsonb_array_elements(stage.payload->'sibling_as') AS member
WHERE stage.disposition = 'accepted' AND stage.record_kind = 'organization';

INSERT INTO info.organization_prefix(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    org_key, afi, ordinal, prefix_raw, prefix_cidr, quality_status
)
SELECT
    {release_sk}, {source_file_sk}, stage.source_row_no,
    stage.source_record_sha256, stage.payload->>'org_key',
    (prefix->>'afi')::smallint, (prefix->>'ordinal')::integer,
    prefix->>'prefix_raw', (prefix->>'prefix_cidr')::cidr,
    prefix->>'quality_status'
FROM info_full_import_stage AS stage
CROSS JOIN LATERAL jsonb_array_elements(stage.payload->'prefixes') AS prefix
WHERE stage.disposition = 'accepted' AND stage.record_kind = 'organization';

INSERT INTO info.legacy_record(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    dataset_kind, natural_key, payload, payload_sha256, source_active
)
SELECT
    {release_sk}, {source_file_sk}, source_row_no, source_record_sha256,
    payload->>'dataset_kind', NULLIF(natural_key, ''), payload->'_source_raw',
    (payload->>'payload_sha256')::char(64),
    (payload->>'source_active')::boolean
FROM info_full_import_stage
WHERE disposition = 'accepted' AND record_kind = 'legacy';

{domain_winner_sql}

UPDATE info.source_file
SET load_status = 'loaded',
    loaded_record_count = {accepted_count},
    quarantined_record_count = {quarantined_count},
    loaded_at = clock_timestamp()
WHERE source_file_sk = {source_file_sk}
  AND logical_record_count = {logical_count};

DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM info.source_file
        WHERE source_file_sk = {source_file_sk}
          AND load_status = 'loaded'
          AND loaded_record_count = {accepted_count}
          AND quarantined_record_count = {quarantined_count}
          AND loaded_record_count + quarantined_record_count
              = logical_record_count
    ) THEN
        RAISE EXCEPTION 'source_file 记录数对账失败：{spec.name}';
    END IF;
    IF (
        SELECT count(*)
        FROM info.source_record
        WHERE release_sk = {release_sk}
          AND source_file_sk = {source_file_sk}
    ) <> {logical_count} THEN
        RAISE EXCEPTION 'source_record 逐记录账本对账失败：{spec.name}';
    END IF;
END
$block$;
COMMIT;
"""


def _register_full_run(
    database: DockerPsql,
    manifest: Mapping[str, Any],
) -> Tuple[int, int, Dict[str, int], bool]:
    idempotency_key = hashlib.sha256(
        (
            str(manifest["manifest_sha256"])
            + "\0"
            + PARSER_VERSION
            + "\0"
            + FULL_IMPORTER_VERSION
            + "\0all_24_files"
        ).encode("utf-8")
    ).hexdigest()
    sql = f"""
BEGIN;
SELECT pg_advisory_xact_lock(
    hashtextextended(
        {_sql_literal('static_info_full:' + str(manifest['manifest_sha256']))},
        0
    )
);
DO $block$
DECLARE
    target_release bigint;
BEGIN
    SELECT release_sk INTO target_release
    FROM info.dataset_release
    WHERE content_id = {_sql_literal(manifest['content_id'])}
      AND manifest_sha256 = {_sql_literal(manifest['manifest_sha256'])}
      AND parser_version = {_sql_literal(manifest['parser_version'])}
      AND importer_config_sha256 =
          {_sql_literal(manifest['importer_config_sha256'])};
    IF target_release IS NULL THEN
        RAISE EXCEPTION 'S2 找不到与 manifest 完全一致的 S1 release';
    END IF;
    IF (
        SELECT count(*)
        FROM info.source_file
        WHERE release_sk = target_release
          AND name IN (
              'as_entity.csv', 'important_as.csv',
              'ip_bgp_entity.csv', 'country.xlsx'
          )
          AND load_status = 'loaded'
    ) <> 4 THEN
        RAISE EXCEPTION 'S2 入口失败：四核心文件尚未全部 loaded';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM info.active_release
        WHERE release_sk = target_release
    ) THEN
        RAISE EXCEPTION 'S2 只能扩展未激活的 shadow release';
    END IF;
END
$block$;

WITH target_release AS (
    SELECT release_sk
    FROM info.dataset_release
    WHERE manifest_sha256 = {_sql_literal(manifest['manifest_sha256'])}
), next_attempt AS (
    SELECT
        target_release.release_sk,
        coalesce(max(existing.attempt_no), 0) + 1 AS attempt_no
    FROM target_release
    LEFT JOIN info.import_run AS existing
      ON existing.release_sk = target_release.release_sk
    GROUP BY target_release.release_sk
)
INSERT INTO info.import_run(
    release_sk, idempotency_key, attempt_no, parser_version,
    importer_config_sha256, scope, status
)
SELECT
    release_sk, {_sql_literal(idempotency_key)}, attempt_no,
    {_sql_literal(FULL_IMPORTER_VERSION)},
    {_sql_literal(manifest['importer_config_sha256'])},
    'all_24_files', 'loading'
FROM next_attempt
ON CONFLICT (idempotency_key) DO NOTHING;

UPDATE info.import_run
SET status = CASE WHEN status = 'completed' THEN status ELSE 'loading' END,
    error_summary = CASE WHEN status = 'completed' THEN error_summary ELSE NULL END,
    finished_at = CASE WHEN status = 'completed' THEN finished_at ELSE NULL END
WHERE idempotency_key = {_sql_literal(idempotency_key)};
COMMIT;

SELECT release.release_sk, run.import_run_sk, run.status
FROM info.dataset_release AS release
JOIN info.import_run AS run ON run.release_sk = release.release_sk
WHERE release.manifest_sha256 = {_sql_literal(manifest['manifest_sha256'])}
  AND run.idempotency_key = {_sql_literal(idempotency_key)};
"""
    result = database.execute(sql, capture=True).splitlines()
    if not result:
        raise LoadError("注册 S2 import_run 后未返回 release/run 标识")
    release_text, run_text, run_status = result[-1].split("|")
    release_sk = int(release_text)
    import_run_sk = int(run_text)
    rows = database.execute(
        "SELECT name || '|' || source_file_sk || '|' || load_status "
        "FROM info.source_file "
        f"WHERE release_sk = {release_sk} ORDER BY name;\n",
        capture=True,
    ).splitlines()
    source_ids: Dict[str, int] = {}
    for row in rows:
        name, source_id, _status = row.split("|")
        source_ids[name] = int(source_id)
    if set(source_ids) != {spec.name for spec in DATA_FILE_SPECS}:
        raise LoadError("S2 release 的 source_file 集合不是合同规定的 24 个文件")
    return release_sk, import_run_sk, source_ids, run_status == "completed"


def _precheck_full_entry(
    database: DockerPsql,
    manifest: Mapping[str, Any],
) -> None:
    result = database.execute(
        f"""
SELECT
    release.release_sk || '|' ||
    release.status || '|' ||
    count(*) FILTER (
        WHERE source.name IN (
            'as_entity.csv', 'important_as.csv',
            'ip_bgp_entity.csv', 'country.xlsx'
        )
          AND source.load_status = 'loaded'
          AND source.loaded_record_count + source.quarantined_record_count
              = source.logical_record_count
    ) || '|' ||
    (active.release_sk IS NOT NULL)
FROM info.dataset_release AS release
JOIN info.source_file AS source
  ON source.release_sk = release.release_sk
LEFT JOIN info.active_release AS active
  ON active.release_sk = release.release_sk
WHERE release.content_id = {_sql_literal(manifest['content_id'])}
  AND release.manifest_sha256 =
      {_sql_literal(manifest['manifest_sha256'])}
  AND release.parser_version =
      {_sql_literal(manifest['parser_version'])}
  AND release.importer_config_sha256 =
      {_sql_literal(manifest['importer_config_sha256'])}
  AND EXISTS (
      SELECT 1
      FROM info.schema_metadata AS metadata
      WHERE metadata.singleton
        AND metadata.schema_version = 1
        AND metadata.implementation_scope IN (
            'core_four_files', 'all_24_files'
        )
  )
GROUP BY release.release_sk, active.release_sk;
""",
        capture=True,
    )
    fields = result.split("|") if result else []
    if (
        len(fields) != 4
        or fields[1] != "validating"
        or fields[2] != "4"
        or fields[3] != "false"
    ):
        raise LoadError(
            "S2 数据库入口未满足：必须是同一 manifest、"
            "四核心文件守恒、validating 且未激活的 S1 release"
        )


def _backfill_core_source_records(
    database: DockerPsql,
    release_sk: int,
) -> None:
    database.execute(
        f"""
BEGIN;
SELECT info.ensure_release_partitions({release_sk});

INSERT INTO info.source_record(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    dataset_kind, record_kind, natural_key, disposition,
    reason_code, quality_flags, restricted_payload
)
SELECT
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    'autonomous_system', 'autonomous_system', asn::text,
    'accepted', NULL, '[]'::jsonb, NULL
FROM info.autonomous_system
WHERE release_sk = {release_sk}
ON CONFLICT (release_sk, source_file_sk, source_row_no) DO NOTHING;

INSERT INTO info.source_record(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    dataset_kind, record_kind, natural_key, disposition,
    reason_code, quality_flags, restricted_payload
)
SELECT
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    'important_as', 'important_as', asn::text,
    'accepted', NULL, '[]'::jsonb, NULL
FROM info.important_as
WHERE release_sk = {release_sk}
ON CONFLICT (release_sk, source_file_sk, source_row_no) DO NOTHING;

INSERT INTO info.source_record(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    dataset_kind, record_kind, natural_key, disposition,
    reason_code, quality_flags, restricted_payload
)
SELECT
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    'prefix', 'prefix', prefix_raw,
    'accepted', NULL, '[]'::jsonb, NULL
FROM info.prefix
WHERE release_sk = {release_sk}
ON CONFLICT (release_sk, source_file_sk, source_row_no) DO NOTHING;

INSERT INTO info.source_record(
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    dataset_kind, record_kind, natural_key, disposition,
    reason_code, quality_flags, restricted_payload
)
SELECT
    release_sk, source_file_sk, source_row_no, source_record_sha256,
    'country', 'country', coalesce(alpha2, 'source-row:' || source_row_no),
    'accepted', NULL, '[]'::jsonb, NULL
FROM info.country
WHERE release_sk = {release_sk}
ON CONFLICT (release_sk, source_file_sk, source_row_no) DO NOTHING;

DO $block$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM info.source_file AS source
        WHERE source.release_sk = {release_sk}
          AND source.name IN (
              'as_entity.csv', 'important_as.csv',
              'ip_bgp_entity.csv', 'country.xlsx'
          )
          AND (
              SELECT count(*)
              FROM info.source_record AS record
              WHERE record.release_sk = source.release_sk
                AND record.source_file_sk = source.source_file_sk
          ) <> source.logical_record_count
    ) THEN
        RAISE EXCEPTION 'S1 核心记录无法完整回填 source_record 账本';
    END IF;
END
$block$;
COMMIT;
"""
    )


def _collect_full_quality(
    database: DockerPsql,
    manifest: Mapping[str, Any],
    release_sk: int,
) -> Dict[str, Any]:
    lines = database.execute(
        f"""
SELECT
    source.name || '|' ||
    source.load_status || '|' ||
    source.logical_record_count || '|' ||
    source.loaded_record_count || '|' ||
    source.quarantined_record_count || '|' ||
    count(record.source_row_no) || '|' ||
    count(*) FILTER (WHERE record.disposition = 'accepted') || '|' ||
    count(*) FILTER (WHERE record.disposition = 'quarantined') || '|' ||
    count(*) FILTER (
        WHERE record.disposition = 'quarantined'
          AND (record.reason_code IS NULL OR btrim(record.reason_code) = '')
    )
FROM info.source_file AS source
LEFT JOIN info.source_record AS record
  ON record.release_sk = source.release_sk
 AND record.source_file_sk = source.source_file_sk
WHERE source.release_sk = {release_sk}
GROUP BY source.source_file_sk
ORDER BY source.name;
""",
        capture=True,
    ).splitlines()
    files: Dict[str, Dict[str, Any]] = {}
    for line in lines:
        (
            name,
            load_status,
            logical,
            loaded,
            quarantined,
            ledger,
            ledger_accepted,
            ledger_quarantined,
            missing_reason,
        ) = line.split("|")
        files[name] = {
            "load_status": load_status,
            "logical_record_count": int(logical),
            "loaded_record_count": int(loaded),
            "quarantined_record_count": int(quarantined),
            "source_record_count": int(ledger),
            "source_record_accepted_count": int(ledger_accepted),
            "source_record_quarantined_count": int(ledger_quarantined),
            "quarantine_missing_reason_count": int(missing_reason),
        }
    source_file_count = len(files)
    loaded_file_count = sum(
        1
        for item in files.values()
        if (
            item["load_status"] == "loaded"
            and item["loaded_record_count"] + item["quarantined_record_count"]
            == item["logical_record_count"]
        )
    )
    reconciliation_failures = sum(
        1
        for item in files.values()
        if (
            item["load_status"] != "loaded"
            or item["loaded_record_count"] + item["quarantined_record_count"]
            != item["logical_record_count"]
            or item["source_record_count"] != item["logical_record_count"]
            or item["source_record_accepted_count"]
            != item["loaded_record_count"]
            or item["source_record_quarantined_count"]
            != item["quarantined_record_count"]
        )
    )
    missing_reason_count = sum(
        item["quarantine_missing_reason_count"] for item in files.values()
    )
    blocking_quality_flag_count = int(
        database.execute(
            f"""
SELECT count(*)
FROM info.source_record AS record
CROSS JOIN LATERAL jsonb_array_elements(record.quality_flags) AS flag
WHERE record.release_sk = {release_sk}
  AND coalesce((flag->>'blocking')::boolean, false);
""",
            capture=True,
        )
        or "0"
    )
    quarantine_mirror_failure_count = int(
        database.execute(
            f"""
SELECT count(*)
FROM info.source_record AS record
FULL OUTER JOIN info.quarantine AS quarantine
  ON quarantine.release_sk = record.release_sk
 AND quarantine.source_file_sk = record.source_file_sk
 AND quarantine.source_row_no = record.source_row_no
WHERE coalesce(record.release_sk, quarantine.release_sk) = {release_sk}
  AND (
    record.disposition = 'quarantined'
    OR quarantine.quarantine_sk IS NOT NULL
  )
  AND (
    record.source_row_no IS NULL
    OR quarantine.quarantine_sk IS NULL
    OR record.disposition <> 'quarantined'
    OR record.reason_code IS DISTINCT FROM quarantine.reason_code
    OR record.source_record_sha256 IS DISTINCT FROM
       quarantine.raw_record_sha256
  );
""",
            capture=True,
        )
        or "0"
    )
    traceability_tables = (
        "country",
        "country_alias",
        "autonomous_system",
        "as_contact",
        "as_policy_member",
        "as_relation",
        "important_as",
        "prefix",
        "prefix_origin",
        "prefix_domain",
        "mapping_record",
        "domain_record",
        "domain_address",
        "as_prefix_history",
        "important_prefix",
        "important_domain",
        "private_as_location",
        "route_triplet_baseline",
        "dns_observation",
        "as_rank",
        "organization",
        "organization_as",
        "organization_prefix",
        "legacy_record",
    )
    traceability_union = "\nUNION ALL\n".join(
        f"""
SELECT {_sql_literal(table_name)} AS table_name
WHERE EXISTS (
    SELECT 1
    FROM info.{table_name} AS business
    LEFT JOIN info.source_record AS source
      ON source.release_sk = business.release_sk
     AND source.source_file_sk = business.source_file_sk
     AND source.source_row_no = business.source_row_no
     AND source.source_record_sha256 = business.source_record_sha256
     AND source.disposition = 'accepted'
    WHERE business.release_sk = {release_sk}
      AND source.source_row_no IS NULL
)
"""
        for table_name in traceability_tables
    )
    traceability_failure_count = int(
        database.execute(
            f"SELECT count(*) FROM (\n{traceability_union}\n) AS failures;\n",
            capture=True,
        )
        or "0"
    )
    accepted_record_visibility_failure_count = int(
        database.execute(
            f"""
WITH accepted_by_kind AS (
    SELECT record_kind, count(*) AS record_count
    FROM info.source_record
    WHERE release_sk = {release_sk}
      AND disposition = 'accepted'
    GROUP BY record_kind
),
visible_by_kind AS (
    SELECT 'autonomous_system'::text AS record_kind, count(*) AS record_count
    FROM info.autonomous_system WHERE release_sk = {release_sk}
    UNION ALL
    SELECT 'important_as', count(*)
    FROM info.important_as WHERE release_sk = {release_sk}
    UNION ALL
    SELECT 'prefix', count(*)
    FROM info.prefix WHERE release_sk = {release_sk}
    UNION ALL
    SELECT 'country', count(*)
    FROM info.country WHERE release_sk = {release_sk}
    UNION ALL
    SELECT 'domain', count(*)
    FROM info.domain_record WHERE release_sk = {release_sk}
    UNION ALL
    SELECT mapping_kind, count(*)
    FROM info.mapping_record
    WHERE release_sk = {release_sk}
    GROUP BY mapping_kind
    UNION ALL
    SELECT 'important_domain', count(*)
    FROM info.important_domain WHERE release_sk = {release_sk}
    UNION ALL
    SELECT 'route_triplet', count(*)
    FROM info.route_triplet_baseline WHERE release_sk = {release_sk}
    UNION ALL
    SELECT 'important_prefix', count(*)
    FROM info.important_prefix WHERE release_sk = {release_sk}
    UNION ALL
    SELECT 'dns_observation', count(*)
    FROM info.dns_observation WHERE release_sk = {release_sk}
    UNION ALL
    SELECT 'as_rank', count(*)
    FROM info.as_rank WHERE release_sk = {release_sk}
    UNION ALL
    SELECT 'organization', count(*)
    FROM info.organization WHERE release_sk = {release_sk}
    UNION ALL
    SELECT 'legacy', count(*)
    FROM info.legacy_record WHERE release_sk = {release_sk}
)
SELECT coalesce(
    sum(
        abs(
            coalesce(accepted.record_count, 0)
            - coalesce(visible.record_count, 0)
        )
    ),
    0
)
FROM accepted_by_kind AS accepted
FULL OUTER JOIN visible_by_kind AS visible USING (record_kind);
""",
            capture=True,
        )
        or "0"
    )
    source_active_tables = (
        "domain_record",
        "mapping_record",
        "as_prefix_history",
        "as_relation",
        "important_domain",
        "private_as_location",
        "route_triplet_baseline",
        "important_prefix",
        "legacy_record",
    )
    source_active_union = "\nUNION ALL\n".join(
        f"""
SELECT {_sql_literal(table_name)} AS table_name
WHERE EXISTS (
    SELECT 1
    FROM info.{table_name} AS business
    JOIN info.source_file AS source
      ON source.release_sk = business.release_sk
     AND source.source_file_sk = business.source_file_sk
    WHERE business.release_sk = {release_sk}
      AND business.source_active
      AND source.role <> 'active'
)
"""
        for table_name in source_active_tables
    )
    source_role_activation_failure_count = int(
        database.execute(
            f"SELECT count(*) FROM (\n{source_active_union}\n) AS failures;\n",
            capture=True,
        )
        or "0"
    )
    rules = [
        {
            "rule_id": "full.source_file_count",
            "rule_version": 1,
            "blocking": True,
            "status": "pass" if source_file_count == 24 else "fail",
            "observed": source_file_count,
            "expected": 24,
            "evidence": {},
        },
        {
            "rule_id": "full.loaded_file_count",
            "rule_version": 1,
            "blocking": True,
            "status": "pass" if loaded_file_count == 24 else "fail",
            "observed": loaded_file_count,
            "expected": 24,
            "evidence": {},
        },
        {
            "rule_id": "full.per_file_reconciliation",
            "rule_version": 1,
            "blocking": True,
            "status": "pass" if reconciliation_failures == 0 else "fail",
            "observed": reconciliation_failures,
            "expected": 0,
            "evidence": {},
        },
        {
            "rule_id": "full.quarantine_reason_coverage",
            "rule_version": 1,
            "blocking": True,
            "status": "pass" if missing_reason_count == 0 else "fail",
            "observed": missing_reason_count,
            "expected": 0,
            "evidence": {},
        },
        {
            "rule_id": "full.business_record_traceability",
            "rule_version": 1,
            "blocking": True,
            "status": "pass" if traceability_failure_count == 0 else "fail",
            "observed": traceability_failure_count,
            "expected": 0,
            "evidence": {"unit": "business_tables_with_orphan_source_record"},
        },
        {
            "rule_id": "full.accepted_record_visibility",
            "rule_version": 1,
            "blocking": True,
            "status": (
                "pass"
                if accepted_record_visibility_failure_count == 0
                else "fail"
            ),
            "observed": accepted_record_visibility_failure_count,
            "expected": 0,
            "evidence": {"unit": "accepted_records_without_business_parent"},
        },
        {
            "rule_id": "full.unapproved_blocking_quality_flags",
            "rule_version": 1,
            "blocking": True,
            "status": "pass" if blocking_quality_flag_count == 0 else "fail",
            "observed": blocking_quality_flag_count,
            "expected": 0,
            "evidence": {"unit": "record_quality_flags"},
        },
        {
            "rule_id": "full.quarantine_mirror",
            "rule_version": 1,
            "blocking": True,
            "status": (
                "pass" if quarantine_mirror_failure_count == 0 else "fail"
            ),
            "observed": quarantine_mirror_failure_count,
            "expected": 0,
            "evidence": {"unit": "source_record_quarantine_mismatches"},
        },
        {
            "rule_id": "full.source_role_activation_boundary",
            "rule_version": 1,
            "blocking": True,
            "status": (
                "pass"
                if source_role_activation_failure_count == 0
                else "fail"
            ),
            "observed": source_role_activation_failure_count,
            "expected": 0,
            "evidence": {"unit": "tables_with_nonactive_source_marked_active"},
        },
    ]
    blocking_failures = [
        rule["rule_id"]
        for rule in rules
        if rule["blocking"] and rule["status"] != "pass"
    ]
    return {
        "schema_version": 1,
        "component": "static_info_full_quality",
        "quality_gate_version": "info-full-quality-v1",
        "content_id": manifest["content_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "scope": "all_24_files",
        "status": "pass" if not blocking_failures else "fail",
        "blocking_failure_count": len(blocking_failures),
        "blocking_failures": blocking_failures,
        "business_traceability_failure_count": traceability_failure_count,
        "accepted_record_visibility_failure_count": (
            accepted_record_visibility_failure_count
        ),
        "blocking_quality_flag_count": blocking_quality_flag_count,
        "quarantine_mirror_failure_count": quarantine_mirror_failure_count,
        "source_role_activation_failure_count": (
            source_role_activation_failure_count
        ),
        "files": files,
        "rules": rules,
    }


def _finalize_full_run(
    database: DockerPsql,
    manifest: Mapping[str, Any],
    release_sk: int,
    import_run_sk: int,
    quality: Mapping[str, Any],
) -> None:
    rule_values = ",\n".join(
        "("
        + ", ".join(
            [
                _sql_literal(rule["rule_id"]),
                _sql_literal(rule["rule_version"]),
                _sql_literal(rule["blocking"]),
                _sql_literal(rule["status"]),
                _json_literal(rule["observed"]),
                _json_literal(rule["expected"]),
                _json_literal(rule["evidence"]),
            ]
        )
        + ")"
        for rule in quality["rules"]
    )
    run_status = "completed" if quality["status"] == "pass" else "failed"
    database.execute(
        f"""
BEGIN;
INSERT INTO info.quality_result(
    release_sk, import_run_sk, rule_id, rule_version, blocking,
    status, observed, expected, evidence_ref
)
SELECT
    {release_sk}, {import_run_sk}, rule_id, rule_version,
    blocking, status, observed, expected, evidence_ref
FROM (VALUES
{rule_values}
) AS quality(
    rule_id, rule_version, blocking,
    status, observed, expected, evidence_ref
)
ON CONFLICT (release_sk, rule_id, rule_version) DO UPDATE
SET import_run_sk = EXCLUDED.import_run_sk,
    blocking = EXCLUDED.blocking,
    status = EXCLUDED.status,
    observed = EXCLUDED.observed,
    expected = EXCLUDED.expected,
    evidence_ref = EXCLUDED.evidence_ref,
    checked_at = clock_timestamp();

UPDATE info.dataset_release
SET status = 'validating',
    loaded_scope = ARRAY['core_four_files', 'all_24_files'],
    quality_summary = {_json_literal({
        "quality_gate_version": quality["quality_gate_version"],
        "scope": "all_24_files",
        "status": quality["status"],
        "blocking_failure_count": quality["blocking_failure_count"],
    })}
WHERE release_sk = {release_sk}
  AND status <> 'active';

UPDATE info.import_run
SET status = {_sql_literal(run_status)},
    checkpoint = {_json_literal({
        "loaded_files": [spec.name for spec in DATA_FILE_SPECS],
        "next_scope": "query_and_snapshot_shadow",
    })},
    error_summary = CASE
        WHEN {_sql_literal(run_status)} = 'failed'
            THEN 'S2 全量质量门禁未通过'
        ELSE NULL
    END,
    finished_at = clock_timestamp()
WHERE import_run_sk = {import_run_sk};
COMMIT;
"""
    )


def _full_result(
    manifest: Mapping[str, Any],
    release_sk: int,
    import_run_sk: int,
    quality: Mapping[str, Any],
    *,
    status: str,
) -> Dict[str, Any]:
    file_values = quality["files"].values()
    reconciled_file_count = sum(
        1
        for item in file_values
        if (
            item["load_status"] == "loaded"
            and item["loaded_record_count"] + item["quarantined_record_count"]
            == item["logical_record_count"]
            and item["source_record_count"] == item["logical_record_count"]
            and item["source_record_accepted_count"]
            == item["loaded_record_count"]
            and item["source_record_quarantined_count"]
            == item["quarantined_record_count"]
        )
    )
    unreconciled_record_count = sum(
        abs(
            item["logical_record_count"]
            - item["loaded_record_count"]
            - item["quarantined_record_count"]
        )
        + abs(item["logical_record_count"] - item["source_record_count"])
        + abs(
            item["loaded_record_count"]
            - item["source_record_accepted_count"]
        )
        + abs(
            item["quarantined_record_count"]
            - item["source_record_quarantined_count"]
        )
        for item in quality["files"].values()
    )
    traceability_failure_count = int(
        quality.get("business_traceability_failure_count", 0)
    )
    accepted_visibility_failure_count = int(
        quality.get("accepted_record_visibility_failure_count", 0)
    )
    return {
        "schema_version": 1,
        "component": "static_info_full_load",
        "content_id": manifest["content_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "release_sk": release_sk,
        "import_run_sk": import_run_sk,
        "scope": "all_24_files",
        "status": status,
        "database_release_status": "validating",
        "activated": False,
        "source_file_count": len(quality["files"]),
        "reconciled_source_file_count": reconciled_file_count,
        "unreconciled_record_count": unreconciled_record_count,
        "visible_record_traceability_percent": (
            100
            if unreconciled_record_count == 0
            and traceability_failure_count == 0
            and accepted_visibility_failure_count == 0
            else 0
        ),
        "quarantine_reason_coverage_percent": (
            100
            if sum(
                item["quarantine_missing_reason_count"]
                for item in quality["files"].values()
            )
            == 0
            and quality.get("quarantine_mirror_failure_count", 0) == 0
            else 0
        ),
        "next_scope": "query_and_snapshot_shadow",
    }


def load_full_files(
    source_dir: os.PathLike[str] | str,
    manifest: Mapping[str, Any],
    database: DockerPsql,
    *,
    schema_sql: os.PathLike[str] | str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """在同一 S1 release 上装载其余 20 文件并闭合全部 24 文件账本。"""

    validate_manifest(manifest)
    root = Path(source_dir)
    if root.is_symlink() or not root.is_dir():
        raise LoadError(f"S2 来源目录无效：{root}")
    _validate_spool_capacity(manifest)
    _precheck_full_entry(database, manifest)
    database.execute_file(Path(schema_sql))
    release_sk, import_run_sk, source_ids, already_completed = _register_full_run(
        database,
        manifest,
    )
    database.execute(f"SELECT info.ensure_release_partitions({release_sk});\n")
    _backfill_core_source_records(database, release_sk)
    if already_completed:
        quality = _collect_full_quality(database, manifest, release_sk)
        return (
            _full_result(
                manifest,
                release_sk,
                import_run_sk,
                quality,
                status="already_completed",
            ),
            quality,
        )

    file_map = {str(item["name"]): item for item in manifest["files"]}
    try:
        for name in FULL_PHASE_FILE_NAMES:
            if _is_source_loaded(database, release_sk, name):
                continue
            spec = SPEC_BY_NAME[name]
            source_path = root / name
            source_identity = _source_file_identity(source_path)
            stream, logical, accepted, quarantined = _spool_full_file(
                source_path,
                spec,
            )
            try:
                if _source_file_identity(source_path) != source_identity:
                    raise LoadError(f"{name} 在 S2 解析期间发生变化")
                _verify_source_file(source_path, file_map[name])
                expected = int(file_map[name]["logical_record_count"])
                if logical != expected:
                    raise LoadError(
                        f"{name} S2 解析记录数与 manifest 不一致："
                        f"observed={logical} expected={expected}"
                    )
                database.copy_stage(
                    _FULL_COPY_HEAD,
                    stream,
                    _full_tail(
                        release_sk,
                        source_ids[name],
                        spec,
                        logical,
                        accepted,
                        quarantined,
                    ),
                )
            finally:
                stream.close()
        quality = _collect_full_quality(database, manifest, release_sk)
        _finalize_full_run(
            database,
            manifest,
            release_sk,
            import_run_sk,
            quality,
        )
    except BaseException as exc:
        try:
            database.execute(
                "UPDATE info.import_run "
                "SET status = 'failed', finished_at = clock_timestamp(), "
                f"error_summary = {_sql_literal(str(exc)[:2000])} "
                f"WHERE import_run_sk = {import_run_sk};\n"
            )
        except BaseException:
            pass
        raise
    return (
        _full_result(
            manifest,
            release_sk,
            import_run_sk,
            quality,
            status="completed" if quality["status"] == "pass" else "quality_failed",
        ),
        quality,
    )
