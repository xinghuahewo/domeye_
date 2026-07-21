#!/usr/bin/env python3
"""对固定二三月开发数据库执行可重复、严格只读的 P0 数据探针。

该程序只允许连接 ``state.json`` 声明的 loopback 端口，并且只使用
``database.env`` 中的 reader 凭据。所有数据库查询都位于同一个
``REPEATABLE READ READ ONLY`` 事务中；程序最终始终回滚该事务。
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Iterable


PROBE_PATH = Path(__file__).absolute()


LOOPBACK_HOST = "127.0.0.1"
EXPECTED_READER = "domeye_core_reader"
EVENT_TYPES = (
    "hijack",
    "sub_hijack",
    "leak",
    "prefix_outage",
    "as_outage",
    "country_outage",
)
EVENT_TYPE_LABELS = {
    "hijack": "前缀劫持",
    "sub_hijack": "子前缀劫持",
    "leak": "路由泄漏",
    "prefix_outage": "前缀中断",
    "as_outage": "AS中断",
    "country_outage": "国家中断",
}
MONTHLY_FAMILIES = (
    "event_table",
    "hijack",
    "sub_hijack",
    "leak_event",
    "prefix_outage",
    "as_outage",
    "country_outage",
    "feature_other",
    "feature_us",
    "feature_br",
    "feature_cn",
    "feature_ru",
    "feature_in",
    "feature_gb",
    "feature_id",
    "feature_de",
    "feature_au",
    "feature_pl",
)
AS_FEATURE_FAMILIES = (
    "feature_other",
    "feature_us",
    "feature_br",
    "feature_cn",
    "feature_ru",
    "feature_in",
    "feature_gb",
    "feature_id",
    "feature_de",
    "feature_au",
    "feature_pl",
)
FACT_DEFINITIONS = {
    "hijack": {
        "family": "hijack",
        "problem": "prefix",
        "event_id": "hijack_eventid",
        "native_key": ("source", "prefix", "hijack_eventid"),
    },
    "sub_hijack": {
        "family": "sub_hijack",
        "problem": "prefix",
        "event_id": "sub_hijack_eventid",
        "native_key": ("source", "prefix", "sub_hijack_eventid"),
    },
    "leak": {
        "family": "leak_event",
        "problem": "prefix",
        "event_id": "leak_event_id",
        "native_key": ("source", "prefix", "leak_event_id"),
    },
    "prefix_outage": {
        "family": "prefix_outage",
        "problem": "prefix",
        "event_id": "outage_id",
        "native_key": ("source", "prefix", "outage_id", "asn"),
    },
    "as_outage": {
        "family": "as_outage",
        "problem": "asn",
        "event_id": "outage_id",
        "native_key": ("source", "asn", "outage_id"),
    },
    "country_outage": {
        "family": "country_outage",
        "problem": "country",
        "event_id": "outage_id",
        "native_key": ("source", "country", "outage_id"),
    },
}
PROFILE_RAW_KEYS = (
    "schema_version",
    "id",
    "mode",
    "timezone",
    "window_start",
    "window_end_exclusive",
    "snapshot_time",
    "api_profile",
)
RELEASE_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z(?:-[a-z0-9][a-z0-9._-]{0,47})?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LEGACY_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ProbeError(RuntimeError):
    """探针输入、安全边界或数据库结果不符合约定。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (datetime, date)):
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            )
        return value.isoformat(sep=" ")
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ProbeError("探针结果包含非有限浮点数")
    return value


def _assert_regular_file(path: Path, label: str, *, secret: bool = False) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise ProbeError("{}不可读取：{}".format(label, path)) from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ProbeError("{}必须是普通文件且禁止软链接：{}".format(label, path))
    if secret:
        mode = stat.S_IMODE(info.st_mode)
        if not (mode & stat.S_IRUSR) or mode & ~0o600:
            raise ProbeError(
                "{}权限必须是 0600 的子集且所有者可读：{}（当前 {:04o}）".format(
                    label, path, mode
                )
            )


def _load_json_file(path: Path, label: str) -> dict[str, Any]:
    _assert_regular_file(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProbeError("{}不是有效 JSON：{}".format(label, path)) from error
    if not isinstance(value, dict):
        raise ProbeError("{}顶层必须是对象：{}".format(label, path))
    return value


def _load_project_data_profile(project_root: Path) -> dict[str, Any]:
    """从显式项目根加载数据档，支持探针在隔离目录执行。"""

    loader_path = project_root / "dev" / "data_profile.py"
    profile_path = project_root / "config" / "data-profile.json"
    _assert_regular_file(loader_path, "数据档加载器")
    _assert_regular_file(profile_path, "唯一数据档")
    spec = importlib.util.spec_from_file_location("_domeye_p0_data_profile", loader_path)
    if spec is None or spec.loader is None:
        raise ProbeError("无法加载数据档校验器：{}".format(loader_path))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        profile = module.load_data_profile(profile_path)
    except Exception as error:
        raise ProbeError("唯一数据档校验失败：{}".format(profile_path)) from error
    if not isinstance(profile, dict):
        raise ProbeError("数据档加载器未返回对象")
    return profile


def _read_database_env(path: Path) -> dict[str, str]:
    """解析最小 KEY=VALUE 文件；绝不执行 shell 或变量展开。"""

    _assert_regular_file(path, "数据库配置", secret=True)
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ProbeError("无法读取数据库配置：{}".format(path)) from error
    for line_number, raw_line in enumerate(lines, 1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ProbeError("数据库配置第 {} 行不是 KEY=VALUE".format(line_number))
        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not ENV_NAME_RE.fullmatch(name):
            raise ProbeError("数据库配置第 {} 行变量名非法".format(line_number))
        if name in values:
            raise ProbeError("数据库配置变量重复：{}".format(name))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[name] = value

    required = (
        "DOMEYE_CORE_DB_NAME",
        "DOMEYE_CORE_DB_READER_USER",
        "DOMEYE_CORE_DB_READER_PASSWORD",
    )
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise ProbeError("数据库配置缺少 reader 字段：{}".format(", ".join(missing)))
    if values["DOMEYE_CORE_DB_READER_USER"] != EXPECTED_READER:
        raise ProbeError("P0 探针只允许使用固定 reader 角色：{}".format(EXPECTED_READER))
    return {name: values[name] for name in required}


def _profile_months(profile: dict[str, Any]) -> list[str]:
    start = profile["parsed"]["start"]
    end = profile["parsed"]["end_exclusive"]
    cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = []
    while cursor < end:
        months.append(cursor.strftime("%Y%m"))
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)
    return months


def _expected_tables(profile: dict[str, Any]) -> list[str]:
    return ["feature_country"] + [
        "{}_{}".format(family, month)
        for month in _profile_months(profile)
        for family in MONTHLY_FAMILIES
    ]


def _validate_release_context(
    *,
    profile: dict[str, Any],
    state_path: Path,
    release_dir: Path,
) -> dict[str, Any]:
    _assert_regular_file(state_path, "开发数据库状态")
    if release_dir.is_symlink() or not release_dir.is_dir():
        raise ProbeError("来源发布目录必须存在且禁止软链接：{}".format(release_dir))
    state = _load_json_file(state_path, "开发数据库状态")
    manifest_path = release_dir / "manifest.json"
    database_manifest_path = release_dir / "database-manifest.json"
    inventory_path = release_dir / "database-inventory.json"
    manifest = _load_json_file(manifest_path, "发布总清单")
    database_manifest = _load_json_file(database_manifest_path, "数据库组件清单")
    inventory = _load_json_file(inventory_path, "数据库 inventory")

    release_id = state.get("release_id")
    if not isinstance(release_id, str) or not RELEASE_ID_RE.fullmatch(release_id):
        raise ProbeError("state.json release_id 无效")
    if manifest.get("release_id") != release_id or database_manifest.get("release_id") != release_id:
        raise ProbeError("state、manifest 与 database-manifest 的 release_id 不一致")
    try:
        configured_release = Path(str(state["release_dir"])).resolve(strict=True)
        supplied_release = release_dir.resolve(strict=True)
    except (KeyError, OSError) as error:
        raise ProbeError("无法解析 state.json 的 release_dir") from error
    if configured_release != supplied_release:
        raise ProbeError("state.json release_dir 与输入发布目录不一致")

    hashes = state.get("hashes")
    if not isinstance(hashes, dict):
        raise ProbeError("state.json 缺少 hashes")
    actual_hashes = {
        "release_manifest": _sha256(manifest_path),
        "database_manifest": _sha256(database_manifest_path),
        "inventory": _sha256(inventory_path),
    }
    for name, actual in actual_hashes.items():
        expected = hashes.get(name)
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected) or expected != actual:
            raise ProbeError("state.json {} SHA256 与来源文件不一致".format(name))

    component_inventory = database_manifest.get("inventory")
    if not isinstance(component_inventory, dict):
        raise ProbeError("database-manifest 缺少 inventory 引用")
    if component_inventory.get("name") != "database-inventory.json" or component_inventory.get(
        "sha256"
    ) != actual_hashes["inventory"]:
        raise ProbeError("database-manifest 与 database-inventory SHA256 不一致")
    if manifest.get("database") != database_manifest:
        raise ProbeError("发布总清单内嵌的 database 组件与 database-manifest 不一致")

    expected_start = profile["local"]["start"]
    expected_end = profile["local"]["end_exclusive"]
    if state.get("data_start") != expected_start or state.get("data_end_exclusive") != expected_end:
        raise ProbeError("state.json 数据窗口与唯一 data-profile 不一致")
    port = state.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ProbeError("state.json 数据库端口无效")
    if state.get("phase") != "verified":
        raise ProbeError("开发数据库状态不是 verified")
    system_identifier = state.get("system_identifier")
    if not isinstance(system_identifier, str) or not system_identifier.isdigit():
        raise ProbeError("state.json system_identifier 无效")

    inventory_tables = inventory.get("tables")
    if not isinstance(inventory_tables, list):
        raise ProbeError("database-inventory 缺少 tables 数组")
    legacy_hashes: dict[str, str] = {}
    for item in inventory_tables:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ProbeError("database-inventory 表记录无效")
        schema_hash = item.get("schema_hash")
        if not isinstance(schema_hash, str) or not LEGACY_MD5_RE.fullmatch(schema_hash):
            raise ProbeError("database-inventory 表 {} 的 legacy schema MD5 无效".format(item["name"]))
        if item["name"] in legacy_hashes:
            raise ProbeError("database-inventory 表名重复：{}".format(item["name"]))
        legacy_hashes[item["name"]] = schema_hash

    return {
        "release_id": release_id,
        "release_dir": str(supplied_release),
        "port": port,
        "system_identifier": system_identifier,
        "state_sha256": _sha256(state_path),
        "manifest_sha256": actual_hashes["release_manifest"],
        "database_manifest_sha256": actual_hashes["database_manifest"],
        "inventory_sha256": actual_hashes["inventory"],
        "legacy_schema_hashes": legacy_hashes,
    }


