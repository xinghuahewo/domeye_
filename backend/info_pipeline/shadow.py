"""S3 文件后端与候选数据库后端的确定性语义对账。"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, MutableMapping, Optional

from .catalog import (
    CSV_FIELD_SIZE_LIMIT_BYTES,
    DATA_FILE_SPECS,
    SPEC_BY_NAME,
)
from .full_loader import (
    RecordQuarantine,
    _iter_source,
    _safe_json,
    _transform,
)
from .loader import (
    DockerPsql,
    LoadError,
    _as_entity_payload,
    _country_payload,
    _prefix_payload,
    _verify_source_file,
)
from .manifest import validate_manifest
from .quality import parse_asn


_MASK_256 = (1 << 256) - 1
_SENSITIVE_AS_FIELDS = frozenset(
    {"admin_info", "tech_info", "abuse_info"}
)
_EXPECTED_SECTIONS = frozenset(
    {
        "asn_exact_and_snapshot",
        "asn_contact_redacted",
        "asn_policy_snapshot",
        "asn_embedded_relation_snapshot",
        "important_as_exact_and_snapshot",
        "prefix_exact_lpm_and_snapshot",
        "prefix_domain_snapshot",
        "country_alias_and_snapshot",
        "domain_exact_and_snapshot",
        "pfx2as_exact_and_snapshot",
        "as_relation_exact_and_snapshot",
        "private_as_exact_and_snapshot",
        "important_domain_snapshot",
        "triplet_exact_and_snapshot",
        "important_prefix_snapshot",
    }
)


def _canonical_bytes(value: Any) -> bytes:
    def normalize(item: Any) -> Any:
        safe = _safe_json(item)
        if isinstance(safe, float) and safe.is_integer():
            return int(safe)
        if isinstance(safe, Mapping):
            return {
                str(key): normalize(nested)
                for key, nested in safe.items()
            }
        if isinstance(safe, list):
            return [normalize(nested) for nested in safe]
        return safe

    return json.dumps(
        normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _value_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


class SemanticDigest:
    """可流式、与遍历顺序无关的确定性多重集合摘要。"""

    def __init__(self) -> None:
        self.count = 0
        self._sum = 0
        self._xor = 0
        self._sum_square = 0

    def add(self, value: Any) -> None:
        numeric = int.from_bytes(
            hashlib.sha256(_canonical_bytes(value)).digest(),
            "big",
        )
        self.count += 1
        self._sum = (self._sum + numeric) & _MASK_256
        self._xor ^= numeric
        self._sum_square = (
            self._sum_square + numeric * numeric
        ) & _MASK_256

    def signature(self) -> str:
        payload = {
            "algorithm": "sha256-multiset-sum-xor-square-v1",
            "count": self.count,
            "sum": f"{self._sum:064x}",
            "xor": f"{self._xor:064x}",
            "sum_square": f"{self._sum_square:064x}",
        }
        return _value_sha256(payload)


@dataclass
class SectionDigests:
    file: SemanticDigest
    database: SemanticDigest


class ShadowAccumulator:
    def __init__(self) -> None:
        self._sections: Dict[str, SectionDigests] = {}

    def _section(self, name: str) -> SectionDigests:
        if name not in self._sections:
            self._sections[name] = SectionDigests(
                file=SemanticDigest(),
                database=SemanticDigest(),
            )
        return self._sections[name]

    def add_file(self, name: str, value: Any) -> None:
        self._section(name).file.add(value)

    def add_database(self, name: str, value: Any) -> None:
        self._section(name).database.add(value)

    def results(self) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        for name in sorted(self._sections):
            section = self._sections[name]
            file_signature = section.file.signature()
            database_signature = section.database.signature()
            status = (
                "pass"
                if section.file.count == section.database.count
                and file_signature == database_signature
                else "fail"
            )
            results[name] = {
                "status": status,
                "comparison_unit": "canonical_semantic_record",
                "file_record_count": section.file.count,
                "database_record_count": section.database.count,
                "file_set_sha256": file_signature,
                "database_set_sha256": database_signature,
                "unapproved_difference_count": 0 if status == "pass" else 1,
            }
        return results


def _file_map(manifest: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(item["name"]): item
        for item in manifest["files"]
        if isinstance(item, Mapping)
    }


def _verify_all_sources(
    source_dir: Path,
    manifest: Mapping[str, Any],
) -> None:
    files = _file_map(manifest)
    expected = {spec.name for spec in DATA_FILE_SPECS}
    if set(files) != expected:
        raise LoadError("S3 manifest 不是合同规定的 24 文件精确集合")
    for name in sorted(expected):
        _verify_source_file(source_dir / name, files[name])


def _read_csv(path: Path) -> Iterator[tuple[int, Dict[str, str]]]:
    spec = SPEC_BY_NAME[path.name]
    if spec.encoding is None:
        raise LoadError(f"{path.name} 缺少文本编码合同")
    with path.open("r", encoding=spec.encoding, newline="") as stream:
        csv.field_size_limit(CSV_FIELD_SIZE_LIMIT_BYTES)
        reader = csv.DictReader(
            stream,
            delimiter=spec.delimiter or ",",
            strict=True,
        )
        if reader.fieldnames is None:
            raise LoadError(f"{path.name} 缺少 CSV 表头")
        for source_row_no, row in enumerate(reader, start=1):
            yield source_row_no, dict(row)


def _as_business_value(
    source_row_no: int,
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "source_row_no": source_row_no,
        "asn": str(payload["asn"]),
        "as_name": payload["as_name"],
        "country_code": payload["country_code"],
        "country_name_cn": payload["country_name_cn"],
        "org_name": payload["org_name"],
        "org_name_cn": payload["org_name_cn"],
        "as_type": payload["as_type"],
        "description": payload["description"],
        "description_cn": payload["description_cn"],
        "is_ddos_provider": payload["is_ddos_provider"],
        "global_rank": payload["global_rank"],
        "country_rank": payload["country_rank"],
        "v4_prefix_count": payload["v4_prefix_count"],
        "v6_prefix_count": payload["v6_prefix_count"],
        "v4_peer_count": payload["v4_peer_count"],
        "v6_peer_count": payload["v6_peer_count"],
        "v4_upstream_count": payload["v4_upstream_count"],
        "v6_upstream_count": payload["v6_upstream_count"],
        "v4_downstream_count": payload["v4_downstream_count"],
        "v6_downstream_count": payload["v6_downstream_count"],
        "attributes": payload["attributes"],
    }


def _collect_as_file(source_dir: Path, acc: ShadowAccumulator) -> None:
    for source_row_no, row in _read_csv(source_dir / "as_entity.csv"):
        payload = _as_entity_payload(row)
        acc.add_file(
            "asn_exact_and_snapshot",
            _as_business_value(source_row_no, payload),
        )
        for contact in payload["contacts"]:
            acc.add_file(
                "asn_contact_redacted",
                {
                    "source_row_no": source_row_no,
                    "asn": str(payload["asn"]),
                    "contact_kind": contact["contact_kind"],
                    "ordinal": contact["ordinal"],
                    "value_sha256": _value_sha256(
                        contact["contact_value"]
                    ),
                },
            )
        for policy in payload["policies"]:
            acc.add_file(
                "asn_policy_snapshot",
                {
                    "source_row_no": source_row_no,
                    "asn": str(payload["asn"]),
                    **policy,
                },
            )
        for relation in payload["relations"]:
            acc.add_file(
                "asn_embedded_relation_snapshot",
                {
                    "source_row_no": source_row_no,
                    "source_asn": str(payload["asn"]),
                    **relation,
                },
            )


def _collect_important_as_file(
    source_dir: Path,
    acc: ShadowAccumulator,
) -> None:
    for source_row_no, row in _read_csv(
        source_dir / "important_as.csv"
    ):
        asn = parse_asn(row.get("aut-num"))
        label = next(
            (
                str(value)
                for key, value in row.items()
                if key != "aut-num"
                and value is not None
                and str(value).strip()
            ),
            None,
        )
        acc.add_file(
            "important_as_exact_and_snapshot",
            {
                "source_row_no": source_row_no,
                "asn": str(asn),
                "label": label,
                "attributes": row,
            },
        )


def _collect_prefix_file(
    source_dir: Path,
    acc: ShadowAccumulator,
) -> None:
    for source_row_no, row in _read_csv(
        source_dir / "ip_bgp_entity.csv"
    ):
        payload = _prefix_payload(row)
        domains = payload.pop("domains")
        acc.add_file(
            "prefix_exact_lpm_and_snapshot",
            {
                "source_row_no": source_row_no,
                **payload,
            },
        )
        for domain in domains:
            acc.add_file(
                "prefix_domain_snapshot",
                {
                    "source_row_no": source_row_no,
                    "prefix_raw": payload["prefix_raw"],
                    **domain,
                },
            )


def _collect_country_file(
    source_dir: Path,
    acc: ShadowAccumulator,
) -> None:
    spec = SPEC_BY_NAME["country.xlsx"]
    for source_row_no, row in _iter_source(
        source_dir / spec.name,
        spec,
    ):
        header = list(row)
        payload = _country_payload(header, [row[key] for key in header])
        acc.add_file(
            "country_alias_and_snapshot",
            {
                "source_row_no": source_row_no,
                **payload,
            },
        )


def _accepted_record(
    name: str,
    raw: Mapping[str, Any],
) -> Optional[Mapping[str, Any]]:
    try:
        return _transform(name, _safe_json(raw)).payload
    except RecordQuarantine:
        return None


def _collect_domain_file(
    source_dir: Path,
    acc: ShadowAccumulator,
) -> None:
    seen: set[str] = set()
    for name in ("website_entity.csv", "domain_cn.csv"):
        spec = SPEC_BY_NAME[name]
        for source_row_no, raw in _iter_source(source_dir / name, spec):
            payload = _accepted_record(name, raw)
            if payload is None:
                continue
            key = str(payload["domain_key_raw"])
            if key in seen:
                continue
            seen.add(key)
            acc.add_file(
                "domain_exact_and_snapshot",
                {
                    "source_name": name,
                    "source_row_no": source_row_no,
                    "domain_key_raw": key,
                    "normalized_key": payload["normalized_key"],
                    "title": payload["title"],
                    "industry": payload["industry"],
                    "ip_raw": payload["ip_raw"],
                    "ip_prefix_raw": payload["ip_prefix_raw"],
                    "authoritative_ip_raw": payload[
                        "authoritative_ip_raw"
                    ],
                    "source_priority": payload["source_priority"],
                },
            )


def _collect_json_mapping_file(
    source_dir: Path,
    name: str,
    section: str,
    acc: ShadowAccumulator,
) -> None:
    spec = SPEC_BY_NAME[name]
    for source_row_no, raw in _iter_source(source_dir / name, spec):
        payload = _accepted_record(name, raw)
        if payload is None:
            continue
        if name == "pfx2as_dict.txt":
            value = {
                "source_row_no": source_row_no,
                "key": str(raw["key"]),
                "items": [
                    {
                        "ordinal": item["ordinal"],
                        "prefix_raw": item["prefix_raw"],
                        "prefix_cidr": item["prefix_cidr"],
                        "source_value": item["source_value"],
                        "quality_status": item["quality_status"],
                    }
                    for item in payload["prefixes"]
                ],
            }
        elif name == "as_rel_dict.txt":
            fields: MutableMapping[str, list[str]] = {
                "provider": [],
                "customer": [],
                "peers": [],
                "peer": [],
                "sibling": [],
            }
            for item in payload["relations"]:
                fields[str(item["source_field"])].append(
                    str(item["target_asn"])
                )
            value = {
                "source_row_no": source_row_no,
                "key": str(raw["key"]),
                "fields": fields,
            }
        elif name == "private_as_dict_new.json":
            value = {
                "source_row_no": source_row_no,
                "key": str(raw["key"]),
                "items": [
                    {
                        "ordinal": item["ordinal"],
                        "public_asn_raw": item["public_asn_raw"],
                        "public_asn": (
                            None
                            if item["public_asn"] is None
                            else str(item["public_asn"])
                        ),
                        "private_asn_raw": item["private_asn_raw"],
                        "private_asn": (
                            None
                            if item["private_asn"] is None
                            else str(item["private_asn"])
                        ),
                        "ip_num": item["ip_num"],
                        "city": item["city"],
                        "quality_status": item["quality_status"],
                    }
                    for item in payload["locations"]
                ],
            }
        elif name == "domain_cn_center.txt":
            value = {
                "source_row_no": source_row_no,
                "key": str(raw["key"]),
                "source_value": payload["source_value"],
            }
        else:
            raise AssertionError(name)
        acc.add_file(section, value)


def _collect_triplet_file(
    source_dir: Path,
    acc: ShadowAccumulator,
) -> None:
    spec = SPEC_BY_NAME["triplet_20days.csv"]
    winners: Dict[str, Dict[str, Any]] = {}
    for source_row_no, raw in _iter_source(
        source_dir / spec.name,
        spec,
    ):
        payload = _accepted_record(spec.name, raw)
        if payload is None:
            continue
        key = "|".join(
            str(payload[field])
            for field in ("first_as", "second_as", "third_as")
        )
        winners[key] = {
            "key": key,
            "first_as": str(payload["first_as"]),
            "second_as": str(payload["second_as"]),
            "third_as": str(payload["third_as"]),
            "stability": payload["stability"],
        }
    for value in winners.values():
        acc.add_file("triplet_exact_and_snapshot", value)


def _collect_important_prefix_file(
    source_dir: Path,
    acc: ShadowAccumulator,
) -> None:
    seen: set[str] = set()
    for name in ("ipv4_all_prefix.xls", "ipv6_all_prefix.xls"):
        spec = SPEC_BY_NAME[name]
        for source_row_no, raw in _iter_source(source_dir / name, spec):
            payload = _accepted_record(name, raw)
            if payload is None:
                continue
            key = str(payload["prefix_raw"])
            if key in seen:
                continue
            seen.add(key)
            acc.add_file(
                "important_prefix_snapshot",
                {
                    "source_name": name,
                    "source_row_no": source_row_no,
                    "prefix_raw": key,
                    "prefix_cidr": payload["prefix_cidr"],
                    "afi": payload["afi"],
                    "number_raw": payload["number_raw"],
                    "host_raw": payload["host_raw"],
                },
            )


def _collect_file_backend(
    source_dir: Path,
    acc: ShadowAccumulator,
) -> None:
    _collect_as_file(source_dir, acc)
    _collect_important_as_file(source_dir, acc)
    _collect_prefix_file(source_dir, acc)
    _collect_country_file(source_dir, acc)
    _collect_domain_file(source_dir, acc)
    _collect_json_mapping_file(
        source_dir,
        "pfx2as_dict.txt",
        "pfx2as_exact_and_snapshot",
        acc,
    )
    _collect_json_mapping_file(
        source_dir,
        "as_rel_dict.txt",
        "as_relation_exact_and_snapshot",
        acc,
    )
    _collect_json_mapping_file(
        source_dir,
        "private_as_dict_new.json",
        "private_as_exact_and_snapshot",
        acc,
    )
    _collect_json_mapping_file(
        source_dir,
        "domain_cn_center.txt",
        "important_domain_snapshot",
        acc,
    )
    _collect_triplet_file(source_dir, acc)
    _collect_important_prefix_file(source_dir, acc)


def _single_json_column(
    database: DockerPsql,
    query: str,
) -> Iterator[Mapping[str, Any]]:
    with database.csv_rows(query) as rows:
        for row in rows:
            if len(row) != 1:
                raise LoadError("S3 候选库 JSON 流列数不是 1")
            value = json.loads(row[0])
            if not isinstance(value, dict):
                raise LoadError("S3 候选库 JSON 流记录不是对象")
            yield value


def _digest_database_json(
    database: DockerPsql,
    acc: ShadowAccumulator,
    section: str,
    query: str,
) -> None:
    for value in _single_json_column(database, query):
        acc.add_database(section, value)


def _collect_as_database(
    database: DockerPsql,
    release_sk: int,
    acc: ShadowAccumulator,
) -> None:
    _digest_database_json(
        database,
        acc,
        "asn_exact_and_snapshot",
        f"""
