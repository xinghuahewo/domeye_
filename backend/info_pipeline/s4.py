"""S4 检测等价、性能、权限和可观测性真实验收。"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib
import io
import ipaddress
import json
import math
import os
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .loader import LoadError
from .manifest import validate_manifest
from .output import write_text_exclusive
from .runtime import DatabaseStaticInfo, PostgresStaticInfoRepository
from .shadow import _verify_all_sources


_EVENT_TYPES = (
    "hijack",
    "sub_hijack",
    "leak",
    "prefix_outage",
    "as_outage",
    "country_outage",
)
_SENSITIVE_OUTPUT_KEYS = frozenset(
    {"admin_info", "tech_info", "abuse_info"}
)


class S4AcceptanceError(LoadError):
    """S4 不能产生可判定的真实证据。"""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _sha256_value(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise S4AcceptanceError("性能样本不能为空")
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 6)


def _regression_percent(candidate: float, baseline: float) -> float:
    if baseline <= 0:
        raise S4AcceptanceError("性能基线必须大于 0")
    return round(((candidate - baseline) / baseline) * 100.0, 6)


def _core_hash_state(core_backend_root: Path) -> Dict[str, Any]:
    manifest_path = core_backend_root / "core.sha256"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise S4AcceptanceError(f"检测核心哈希清单缺失：{manifest_path}")
    files: Dict[str, str] = {}
    for raw_line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise S4AcceptanceError("检测核心哈希清单格式错误")
        expected, relative = parts
        relative = relative.lstrip("*")
        path = core_backend_root / relative
        if path.is_symlink() or not path.is_file():
            raise S4AcceptanceError(f"检测核心文件缺失：{relative}")
        observed = _sha256_file(path)
        if observed != expected:
            raise S4AcceptanceError(
                f"检测核心哈希漂移：{relative} "
                f"observed={observed} expected={expected}"
            )
        files[relative] = observed
    return {
        "manifest_sha256": _sha256_file(manifest_path),
        "file_count": len(files),
        "files_sha256": _sha256_value(files),
    }


def _load_core_classes(core_backend_root: Path) -> Dict[str, Any]:
    root = str(core_backend_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    modules = {
        "hijack": importlib.import_module("core.BGPHijack"),
        "sub_hijack": importlib.import_module("core.BGPSubHijack"),
        "leak": importlib.import_module("core.BGPLeak"),
        "outage": importlib.import_module("core.BGPOutage"),
        "info": importlib.import_module("core.BGPInfo"),
        "other": importlib.import_module("utils.get_other_info"),
    }
    return {
        "BGPHijack": modules["hijack"].BGPHijack,
        "BGPSubHijack": modules["sub_hijack"].BGPSubHijack,
        "BGPLeak": modules["leak"].BGPLeak,
        "BGPOutage": modules["outage"].BGPOutage,
        "BGPInfo": modules["info"].BGPInfo,
        "get_leak_triplet": modules["other"].get_leak_triplet,
    }


def _load_file_info(core_backend_root: Path) -> Any:
    classes = _load_core_classes(core_backend_root)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        return classes["BGPInfo"]()


def _connect_reader(
    *,
    host: str,
    port: int,
    db_name: str,
    db_user: str,
) -> Any:
    try:
        import psycopg2
    except ImportError as exc:
        raise S4AcceptanceError("S4 运行环境缺少 psycopg2") from exc
    connection = psycopg2.connect(
        host=host,
        port=port,
        dbname=db_name,
        user=db_user,
        connect_timeout=10,
        application_name="domeye_static_info_s4",
    )
    connection.set_session(readonly=True, autocommit=True)
    return connection


def _snapshot_probe(
    *,
    backend: str,
    core_backend_root: Path,
    source_dir: Path,
    host: str,
    port: int,
    db_name: str,
    db_user: str,
    content_id: str,
    manifest_sha256: str,
) -> Dict[str, Any]:
    os.environ["INFO_DIR"] = str(source_dir)
    started = time.perf_counter()
    query_count = 0
    full_table_load_count = 0
    if backend == "file":
        info = _load_file_info(core_backend_root)
        identity = {
            "as_count": len(info.as_info),
            "prefix_count": len(info.prefix_info),
        }
        snapshot_kind = "legacy_file_materialized_bgp_info"
    elif backend == "database":
        connection = _connect_reader(
            host=host,
            port=port,
            db_name=db_name,
            db_user=db_user,
        )
        try:
            repository = PostgresStaticInfoRepository(
                connection,
                content_id=content_id,
                manifest_sha256=manifest_sha256,
                allow_statuses=("validating",),
            )
            info = DatabaseStaticInfo(repository)
            identity = {
                "release_sk": info.release_sk,
                "content_id": info.content_id,
            }
            snapshot_kind = info.snapshot_kind
            query_count = repository.telemetry.query_count
            full_table_load_count = (
                repository.telemetry.full_table_load_count
            )
        finally:
            connection.close()
    else:
        raise S4AcceptanceError(f"未知快照后端：{backend}")
    elapsed = time.perf_counter() - started
    peak_kib = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return {
        "backend": backend,
        "snapshot_kind": snapshot_kind,
        "load_time_seconds": round(elapsed, 6),
        "peak_rss_bytes": peak_kib * 1024,
        "query_count": query_count,
        "request_path_full_table_load_count": full_table_load_count,
        "identity": identity,
    }


def _run_snapshot_probe(
    *,
    backend: str,
    core_backend_root: Path,
    source_dir: Path,
    host: str,
    port: int,
    db_name: str,
    db_user: str,
    content_id: str,
    manifest_sha256: str,
) -> Dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "backend.info_pipeline.s4",
        "snapshot-probe",
        "--backend",
        backend,
        "--core-backend-root",
        str(core_backend_root),
        "--source-dir",
        str(source_dir),
        "--db-host",
        host,
        "--db-port",
        str(port),
        "--db-name",
        db_name,
        "--db-user",
        db_user,
        "--content-id",
        content_id,
        "--manifest-sha256",
        manifest_sha256,
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=dict(os.environ),
    )
    if completed.returncode != 0:
        raise S4AcceptanceError(
            f"{backend} 快照探针失败：{completed.stderr[-4000:]}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise S4AcceptanceError(
            f"{backend} 快照探针输出不是 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise S4AcceptanceError(f"{backend} 快照探针顶层不是对象")
    return value


def _literal_domains(prefix_value: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for field in ("domain", "domain_auth"):
        raw = prefix_value.get(field)
        if raw in (None, ""):
            continue
        try:
            parsed = ast.literal_eval(str(raw))
        except (SyntaxError, ValueError):
            continue
        if isinstance(parsed, list):
            result.extend(str(item) for item in parsed)
    return result


def _select_detector_fixture(
    repository: PostgresStaticInfoRepository,
    file_info: Any,
    core_classes: Mapping[str, Any],
) -> Dict[str, Any]:
    prefix_rows = repository._all(
        """
        SELECT prefix_raw, route_raw,
               coalesce(declared_domain_count, 0)
                 + coalesce(declared_authoritative_domain_count, 0)
                   AS service_count
        FROM info.prefix
        WHERE release_sk = %s
          AND family(prefix_cidr) = 4
          AND masklen(prefix_cidr) <= 28
          AND route_raw IS NOT NULL
        ORDER BY service_count DESC, prefix_raw
        LIMIT 500
        """,
        (repository.release_sk,),
    )
    candidate_as_rows = repository._all(
        """
        SELECT asn::text
        FROM info.autonomous_system
        WHERE release_sk = %s
          AND asn NOT BETWEEN 64512 AND 65535
          AND asn < 4200000000
          AND coalesce(org_name_cn, org_name, '') NOT LIKE '%%个人%%'
          AND coalesce(org_name_cn, org_name, '') NOT LIKE '%%未知%%'
          AND coalesce(is_ddos_provider, false) = false
        ORDER BY asn
        LIMIT 2048
        """,
        (repository.release_sk,),
    )
    candidates = [str(row[0]) for row in candidate_as_rows]
    hijack = core_classes["BGPHijack"](None, file_info, None)
    sub_hijack = core_classes["BGPSubHijack"](None, file_info, None)
    leak = core_classes["BGPLeak"](None, file_info, None)
    selected: Optional[tuple[str, str, str, str]] = None
    for prefix_raw, route_raw, _service_count in prefix_rows:
        origin = str(route_raw or "")
        if not origin.isdigit() or origin not in file_info.as_info:
            continue
        try:
            network = ipaddress.ip_network(str(prefix_raw), strict=True)
            child = str(next(network.subnets(prefixlen_diff=1)))
        except (ValueError, StopIteration):
            continue
        for attacker in candidates:
            if attacker == origin:
                continue
            if attacker not in file_info.as_info:
                continue
            is_hijack, _ = hijack.is_hijack_event(
                str(prefix_raw),
                {origin, attacker},
                origin,
            )
            is_sub, _ = sub_hijack.is_sub_hijack_event(
                attacker,
                {origin},
                child,
                str(prefix_raw),
            )
            is_leak, _ = leak.is_leak_event(origin, attacker)
            if bool(is_hijack) and bool(is_sub) and bool(is_leak):
                selected = (
                    str(prefix_raw),
                    child,
                    origin,
                    attacker,
                )
                break
        if selected is not None:
            break
    if selected is None:
        raise S4AcceptanceError(
            "无法从同一 release 确定性选出三类正向检测语料"
        )
    prefix, child_prefix, origin_as, attacker_as = selected

    important_rows = repository._all(
        """
        SELECT important.asn::text
        FROM info.important_as AS important
        JOIN info.autonomous_system AS system
          ON system.release_sk = important.release_sk
         AND system.asn = important.asn
        WHERE important.release_sk = %s
          AND important.asn NOT BETWEEN 64512 AND 65535
          AND important.asn < 4200000000
        ORDER BY important.asn
        LIMIT 100
        """,
        (repository.release_sk,),
    )
    important_as = next(
        (
            str(row[0])
            for row in important_rows
            if str(row[0]) in file_info.as_info
        ),
        None,
    )
    if important_as is None:
        raise S4AcceptanceError("固定语料缺少可用的重要 AS")

    country_row = repository._one(
        """
        SELECT system.country_code, count(*) AS as_count
        FROM info.autonomous_system AS system
        JOIN info.country AS country
          ON country.release_sk = system.release_sk
         AND country.alpha2 = system.country_code
        WHERE system.release_sk = %s
          AND system.country_code IS NOT NULL
        GROUP BY system.country_code
        HAVING count(*) >= 100
        ORDER BY system.country_code
        LIMIT 1
        """,
        (repository.release_sk,),
    )
    if country_row is None:
        raise S4AcceptanceError("固定语料缺少至少 100 个 AS 的国家")
    country_code = str(country_row[0])
    cohort_rows = repository._all(
        """
        SELECT asn::text
        FROM info.autonomous_system
        WHERE release_sk = %s AND country_code = %s
        ORDER BY asn
        LIMIT 100
        """,
        (repository.release_sk, country_code),
    )
    cohort = [str(row[0]) for row in cohort_rows]
    if len(cohort) != 100:
        raise S4AcceptanceError("国家中断固定 cohort 不是 100 个 AS")

    triplet_row = repository._one(
        """
        SELECT first_as::text, second_as::text, third_as::text, stability
        FROM (
            SELECT DISTINCT ON (first_as, second_as, third_as)
                   first_as, second_as, third_as, stability
            FROM info.route_triplet_baseline
            WHERE release_sk = %s AND source_active
            ORDER BY first_as, second_as, third_as, source_row_no DESC
        ) AS winner
        WHERE stability >= 0.2
        ORDER BY first_as, second_as, third_as
        LIMIT 1
        """,
        (repository.release_sk,),
    )
    if triplet_row is None:
        raise S4AcceptanceError("固定语料缺少稳定度不低于 0.2 的三元组")
    triplet = [str(triplet_row[index]) for index in range(3)]

    fixture = {
        "fixture_version": "static-info-detector-fixture-v1",
        "selection": "deterministic_same_release_positive_decision_corpus_v1",
        "rib": {
            "prefix": prefix,
            "origin_as": origin_as,
            "vantage_points": [
                {"id": "fixture-vp-1", "as_path": origin_as},
                {"id": "fixture-vp-2", "as_path": origin_as},
                {"id": "fixture-vp-3", "as_path": origin_as},
            ],
            "country_code": country_code,
            "country_cohort_asns": cohort,
        },
        "updates": [
            {
                "event_type": "hijack",
                "flag": "A",
                "prefix": prefix,
                "as_path": attacker_as,
            },
            {
                "event_type": "sub_hijack",
                "flag": "A",
                "prefix": child_prefix,
                "as_path": attacker_as,
            },
            {
                "event_type": "leak",
                "flag": "A",
                "prefix": prefix,
                "as_path": " ".join(triplet),
            },
            {
                "event_type": "prefix_outage",
                "flag": "W",
                "prefix": prefix,
                "withdrawn_vantage_point_count": 3,
            },
            {
                "event_type": "as_outage",
                "flag": "W",
                "asn": important_as,
                "outage_prefix_count": 12,
                "total_prefix_count": 12,
            },
            {
                "event_type": "country_outage",
                "flag": "W",
                "country_code": country_code,
                "affected_asns": cohort[:8],
                "cohort_asn_count": 100,
            },
        ],
        "keys": {
            "prefix": prefix,
            "child_prefix": child_prefix,
            "origin_as": origin_as,
            "attacker_as": attacker_as,
            "important_as": important_as,
            "country_code": country_code,
            "triplet": triplet,
        },
    }
    fixture["fixture_sha256"] = _sha256_value(fixture)
    return fixture


def _sanitized_as_value(value: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(value)
    for key in _SENSITIVE_OUTPUT_KEYS:
        result[key] = ""
    return result


def _project_info(source: Any, fixture: Mapping[str, Any]) -> Any:
    keys = fixture["keys"]
    as_keys = {
        str(keys["origin_as"]),
        str(keys["attacker_as"]),
        str(keys["important_as"]),
        *(str(item) for item in keys["triplet"]),
    }
    as_info: Dict[str, Any] = {}
    as_rel: Dict[str, Any] = {}
    as_prefix: Dict[str, Any] = {}
    important_as: Dict[int, Any] = {}
    for asn in sorted(as_keys, key=int):
        value = source.as_info.get(asn)
        if value is not None:
            as_info[asn] = _sanitized_as_value(value)
        relation = source.as_rel_dict.get(asn)
        if relation is not None:
            as_rel[asn] = dict(relation)
        prefixes = source.as_prefix_dict.get(asn)
        if prefixes is not None:
            as_prefix[asn] = dict(prefixes)
        important_value = source.important_as_dict.get(int(asn))
        if important_value is not None:
            important_as[int(asn)] = important_value

    prefix = str(keys["prefix"])
    prefix_value = source.prefix_info.get(prefix)
    if prefix_value is None:
        raise S4AcceptanceError(f"固定前缀不在 INFO 快照中：{prefix}")
    domains = _literal_domains(prefix_value)
    important_domain: Dict[str, Any] = {}
    for domain in domains:
        value = source.important_domain_dict.get(domain)
        if value is not None:
            important_domain[domain] = value

    country_code = str(keys["country_code"])
    country_value = source.country.get(country_code)
    if country_value is None:
        raise S4AcceptanceError(f"固定国家不在 INFO 快照中：{country_code}")

    triplet = [str(item) for item in keys["triplet"]]
    triplet_first = source.triplet_info.get(triplet[0])
    triplet_info: Dict[str, Any] = {}
    if triplet_first is not None:
        second = triplet_first.get(triplet[1])
        if second is not None and triplet[2] in second:
            triplet_info = {
                triplet[0]: {
                    triplet[1]: {
                        triplet[2]: dict(second[triplet[2]])
                    }
                }
            }

    return SimpleNamespace(
        as_info=as_info,
        country={country_code: dict(country_value)},
        important_as_dict=important_as,
        important_prefix_dict={},
        as_prefix_dict=as_prefix,
        as_rel_dict=as_rel,
        prefix_info={prefix: dict(prefix_value)},
        important_domain_dict=important_domain,
        private_as_dict={},
        triplet_info=triplet_info,
    )


def _evaluate_core_cases(
    info: Any,
    fixture: Mapping[str, Any],
    core_classes: Mapping[str, Any],
) -> Dict[str, Any]:
    keys = fixture["keys"]
    prefix = str(keys["prefix"])
    child = str(keys["child_prefix"])
    origin = str(keys["origin_as"])
    attacker = str(keys["attacker_as"])
    important_as = str(keys["important_as"])
    country = str(keys["country_code"])
    triplet = [str(item) for item in keys["triplet"]]

    hijack = core_classes["BGPHijack"](None, info, None)
    is_hijack, hijack_reason = hijack.is_hijack_event(
        prefix,
        {origin, attacker},
        origin,
    )
    hijack_level, hijack_description = hijack.hijack_level(
        prefix,
        {origin, attacker},
    )

    sub = core_classes["BGPSubHijack"](None, info, None)
    is_sub, sub_reason = sub.is_sub_hijack_event(
        attacker,
        {origin},
        child,
        prefix,
    )
    sub_level, sub_description = sub.sub_hijack_level(
        prefix,
        {origin, attacker},
    )

    leak = core_classes["BGPLeak"](None, info, None)
    is_leak, leak_reason = leak.is_leak_event(origin, attacker)
    leak_level, leak_description = leak.leak_level(prefix, attacker)
    relation_code = leak.get_as_rel(origin, attacker)
    triplet_stability = core_classes["get_leak_triplet"](
        info.triplet_info,
        *triplet,
    )

    outage = core_classes["BGPOutage"](None, info, None)
    prefix_level, prefix_description = getattr(
        outage,
        "_BGPOutage__prefix_outage_level",
    )(prefix)
    outage.as_outage_event = {
        important_as: {
            1: {
                "total_prefix_num": 12,
                "max_outage_prefix_num": 12,
                "max_outage_prefix_ratio": 1.0,
                "outage_level": "",
                "outage_level_descr": "",
            }
        }
    }
    as_level, as_description = getattr(
        outage,
        "_BGPOutage__as_outage_level",
    )(important_as, 1)
    outage.country_outage_event = {
        country: {
            1: {
                "total_as_num": 100,
                "max_outage_as_num": 8,
                "max_outage_as_ratio": 0.08,
                "outage_level": "",
                "outage_level_descr": "",
            }
        }
    }
    country_level, country_description = getattr(
        outage,
        "_BGPOutage__country_outage_level",
    )(country, 1)

    result = {
        "hijack": {
            "event_count": int(bool(is_hijack)),
            "natural_key": f"{prefix}|{origin}|{attacker}",
            "filter_reason": str(hijack_reason),
            "risk_level": str(hijack_level),
            "critical_description": str(hijack_description),
            "core_methods": ["is_hijack_event", "hijack_level"],
        },
        "sub_hijack": {
            "event_count": int(bool(is_sub)),
            "natural_key": f"{child}|{prefix}|{origin}|{attacker}",
            "filter_reason": str(sub_reason),
            "risk_level": str(sub_level),
            "critical_description": str(sub_description),
            "core_methods": [
                "is_sub_hijack_event",
                "sub_hijack_level",
            ],
        },
        "leak": {
            "event_count": int(bool(is_leak)),
            "natural_key": f"{prefix}|{'|'.join(triplet)}",
            "filter_reason": str(leak_reason),
            "risk_level": str(leak_level),
            "critical_description": str(leak_description),
            "relationship_code": int(relation_code),
            "triplet_stability": triplet_stability,
            "core_methods": [
                "is_leak_event",
                "get_as_rel",
                "leak_level",
                "get_leak_triplet",
            ],
        },
        "prefix_outage": {
            "event_count": 1,
            "natural_key": prefix,
            "filter_reason": "fixed_update_reached_prefix_threshold",
            "risk_level": str(prefix_level),
            "critical_description": str(prefix_description),
            "core_methods": ["__prefix_outage_level"],
        },
        "as_outage": {
            "event_count": 1,
            "natural_key": important_as,
            "filter_reason": "fixed_update_reached_as_threshold",
            "risk_level": str(as_level),
            "critical_description": str(as_description),
            "core_methods": ["__as_outage_level"],
        },
        "country_outage": {
            "event_count": 1,
            "natural_key": country,
            "filter_reason": "fixed_update_reached_country_threshold",
            "risk_level": str(country_level),
            "critical_description": str(country_description),
            "core_methods": ["__country_outage_level"],
        },
    }
    if tuple(result) != _EVENT_TYPES:
        raise S4AcceptanceError("六类检测输出顺序或集合发生变化")
    return result


def _result_differences(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> list[Dict[str, Any]]:
    differences: list[Dict[str, Any]] = []
    for event_type in _EVENT_TYPES:
        left_value = left.get(event_type)
        right_value = right.get(event_type)
        if left_value != right_value:
            differences.append(
                {
                    "event_type": event_type,
                    "file_sha256": _sha256_value(left_value),
                    "database_sha256": _sha256_value(right_value),
                    "reason": "normalized_core_output_mismatch",
                }
            )
    return differences


def _benchmark_detector_throughput(
    file_info: Any,
    database_info: Any,
    fixture: Mapping[str, Any],
    core_classes: Mapping[str, Any],
) -> Dict[str, Any]:
    _evaluate_core_cases(file_info, fixture, core_classes)
    _evaluate_core_cases(database_info, fixture, core_classes)
    loops = 80
    rounds = 7
    samples: Dict[str, list[float]] = {"file": [], "database": []}
    for round_index in range(rounds):
        order = (
            ("file", file_info, "database", database_info)
            if round_index % 2 == 0
            else ("database", database_info, "file", file_info)
        )
        for index in (0, 2):
            name = order[index]
            info = order[index + 1]
            started = time.perf_counter()
            for _ in range(loops):
                _evaluate_core_cases(info, fixture, core_classes)
            elapsed = time.perf_counter() - started
            samples[name].append((loops * 6) / elapsed)
    file_eps = statistics.median(samples["file"])
    database_eps = statistics.median(samples["database"])
    return {
        "unit": "normalized_event_decisions_per_second",
        "loops_per_round": loops,
        "round_count": rounds,
        "file_throughput": round(file_eps, 6),
        "database_throughput": round(database_eps, 6),
        "regression_percent": _regression_percent(
            database_eps,
            file_eps,
        )
        * -1.0,
        "file_rounds": [round(item, 6) for item in samples["file"]],
        "database_rounds": [
            round(item, 6) for item in samples["database"]
        ],
    }


def _measure_query(
    connection: Any,
    query: str,
    parameters: tuple[Any, ...],
) -> float:
    started = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        cursor.fetchone()
    return (time.perf_counter() - started) * 1000


def _benchmark_database_queries(
    connection: Any,
    release_sk: int,
) -> Dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT asn::text
            FROM info.autonomous_system
            WHERE release_sk = %s
            ORDER BY source_order
            LIMIT 128
            """,
            (release_sk,),
        )
        asns = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT prefix_raw, host(prefix_cidr)
            FROM info.prefix
            WHERE release_sk = %s
            ORDER BY source_row_no
            LIMIT 128
            """,
            (release_sk,),
        )
        prefixes = [(str(row[0]), str(row[1])) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT domain_key_raw
            FROM info.domain_record
            WHERE release_sk = %s AND source_active
            ORDER BY source_priority, source_row_no
            LIMIT 128
            """,
            (release_sk,),
        )
        domains = [str(row[0]) for row in cursor.fetchall()]
    if len(asns) < 64 or len(prefixes) < 64 or len(domains) < 64:
        raise S4AcceptanceError("查询性能样本键不足")

    exact_queries = (
        (
            "autonomous_system",
            """
            SELECT as_name, country_code, org_name, is_ddos_provider
            FROM info.autonomous_system
            WHERE release_sk = %s AND asn = %s
            """,
            lambda index: (release_sk, int(asns[index % len(asns)])),
        ),
        (
            "prefix",
            """
            SELECT prefix_raw, route_raw, declared_domain_count
            FROM info.prefix
            WHERE release_sk = %s AND prefix_raw = %s
            """,
            lambda index: (
                release_sk,
                prefixes[index % len(prefixes)][0],
            ),
        ),
        (
            "as_relation",
            """
            SELECT source_asn, relation_kind, target_asn
            FROM info.as_relation
            WHERE release_sk = %s AND source_asn = %s
            ORDER BY relation_kind, target_asn
            LIMIT 1
            """,
            lambda index: (release_sk, int(asns[index % len(asns)])),
        ),
        (
            "domain_record",
            """
            SELECT domain_key_raw, title, industry
            FROM info.domain_record
            WHERE release_sk = %s AND source_active
              AND domain_key_raw = %s
            """,
            lambda index: (
                release_sk,
                domains[index % len(domains)],
            ),
        ),
    )
    for _name, query, parameters in exact_queries:
        for index in range(8):
            _measure_query(connection, query, parameters(index))
    exact_latencies: list[float] = []
    exact_by_kind: Dict[str, list[float]] = {
        name: [] for name, _query, _parameters in exact_queries
    }
    for index in range(800):
        name, query, parameters = exact_queries[
            index % len(exact_queries)
        ]
        latency = _measure_query(
            connection,
            query,
            parameters(index),
        )
        exact_latencies.append(latency)
        exact_by_kind[name].append(latency)

    lpm_query = """
        SELECT prefix_raw
        FROM info.prefix
        WHERE release_sk = %s
          AND prefix_cidr >>= %s::inet
        ORDER BY masklen(prefix_cidr) DESC
        LIMIT 1
    """
    for index in range(16):
        _measure_query(
            connection,
            lpm_query,
            (release_sk, prefixes[index % len(prefixes)][1]),
        )
    lpm_latencies = [
        _measure_query(
            connection,
            lpm_query,
            (release_sk, prefixes[index % len(prefixes)][1]),
        )
        for index in range(400)
    ]
    return {
        "warmup_exact_query_count": 32,
        "exact_query_sample_count": len(exact_latencies),
        "lpm_warmup_count": 16,
        "lpm_sample_count": len(lpm_latencies),
        "exact_query_p95_ms": _percentile(exact_latencies, 0.95),
        "exact_query_p99_ms": _percentile(exact_latencies, 0.99),
        "exact_query_by_kind": {
            name: {
                "sample_count": len(values),
                "p95_ms": _percentile(values, 0.95),
                "p99_ms": _percentile(values, 0.99),
                "max_ms": round(max(values), 6),
            }
            for name, values in exact_by_kind.items()
        },
        "longest_prefix_match_p95_ms": _percentile(
            lpm_latencies,
            0.95,
        ),
        "exact_query_max_ms": round(max(exact_latencies), 6),
        "lpm_max_ms": round(max(lpm_latencies), 6),
    }