def _quote_identifier(value: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ProbeError("SQL 标识符无效")
    return '"{}"'.format(value.replace('"', '""'))


def _sql_literal(value: str) -> str:
    return "'{}'".format(value.replace("'", "''"))


def _rate(count: int | None, row_count: int) -> float | None:
    if count is None or row_count == 0:
        return None
    return round(count / row_count, 12)


def _begin_readonly_transaction(cursor: Any, statement_timeout_ms: int) -> None:
    if not 1_000 <= statement_timeout_ms <= 3_600_000:
        raise ProbeError("statement timeout 必须位于 1000 至 3600000 毫秒")
    cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    cursor.execute("SET LOCAL lock_timeout = '5s'")
    cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
    cursor.execute("SET LOCAL statement_timeout = '{}ms'".format(statement_timeout_ms))


def _validate_security_row(row: Iterable[Any], expected_user: str, expected_database: str, expected_system: str) -> dict[str, Any]:
    values = tuple(row)
    if len(values) != 14:
        raise ProbeError("数据库安全探针返回列数异常")
    (
        current_user,
        database_name,
        transaction_read_only,
        default_transaction_read_only,
        isolation,
        server_version,
        system_identifier,
        rolsuper,
        rolcreaterole,
        rolcreatedb,
        rolreplication,
        rolbypassrls,
        role_config,
        query_started_at,
    ) = values
    if current_user != expected_user or database_name != expected_database:
        raise ProbeError("数据库实际用户或库名与 reader 配置不一致")
    if transaction_read_only != "on" or default_transaction_read_only != "on":
        raise ProbeError("reader 会话或默认事务不是只读")
    if str(isolation).lower() != "repeatable read":
        raise ProbeError("P0 探针事务隔离级别不是 repeatable read")
    if any(bool(value) for value in (rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls)):
        raise ProbeError("reader 角色具有高权限")
    if str(system_identifier) != expected_system:
        raise ProbeError("数据库 system_identifier 与 state.json 不一致")
    return {
        "database": database_name,
        "current_user": current_user,
        "transaction_read_only": True,
        "default_transaction_read_only": True,
        "transaction_isolation": "repeatable read",
        "server_version": str(server_version),
        "system_identifier": str(system_identifier),
        "role_config": list(role_config or []),
        "query_started_at": _json_ready(query_started_at),
    }


def _verify_reader_security(cursor: Any, *, expected_user: str, expected_database: str, expected_system: str) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT current_user,
               current_database(),
               current_setting('transaction_read_only'),
               current_setting('default_transaction_read_only'),
               current_setting('transaction_isolation'),
               current_setting('server_version'),
               (SELECT system_identifier::text FROM pg_control_system()),
               role.rolsuper,
               role.rolcreaterole,
               role.rolcreatedb,
               role.rolreplication,
               role.rolbypassrls,
               role.rolconfig,
               clock_timestamp()
        FROM pg_roles AS role
        WHERE role.rolname = current_user
        """
    )
    row = cursor.fetchone()
    if row is None:
        raise ProbeError("无法读取当前 reader 角色")
    security = _validate_security_row(row, expected_user, expected_database, expected_system)

    cursor.execute(
        """
        SELECT relation.relname,
               has_table_privilege(current_user, relation.oid, 'SELECT') AS can_select,
               (has_table_privilege(current_user, relation.oid, 'INSERT')
                OR has_table_privilege(current_user, relation.oid, 'UPDATE')
                OR has_table_privilege(current_user, relation.oid, 'DELETE')
                OR has_table_privilege(current_user, relation.oid, 'TRUNCATE')
                OR has_table_privilege(current_user, relation.oid, 'REFERENCES')
                OR has_table_privilege(current_user, relation.oid, 'TRIGGER')) AS can_write
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p')
        ORDER BY relation.relname
        """
    )
    privileges = cursor.fetchall()
    failures = [name for name, can_select, can_write in privileges if not can_select or can_write]
    if failures:
        raise ProbeError("reader 缺少 SELECT 或具有写权限：{}".format(", ".join(failures)))
    security["public_table_privilege_count"] = len(privileges)
    return security


def _read_relations(cursor: Any) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT relation.oid,
               relation.relname,
               relation.relkind,
               relation.relispartition,
               pg_get_partkeydef(relation.oid),
               parent_namespace.nspname,
               parent.relname
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        LEFT JOIN pg_inherits AS inheritance ON inheritance.inhrelid = relation.oid
        LEFT JOIN pg_class AS parent ON parent.oid = inheritance.inhparent
        LEFT JOIN pg_namespace AS parent_namespace ON parent_namespace.oid = parent.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p')
        ORDER BY relation.relname
        """
    )
    return [
        {
            "oid": int(oid),
            "name": name,
            "relation_kind": kind,
            "is_partition": bool(is_partition),
            "partition_key": partition_key,
            "parent": (
                {"schema": parent_schema, "name": parent_name}
                if parent_schema is not None and parent_name is not None
                else None
            ),
        }
        for oid, name, kind, is_partition, partition_key, parent_schema, parent_name in cursor.fetchall()
    ]


