#!/usr/bin/env python3
"""构建并验收 RRC25 224-310 S6 影子迁移与安全边界候选。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from read_model_candidate import (
    CandidateError,
    Postgres,
    ReadModelRuntime,
    canonical_bytes,
    read_tsv_gzip,
    run_command,
    sha256_bytes,
    sha256_file,
    sql_literal,
    stable_id,
    utc_now,
    verify_candidate as verify_s5_candidate,
    write_json_create_only,
    write_tsv_gzip,
)


COLLECTOR_ID = "rrc25"
LEGACY_SOURCE_CODE = "r"
WINDOW_START = "2026-02-24T00:00:00Z"
WINDOW_END = "2026-03-11T00:00:00Z"
STATE_POINT_COUNT = 4320
COUNTRY_BUCKET_COUNT = 241
LEGACY_TABLE_COUNT = 37
LEGACY_COUNTRY_COUNT = 111
RECONCILED_COUNTRY_COUNT = 81
TRACE_ONLY_COUNTRY_COUNT = 30
EXPECTED_LEGACY_SCHEMA_SHA256 = "1b823a9a236f416f6142fc773076f014e1403c243e0b33e3d6a7c39655e88af9"
SCHEMA_VERSION = "rrc25-shadow-migration/v1"
DATABASE_MODEL = "domeye-shadow-migration-postgresql/v1"
DATABASE_RE = re.compile(r"^domeye_dl_s6_[a-z0-9_]{1,47}$")
IMPLEMENTATION_RE = re.compile(r"^[0-9a-f]{40}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


def _table_specs() -> tuple[dict[str, Any], ...]:
    specs: list[dict[str, Any]] = []

    def add_pair(family: str, time_column: str, pk: Sequence[str], disposition: str) -> None:
        for month in ("202602", "202603"):
            specs.append({
                "table": f"{family}_{month}", "family": family,
                "time_column": time_column, "pk": list(pk),
                "disposition": disposition,
            })

    add_pair("as_outage", "s_time", ("source", "asn", "outage_id", "s_time"),
             "trace_only_semantic_adapter_required")
    add_pair("country_outage", "s_time", ("source", "country", "outage_id", "s_time"),
             "country_outage_identity_reconciled_or_trace_only")
    add_pair("event_table", "s_time", ("source", "event_type", "s_time", "detail_url"),
             "trace_only_aggregate_index_not_authoritative_fact")
    specs.append({
        "table": "feature_country", "family": "feature_country", "time_column": "t",
        "pk": ["t", "source", "country"],
        "disposition": "trace_only_legacy_metric_semantics_not_comparable",
    })
    for country in ("au", "br", "cn", "de", "gb", "id", "in", "other", "pl", "ru", "us"):
        add_pair(
            f"feature_{country}", "t", ("t", "source", "asn", "country"),
            "trace_only_legacy_metric_semantics_not_comparable",
        )
    add_pair("hijack", "s_time", ("source", "prefix", "hijack_eventid", "s_time"),
             "trace_only_outside_country_outage_contract")
    add_pair("leak_event", "s_time", ("source", "prefix", "leak_event_id", "s_time", "leak_vp"),
             "trace_only_outside_country_outage_contract")
    add_pair("prefix_outage", "s_time", ("source", "prefix", "outage_id", "s_time"),
             "trace_only_outside_country_outage_contract")
    add_pair("sub_hijack", "s_time", ("source", "prefix", "sub_hijack_eventid", "s_time"),
             "trace_only_outside_country_outage_contract")
    if len(specs) != LEGACY_TABLE_COUNT:
        raise AssertionError(f"legacy table spec count={len(specs)}")
    return tuple(sorted(specs, key=lambda row: row["table"]))


OLD_TABLE_SPECS = _table_specs()
OLD_TABLE_NAMES = tuple(row["table"] for row in OLD_TABLE_SPECS)

SNAPSHOT_COLUMNS = (
    "candidate_id", "import_batch_id", "snapshot_id", "source_database", "source_table",
    "semantic_family", "source_time_column", "source_time_semantics",
    "source_pk_fields", "has_declared_primary_key", "scope_row_count",
    "min_source_time_utc", "max_source_time_utc",
    "multiset_fingerprint_sha256", "schema_fragment_sha256", "disposition",
    "payload",
)
LEGACY_COLUMNS = (
    "candidate_id", "import_batch_id", "source_record_id", "source_table", "source_primary_key",
    "legacy_reference", "source_code", "collector_id", "country_code",
    "outage_id", "source_start_time_local", "normalized_start_time_utc",
    "normalized_end_time_utc", "source_row_sha256", "import_disposition",
    "unified_incident_id", "payload",
)
SOURCE_FIELD_COLUMNS = (
    "candidate_id", "import_batch_id", "reconciliation_id", "source_table",
    "source_field", "standardized_field", "comparison", "reason_code",
    "disposition_status", "source_row_count", "unified_population", "payload",
)
RECONCILIATION_COLUMNS = (
    "candidate_id", "reconciliation_id", "source_record_id", "field_name",
    "legacy_value", "unified_value", "comparison", "reason_code",
    "disposition_status",
)
OBJECT_COLUMNS = (
    "candidate_id", "object_id", "object_kind", "object_state",
    "path_identity", "dataset_id", "content_sha256", "runtime_readable",
    "retention_policy", "payload",
)
REFERENCE_COLUMNS = (
    "candidate_id", "reference_id", "object_id", "reference_kind",
    "reference_source_id", "purpose",
)
BUNDLE_COLUMNS = (
    "candidate_id", "bundle_id", "bundle_mode", "bundle_state",
    "content_sha256", "coherent_components", "payload",
)
BUNDLE_OBJECT_COLUMNS = (
    "candidate_id", "bundle_id", "object_id", "purpose",
)


def json_null(value: Any) -> Any:
    return {"value": value}


def pg_dump_schema_sha256(database: Postgres) -> str:
    result = run_command([
        "docker", "exec", database.container, "pg_dump", "-U", database.user,
        "-d", database.database, "--schema-only", "--no-owner", "--no-privileges",
    ])
    return sha256_bytes(result.stdout)


def exact_manifest(root: Path) -> tuple[dict[str, Any], str]:
    manifest_path = root / "manifest.json"
    complete_path = root / "COMPLETE.json"
    if not manifest_path.is_file() or not complete_path.is_file():
        raise CandidateError(f"来源缺少 manifest/COMPLETE：{root}")
    raw = manifest_path.read_bytes()
    if raw != complete_path.read_bytes():
        raise CandidateError(f"来源 manifest/COMPLETE 不逐字一致：{root}")
    value = json.loads(raw)
    if value.get("status") != "complete":
        raise CandidateError(f"来源不是 complete：{root}")
    return value, sha256_bytes(raw)


def source_manifests(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    roots = {
        "s1": Path(args.s1_root), "s2": Path(args.s2_root),
        "s3": Path(args.s3_root), "s4": Path(args.s4_root),
        "s5": Path(args.s5_root),
    }
    expected_versions = {
        "s1": "rrc25-route-event-store/v1", "s2": "rrc25-route-state-store/v1",
        "s3": "rrc25-route-metric-store/v1", "s4": "rrc25-event-publication-store/v1",
        "s5": "rrc25-read-model-store/v1",
    }
    result: dict[str, dict[str, Any]] = {}
    for stage, root in roots.items():
        manifest, manifest_sha = exact_manifest(root)
        if (
            manifest.get("schema_version") != expected_versions[stage]
            or manifest.get("collector_id") != COLLECTOR_ID
            or manifest.get("window_start_utc") != WINDOW_START
            or manifest.get("window_end_exclusive_utc") != WINDOW_END
            or not SHA_RE.fullmatch(str(manifest.get("content_sha256", "")))
        ):
            raise CandidateError(f"{stage.upper()} 来源范围或身份冲突")
        result[stage] = {
            "root": str(root), "manifest": manifest,
            "manifest_sha256": manifest_sha,
        }
    s1, s2, s3, s4, s5 = (result[key]["manifest"] for key in ("s1", "s2", "s3", "s4", "s5"))
    candidate_id = s3.get("candidate_id")
    if not candidate_id or s4.get("candidate_id") != candidate_id or s5.get("candidate_id") != candidate_id:
        raise CandidateError("S3/S4/S5 端到端 candidate 身份不一致")
    if (
        s2.get("source_route_event_dataset_id") != s1.get("dataset_id")
        or s3.get("source_route_event_dataset_id") != s1.get("dataset_id")
        or s3.get("source_route_state_dataset_id") != s2.get("dataset_id")
        or s4.get("source_metric_dataset_id") != s3.get("dataset_id")
        or s5.get("source_event_publication_dataset_id") != s4.get("dataset_id")
        or s5.get("source_route_state_dataset_id") != s2.get("dataset_id")
    ):
        raise CandidateError("S1 至 S5 dataset 血缘不闭合")
    for stage in (s2, s3, s4, s5):
        if stage.get("state_point_count") != STATE_POINT_COUNT:
            raise CandidateError("S2 至 S5 状态点人口漂移")
    for stage in (s3, s4, s5):
        if stage.get("country_bucket_count") != COUNTRY_BUCKET_COUNT:
            raise CandidateError("S3 至 S5 国家桶人口漂移")
    verify_s5_candidate(roots["s5"], deep_evidence=False)
    result["candidate_id"] = candidate_id
    return result


def database_source_evidence(args: argparse.Namespace, sources: Mapping[str, Any]) -> dict[str, Any]:
    s3 = Postgres(args.container, args.s3_database, args.user)
    s4 = Postgres(args.container, args.s4_database, args.user)
    s5 = Postgres(args.container, args.s5_database, args.user)
    s3_row = json.loads(s3.scalar("""
SELECT json_build_object(
 'candidate_id',(SELECT candidate_id FROM domeye_data.candidate_registry WHERE status='complete'),
 'country_rows',(SELECT count(*) FROM domeye_data.route_metric_5m WHERE subject_type='country'),
 'slot_count',(SELECT count(*) FROM domeye_data.metric_slot_5m),
 'gap_count',(SELECT count(*) FROM domeye_data.quality_gap),
 'fingerprint',(SELECT database_fingerprint_sha256 FROM domeye_data.load_receipt WHERE status='complete')
)
"""))
    s4_row = json.loads(s4.scalar("""
SELECT json_build_object(
 'candidate_id',(SELECT candidate_id FROM domeye_event.candidate_registry WHERE status='complete'),
 'incident_count',(SELECT count(*) FROM domeye_event.incident),
 'fact_count',(SELECT count(*) FROM domeye_event.event_fact),
 'pointer_count',(SELECT count(*) FROM domeye_event.publication_pointer),
 'fingerprint',(SELECT database_fingerprint_sha256 FROM domeye_event.load_receipt WHERE status='complete')
)
"""))
    s5_row = json.loads(s5.scalar("""
