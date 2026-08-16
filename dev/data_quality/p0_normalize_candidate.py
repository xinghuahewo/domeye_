#!/usr/bin/env python3
"""从固定二三月只读数据库生成 P0 D2 规范化候选制品。

该 runner 不修改历史数据库，也不改写检测核心。它先在 PostgreSQL 内完成
总表/事实表的碰撞、歧义和反向孤儿识别，再使用服务端游标流式生成旁路
JSONL。历史路径字段只读取“是否存在、是否为空、物理字节量”元数据，绝不
把 117 万行的大路径 payload 复制进候选制品。

所有 PostgreSQL 查询都位于同一个 ``REPEATABLE READ READ ONLY`` 事务中，
无论成功或失败最终都执行 rollback。输出先写入同级临时目录，完整生成
SHA256SUMS 后才改名为目标目录。
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence

# 允许以 ``python dev/data_quality/p0_normalize_candidate.py`` 从任意 cwd
# 执行；服务器 staging bundle 保持相同目录布局。
_BUNDLE_ROOT = Path(__file__).resolve().parents[2]
if str(_BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BUNDLE_ROOT))

from dev.data_quality.p0_probe import (
    EVENT_TYPE_LABELS,
    LOOPBACK_HOST,
    PROFILE_RAW_KEYS,
    ProbeError,
    _assert_no_symlink_ancestors,
    _begin_readonly_transaction,
    _canonical_sha256,
    _connect_database,
    _git_provenance,
    _load_project_data_profile,
    _quote_identifier,
    _read_database_env,
    _sha256,
    _validate_release_context,
    _verify_reader_security,
)


CANDIDATE_PATH = Path(__file__).absolute()
UTC = timezone.utc
MONTH_RE = re.compile(r"^[0-9]{6}$")
EVENT_TYPES = (
    "hijack",
    "sub_hijack",
    "leak",
    "prefix_outage",
    "as_outage",
    "country_outage",
)

# 顺序同时决定源扫描顺序和 JSONL 的稳定排序。
FACT_SPECS: dict[str, dict[str, Any]] = {
    "hijack": {
        "family": "hijack",
        "problem": "prefix",
        "event_id": "hijack_eventid",
        "primary_key": ("source", "prefix", "hijack_eventid"),
        "fields": (
            "source", "prefix", "hijack_eventid", "hijacked_as", "hijacker_as",
            "s_time", "e_time", "duration", "hijack_level", "event_info",
        ),
        "phases": {
            "before": ("pre_vp_paths", "jsonb"),
            "during": ("eve_vp_paths", "jsonb"),
            "after": ("next_vp_paths", "jsonb"),
        },
    },
    "sub_hijack": {
        "family": "sub_hijack",
        "problem": "prefix",
        "event_id": "sub_hijack_eventid",
        "primary_key": ("source", "prefix", "sub_hijack_eventid"),
        "fields": (
            "source", "prefix", "sub_hijack_eventid", "hijacked_prefix",
            "hijacked_as", "hijacker_as", "s_time", "e_time", "duration",
            "sub_hijack_level", "event_info",
        ),
        "phases": {},
    },
    "leak": {
        "family": "leak_event",
        "problem": "prefix",
        "event_id": "leak_event_id",
        "primary_key": ("source", "prefix", "leak_event_id"),
        "fields": (
            "source", "prefix", "leak_event_id", "s_time", "leak_by", "leak_to",
            "prefix_ori_as", "leak_vp", "leak_level", "event_info",
        ),
        "phases": {"during": ("as_path", "text")},
    },
    "prefix_outage": {
        "family": "prefix_outage",
        "problem": "prefix",
        "event_id": "outage_id",
        "primary_key": ("source", "prefix", "outage_id", "asn"),
        "fields": (
            "source", "prefix", "outage_id", "asn", "s_time", "e_time",
            "duration", "outage_level", "country", "event_info",
        ),
        "phases": {
            "before": ("pre_vp_paths", "jsonb"),
            "during": ("eve_vp_paths", "jsonb"),
            "after": ("next_vp_paths", "jsonb"),
        },
    },
    "as_outage": {
        "family": "as_outage",
        "problem": "asn",
        "event_id": "outage_id",
        "primary_key": ("source", "asn", "outage_id"),
        "fields": (
            "source", "asn", "outage_id", "s_time", "e_time", "duration",
            "outage_level", "country", "outage_prefixes", "total_prefix_num",
            "max_outage_prefix_num", "event_info",
        ),
        "phases": {
            "before": ("pre_vp_paths", "jsonb"),
            "during": ("eve_vp_paths", "jsonb"),
            "after": ("next_vp_paths", "jsonb"),
        },
    },
    "country_outage": {
        "family": "country_outage",
        "problem": "country",
        "event_id": "outage_id",
        "primary_key": ("source", "country", "outage_id"),
        "fields": (
            "source", "country", "outage_id", "s_time", "e_time", "duration",
            "outage_level", "outage_ases", "total_as_num", "max_outage_as_num",
            "event_info",
        ),
        "phases": {},
    },
}

EVENT_FIELDS = ("source", "event_type", "level", "s_time", "e_time", "duration", "detail_url")
JSONL_FILES = (
    "incidents.jsonl.gz",
    "links.jsonl.gz",
    "collision_groups.jsonl.gz",
    "quarantine.jsonl.gz",
)


class CandidateError(ProbeError):
    """候选输入、安全边界或对账结果不符合约定。"""


def _canonical_line(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            _utc_json_ready(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _utc_text(value: datetime) -> str:
    """naive 历史时间按固定数据档的 Asia/Shanghai 业务时间解释。"""

    if value.tzinfo is None:
        # 固定数据档已冻结为 +08:00；不从宿主机本地时区猜测。
        value = value.replace(tzinfo=timezone(timedelta(hours=8)))
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _utc_json_ready(value: Any) -> Any:
    """确定性 JSON 转换；时间一律输出 UTC Z，缺失保持 None。"""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, datetime):
        return _utc_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, timedelta):
        seconds = value.total_seconds()
        return int(seconds) if float(seconds).is_integer() else seconds
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CandidateError("候选记录包含非有限浮点数")
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _utc_json_ready(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_utc_json_ready(item) for item in value]
    return str(value)


class DeterministicGzipJsonlWriter:
    """mtime=0、空文件名头的确定性 gzip JSONL writer。"""

    def __init__(self, path: Path):
        self.path = path
        self.row_count = 0
        self._content_hash = hashlib.sha256()
        self._raw = path.open("xb")
        self._gzip = gzip.GzipFile(
            filename="", mode="wb", fileobj=self._raw, compresslevel=9, mtime=0
        )

    def write(self, value: Mapping[str, Any]) -> None:
        line = _canonical_line(value)
        self._gzip.write(line)
        self._content_hash.update(line)
        self.row_count += 1

    def close(self) -> None:
        if self._gzip is not None:
            self._gzip.close()
            self._gzip = None
        if self._raw is not None:
            self._raw.flush()
            os.fsync(self._raw.fileno())
            self._raw.close()
            self._raw = None

    def __enter__(self) -> "DeterministicGzipJsonlWriter":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    @property
    def content_sha256(self) -> str:
        return self._content_hash.hexdigest()

    def inventory(self) -> dict[str, Any]:
        if self._gzip is not None or self._raw is not None:
            raise CandidateError("必须先关闭 JSONL writer 再生成 inventory")
        return {
            "name": self.path.name,
            "media_type": "application/x-ndjson+gzip",
            "compression": {"algorithm": "gzip", "level": 9, "mtime": 0, "header_filename": ""},
            "order": "runner_defined_deterministic_order_v1",
            "row_count": self.row_count,
            "content_sha256": self.content_sha256,
            "sha256": _sha256(self.path),
            "size_bytes": self.path.stat().st_size,
        }


def _json_object(alias: str, fields: Sequence[str]) -> str:
    arguments: list[str] = []
    for field in fields:
        arguments.extend(("'{}'".format(field), "{}.{}".format(alias, _quote_identifier(field))))
    return "jsonb_build_object({})".format(", ".join(arguments))


def _problem_expression(alias: str, spec: Mapping[str, Any]) -> str:
    column = "{}.{}".format(alias, _quote_identifier(str(spec["problem"])))
    if spec["problem"] == "prefix":
        return "replace({}::text, '/', '-')".format(column)
    return "{}::text".format(column)


def _native_key_expression(alias: str, spec: Mapping[str, Any]) -> str:
    arguments: list[str] = []
    for field in spec["primary_key"]:
        arguments.extend(("'{}'".format(field), "{}.{}".format(alias, _quote_identifier(field))))
    return "jsonb_build_object({})".format(", ".join(arguments))


def _phase_metadata_expression(alias: str, spec: Mapping[str, Any]) -> str:
    phases: list[str] = []
    for phase, (field, kind) in spec["phases"].items():
        column = "{}.{}".format(alias, _quote_identifier(field))
        if kind == "jsonb":
            empty = "({0} = '[]'::jsonb OR {0} = '{{}}'::jsonb)".format(column)
            size = "pg_column_size({})".format(column)
        else:
            empty = "({0} = '')".format(column)
            size = "octet_length({})".format(column)
        phases.extend(
            (
                "'{}'".format(phase),
                "jsonb_build_object('source_field','{field}','retained',({column} IS NOT NULL),"
                "'empty',CASE WHEN {column} IS NULL THEN NULL ELSE {empty} END,"
                "'stored_bytes',CASE WHEN {column} IS NULL THEN NULL ELSE {size} END)".format(
                    field=field, column=column, empty=empty, size=size
                ),
            )
        )
    return "jsonb_build_object({})".format(", ".join(phases)) if phases else "'{}'::jsonb"


def _refs_cte(months: Sequence[str]) -> str:
    parts = []
    for month in months:
        if not MONTH_RE.fullmatch(month):
            raise CandidateError("非法月份：{}".format(month))
        table = "{}.{}".format(
            _quote_identifier("public"), _quote_identifier("event_table_{}".format(month))
        )
        parts.append(
            "SELECT '{month}'::text AS physical_month, detail_url::text, source::text AS event_source, "
            "event_type::text AS declared_event_type, s_time, level::text FROM {table}".format(
                month=month, table=table
            )
        )
    return "\nUNION ALL\n".join(parts)


def _fact_table(month: str, spec: Mapping[str, Any]) -> str:
    return "{}.{}".format(
        _quote_identifier("public"),
        _quote_identifier("{}_{}".format(spec["family"], month)),
    )


def _collision_query(month: str, event_type: str, months: Sequence[str]) -> str:
    spec = FACT_SPECS[event_type]
    fact_table = _fact_table(month, spec)
    fact_problem = _problem_expression("fact", spec)
    native_key = _native_key_expression("fact", spec)
    return """
        WITH all_events AS MATERIALIZED ({refs}),
        refs AS MATERIALIZED (
            SELECT detail_url,
                   split_part(detail_url, '/', 3) AS problem,
                   split_part(detail_url, '/', 4) AS event_id,
                   split_part(detail_url, '/', 5) AS source
            FROM all_events
            WHERE split_part(detail_url, '/', 1) = %s
              AND replace(substr(split_part(detail_url, '/', 2), 1, 7), '-', '') = %s
              AND cardinality(string_to_array(detail_url, '/')) = 5
              AND split_part(detail_url, '/', 4) ~ '^[0-9]+$'
        ),
        grouped_refs AS MATERIALIZED (
            SELECT problem, event_id, source,
                   array_agg(DISTINCT detail_url ORDER BY detail_url) AS detail_urls
            FROM refs
            GROUP BY problem, event_id, source
            HAVING count(DISTINCT detail_url) > 1
        ),
        facts AS MATERIALIZED (
            SELECT {problem} AS problem, fact.{event_id}::text AS event_id,
                   fact.source::text AS source, {native_key} AS native_key
            FROM {fact_table} AS fact
        ),
        unique_facts AS (
            SELECT problem, event_id, source, min(native_key::text)::jsonb AS native_key
            FROM facts
            GROUP BY problem, event_id, source
            HAVING count(*) = 1
        )
        SELECT grouped_refs.problem, grouped_refs.event_id, grouped_refs.source,
               unique_facts.native_key, grouped_refs.detail_urls
        FROM grouped_refs
        JOIN unique_facts USING (problem, event_id, source)
        ORDER BY grouped_refs.problem, grouped_refs.event_id, grouped_refs.source
    """.format(
        refs=_refs_cte(months),
        problem=fact_problem,
        event_id=_quote_identifier(spec["event_id"]),
        native_key=native_key,
        fact_table=fact_table,
    )


def _ambiguity_query(month: str, event_type: str) -> str:
    spec = FACT_SPECS[event_type]
    fact_table = _fact_table(month, spec)
    return """
        SELECT {problem} AS problem, fact.{event_id}::text AS event_id,
               fact.source::text AS source,
               jsonb_agg({native_key} ORDER BY {native_key}::text) AS native_keys
        FROM {fact_table} AS fact
        GROUP BY {problem}, fact.{event_id}::text, fact.source::text
        HAVING count(*) > 1
        ORDER BY {problem}, fact.{event_id}::text, fact.source::text
    """.format(
        problem=_problem_expression("fact", spec),
        event_id=_quote_identifier(spec["event_id"]),
        native_key=_native_key_expression("fact", spec),
        fact_table=fact_table,
    )


def _orphan_query(month: str, event_type: str, months: Sequence[str]) -> str:
    spec = FACT_SPECS[event_type]
    fact_table = _fact_table(month, spec)
    fact_json = _json_object("fact", spec["fields"])
    return """
        WITH all_events AS MATERIALIZED ({refs}),
        refs AS MATERIALIZED (
            SELECT split_part(detail_url, '/', 3) AS problem,
                   split_part(detail_url, '/', 4) AS event_id,
                   split_part(detail_url, '/', 5) AS source
            FROM all_events
            WHERE split_part(detail_url, '/', 1) = %s
              AND replace(substr(split_part(detail_url, '/', 2), 1, 7), '-', '') = %s
              AND cardinality(string_to_array(detail_url, '/')) = 5
              AND split_part(detail_url, '/', 4) ~ '^[0-9]+$'
        )
        SELECT {fact_json} || jsonb_build_object('source_table', %s) AS fact_row,
               {phase_metadata} AS phase_metadata
        FROM {fact_table} AS fact
        WHERE NOT EXISTS (
            SELECT 1 FROM refs
            WHERE refs.problem = {problem}
              AND refs.event_id = fact.{event_id}::text
              AND refs.source = fact.source::text
        )
        ORDER BY {primary_key}
    """.format(
        refs=_refs_cte(months),
        fact_json=fact_json,
        phase_metadata=_phase_metadata_expression("fact", spec),
        fact_table=fact_table,
        problem=_problem_expression("fact", spec),
        event_id=_quote_identifier(spec["event_id"]),
        primary_key=", ".join("fact.{}".format(_quote_identifier(field)) for field in spec["primary_key"]),
    )


def _event_join_query(month: str, event_type: str) -> str:
    spec = FACT_SPECS[event_type]
    event_table = "{}.{}".format(
        _quote_identifier("public"), _quote_identifier("event_table_{}".format(month))
    )
    fact_table = _fact_table(month, spec)
    fact_json = _json_object("fact", spec["fields"])
    event_json = _json_object("event", EVENT_FIELDS)
    return """
        SELECT {event_json} || jsonb_build_object('source_table', %s) AS event_row,
               CASE WHEN fact.{pk_first} IS NULL THEN NULL
                    ELSE {fact_json} || jsonb_build_object('source_table', %s) END AS fact_row,
               CASE WHEN fact.{pk_first} IS NULL THEN '{{}}'::jsonb
                    ELSE {phase_metadata} END AS phase_metadata
        FROM {event_table} AS event
        LEFT JOIN {fact_table} AS fact
          ON {problem} = split_part(event.detail_url, '/', 3)
         AND fact.{event_id}::text = split_part(event.detail_url, '/', 4)
         AND fact.source::text = split_part(event.detail_url, '/', 5)
        WHERE split_part(event.detail_url, '/', 1) = %s
        ORDER BY event.detail_url, {primary_key}
    """.format(
        event_json=event_json,
        pk_first=_quote_identifier(spec["primary_key"][0]),
        fact_json=fact_json,
        phase_metadata=_phase_metadata_expression("fact", spec),
        event_table=event_table,
        fact_table=fact_table,
        problem=_problem_expression("fact", spec),
        event_id=_quote_identifier(spec["event_id"]),
        primary_key=", ".join("fact.{} NULLS FIRST".format(_quote_identifier(field)) for field in spec["primary_key"]),
    )


def _malformed_event_query(month: str) -> str:
    event_table = "{}.{}".format(
        _quote_identifier("public"), _quote_identifier("event_table_{}".format(month))
    )
    accepted = ", ".join("'{}'".format(item) for item in EVENT_TYPES)
    declared = " OR ".join(
        "(split_part(event.detail_url, '/', 1) = '{kind}' AND event.event_type = '{label}')".format(
            kind=kind, label=EVENT_TYPE_LABELS[kind]
        )
        for kind in EVENT_TYPES
    )
    return """
        SELECT {event_json} || jsonb_build_object('source_table', %s) AS event_row
        FROM {event_table} AS event
        WHERE NOT (
            cardinality(string_to_array(event.detail_url, '/')) = 5
            AND split_part(event.detail_url, '/', 1) IN ({accepted})
            AND split_part(event.detail_url, '/', 2)
                ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}} [0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}$'
            AND split_part(event.detail_url, '/', 3) <> ''
            AND split_part(event.detail_url, '/', 4) ~ '^[0-9]+$'
            AND split_part(event.detail_url, '/', 5) <> ''
            AND split_part(event.detail_url, '/', 5) = event.source::text
            AND ({declared})
            AND split_part(event.detail_url, '/', 2) = to_char(event.s_time, 'YYYY-MM-DD HH24:MI:SS')
            AND replace(substr(split_part(event.detail_url, '/', 2), 1, 7), '-', '') = %s
        )
        ORDER BY event.detail_url
    """.format(event_json=_json_object("event", EVENT_FIELDS), event_table=event_table, accepted=accepted, declared=declared)


def _duplicate_event_query(months: Sequence[str]) -> str:
    return """
        WITH all_events AS MATERIALIZED ({refs})
        SELECT detail_url, array_agg(physical_month ORDER BY physical_month) AS physical_months
        FROM all_events
        GROUP BY detail_url
        HAVING count(*) > 1
        ORDER BY detail_url
    """.format(refs=_refs_cte(months))


def _count_query(table_names: Sequence[str]) -> str:
    parts = []
    for name in table_names:
        table = "{}.{}".format(_quote_identifier("public"), _quote_identifier(name))
        parts.append("SELECT '{}'::text AS table_name, count(*)::bigint AS row_count FROM {}".format(name, table))
    return "\nUNION ALL\n".join(parts) + "\nORDER BY table_name"


def _stream_cursor(connection: Any, name: str, *, fetch_size: int = 1000) -> Any:
    try:
        cursor = connection.cursor(name=name, withhold=False)
    except TypeError:
        cursor = connection.cursor()
    try:
        cursor.itersize = fetch_size
    except Exception:
        pass
    return cursor


def _fetchmany(cursor: Any, size: int = 1000) -> Iterator[tuple[Any, ...]]:
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            break
        for row in rows:
            yield tuple(row)


def _link_key(source_table: str, problem: str, event_id: str, source: str) -> str:
    return json.dumps(
        [source_table, str(problem), str(event_id), str(source)],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _load_normalizer(pipeline_root: Path) -> Any:
    """从显式 bundle 根加载公共 API，不要求修改只读 project checkout。"""

    supplied_root = pipeline_root.absolute()
    if supplied_root.is_symlink():
        raise CandidateError("pipeline bundle 根禁止软链接：{}".format(supplied_root))
    try:
        pipeline_root = supplied_root.resolve(strict=True)
    except OSError as error:
        raise CandidateError("无法解析 pipeline bundle 根：{}".format(pipeline_root)) from error
    if not pipeline_root.is_dir():
        raise CandidateError("pipeline bundle 根必须是目录且禁止软链接：{}".format(pipeline_root))
    expected_root = pipeline_root / "backend" / "data_pipeline" / "normalize"
    init_path = expected_root / "__init__.py"
    facts_path = expected_root / "facts.py"
    for path in (init_path, facts_path):
        if path.is_symlink() or not path.is_file():
            raise CandidateError("pipeline bundle 缺少普通文件或包含软链接：{}".format(path))
    # 使用独立包名加载，使只读 checkout 中已导入的 backend 模块不能遮蔽
    # staging bundle。submodule_search_locations 让 ``.facts`` 相对导入成立。
    package_name = "_domeye_p0_normalize_{}".format(
        hashlib.sha256(str(expected_root).encode("utf-8")).hexdigest()[:16]
    )
    spec = importlib.util.spec_from_file_location(
        package_name,
        init_path,
        submodule_search_locations=[str(expected_root)],
    )
    if spec is None or spec.loader is None:
        raise CandidateError("无法创建 pipeline 规范化模块加载器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(package_name, None)
        raise
    module_path = Path(module.__file__).resolve(strict=True)
    expected_root = expected_root.resolve(strict=True)
    if expected_root not in module_path.parents:
        raise CandidateError("规范化公共 API 不是来自 --pipeline-root：{}".format(module_path))
    required = (
        "parse_detail_url", "incident_id_v1", "fact_source_primary_key",
        "normalize_event", "normalize_event_facts", "build_collision_group",
        "build_quarantine_record", "canonical_json",
    )
    missing = [name for name in required if not callable(getattr(module, name, None))]
    if missing:
        raise CandidateError("规范化公共 API 缺少：{}".format(", ".join(missing)))
    return module


def _normalizer_source_hashes(pipeline_root: Path) -> dict[str, str]:
    """只接受并哈希冻结的两个 normalizer 源文件。

    macOS tar 可能生成同样以 ``.py`` 结尾的 AppleDouble ``._*`` 文件；若
    用通配符纳入 provenance，会让同一业务代码因传输元数据产生不同候选
    指纹。这里拒绝任何额外 Python 源，防止环境垃圾被静默纳入或执行边界
    漂移。
    """

    root = pipeline_root.absolute()
    directory = root / "backend" / "data_pipeline" / "normalize"
    expected = (directory / "__init__.py", directory / "facts.py")
    actual = tuple(sorted(directory.glob("*.py")))
    if actual != tuple(sorted(expected)):
        unexpected = sorted(path.name for path in set(actual) - set(expected))
        missing = sorted(path.name for path in set(expected) - set(actual))
        raise CandidateError(
            "normalizer 源文件集合不等于冻结清单；额外={}，缺少={}".format(
                unexpected, missing
            )
        )
    return {
        str(path.relative_to(root)): _sha256(path)
        for path in sorted(expected)
    }


def _prepare_staging(output_dir: Path) -> tuple[Path, Path]:
    target = output_dir.absolute()
    _assert_no_symlink_ancestors(target)
    if target.exists() or target.is_symlink():
        raise CandidateError("输出目录必须新建，拒绝已有路径：{}".format(target))
    parent = target.parent
    if parent.is_symlink() or not parent.is_dir():
        raise CandidateError("输出目录父目录必须存在且禁止软链接：{}".format(parent))
    staging = parent / ".{}.tmp.{}".format(target.name, os.getpid())
    if staging.exists() or staging.is_symlink():
        raise CandidateError("候选临时目录已存在：{}".format(staging))
    staging.mkdir(mode=0o750)
    return target, staging


def _cleanup_staging(staging: Path) -> None:
    """只清理由本进程显式创建且具有固定命名模式的临时目录。"""

    if staging.exists() and staging.is_dir() and not staging.is_symlink() and ".tmp." in staging.name:
        shutil.rmtree(staging)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    data = (
        json.dumps(_utc_json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _write_sha256sums(directory: Path, names: Sequence[str]) -> None:
    lines = ["{}  {}".format(_sha256(directory / name), name) for name in sorted(names)]
    path = directory / "SHA256SUMS"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _apply_phase_metadata(incident: dict[str, Any], metadata: Mapping[str, Any]) -> None:
    """用小型元数据替代大路径内容，同时保持六种阶段质量状态。"""

    if incident.get("fact_link_status") == "legacy_collision":
        return
    coverage = incident.get("phase_coverage")
    if not isinstance(coverage, dict):
        return
    for phase, source in metadata.items():
        if phase not in coverage or not isinstance(source, Mapping):
            continue
        if not source.get("retained"):
            continue
        if source.get("empty") is True:
            coverage[phase] = {
                "source_field": source.get("source_field"),
                "semantics": "route_observation_not_causal_trace",
                "supports_recovery": False,
                "status": "observed_no_path_in_snapshot",
                "missing_reason": None,
                "observations": [],
                "materialization": {"payload_included": False, "stored_bytes": source.get("stored_bytes")},
            }
        else:
            coverage[phase] = {
                "source_field": source.get("source_field"),
                "semantics": "route_observation_not_causal_trace",
                "supports_recovery": False,
                "status": "legacy_unknown",
                "missing_reason": "candidate_excludes_large_path_payload",
                "observations": None,
                "materialization": {"payload_included": False, "stored_bytes": source.get("stored_bytes")},
            }


def _database_row_ready(row: Mapping[str, Any], normalizer: Any) -> dict[str, Any]:
    """将 JSONB 中已文本化的数据库时间字段也统一成 UTC Z。"""

    ready = dict(_utc_json_ready(row))
    for field in ("s_time", "e_time", "start_time", "end_time", "judge_time", "notify_time"):
        value = ready.get(field)
        if value in (None, ""):
            continue
        try:
            ready[field] = normalizer.business_time_to_utc(value)
        except Exception as error:
            raise CandidateError("数据库时间字段 {} 无法规范为 UTC Z".format(field)) from error
    return ready


class _SidecarIndex:
    """磁盘索引避免 117 万稳定 ID 或异常映射整批驻留内存。"""

    def __init__(self, path: Path):
        self.path = path
        self.connection = sqlite3.connect(str(path))
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("CREATE TABLE exceptions (link_key TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE duplicate_events (physical_month TEXT NOT NULL, detail_url TEXT NOT NULL, PRIMARY KEY (physical_month, detail_url))")
        self.connection.execute("CREATE TABLE quarantined_events (physical_month TEXT NOT NULL, detail_url TEXT NOT NULL, PRIMARY KEY (physical_month, detail_url))")
        self.connection.execute("CREATE TABLE stable_ids (kind TEXT NOT NULL, stable_id TEXT NOT NULL, source_ref TEXT NOT NULL, PRIMARY KEY (kind, stable_id))")

    def close(self) -> None:
        self.connection.close()

    def put_exception(self, key: str, kind: str, payload: Mapping[str, Any]) -> None:
        try:
            self.connection.execute(
                "INSERT INTO exceptions(link_key,kind,payload) VALUES(?,?,?)",
                (key, kind, json.dumps(_utc_json_ready(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
            )
        except sqlite3.IntegrityError as error:
            raise CandidateError("同一 locator 同时出现多种未消解异常：{}".format(key)) from error

    def get_exception(self, key: str) -> Optional[tuple[str, dict[str, Any]]]:
        row = self.connection.execute("SELECT kind,payload FROM exceptions WHERE link_key=?", (key,)).fetchone()
        if row is None:
            return None
        return str(row[0]), json.loads(row[1])

    def put_duplicate_event(self, physical_month: str, detail_url: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO duplicate_events(physical_month,detail_url) VALUES(?,?)",
            (physical_month, detail_url),
        )

    def is_duplicate_event(self, physical_month: str, detail_url: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM duplicate_events WHERE physical_month=? AND detail_url=?",
            (physical_month, detail_url),
        ).fetchone() is not None

    def put_quarantined_event(self, physical_month: str, detail_url: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO quarantined_events(physical_month,detail_url) VALUES(?,?)",
            (physical_month, detail_url),
        )

    def is_quarantined_event(self, physical_month: str, detail_url: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM quarantined_events WHERE physical_month=? AND detail_url=?",
            (physical_month, detail_url),
        ).fetchone() is not None

    def assert_unique(self, kind: str, stable_id: str, source_ref: str) -> None:
        try:
            self.connection.execute(
                "INSERT INTO stable_ids(kind,stable_id,source_ref) VALUES(?,?,?)",
                (kind, stable_id, source_ref),
            )
        except sqlite3.IntegrityError as error:
            previous = self.connection.execute(
                "SELECT source_ref FROM stable_ids WHERE kind=? AND stable_id=?",
                (kind, stable_id),
            ).fetchone()
            raise CandidateError(
                "稳定 ID 重复：{} {}（{} / {}）".format(
                    kind, stable_id, previous[0] if previous else "unknown", source_ref
                )
            ) from error

    def commit(self) -> None:
        self.connection.commit()


def _group_join_rows(rows: Iterable[tuple[Any, ...]]) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]]:
    current_reference: Optional[str] = None
    current_event: Optional[dict[str, Any]] = None
    current_facts: list[dict[str, Any]] = []
    current_metadata: dict[str, Any] = {}
    for event_row, fact_row, phase_metadata in rows:
        event = dict(event_row)
        reference = str(event.get("detail_url"))
        if current_reference is not None and reference != current_reference:
            assert current_event is not None
            yield current_event, current_facts, current_metadata
            current_facts = []
            current_metadata = {}
        current_reference = reference
        current_event = event
        if fact_row is not None:
            current_facts.append(dict(fact_row))
            if not current_metadata:
                current_metadata = dict(phase_metadata or {})
    if current_reference is not None:
        assert current_event is not None
        yield current_event, current_facts, current_metadata


def _classify_quarantine(normalizer: Any, *, event: Optional[Mapping[str, Any]] = None, fact: Optional[Mapping[str, Any]] = None, window_start: str, window_end: str) -> dict[str, Any]:
    result = normalizer.normalize_event_facts(
        [event] if event is not None else [],
        [fact] if fact is not None else [],
        window_start=window_start,
        window_end_exclusive=window_end,
    )
    records = result["quarantine"]
    if len(records) == 1:
        return dict(records[0])
    if not records and event is not None:
        # 公共 batch API 会识别身份、来源和分区异常；历史总表 s_time 与
        # detail_url 时间不一致属于 runner 的跨记录对账项，使用同一公共
        # quarantine builder 显式分流。
        source_table = str(event.get("source_table") or "event_table_unknown")
        detail_reference = str(event.get("detail_url"))
        return normalizer.build_quarantine_record(
            source_table=source_table,
            source_primary_key={"detail_url": detail_reference},
            reasons=("legacy_time_mismatch",),
            record_kind="event_reference",
            legacy_payload=event,
            evidence=({"reason": "event_s_time_differs_from_detail_reference"},),
        )
    raise CandidateError("单条异常记录没有得到唯一 quarantine 解释")


def _source_table_names(months: Sequence[str]) -> list[str]:
    names = ["event_table_{}".format(month) for month in months]
    names.extend(
        "{}_{}".format(FACT_SPECS[event_type]["family"], month)
        for month in months
        for event_type in EVENT_TYPES
    )
    return sorted(names)


def _preflight_exceptions(
    cursor: Any,
    *,
    months: Sequence[str],
    normalizer: Any,
    index: _SidecarIndex,
    collision_writer: DeterministicGzipJsonlWriter,
    counters: dict[str, int],
) -> None:
    # 先冻结跨月完全相同的 event reference，防止稳定 ID 重复。
    cursor.execute(_duplicate_event_query(months))
    for detail_url, physical_months in cursor.fetchall():
        for physical_month in physical_months:
            index.put_duplicate_event(str(physical_month), str(detail_url))
            counters["duplicate_event_reference_count"] += 1

    for month in months:
        for event_type in EVENT_TYPES:
            source_table = "{}_{}".format(FACT_SPECS[event_type]["family"], month)
            cursor.execute(_ambiguity_query(month, event_type))
            for problem, event_id, source, native_keys in cursor.fetchall():
                key = _link_key(source_table, problem, event_id, source)
                index.put_exception(
                    key,
                    "ambiguous",
                    {"candidate_source_primary_keys": native_keys, "reason": "locator_matches_multiple_native_keys"},
                )
                counters["ambiguous_locator_group_count"] += 1

            cursor.execute(_collision_query(month, event_type, months), (event_type, month))
            for problem, event_id, source, native_key, detail_urls in cursor.fetchall():
                incident_ids = []
                for detail_url in detail_urls:
                    incident_ids.append(normalizer.parse_detail_url(detail_url)["incident_id"])
                group = normalizer.build_collision_group(
                    source_table=source_table,
                    source_primary_key=native_key,
                    incident_ids=incident_ids,
                )
                index.assert_unique("collision_group", group["collision_group_id"], source_table)
                collision_writer.write(group)
                key = _link_key(source_table, problem, event_id, source)
                index.put_exception(key, "collision", group)
                counters["collision_group_count"] += 1
                counters["collision_incident_count"] += len(group["incident_ids"])
    index.commit()


def _write_orphans(
    connection: Any,
    *,
    months: Sequence[str],
    normalizer: Any,
    window_start: str,
    window_end: str,
    quarantine_writer: DeterministicGzipJsonlWriter,
    index: _SidecarIndex,
    counters: dict[str, int],
) -> None:
    # detail_url 的 split_part 过滤缺少表达式统计，固定库上 PostgreSQL 会把
    # 逾百万行 refs 误估为 1 行，并只按 source 选择 Merge Anti Join；source
    # 基本恒为 r 时会退化为近似笛卡尔比较。该事务级保护只影响孤儿反连接，
    # 强制使用三个等值条件的 Hash Anti Join；完成后立即恢复默认规划器开关。
    planner_cursor = connection.cursor()
    try:
        planner_cursor.execute("SET LOCAL enable_mergejoin = off")
        planner_cursor.execute("SET LOCAL enable_nestloop = off")
    finally:
        planner_cursor.close()

    completed = False
    try:
        for month in months:
            for event_type in EVENT_TYPES:
                cursor = _stream_cursor(connection, "p0_orphan_{}_{}".format(event_type, month))
                try:
                    cursor.execute(_orphan_query(month, event_type, months), (event_type, month, "{}_{}".format(FACT_SPECS[event_type]["family"], month)))
                    for fact_row, _phase_metadata in _fetchmany(cursor):
                        fact = _database_row_ready(dict(fact_row), normalizer)
                        record = _classify_quarantine(
                            normalizer,
                            fact=fact,
                            window_start=window_start,
                            window_end=window_end,
                        )
                        index.assert_unique("quarantine", record["quarantine_id"], record["source_table"])
                        quarantine_writer.write(record)
                        counters["reverse_orphan_count"] += 1
                        counters["explained_reverse_orphan_count"] += 1
                        for reason in record["reason_codes"]:
                            counters["quarantine_reason_counts"][reason] = counters["quarantine_reason_counts"].get(reason, 0) + 1
                finally:
                    cursor.close()
        completed = True
    finally:
        if completed:
            planner_cursor = connection.cursor()
            try:
                planner_cursor.execute("SET LOCAL enable_mergejoin = on")
                planner_cursor.execute("SET LOCAL enable_nestloop = on")
            finally:
                planner_cursor.close()
    index.commit()


def _write_malformed_events(
    connection: Any,
    *,
    months: Sequence[str],
    normalizer: Any,
    window_start: str,
    window_end: str,
    quarantine_writer: DeterministicGzipJsonlWriter,
    index: _SidecarIndex,
    counters: dict[str, int],
) -> None:
    for month in months:
        cursor = _stream_cursor(connection, "p0_bad_events_{}".format(month))
        try:
            cursor.execute(_malformed_event_query(month), ("event_table_{}".format(month), month))
            for (event_row,) in _fetchmany(cursor):
                event = _database_row_ready(dict(event_row), normalizer)
                record = _classify_quarantine(
                    normalizer,
                    event=event,
                    window_start=window_start,
                    window_end=window_end,
                )
                index.assert_unique("quarantine", record["quarantine_id"], record["source_table"])
                quarantine_writer.write(record)
                index.put_quarantined_event(month, str(event.get("detail_url")))
                counters["malformed_or_mismatched_event_count"] += 1
                for reason in record["reason_codes"]:
                    counters["quarantine_reason_counts"][reason] = counters["quarantine_reason_counts"].get(reason, 0) + 1
        finally:
            cursor.close()
    index.commit()


def _write_incidents(
    connection: Any,
    *,
    months: Sequence[str],
    normalizer: Any,
    incident_writer: DeterministicGzipJsonlWriter,
    link_writer: DeterministicGzipJsonlWriter,
    quarantine_writer: DeterministicGzipJsonlWriter,
    index: _SidecarIndex,
    counters: dict[str, int],
    max_events: Optional[int],
    window_start: str,
    window_end: str,
) -> None:
    visited_events = 0
    for month in months:
        for event_type in EVENT_TYPES:
            if max_events is not None and visited_events >= max_events:
                return
            cursor = _stream_cursor(connection, "p0_incident_{}_{}".format(event_type, month))
            try:
                cursor.execute(
                    _event_join_query(month, event_type),
                    ("event_table_{}".format(month), "{}_{}".format(FACT_SPECS[event_type]["family"], month), event_type),
                )
                groups = _group_join_rows(_fetchmany(cursor))
                for event_row, fact_rows, phase_metadata in groups:
                    if max_events is not None and visited_events >= max_events:
                        return
                    visited_events += 1
                    event = _database_row_ready(event_row, normalizer)
                    detail_reference = str(event.get("detail_url"))
                    if index.is_quarantined_event(month, detail_reference):
                        # 已由前置严格对账分流，不能再次进入 Incident。
                        continue
                    if index.is_duplicate_event(month, detail_reference):
                        # malformed 扫描不覆盖跨月重复；在这里按物理分区分别隔离。
                        record = normalizer.build_quarantine_record(
                            source_table="event_table_{}".format(month),
                            source_primary_key={"detail_url": detail_reference},
                            reasons=("duplicate_event_reference",),
                            record_kind="event_reference",
                            legacy_payload=event,
                            evidence=({"reason": "same_detail_reference_in_multiple_partitions"},),
                        )
                        index.assert_unique("quarantine", record["quarantine_id"], record["source_table"])
                        quarantine_writer.write(record)
                        counters["quarantined_duplicate_event_count"] += 1
                        counters["quarantine_reason_counts"]["duplicate_event_reference"] = counters["quarantine_reason_counts"].get("duplicate_event_reference", 0) + 1
                        continue
                    try:
                        locator = normalizer.parse_detail_url(detail_reference)
                    except Exception:
                        # SQL 过滤只做廉价结构判断；严格身份仍以公共 API 为准。
                        record = _classify_quarantine(
                            normalizer,
                            event=event,
                            window_start=window_start,
                            window_end=window_end,
                        )
                        index.assert_unique("quarantine", record["quarantine_id"], record["source_table"])
                        quarantine_writer.write(record)
                        counters["malformed_or_mismatched_event_count"] += 1
                        continue

                    exception = index.get_exception(
                        _link_key(locator["source_table"], locator["problem"], str(locator["event_id"]), locator["source"])
                    )
                    status = "matched"
                    selected_fact: Optional[dict[str, Any]] = None
                    collision_group_id = None
                    candidate_keys: list[dict[str, Any]] = []
                    unresolved_reasons: list[str] = []
                    if exception is not None and exception[0] == "collision":
                        status = "legacy_collision"
                        collision_group_id = exception[1]["collision_group_id"]
                        if len(fact_rows) != 1:
                            raise CandidateError("碰撞 locator 未精确落到一条事实：{}".format(detail_reference))
                        selected_fact = _database_row_ready(fact_rows[0], normalizer)
                        candidate_keys = [normalizer.fact_source_primary_key(event_type, selected_fact)]
                    elif exception is not None and exception[0] == "ambiguous":
                        status = "unresolved"
                        candidate_keys = list(exception[1]["candidate_source_primary_keys"])
                        unresolved_reasons.append("locator_matches_multiple_native_keys")
                        counters["forward_ambiguous_count"] += 1
                    elif len(fact_rows) == 1:
                        selected_fact = _database_row_ready(fact_rows[0], normalizer)
                        candidate_keys = [normalizer.fact_source_primary_key(event_type, selected_fact)]
                        fact_start = selected_fact.get("s_time", selected_fact.get("start_time"))
                        try:
                            fact_time_matches = (
                                fact_start not in (None, "")
                                and normalizer.business_time_to_utc(fact_start)
                                == locator["event_time_utc"]
                            )
                        except Exception:
                            fact_time_matches = False
                        if not fact_time_matches:
                            # 单候选但时间不一致仍是 unresolved，不能因为 native
                            # locator 唯一就把混合或错月事实静默接入 Incident。
                            status = "unresolved"
                            selected_fact = None
                            unresolved_reasons.append("fact_start_time_mismatch")
                            counters["forward_time_mismatch_count"] += 1
                    elif len(fact_rows) == 0:
                        status = "unresolved"
                        unresolved_reasons.append("fact_not_found")
                        counters["forward_missing_count"] += 1
                    else:
                        # 即使预检索引异常，也禁止静默选择第一条。
                        status = "unresolved"
                        candidate_keys = [
                            normalizer.fact_source_primary_key(
                                event_type, _database_row_ready(row, normalizer)
                            )
                            for row in fact_rows
                        ]
                        unresolved_reasons.append("locator_matches_multiple_native_keys")
                        counters["forward_ambiguous_count"] += 1

                    incident = normalizer.normalize_event(
                        event,
                        selected_fact,
                        {
                            "fact_link_status": status,
                            "source_table": locator["source_table"],
                            "collision_group_id": collision_group_id,
                            "link_issues": unresolved_reasons,
                        },
                    )
                    incident = _utc_json_ready(incident)
                    _apply_phase_metadata(incident, phase_metadata)
                    index.assert_unique("incident", incident["incident_id"], detail_reference)
                    incident_writer.write(incident)
                    link = {
                        "incident_id": incident["incident_id"],
                        "detail_reference": detail_reference,
                        "event_type": event_type,
                        "source_table": locator["source_table"],
                        "status": status,
                        "matched_source_primary_key": candidate_keys[0] if status in ("matched", "legacy_collision") else None,
                        "candidate_source_primary_keys": candidate_keys,
                        "locator_risks": locator["locator_risks"],
                        "unresolved_reasons": sorted(set(unresolved_reasons)),
                        "collision_group_id": collision_group_id,
                        "classification": "observation_only",
                        "causal_conclusion": None,
                    }
                    link_writer.write(link)
                    counters["incident_count"] += 1
                    counters["link_count"] += 1
                    counters["fact_link_status_counts"][status] = counters["fact_link_status_counts"].get(status, 0) + 1
                    counters["event_type_counts"][event_type] = counters["event_type_counts"].get(event_type, 0) + 1
                    if counters["incident_count"] % 10000 == 0:
                        index.commit()
            finally:
                cursor.close()
    index.commit()


def _summary_markdown(manifest: Mapping[str, Any]) -> str:
    summary = manifest["summary"]
    source = manifest["source"]
    sample = manifest["sample"]
    return """# P0 D2 规范化候选摘要