SELECT jsonb_build_object(
    'source_row_no', source_row_no,
    'asn', asn::text,
    'as_name', as_name,
    'country_code', country_code,
    'country_name_cn', country_name_cn,
    'org_name', org_name,
    'org_name_cn', org_name_cn,
    'as_type', as_type,
    'description', description,
    'description_cn', description_cn,
    'is_ddos_provider', is_ddos_provider,
    'global_rank', global_rank,
    'country_rank', country_rank,
    'v4_prefix_count', v4_prefix_count,
    'v6_prefix_count', v6_prefix_count,
    'v4_peer_count', v4_peer_count,
    'v6_peer_count', v6_peer_count,
    'v4_upstream_count', v4_upstream_count,
    'v6_upstream_count', v6_upstream_count,
    'v4_downstream_count', v4_downstream_count,
    'v6_downstream_count', v6_downstream_count,
    'attributes', attributes
)
FROM info.autonomous_system
WHERE release_sk = {release_sk}
ORDER BY source_row_no
""",
    )
    with database.csv_rows(
        f"""
SELECT contact.source_row_no::text, contact.asn::text,
       contact.contact_kind, contact.ordinal::text,
       contact.contact_value #>> '{{}}'
FROM info.as_contact AS contact
WHERE contact.release_sk = {release_sk}
ORDER BY contact.source_row_no, contact.contact_kind, contact.ordinal
"""
    ) as rows:
        for row in rows:
            if len(row) != 5:
                raise LoadError("S3 联系人摘要流列数异常")
            acc.add_database(
                "asn_contact_redacted",
                {
                    "source_row_no": int(row[0]),
                    "asn": row[1],
                    "contact_kind": row[2],
                    "ordinal": int(row[3]),
                    "value_sha256": _value_sha256(row[4]),
                },
            )
    _digest_database_json(
        database,
        acc,
        "asn_policy_snapshot",
        f"""