def _read_timescale_catalog(cursor: Any) -> dict[str, dict[str, Any]]:
    cursor.execute(
        """
        SELECT hypertable_name, num_dimensions, num_chunks, compression_enabled
        FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'public'
        ORDER BY hypertable_name
        """
    )
    return {
        name: {
            "is_hypertable": True,
            "num_dimensions": int(dimensions),
            "num_chunks": int(chunks),
            "compression_enabled": bool(compression),
        }
        for name, dimensions, chunks, compression in cursor.fetchall()
    }


def _schema_fingerprint_payload(schema_payload: dict[str, Any]) -> dict[str, Any]:
    """提取不随 TimescaleDB 物理 chunk 漂移的逻辑 schema。"""

    timescaledb = schema_payload["timescaledb"]
    logical_timescaledb = {
        key: timescaledb[key]
        for key in ("is_hypertable", "num_dimensions", "compression_enabled")
        if key in timescaledb
    }
    return {
        "relation_kind": schema_payload["relation_kind"],
        "is_partition": schema_payload["is_partition"],
        "partition_key": schema_payload["partition_key"],
        "parent": schema_payload["parent"],
        "columns": schema_payload["columns"],
        "constraints": schema_payload["constraints"],
        "indexes": schema_payload["indexes"],
        # TimescaleDB 的内部 chunk 属于物理布局；只保留 public 逻辑分区。
        "partitions": [
            item for item in schema_payload["partitions"] if item["schema"] == "public"
        ],
        "timescaledb": logical_timescaledb,
    }


def _read_table_catalog(cursor: Any, relation: dict[str, Any], timescale: dict[str, dict[str, Any]]) -> dict[str, Any]:
    oid = relation["oid"]
    cursor.execute(
        """
        SELECT attribute.attnum,
               attribute.attname,
               format_type(attribute.atttypid, attribute.atttypmod),
               attribute.attnotnull,
               attribute.attidentity,
               attribute.attgenerated,
               pg_get_expr(default_value.adbin, default_value.adrelid),
               CASE WHEN attribute.attcollation = 0 THEN NULL ELSE collation_value.collname END,
               type.typcategory,
               type.typname
        FROM pg_attribute AS attribute
        JOIN pg_type AS type ON type.oid = attribute.atttypid
        LEFT JOIN pg_attrdef AS default_value
          ON default_value.adrelid = attribute.attrelid
         AND default_value.adnum = attribute.attnum
        LEFT JOIN pg_collation AS collation_value
          ON collation_value.oid = attribute.attcollation
        WHERE attribute.attrelid = %s
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
        ORDER BY attribute.attnum
        """,
        (oid,),
    )
    columns = [
        {
            "ordinal_position": int(position),
            "name": name,
            "data_type": data_type,
            "nullable": not bool(not_null),
            "identity": identity or "",
            "generated": generated or "",
            "default": default,
            "collation": collation,
            "type_category": type_category,
            "type_name": type_name,
        }
        for position, name, data_type, not_null, identity, generated, default, collation, type_category, type_name in cursor.fetchall()
    ]

    cursor.execute(
        """
        SELECT constraint_value.conname,
               constraint_value.contype,
               constraint_value.condeferrable,
               constraint_value.condeferred,
               constraint_value.convalidated,
               pg_get_constraintdef(constraint_value.oid, true),
               ARRAY(
                   SELECT attribute.attname
                   FROM unnest(constraint_value.conkey) WITH ORDINALITY AS key(attnum, position)
                   JOIN pg_attribute AS attribute
                     ON attribute.attrelid = constraint_value.conrelid
                    AND attribute.attnum = key.attnum
                   ORDER BY key.position
               ),
               CASE WHEN constraint_value.conindid = 0 THEN NULL ELSE index_value.indisvalid END
        FROM pg_constraint AS constraint_value
        LEFT JOIN pg_index AS index_value ON index_value.indexrelid = constraint_value.conindid
        WHERE constraint_value.conrelid = %s
        ORDER BY constraint_value.contype, constraint_value.conname
        """,
        (oid,),
    )
    constraints = [
        {
            "name": name,
            "type": constraint_type,
            "deferrable": bool(deferrable),
            "initially_deferred": bool(deferred),
            "validated": bool(validated),
            "definition": definition,
            "columns": list(key_columns or []),
            "backing_index_valid": None if index_valid is None else bool(index_valid),
        }
        for name, constraint_type, deferrable, deferred, validated, definition, key_columns, index_valid in cursor.fetchall()
    ]

    cursor.execute(
        """
        SELECT index_relation.relname,
               pg_get_indexdef(index_value.indexrelid),
               index_value.indisunique,
               index_value.indisprimary,
               index_value.indisvalid,
               index_value.indisready
        FROM pg_index AS index_value
        JOIN pg_class AS index_relation ON index_relation.oid = index_value.indexrelid
        WHERE index_value.indrelid = %s
        ORDER BY index_relation.relname
        """,
        (oid,),
    )
    indexes = [
        {
            "name": name,
            "definition": definition,
            "unique": bool(unique),
            "primary": bool(primary),
            "valid": bool(valid),
            "ready": bool(ready),
        }
        for name, definition, unique, primary, valid, ready in cursor.fetchall()
    ]

    cursor.execute(
        """
        SELECT child_namespace.nspname,
               child.relname,
               pg_get_expr(child.relpartbound, child.oid)
        FROM pg_inherits AS inheritance
        JOIN pg_class AS child ON child.oid = inheritance.inhrelid
        JOIN pg_namespace AS child_namespace ON child_namespace.oid = child.relnamespace
        WHERE inheritance.inhparent = %s
        ORDER BY child_namespace.nspname, child.relname
        """,
        (oid,),
    )
    partitions = [
        {"schema": schema_name, "name": name, "bound": bound}
        for schema_name, name, bound in cursor.fetchall()
    ]
    primary_key = next((item for item in constraints if item["type"] == "p"), None)
    schema_payload = {
        "relation_kind": relation["relation_kind"],
        "is_partition": relation["is_partition"],
        "partition_key": relation["partition_key"],
        "parent": relation["parent"],
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "partitions": partitions,
        "timescaledb": timescale.get(relation["name"], {"is_hypertable": False}),
    }
    return {
        **relation,
        **schema_payload,
        "primary_key": primary_key,
        "schema_sha256": _canonical_sha256(_schema_fingerprint_payload(schema_payload)),
    }


def _build_table_stats_query(table_name: str, time_column: str, columns: list[dict[str, Any]]) -> str:
    table = "{}.{}".format(_quote_identifier("public"), _quote_identifier(table_name))
    time_value = _quote_identifier(time_column)
    expressions = [
        "count(*)::bigint",
        "min({})::text".format(time_value),
        "max({})::text".format(time_value),
        "count(*) FILTER (WHERE {} < %s OR {} >= %s)::bigint".format(time_value, time_value),
    ]
    for column in columns:
        identifier = _quote_identifier(column["name"])
        expressions.append("count(*) FILTER (WHERE {} IS NULL)::bigint".format(identifier))
        if column["type_category"] == "S":
            expressions.append("count(*) FILTER (WHERE {} = '')::bigint".format(identifier))
        else:
            expressions.append("NULL::bigint")
        if column["type_name"] in {"json", "jsonb"}:
            expressions.append(
                "count(*) FILTER (WHERE {0}::jsonb = '{{}}'::jsonb OR {0}::jsonb = '[]'::jsonb)::bigint".format(
                    identifier
                )
            )
        else:
            expressions.append("NULL::bigint")
        if column["type_category"] == "N":
            expressions.append("count(*) FILTER (WHERE {} = 0)::bigint".format(identifier))
        else:
            expressions.append("NULL::bigint")
    return "SELECT {} FROM {}".format(",\n       ".join(expressions), table)