## 候选身份

- 数据档：`{profile}`
- 来源发布：`{release}`
- 数据库系统标识：`{system}`
- 候选指纹：`{fingerprint}`
- 模式：`{mode}`
- 准入结论：`{admission}`

## 对账结果

- Incident：{incidents}
- Link：{links}
- 碰撞组：{collisions}
- 正向缺失：{forward_missing}
- 正向一对多歧义：{forward_ambiguous}
- 正向事实时间不一致：{forward_time_mismatch}
- 反向孤儿：{orphans}
- 已由 quarantine 解释的反向孤儿：{explained_orphans}
- 未解释反向引用：{unexplained_orphans}
- quarantine：{quarantine}

## 解释边界

本候选只表达历史观测与数据质量，不表达因果。`classification` 固定为
`observation_only`，`causal_conclusion` 固定为 `null`。历史路径 payload
没有复制到候选；候选只保留是否存在、是否为空和物理字节量。空路径快照不
证明网络中没有路径，也不证明事件已经恢复。

所有缺失均以显式状态与原因表达，未把缺失、查询失败或未保留字段补成 0。
""".format(
        profile=manifest["data_profile"]["id"],
        release=source["release_id"],
        system=source["database"]["system_identifier"],
        fingerprint=manifest["candidate_fingerprint_sha256"],
        mode="fixture sample（不得准入）" if sample["enabled"] else "固定窗口全量候选",
        admission=manifest["admission"]["status"],
        incidents=summary["incident_count"],
        links=summary["link_count"],
        collisions=summary["collision_group_count"],
        forward_missing=summary["forward_missing_count"],
        forward_ambiguous=summary["forward_ambiguous_count"],
        forward_time_mismatch=summary["forward_time_mismatch_count"],
        orphans=summary["reverse_orphan_count"],
        explained_orphans=summary["explained_reverse_orphan_count"],
        unexplained_orphans=summary["unexplained_reverse_orphan_count"],
        quarantine=summary["quarantine_count"],
    )


def normalize_candidate(
    connection: Any,
    *,
    profile: Mapping[str, Any],
    context: Mapping[str, Any],
    database_config: Mapping[str, str],
    provenance: Mapping[str, Any],
    project_root: Path,
    output_dir: Path,
    pipeline_root: Optional[Path] = None,
    max_events: Optional[int] = None,
    statement_timeout_ms: int = 3_600_000,
    security_verifier: Callable[..., Mapping[str, Any]] = _verify_reader_security,
) -> dict[str, Any]:
    """在严格只读事务中生成候选；供 CLI 与 fake connection 测试复用。"""

    if max_events is not None and (isinstance(max_events, bool) or max_events < 1):
        raise CandidateError("max-events 必须是正整数")
    effective_pipeline_root = project_root if pipeline_root is None else pipeline_root
    normalizer = _load_normalizer(effective_pipeline_root)
    months = []
    start = profile["parsed"]["start"]
    end = profile["parsed"]["end_exclusive"]
    cursor_month = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor_month < end:
        months.append(cursor_month.strftime("%Y%m"))
        if cursor_month.month == 12:
            cursor_month = cursor_month.replace(year=cursor_month.year + 1, month=1)
        else:
            cursor_month = cursor_month.replace(month=cursor_month.month + 1)
    if months != ["202602", "202603"]:
        raise CandidateError("P0 D2 runner 只接受唯一固定二三月数据档")

    target, staging = _prepare_staging(output_dir)
    transaction_cursor = None
    completed = False
    try:
        transaction_cursor = connection.cursor()
        _begin_readonly_transaction(transaction_cursor, statement_timeout_ms)
        security = security_verifier(
            transaction_cursor,
            expected_user=database_config["DOMEYE_CORE_DB_READER_USER"],
            expected_database=database_config["DOMEYE_CORE_DB_NAME"],
            expected_system=context["system_identifier"],
        )
        source_table_names = _source_table_names(months)
        transaction_cursor.execute(_count_query(source_table_names))
        source_counts = {name: int(count) for name, count in transaction_cursor.fetchall()}

        counters: dict[str, Any] = {
            "incident_count": 0,
            "link_count": 0,
            "collision_group_count": 0,
            "collision_incident_count": 0,
            "reverse_orphan_count": 0,
            "explained_reverse_orphan_count": 0,
            "forward_missing_count": 0,
            "forward_ambiguous_count": 0,
            "forward_time_mismatch_count": 0,
            "ambiguous_locator_group_count": 0,
            "duplicate_event_reference_count": 0,
            "quarantined_duplicate_event_count": 0,
            "malformed_or_mismatched_event_count": 0,
            "fact_link_status_counts": {},
            "event_type_counts": {},
            "quarantine_reason_counts": {},
        }
        writers: dict[str, DeterministicGzipJsonlWriter] = {}
        sidecar = _SidecarIndex(staging / ".candidate-index.sqlite3")
        try:
            with ExitStack() as stack:
                for filename in JSONL_FILES:
                    writers[filename] = stack.enter_context(DeterministicGzipJsonlWriter(staging / filename))
                _preflight_exceptions(
                    transaction_cursor,
                    months=months,
                    normalizer=normalizer,
                    index=sidecar,
                    collision_writer=writers["collision_groups.jsonl.gz"],
                    counters=counters,
                )
                _write_orphans(
                    connection,
                    months=months,
                    normalizer=normalizer,
                    window_start=profile["local"]["start"],
                    window_end=profile["local"]["end_exclusive"],
                    quarantine_writer=writers["quarantine.jsonl.gz"],
                    index=sidecar,
                    counters=counters,
                )
                _write_malformed_events(
                    connection,
                    months=months,
                    normalizer=normalizer,
                    window_start=profile["local"]["start"],
                    window_end=profile["local"]["end_exclusive"],
                    quarantine_writer=writers["quarantine.jsonl.gz"],
                    index=sidecar,
                    counters=counters,
                )
                _write_incidents(
                    connection,
                    months=months,
                    normalizer=normalizer,
                    incident_writer=writers["incidents.jsonl.gz"],
                    link_writer=writers["links.jsonl.gz"],
                    quarantine_writer=writers["quarantine.jsonl.gz"],
                    index=sidecar,
                    counters=counters,
                    max_events=max_events,
                    window_start=profile["local"]["start"],
                    window_end=profile["local"]["end_exclusive"],
                )
        finally:
            sidecar.close()
        (staging / ".candidate-index.sqlite3").unlink()

        file_inventory = {name: writers[name].inventory() for name in JSONL_FILES}
        counters["quarantine_count"] = file_inventory["quarantine.jsonl.gz"]["row_count"]
        counters["unexplained_reverse_orphan_count"] = (
            counters["reverse_orphan_count"] - counters["explained_reverse_orphan_count"]
        )
        counters["unexplained_forward_reference_count"] = (
            counters["forward_missing_count"]
            + counters["forward_ambiguous_count"]
            + counters["forward_time_mismatch_count"]
        )
        sample = {"enabled": max_events is not None, "max_events": max_events, "admissible": False if max_events is not None else True}
        raw_profile = {key: profile[key] for key in PROFILE_RAW_KEYS}
        summary = _utc_json_ready(counters)
        admission_failures = []
        if max_events is not None:
            admission_failures.append("fixture_sample_not_admissible")
        if counters["unexplained_reverse_orphan_count"]:
            admission_failures.append("unexplained_reverse_references")
        if counters["unexplained_forward_reference_count"]:
            admission_failures.append("unexplained_forward_references")
        admission = {
            "status": "not_eligible" if admission_failures else "legacy_candidate_ready",
            "eligible_for_release_gate": not admission_failures,
            "blocking_reasons": admission_failures,
            "raw_traceable": False,
        }
        normalizer_hashes = _normalizer_source_hashes(effective_pipeline_root)
        fingerprint_payload = {
            "schema_version": "p0_normalization_candidate_v1",
            "data_profile": raw_profile,
            "source_release": {
                "release_id": context["release_id"],
                "system_identifier": context["system_identifier"],
                "state_sha256": context["state_sha256"],
                "manifest_sha256": context["manifest_sha256"],
                "database_manifest_sha256": context["database_manifest_sha256"],
                "inventory_sha256": context["inventory_sha256"],
            },
            "runner_sha256": _sha256(CANDIDATE_PATH),
            "normalizer_hashes": normalizer_hashes,
            "source_table_counts": source_counts,
            "files": file_inventory,
            "summary": summary,
            "sample": sample,
            "classification": "observation_only",
            "causal_conclusion": None,
        }
        fingerprint = _canonical_sha256(fingerprint_payload)
        manifest = {
            "schema_version": "p0_normalization_candidate_v1",
            "candidate_kind": "readonly_legacy_fact_normalization",
            "candidate_fingerprint_sha256": fingerprint,
            "data_profile": raw_profile,
            "window_utc": {
                "start": normalizer.business_time_to_utc(profile["local"]["start"]),
                "end_exclusive": normalizer.business_time_to_utc(profile["local"]["end_exclusive"]),
            },
            "source": {
                "release_id": context["release_id"],
                "state_sha256": context["state_sha256"],
                "manifest_sha256": context["manifest_sha256"],
                "database_manifest_sha256": context["database_manifest_sha256"],
                "inventory_sha256": context["inventory_sha256"],
                "database": {
                    "host": LOOPBACK_HOST,
                    "port": context["port"],
                    "name": security["database"],
                    "system_identifier": security["system_identifier"],
                    "transaction_read_only": True,
                    "transaction_isolation": "repeatable read",
                },
                "provenance": dict(provenance),
                "normalizer_hashes": normalizer_hashes,
            },
            "source_table_counts": source_counts,
            "files": file_inventory,
            "summary": summary,
            "sample": sample,
            "admission": admission,
            "materialization_policy": {
                "large_path_payload_included": False,
                "retained_metadata": ["retained", "empty", "stored_bytes"],
                "nonempty_phase_status": "legacy_unknown",
                "missing_values_coerced_to_zero": False,
            },
            "classification": "observation_only",
            "causal_conclusion": None,
        }
        _write_json(staging / "manifest.json", manifest)
        summary_path = staging / "摘要.md"
        with summary_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(_summary_markdown(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        _write_sha256sums(staging, list(JSONL_FILES) + ["manifest.json", "摘要.md"])
        for path in staging.iterdir():
            path.chmod(0o440)
        if target.exists() or target.is_symlink():
            raise CandidateError("发布候选时目标目录已出现，拒绝覆盖：{}".format(target))
        staging.rename(target)
        completed = True
        return manifest
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            connection.rollback()
        except Exception:
            pass
        try:
            if transaction_cursor is not None:
                transaction_cursor.close()
        except Exception:
            pass
        if not completed:
            _cleanup_staging(staging)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生成固定二三月 P0 D2 只读规范化候选制品")
    parser.add_argument("--database-env", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument(
        "--pipeline-root",
        type=Path,
        help="含 backend/data_pipeline 的只读 staging bundle 根；默认使用 project-root",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-events", type=int, help="仅供 fixture 测试；启用后制品不得准入")
    parser.add_argument("--statement-timeout-ms", type=int, default=3_600_000)
    parser.add_argument("--connect-timeout", type=int, default=5)
    arguments = parser.parse_args(argv)
    connection = None
    try:
        try:
            project_root = arguments.project_root.resolve(strict=True)
        except OSError as error:
            raise CandidateError("无法解析项目根目录：{}".format(arguments.project_root)) from error
        profile = _load_project_data_profile(project_root)
        context = _validate_release_context(
            profile=profile,
            state_path=arguments.state,
            release_dir=arguments.release_dir,
        )
        database_config = _read_database_env(arguments.database_env)
        provenance = _git_provenance(
            project_root,
            probe_path=CANDIDATE_PATH,
            data_profile_path=project_root / "config" / "data-profile.json",
        )
        connection = _connect_database(database_config, context, arguments.connect_timeout)
        normalize_candidate(
            connection,
            profile=profile,
            context=context,
            database_config=database_config,
            provenance=provenance,
            project_root=project_root,
            pipeline_root=arguments.pipeline_root,
            output_dir=arguments.output_dir,
            max_events=arguments.max_events,
            statement_timeout_ms=arguments.statement_timeout_ms,
        )
    except ProbeError as error:
        parser.error(str(error))
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