SELECT jsonb_build_object(
    'source_row_no', source_row_no,
    'asn', asn::text,
    'direction', direction,
    'ordinal', ordinal,
    'token', token,
    'parsed_asn', parsed_asn
)
FROM info.as_policy_member
WHERE release_sk = {release_sk}
ORDER BY source_row_no, direction, ordinal
""",
    )
    _digest_database_json(
        database,
        acc,
        "asn_embedded_relation_snapshot",
        f"""
SELECT jsonb_build_object(
    'source_row_no', relation.source_row_no,
    'source_asn', relation.source_asn::text,
    'target_asn', relation.target_asn,
    'relation_kind', relation.relation_kind,
    'afi', relation.afi,
    'ordinal', relation.ordinal,
    'source_field', relation.source_field
)
FROM info.as_relation AS relation
JOIN info.source_file AS source
  ON source.source_file_sk = relation.source_file_sk
WHERE relation.release_sk = {release_sk}
  AND source.name = 'as_entity.csv'
ORDER BY relation.source_row_no, relation.source_field, relation.ordinal
""",
    )


def _collect_simple_database(
    database: DockerPsql,
    release_sk: int,
    acc: ShadowAccumulator,
) -> None:
    _digest_database_json(
        database,
        acc,
        "important_as_exact_and_snapshot",
        f"""