def _apply_table_stats(
    cursor: Any,
    table: dict[str, Any],
    *,
    start: datetime,
    end_exclusive: datetime,
) -> None:
    time_column = "t" if table["name"].startswith("feature_") else "s_time"
    if time_column not in {column["name"] for column in table["columns"]}:
        raise ProbeError("表 {} 缺少时间字段 {}".format(table["name"], time_column))
    cursor.execute(
        _build_table_stats_query(table["name"], time_column, table["columns"]),
        (start, end_exclusive),
    )
    row = cursor.fetchone()
    if row is None:
        raise ProbeError("表 {} 统计查询没有返回结果".format(table["name"]))
    values = tuple(row)
    expected_length = 4 + 4 * len(table["columns"])
    if len(values) != expected_length:
        raise ProbeError("表 {} 统计查询列数异常".format(table["name"]))
    row_count = int(values[0])
    table["time_column"] = time_column
    table["row_count"] = row_count
    table["min_time"] = values[1]
    table["max_time"] = values[2]
    table["out_of_window_count"] = int(values[3])
    offset = 4
    for column in table["columns"]:
        null_count, empty_string_count, empty_json_count, zero_count = values[offset : offset + 4]
        offset += 4
        column["null_count"] = int(null_count)
        column["null_rate"] = _rate(int(null_count), row_count)
        column["empty_string_count"] = (
            None if empty_string_count is None else int(empty_string_count)
        )
        column["empty_string_rate"] = _rate(column["empty_string_count"], row_count)
        column["empty_json_count"] = None if empty_json_count is None else int(empty_json_count)
        column["empty_json_rate"] = _rate(column["empty_json_count"], row_count)
        column["zero_count"] = None if zero_count is None else int(zero_count)
        column["zero_rate"] = _rate(column["zero_count"], row_count)


def _build_full_row_duplicate_query(table_name: str, columns: list[dict[str, Any]]) -> str:
    table = "{}.{}".format(_quote_identifier("public"), _quote_identifier(table_name))
    group_columns = ", ".join(_quote_identifier(column["name"]) for column in columns)
    return """
        SELECT count(*)::bigint, coalesce(sum(group_size - 1), 0)::bigint
        FROM (
            SELECT count(*)::bigint AS group_size
            FROM {table}
            GROUP BY {columns}
            HAVING count(*) > 1
        ) AS duplicate_groups
    """.format(table=table, columns=group_columns)


def _apply_duplicate_stats(cursor: Any, table: dict[str, Any]) -> None:
    primary_key = table["primary_key"]
    if (
        primary_key is not None
        and primary_key["validated"]
        and primary_key["backing_index_valid"] is True
        and primary_key["columns"]
    ):
        table["duplicate_basis"] = {
            "kind": "enforced_primary_key",
            "columns": primary_key["columns"],
            "duplicate_group_count": 0,
            "duplicate_excess_row_count": 0,
            "duplicate_rate": 0.0 if table["row_count"] else None,
        }
        return
    if any(column["type_name"] == "json" for column in table["columns"]):
        table["duplicate_basis"] = {
            "kind": "not_assessed_unGroupable_full_row",
            "columns": [column["name"] for column in table["columns"]],
            "duplicate_group_count": None,
            "duplicate_excess_row_count": None,
            "duplicate_rate": None,
        }
        return
    cursor.execute(_build_full_row_duplicate_query(table["name"], table["columns"]))
    group_count, excess_count = cursor.fetchone()
    table["duplicate_basis"] = {
        "kind": "exact_full_row_grouping",
        "columns": [column["name"] for column in table["columns"]],
        "duplicate_group_count": int(group_count),
        "duplicate_excess_row_count": int(excess_count),
        "duplicate_rate": _rate(int(excess_count), table["row_count"]),
    }


def _native_key_json(definition: dict[str, Any]) -> str:
    arguments = []
    for column in definition["native_key"]:
        arguments.extend((_sql_literal(column), "fact.{}".format(_quote_identifier(column))))
    return "jsonb_build_object({})".format(", ".join(arguments))