SELECT json_build_object(
 'candidate_id',(SELECT candidate_id FROM domeye_read.candidate_registry WHERE status='complete'),
 'event_count',(SELECT count(*) FROM domeye_read.event_read_model),
 'report_count',(SELECT count(*) FROM domeye_read.report_snapshot),
 'pointer_count',(SELECT count(*) FROM domeye_read.report_pointer),
 'fingerprint',(SELECT database_fingerprint_sha256 FROM domeye_read.load_receipt WHERE status='complete')
)
"""))
    candidate_id = sources["candidate_id"]
    if s3_row != {
        "candidate_id": candidate_id, "country_rows": 1041120,
        "slot_count": 4320, "gap_count": 0,
        "fingerprint": "cb016daaa263c3a184e09b1d8b3ef0b46745acab96342463428d0e0627761a2c",
    }:
        raise CandidateError(f"S3 数据库证据不闭合：{s3_row}")
    if s4_row != {
        "candidate_id": candidate_id, "incident_count": 81, "fact_count": 243,
        "pointer_count": 81,
        "fingerprint": "d9531ce36db3049fefe09adb3957665a1257b9d83b0f47339e69719501c907a8",
    }:
        raise CandidateError(f"S4 数据库证据不闭合：{s4_row}")
    if s5_row != {
        "candidate_id": candidate_id, "event_count": 81, "report_count": 82,
        "pointer_count": 81,
        "fingerprint": "ad93e7c9319c42e22105f8bfe6d755956b07759c018443d8ab6dca6d013211b6",
    }:
        raise CandidateError(f"S5 数据库证据不闭合：{s5_row}")
    return {"s3": s3_row, "s4": s4_row, "s5": s5_row}


def _hex_json_lines(result: subprocess.CompletedProcess[bytes], prefix: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    marker = prefix + "\t"
    for line in result.stdout.decode().splitlines():
        if not line.startswith(marker):
            continue
        values.append(json.loads(bytes.fromhex(line[len(marker):]).decode()))
    return values


def _schema_metadata(database: Postgres) -> dict[str, Any]:
    names = ",".join(sql_literal(name) for name in OLD_TABLE_NAMES)
    value = json.loads(database.scalar(f"""
SELECT json_build_object(
 'columns',(SELECT json_agg(json_build_object(
   'table_name',table_name,'ordinal_position',ordinal_position,
   'column_name',column_name,'data_type',data_type,'is_nullable',is_nullable,
   'column_default',column_default
 ) ORDER BY table_name,ordinal_position)
 FROM information_schema.columns
 WHERE table_schema='public' AND table_name IN ({names})),
 'constraints',(SELECT coalesce(json_agg(json_build_object(
   'table_name',c.relname,'constraint_name',x.conname,
   'constraint_type',x.contype,'definition',pg_get_constraintdef(x.oid)
 ) ORDER BY c.relname,x.conname),'[]'::json)
 FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid
 WHERE c.relnamespace='public'::regnamespace AND c.relname IN ({names})),
 'indexes',(SELECT coalesce(json_agg(json_build_object(
   'table_name',tablename,'index_name',indexname,'definition',indexdef
 ) ORDER BY tablename,indexname),'[]'::json)
 FROM pg_indexes WHERE schemaname='public' AND tablename IN ({names}))
)
"""))
    found = {row["table_name"] for row in value["columns"]}
    if found != set(OLD_TABLE_NAMES):
        raise CandidateError(f"旧库 37 表集合漂移：missing={sorted(set(OLD_TABLE_NAMES)-found)}")
    return value


def capture_legacy_snapshot(database: Postgres) -> dict[str, Any]:
    schema_sha = pg_dump_schema_sha256(database)
    if schema_sha != EXPECTED_LEGACY_SCHEMA_SHA256:
        raise CandidateError(f"旧库 schema SHA-256 漂移：{schema_sha}")
    metadata = _schema_metadata(database)
    columns_by_table: dict[str, list[dict[str, Any]]] = {name: [] for name in OLD_TABLE_NAMES}
    constraints_by_table: dict[str, list[dict[str, Any]]] = {name: [] for name in OLD_TABLE_NAMES}
    indexes_by_table: dict[str, list[dict[str, Any]]] = {name: [] for name in OLD_TABLE_NAMES}
    for row in metadata["columns"]:
        columns_by_table[row["table_name"]].append(row)
    for row in metadata["constraints"]:
        constraints_by_table[row["table_name"]].append(row)
    for row in metadata["indexes"]:
        indexes_by_table[row["table_name"]].append(row)

    statements = ["BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;"]
    statements.append("""
SELECT 'M'||E'\\t'||encode(convert_to(jsonb_build_object(
 'transaction_snapshot',txid_current_snapshot(),
 'wal_lsn',pg_current_wal_lsn()::text,
 'database_name',current_database(),
 'server_version',current_setting('server_version')
)::text,'UTF8'),'hex');
""")
    for spec in OLD_TABLE_SPECS:
        table = spec["table"]
        time_column = spec["time_column"]
        statements.append(f"""
WITH scoped AS MATERIALIZED (
 SELECT ({time_column} AT TIME ZONE 'Asia/Shanghai') AS normalized_time,
        hashtextextended(to_jsonb(source_row)::text,0) AS row_hash
   FROM public.{table} source_row
  WHERE source='r'
    AND ({time_column} AT TIME ZONE 'Asia/Shanghai') >= TIMESTAMPTZ '{WINDOW_START}'
    AND ({time_column} AT TIME ZONE 'Asia/Shanghai') < TIMESTAMPTZ '{WINDOW_END}'
), summary AS (
 SELECT count(*) AS row_count,min(normalized_time) AS min_time,max(normalized_time) AS max_time,
        coalesce(sum(row_hash::numeric),0)::text AS hash_sum,
        coalesce(sum(row_hash::numeric*row_hash::numeric),0)::text AS hash_square_sum,
        min(row_hash) AS hash_min,max(row_hash) AS hash_max
   FROM scoped
)
SELECT 'S'||E'\\t'||encode(convert_to(jsonb_build_object(
 'source_table','{table}','row_count',row_count,'min_time_utc',min_time,
 'max_time_utc',max_time,'hash_sum',hash_sum,'hash_square_sum',hash_square_sum,
 'hash_min',hash_min,'hash_max',hash_max
)::text,'UTF8'),'hex') FROM summary;
""")
        if table.startswith("country_outage_"):
            statements.append(f"""
SELECT 'C'||E'\\t'||encode(convert_to(to_jsonb(source_row)::text,'UTF8'),'hex')
  FROM public.{table} source_row
 WHERE source='r'
   AND (s_time AT TIME ZONE 'Asia/Shanghai') >= TIMESTAMPTZ '{WINDOW_START}'
   AND (s_time AT TIME ZONE 'Asia/Shanghai') < TIMESTAMPTZ '{WINDOW_END}'
 ORDER BY s_time,country,outage_id;
""")
    statements.append("COMMIT;")
    result = run_command(
        [
            "docker", "exec", "-i", database.container, "psql", "-X",
            "-v", "ON_ERROR_STOP=1", "-U", database.user, "-d", database.database,
            "-A", "-t", "-f", "-",
        ],
        input_bytes="\n".join(statements).encode(),
    )
    meta_rows = _hex_json_lines(result, "M")
    snapshot_rows = _hex_json_lines(result, "S")
    country_rows = _hex_json_lines(result, "C")
    if len(meta_rows) != 1 or len(snapshot_rows) != LEGACY_TABLE_COUNT:
        raise CandidateError("旧库一致性快照结果人口错误")
    if len(country_rows) != LEGACY_COUNTRY_COUNT:
        raise CandidateError(f"旧 country_outage 窗口人口不是 111：{len(country_rows)}")

    by_name = {row["source_table"]: row for row in snapshot_rows}
    normalized: list[dict[str, Any]] = []
    for spec in OLD_TABLE_SPECS:
        table = spec["table"]
        row = by_name[table]
        fragment = {
            "columns": columns_by_table[table],
            "constraints": constraints_by_table[table],
            "indexes": indexes_by_table[table],
        }
        hash_stats = {
            "row_count": row["row_count"], "min_time_utc": row["min_time_utc"],
            "max_time_utc": row["max_time_utc"], "hash_sum": row["hash_sum"],
            "hash_square_sum": row["hash_square_sum"], "hash_min": row["hash_min"],
            "hash_max": row["hash_max"],
        }
        normalized.append({
            "source_table": table, "semantic_family": spec["family"],
            "source_time_column": spec["time_column"],
            "source_time_semantics": "timestamp_without_time_zone_as_Asia/Shanghai_then_UTC",
            "source_pk_fields": spec["pk"],
            "has_declared_primary_key": any(
                item["constraint_type"] == "p" for item in fragment["constraints"]
            ),
            "scope_row_count": row["row_count"],
            "min_source_time_utc": row["min_time_utc"],
            "max_source_time_utc": row["max_time_utc"],
            "multiset_fingerprint_sha256": sha256_bytes(canonical_bytes(hash_stats)),
            "schema_fragment_sha256": sha256_bytes(canonical_bytes(fragment)),
            "disposition": spec["disposition"],
            "payload": {
                "normalized_collector_id": COLLECTOR_ID,
                "legacy_source_code": LEGACY_SOURCE_CODE,
                "window_start_utc": WINDOW_START,
                "window_end_exclusive_utc": WINDOW_END,
                "hash_method": "count+sum(hashtextextended)+sum(square)+min+max",
                "hash_statistics": hash_stats,
                "schema_fragment": fragment,
            },
        })
    snapshot_set_sha = sha256_bytes(canonical_bytes({
        "legacy_database_schema_sha256": schema_sha,
        "tables": [{key: row[key] for key in (
            "source_table", "scope_row_count", "multiset_fingerprint_sha256",
            "schema_fragment_sha256", "source_time_semantics",
        )} for row in normalized],
    }))
    return {
        "meta": meta_rows[0], "legacy_schema_sha256": schema_sha,
        "source_table_snapshots": normalized, "country_rows": country_rows,
        "source_snapshot_set_sha256": snapshot_set_sha,
    }


def export_s4_incidents(database: Postgres) -> dict[str, dict[str, Any]]:
    result = database.psql("""