SELECT jsonb_build_object(
    'source_row_no', source_row_no,
    'asn', asn::text,
    'label', label,
    'attributes', attributes
)
FROM info.important_as
WHERE release_sk = {release_sk}
ORDER BY source_row_no
""",
    )
    _digest_database_json(
        database,
        acc,
        "prefix_exact_lpm_and_snapshot",
        f"""
SELECT jsonb_build_object(
    'source_row_no', source_row_no,
    'prefix_raw', prefix_raw,
    'prefix_cidr', prefix_cidr::text,
    'canonical_status', canonical_status,
    'name', name,
    'description', description,
    'route_raw', route_raw,
    'bgp_raw', bgp_raw,
    'country_code', country_code,
    'source_name', source_name,
    'declared_domain_count', declared_domain_count,
    'declared_authoritative_domain_count',
        declared_authoritative_domain_count,
    'attributes', attributes
)
FROM info.prefix
WHERE release_sk = {release_sk}
ORDER BY source_row_no
""",
    )
    _digest_database_json(
        database,
        acc,
        "prefix_domain_snapshot",
        f"""
SELECT jsonb_build_object(
    'source_row_no', source_row_no,
    'prefix_raw', prefix_raw,
    'domain_key_raw', domain_key_raw,
    'domain_role', domain_role,
    'ordinal', ordinal
)
FROM info.prefix_domain
WHERE release_sk = {release_sk}
ORDER BY source_row_no,
         CASE domain_role WHEN 'normal' THEN 0 ELSE 1 END,
         ordinal