def _build_reference_query(month: str, event_type: str) -> str:
    if not re.fullmatch(r"[0-9]{6}", month) or event_type not in FACT_DEFINITIONS:
        raise ProbeError("引用查询月份或事件类型无效")
    definition = FACT_DEFINITIONS[event_type]
    event_table = "{}.{}".format(
        _quote_identifier("public"), _quote_identifier("event_table_{}".format(month))
    )
    fact_table_name = "{}_{}".format(definition["family"], month)
    fact_table = "{}.{}".format(_quote_identifier("public"), _quote_identifier(fact_table_name))
    problem_column = "fact.{}".format(_quote_identifier(definition["problem"]))
    if definition["problem"] == "prefix":
        problem_expression = "replace({}::text, '/', '-')".format(problem_column)
    else:
        problem_expression = "{}::text".format(problem_column)
    event_id_expression = "fact.{}::text".format(_quote_identifier(definition["event_id"]))
    native_key = _native_key_json(definition)
    return """
        WITH ref_base AS MATERIALIZED (
            SELECT event.detail_url,
                   event.event_type::text AS declared_event_type,
                   event.source::text AS event_source,
                   to_char(event.s_time, 'YYYY-MM-DD HH24:MI:SS') AS event_time_text,
                   split_part(event.detail_url, '/', 2) AS url_time_text,
                   split_part(event.detail_url, '/', 3) AS problem,
                   split_part(event.detail_url, '/', 4) AS event_id,
                   split_part(event.detail_url, '/', 5) AS source,
                   cardinality(string_to_array(event.detail_url, '/')) AS part_count
            FROM {event_table} AS event
            WHERE split_part(event.detail_url, '/', 1) = %(event_type)s
        ),
        refs AS MATERIALIZED (
            SELECT ref_base.*,
                   (part_count = 5
                    AND url_time_text ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}} [0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}$'
                    AND problem <> ''
                   AND event_id ~ '^[0-9]+$'
                   AND source <> ''
                   AND declared_event_type = %(declared_event_type)s
                   AND source = event_source) AS locator_valid
            FROM ref_base
        ),
        valid_refs AS MATERIALIZED (
            SELECT * FROM refs WHERE locator_valid
        ),
        facts AS MATERIALIZED (
            SELECT {problem_expression} AS problem,
                   {event_id_expression} AS event_id,
                   fact.source::text AS source,
                   to_char(fact.s_time, 'YYYY-MM-DD HH24:MI:SS') AS fact_time_text,
                   {native_key} AS native_key
            FROM {fact_table} AS fact
        ),
        match_summary AS MATERIALIZED (
            SELECT reference.detail_url,
                   reference.problem,
                   reference.event_id,
                   reference.source,
                   reference.url_time_text,
                   reference.event_time_text,
                   count(fact.native_key)::bigint AS match_count,
                   count(fact.native_key) FILTER (
                       WHERE fact.fact_time_text = reference.url_time_text
                         AND fact.fact_time_text = reference.event_time_text
                   )::bigint AS exact_time_match_count
            FROM valid_refs AS reference
            LEFT JOIN facts AS fact
              ON fact.problem = reference.problem
             AND fact.event_id = reference.event_id
             AND fact.source = reference.source
            GROUP BY reference.detail_url, reference.problem, reference.event_id,
                     reference.source, reference.url_time_text, reference.event_time_text
        ),
        reverse_summary AS MATERIALIZED (
            SELECT fact.native_key,
                   fact.problem,
                   fact.event_id,
                   fact.source,
                   fact.fact_time_text,
                   count(reference.detail_url)::bigint AS reference_count
            FROM facts AS fact
            LEFT JOIN valid_refs AS reference
              ON reference.problem = fact.problem
             AND reference.event_id = fact.event_id
             AND reference.source = fact.source
            GROUP BY fact.native_key, fact.problem, fact.event_id, fact.source, fact.fact_time_text
        ),
        duplicate_locators AS MATERIALIZED (
            SELECT problem, event_id, source, count(*)::bigint AS locator_count,
                   jsonb_agg(native_key ORDER BY native_key::text) AS native_keys
            FROM facts
            GROUP BY problem, event_id, source
            HAVING count(*) > 1
        ),
        duplicate_event_locators AS MATERIALIZED (
            SELECT problem, event_id, source, count(*)::bigint AS reference_count,
                   jsonb_agg(
                       jsonb_build_object(
                           'detail_url', detail_url,
                           'event_time', event_time_text
                       )
                       ORDER BY detail_url, event_time_text
                   ) AS event_references
            FROM valid_refs
            GROUP BY problem, event_id, source
            HAVING count(*) > 1
        ),
        issues AS (
            SELECT 10 AS priority, 'forward_missing'::text AS issue,
                   jsonb_build_object('detail_url', detail_url, 'problem', problem,
                                      'event_id', event_id, 'source', source) AS evidence
            FROM match_summary WHERE match_count = 0
            UNION ALL
            SELECT 20, 'forward_ambiguous',
                   jsonb_build_object('detail_url', detail_url, 'problem', problem,
                                      'event_id', event_id, 'source', source,
                                      'match_count', match_count)
            FROM match_summary WHERE match_count > 1
            UNION ALL
            SELECT 30, 'reverse_missing',
                   jsonb_build_object('native_key', native_key, 'fact_time', fact_time_text)
            FROM reverse_summary WHERE reference_count = 0
            UNION ALL
            SELECT 40, 'time_mismatch',
                   jsonb_build_object('detail_url', detail_url, 'url_time', url_time_text,
                                      'event_time', event_time_text,
                                      'match_count', match_count,
                                      'exact_time_match_count', exact_time_match_count)
            FROM match_summary
            WHERE url_time_text <> event_time_text
               OR (match_count > 0 AND exact_time_match_count <> match_count)
            UNION ALL
            SELECT 50, 'event_partition_mismatch',
                   jsonb_build_object('detail_url', detail_url, 'url_time', url_time_text,
                                      'event_time', event_time_text)
            FROM valid_refs
            WHERE replace(substr(url_time_text, 1, 7), '-', '') <> %(month)s
               OR replace(substr(event_time_text, 1, 7), '-', '') <> %(month)s
            UNION ALL
            SELECT 60, 'fact_partition_mismatch',
                   jsonb_build_object('native_key', native_key, 'fact_time', fact_time_text)
            FROM facts
            WHERE replace(substr(fact_time_text, 1, 7), '-', '') <> %(month)s
            UNION ALL
            SELECT 70, 'duplicate_locator',
                   jsonb_build_object('problem', problem, 'event_id', event_id,
                                      'source', source, 'locator_count', locator_count,
                                      'native_keys', native_keys)
            FROM duplicate_locators
            UNION ALL
            SELECT 80, 'duplicate_event_locator',
                   jsonb_build_object('problem', problem, 'event_id', event_id,
                                      'source', source, 'reference_count', reference_count,
                                      'event_references', event_references)
            FROM duplicate_event_locators
        )
        SELECT jsonb_build_object(
            'event_type', %(event_type)s,
            'fact_table', %(fact_table)s,
            'event_row_count', (SELECT count(*) FROM ref_base),
            'valid_locator_count', (SELECT count(*) FROM valid_refs),
            'malformed_locator_count', (SELECT count(*) FROM refs WHERE NOT locator_valid),
            'fact_row_count', (SELECT count(*) FROM facts),
            'forward_missing_count', (SELECT count(*) FROM match_summary WHERE match_count = 0),
            'forward_ambiguous_count', (SELECT count(*) FROM match_summary WHERE match_count > 1),
            'reverse_missing_count', (SELECT count(*) FROM reverse_summary WHERE reference_count = 0),
            'duplicate_locator_group_count', (SELECT count(*) FROM duplicate_locators),
            'duplicate_event_locator_group_count',
                (SELECT count(*) FROM duplicate_event_locators),
            'duplicate_event_locator_excess_count',
                (SELECT coalesce(sum(reference_count - 1), 0)
                 FROM duplicate_event_locators),
            'time_mismatch_count', (
                SELECT count(*) FROM match_summary
                WHERE url_time_text <> event_time_text
                   OR (match_count > 0 AND exact_time_match_count <> match_count)
            ),
            'partition_mismatch_count',
                (SELECT count(*) FROM valid_refs
                 WHERE replace(substr(url_time_text, 1, 7), '-', '') <> %(month)s
                    OR replace(substr(event_time_text, 1, 7), '-', '') <> %(month)s)
                +
                (SELECT count(*) FROM facts
                 WHERE replace(substr(fact_time_text, 1, 7), '-', '') <> %(month)s),
            'samples', coalesce((
                SELECT jsonb_agg(jsonb_build_object('issue', issue, 'evidence', evidence)
                                 ORDER BY priority, evidence::text)
                FROM (
                    SELECT priority, issue, evidence
                    FROM issues
                    ORDER BY priority, evidence::text
                    LIMIT %(sample_limit)s
                ) AS limited_issues
            ), '[]'::jsonb)
        )
    """.format(
        event_table=event_table,
        fact_table=fact_table,
        problem_expression=problem_expression,
        event_id_expression=event_id_expression,
        native_key=native_key,
    )


def _build_malformed_locator_query(month: str) -> str:
    if not re.fullmatch(r"[0-9]{6}", month):
        raise ProbeError("引用查询月份无效")
    event_table = "{}.{}".format(
        _quote_identifier("public"), _quote_identifier("event_table_{}".format(month))
    )
    accepted_types = ", ".join(_sql_literal(value) for value in EVENT_TYPES)
    declared_type_matches = " OR ".join(
        "(event_type = {event_type} AND declared_event_type = {declared_type})".format(
            event_type=_sql_literal(event_type),
            declared_type=_sql_literal(EVENT_TYPE_LABELS[event_type]),
        )
        for event_type in EVENT_TYPES
    )
    return """
        WITH parsed AS (
            SELECT detail_url,
                   event_type::text AS declared_event_type,
                   source::text AS event_source,
                   split_part(detail_url, '/', 1) AS event_type,
                   split_part(detail_url, '/', 2) AS url_time_text,
                   split_part(detail_url, '/', 3) AS problem,
                   split_part(detail_url, '/', 4) AS event_id,
                   split_part(detail_url, '/', 5) AS url_source,
                   cardinality(string_to_array(detail_url, '/')) AS part_count
            FROM {event_table}
        ),
        malformed AS (
            SELECT * FROM parsed
            WHERE NOT (
                part_count = 5
                AND event_type IN ({accepted_types})
                AND url_time_text ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}} [0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}$'
                AND problem <> ''
                AND event_id ~ '^[0-9]+$'
                AND url_source <> ''
                AND ({declared_type_matches})
                AND url_source = event_source
            )
        )
        SELECT jsonb_build_object(
            'count', (SELECT count(*) FROM malformed),
            'samples', coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'detail_url', detail_url,
                    'event_source', event_source,
                    'url_event_type', event_type,
                    'declared_event_type', declared_event_type
                ) ORDER BY detail_url)
                FROM (SELECT * FROM malformed ORDER BY detail_url LIMIT %(sample_limit)s) AS sample
            ), '[]'::jsonb)
        )
    """.format(
        event_table=event_table,
        accepted_types=accepted_types,
        declared_type_matches=declared_type_matches,
    )


REFERENCE_COUNT_FIELDS = (
    "forward_missing_count",
    "forward_ambiguous_count",
    "reverse_missing_count",
    "duplicate_locator_group_count",
    "duplicate_event_locator_group_count",
    "duplicate_event_locator_excess_count",
    "time_mismatch_count",
    "partition_mismatch_count",
)