COPY (
 SELECT jsonb_build_object(
   'incident_id',incident_id,'legacy_reference',legacy_reference,
   'country_code',country_code,
   'legacy_event_time_utc',to_char(legacy_event_time_utc AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
   'source_code',source_code,'collector_id',collector_id,
   'window_start_utc',to_char(window_start_utc AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
   'window_end_exclusive_utc',to_char(window_end_exclusive_utc AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')
 ) FROM domeye_event.incident ORDER BY legacy_reference
) TO STDOUT
""")
    rows = [json.loads(line) for line in result.stdout.decode().splitlines() if line]
    if len(rows) != RECONCILED_COUNTRY_COUNT:
        raise CandidateError("S4 incident 人口不是 81")
    by_ref = {row["legacy_reference"]: row for row in rows}
    if len(by_ref) != len(rows):
        raise CandidateError("S4 legacy_reference 不唯一")
    return by_ref


def parse_legacy_time(value: str | None) -> str | None:
    if value is None:
        return None
    local = datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_country_rows(
    candidate_id: str, import_batch_id: str, rows: Sequence[Mapping[str, Any]],
    incidents: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    legacy_rows: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    matched_refs: set[str] = set()

    def add_field(
        source_record_id: str, name: str, legacy: Any, unified: Any,
        comparison: str, reason: str, status: str = "closed",
    ) -> None:
        semantic = {
            "source_record_id": source_record_id, "field_name": name,
            "legacy_value": legacy, "unified_value": unified,
            "comparison": comparison, "reason_code": reason,
            "disposition_status": status,
        }
        fields.append({
            "candidate_id": candidate_id,
            "reconciliation_id": stable_id("reconciliation_v1_", semantic),
            **semantic,
            "legacy_value": json_null(legacy), "unified_value": json_null(unified),
        })

    for payload in sorted(rows, key=lambda row: (row["s_time"], row.get("country") or "", row["outage_id"])):
        local_time = payload["s_time"]
        reference = (
            f"country_outage/{local_time.replace('T', ' ')}/{payload.get('country') or ''}/"
            f"{payload['outage_id']}/{payload['source']}"
        )
        normalized_start = parse_legacy_time(local_time)
        normalized_end = parse_legacy_time(payload.get("e_time"))
        source_pk = {
            "source": payload["source"], "country": payload.get("country"),
            "outage_id": payload["outage_id"], "s_time": local_time,
        }
        row_sha = sha256_bytes(canonical_bytes(payload))
        source_record_id = stable_id("legacy_country_outage_v1_", {
            "source_table": "country_outage_" + local_time[:7].replace("-", ""),
            "source_primary_key": source_pk, "source_row_sha256": row_sha,
        })
        incident = incidents.get(reference)
        country = payload.get("country") or None
        if incident:
            disposition = "reconciled_to_unified_incident"
            unified_incident_id = incident["incident_id"]
            matched_refs.add(reference)
        elif not country or not COUNTRY_RE.fullmatch(country):
            disposition = "quarantined_invalid_country_code"
            unified_incident_id = None
        else:
            disposition = "trace_only_not_in_frozen_publication_registry"
            unified_incident_id = None
        source_table = "country_outage_" + local_time[:7].replace("-", "")
        legacy_rows.append({
            "candidate_id": candidate_id, "import_batch_id": import_batch_id,
            "source_record_id": source_record_id,
            "source_table": source_table, "source_primary_key": source_pk,
            "legacy_reference": reference, "source_code": LEGACY_SOURCE_CODE,
            "collector_id": COLLECTOR_ID, "country_code": country,
            "outage_id": payload["outage_id"],
            "source_start_time_local": local_time.replace("T", " "),
            "normalized_start_time_utc": normalized_start,
            "normalized_end_time_utc": normalized_end,
            "source_row_sha256": row_sha, "import_disposition": disposition,
            "unified_incident_id": unified_incident_id,
            "payload": {
                "legacy_payload": payload,
                "legacy_time_zone": "Asia/Shanghai",
                "source_primary_key_kind": "declared_synthetic_key_no_database_pk",
                "legacy_reference": reference,
            },
        })
        status = "quarantined" if disposition == "quarantined_invalid_country_code" else "closed"
        add_field(source_record_id, "collector_id", payload["source"], COLLECTOR_ID,
                  "mapped", "legacy_source_r_maps_only_to_rrc25", status)
        add_field(source_record_id, "legacy_reference", reference,
                  incident["legacy_reference"] if incident else None,
                  "equal" if incident else "absent",
                  "exact_registry_identity" if incident else "not_in_frozen_publication_registry_trace_only", status)
        add_field(source_record_id, "country_code", country,
                  incident["country_code"] if incident else None,
                  "equal" if incident else ("invalid" if status == "quarantined" else "absent"),
                  "country_identity_equal" if incident else (
                      "invalid_country_code_quarantined" if status == "quarantined"
                      else "not_in_frozen_publication_registry_trace_only"
                  ), status)
        add_field(source_record_id, "start_time_utc", normalized_start,
                  incident["legacy_event_time_utc"] if incident else None,
                  "equal" if incident else "absent",
                  "asia_shanghai_naive_timestamp_normalized_to_utc" if incident
                  else "not_in_frozen_publication_registry_trace_only", status)
        add_field(source_record_id, "end_time", normalized_end,
                  "event_facts_and_publication_lifecycle",
                  "not_comparable", "legacy_mutable_end_time_is_not_unified_lifecycle_fact", status)
        add_field(source_record_id, "outage_metric", {
            "max_outage_as_ratio": payload.get("max_outage_as_ratio"),
            "max_outage_as_num": payload.get("max_outage_as_num"),
            "total_as_num": payload.get("total_as_num"),
        }, {
            "metric_semantics": "fixed_cohort_prefix_vp_route_state_projection",
            "unit": "route_state_count_by_address_family",
        }, "not_comparable", "legacy_as_ratio_population_differs_from_unified_route_state_population", status)
    if set(incidents) != matched_refs:
        raise CandidateError(f"S4 事件未全部映射到旧库：{sorted(set(incidents)-matched_refs)[:3]}")
    disposition_counts: dict[str, int] = {}
    for row in legacy_rows:
        disposition_counts[row["import_disposition"]] = disposition_counts.get(row["import_disposition"], 0) + 1
    if disposition_counts != {
        "reconciled_to_unified_incident": 81,
        "trace_only_not_in_frozen_publication_registry": 29,
        "quarantined_invalid_country_code": 1,
    }:
        raise CandidateError(f"旧国家中断处置人口异常：{disposition_counts}")
    if len(fields) != LEGACY_COUNTRY_COUNT * 6:
        raise CandidateError("字段级对账人口错误")
    return legacy_rows, fields, disposition_counts


def build_source_field_reconciliation(
    candidate_id: str, import_batch_id: str,
    table_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    feature_asn_population = 40609327
    for table in table_rows:
        source_table = table["source_table"]
        family = table["semantic_family"]
        source_count = table["scope_row_count"]
        if family == "feature_country":
            unified_population = 1041120
        elif family.startswith("feature_"):
            unified_population = feature_asn_population
        elif family == "country_outage":
            unified_population = RECONCILED_COUNTRY_COUNT
        else:
            unified_population = 0
        for column in table["payload"]["schema_fragment"]["columns"]:
            field = column["column_name"]
            standardized: str | None = None
            comparison = "trace_only"
            reason = "semantic_family_outside_current_unified_product_contract"
            if field == "source":
                standardized, comparison = "collector_id", "mapped"
                reason = "legacy_source_r_maps_only_to_rrc25"
            elif field == table["source_time_column"]:
                standardized, comparison = (
                    "state_point_utc" if family.startswith("feature_") else "event_time_utc"
                ), "mapped"
                reason = "asia_shanghai_naive_timestamp_normalized_to_utc"
            elif field == "country":
                standardized, comparison = (
                    "subject_id" if family.startswith("feature_") else "country_code"
                ), "mapped"
                reason = "legacy_country_identifier_preserved"
            elif field == "asn":
                standardized, comparison = "subject_id", "mapped"
                reason = "legacy_asn_identifier_preserved_for_trace"
            elif family == "country_outage" and field == "outage_id":
                standardized, comparison = "legacy_reference", "mapped"
                reason = "legacy_outage_id_preserved_inside_reference"
            elif family == "country_outage" and field == "e_time":
                standardized, comparison = "event_fact_and_publication_lifecycle", "not_comparable"
                reason = "legacy_mutable_end_time_is_not_unified_lifecycle_fact"
            elif family == "country_outage" and field in {
                "max_outage_as_ratio", "max_outage_as_num", "total_as_num", "outage_ases",
            }:
                standardized, comparison = "route_state_projected_metric", "not_comparable"
                reason = "legacy_as_population_differs_from_prefix_vp_route_state_population"
            elif family.startswith("feature_") and field in {"v4prefix_num", "v6prefix_num"}:
                standardized, comparison = (
                    "current_visible_route_state_count_v4" if field.startswith("v4")
                    else "current_visible_route_state_count_v6"
                ), "not_comparable"
                reason = "legacy_prefix_count_without_vp_dimension_differs_from_prefix_vp_route_state_count"
            elif family.startswith("feature_") and field in {"announ_num", "withdraw_num"}:
                standardized, comparison = (
                    "announcement_count" if field == "announ_num" else "withdrawal_count"
                ), "not_comparable"
                reason = "legacy_aggregate_event_unit_not_proven_equal_to_route_state_projector_unit"
            elif family.startswith("feature_") and field == "v4ip_num":
                standardized, comparison = None, "not_comparable"
                reason = "legacy_ipv4_address_space_measure_has_no_frozen_unified_metric"
            semantic = {
                "source_table": source_table, "source_field": field,
                "standardized_field": standardized, "comparison": comparison,
                "reason_code": reason, "source_row_count": source_count,
                "unified_population": unified_population,
            }
            rows.append({
                "candidate_id": candidate_id, "import_batch_id": import_batch_id,
                "reconciliation_id": stable_id("source_field_reconciliation_v1_", semantic),
                **semantic, "disposition_status": "closed",
                "payload": {
                    "source_data_type": column["data_type"],
                    "source_nullable": column["is_nullable"],
                    "source_table_disposition": table["disposition"],
                    "population_scope": "table_rows_vs_unified_family_population_not_row_equivalence",
                },
            })
    if len(rows) != 534:
        raise CandidateError(f"旧库字段级处置人口不是 534：{len(rows)}")
    return rows


def make_release_rows(
    candidate_id: str, dataset_id: str, migration_content_sha256: str,
    snapshot_set_sha256: str, sources: Mapping[str, Any],
    database_evidence: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    objects: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []

    def add_object(
        kind: str, state: str, path: str, object_dataset_id: str | None,
        content_sha: str | None, runtime_readable: bool, retention: str,
        payload: Mapping[str, Any],
    ) -> str:
        semantic = {
            "object_kind": kind, "object_state": state, "path_identity": path,
            "dataset_id": object_dataset_id, "content_sha256": content_sha,
        }
        object_id = stable_id("release_object_v1_", semantic)
        objects.append({
            "candidate_id": candidate_id, "object_id": object_id,
            **semantic, "runtime_readable": runtime_readable,
            "retention_policy": retention, "payload": dict(payload),
        })
        return object_id

    object_ids: dict[str, str] = {}
    legacy_payload = {
        "source_database": "bgp_project", "source_schema_sha256": EXPECTED_LEGACY_SCHEMA_SHA256,
        "source_snapshot_set_sha256": snapshot_set_sha256,
        "access_mode": "migration_reader_read_only", "production_selected": False,
    }
    object_ids["legacy"] = add_object(
        "legacy_postgresql_snapshot", "formal",
        f"postgresql:///bgp_project?snapshot_sha256={snapshot_set_sha256}", None,
        snapshot_set_sha256, False, "preserve", legacy_payload,
    )
    stage_dataset_keys = {
        "s1": "dataset_id", "s2": "dataset_id", "s3": "dataset_id",
        "s4": "dataset_id", "s5": "dataset_id",
    }
    runtime_readable = {"s1": False, "s2": False, "s3": False, "s4": True, "s5": True}
    kinds = {
        "s1": "route_event_evidence", "s2": "route_state_evidence",
        "s3": "route_metric_store", "s4": "event_publication_store",
        "s5": "read_model_and_report_store",
    }
    for stage in ("s1", "s2", "s3", "s4", "s5"):
        info = sources[stage]
        manifest = info["manifest"]
        object_ids[stage] = add_object(
            kinds[stage], "formal", info["root"], manifest[stage_dataset_keys[stage]],
            manifest["content_sha256"], runtime_readable[stage], "referenced",
            {
                "manifest_sha256": info["manifest_sha256"],
                "schema_version": manifest["schema_version"],
                "database_evidence": database_evidence.get(stage),
                "collector_id": COLLECTOR_ID, "window_start_utc": WINDOW_START,
                "window_end_exclusive_utc": WINDOW_END,
            },
        )
    object_ids["s6"] = add_object(
        "shadow_migration_dataset", "formal", f"migration://{dataset_id}", dataset_id,
        migration_content_sha256, False, "referenced",
        {"source_snapshot_set_sha256": snapshot_set_sha256, "database_model": DATABASE_MODEL},
    )
    object_ids["incomplete"] = add_object(
        "incomplete_staging_object", "incomplete",
        "/home/bgpdata/Domeye-Core-dev-data/research-builds/data-layer-224-310-s6/.incomplete/object.tmp",
        None, None, False, "ephemeral", {"api_selectable": False},
    )
    object_ids["quarantine"] = add_object(
        "quarantined_legacy_record", "quarantine",
        "/home/bgpdata/Domeye-Core-dev-data/research-builds/data-layer-224-310-s6/quarantine/invalid-country",
        None, None, False, "preserve", {"api_selectable": False},
    )
    object_ids["cache"] = add_object(
        "unreferenced_rebuild_cache", "candidate",
        "/home/bgpdata/Domeye-Core-dev-data/research-builds/data-layer-224-310-s6/cache/disposable",
        None, sha256_bytes(b"rrc25-s6-disposable-cache"), False, "ephemeral",
        {"rebuildable": True, "api_selectable": False},
    )

    reference_specs = [
        ("legacy", "migration", dataset_id, "source_trace_and_rollback"),
        ("s1", "dataset", sources["s1"]["manifest"]["dataset_id"], "immutable_route_event_source"),
        ("s2", "dataset", sources["s2"]["manifest"]["dataset_id"], "single_route_state_authority"),
        ("s3", "dataset", sources["s3"]["manifest"]["dataset_id"], "unified_metric_source"),
        ("s4", "publication", sources["s4"]["manifest"]["dataset_id"], "immutable_publication_source"),
        ("s5", "report", sources["s5"]["manifest"]["dataset_id"], "read_model_and_report_source"),
        ("s6", "migration", dataset_id, "shadow_migration_reconciliation"),
        ("legacy", "retention_policy", "legacy_retirement_gate_not_reached", "preserve_until_independent_retirement"),
    ]
    for key, kind, source_id, purpose in reference_specs:
        semantic = {"object_id": object_ids[key], "reference_kind": kind,
                    "reference_source_id": source_id, "purpose": purpose}
        references.append({
            "candidate_id": candidate_id,
            "reference_id": stable_id("object_reference_v1_", semantic), **semantic,
        })

    components = {
        "candidate_id": candidate_id,
        "route_event_dataset_id": sources["s1"]["manifest"]["dataset_id"],
        "route_state_dataset_id": sources["s2"]["manifest"]["dataset_id"],
        "metric_dataset_id": sources["s3"]["manifest"]["dataset_id"],
        "event_publication_dataset_id": sources["s4"]["manifest"]["dataset_id"],
        "read_model_dataset_id": sources["s5"]["manifest"]["dataset_id"],
        "migration_dataset_id": dataset_id,
        "collector_id": COLLECTOR_ID, "window_start_utc": WINDOW_START,
        "window_end_exclusive_utc": WINDOW_END,
        "state_point_count": STATE_POINT_COUNT, "country_bucket_count": COUNTRY_BUCKET_COUNT,
    }
    legacy_semantic = {
        "bundle_mode": "legacy_readonly_rollback", "candidate_id": candidate_id,
        "object_ids": [object_ids["legacy"]], "source_snapshot_set_sha256": snapshot_set_sha256,
    }
    unified_semantic = {
        "bundle_mode": "unified", "candidate_id": candidate_id,
        "object_ids": [object_ids[key] for key in ("legacy", "s1", "s2", "s3", "s4", "s5", "s6")],
        "coherent_components": components,
    }
    invalid_semantic = {
        "bundle_mode": "invalid_incomplete", "candidate_id": candidate_id,
        "object_ids": [object_ids["incomplete"]],
    }
    bundle_ids = {
        "legacy": stable_id("release_bundle_v1_", legacy_semantic),
        "unified": stable_id("release_bundle_v1_", unified_semantic),
        "invalid": stable_id("release_bundle_v1_", invalid_semantic),
    }
    bundles = [
        {
            "candidate_id": candidate_id, "bundle_id": bundle_ids["legacy"],
            "bundle_mode": "legacy_readonly_rollback", "bundle_state": "complete",
            "content_sha256": sha256_bytes(canonical_bytes(legacy_semantic)),
            "coherent_components": {"legacy_snapshot": snapshot_set_sha256},
            "payload": legacy_semantic,
        },
        {
            "candidate_id": candidate_id, "bundle_id": bundle_ids["unified"],
            "bundle_mode": "unified", "bundle_state": "complete",
            "content_sha256": sha256_bytes(canonical_bytes(unified_semantic)),
            "coherent_components": components, "payload": unified_semantic,
        },
        {
            "candidate_id": candidate_id, "bundle_id": bundle_ids["invalid"],
            "bundle_mode": "invalid_incomplete", "bundle_state": "incomplete",
            "content_sha256": None, "coherent_components": {}, "payload": invalid_semantic,
        },
    ]
    bundle_objects: list[dict[str, Any]] = []
    for bundle_key, object_keys in (
        ("legacy", ("legacy",)),
        ("unified", ("legacy", "s1", "s2", "s3", "s4", "s5", "s6")),
        ("invalid", ("incomplete",)),
    ):
        for object_key in object_keys:
            bundle_objects.append({
                "candidate_id": candidate_id, "bundle_id": bundle_ids[bundle_key],
                "object_id": object_ids[object_key],
                "purpose": "trace_only" if object_key == "legacy" and bundle_key == "unified"
                else "coherent_release_component",
            })
    return objects, references, bundles, bundle_objects, bundle_ids


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    if not IMPLEMENTATION_RE.fullmatch(args.implementation_id):
        raise CandidateError("S6 implementation_id 必须是完整 Git 提交")
    output = Path(args.output)
    if output.exists():
        raise CandidateError("S6 输出目录已存在；create-only 拒绝覆盖")
    sources = source_manifests(args)
    database_evidence = database_source_evidence(args, sources)
    legacy_database = Postgres(args.container, args.legacy_database, args.user)
    snapshot = capture_legacy_snapshot(legacy_database)
    incidents = export_s4_incidents(Postgres(args.container, args.s4_database, args.user))
    candidate_id = sources["candidate_id"]
    dataset_semantic = {
        "candidate_id": candidate_id, "implementation_id": args.implementation_id,
        "collector_id": COLLECTOR_ID, "window_start_utc": WINDOW_START,
        "window_end_exclusive_utc": WINDOW_END,
        "source_snapshot_set_sha256": snapshot["source_snapshot_set_sha256"],
        "source_datasets": {
            stage: sources[stage]["manifest"]["dataset_id"]
            for stage in ("s1", "s2", "s3", "s4", "s5")
        },
    }
    dataset_id = stable_id("shadow_migration_dataset_v1_", dataset_semantic)
    run_id = stable_id("shadow_migration_run_v1_", dataset_semantic)
    import_batch_id = stable_id("shadow_import_batch_v1_", dataset_semantic)
    legacy_rows, reconciliation_rows, dispositions = normalize_country_rows(
        candidate_id, import_batch_id, snapshot["country_rows"], incidents,
    )
    table_rows: list[dict[str, Any]] = []
    for row in snapshot["source_table_snapshots"]:
        semantic = {
            "source_database": args.legacy_database, "source_table": row["source_table"],
            "source_snapshot_set_sha256": snapshot["source_snapshot_set_sha256"],
            "multiset_fingerprint_sha256": row["multiset_fingerprint_sha256"],
        }
        table_rows.append({
            "candidate_id": candidate_id, "import_batch_id": import_batch_id,
            "snapshot_id": stable_id("legacy_table_snapshot_v1_", semantic),
            "source_database": args.legacy_database, **row,
        })
    source_field_rows = build_source_field_reconciliation(
        candidate_id, import_batch_id, table_rows,
    )
    migration_content_sha = sha256_bytes(canonical_bytes({
        "dataset_id": dataset_id,
        "source_snapshot_set_sha256": snapshot["source_snapshot_set_sha256"],
        "source_record_hashes": [row["source_row_sha256"] for row in legacy_rows],
        "reconciliation_ids": [row["reconciliation_id"] for row in reconciliation_rows],
        "source_field_reconciliation_ids": [
            row["reconciliation_id"] for row in source_field_rows
        ],
        "disposition_counts": dispositions,
    }))
    objects, references, bundles, bundle_objects, bundle_ids = make_release_rows(
        candidate_id, dataset_id, migration_content_sha,
        snapshot["source_snapshot_set_sha256"], sources, database_evidence,
    )
    output.mkdir(parents=True, mode=0o750)
    files = [
        write_tsv_gzip(output / "source-table-snapshot.tsv.gz", SNAPSHOT_COLUMNS, table_rows),
        write_tsv_gzip(output / "source-field-reconciliation.tsv.gz", SOURCE_FIELD_COLUMNS, source_field_rows),
        write_tsv_gzip(output / "legacy-country-outage.tsv.gz", LEGACY_COLUMNS, legacy_rows),
        write_tsv_gzip(output / "reconciliation-field.tsv.gz", RECONCILIATION_COLUMNS, reconciliation_rows),
        write_tsv_gzip(output / "release-object.tsv.gz", OBJECT_COLUMNS, objects),
        write_tsv_gzip(output / "object-reference.tsv.gz", REFERENCE_COLUMNS, references),
        write_tsv_gzip(output / "release-bundle.tsv.gz", BUNDLE_COLUMNS, bundles),
        write_tsv_gzip(output / "bundle-object.tsv.gz", BUNDLE_OBJECT_COLUMNS, bundle_objects),
    ]
    for row, role in zip(files, (
        "legacy_source_table_snapshot", "legacy_source_field_reconciliation",
        "legacy_country_outage_import",
        "field_level_reconciliation", "release_object_registry",
        "object_reference_registry", "atomic_release_bundle", "release_bundle_object_binding",
    ), strict=True):
        row["role"] = role
    content_sha = sha256_bytes(canonical_bytes({
        "dataset_semantic": dataset_semantic,
        "migration_content_sha256": migration_content_sha,
        "files": [{key: row[key] for key in ("path", "row_count", "content_sha256")} for row in files],
        "bundle_ids": bundle_ids,
    }))
    dlae_sources = {
        "DLAE-01": ["docs/Domeye数据层224-310最终验收文档.md", "S3:candidate_registry"],
        "DLAE-02": [sources["s1"]["root"] + "/manifest.json"],
        "DLAE-03": [sources["s1"]["root"] + "/manifest.json"],
        "DLAE-04": [sources["s2"]["root"] + "/manifest.json"],
        "DLAE-05": [sources["s3"]["root"] + "/manifest.json", args.s3_database],
        "DLAE-06": [args.s3_database], "DLAE-07": [args.s3_database],
        "DLAE-08": [sources["s2"]["root"] + "/manifest.json", args.s3_database],
        "DLAE-09": [sources["s4"]["root"] + "/manifest.json", args.s4_database],
        "DLAE-10": [args.s4_database], "DLAE-11": [args.s4_database],
        "DLAE-12": [sources["s5"]["root"] + "/manifest.json", args.s5_database],
        "DLAE-13": [args.s5_database],
        "DLAE-14": [
            "source-table-snapshot.tsv.gz", "source-field-reconciliation.tsv.gz",
            "reconciliation-field.tsv.gz",
        ],
        "DLAE-15": ["release-object.tsv.gz", "release-bundle.tsv.gz"],
        "DLAE-16": ["S6 acceptance receipt and selected runtime audit"],
    }
    manifest = {
        "schema_version": SCHEMA_VERSION, "status": "complete",
        "candidate_id": candidate_id, "run_id": run_id, "dataset_id": dataset_id,
        "import_batch_id": import_batch_id,
        "implementation_id": args.implementation_id, "collector_id": COLLECTOR_ID,
        "window_start_utc": WINDOW_START, "window_end_exclusive_utc": WINDOW_END,
        "state_point_count": STATE_POINT_COUNT, "country_bucket_count": COUNTRY_BUCKET_COUNT,
        "legacy_database_name": args.legacy_database,
        "legacy_database_schema_sha256": snapshot["legacy_schema_sha256"],
        "legacy_source_time_semantics": "timestamp_without_time_zone_as_Asia/Shanghai_then_UTC",
        "legacy_source_code_mapping": {"r": "rrc25"},
        "legacy_table_count": len(table_rows),
        "legacy_scope_row_count": sum(row["scope_row_count"] for row in table_rows),
        "legacy_country_outage_count": len(legacy_rows),
        "reconciled_country_outage_count": dispositions["reconciled_to_unified_incident"],
        "trace_only_country_outage_count": (
            dispositions["trace_only_not_in_frozen_publication_registry"]
            + dispositions["quarantined_invalid_country_code"]
        ),
        "country_outage_disposition_counts": dispositions,
        "reconciliation_field_count": len(reconciliation_rows),
        "source_field_reconciliation_count": len(source_field_rows),
        "source_snapshot_set_sha256": snapshot["source_snapshot_set_sha256"],
        "migration_content_sha256": migration_content_sha,
        "source_snapshot_transaction": snapshot["meta"],
        "source_datasets": {
            stage: {
                "dataset_id": sources[stage]["manifest"]["dataset_id"],
                "content_sha256": sources[stage]["manifest"]["content_sha256"],
                "manifest_sha256": sources[stage]["manifest_sha256"],
                "root": sources[stage]["root"],
            } for stage in ("s1", "s2", "s3", "s4", "s5")
        },
        "source_database_evidence": database_evidence,
        "database_model": DATABASE_MODEL,
        "release_bundle_ids": bundle_ids,
        "release_switch_semantics": "single_transactional_pointer_to_complete_coherent_bundle",
        "production_selection": False,
        "dlae_evidence_plan": dlae_sources,
        "built_at": utc_now(), "files": files, "content_sha256": content_sha,
    }
    raw = canonical_bytes(manifest) + b"\n"
    with (output / "manifest.json").open("xb") as handle:
        handle.write(raw)
    with (output / "COMPLETE.json").open("xb") as handle:
        handle.write(raw)
    return {"manifest": manifest, "manifest_sha256": sha256_bytes(raw)}


def verify_candidate(root: Path) -> dict[str, Any]:
    manifest, manifest_sha = exact_manifest(root)
    required = {
        "schema_version": SCHEMA_VERSION, "status": "complete",
        "collector_id": COLLECTOR_ID, "window_start_utc": WINDOW_START,
        "window_end_exclusive_utc": WINDOW_END, "state_point_count": STATE_POINT_COUNT,
        "country_bucket_count": COUNTRY_BUCKET_COUNT, "legacy_table_count": LEGACY_TABLE_COUNT,
        "legacy_country_outage_count": LEGACY_COUNTRY_COUNT,
        "reconciled_country_outage_count": RECONCILED_COUNTRY_COUNT,
        "trace_only_country_outage_count": TRACE_ONLY_COUNTRY_COUNT,
        "source_field_reconciliation_count": 534,
        "production_selection": False,
    }
    for key, expected in required.items():
        if manifest.get(key) != expected:
            raise CandidateError(f"S6 manifest 字段冲突：{key}")
    if not SHA_RE.fullmatch(str(manifest.get("source_snapshot_set_sha256", ""))):
        raise CandidateError("S6 source snapshot 身份无效")
    if not str(manifest.get("import_batch_id", "")).startswith("shadow_import_batch_v1_"):
        raise CandidateError("S6 import batch 身份无效")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != 8:
        raise CandidateError("S6 数据文件清单人口错误")
    for entry in files:
        path = root / entry["path"]
        if not path.is_file():
            raise CandidateError(f"S6 数据文件缺失：{path}")
        actual_sha, size = sha256_file(path)
        if actual_sha != entry["sha256"] or size != entry["size_bytes"]:
            raise CandidateError(f"S6 数据文件物理身份冲突：{path}")
        with gzip.open(path, "rb") as source:
            raw = source.read()
        if sha256_bytes(raw) != entry["content_sha256"]:
            raise CandidateError(f"S6 数据文件内容身份冲突：{path}")
        if len(raw.splitlines()) - 1 != entry["row_count"]:
            raise CandidateError(f"S6 数据文件行数冲突：{path}")
    table_rows = read_tsv_gzip(root / "source-table-snapshot.tsv.gz")
    source_fields = read_tsv_gzip(root / "source-field-reconciliation.tsv.gz")
    legacy_rows = read_tsv_gzip(root / "legacy-country-outage.tsv.gz")
    reconciliation = read_tsv_gzip(root / "reconciliation-field.tsv.gz")
    objects = read_tsv_gzip(root / "release-object.tsv.gz")
    references = read_tsv_gzip(root / "object-reference.tsv.gz")
    bundles = read_tsv_gzip(root / "release-bundle.tsv.gz")
    bundle_objects = read_tsv_gzip(root / "bundle-object.tsv.gz")
    if (
        len(table_rows) != 37 or len(source_fields) != 534
        or len(legacy_rows) != 111 or len(reconciliation) != 666
        or len(objects) != 10 or len(references) != 8 or len(bundles) != 3
        or len(bundle_objects) != 9
    ):
        raise CandidateError("S6 候选人口不闭合")
    if {row["source_table"] for row in table_rows} != set(OLD_TABLE_NAMES):
        raise CandidateError("S6 旧表快照集合漂移")
    disposition_counts: dict[str, int] = {}
    for row in legacy_rows:
        disposition_counts[row["import_disposition"]] = disposition_counts.get(row["import_disposition"], 0) + 1
    if disposition_counts != manifest["country_outage_disposition_counts"]:
        raise CandidateError("S6 旧事件处置人口冲突")
    if set(manifest["dlae_evidence_plan"]) != {f"DLAE-{index:02d}" for index in range(1, 17)}:
        raise CandidateError("DLAE 证据计划不是 16 项")
    content_sha = sha256_bytes(canonical_bytes({
        "dataset_semantic": {
            "candidate_id": manifest["candidate_id"],
            "implementation_id": manifest["implementation_id"],
            "collector_id": COLLECTOR_ID, "window_start_utc": WINDOW_START,
            "window_end_exclusive_utc": WINDOW_END,
            "source_snapshot_set_sha256": manifest["source_snapshot_set_sha256"],
            "source_datasets": {
                stage: manifest["source_datasets"][stage]["dataset_id"]
                for stage in ("s1", "s2", "s3", "s4", "s5")
            },
        },
        "migration_content_sha256": manifest["migration_content_sha256"],
        "files": [{key: row[key] for key in ("path", "row_count", "content_sha256")} for row in files],
        "bundle_ids": manifest["release_bundle_ids"],
    }))
    if content_sha != manifest["content_sha256"]:
        raise CandidateError("S6 全局内容身份冲突")
    return {
        "manifest": manifest, "manifest_sha256": manifest_sha,
        "rows": {
            "snapshots": table_rows, "source_fields": source_fields,
            "legacy": legacy_rows,
            "reconciliation": reconciliation, "objects": objects,
            "references": references, "bundles": bundles,
            "bundle_objects": bundle_objects,
        },
    }


def expect_database_failure(database: Postgres, sql: str, expected: str) -> dict[str, Any]:
    result = database.psql(sql, check=False)
    stderr = result.stderr.decode("utf-8", "replace")
    if result.returncode == 0 or expected not in stderr:
        raise CandidateError(f"预期数据库拒绝未发生：{expected}；stderr={stderr[-1000:]}")
    return {"returncode": result.returncode, "matched": expected}


def role_names(database_name: str) -> dict[str, str]:
    suffix = sha256_bytes(database_name.encode())[:12]
    return {
        "migration_reader": f"domeye_s6_migration_{suffix}",
        "publisher": f"domeye_s6_publisher_{suffix}",
        "runtime": f"domeye_s6_runtime_{suffix}",
    }


def create_acceptance_roles(
    database: Postgres, candidate_id: str,
) -> dict[str, str]:
    roles = role_names(database.database)
    for role in roles.values():
        exists = database.scalar(
            f"SELECT count(*) FROM pg_roles WHERE rolname={sql_literal(role)}",
            database="postgres",
        )
        if exists != "0":
            raise CandidateError(f"S6 验收角色已存在；create-only 拒绝复用：{role}")
    database.psql("\n".join(
        f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;'
        for role in roles.values()
    ), database="postgres")
    database.psql(f"""
GRANT CONNECT ON DATABASE "{database.database}" TO "{roles['migration_reader']}","{roles['publisher']}","{roles['runtime']}";
GRANT USAGE ON SCHEMA domeye_migration TO "{roles['migration_reader']}";
GRANT SELECT ON domeye_migration.source_table_snapshot,domeye_migration.legacy_country_outage,
 domeye_migration.source_field_reconciliation,domeye_migration.reconciliation_field
 TO "{roles['migration_reader']}";
GRANT USAGE ON SCHEMA domeye_control TO "{roles['publisher']}";
GRANT EXECUTE ON FUNCTION domeye_control.switch_release(bigint,text,text) TO "{roles['publisher']}";
GRANT USAGE ON SCHEMA domeye_runtime TO "{roles['runtime']}";
GRANT SELECT ON domeye_runtime.selected_release,domeye_runtime.selected_object TO "{roles['runtime']}";
""")
    contracts = [
        {
            "identity_name": roles["migration_reader"], "identity_kind": "migration_reader",
            "can_read_legacy": False, "can_write_legacy": False,
            "can_read_control_base": False, "can_write_control_base": False,
            "can_execute_atomic_switch": False, "can_read_runtime_view": False,
            "payload": {
                "login": False,
                "scope": "imported legacy shadow tables SELECT only",
                "source_database_access": "none",
                "source_extractor_transaction": "repeatable_read_read_only",
            },
        },
        {
            "identity_name": roles["publisher"], "identity_kind": "publisher",
            "can_read_legacy": False, "can_write_legacy": False,
            "can_read_control_base": False, "can_write_control_base": False,
            "can_execute_atomic_switch": True, "can_read_runtime_view": False,
            "payload": {"login": False, "scope": "SECURITY DEFINER atomic switch only"},
        },
        {
            "identity_name": roles["runtime"], "identity_kind": "runtime",
            "can_read_legacy": False, "can_write_legacy": False,
            "can_read_control_base": False, "can_write_control_base": False,
            "can_execute_atomic_switch": False, "can_read_runtime_view": True,
            "payload": {"login": False, "scope": "selected formal runtime views only"},
        },
    ]
    values = []
    for row in contracts:
        values.append("(" + ",".join([
            sql_literal(candidate_id), sql_literal(row["identity_name"]),
            sql_literal(row["identity_kind"]), str(row["can_read_legacy"]).lower(),
            "false", str(row["can_read_control_base"]).lower(),
            str(row["can_write_control_base"]).lower(),
            str(row["can_execute_atomic_switch"]).lower(),
            str(row["can_read_runtime_view"]).lower(),
            sql_literal(canonical_bytes(row["payload"]).decode()) + "::jsonb",
        ]) + ")")
    database.psql("""
INSERT INTO domeye_control.role_contract(
 candidate_id,identity_name,identity_kind,can_read_legacy,can_write_legacy,
 can_read_control_base,can_write_control_base,can_execute_atomic_switch,
 can_read_runtime_view,payload
) VALUES
""" + ",\n".join(values))
    return roles


def insert_dlae_evidence(
    database: Postgres, manifest: Mapping[str, Any], indices: Sequence[int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in indices:
        acceptance_id = f"DLAE-{index:02d}"
        refs = list(manifest["dlae_evidence_plan"][acceptance_id])
        if index >= 14:
            refs.extend([
                "domeye_migration.source_table_snapshot",
                "domeye_control.release_pointer",
                "domeye_runtime.selected_release",
            ])
        semantic = {
            "acceptance_id": acceptance_id, "status": "passed",
            "candidate_id": manifest["candidate_id"], "evidence_refs": refs,
        }
        evidence_sha = sha256_bytes(canonical_bytes(semantic))
        rows.append({**semantic, "evidence_sha256": evidence_sha})
        database.psql(f"""
INSERT INTO domeye_control.dlae_evidence(
 candidate_id,acceptance_id,status,evidence_refs,evidence_sha256,payload
) VALUES (
 {sql_literal(manifest['candidate_id'])},{sql_literal(acceptance_id)},'passed',
 {sql_literal(canonical_bytes(refs).decode())}::jsonb,{sql_literal(evidence_sha)},
 {sql_literal(canonical_bytes(semantic).decode())}::jsonb
)
""")
    return rows


def database_summary(database: Postgres) -> dict[str, Any]:
    return json.loads(database.scalar("""
SELECT json_build_object(
 'complete_candidate_count',(SELECT count(*) FROM domeye_migration.candidate_registry WHERE status='complete'),
 'import_batch_count',(SELECT count(*) FROM domeye_migration.import_batch WHERE status='complete'),
 'source_table_snapshot_count',(SELECT count(*) FROM domeye_migration.source_table_snapshot),
 'source_field_reconciliation_count',(SELECT count(*) FROM domeye_migration.source_field_reconciliation),
 'open_source_field_reconciliation_count',(SELECT count(*) FROM domeye_migration.source_field_reconciliation WHERE disposition_status<>'closed'),
 'source_scope_row_count',(SELECT sum(scope_row_count) FROM domeye_migration.source_table_snapshot),
 'legacy_country_outage_count',(SELECT count(*) FROM domeye_migration.legacy_country_outage),
 'reconciled_count',(SELECT count(*) FROM domeye_migration.legacy_country_outage WHERE import_disposition='reconciled_to_unified_incident'),
 'trace_only_count',(SELECT count(*) FROM domeye_migration.legacy_country_outage WHERE import_disposition='trace_only_not_in_frozen_publication_registry'),
 'quarantine_count',(SELECT count(*) FROM domeye_migration.legacy_country_outage WHERE import_disposition='quarantined_invalid_country_code'),
 'reconciliation_field_count',(SELECT count(*) FROM domeye_migration.reconciliation_field),
 'open_reconciliation_count',(SELECT count(*) FROM domeye_migration.reconciliation_field WHERE disposition_status NOT IN ('closed','quarantined')),
 'release_object_count',(SELECT count(*) FROM domeye_control.release_object),
 'formal_object_count',(SELECT count(*) FROM domeye_control.release_object WHERE object_state='formal'),
 'runtime_readable_nonformal_count',(SELECT count(*) FROM domeye_control.release_object WHERE runtime_readable AND object_state<>'formal'),
 'object_reference_count',(SELECT count(*) FROM domeye_control.object_reference),
 'release_bundle_count',(SELECT count(*) FROM domeye_control.release_bundle),
 'orphan_bundle_object_count',(SELECT count(*) FROM domeye_control.bundle_object bo LEFT JOIN domeye_control.release_object ro USING(candidate_id,object_id) WHERE ro.object_id IS NULL),
 'pointer_count',(SELECT count(*) FROM domeye_control.release_pointer),
 'pointer_version',(SELECT pointer_version FROM domeye_control.release_pointer),
 'selected_bundle_id',(SELECT current_bundle_id FROM domeye_control.release_pointer),
 'selected_bundle_mode',(SELECT b.bundle_mode FROM domeye_control.release_pointer p JOIN domeye_control.release_bundle b ON b.candidate_id=p.candidate_id AND b.bundle_id=p.current_bundle_id),
 'switch_audit_count',(SELECT count(*) FROM domeye_control.switch_audit),
 'role_contract_count',(SELECT count(*) FROM domeye_control.role_contract),
 'dlae_pass_count',(SELECT count(*) FROM domeye_control.dlae_evidence WHERE status='passed'),
 'retention_eligible_count',(SELECT count(*) FROM domeye_control.retention_eligibility WHERE eligible_for_collection),
 'referenced_retention_eligible_count',(SELECT count(*) FROM domeye_control.retention_eligibility WHERE eligible_for_collection AND reference_count>0),
 'runtime_selection_count',(SELECT count(*) FROM domeye_runtime.selected_release),
 'production_selection_count',(SELECT count(*) FROM domeye_migration.candidate_registry WHERE selected_by_production)
)
"""))


def _role_scalar(database: Postgres, role: str, sql: str) -> str:
    return database.scalar(f'SET ROLE "{role}"; {sql}')


def run_security_and_switch_drills(
    database: Postgres, legacy: Postgres, manifest: Mapping[str, Any],
    roles: Mapping[str, str],
) -> dict[str, Any]:
    candidate_id = manifest["candidate_id"]
    bundles = manifest["release_bundle_ids"]
    publisher = roles["publisher"]
    runtime = roles["runtime"]
    reader = roles["migration_reader"]
    invalid = expect_database_failure(
        database,
        f'SET ROLE "{publisher}"; SELECT domeye_control.switch_release(1,{sql_literal(bundles["invalid"])},\'reject incomplete bundle\')',
        "target release bundle is not complete",
    )
    pointer_after_invalid = database.scalar("SELECT pointer_version FROM domeye_control.release_pointer")
    if pointer_after_invalid != "1":
        raise CandidateError("无效 bundle 造成部分指针切换")
    forward_version = _role_scalar(
        database, publisher,
        f"SELECT domeye_control.switch_release(1,{sql_literal(bundles['unified'])},'acceptance forward switch')",
    )
    rollback_version = _role_scalar(
        database, publisher,
        f"SELECT domeye_control.switch_release(2,{sql_literal(bundles['legacy'])},'acceptance rollback drill')",
    )
    final_version = _role_scalar(
        database, publisher,
        f"SELECT domeye_control.switch_release(3,{sql_literal(bundles['unified'])},'acceptance final forward switch')",
    )
    if (forward_version, rollback_version, final_version) != ("2", "3", "4"):
        raise CandidateError("原子切换/回退版本序列错误")
    stale = expect_database_failure(
        database,
        f'SET ROLE "{publisher}"; SELECT domeye_control.switch_release(3,{sql_literal(bundles["legacy"])},\'stale switch\')',
        "stale release pointer version",
    )
    direct_pointer = expect_database_failure(
        database,
        f'SET ROLE "{publisher}"; UPDATE domeye_control.release_pointer SET reason=\'direct mutation\'',
        "permission denied",
    )
    reader_count = _role_scalar(
        database, reader, "SELECT count(*) FROM domeye_migration.legacy_country_outage",
    )
    if reader_count != "111":
        raise CandidateError("迁移只读身份无法读取影子导入表")
    reader_write = expect_database_failure(
        database,
        f'SET ROLE "{reader}"; UPDATE domeye_migration.legacy_country_outage SET country_code=country_code WHERE false',
        "permission denied",
    )
    source_read_only = expect_database_failure(
        legacy,
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY; "
        "UPDATE public.country_outage_202602 SET country=country WHERE false",
        "cannot execute UPDATE in a read-only transaction",
    )
    runtime_base = expect_database_failure(
        database,
        f'SET ROLE "{runtime}"; SELECT count(*) FROM domeye_control.release_pointer',
        "permission denied",
    )
    runtime_write = expect_database_failure(
        database,
        f'SET ROLE "{runtime}"; UPDATE domeye_control.release_pointer SET reason=reason',
        "permission denied",
    )
    publisher_base = expect_database_failure(
        database,
        f'SET ROLE "{publisher}"; SELECT count(*) FROM domeye_control.release_pointer',
        "permission denied",
    )
    immutable_object = expect_database_failure(
        database,
        "DELETE FROM domeye_control.release_object WHERE object_state='formal'",
        "immutable S6 object",
    )
    permission_matrix = json.loads(database.scalar(f"""
SELECT json_build_object(
 'migration_reader_legacy_select',has_table_privilege({sql_literal(reader)},'public.country_outage_202602','SELECT'),
 'migration_reader_legacy_update',has_table_privilege({sql_literal(reader)},'public.country_outage_202602','UPDATE'),
 'runtime_legacy_select',has_table_privilege({sql_literal(runtime)},'public.country_outage_202602','SELECT'),
 'publisher_legacy_select',has_table_privilege({sql_literal(publisher)},'public.country_outage_202602','SELECT')
)
""", database=legacy.database))
    if permission_matrix != {
        "migration_reader_legacy_select": False,
        "migration_reader_legacy_update": False,
        "runtime_legacy_select": False,
        "publisher_legacy_select": False,
    }:
        raise CandidateError(f"旧库角色权限矩阵错误：{permission_matrix}")
    retention = json.loads(database.scalar("""
SELECT json_build_object(
 'eligible_ids',(SELECT coalesce(json_agg(object_id ORDER BY object_id),'[]'::json) FROM domeye_control.retention_eligibility WHERE eligible_for_collection),
 'referenced_eligible',(SELECT count(*) FROM domeye_control.retention_eligibility WHERE eligible_for_collection AND reference_count>0),
 'formal_eligible',(SELECT count(*) FROM domeye_control.retention_eligibility WHERE eligible_for_collection AND object_state='formal')
)
"""))
    if len(retention["eligible_ids"]) != 1 or retention["referenced_eligible"] != 0 or retention["formal_eligible"] != 0:
        raise CandidateError(f"回收保护边界错误：{retention}")
    pointer = json.loads(database.scalar("""
SELECT json_build_object('bundle_id',current_bundle_id,'version',pointer_version,
 'selected_by_production',selected_by_production) FROM domeye_control.release_pointer
"""))
    if pointer != {"bundle_id": bundles["unified"], "version": 4, "selected_by_production": False}:
        raise CandidateError(f"最终验收指针错误：{pointer}")
    return {
        "invalid_bundle_rejected": invalid, "pointer_unchanged_after_invalid": True,
        "forward_version": 2, "rollback_version": 3, "final_forward_version": 4,
        "stale_switch_rejected": stale, "direct_pointer_mutation_rejected": direct_pointer,
        "migration_reader_shadow_select_count": int(reader_count),
        "migration_reader_write_rejected": reader_write,
        "source_read_only_transaction_write_rejected": source_read_only,
        "runtime_base_read_rejected": runtime_base, "runtime_write_rejected": runtime_write,
        "publisher_base_read_rejected": publisher_base,
        "immutable_formal_object_delete_rejected": immutable_object,
        "legacy_permission_matrix": permission_matrix, "retention": retention,
        "final_pointer": pointer, "candidate_id": candidate_id,
    }


class ShadowRuntime:
    def __init__(
        self, s6_root: Path, s5_root: Path, database: Postgres,
        runtime_role: str, access_log: Path | None,
    ) -> None:
        verified = verify_candidate(s6_root)
        self.s6_manifest = verified["manifest"]
        raw = _role_scalar(
            database, runtime_role,
            "SELECT row_to_json(s) FROM domeye_runtime.selected_release s",
        )
        if not raw:
            raise CandidateError("运行身份未看到唯一 unified 正式选择")
        self.selection = json.loads(raw)
        if (
            self.selection["candidate_id"] != self.s6_manifest["candidate_id"]
            or self.selection["bundle_id"] != self.s6_manifest["release_bundle_ids"]["unified"]
            or self.selection["pointer_version"] != 4
            or self.selection["selected_by_production"]
        ):
            raise CandidateError("运行身份看到的选择与 S6 候选不一致")
        self.inner = ReadModelRuntime(s5_root)
        expected_read_model = self.selection["coherent_components"]["read_model_dataset_id"]
        if self.inner.manifest["dataset_id"] != expected_read_model:
            raise CandidateError("运行选择的 S5 读模型 dataset 不一致")
        self.runtime_role = runtime_role
        self.access_log = access_log

    def log(self, route: str, sources: Sequence[str]) -> None:
        if self.access_log is None:
            return
        record = {
            "route": route, "sources": list(sources),
            "candidate_id": self.s6_manifest["candidate_id"],
            "bundle_id": self.selection["bundle_id"],
            "pointer_version": self.selection["pointer_version"],
            "effective_database_role": self.runtime_role,
            "legacy_table_read": False, "raw_mrt_scanned": False,
            "route_event_scanned": False, "full_asn_state_scanned": False,
            "publication_recomputed": False,
        }
        with self.access_log.open("ab") as output:
            output.write(canonical_bytes(record) + b"\n")


def make_shadow_handler(runtime: ShadowRuntime) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "DomeyeS6ShadowRuntime/1.0"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def send_json(self, value: Any, status: int = 200) -> None:
            raw = canonical_bytes(value) + b"\n"
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "public, immutable")
            self.end_headers()
            self.wfile.write(raw)

        def send_gzip(self, raw: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "public, immutable")
            self.end_headers()
            self.wfile.write(raw)

        def fail(self, message: str, status: int = 404) -> None:
            self.send_json({"error": message}, status)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            inner = runtime.inner
            if path == "/healthz":
                runtime.log(path, ["domeye_runtime.selected_release", "manifest.json"])
                self.send_json({
                    "status": "ok", "collector_id": COLLECTOR_ID,
                    "candidate_id": runtime.s6_manifest["candidate_id"],
                    "dataset_id": runtime.s6_manifest["dataset_id"],
                    "selected_bundle_id": runtime.selection["bundle_id"],
                    "pointer_version": runtime.selection["pointer_version"],
                    "selected_by_acceptance_runtime": True,
                    "selected_by_production": False,
                })
                return
            if path == "/api/v1/events/by-ref":
                reference = urllib.parse.parse_qs(parsed.query).get("ref", [""])[0]
                incident_id = inner.events_by_ref.get(reference)
                if incident_id is None:
                    self.fail("event reference not found")
                    return
                runtime.log(path, ["formal S5 event-read-model.tsv.gz"])
                self.send_json(inner.events[incident_id])
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 4 and parts[:3] == ["api", "v1", "events"]:
                incident_id = parts[3]
                event = inner.events.get(incident_id)
                if event is None:
                    self.fail("incident not found")
                    return
                if len(parts) == 4:
                    runtime.log(path, ["formal S5 event-read-model.tsv.gz"])
                    self.send_json(event)
                    return
                if len(parts) == 5 and parts[4] == "series":
                    series_id = event["series_ref"]["series_id"]
                    runtime.log(path, [event["series_ref"]["artifact_uri"]])
                    self.send_gzip(inner.series[series_id])
                    return
                if len(parts) == 5 and parts[4] == "report":
                    report_id = inner.report_pointer[incident_id]
                    runtime.log(path, ["formal S5 report-pointer.tsv.gz", "formal S5 report-snapshot.tsv.gz"])
                    self.send_json(inner.reports[report_id])
                    return
                if len(parts) == 5 and parts[4] == "evidence":
                    runtime.log(path, ["formal S5 prefix-vp-evidence-view.tsv.gz"])
                    self.send_json(inner.evidence[incident_id])
                    return
            if len(parts) == 4 and parts[:3] == ["api", "v1", "reports"]:
                report = inner.reports.get(parts[3])
                if report is None:
                    self.fail("report snapshot not found")
                    return
                runtime.log(path, ["formal S5 report-snapshot.tsv.gz"])
                self.send_json(report)
                return
            self.fail("route not found")

    return Handler


def p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def runtime_acceptance(runtime: ShadowRuntime) -> dict[str, Any]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_shadow_handler(runtime))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(path: str) -> tuple[float, bytes, Mapping[str, str]]:
        started = time.perf_counter()
        with urllib.request.urlopen(base + path, timeout=5) as response:
            raw = response.read()
            headers = dict(response.headers.items())
        return (time.perf_counter() - started) * 1000, raw, headers

    ir_ref = "country_outage/2026-02-27 09:12:32/IR/1/r"
    mw_ref = "country_outage/2026-03-09 22:09:38/MW/2/r"
    try:
        health = json.loads(request("/healthz")[1])
        direct: dict[str, Any] = {}
        for label, reference in (("IR", ir_ref), ("MW", mw_ref)):
            elapsed, raw, _ = request("/api/v1/events/by-ref?" + urllib.parse.urlencode({"ref": reference}))
            payload = json.loads(raw)
            if payload["incident"]["legacy_reference"] != reference:
                raise CandidateError(f"{label} 直接 ref 解析错误")
            contract = payload["window_contract"]
            if (
                contract["collector_id"] != COLLECTOR_ID
                or contract["start_utc"] != WINDOW_START
                or contract["end_exclusive_utc"] != WINDOW_END
                or contract["state_point_count"] != STATE_POINT_COUNT
            ):
                raise CandidateError(f"{label} 在线窗口身份漂移")
            direct[label] = {
                "reference": reference, "incident_id": payload["incident"]["incident_id"],
                "elapsed_ms": elapsed, "observation_publication_id": payload["observation_publication"]["publication_id"],
                "analysis_publication_id": payload["analysis_publication"]["publication_id"],
            }
        incidents = sorted(runtime.inner.events)
        cold = [request(f"/api/v1/events/{incident}")[0] for incident in incidents]
        hot: list[float] = []
        for _ in range(3):
            hot.extend(request(f"/api/v1/events/{incident}")[0] for incident in incidents)
        report = [request(f"/api/v1/events/{incident}/report")[0] for incident in incidents]
        series: list[tuple[float, int]] = []
        seen: set[str] = set()
        for incident in incidents:
            country = runtime.inner.events[incident]["incident"]["country_code"]
            if country in seen:
                continue
            seen.add(country)
            elapsed, raw, _ = request(f"/api/v1/events/{incident}/series")
            series.append((elapsed, len(raw)))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    performance = {
        "event_cold_sample_count": len(cold), "event_cold_p95_ms": p95(cold),
        "event_hot_sample_count": len(hot), "event_hot_p95_ms": p95(hot),
        "report_sample_count": len(report), "report_p95_ms": p95(report),
        "series_sample_count": len(series), "series_p95_ms": p95([row[0] for row in series]),
        "series_max_response_bytes": max(row[1] for row in series),
        "budgets": {"event_cold_ms": 2000, "event_hot_ms": 500, "series_ms": 2000, "series_bytes": 1048576},
    }
    if (
        performance["event_cold_p95_ms"] > 2000
        or performance["event_hot_p95_ms"] > 500
        or performance["series_p95_ms"] > 2000
        or performance["series_max_response_bytes"] > 1048576
    ):
        raise CandidateError(f"S6 选择后在线性能/载荷预算失败：{performance}")
    if not health["selected_by_acceptance_runtime"] or health["selected_by_production"]:
        raise CandidateError("S6 health 运行选择语义错误")
    return {"health": health, "direct_event_checks": direct, "performance": performance}


def accept_candidate(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root)
    verified = verify_candidate(root)
    manifest = verified["manifest"]
    if not DATABASE_RE.fullmatch(args.database) or args.database in {
        args.legacy_database, args.s3_database, args.s4_database, args.s5_database,
    }:
        raise CandidateError("S6 目标数据库名无效或指向来源库")
    for target in (Path(args.receipt), Path(args.output), Path(args.access_log)):
        if target.exists():
            raise CandidateError(f"S6 验收输出已存在；create-only 拒绝覆盖：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)

    sources = source_manifests(args)
    current_database_evidence = database_source_evidence(args, sources)
    if current_database_evidence != manifest["source_database_evidence"]:
        raise CandidateError("S3/S4/S5 数据库来源在 S6 构建后发生漂移")
    for stage in ("s1", "s2", "s3", "s4", "s5"):
        expected = manifest["source_datasets"][stage]
        actual = sources[stage]
        if (
            expected["dataset_id"] != actual["manifest"]["dataset_id"]
            or expected["content_sha256"] != actual["manifest"]["content_sha256"]
            or expected["manifest_sha256"] != actual["manifest_sha256"]
        ):
            raise CandidateError(f"{stage.upper()} 来源在 S6 构建后发生漂移")

    legacy = Postgres(args.container, args.legacy_database, args.user)
    database = Postgres(args.container, args.database, args.user)
    exists = database.scalar(
        f"SELECT count(*) FROM pg_database WHERE datname={sql_literal(args.database)}",
        database="postgres",
    )
    if exists != "0":
        raise CandidateError("S6 目标数据库已存在；create-only 拒绝覆盖")
    database.psql(
        f'CREATE DATABASE "{args.database}" TEMPLATE template0 ENCODING \'UTF8\'',
        database="postgres",
    )
    roles: dict[str, str] | None = None
    try:
        database.apply_file(Path(args.ddl))
        database.psql(f"""
INSERT INTO domeye_migration.candidate_registry(
 candidate_id,dataset_id,collector_id,window_start_utc,window_end_exclusive_utc,
 state_point_count,country_bucket_count,implementation_id,manifest_sha256,
 content_sha256,selected_by_acceptance_runtime,selected_by_production,status
) VALUES (
 {sql_literal(manifest['candidate_id'])},{sql_literal(manifest['dataset_id'])},'rrc25',
 TIMESTAMPTZ '{WINDOW_START}',TIMESTAMPTZ '{WINDOW_END}',
 {STATE_POINT_COUNT},{COUNTRY_BUCKET_COUNT},{sql_literal(manifest['implementation_id'])},
 {sql_literal(verified['manifest_sha256'])},{sql_literal(manifest['content_sha256'])},
 false,false,'loading'
)
""")
        database.psql(f"""
INSERT INTO domeye_migration.import_batch(
 import_batch_id,candidate_id,source_database,source_schema_sha256,
 source_snapshot_set_sha256,source_time_semantics,extraction_transaction,status,payload
) VALUES (
 {sql_literal(manifest['import_batch_id'])},{sql_literal(manifest['candidate_id'])},
 'bgp_project',{sql_literal(manifest['legacy_database_schema_sha256'])},
 {sql_literal(manifest['source_snapshot_set_sha256'])},
 'timestamp_without_time_zone_as_Asia/Shanghai_then_UTC',
 'repeatable_read_read_only','complete',
 {sql_literal(canonical_bytes({'run_id': manifest['run_id'], 'dataset_id': manifest['dataset_id']}).decode())}::jsonb
)
""")
        for filename, table, columns in (
            ("source-table-snapshot.tsv.gz", "domeye_migration.source_table_snapshot", SNAPSHOT_COLUMNS),
            ("source-field-reconciliation.tsv.gz", "domeye_migration.source_field_reconciliation", SOURCE_FIELD_COLUMNS),
            ("legacy-country-outage.tsv.gz", "domeye_migration.legacy_country_outage", LEGACY_COLUMNS),
            ("reconciliation-field.tsv.gz", "domeye_migration.reconciliation_field", RECONCILIATION_COLUMNS),
            ("release-object.tsv.gz", "domeye_control.release_object", OBJECT_COLUMNS),
            ("object-reference.tsv.gz", "domeye_control.object_reference", REFERENCE_COLUMNS),
            ("release-bundle.tsv.gz", "domeye_control.release_bundle", BUNDLE_COLUMNS),
            ("bundle-object.tsv.gz", "domeye_control.bundle_object", BUNDLE_OBJECT_COLUMNS),
        ):
            database.copy_gzip(root / filename, table, columns)
        legacy_bundle = manifest["release_bundle_ids"]["legacy"]
        bootstrap_audit = stable_id("switch_audit_v1_", {
            "candidate_id": manifest["candidate_id"], "version": 1,
            "bundle_id": legacy_bundle, "reason": "acceptance bootstrap rollback slot",
        })
        database.psql(f"""
INSERT INTO domeye_control.release_pointer(
 pointer_name,candidate_id,current_bundle_id,pointer_version,selected_by_production,reason
) VALUES (
 'rrc25_224_310_acceptance',{sql_literal(manifest['candidate_id'])},
 {sql_literal(legacy_bundle)},1,false,'acceptance bootstrap rollback slot'
);
INSERT INTO domeye_control.switch_audit(
 audit_id,candidate_id,pointer_name,from_bundle_id,to_bundle_id,from_version,to_version,
 actor_identity,reason,switched_at
) VALUES (
 {sql_literal(bootstrap_audit)},{sql_literal(manifest['candidate_id'])},
 'rrc25_224_310_acceptance',NULL,{sql_literal(legacy_bundle)},0,1,
 's6_candidate_loader','acceptance bootstrap rollback slot',clock_timestamp()
)
""")
        roles = create_acceptance_roles(database, manifest["candidate_id"])
        switch_drills = run_security_and_switch_drills(database, legacy, manifest, roles)

        after_snapshot = capture_legacy_snapshot(legacy)
        if after_snapshot["source_snapshot_set_sha256"] != manifest["source_snapshot_set_sha256"]:
            raise CandidateError("旧库在 S6 影子迁移期间发生内容漂移")
        source_immutability = {
            "legacy_schema_sha256_before": manifest["legacy_database_schema_sha256"],
            "legacy_schema_sha256_after": after_snapshot["legacy_schema_sha256"],
            "source_snapshot_set_sha256_before": manifest["source_snapshot_set_sha256"],
            "source_snapshot_set_sha256_after": after_snapshot["source_snapshot_set_sha256"],
            "unchanged": True, "legacy_data_written": False,
            "legacy_schema_written": False,
            "privilege_change": "none; source database ACL unchanged",
        }
        dlae_rows = insert_dlae_evidence(database, manifest, tuple(range(1, 16)))
        database.psql("""
UPDATE domeye_migration.candidate_registry
   SET selected_by_acceptance_runtime=true,status='complete'
 WHERE status='loading'
""")
        with Path(args.access_log).open("xb"):
            pass
        runtime = ShadowRuntime(
            root, Path(args.s5_root), database, roles["runtime"], Path(args.access_log),
        )
        runtime_result = runtime_acceptance(runtime)
        access_rows = [
            json.loads(line) for line in Path(args.access_log).read_text().splitlines() if line
        ]
        if not access_rows or any(
            row["legacy_table_read"] or row["raw_mrt_scanned"] or row["route_event_scanned"]
            or row["full_asn_state_scanned"] or row["publication_recomputed"]
            or row["effective_database_role"] != roles["runtime"]
            for row in access_rows
        ):
            raise CandidateError("S6 在线访问审计出现旧表/原始扫描/身份越权")
        dlae_rows.extend(insert_dlae_evidence(database, manifest, (16,)))
        summary = database_summary(database)
        expected_summary = {
            "complete_candidate_count": 1, "import_batch_count": 1,
            "source_table_snapshot_count": 37,
            "source_field_reconciliation_count": 534,
            "open_source_field_reconciliation_count": 0,
            "source_scope_row_count": manifest["legacy_scope_row_count"],
            "legacy_country_outage_count": 111, "reconciled_count": 81,
            "trace_only_count": 29, "quarantine_count": 1,
            "reconciliation_field_count": 666, "open_reconciliation_count": 0,
            "release_object_count": 10, "formal_object_count": 7,
            "runtime_readable_nonformal_count": 0, "object_reference_count": 8,
            "release_bundle_count": 3, "orphan_bundle_object_count": 0,
            "pointer_count": 1, "pointer_version": 4,
            "selected_bundle_id": manifest["release_bundle_ids"]["unified"],
            "selected_bundle_mode": "unified", "switch_audit_count": 4,
            "role_contract_count": 3, "dlae_pass_count": 16,
            "retention_eligible_count": 1, "referenced_retention_eligible_count": 0,
            "runtime_selection_count": 1, "production_selection_count": 0,
        }
        if summary != expected_summary:
            raise CandidateError(f"S6 数据库人口/边界不闭合：{summary} != {expected_summary}")
        schema_sha = pg_dump_schema_sha256(database)
        fingerprint = sha256_bytes(canonical_bytes({
            "candidate_id": manifest["candidate_id"], "dataset_id": manifest["dataset_id"],
            "manifest_sha256": verified["manifest_sha256"],
            "content_sha256": manifest["content_sha256"],
            "schema_sha256": schema_sha, "summary": summary,
            "source_snapshot_set_sha256": manifest["source_snapshot_set_sha256"],
        }))
        receipt_id = stable_id("shadow_migration_load_receipt_v1_", {
            "candidate_id": manifest["candidate_id"], "dataset_id": manifest["dataset_id"],
            "database_fingerprint_sha256": fingerprint,
        })
        loaded_at = utc_now()
        database.psql(f"""
INSERT INTO domeye_control.load_receipt(
 receipt_id,candidate_id,dataset_id,manifest_sha256,schema_sha256,
 database_fingerprint_sha256,selected_bundle_id,selected_pointer_version,
 loaded_at,status
) VALUES (
 {sql_literal(receipt_id)},{sql_literal(manifest['candidate_id'])},
 {sql_literal(manifest['dataset_id'])},{sql_literal(verified['manifest_sha256'])},
 {sql_literal(schema_sha)},{sql_literal(fingerprint)},
 {sql_literal(manifest['release_bundle_ids']['unified'])},4,
 TIMESTAMPTZ {sql_literal(loaded_at)},'complete'
)
""")
        receipt = {
            "schema_version": "rrc25-shadow-migration-database-load-receipt/v1",
            "status": "complete", "receipt_id": receipt_id,
            "candidate_id": manifest["candidate_id"], "dataset_id": manifest["dataset_id"],
            "manifest_sha256": verified["manifest_sha256"],
            "content_sha256": manifest["content_sha256"],
            "database_name": args.database, "database_model": DATABASE_MODEL,
            "schema_sha256": schema_sha, "database_fingerprint_sha256": fingerprint,
            "selected_bundle_id": manifest["release_bundle_ids"]["unified"],
            "selected_pointer_version": 4, "roles": roles,
            "switch_drills": switch_drills, "source_immutability": source_immutability,
            "summary": summary, "loaded_at": loaded_at,
            "selected_by_acceptance_runtime": True,
            "selected_by_production": False,
            "production_database_written": False,
            "production_runtime_changed": False,
        }
        receipt["receipt_content_sha256"] = sha256_bytes(canonical_bytes(receipt))
        write_json_create_only(Path(args.receipt), receipt)
    except BaseException:
        try:
            database.psql("""
UPDATE domeye_migration.candidate_registry SET status='failed'
 WHERE status='loading'
""")
        except BaseException:
            pass
        raise

    assert roles is not None
    dlae_results = [
        {
            "acceptance_id": row["acceptance_id"], "status": "passed",
            "candidate_id": manifest["candidate_id"],
            "evidence_refs": row["evidence_refs"] + (
                [args.receipt, args.access_log] if int(row["acceptance_id"][-2:]) >= 14 else []
            ),
        }
        for row in dlae_rows
    ]
    acceptance = {
        "schema_version": "rrc25-data-layer-end-to-end-acceptance/v1",
        "status": "passed", "candidate_id": manifest["candidate_id"],
        "dataset_id": manifest["dataset_id"], "database_name": args.database,
        "selected_bundle_id": manifest["release_bundle_ids"]["unified"],
        "selected_pointer_version": 4,
        "selected_by_acceptance_runtime": True, "selected_by_production": False,
        "collector_id": COLLECTOR_ID, "window_start_utc": WINDOW_START,
        "window_end_exclusive_utc": WINDOW_END,
        "state_point_count": STATE_POINT_COUNT, "country_bucket_count": COUNTRY_BUCKET_COUNT,
        "country_metric_row_count": 1041120,
        "legacy_reconciliation": {
            "source_table_count": 37, "legacy_country_outage_count": 111,
            "import_batch_id": manifest["import_batch_id"],
            "source_field_reconciliation_count": 534,
            "reconciled_count": 81, "trace_only_count": 29,
            "quarantine_count": 1, "field_reconciliation_count": 666,
            "source_snapshot_set_sha256": manifest["source_snapshot_set_sha256"],
        },
        "database": {"schema_sha256": receipt["schema_sha256"],
                     "fingerprint_sha256": receipt["database_fingerprint_sha256"],
                     "summary": receipt["summary"]},
        "security_and_switch_drills": receipt["switch_drills"],
        "source_immutability": receipt["source_immutability"],
        "runtime": runtime_result,
        "runtime_access_audit": {
            "row_count": len(access_rows),
            "content_sha256": sha256_bytes(Path(args.access_log).read_bytes()),
            "legacy_table_read_count": sum(bool(row["legacy_table_read"]) for row in access_rows),
            "raw_or_state_scan_count": sum(
                bool(row["raw_mrt_scanned"] or row["route_event_scanned"] or row["full_asn_state_scanned"])
                for row in access_rows
            ),
        },
        "dlae_results": dlae_results,
        "limitations": [
            "这是独立验收数据库与候选运行时选择，不是生产部署或生产切换",
            "旧库 ACL、数据和表结构均未修改；源提取使用可重复读只读事务，迁移角色只读独立候选中的影子导入表",
            "仅描述 RRC25 控制面观测，不证明用户影响、原因、攻击、责任或完全恢复",
        ],
    }
    acceptance["content_sha256"] = sha256_bytes(canonical_bytes(acceptance))
    write_json_create_only(Path(args.output), acceptance)
    return acceptance


def serve_candidate(args: argparse.Namespace) -> None:
    database = Postgres(args.container, args.database, args.user)
    runtime = ShadowRuntime(
        Path(args.root), Path(args.s5_root), database, args.runtime_role,
        Path(args.access_log) if args.access_log else None,
    )
    server = ThreadingHTTPServer((args.host, args.port), make_shadow_handler(runtime))
    print(canonical_bytes({
        "status": "ready", "host": args.host, "port": server.server_port,
        "candidate_id": runtime.s6_manifest["candidate_id"],
        "bundle_id": runtime.selection["bundle_id"],
        "selected_by_production": False,
    }).decode(), flush=True)
    server.serve_forever()


def configure_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def sources(target: argparse.ArgumentParser) -> None:
        target.add_argument("--s1-root", required=True)
        target.add_argument("--s2-root", required=True)
        target.add_argument("--s3-root", required=True)
        target.add_argument("--s4-root", required=True)
        target.add_argument("--s5-root", required=True)
        target.add_argument("--s3-database", required=True)
        target.add_argument("--s4-database", required=True)
        target.add_argument("--s5-database", required=True)
        target.add_argument("--legacy-database", default="bgp_project")
        target.add_argument("--container", default="domeye_core_dev_pg")
        target.add_argument("--user", default="postgres")

    build = subparsers.add_parser("build")
    sources(build)
    build.add_argument("--implementation-id", required=True)
    build.add_argument("--output", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", required=True)

    accept = subparsers.add_parser("accept")
    sources(accept)
    accept.add_argument("--root", required=True)
    accept.add_argument("--database", required=True)
    accept.add_argument("--ddl", required=True)
    accept.add_argument("--receipt", required=True)
    accept.add_argument("--access-log", required=True)
    accept.add_argument("--output", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--root", required=True)
    serve.add_argument("--s5-root", required=True)
    serve.add_argument("--database", required=True)
    serve.add_argument("--runtime-role", required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=28661)
    serve.add_argument("--access-log")
    serve.add_argument("--container", default="domeye_core_dev_pg")
    serve.add_argument("--user", default="postgres")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = configure_parser().parse_args(argv)
    try:
        if args.command == "build":
            result = build_candidate(args)
        elif args.command == "verify":
            result = verify_candidate(Path(args.root))
            result = {"manifest": result["manifest"], "manifest_sha256": result["manifest_sha256"]}
        elif args.command == "accept":
            result = accept_candidate(args)
        elif args.command == "serve":
            serve_candidate(args)
            return 0
        else:
            raise CandidateError("未知命令")
        print(canonical_bytes(result).decode())
        return 0
    except CandidateError as error:
        print(str(error), file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