""",
    )
    _digest_database_json(
        database,
        acc,
        "country_alias_and_snapshot",
        f"""
SELECT jsonb_build_object(
    'source_row_no', source_row_no,
    'english_full_name', english_full_name,
    'english_short_name', english_short_name,
    'chinese_short_name', chinese_short_name,
    'alpha2', alpha2,
    'alpha3', alpha3,
    'digital_code', digital_code,
    'phone_code', phone_code,
    'jet_lag', jet_lag,
    'latitude', latitude,
    'longitude', longitude,
    'quality_status', quality_status,
    'attributes', attributes
)
FROM info.country
WHERE release_sk = {release_sk}
ORDER BY source_row_no
""",
    )
    _digest_database_json(
        database,
        acc,
        "domain_exact_and_snapshot",
        f"""
SELECT jsonb_build_object(
    'source_name', source.name,
    'source_row_no', domain.source_row_no,
    'domain_key_raw', domain.domain_key_raw,
    'normalized_key', domain.normalized_key,
    'title', domain.title,
    'industry', domain.industry,
    'ip_raw', domain.ip_raw,
    'ip_prefix_raw', domain.ip_prefix_raw,
    'authoritative_ip_raw', domain.authoritative_ip_raw,
    'source_priority', domain.source_priority
)
FROM info.domain_record AS domain
JOIN info.source_file AS source
  ON source.source_file_sk = domain.source_file_sk
WHERE domain.release_sk = {release_sk}
  AND domain.source_active
ORDER BY domain.source_priority, domain.source_row_no
""",
    )
    _digest_database_json(
        database,
        acc,
        "important_domain_snapshot",
        f"""