def _read_reference_integrity(cursor: Any, months: list[str], sample_limit: int) -> dict[str, Any]:
    by_month = []
    totals = {field: 0 for field in REFERENCE_COUNT_FIELDS}
    totals["malformed_locator_count"] = 0
    for month in months:
        type_results = []
        month_samples = []
        month_totals = {field: 0 for field in REFERENCE_COUNT_FIELDS}
        cursor.execute(_build_malformed_locator_query(month), {"sample_limit": sample_limit})
        malformed = cursor.fetchone()[0]
        malformed_count = int(malformed["count"])
        month_samples.extend(
            {"event_type": None, "issue": "malformed_locator", "evidence": sample}
            for sample in malformed["samples"]
        )
        for event_type in EVENT_TYPES:
            definition = FACT_DEFINITIONS[event_type]
            fact_table = "{}_{}".format(definition["family"], month)
            cursor.execute(
                _build_reference_query(month, event_type),
                {
                    "event_type": event_type,
                    "declared_event_type": EVENT_TYPE_LABELS[event_type],
                    "month": month,
                    "fact_table": fact_table,
                    "sample_limit": sample_limit,
                },
            )
            result = cursor.fetchone()[0]
            normalized = _json_ready(result)
            type_results.append(normalized)
            for field in REFERENCE_COUNT_FIELDS:
                value = int(normalized[field])
                month_totals[field] += value
                totals[field] += value
            month_samples.extend(
                {"event_type": event_type, **sample} for sample in normalized["samples"]
            )
        totals["malformed_locator_count"] += malformed_count
        by_month.append(
            {
                "month": month,
                "malformed_locator_count": malformed_count,
                **month_totals,
                "types": type_results,
                "samples": month_samples[:sample_limit],
            }
        )
    return {"by_month": by_month, "totals": totals}


def _build_timeseries_coverage_query(
    table_names: list[str], *, collect_only: bool = False
) -> str:
    if not table_names:
        raise ProbeError("时序覆盖查询至少需要一张表")
    predicate = " WHERE {country} = 'collect'".format(
        country=_quote_identifier("country")
    ) if collect_only else ""
    observed_parts = [
        "SELECT {time_column} AS observed_at FROM {table}{predicate}".format(
            time_column=_quote_identifier("t"),
            table="{}.{}".format(_quote_identifier("public"), _quote_identifier(name)),
            predicate=predicate,
        )
        for name in table_names
    ]
    observed_union = "\nUNION\n".join(observed_parts)
    return """
        WITH expected AS MATERIALIZED (
            SELECT generate_series(
                %s::timestamp without time zone,
                %s::timestamp without time zone - interval '5 minutes',
                interval '5 minutes'
            ) AS expected_at
        ),
        observed_raw AS MATERIALIZED (
            {observed_union}
        ),
        observed AS MATERIALIZED (
            SELECT DISTINCT observed_at
            FROM observed_raw
            WHERE observed_at >= %s::timestamp without time zone
              AND observed_at < %s::timestamp without time zone
        ),
        missing AS MATERIALIZED (
            SELECT expected.expected_at
            FROM expected
            LEFT JOIN observed ON observed.observed_at = expected.expected_at
            WHERE observed.observed_at IS NULL
        ),
        missing_numbered AS MATERIALIZED (
            SELECT expected_at,
                   expected_at
                     - row_number() OVER (ORDER BY expected_at) * interval '5 minutes'
                     AS range_key
            FROM missing
        ),
        missing_ranges AS MATERIALIZED (
            SELECT min(expected_at) AS range_start,
                   max(expected_at) + interval '5 minutes' AS range_end_exclusive,
                   count(*)::bigint AS sample_count
            FROM missing_numbered
            GROUP BY range_key
        ),
        off_grid AS MATERIALIZED (
            SELECT observed.observed_at
            FROM observed
            LEFT JOIN expected ON expected.expected_at = observed.observed_at
            WHERE expected.expected_at IS NULL
        )
        SELECT jsonb_build_object(
            'expected_sample_count', (SELECT count(*) FROM expected),
            'raw_observed_timestamp_count', (SELECT count(*) FROM observed),
            'observed_sample_count', (
                SELECT count(*) FROM observed
                JOIN expected ON expected.expected_at = observed.observed_at
            ),
            'missing_sample_count', (SELECT count(*) FROM missing),
            'off_grid_sample_count', (SELECT count(*) FROM off_grid),
            'first_observed_at', (SELECT min(observed_at) FROM observed),
            'last_observed_at', (SELECT max(observed_at) FROM observed),
            'missing_ranges', coalesce((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'start', to_char(range_start, 'YYYY-MM-DD HH24:MI:SS'),
                        'end_exclusive', to_char(range_end_exclusive, 'YYYY-MM-DD HH24:MI:SS'),
                        'sample_count', sample_count
                    )
                    ORDER BY range_start
                )
                FROM missing_ranges
            ), '[]'::jsonb),
            'missing_samples', coalesce((
                SELECT jsonb_agg(to_char(expected_at, 'YYYY-MM-DD HH24:MI:SS') ORDER BY expected_at)
                FROM (
                    SELECT expected_at FROM missing ORDER BY expected_at LIMIT %(sample_limit)s
                ) AS sample
            ), '[]'::jsonb),
            'off_grid_samples', coalesce((
                SELECT jsonb_agg(to_char(observed_at, 'YYYY-MM-DD HH24:MI:SS') ORDER BY observed_at)
                FROM (
                    SELECT observed_at FROM off_grid ORDER BY observed_at LIMIT %(sample_limit)s
                ) AS sample
            ), '[]'::jsonb)
        )
    """.format(observed_union=observed_union)


def _build_timeseries_activity_query(table_names: list[str]) -> str:
    """对稀疏 ASN 活动表只统计实际活动时间，不推断应有槽。"""

    if not table_names:
        raise ProbeError("时序活动查询至少需要一张表")
    observed_parts = [
        "SELECT {time_column} AS observed_at FROM {table}".format(
            time_column=_quote_identifier("t"),
            table="{}.{}".format(_quote_identifier("public"), _quote_identifier(name)),
        )
        for name in table_names
    ]
    return """
        WITH activity_raw AS MATERIALIZED (
            {observed_union}
        ),
        activity AS MATERIALIZED (
            SELECT DISTINCT observed_at
            FROM activity_raw
            WHERE observed_at >= %s::timestamp without time zone
              AND observed_at < %s::timestamp without time zone
        )
        SELECT jsonb_build_object(
            'activity_timestamp_count', (SELECT count(*) FROM activity),
            'first_activity_at', (SELECT min(observed_at) FROM activity),
            'last_activity_at', (SELECT max(observed_at) FROM activity)
        )
    """.format(observed_union="\nUNION\n".join(observed_parts))


def _execute_timeseries_coverage_query(
    cursor: Any,
    *,
    query: str,
    start: datetime,
    end_exclusive: datetime,
    sample_limit: int,
) -> dict[str, Any]:
    # 将 LIMIT 作为已经验证上限的整数字面量写入，时间仍使用绑定参数。
    query = query.replace("%(sample_limit)s", str(sample_limit))
    cursor.execute(query, (start, end_exclusive, start, end_exclusive))
    row = cursor.fetchone()
    if row is None or not isinstance(row[0], dict):
        raise ProbeError("时序覆盖查询没有返回 JSON 对象")
    return _json_ready(row[0])


def _execute_timeseries_activity_query(
    cursor: Any,
    *,
    query: str,
    start: datetime,
    end_exclusive: datetime,
) -> dict[str, Any]:
    cursor.execute(query, (start, end_exclusive))
    row = cursor.fetchone()
    if row is None or not isinstance(row[0], dict):
        raise ProbeError("时序活动查询没有返回 JSON 对象")
    return _json_ready(row[0])