def _docker_fingerprint(names: Sequence[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for name in names:
        completed = subprocess.run(
            ["docker", "inspect", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            result[name] = {"exists": False}
            continue
        value = json.loads(completed.stdout)[0]
        result[name] = {
            "exists": True,
            "id": value.get("Id"),
            "image": value.get("Image"),
            "running": value.get("State", {}).get("Running"),
            "started_at": value.get("State", {}).get("StartedAt"),
            "restart_count": value.get("RestartCount"),
        }
    return result


def _reader_privileges(connection: Any, role: str) -> Dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT current_user,
                   current_setting('transaction_read_only'),
                   current_setting('default_transaction_read_only'),
                   has_schema_privilege(%s, 'info', 'USAGE'),
                   has_table_privilege(
                       %s, 'info.dataset_release', 'SELECT'
                   ),
                   has_table_privilege(
                       %s, 'info.dataset_release', 'UPDATE'
                   ),
                   has_table_privilege(%s, 'info.as_contact', 'SELECT'),
                   has_function_privilege(
                       %s,
                       'info.activate_release(text,bigint,text,text)',
                       'EXECUTE'
                   )
            """,
            (role, role, role, role, role),
        )
        row = cursor.fetchone()
    return {
        "current_user_matches": str(row[0]) == role,
        "transaction_read_only": str(row[1]) == "on",
        "default_transaction_read_only": str(row[2]) == "on",
        "info_schema_usage": bool(row[3]),
        "release_select": bool(row[4]),
        "release_update": bool(row[5]),
        "contact_select": bool(row[6]),
        "activate_execute": bool(row[7]),
    }


def _security_negative_checks(
    connection: Any,
    release_sk: int,
) -> Dict[str, Any]:
    statements = {
        "release_update": (
            "UPDATE info.dataset_release SET status = status "
            "WHERE release_sk = %s",
            (release_sk,),
        ),
        "active_release_insert": (
            "INSERT INTO info.active_release("
            "profile_name,release_sk,activated_at,activated_by,"
            "activation_reason) VALUES "
            "('s4-forbidden',%s,clock_timestamp(),'s4','forbidden')",
            (release_sk,),
        ),
        "quality_insert": (
            "INSERT INTO info.quality_result("
            "release_sk,rule_id,rule_version,blocking,status,"
            "observed,expected) VALUES "
            "(%s,'s4.forbidden',1,true,'pass','{}','{}')",
            (release_sk,),
        ),
        "schema_create": (
            "CREATE TABLE info.s4_forbidden_probe(id integer)",
            (),
        ),
        "activation_call": (
            "SELECT info.activate_release("
            "'default',%s,'s4-forbidden','forbidden')",
            (release_sk,),
        ),
    }
    outcomes: list[Dict[str, Any]] = []
    success_count = 0
    for check_id, (statement, parameters) in statements.items():
        succeeded = False
        sqlstate = None
        with connection.cursor() as cursor:
            try:
                cursor.execute("BEGIN READ ONLY")
                cursor.execute(statement, parameters)
                succeeded = True
            except Exception as exc:  # psycopg2 exposes pgcode dynamically
                sqlstate = getattr(exc, "pgcode", None)
            finally:
                try:
                    cursor.execute("ROLLBACK")
                except Exception:
                    connection.rollback()
        if succeeded:
            success_count += 1
        outcomes.append(
            {
                "check_id": check_id,
                "attempted_in_explicit_read_only_transaction": True,
                "write_succeeded": succeeded,
                "sqlstate": sqlstate,
            }
        )

    contact_read_succeeded = False
    contact_sqlstate = None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM info.as_contact "
                "WHERE release_sk = %s LIMIT 1",
                (release_sk,),
            )
            cursor.fetchone()
            contact_read_succeeded = True
    except Exception as exc:
        contact_sqlstate = getattr(exc, "pgcode", None)
    return {
        "unauthorized_write_success_count": success_count,
        "write_checks": outcomes,
        "unauthorized_contact_read_success_count": int(
            contact_read_succeeded
        ),
        "contact_read_sqlstate": contact_sqlstate,
    }


def _database_state(connection: Any, release_sk: int) -> Dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT release.status, release.activated_at IS NOT NULL,
                   (SELECT count(*) FROM info.active_release),
                   (SELECT count(*) FROM info.source_file
                    WHERE release_sk = %s),
                   (SELECT count(*) FROM info.quality_result
                    WHERE release_sk = %s),
                   (SELECT count(*) FROM info.prefix
                    WHERE release_sk = %s),
                   (SELECT count(*) FROM info.autonomous_system
                    WHERE release_sk = %s)
            FROM info.dataset_release AS release
            WHERE release.release_sk = %s
            """,
            (
                release_sk,
                release_sk,
                release_sk,
                release_sk,
                release_sk,
            ),
        )
        row = cursor.fetchone()
    if row is None:
        raise S4AcceptanceError("候选 release 在安全检查期间消失")
    return {
        "release_status": str(row[0]),
        "release_activated": bool(row[1]),
        "active_release_count": int(row[2]),
        "source_file_count": int(row[3]),
        "quality_result_count": int(row[4]),
        "prefix_count": int(row[5]),
        "asn_count": int(row[6]),
    }


def _docker_psql_json(
    container: str,
    db_admin: str,
    db_name: str,
    query: str,
) -> Mapping[str, Any]:
    completed = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            db_admin,
            "-d",
            db_name,
            "-XAt",
            "-c",
            query,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise S4AcceptanceError(
            f"候选库运维证据查询失败：{completed.stderr[-4000:]}"
        )
    try:
        value = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise S4AcceptanceError("候选库运维证据不是 JSON") from exc
    if not isinstance(value, dict):
        raise S4AcceptanceError("候选库运维证据顶层不是对象")
    return value


def _operations_evidence(
    *,
    container: str,
    db_admin: str,
    db_name: str,
    release_sk: int,
) -> Mapping[str, Any]:
    return _docker_psql_json(
        container,
        db_admin,
        db_name,
        f"""
        SELECT jsonb_build_object(
            'release', (
                SELECT jsonb_build_object(
                    'release_sk', release_sk,
                    'content_id', content_id,
                    'status', status,
                    'activated', activated_at IS NOT NULL,
                    'quality_summary', quality_summary
                )
                FROM info.dataset_release
                WHERE release_sk = {release_sk}
            ),
            'active_release_count', (
                SELECT count(*) FROM info.active_release
            ),
            'files', (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'name', name,
                        'logical_record_count', logical_record_count,
                        'loaded_record_count', loaded_record_count,
                        'quarantined_record_count',
                            quarantined_record_count,
                        'unprocessed_record_count',
                            logical_record_count
                            - loaded_record_count
                            - quarantined_record_count,
                        'load_status', load_status,
                        'loaded_at', loaded_at
                    )
                    ORDER BY name
                )
                FROM info.source_file
                WHERE release_sk = {release_sk}
            ),
            'quality', (
                SELECT jsonb_build_object(
                    'rule_count', count(*),
                    'blocking_failure_count',
                        count(*) FILTER (
                            WHERE blocking AND status <> 'pass'
                        ),
                    'checked_through',
                        max(checked_at)
                )
                FROM info.quality_result
                WHERE release_sk = {release_sk}
            ),
            'import_runs', (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'import_run_sk', import_run_sk,
                        'scope', scope,
                        'status', status,
                        'checkpoint', checkpoint,
                        'error_summary', error_summary,
                        'started_at', started_at,
                        'finished_at', finished_at,
                        'duration_seconds',
                            extract(epoch FROM finished_at - started_at)
                    )
                    ORDER BY import_run_sk
                )
                FROM info.import_run
                WHERE release_sk = {release_sk}
            ),
            'database_size_bytes', pg_database_size(current_database()),
            'info_index_size_bytes', (
                SELECT coalesce(sum(pg_relation_size(indexrelid)), 0)
                FROM pg_index
                JOIN pg_class
                  ON pg_class.oid = indexrelid
                JOIN pg_namespace
                  ON pg_namespace.oid = pg_class.relnamespace
                WHERE pg_namespace.nspname = 'info'
            )
        )
        """,
    )


def _directory_size(path: Path) -> int:
    total = 0
    for entry in path.iterdir():
        if entry.is_symlink():
            raise S4AcceptanceError(f"容量基线目录包含软链接：{entry}")
        if entry.is_file():
            total += entry.stat().st_size
    return total


def _memory_available_bytes() -> Optional[int]:
    path = Path("/proc/meminfo")
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return None


def _capacity_evidence(
    *,
    source_dir: Path,
    operations: Mapping[str, Any],
    file_snapshot: Mapping[str, Any],
    database_snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    usage = os.statvfs(source_dir)
    available = usage.f_bavail * usage.f_frsize
    total = usage.f_blocks * usage.f_frsize
    current_release = int(operations["database_size_bytes"])
    rollback_file = _directory_size(source_dir)
    next_release_build = current_release
    build_temporary = math.ceil(current_release * 0.25)
    runtime_peak = max(
        int(file_snapshot["peak_rss_bytes"]),
        int(database_snapshot["peak_rss_bytes"]),
    )
    required_additional = (
        rollback_file
        + next_release_build
        + build_temporary
        + runtime_peak
    )
    memory_available = _memory_available_bytes()
    memory_pass = (
        memory_available is None
        or memory_available >= runtime_peak * 2
    )
    disk_pass = available >= required_additional
    return {
        "status": "pass" if disk_pass and memory_pass else "fail",
        "filesystem_total_bytes": total,
        "filesystem_available_bytes": available,
        "current_release_bytes": current_release,
        "previous_file_rollback_bytes": rollback_file,
        "next_release_build_bytes": next_release_build,
        "build_temporary_bytes": build_temporary,
        "runtime_peak_bytes": runtime_peak,
        "required_additional_bytes": required_additional,
        "disk_headroom_bytes": available - required_additional,
        "memory_available_bytes": memory_available,
        "disk_pass": disk_pass,
        "memory_pass": memory_pass,
        "formula": (
            "previous_file_rollback + next_release_build + "
            "25_percent_build_temporary + runtime_peak"
        ),
    }


def _ensure_safe_report(value: Mapping[str, Any]) -> None:
    serialized = _canonical_json(value)
    for sensitive in _SENSITIVE_OUTPUT_KEYS:
        if f'"{sensitive}":' in serialized:
            raise S4AcceptanceError("S4 证据意外包含联系人字段")


def run_s4_acceptance(
    *,
    source_dir: Path,
    manifest: Mapping[str, Any],
    core_backend_root: Path,
    db_host: str,
    db_port: int,
    db_name: str,
    db_user: str,
    db_admin: str,
    container: str,
    production_containers: Sequence[str],
) -> Dict[str, Mapping[str, Any]]:
    validate_manifest(manifest)
    _verify_all_sources(source_dir, manifest)
    core_before = _core_hash_state(core_backend_root)
    os.environ["INFO_DIR"] = str(source_dir)

    connection = _connect_reader(
        host=db_host,
        port=db_port,
        db_name=db_name,
        db_user=db_user,
    )
    try:
        repository = PostgresStaticInfoRepository(
            connection,
            content_id=str(manifest["content_id"]),
            manifest_sha256=str(manifest["manifest_sha256"]),
            allow_statuses=("validating",),
        )
        database_runtime = DatabaseStaticInfo(repository)
        file_snapshot = _run_snapshot_probe(
            backend="file",
            core_backend_root=core_backend_root,
            source_dir=source_dir,
            host=db_host,
            port=db_port,
            db_name=db_name,
            db_user=db_user,
            content_id=str(manifest["content_id"]),
            manifest_sha256=str(manifest["manifest_sha256"]),
        )
        database_snapshot = _run_snapshot_probe(
            backend="database",
            core_backend_root=core_backend_root,
            source_dir=source_dir,
            host=db_host,
            port=db_port,
            db_name=db_name,
            db_user=db_user,
            content_id=str(manifest["content_id"]),
            manifest_sha256=str(manifest["manifest_sha256"]),
        )

        core_classes = _load_core_classes(core_backend_root)
        file_info = _load_file_info(core_backend_root)
        fixture = _select_detector_fixture(
            repository,
            file_info,
            core_classes,
        )
        projected_file = _project_info(file_info, fixture)
        projected_database = _project_info(database_runtime, fixture)
        file_result_first = _evaluate_core_cases(
            projected_file,
            fixture,
            core_classes,
        )
        database_result_first = _evaluate_core_cases(
            projected_database,
            fixture,
            core_classes,
        )
        file_result_second = _evaluate_core_cases(
            projected_file,
            fixture,
            core_classes,
        )
        database_result_second = _evaluate_core_cases(
            projected_database,
            fixture,
            core_classes,
        )
        differences = _result_differences(
            file_result_first,
            database_result_first,
        )
        reproducible = (
            file_result_first == file_result_second
            and database_result_first == database_result_second
        )
        all_positive = all(
            int(value["event_count"]) > 0
            for value in file_result_first.values()
        )
        throughput = _benchmark_detector_throughput(
            projected_file,
            projected_database,
            fixture,
            core_classes,
        )
        query_metrics = _benchmark_database_queries(
            connection,
            repository.release_sk,
        )

        production_before = _docker_fingerprint(production_containers)
        database_before = _database_state(
            connection,
            repository.release_sk,
        )
        privileges = _reader_privileges(connection, db_user)
        negative = _security_negative_checks(
            connection,
            repository.release_sk,
        )
        database_after = _database_state(
            connection,
            repository.release_sk,
        )
        production_after = _docker_fingerprint(production_containers)
        operations = _operations_evidence(
            container=container,
            db_admin=db_admin,
            db_name=db_name,
            release_sk=repository.release_sk,
        )
        capacity = _capacity_evidence(
            source_dir=source_dir,
            operations=operations,
            file_snapshot=file_snapshot,
            database_snapshot=database_snapshot,
        )

        core_after = _core_hash_state(core_backend_root)
        core_unchanged = core_before == core_after
        detector_status = (
            "pass"
            if not differences
            and all_positive
            and reproducible
            and core_unchanged
            else "fail"
        )
        detector_report: Dict[str, Any] = {
            "schema_version": 1,
            "component": "static_info_detector_ab",
            "content_id": manifest["content_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "release_sk": repository.release_sk,
            "status": detector_status,
            "scope": "six_core_event_decision_boundaries",
            "event_type_count": len(_EVENT_TYPES),
            "event_types": list(_EVENT_TYPES),
            "fixed_input": fixture,
            "file_backend_results": file_result_first,
            "database_backend_results": database_result_first,
            "file_result_sha256": _sha256_value(file_result_first),
            "database_result_sha256": _sha256_value(
                database_result_first
            ),
            "same_input_reproducible": reproducible,
            "all_event_types_positive": all_positive,
            "unapproved_difference_count": len(differences),
            "unapproved_differences": differences,
            "core_hash_unchanged": core_unchanged,
            "core_before": core_before,
            "core_after": core_after,
            "contact_fields_in_evidence": False,
        }

        snapshot_time_regression = _regression_percent(
            float(database_snapshot["load_time_seconds"]),
            float(file_snapshot["load_time_seconds"]),
        )
        snapshot_rss_regression = _regression_percent(
            float(database_snapshot["peak_rss_bytes"]),
            float(file_snapshot["peak_rss_bytes"]),
        )
        throughput_regression = float(throughput["regression_percent"])
        performance_status = (
            "pass"
            if query_metrics["exact_query_p95_ms"] <= 20
            and query_metrics["exact_query_p99_ms"] <= 50
            and query_metrics["longest_prefix_match_p95_ms"] <= 30
            and snapshot_time_regression <= 10
            and snapshot_rss_regression <= 10
            and throughput_regression <= 5
            and database_runtime.request_path_full_table_load_count == 0
            and capacity["status"] == "pass"
            else "fail"
        )
        performance_report: Dict[str, Any] = {
            "schema_version": 1,
            "component": "static_info_performance",
            "content_id": manifest["content_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "release_sk": repository.release_sk,
            "status": performance_status,
            **{
                key: query_metrics[key]
                for key in (
                    "exact_query_p95_ms",
                    "exact_query_p99_ms",
                    "longest_prefix_match_p95_ms",
                )
            },
            "snapshot_load_time_regression_percent": (
                snapshot_time_regression
            ),
            "snapshot_peak_rss_regression_percent": (
                snapshot_rss_regression
            ),
            "detector_throughput_regression_percent": (
                throughput_regression
            ),
            "request_path_full_table_load_count": (
                database_runtime.request_path_full_table_load_count
            ),
            "capacity_status": capacity["status"],
            "query_benchmark": query_metrics,
            "file_snapshot_baseline": file_snapshot,
            "database_snapshot_candidate": database_snapshot,
            "detector_throughput": throughput,
            "capacity": capacity,
            "thresholds": {
                "exact_query_p95_ms": 20,
                "exact_query_p99_ms": 50,
                "longest_prefix_match_p95_ms": 30,
                "snapshot_regression_percent": 10,
                "detector_throughput_regression_percent": 5,
                "request_path_full_table_load_count": 0,
            },
        }

        production_unchanged = production_before == production_after
        database_unchanged = database_before == database_after
        runtime_read_only = all(
            (
                privileges["current_user_matches"],
                privileges["transaction_read_only"],
                privileges["default_transaction_read_only"],
                privileges["info_schema_usage"],
                privileges["release_select"],
                not privileges["release_update"],
                not privileges["contact_select"],
                not privileges["activate_execute"],
            )
        )
        contact_exposure_count = int(
            negative["unauthorized_contact_read_success_count"]
        )
        side_effect_count = int(not production_unchanged) + int(
            not database_unchanged
        )
        security_status = (
            "pass"
            if negative["unauthorized_write_success_count"] == 0
            and contact_exposure_count == 0
            and side_effect_count == 0
            and runtime_read_only
            else "fail"
        )
        security_report: Dict[str, Any] = {
            "schema_version": 1,
            "component": "static_info_security",
            "content_id": manifest["content_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "release_sk": repository.release_sk,
            "status": security_status,
            "runtime_role": db_user,
            "runtime_role_read_only": runtime_read_only,
            "runtime_privileges": privileges,
            "unauthorized_write_success_count": negative[
                "unauthorized_write_success_count"
            ],
            "unauthorized_contact_read_success_count": negative[
                "unauthorized_contact_read_success_count"
            ],
            "contact_plaintext_exposure_count": contact_exposure_count,
            "check_production_side_effect_count": side_effect_count,
            "negative_checks": negative["write_checks"],
            "contact_read_sqlstate": negative[
                "contact_read_sqlstate"
            ],
            "production_boundary": {
                "before_sha256": _sha256_value(production_before),
                "after_sha256": _sha256_value(production_after),
                "unchanged": production_unchanged,
                "containers": list(production_containers),
            },
            "candidate_boundary": {
                "before": database_before,
                "after": database_after,
                "unchanged": database_unchanged,
            },
        }

        files = operations.get("files")
        import_runs = operations.get("import_runs")
        per_file_observable = (
            isinstance(files, list)
            and len(files) == 24
            and all(
                isinstance(item, dict)
                and item.get("unprocessed_record_count") == 0
                for item in files
            )
        )
        checkpoint_resumable = (
            isinstance(import_runs, list)
            and any(
                isinstance(item, dict)
                and item.get("scope") == "all_24_files"
                and item.get("status") == "completed"
                and isinstance(item.get("checkpoint"), dict)
                and len(item["checkpoint"].get("loaded_files", [])) == 24
                for item in import_runs
            )
        )
        release_value = operations.get("release")
        release_observable = (
            isinstance(release_value, dict)
            and release_value.get("content_id") == manifest["content_id"]
            and release_value.get("status") == "validating"
            and operations.get("active_release_count") == 0
        )
        activated = not (
            isinstance(release_value, dict)
            and release_value.get("activated") is False
            and operations.get("active_release_count") == 0
        )
        operations_status = (
            "pass"
            if release_observable
            and per_file_observable
            and checkpoint_resumable
            and reproducible
            and not activated
            else "fail"
        )
        operations_report: Dict[str, Any] = {
            "schema_version": 1,
            "component": "static_info_operations",
            "content_id": manifest["content_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "release_sk": repository.release_sk,
            "status": operations_status,
            "release_state_observable": release_observable,
            "per_file_counts_observable": per_file_observable,
            "checkpoint_resumable": checkpoint_resumable,
            "same_input_reproducible": reproducible,
            "activated": activated,
            "release": release_value,
            "active_release_count": operations.get(
                "active_release_count"
            ),
            "files": files,
            "quality": operations.get("quality"),
            "import_runs": import_runs,
            "database_size_bytes": operations.get(
                "database_size_bytes"
            ),
            "info_index_size_bytes": operations.get(
                "info_index_size_bytes"
            ),
            "capacity_status": capacity["status"],
            "fixture_sha256": fixture["fixture_sha256"],
            "file_result_sha256": _sha256_value(file_result_first),
            "database_result_sha256": _sha256_value(
                database_result_first
            ),
        }
        reports = {
            "static-info-detector-ab.json": detector_report,
            "static-info-performance.json": performance_report,
            "static-info-security.json": security_report,
            "static-info-operations.json": operations_report,
        }
        for value in reports.values():
            _ensure_safe_report(value)
        return reports
    finally:
        connection.close()


def _read_json(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise S4AcceptanceError(f"JSON 文件缺失或为软链接：{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise S4AcceptanceError(f"JSON 顶层不是对象：{path}")
    return value


def _write_reports(
    reports: Mapping[str, Mapping[str, Any]],
    output_dir: Path,
) -> None:
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise S4AcceptanceError(f"S4 输出目录无效：{output_dir}")
    for name, value in reports.items():
        write_text_exclusive(
            output_dir / name,
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.info_pipeline.s4",
        description="static INFO S4 真实验收",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe = subparsers.add_parser("snapshot-probe")
    probe.add_argument("--backend", choices=("file", "database"), required=True)
    acceptance = subparsers.add_parser("acceptance")
    for target in (probe, acceptance):
        target.add_argument("--core-backend-root", required=True)
        target.add_argument("--source-dir", required=True)
        target.add_argument("--db-host", required=True)
        target.add_argument("--db-port", type=int, default=5432)
        target.add_argument("--db-name", required=True)
        target.add_argument("--db-user", required=True)
        target.add_argument("--content-id", required=True)
        target.add_argument("--manifest-sha256", required=True)
    acceptance.add_argument("--manifest", required=True)
    acceptance.add_argument("--db-admin", required=True)
    acceptance.add_argument("--container", required=True)
    acceptance.add_argument(
        "--production-container",
        action="append",
        default=[],
    )
    acceptance.add_argument("--output-dir", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "snapshot-probe":
            value = _snapshot_probe(
                backend=args.backend,
                core_backend_root=Path(args.core_backend_root),
                source_dir=Path(args.source_dir),
                host=args.db_host,
                port=args.db_port,
                db_name=args.db_name,
                db_user=args.db_user,
                content_id=args.content_id,
                manifest_sha256=args.manifest_sha256,
            )
            sys.stdout.write(_canonical_json(value) + "\n")
            return 0
        if args.command == "acceptance":
            manifest = _read_json(Path(args.manifest))
            if (
                manifest.get("content_id") != args.content_id
                or manifest.get("manifest_sha256")
                != args.manifest_sha256
            ):
                raise S4AcceptanceError(
                    "命令行 content 身份与 manifest 不一致"
                )
            reports = run_s4_acceptance(
                source_dir=Path(args.source_dir),
                manifest=manifest,
                core_backend_root=Path(args.core_backend_root),
                db_host=args.db_host,
                db_port=args.db_port,
                db_name=args.db_name,
                db_user=args.db_user,
                db_admin=args.db_admin,
                container=args.container,
                production_containers=args.production_container,
            )
            _write_reports(reports, Path(args.output_dir))
            status = (
                "pass"
                if all(value["status"] == "pass" for value in reports.values())
                else "fail"
            )
            sys.stdout.write(
                _canonical_json(
                    {
                        "status": status,
                        "content_id": args.content_id,
                        "artifacts": sorted(reports),
                    }
                )
                + "\n"
            )
            return 0 if status == "pass" else 1
        raise AssertionError(args.command)
    except (
        OSError,
        ValueError,
        S4AcceptanceError,
        subprocess.SubprocessError,
    ) as exc:
        sys.stderr.write(f"错误：{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