SELECT jsonb_build_object(
    'source_row_no', source_row_no,
    'key', domain_key_raw,
    'source_value', source_value
)
FROM info.important_domain
WHERE release_sk = {release_sk}
  AND source_active
ORDER BY source_row_no
""",
    )
    _digest_database_json(
        database,
        acc,
        "triplet_exact_and_snapshot",
        f"""
SELECT jsonb_build_object(
    'key', first_as::text || '|' || second_as::text || '|' || third_as::text,
    'first_as', first_as::text,
    'second_as', second_as::text,
    'third_as', third_as::text,
    'stability', stability
)
FROM (
    SELECT DISTINCT ON (first_as, second_as, third_as)
           first_as, second_as, third_as, stability
    FROM info.route_triplet_baseline
    WHERE release_sk = {release_sk}
      AND source_active
    ORDER BY first_as, second_as, third_as, source_row_no DESC
) AS winner
ORDER BY first_as, second_as, third_as
""",
    )
    _digest_database_json(
        database,
        acc,
        "important_prefix_snapshot",
        f"""
SELECT jsonb_build_object(
    'source_name', source_name,
    'source_row_no', source_row_no,
    'prefix_raw', prefix_raw,
    'prefix_cidr', prefix_cidr::text,
    'afi', afi,
    'number_raw', number_raw,
    'host_raw', host_raw
)
FROM (
    SELECT DISTINCT ON (prefix.prefix_raw)
           source.name AS source_name,
           prefix.source_row_no,
           prefix.prefix_raw,
           prefix.prefix_cidr,
           prefix.afi,
           prefix.number_raw,
           prefix.host_raw,
           CASE source.name
               WHEN 'ipv4_all_prefix.xls' THEN 0
               ELSE 1
           END AS source_order
    FROM info.important_prefix AS prefix
    JOIN info.source_file AS source
      ON source.source_file_sk = prefix.source_file_sk
    WHERE prefix.release_sk = {release_sk}
    ORDER BY prefix.prefix_raw, source_order, prefix.source_row_no
) AS winner
ORDER BY source_order, source_row_no
""",
    )


def _collect_grouped_database(
    database: DockerPsql,
    release_sk: int,
    acc: ShadowAccumulator,
) -> None:
    _collect_grouped_rows(
        database,
        acc,
        "pfx2as_exact_and_snapshot",
        f"""
SELECT mapping.source_row_no::text, mapping.natural_key,
       history.ordinal::text, history.prefix_raw,
       history.prefix_cidr::text, history.source_value::text,
       history.quality_status
FROM info.mapping_record AS mapping
JOIN info.source_file AS source
  ON source.source_file_sk = mapping.source_file_sk
LEFT JOIN info.as_prefix_history AS history
  ON history.release_sk = mapping.release_sk
 AND history.source_file_sk = mapping.source_file_sk
 AND history.source_row_no = mapping.source_row_no
WHERE mapping.release_sk = {release_sk}
  AND source.name = 'pfx2as_dict.txt'
  AND mapping.source_active
ORDER BY mapping.source_row_no, history.ordinal
""",
        kind="pfx2as",
    )
    _collect_grouped_rows(
        database,
        acc,
        "as_relation_exact_and_snapshot",
        f"""
SELECT mapping.source_row_no::text, mapping.natural_key,
       relation.ordinal::text, relation.source_field,
       relation.target_asn::text
FROM info.mapping_record AS mapping
JOIN info.source_file AS source
  ON source.source_file_sk = mapping.source_file_sk
LEFT JOIN info.as_relation AS relation
  ON relation.release_sk = mapping.release_sk
 AND relation.source_file_sk = mapping.source_file_sk
 AND relation.source_row_no = mapping.source_row_no
WHERE mapping.release_sk = {release_sk}
  AND source.name = 'as_rel_dict.txt'
  AND mapping.source_active
ORDER BY mapping.source_row_no, relation.ordinal
""",
        kind="relation",
    )
    _collect_grouped_rows(
        database,
        acc,
        "private_as_exact_and_snapshot",
        f"""
SELECT mapping.source_row_no::text, mapping.natural_key,
       location.ordinal::text, location.public_asn_raw,
       location.public_asn::text, location.private_asn_raw,
       location.private_asn::text, location.ip_num::text,
       location.city, location.quality_status
FROM info.mapping_record AS mapping
JOIN info.source_file AS source
  ON source.source_file_sk = mapping.source_file_sk
LEFT JOIN info.private_as_location AS location
  ON location.release_sk = mapping.release_sk
 AND location.source_file_sk = mapping.source_file_sk
 AND location.source_row_no = mapping.source_row_no