def _collect_timeseries_coverage(
    cursor: Any,
    *,
    profile: dict[str, Any],
    sample_limit: int,
) -> dict[str, Any]:
    months = _profile_months(profile)
    start = profile["parsed"]["start"].replace(tzinfo=None)
    end_exclusive = profile["parsed"]["end_exclusive"].replace(tzinfo=None)
    source_result = _execute_timeseries_coverage_query(
        cursor,
        query=_build_timeseries_coverage_query(["feature_country"], collect_only=True),
        start=start,
        end_exclusive=end_exclusive,
        sample_limit=sample_limit,
    )
    expected = int(source_result["expected_sample_count"])
    observed = int(source_result["observed_sample_count"])
    source_result.update(
        {
            "series_id": "feature_country.collect",
            "table_names": ["feature_country"],
            "subject_filter": {"country": "collect"},
            "granularity_seconds": 300,
            "coverage_ratio": _rate(observed, expected),
            "coverage_status": (
                "complete"
                if int(source_result["missing_sample_count"]) == 0
                and int(source_result["off_grid_sample_count"]) == 0
                else "observed_gap"
            ),
            "missing_reason": (
                None
                if int(source_result["missing_sample_count"]) == 0
                else "legacy_unknown"
            ),
        }
    )

    activity_specs = [
        (family, ["{}_{}".format(family, month) for month in months])
        for family in AS_FEATURE_FAMILIES
    ]
    activity_series = []
    for series_id, table_names in activity_specs:
        result = _execute_timeseries_activity_query(
            cursor,
            query=_build_timeseries_activity_query(table_names),
            start=start,
            end_exclusive=end_exclusive,
        )
        result.update(
            {
                "series_id": series_id,
                "table_names": table_names,
                "series_semantics": "subject_activity_sparse",
            }
        )
        activity_series.append(result)
    return {
        "granularity_seconds": 300,
        "source_series": [source_result],
        "activity_series": activity_series,
        "totals": {
            "source_series_count": 1,
            "source_series_with_missing_samples": int(
                int(source_result["missing_sample_count"]) > 0
            ),
            "source_series_with_off_grid_samples": int(
                int(source_result["off_grid_sample_count"]) > 0
            ),
            "source_missing_sample_count": int(source_result["missing_sample_count"]),
            "source_off_grid_sample_count": int(source_result["off_grid_sample_count"]),
            "activity_series_count": len(activity_series),
            "activity_timestamp_count": sum(
                int(item["activity_timestamp_count"]) for item in activity_series
            ),
        },
    }


def _check(check_id: str, passed: bool | None, summary: str, evidence: Any) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": "pending" if passed is None else "pass" if passed else "fail",
        "summary": summary,
        "evidence": _json_ready(evidence),
    }


def _quality_gate_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [item["check_id"] for item in checks if item["status"] == "fail"]
    pending = [item["check_id"] for item in checks if item["status"] == "pending"]
    return {
        "status": "fail" if failed else "pending" if pending else "pass",
        "blocking_failure_count": len(failed),
        "blocking_failures": failed,
        "pending_check_count": len(pending),
        "pending_checks": pending,
    }


def _build_checks(
    *,
    expected_tables: list[str],
    actual_tables: list[str],
    tables: list[dict[str, Any]],
    reference_integrity: dict[str, Any] | None,
    timeseries_coverage: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    missing = sorted(set(expected_tables) - set(actual_tables))
    extra = sorted(set(actual_tables) - set(expected_tables))
    out_of_window = sum(int(table["out_of_window_count"]) for table in tables)
    invalid_primary_keys = [
        table["name"]
        for table in tables
        if table["primary_key"] is None
        or not table["primary_key"]["validated"]
        or table["primary_key"]["backing_index_valid"] is not True
    ]
    duplicate_excess = sum(
        int(table["duplicate_basis"]["duplicate_excess_row_count"] or 0) for table in tables
    )
    unknown_duplicates = [
        table["name"]
        for table in tables
        if table["duplicate_basis"]["duplicate_excess_row_count"] is None
    ]
    missing_legacy_hash = [table["name"] for table in tables if table["legacy_schema_md5"] is None]
    checks = [
        _check("security.readonly", True, "reader 权限和只读事务验证通过。", None),
        _check(
            "tables.required",
            not missing and not extra,
            "固定数据档 public 表集合与预期一致。",
            {"expected_count": len(expected_tables), "actual_count": len(actual_tables), "missing": missing, "extra": extra},
        ),
        _check(
            "tables.window",
            out_of_window == 0,
            "固定窗口外记录数必须为 0。",
            {"out_of_window_count": out_of_window},
        ),
        _check(
            "tables.primary_key_and_duplicates",
            not invalid_primary_keys and not unknown_duplicates and duplicate_excess == 0,
            "所有表必须有有效主键且重复记录数为 0。",
            {
                "invalid_primary_keys": invalid_primary_keys,
                "unknown_duplicate_tables": unknown_duplicates,
                "duplicate_excess_row_count": duplicate_excess,
            },
        ),
        _check(
            "schema.legacy_inventory_link",
            not missing_legacy_hash,
            "每张目标表均可定位到来源 inventory 的 legacy schema MD5。",
            {"missing_tables": missing_legacy_hash},
        ),
    ]
    if timeseries_coverage is None:
        checks.append(
            _check(
                "timeseries.coverage_measured",
                False,
                "目标特征表集合不完整，未执行时序采样覆盖检查。",
                None,
            )
        )
    else:
        coverage_totals = timeseries_coverage["totals"]
        checks.extend(
            [
                _check(
                    "timeseries.coverage_measured",
                    coverage_totals["source_series_with_off_grid_samples"] == 0,
                    "feature_country collect 源时序的 expected、observed 与 missing 五分钟槽已精确计量，且时间点必须位于网格。",
                    coverage_totals,
                ),
                _check(
                    "timeseries.unclassified_missing",
                    True
                    if coverage_totals["source_series_with_missing_samples"] == 0
                    else None,
                    "源时序覆盖缺口本身不阻断历史兼容；缺失原因由 baseline 联合原始制品清单归类。ASN 活动稀疏表不参与覆盖判定。",
                    {
                        "source_series_with_missing_samples": coverage_totals[
                            "source_series_with_missing_samples"
                        ],
                        "source_missing_sample_count": coverage_totals[
                            "source_missing_sample_count"
                        ],
                    },
                ),
            ]
        )
    if reference_integrity is None:
        checks.append(
            _check(
                "references.complete",
                False,
                "目标表集合不完整，未执行六类双向引用检查。",
                None,
            )
        )
        return checks
    totals = reference_integrity["totals"]
    checks.extend(
        [
            _check(
                "references.locator_format",
                totals["malformed_locator_count"] == 0,
                "事件总表 locator 格式必须全部有效。",
                {"count": totals["malformed_locator_count"]},
            ),
            _check(
                "references.forward",
                totals["forward_missing_count"] == 0,
                "总表到事实表的悬空引用必须为 0。",
                {"count": totals["forward_missing_count"]},
            ),
            _check(
                "references.ambiguity",
                totals["forward_ambiguous_count"] == 0
                and totals["duplicate_locator_group_count"] == 0
                and totals["duplicate_event_locator_group_count"] == 0,
                "总表 locator 多行匹配、事实重复 locator 和总表重复 locator 必须为 0。",
                {
                    "forward_ambiguous_count": totals["forward_ambiguous_count"],
                    "duplicate_locator_group_count": totals["duplicate_locator_group_count"],
                    "duplicate_event_locator_group_count": totals[
                        "duplicate_event_locator_group_count"
                    ],
                    "duplicate_event_locator_excess_count": totals[
                        "duplicate_event_locator_excess_count"
                    ],
                },
            ),
            _check(
                "references.reverse",
                totals["reverse_missing_count"] == 0,
                "事实表到事件总表的未解释孤儿必须为 0。",
                {"count": totals["reverse_missing_count"]},
            ),
            _check(
                "references.time_partition",
                totals["time_mismatch_count"] == 0
                and totals["partition_mismatch_count"] == 0,
                "locator、总表、事实表时间及月份必须一致。",
                {
                    "time_mismatch_count": totals["time_mismatch_count"],
                    "partition_mismatch_count": totals["partition_mismatch_count"],
                },
            ),
        ]
    )
    return checks


def _git_provenance(
    project_root: Path, *, probe_path: Path, data_profile_path: Path
) -> dict[str, Any]:
    if project_root.is_symlink() or not project_root.is_dir():
        raise ProbeError("项目根目录必须存在且禁止软链接：{}".format(project_root))
    try:
        resolved_root = project_root.resolve(strict=True)
    except OSError as error:
        raise ProbeError("无法解析项目根目录：{}".format(project_root)) from error
    _assert_regular_file(probe_path, "P0 探针程序")
    _assert_regular_file(data_profile_path, "唯一数据档")
    data_profile_loader_path = resolved_root / "dev" / "data_profile.py"
    _assert_regular_file(data_profile_loader_path, "数据档加载器")
    try:
        git_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(resolved_root),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout.strip()
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(resolved_root),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout.strip()
        status_output = subprocess.run(
            ["git", "-c", "core.quotePath=false", "status", "--porcelain=v1"],
            cwd=str(resolved_root),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ProbeError("无法读取 Git provenance") from error
    try:
        resolved_git_root = Path(git_root).resolve(strict=True)
    except OSError as error:
        raise ProbeError("Git 项目根目录无法解析") from error
    if resolved_git_root != resolved_root:
        raise ProbeError("--project-root 必须是 Git 顶层目录")
    return {
        "project_root": str(resolved_root),
        "git_sha": sha,
        "git_dirty": bool(status_output),
        "git_status_sha256": hashlib.sha256(status_output.encode("utf-8")).hexdigest(),
        "probe_path": str(probe_path.absolute()),
        "probe_sha256": _sha256(probe_path),
        "data_profile_path": str(data_profile_path.absolute()),
        "data_profile_sha256": _sha256(data_profile_path),
        "data_profile_loader_sha256": _sha256(data_profile_loader_path),
    }


def probe_database(
    connection: Any,
    *,
    profile: dict[str, Any],
    context: dict[str, Any],
    database_config: dict[str, str],
    provenance: dict[str, Any],
    sample_limit: int,
    statement_timeout_ms: int,
) -> dict[str, Any]:
    if not 1 <= sample_limit <= 100:
        raise ProbeError("sample-limit 必须位于 1 至 100")
    expected_tables = _expected_tables(profile)
    cursor = connection.cursor()
    query_completed_at = None
    try:
        _begin_readonly_transaction(cursor, statement_timeout_ms)
        security = _verify_reader_security(
            cursor,
            expected_user=database_config["DOMEYE_CORE_DB_READER_USER"],
            expected_database=database_config["DOMEYE_CORE_DB_NAME"],
            expected_system=context["system_identifier"],
        )
        relations = _read_relations(cursor)
        actual_tables = [relation["name"] for relation in relations]
        relation_by_name = {relation["name"]: relation for relation in relations}
        timescale = _read_timescale_catalog(cursor)
        tables = []
        start = profile["parsed"]["start"].replace(tzinfo=None)
        end_exclusive = profile["parsed"]["end_exclusive"].replace(tzinfo=None)
        for name in expected_tables:
            relation = relation_by_name.get(name)
            if relation is None:
                continue
            table = _read_table_catalog(cursor, relation, timescale)
            table.pop("oid", None)
            table["legacy_schema_md5"] = context["legacy_schema_hashes"].get(name)
            _apply_table_stats(cursor, table, start=start, end_exclusive=end_exclusive)
            _apply_duplicate_stats(cursor, table)
            tables.append(table)

        complete_table_set = set(expected_tables) == set(actual_tables)
        reference_integrity = (
            _read_reference_integrity(cursor, _profile_months(profile), sample_limit)
            if complete_table_set
            else None
        )
        timeseries_coverage = (
            _collect_timeseries_coverage(cursor, profile=profile, sample_limit=sample_limit)
            if complete_table_set
            else None
        )
        cursor.execute("SELECT clock_timestamp()")
        query_completed_at = _json_ready(cursor.fetchone()[0])
        checks = _build_checks(
            expected_tables=expected_tables,
            actual_tables=actual_tables,
            tables=tables,
            reference_integrity=reference_integrity,
            timeseries_coverage=timeseries_coverage,
        )
        quality_gate = _quality_gate_summary(checks)
        raw_profile = {key: profile[key] for key in PROFILE_RAW_KEYS}
        payload = {
            "schema_version": 1,
            "probe_kind": "p0_quality_probe",
            "data_profile": raw_profile,
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
                    "server_version": security["server_version"],
                    "system_identifier": security["system_identifier"],
                },
                "current_user": security["current_user"],
                "transaction_read_only": security["transaction_read_only"],
                "default_transaction_read_only": security["default_transaction_read_only"],
                "transaction_isolation": security["transaction_isolation"],
                "query_started_at": security["query_started_at"],
                "query_completed_at": query_completed_at,
                **provenance,
            },
            "tables": tables,
            "timeseries_coverage": timeseries_coverage,
            "reference_integrity": reference_integrity,
            "checks": checks,
            "quality_gate": quality_gate,
            # 保留顶层计数供旧消费者过渡；验收必须使用 quality_gate.status。
            "blocking_failure_count": quality_gate["blocking_failure_count"],
            "pending_check_count": quality_gate["pending_check_count"],
        }
        payload["result_fingerprint_sha256"] = _canonical_sha256(
            {
                "schema_version": payload["schema_version"],
                "probe_kind": payload["probe_kind"],
                "data_profile": payload["data_profile"],
                "release_id": context["release_id"],
                "inventory_sha256": context["inventory_sha256"],
                "provenance": {
                    key: provenance[key]
                    for key in (
                        "git_sha",
                        "git_dirty",
                        "git_status_sha256",
                        "probe_sha256",
                        "data_profile_sha256",
                        "data_profile_loader_sha256",
                    )
                },
                "tables": payload["tables"],
                "timeseries_coverage": payload["timeseries_coverage"],
                "reference_integrity": payload["reference_integrity"],
                "checks": payload["checks"],
            }
        )
        return _json_ready(payload)
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        if query_completed_at is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        try:
            cursor.close()
        except Exception:
            pass


