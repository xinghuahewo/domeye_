"""固定 static INFO release 的最小只读运行时适配器。

本模块位于 ``backend/core`` 之外，只把数据库中的规范记录投影成既有检测器读取的
``BGPInfo`` 字典接口。运行时必须显式固定 content_id；普通查询不读取联系人表，也
不允许通过迭代接口触发全表装载。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Generic, Optional, TypeVar


KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")
_UNSEEN = object()
_MISSING = object()


class StaticInfoRuntimeError(RuntimeError):
    """static INFO 数据库运行时不能安全建立或读取。"""


class FullTableLoadRejected(StaticInfoRuntimeError):
    """请求路径试图枚举完整静态表。"""


@dataclass
class QueryTelemetry:
    """只记录查询性能元数据，不记录参数值或结果内容。"""

    query_count: int = 0
    full_table_load_count: int = 0
    latency_ms: list[float] = field(default_factory=list)
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
    )

    def observe(self, elapsed_ms: float) -> None:
        with self._lock:
            self.query_count += 1
            self.latency_ms.append(float(elapsed_ms))

    def reject_full_table_load(self) -> None:
        with self._lock:
            self.full_table_load_count += 1


class ExactLookupMapping(Mapping[KeyT, ValueT], Generic[KeyT, ValueT]):
    """仅支持精确键读取的 Mapping；显式拒绝全量迭代。"""

    def __init__(
        self,
        *,
        name: str,
        fetch: Callable[[KeyT], Optional[ValueT]],
        count: Callable[[], int],
        telemetry: QueryTelemetry,
        normalize_key: Callable[[Any], KeyT],
    ) -> None:
        self._name = name
        self._fetch = fetch
        self._count = count
        self._telemetry = telemetry
        self._normalize_key = normalize_key
        self._cache: Dict[KeyT, object] = {}

    def __getitem__(self, key: KeyT) -> ValueT:
        normalized = self._normalize_key(key)
        cached = self._cache.get(normalized, _UNSEEN)
        if cached is _UNSEEN:
            value = self._fetch(normalized)
            cached = _MISSING if value is None else value
            self._cache[normalized] = cached
        if cached is _MISSING:
            raise KeyError(key)
        return cached  # type: ignore[return-value]

    def __contains__(self, key: object) -> bool:
        try:
            self[key]  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            return False
        return True

    def __iter__(self) -> Iterator[KeyT]:
        self._telemetry.reject_full_table_load()
        raise FullTableLoadRejected(
            f"请求路径禁止枚举完整 static INFO 映射：{self._name}"
        )

    def __len__(self) -> int:
        return self._count()

    def cached_items(self) -> Dict[KeyT, ValueT]:
        """返回已通过精确查询加载的安全缓存副本。"""

        return {
            key: value  # type: ignore[misc]
            for key, value in self._cache.items()
            if value is not _MISSING
        }


def _str_key(value: Any) -> str:
    return str(value)


def _int_key(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("布尔值不是 ASN")
    return int(value)


def _json_object(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise StaticInfoRuntimeError("数据库 JSON 字段不是对象")


def _json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    raise StaticInfoRuntimeError("数据库 JSON 字段不是数组")


class PostgresStaticInfoRepository:
    """通过 DB-API 连接读取一个显式固定的候选 INFO release。"""

    def __init__(
        self,
        connection: Any,
        *,
        content_id: str,
        manifest_sha256: str,
        allow_statuses: tuple[str, ...] = ("validating", "ready", "active"),
        require_active_profile: Optional[str] = None,
    ) -> None:
        self.connection = connection
        self.telemetry = QueryTelemetry()
        self._count_cache: Dict[str, int] = {}
        self.release_sk, self.release_status = self._pin_release(
            content_id,
            manifest_sha256,
            allow_statuses,
        )
        self.content_id = content_id
        self.manifest_sha256 = manifest_sha256
        self.active_profile = require_active_profile
        if require_active_profile is not None:
            row = self._one(
                """
                SELECT 1
                FROM info.active_release AS active
                WHERE active.profile_name = %s
                  AND active.release_sk = %s
                """,
                (require_active_profile, self.release_sk),
            )
            if row is None or self.release_status != "active":
                raise StaticInfoRuntimeError(
                    "static INFO release 与要求的活动 profile 不一致"
                )

    def _pin_release(
        self,
        content_id: str,
        manifest_sha256: str,
        allow_statuses: tuple[str, ...],
    ) -> tuple[int, str]:
        row = self._one(
            """
            SELECT release.release_sk, release.status,
                   count(DISTINCT source.source_file_sk) AS file_count,
                   count(DISTINCT active.profile_name) AS active_count,
                   count(DISTINCT quality.quality_result_sk) FILTER (
                       WHERE quality.blocking AND quality.status <> 'pass'
                   ) AS blocking_failure_count
            FROM info.dataset_release AS release
            JOIN info.source_file AS source
              ON source.release_sk = release.release_sk
            LEFT JOIN info.active_release AS active
              ON active.release_sk = release.release_sk
            LEFT JOIN info.quality_result AS quality
              ON quality.release_sk = release.release_sk
            WHERE release.content_id = %s
              AND release.manifest_sha256 = %s
            GROUP BY release.release_sk, release.status
            """,
            (content_id, manifest_sha256),
        )
        if row is None:
            raise StaticInfoRuntimeError(
                "找不到与 manifest 身份一致的 static INFO release"
            )
        release_sk, status, file_count, _active_count, failures = row
        if str(status) not in allow_statuses:
            raise StaticInfoRuntimeError(
                f"static INFO release 状态不允许读取：{status}"
            )
        if int(file_count) != 24 or int(failures or 0) != 0:
            raise StaticInfoRuntimeError(
                "static INFO release 未满足 24 文件闭合或存在阻断质量失败"
            )
        return int(release_sk), str(status)

    def _one(
        self,
        query: str,
        parameters: tuple[Any, ...] = (),
    ) -> Optional[tuple[Any, ...]]:
        started = time.perf_counter()
        with self.connection.cursor() as cursor:
            cursor.execute(query, parameters)
            row = cursor.fetchone()
        self.telemetry.observe((time.perf_counter() - started) * 1000)
        return row

    def _all(
        self,
        query: str,
        parameters: tuple[Any, ...] = (),
    ) -> list[tuple[Any, ...]]:
        started = time.perf_counter()
        with self.connection.cursor() as cursor:
            cursor.execute(query, parameters)
            rows = cursor.fetchall()
        self.telemetry.observe((time.perf_counter() - started) * 1000)
        return list(rows)

    def _count(self, table: str, predicate: str = "") -> int:
        key = f"{table}|{predicate}"
        if key not in self._count_cache:
            allowed = {
                "autonomous_system",
                "country",
                "important_as",
                "important_prefix",
                "mapping_record",
                "prefix",
                "important_domain",
                "route_triplet_baseline",
            }
            if table not in allowed:
                raise StaticInfoRuntimeError(f"未批准的计数表：{table}")
            suffix = f" AND {predicate}" if predicate else ""
            row = self._one(
                f"SELECT count(*) FROM info.{table} "
                f"WHERE release_sk = %s{suffix}",
                (self.release_sk,),
            )
            self._count_cache[key] = int(row[0] if row else 0)
        return self._count_cache[key]

    def fetch_as_info(self, asn: str) -> Optional[Dict[str, Any]]:
        row = self._one(
            """
            SELECT system.as_name, system.country_code,
                   system.country_name_cn, system.org_name,
                   system.org_name_cn, system.as_type,
                   system.description, system.description_cn,
                   system.is_ddos_provider, system.attributes,
                   coalesce((
                       SELECT jsonb_agg(policy.token ORDER BY policy.ordinal)
                       FROM info.as_policy_member AS policy
                       WHERE policy.release_sk = system.release_sk
                         AND policy.asn = system.asn
                         AND policy.direction = 'import'
                   ), '[]'::jsonb) AS import_as,
                   coalesce((
                       SELECT jsonb_agg(policy.token ORDER BY policy.ordinal)
                       FROM info.as_policy_member AS policy
                       WHERE policy.release_sk = system.release_sk
                         AND policy.asn = system.asn
                         AND policy.direction = 'export'
                   ), '[]'::jsonb) AS export_as,
                   coalesce((
                       SELECT jsonb_agg(relation.target_asn ORDER BY relation.ordinal)
                       FROM info.as_relation AS relation
                       JOIN info.source_file AS source
                         ON source.source_file_sk = relation.source_file_sk
                       WHERE relation.release_sk = system.release_sk
                         AND relation.source_asn = system.asn
                         AND source.name = 'as_entity.csv'
                         AND relation.source_field = 'v4Peer'
                   ), '[]'::jsonb) AS v4_peer,
                   coalesce((
                       SELECT jsonb_agg(relation.target_asn ORDER BY relation.ordinal)
                       FROM info.as_relation AS relation
                       JOIN info.source_file AS source
                         ON source.source_file_sk = relation.source_file_sk
                       WHERE relation.release_sk = system.release_sk
                         AND relation.source_asn = system.asn
                         AND source.name = 'as_entity.csv'
                         AND relation.source_field = 'v6Peer'
                   ), '[]'::jsonb) AS v6_peer,
                   coalesce((
                       SELECT jsonb_agg(relation.target_asn ORDER BY relation.ordinal)
                       FROM info.as_relation AS relation
                       JOIN info.source_file AS source
                         ON source.source_file_sk = relation.source_file_sk
                       WHERE relation.release_sk = system.release_sk
                         AND relation.source_asn = system.asn
                         AND source.name = 'as_entity.csv'
                         AND relation.source_field = 'sibling_as'
                   ), '[]'::jsonb) AS sibling
            FROM info.autonomous_system AS system
            WHERE system.release_sk = %s
              AND system.asn = %s
            """,
            (self.release_sk, int(asn)),
        )
        if row is None:
            return None
        (
            as_name,
            country_code,
            country_name_cn,
            org_name,
            org_name_cn,
            as_type,
            description,
            description_cn,
            is_ddos_provider,
            attributes,
            import_as,
            export_as,
            v4_peer,
            v6_peer,
            sibling,
        ) = row
        value = _json_object(attributes)
        value.update(
            {
                "as_name": as_name or "",
                "as_country": country_code or "",
                "as_country_cn": country_name_cn or "",
                "type": as_type or "",
                "org_name": org_name or "",
                "org_name_cn": org_name_cn or "",
                "descr": description or "",
                "descr_cn": description_cn or "",
                "admin_info": "",
                "tech_info": "",
                "abuse_info": "",
                "import_as": _json_list(import_as),
                "export_as": _json_list(export_as),
                "is_ddos_provider": is_ddos_provider,
                "v4Peer": [int(item) for item in _json_list(v4_peer)],
                "v6Peer": [int(item) for item in _json_list(v6_peer)],
                "sibling_as": [int(item) for item in _json_list(sibling)],
            }
        )
        return value

    def fetch_country(self, alpha2: str) -> Optional[Dict[str, Any]]:
        row = self._one(
            """
            SELECT english_full_name, english_short_name,
                   chinese_short_name, alpha3, digital_code,
                   phone_code, jet_lag, latitude, longitude
            FROM info.country
            WHERE release_sk = %s AND alpha2 = %s
            """,
            (self.release_sk, alpha2.upper()),
        )
        if row is None:
            return None
        return {
            "english_full_name": row[0] or "",
            "english_short_name": row[1] or "",
            "chinese_short_name": row[2] or "",
            "three_letter_code": row[3] or "",
            "digital_code": row[4] or "",
            "phone_code": row[5] or "",
            "jet_lag": row[6] or "",
            "latitude": row[7],
            "longitude": row[8],
        }

    def fetch_important_as(self, asn: int) -> Optional[Dict[str, Any]]:
        row = self._one(
            """
            SELECT attributes
            FROM info.important_as
            WHERE release_sk = %s AND asn = %s
            """,
            (self.release_sk, asn),
        )
        return None if row is None else _json_object(row[0])

    def fetch_important_prefix(self, prefix: str) -> Optional[Dict[str, Any]]:
        row = self._one(
            """
            SELECT prefix.prefix_raw, prefix.number_raw, prefix.host_raw,
                   prefix.attributes
            FROM info.important_prefix AS prefix
            JOIN info.source_file AS source
              ON source.source_file_sk = prefix.source_file_sk
            WHERE prefix.release_sk = %s
              AND prefix.prefix_raw = %s
            ORDER BY CASE source.name
                         WHEN 'ipv4_all_prefix.xls' THEN 0 ELSE 1
                     END,
                     prefix.source_row_no
            LIMIT 1
            """,
            (self.release_sk, prefix),
        )
        if row is None:
            return None
        value = _json_object(row[3])
        value.pop("prefix", None)
        if row[1] is not None:
            value.setdefault("number", row[1])
        if row[2] is not None:
            value.setdefault("host", row[2])
        return value

    def fetch_as_prefix(self, asn: str) -> Optional[Dict[str, Any]]:
        rows = self._all(
            """
            SELECT history.prefix_raw, history.source_value
            FROM info.as_prefix_history AS history
            JOIN info.source_file AS source
              ON source.source_file_sk = history.source_file_sk
            WHERE history.release_sk = %s
              AND history.asn = %s
              AND source.name = 'pfx2as_dict.txt'
              AND history.source_active
            ORDER BY history.ordinal
            """,
            (self.release_sk, int(asn)),
        )
        if not rows:
            row = self._one(
                """
                SELECT 1
                FROM info.mapping_record AS mapping
                JOIN info.source_file AS source
                  ON source.source_file_sk = mapping.source_file_sk
                WHERE mapping.release_sk = %s
                  AND mapping.mapping_kind = 'as_prefix_history'
                  AND mapping.natural_key = %s
                  AND mapping.source_active
                  AND source.name = 'pfx2as_dict.txt'
                LIMIT 1
                """,
                (self.release_sk, asn),
            )
            if row is None:
                return None
        return {
            str(prefix): source_value
            for prefix, source_value in rows
            if prefix is not None
        }

    def fetch_as_relation(self, asn: str) -> Optional[Dict[str, Any]]:
        rows = self._all(
            """
            SELECT relation.source_field, relation.target_asn::text
            FROM info.as_relation AS relation
            JOIN info.source_file AS source
              ON source.source_file_sk = relation.source_file_sk
            WHERE relation.release_sk = %s
              AND relation.source_asn = %s
              AND source.name = 'as_rel_dict.txt'
              AND relation.source_active
            ORDER BY relation.ordinal
            """,
            (self.release_sk, int(asn)),
        )
        if not rows:
            row = self._one(
                """
                SELECT 1
                FROM info.mapping_record AS mapping
                JOIN info.source_file AS source
                  ON source.source_file_sk = mapping.source_file_sk
                WHERE mapping.release_sk = %s
                  AND mapping.mapping_kind = 'as_relation'
                  AND mapping.natural_key = %s
                  AND mapping.source_active
                  AND source.name = 'as_rel_dict.txt'
                LIMIT 1
                """,
                (self.release_sk, asn),
            )
            if row is None:
                return None
        fields: Dict[str, list[str]] = {
            "provider": [],
            "customer": [],
            "peers": [],
            "peer": [],
            "sibling": [],
        }
        for source_field, target_asn in rows:
            if source_field is not None:
                fields[str(source_field)].append(str(target_asn))
        return fields

    def fetch_prefix(self, prefix: str) -> Optional[Dict[str, Any]]:
        row = self._one(
            """
            SELECT attributes, name, description, route_raw, bgp_raw,
                   country_code, source_name, declared_domain_count,
                   declared_authoritative_domain_count
            FROM info.prefix
            WHERE release_sk = %s AND prefix_raw = %s
            """,
            (self.release_sk, prefix),
        )
        if row is None:
            return None
        value = _json_object(row[0])
        value.pop("prefix", None)
        value.update(
            {
                "name": row[1] or "",
                "descr": row[2] or "",
                "route": row[3] or "",
                "bgp": row[4] or "",
                "country": row[5] or "",
                "source": row[6] or "",
                "domain_num": int(row[7] or 0),
                "domain_auth_num": int(row[8] or 0),
            }
        )
        value.setdefault("domain", "")
        value.setdefault("domain_auth", "")
        return value

    def fetch_important_domain(self, domain: str) -> Optional[Any]:
        row = self._one(
            """
            SELECT source_value
            FROM info.important_domain
            WHERE release_sk = %s
              AND source_active
              AND domain_key_raw = %s
            LIMIT 1
            """,
            (self.release_sk, domain),
        )
        return None if row is None else row[0]

    def fetch_important_domains_for_prefix(
        self,
        prefix: str,
    ) -> Dict[str, Any]:
        """在数据库内关联一个前缀的全部重要域名，避免逐域名往返。"""

        rows = self._all(
            """
            SELECT DISTINCT ON (important.domain_key_raw)
                   important.domain_key_raw,
                   important.source_value
            FROM info.prefix_domain AS linked
            JOIN info.important_domain AS important
              ON important.release_sk = linked.release_sk
             AND important.domain_key_raw = linked.domain_key_raw
             AND important.source_active
            WHERE linked.release_sk = %s
              AND linked.prefix_raw = %s
            ORDER BY important.domain_key_raw,
                     important.source_row_no
            """,
            (self.release_sk, prefix),
        )
        return {
            str(domain): source_value
            for domain, source_value in rows
            if domain is not None
        }

    def fetch_private_as(self, public_asn: str) -> Optional[Dict[str, Any]]:
        rows = self._all(
            """
            SELECT location.private_asn_raw, location.ip_num, location.city
            FROM info.private_as_location AS location
            JOIN info.source_file AS source
              ON source.source_file_sk = location.source_file_sk
            WHERE location.release_sk = %s
              AND location.public_asn = %s
              AND source.name = 'private_as_dict_new.json'
              AND location.source_active
            ORDER BY location.ordinal
            """,
            (self.release_sk, int(public_asn)),
        )
        if not rows:
            row = self._one(
                """
                SELECT 1
                FROM info.mapping_record AS mapping
                JOIN info.source_file AS source
                  ON source.source_file_sk = mapping.source_file_sk
                WHERE mapping.release_sk = %s
                  AND mapping.mapping_kind = 'private_as_location'
                  AND mapping.natural_key = %s
                  AND mapping.source_active
                  AND source.name = 'private_as_dict_new.json'
                LIMIT 1
                """,
                (self.release_sk, public_asn),
            )
            if row is None:
                return None
        return {
            str(private_asn): {
                "ip_num": ip_num,
                "city": city,
            }
            for private_asn, ip_num, city in rows
            if private_asn is not None
        }

    def fetch_triplet_first(
        self,
        first_as: str,
    ) -> Optional[ExactLookupMapping[str, Any]]:
        row = self._one(
            """
            SELECT 1
            FROM info.route_triplet_baseline
            WHERE release_sk = %s
              AND source_active
              AND first_as = %s
            LIMIT 1
            """,
            (self.release_sk, int(first_as)),
        )
        if row is None:
            return None
        return ExactLookupMapping(
            name=f"triplet_second:{first_as}",
            fetch=lambda second_as: self.fetch_triplet_second(
                first_as,
                second_as,
            ),
            count=lambda: self.count_triplet_second(first_as),
            telemetry=self.telemetry,
            normalize_key=_str_key,
        )

    def fetch_triplet_second(
        self,
        first_as: str,
        second_as: str,
    ) -> Optional[ExactLookupMapping[str, Dict[str, Any]]]:
        row = self._one(
            """
            SELECT 1
            FROM info.route_triplet_baseline
            WHERE release_sk = %s
              AND source_active
              AND first_as = %s
              AND second_as = %s
            LIMIT 1
            """,
            (self.release_sk, int(first_as), int(second_as)),
        )
        if row is None:
            return None
        return ExactLookupMapping(
            name=f"triplet_third:{first_as}:{second_as}",
            fetch=lambda third_as: self.fetch_triplet_third(
                first_as,
                second_as,
                third_as,
            ),
            count=lambda: self.count_triplet_third(
                first_as,
                second_as,
            ),
            telemetry=self.telemetry,
            normalize_key=_str_key,
        )

    def fetch_triplet_third(
        self,
        first_as: str,
        second_as: str,
        third_as: str,
    ) -> Optional[Dict[str, Any]]:
        row = self._one(
            """
            SELECT stability
            FROM info.route_triplet_baseline
            WHERE release_sk = %s
              AND source_active
              AND first_as = %s
              AND second_as = %s
              AND third_as = %s
            ORDER BY source_row_no DESC
            LIMIT 1
            """,
            (
                self.release_sk,
                int(first_as),
                int(second_as),
                int(third_as),
            ),
        )
        return None if row is None else {"stability": row[0]}

    def count_triplet_first(self) -> int:
        row = self._one(
            """
            SELECT count(DISTINCT first_as)
            FROM info.route_triplet_baseline
            WHERE release_sk = %s AND source_active
            """,
            (self.release_sk,),
        )
        return int(row[0] if row else 0)

    def count_triplet_second(self, first_as: str) -> int:
        row = self._one(
            """
            SELECT count(DISTINCT second_as)
            FROM info.route_triplet_baseline
            WHERE release_sk = %s
              AND source_active
              AND first_as = %s
            """,
            (self.release_sk, int(first_as)),
        )
        return int(row[0] if row else 0)

    def count_triplet_third(
        self,
        first_as: str,
        second_as: str,
    ) -> int:
        row = self._one(
            """
            SELECT count(DISTINCT third_as)
            FROM info.route_triplet_baseline
            WHERE release_sk = %s
              AND source_active
              AND first_as = %s
              AND second_as = %s
            """,
            (self.release_sk, int(first_as), int(second_as)),
        )
        return int(row[0] if row else 0)

    def count_mapping_kind(self, kind: str) -> int:
        safe = {
            "as_prefix_history",
            "as_relation",
            "private_as_location",
        }
        if kind not in safe:
            raise StaticInfoRuntimeError(f"未知 mapping kind：{kind}")
        return self._count(
            "mapping_record",
            f"mapping_kind = '{kind}' AND source_active",
        )


class DatabaseStaticInfo:
    """兼容既有 ``BGPInfo`` 属性名的 release-pinned 惰性快照。"""

    snapshot_kind = "release_pinned_lazy_exact_lookup_v1"

    def __init__(self, repository: PostgresStaticInfoRepository) -> None:
        self.repository = repository
        telemetry = repository.telemetry
        self.content_id = repository.content_id
        self.manifest_sha256 = repository.manifest_sha256
        self.release_sk = repository.release_sk
        self.release_status = repository.release_status
        self.as_info = ExactLookupMapping(
            name="as_info",
            fetch=repository.fetch_as_info,
            count=lambda: repository._count("autonomous_system"),
            telemetry=telemetry,
            normalize_key=_str_key,
        )
        self.country = ExactLookupMapping(
            name="country",
            fetch=repository.fetch_country,
            count=lambda: repository._count("country"),
            telemetry=telemetry,
            normalize_key=lambda value: str(value).upper(),
        )
        self.important_as_dict = ExactLookupMapping(
            name="important_as",
            fetch=repository.fetch_important_as,
            count=lambda: repository._count("important_as"),
            telemetry=telemetry,
            normalize_key=_int_key,
        )
        self.important_prefix_dict = ExactLookupMapping(
            name="important_prefix",
            fetch=repository.fetch_important_prefix,
            count=lambda: repository._count("important_prefix"),
            telemetry=telemetry,
            normalize_key=_str_key,
        )
        self.as_prefix_dict = ExactLookupMapping(
            name="as_prefix",
            fetch=repository.fetch_as_prefix,
            count=lambda: repository.count_mapping_kind(
                "as_prefix_history"
            ),
            telemetry=telemetry,
            normalize_key=_str_key,
        )
        self.as_rel_dict = ExactLookupMapping(
            name="as_relation",
            fetch=repository.fetch_as_relation,
            count=lambda: repository.count_mapping_kind("as_relation"),
            telemetry=telemetry,
            normalize_key=_str_key,
        )
        self.prefix_info = ExactLookupMapping(
            name="prefix",
            fetch=repository.fetch_prefix,
            count=lambda: repository._count("prefix"),
            telemetry=telemetry,
            normalize_key=_str_key,
        )
        self.important_domain_dict = ExactLookupMapping(
            name="important_domain",
            fetch=repository.fetch_important_domain,
            count=lambda: repository._count("important_domain"),
            telemetry=telemetry,
            normalize_key=_str_key,
        )
        self.private_as_dict = ExactLookupMapping(
            name="private_as",
            fetch=repository.fetch_private_as,
            count=lambda: repository.count_mapping_kind(
                "private_as_location"
            ),
            telemetry=telemetry,
            normalize_key=_str_key,
        )
        self.triplet_info = ExactLookupMapping(
            name="triplet_first_as",
            fetch=repository.fetch_triplet_first,
            count=repository.count_triplet_first,
            telemetry=telemetry,
            normalize_key=_str_key,
        )

    @property
    def request_path_full_table_load_count(self) -> int:
        return self.repository.telemetry.full_table_load_count


@dataclass(frozen=True)
class RuntimeBackendState:
    """仓库外原子状态文件中已获准的数据库运行身份。"""

    generation: int
    backend: str
    content_id: str
    manifest_sha256: str
    release_sk: int
    changed_at: str
    reason: str


def _reject_duplicate_state_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise StaticInfoRuntimeError(f"运行后端状态存在重复键：{key}")
        value[key] = item
    return value


def read_runtime_backend_state(
    state_path: os.PathLike[str] | str,
) -> RuntimeBackendState:
    """读取并严格验证数据库模式的原子运行状态。

    普通数据库运行入口故意不提供隐式文件回退：状态不是 ``database`` 时失败关闭，
    文件回滚只能由独立、受控的发布边界显式启动。
    """

    path = Path(state_path)
    if path.is_symlink() or not path.is_file():
        raise StaticInfoRuntimeError(
            f"static INFO 运行状态缺失、不是普通文件或为软链接：{path}"
        )
    before = path.stat()
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_state_pairs,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StaticInfoRuntimeError(
            f"static INFO 运行状态读取失败：{exc}"
        ) from exc
    after = path.stat()
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after:
        raise StaticInfoRuntimeError("static INFO 运行状态在读取期间发生变化")
    if not isinstance(value, dict):
        raise StaticInfoRuntimeError("static INFO 运行状态顶层必须是对象")
    if (
        value.get("schema_version") != 1
        or value.get("component") != "static_info_runtime_backend_state"
        or value.get("backend") != "database"
    ):
        raise StaticInfoRuntimeError(
            "普通运行时只接受 v1 database static INFO 状态"
        )
    generation = value.get("generation")
    release_sk = value.get("release_sk")
    content_id = value.get("content_id")
    manifest_sha256 = value.get("manifest_sha256")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation <= 0
        or not isinstance(release_sk, int)
        or isinstance(release_sk, bool)
        or release_sk <= 0
        or not isinstance(content_id, str)
        or re.fullmatch(r"info_v1_[0-9a-f]{32}", content_id) is None
        or not isinstance(manifest_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
    ):
        raise StaticInfoRuntimeError(
            "static INFO 运行状态的代次或 release 身份无效"
        )
    changed_at = value.get("changed_at")
    reason = value.get("reason")
    if (
        not isinstance(changed_at, str)
        or not changed_at
        or not isinstance(reason, str)
        or not reason
    ):
        raise StaticInfoRuntimeError("static INFO 运行状态缺少变更时间或原因")
    return RuntimeBackendState(
        generation=generation,
        backend="database",
        content_id=content_id,
        manifest_sha256=manifest_sha256,
        release_sk=release_sk,
        changed_at=changed_at,
        reason=reason,
    )


class PinnedStaticInfoRuntime(AbstractContextManager["PinnedStaticInfoRuntime"]):
    """持有一个只读连接和一个固定 release 的运行时快照。"""

    def __init__(
        self,
        *,
        state: RuntimeBackendState,
        connection: Any,
        repository: PostgresStaticInfoRepository,
    ) -> None:
        self.state = state
        self.connection = connection
        self.repository = repository
        self.info = DatabaseStaticInfo(repository)
        self.closed = False

    @property
    def content_id(self) -> str:
        return self.info.content_id

    @property
    def release_sk(self) -> int:
        return self.info.release_sk

    def close(self) -> None:
        if not self.closed:
            self.connection.close()
            self.closed = True

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def open_pinned_database_runtime(
    state_path: os.PathLike[str] | str,
    connect: Callable[[], Any],
    *,
    active_profile: str = "core",
) -> PinnedStaticInfoRuntime:
    """按原子状态打开一个请求/快照/检测运行独占的 INFO release。"""

    state = read_runtime_backend_state(state_path)
    connection = connect()
    try:
        connection.set_session(readonly=True, autocommit=True)
        repository = PostgresStaticInfoRepository(
            connection,
            content_id=state.content_id,
            manifest_sha256=state.manifest_sha256,
            allow_statuses=("active",),
            require_active_profile=active_profile,
        )
        if repository.release_sk != state.release_sk:
            raise StaticInfoRuntimeError(
                "运行状态 release_sk 与数据库活动 release 不一致"
            )
        return PinnedStaticInfoRuntime(
            state=state,
            connection=connection,
            repository=repository,
        )
    except BaseException:
        connection.close()
        raise