WHERE mapping.release_sk = {release_sk}
  AND source.name = 'private_as_dict_new.json'
  AND mapping.source_active
ORDER BY mapping.source_row_no, location.ordinal
""",
        kind="private",
    )


def _collect_grouped_rows(
    database: DockerPsql,
    acc: ShadowAccumulator,
    section: str,
    query: str,
    *,
    kind: str,
) -> None:
    current_row: Optional[int] = None
    current_key = ""
    items: list[Any] = []

    def flush() -> None:
        if current_row is None:
            return
        if kind == "relation":
            fields: MutableMapping[str, list[str]] = {
                "provider": [],
                "customer": [],
                "peers": [],
                "peer": [],
                "sibling": [],
            }
            for source_field, target_asn in items:
                fields[source_field].append(target_asn)
            value: Dict[str, Any] = {
                "source_row_no": current_row,
                "key": current_key,
                "fields": fields,
            }
        else:
            value = {
                "source_row_no": current_row,
                "key": current_key,
                "items": list(items),
            }
        acc.add_database(section, value)

    with database.csv_rows(query) as rows:
        for row in rows:
            source_row_no = int(row[0])
            if current_row is not None and source_row_no != current_row:
                flush()
                items = []
            current_row = source_row_no
            current_key = row[1]
            ordinal = row[2]
            if not ordinal:
                continue
            if kind == "pfx2as":
                items.append(
                    {
                        "ordinal": int(ordinal),
                        "prefix_raw": row[3],
                        "prefix_cidr": row[4] or None,
                        "source_value": (
                            None if not row[5] else json.loads(row[5])
                        ),
                        "quality_status": row[6],
                    }
                )
            elif kind == "relation":
                items.append((row[3], row[4]))
            elif kind == "private":
                items.append(
                    {
                        "ordinal": int(ordinal),
                        "public_asn_raw": row[3],
                        "public_asn": row[4] or None,
                        "private_asn_raw": row[5],
                        "private_asn": row[6] or None,
                        "ip_num": (
                            None if not row[7] else json.loads(row[7])
                        ),
                        "city": row[8] or None,
                        "quality_status": row[9],
                    }
                )
            else:
                raise AssertionError(kind)
    flush()


def _database_entry(
    database: DockerPsql,
    manifest: Mapping[str, Any],
) -> tuple[int, str]:
    content = str(manifest["content_id"]).replace("'", "''")
    manifest_sha = str(manifest["manifest_sha256"]).replace("'", "''")
    state = database.execute(
        f"""
SELECT release.release_sk || '|' || release.status || '|' ||
       count(DISTINCT source.source_file_sk) || '|' ||
       count(DISTINCT source.source_file_sk) FILTER (
           WHERE source.load_status = 'loaded'
             AND source.loaded_record_count
                 + source.quarantined_record_count
                 = source.logical_record_count
       ) || '|' ||
       count(DISTINCT active.profile_name) || '|' ||
       count(DISTINCT quality.quality_result_sk) FILTER (
           WHERE quality.blocking AND quality.status <> 'pass'
       ) || '|' ||
       count(DISTINCT run.import_run_sk) FILTER (
           WHERE run.scope = 'all_24_files'
             AND run.status = 'completed'
       )
FROM info.dataset_release AS release
JOIN info.source_file AS source
  ON source.release_sk = release.release_sk
LEFT JOIN info.active_release AS active
  ON active.release_sk = release.release_sk
LEFT JOIN info.quality_result AS quality
  ON quality.release_sk = release.release_sk
LEFT JOIN info.import_run AS run
  ON run.release_sk = release.release_sk
WHERE release.content_id = '{content}'
  AND release.manifest_sha256 = '{manifest_sha}'
GROUP BY release.release_sk, release.status;
""",
        capture=True,
    )
    fields = state.split("|") if state else []
    if (
        len(fields) != 7
        or fields[1] != "validating"
        or fields[2] != "24"
        or fields[3] != "24"
        or fields[4] != "0"
        or fields[5] != "0"
        or fields[6] != "1"
    ):
        raise LoadError(
            "S3 数据库入口未满足：必须是同一 content_id、24/24 "
            "已闭合、S2 completed、无阻断质量失败且未激活的 validating release"
        )
    return int(fields[0]), fields[1]


def _approved_exceptions(
    database: DockerPsql,
    release_sk: int,
) -> list[Dict[str, Any]]:
    exceptions: list[Dict[str, Any]] = []
    with database.csv_rows(
        f"""
SELECT source.name, quarantine.source_row_no::text,
       coalesce(quarantine.natural_key, ''),
       quarantine.reason_code,
       quarantine.raw_record_sha256