def _connect_database(config: dict[str, str], context: dict[str, Any], connect_timeout: int) -> Any:
    if not 1 <= connect_timeout <= 30:
        raise ProbeError("connect-timeout 必须位于 1 至 30 秒")
    try:
        import psycopg2
    except ImportError as error:
        raise ProbeError("缺少 psycopg2；请使用 backend 的冻结 Python 环境执行") from error
    return psycopg2.connect(
        database=config["DOMEYE_CORE_DB_NAME"],
        user=config["DOMEYE_CORE_DB_READER_USER"],
        password=config["DOMEYE_CORE_DB_READER_PASSWORD"],
        host=LOOPBACK_HOST,
        port=context["port"],
        connect_timeout=connect_timeout,
        application_name="domeye_p0_readonly_probe",
        options="-c default_transaction_read_only=on",
    )


def _assert_no_symlink_ancestors(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if not current.exists() and not current.is_symlink():
            continue
        if current.is_symlink():
            raise ProbeError("输出路径祖先不能是软链接：{}".format(current))


def _write_json(payload: dict[str, Any], output: str) -> None:
    serialized = json.dumps(
        _json_ready(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if output == "-":
        sys.stdout.write(serialized)
        return
    path = Path(output)
    _assert_no_symlink_ancestors(path)
    if path.exists() or path.is_symlink():
        raise ProbeError("输出文件已存在，拒绝覆盖：{}".format(path))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    _assert_no_symlink_ancestors(path.parent)
    temporary = path.with_name(".{}.tmp.{}".format(path.name, os.getpid()))
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as error:
            raise ProbeError("输出文件在写入期间出现，拒绝覆盖：{}".format(path)) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成固定二三月数据库的只读 P0 质量探针")
    parser.add_argument("--database-env", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output", required=True, help="输出 JSON 路径，使用 - 输出到标准输出")
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--statement-timeout-ms", type=int, default=900_000)
    parser.add_argument("--connect-timeout", type=int, default=5)
    arguments = parser.parse_args(argv)
    connection = None
    try:
        try:
            project_root = arguments.project_root.resolve(strict=True)
        except OSError as error:
            raise ProbeError("无法解析项目根目录：{}".format(arguments.project_root)) from error
        data_profile_path = project_root / "config" / "data-profile.json"
        provenance = _git_provenance(
            project_root,
            probe_path=PROBE_PATH,
            data_profile_path=data_profile_path,
        )
        profile = _load_project_data_profile(project_root)
        context = _validate_release_context(
            profile=profile,
            state_path=arguments.state,
            release_dir=arguments.release_dir,
        )
        database_config = _read_database_env(arguments.database_env)
        connection = _connect_database(database_config, context, arguments.connect_timeout)
        payload = probe_database(
            connection,
            profile=profile,
            context=context,
            database_config=database_config,
            provenance=provenance,
            sample_limit=arguments.sample_limit,
            statement_timeout_ms=arguments.statement_timeout_ms,
        )
        _write_json(payload, arguments.output)
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