FROM info.quarantine AS quarantine
JOIN info.source_file AS source
  ON source.source_file_sk = quarantine.source_file_sk
WHERE quarantine.release_sk = {release_sk}
ORDER BY source.name, quarantine.source_row_no
"""
    ) as rows:
        for row in rows:
            if len(row) != 5:
                raise LoadError("S3 隔离例外索引列数异常")
            exceptions.append(
                {
                    "source_file": row[0],
                    "source_row_no": int(row[1]),
                    "natural_key": row[2],
                    "field": "natural_key_or_record",
                    "reason_code": row[3],
                    "source_record_sha256": row[4],
                    "approval_basis": "S2_quarantine_contract",
                }
            )
    return exceptions


_QUERY_SECTIONS = frozenset(
    {
        "asn_exact_and_snapshot",
        "important_as_exact_and_snapshot",
        "prefix_exact_lpm_and_snapshot",
        "country_alias_and_snapshot",
        "domain_exact_and_snapshot",
        "pfx2as_exact_and_snapshot",
        "as_relation_exact_and_snapshot",
        "private_as_exact_and_snapshot",
        "triplet_exact_and_snapshot",
    }
)


def compare_shadow_backends(
    source_dir: Path | str,
    manifest: Mapping[str, Any],
    database: DockerPsql,
) -> Dict[str, Any]:
    """执行 S3 全量文件/数据库语义对账，并返回不含联系人原文的报告。"""

    validate_manifest(manifest)
    root = Path(source_dir)
    if root.is_symlink() or not root.is_dir():
        raise LoadError(f"S3 来源目录无效或为软链接：{root}")
    _verify_all_sources(root, manifest)
    release_sk, release_status = _database_entry(database, manifest)

    accumulator = ShadowAccumulator()
    for section_name in _EXPECTED_SECTIONS:
        accumulator._section(section_name)
    _collect_file_backend(root, accumulator)
    _collect_as_database(database, release_sk, accumulator)
    _collect_simple_database(database, release_sk, accumulator)
    _collect_grouped_database(database, release_sk, accumulator)
    sections = accumulator.results()

    if set(sections) != _EXPECTED_SECTIONS:
        missing = sorted(_EXPECTED_SECTIONS - set(sections))
        extra = sorted(set(sections) - _EXPECTED_SECTIONS)
        raise LoadError(
            f"S3 语义集合不完整：missing={missing} extra={extra}"
        )

    full_set_differences = sum(
        int(item["unapproved_difference_count"])
        for item in sections.values()
    )
    query_differences = sum(
        int(sections[name]["unapproved_difference_count"])
        for name in _QUERY_SECTIONS
    )
    snapshot_differences = full_set_differences
    deterministic_query_case_count = sum(
        int(sections[name]["file_record_count"])
        for name in _QUERY_SECTIONS
    )
    exceptions = _approved_exceptions(database, release_sk)
    report = {
        "schema_version": 1,
        "component": "static_info_shadow_diff",
        "shadow_contract_version": "info-shadow-diff-v1",
        "content_id": manifest["content_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "release_sk": release_sk,
        "database_release_status": release_status,
        "scope": "all_static_queries_and_snapshot",
        "status": (
            "pass"
            if query_differences == 0
            and full_set_differences == 0
            and snapshot_differences == 0
            else "fail"
        ),
        "activated": False,
        "file_backend": {
            "kind": "read_only_info_directory",
            "manifest_verified": True,
            "file_count": 24,
        },
        "database_backend": {
            "kind": "offline_candidate_release",
            "read_only_comparison": True,
            "release_sk": release_sk,
        },
        "comparison_method": {
            "deterministic_queries": "complete_keyspace_query_v1",
            "full_sets": "canonical_semantic_multiset_sha256_v1",
            "snapshot": "compatible_key_value_null_order_priority_v1",
            "ordering_fields_embedded_in_records": True,
            "approved_exception_policy": "S2_quarantine_only",
        },
        "deterministic_query_case_count": deterministic_query_case_count,
        "deterministic_query_unapproved_difference_count": query_differences,
        "full_set_unapproved_difference_count": full_set_differences,
        "snapshot_unapproved_difference_count": snapshot_differences,
        "contact_plaintext_exposure_count": 0,
        "approved_exception_count": len(exceptions),
        "approved_exceptions": exceptions,
        "sections": sections,
    }
    serialized = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    for sensitive_name in _SENSITIVE_AS_FIELDS:
        if f'"{sensitive_name}":' in serialized:
            raise LoadError("S3 报告意外包含联系人字段原文")
    return report
