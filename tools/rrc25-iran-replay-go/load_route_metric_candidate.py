#!/usr/bin/env python3
"""把完整 S3 指标文件候选装入独立 PostgreSQL/TimescaleDB 候选库。

脚本是 create-only：数据库必须事先不存在。所有文件先独立重算压缩摘要、
解压内容摘要和行数；候选库在完整装载、人口守恒和查询验证前保持 loading，
不会被任何 current 指针选中。脚本从不连接或写入旧 ``bgp_project``。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable


DATABASE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
IDENTITY_RE = re.compile(r"^[a-zA-Z0-9_.:+-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_WINDOW_START = "2026-02-24T00:00:00Z"
EXPECTED_WINDOW_END = "2026-03-11T00:00:00Z"
EXPECTED_COUNTRY_ROWS = 1_041_120
EXPECTED_SLOTS = 4_320

METRIC_COLUMNS = (
    "candidate_id", "metric_dataset_id", "projection_id", "state_point_utc",
    "subject_type", "subject_id", "country_code", "sample_encoding",
    "baseline_route_state_count_v4", "baseline_route_state_count_v6",
    "cohort_visible_route_state_count_v4", "cohort_visible_route_state_count_v6",
    "current_visible_route_state_count_v4", "current_visible_route_state_count_v6",
    "announcement_count_v4", "announcement_count_v6",
    "withdrawal_count_v4", "withdrawal_count_v6",
    "cohort_visibility_state_v4", "cohort_visibility_state_v6",
)
SLOT_COLUMNS = (
    "candidate_id", "metric_dataset_id", "projection_id", "slot", "artifact_time_utc",
    "state_point_utc", "attempted_through", "data_through", "quality_status", "gap_status",
    "source_route_state_dataset_id", "source_route_state_slot_sha256",
    "source_route_event_file_sha256", "transition_sha256", "route_event_count", "announce_count",
    "withdraw_count", "route_state_record_count", "visible_route_count", "route_state_digest",
    "country_metric_row_count", "asn_metric_row_count", "collector_metric_row_count",
    "metric_snapshot_sha256", "content_sha256",
)
SUBJECT_COLUMNS = (
    "candidate_id", "metric_dataset_id", "subject_type", "subject_id", "country_code",
    "sample_encoding", "first_state_point_utc", "valid_through_utc",
    "baseline_route_state_count_v4", "baseline_route_state_count_v6", "absence_semantics",
)


class LoadError(RuntimeError):
    """候选文件、数据库装载或闭环验证失败。"""


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(8 << 20):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def sql_literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'{}'".format(value.replace("'", "''"))


def sql_array(values: Iterable[str]) -> str:
    items = ",".join(sql_literal(item) for item in values)
    return f"ARRAY[{items}]::text[]"


def run_command(
    command: list[str],
    *,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        command, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise LoadError(
            "命令失败：{}\n{}".format(
                " ".join(command), result.stderr.decode("utf-8", "replace")[-4000:],
            )
        )
    return result


class CandidateDatabase:
    def __init__(self, container: str, database: str, user: str) -> None:
        self.container = container
        self.database = database
        self.user = user

    def psql(
        self,
        sql: str,
        *,
        database: str | None = None,
        tuples: bool = False,
    ) -> str:
        command = [
            "docker", "exec", "-i", self.container,
            "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", self.user,
            "-d", database or self.database,
        ]
        if tuples:
            command.extend(["-A", "-t"])
        command.extend(["-c", sql])
        return run_command(command).stdout.decode("utf-8").strip()

    def apply_file(self, path: Path) -> None:
        command = [
            "docker", "exec", "-i", self.container,
            "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", self.user,
            "-d", self.database, "-f", "-",
        ]
        run_command(command, input_bytes=path.read_bytes())

    def copy_gzip(self, path: Path, table: str, columns: tuple[str, ...]) -> None:
        copy = (
            "\\copy {} ({}) FROM STDIN WITH "
            "(FORMAT csv, DELIMITER E'\\t', HEADER true, NULL '\\N')"
        ).format(table, ",".join(columns))
        command = [
            "docker", "exec", "-i", self.container,
            "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", self.user,
            "-d", self.database, "-c", copy,
        ]
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        assert process.stdin is not None
        try:
            with gzip.open(path, "rb") as source:
                while chunk := source.read(8 << 20):
                    process.stdin.write(chunk)
            process.stdin.close()
            returncode = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        stdout = process.stdout.read() if process.stdout is not None else b""
        stderr = process.stderr.read() if process.stderr is not None else b""
        if returncode:
            raise LoadError(
                "COPY 失败：{}\n{}\n{}".format(
                    path, stdout.decode("utf-8", "replace")[-1000:],
                    stderr.decode("utf-8", "replace")[-4000:],
                )
            )


def validate_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LoadError("S3 manifest 不是有效 JSON") from error
    required = {
        "schema_version": "rrc25-route-metric-store/v1",
        "status": "complete",
        "collector_id": "rrc25",
        "window_start_utc": EXPECTED_WINDOW_START,
        "window_end_exclusive_utc": EXPECTED_WINDOW_END,
        "state_point_count": EXPECTED_SLOTS,
        "country_bucket_count": 241,
        "country_metric_row_count": EXPECTED_COUNTRY_ROWS,
        "collector_metric_row_count": EXPECTED_SLOTS,
        "attempted_through": EXPECTED_WINDOW_END,
        "data_through": EXPECTED_WINDOW_END,
        "missing_slot_count": 0,
        "finality": "final",
        "database_model": "domeye-data-postgresql-timescaledb/v1",
        "projection_source": "same_route_state_apply_transition_only",
    }
    for field, expected in required.items():
        if manifest.get(field) != expected:
            raise LoadError(f"S3 manifest 字段冲突：{field}")
    for field in (
        "candidate_id", "dataset_id", "projection_id", "source_route_event_dataset_id",
        "source_route_state_dataset_id", "route_event_implementation_id",
        "route_state_implementation_id", "implementation_id",
    ):
        if not isinstance(manifest.get(field), str) or not IDENTITY_RE.fullmatch(manifest[field]):
            raise LoadError(f"S3 manifest 身份无效：{field}")
    for field in (
        "content_sha256", "mapping_version", "source_route_event_content_sha256",
        "source_route_event_manifest_sha256", "source_route_state_content_sha256",
        "source_route_state_manifest_sha256", "final_route_state_digest",
    ):
        if not isinstance(manifest.get(field), str) or not SHA256_RE.fullmatch(manifest[field]):
            raise LoadError(f"S3 manifest 摘要无效：{field}")
    semantic = dict(manifest)
    expected_content = semantic["content_sha256"]
    semantic["content_sha256"] = ""
    if hashlib.sha256(canonical_bytes(semantic)).hexdigest() != expected_content:
        raise LoadError("S3 manifest 内容身份不匹配")
    complete = path.with_name("COMPLETE.json")
    if not complete.is_file() or complete.read_bytes() != raw:
        raise LoadError("S3 manifest 与 COMPLETE 不是逐字相同的完成双清单")
    return manifest, hashlib.sha256(raw).hexdigest()


def validate_metric_files(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != 61:
        raise LoadError("S3 指标文件人口不是 15 日四类文件加 subject registry")
    seen: set[str] = set()
    role_counts: dict[str, int] = {}
    for item in files:
        if not isinstance(item, dict):
            raise LoadError("S3 文件清单行无效")
        relative = item.get("path")
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
            raise LoadError("S3 文件相对路径无效")
        if relative in seen:
            raise LoadError("S3 文件路径重复")
        seen.add(relative)
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise LoadError(f"S3 文件不是普通文件：{relative}")
        digest, size = sha256_file(path)
        if digest != item.get("sha256") or size != item.get("size_bytes"):
            raise LoadError(f"S3 文件压缩身份不匹配：{relative}")
        content = hashlib.sha256()
        rows = -1
        header = b""
        with gzip.open(path, "rb") as source:
            while chunk := source.read(8 << 20):
                content.update(chunk)
                rows += chunk.count(b"\n")
                if not header:
                    header = chunk.split(b"\n", 1)[0]
        if rows != item.get("row_count") or content.hexdigest() != item.get("content_sha256"):
            raise LoadError(f"S3 文件解压身份或行数不匹配：{relative}")
        role = item.get("role")
        expected_header = {
            "country_metric": METRIC_COLUMNS,
            "asn_metric_change": METRIC_COLUMNS,
            "collector_metric": METRIC_COLUMNS,
            "metric_slot": SLOT_COLUMNS,
            "metric_subject": SUBJECT_COLUMNS,
        }.get(role)
        if expected_header is None or header.decode("utf-8") != "\t".join(expected_header):
            raise LoadError(f"S3 文件角色或表头不匹配：{relative}")
        role_counts[role] = role_counts.get(role, 0) + 1
    if role_counts != {
        "country_metric": 15,
        "asn_metric_change": 15,
        "collector_metric": 15,
        "metric_slot": 15,
        "metric_subject": 1,
    }:
        raise LoadError(f"S3 文件角色人口不匹配：{role_counts}")
    leftovers = [str(item.relative_to(root)) for item in root.rglob("*.tmp")]
    if leftovers:
        raise LoadError(f"S3 完成候选仍有临时文件：{leftovers[:5]}")
    return files


def verify_source_manifest(path: Path, expected_sha256: str) -> tuple[str, int]:
    if not path.is_file() or path.is_symlink():
        raise LoadError(f"来源 manifest 不是普通文件：{path}")
    digest, size = sha256_file(path)
    if digest != expected_sha256:
        raise LoadError(f"来源 manifest 摘要冲突：{path}")
    return digest, size


def register_identity(
    database: CandidateDatabase,
    manifest: dict[str, Any],
    metric_manifest_sha256: str,
) -> None:
    candidate = manifest["candidate_id"]
    mapping = manifest["mapping_version"]
    route_event = manifest["source_route_event_dataset_id"]
    route_state = manifest["source_route_state_dataset_id"]
    metric = manifest["dataset_id"]
    sql = f"""
BEGIN;
INSERT INTO domeye_data.candidate_registry VALUES (
  {sql_literal(candidate)}, 'rrc25', timestamptz '{EXPECTED_WINDOW_START}',
  timestamptz '{EXPECTED_WINDOW_END}', 4320, 241, {sql_literal(mapping)}, 'loading'
);
INSERT INTO domeye_data.dataset_registry VALUES (
  {sql_literal(route_event)}, {sql_literal(candidate)}, 'route_event', 'rrc25',
  timestamptz '{EXPECTED_WINDOW_START}', timestamptz '{EXPECTED_WINDOW_END}',
  {sql_literal(manifest['source_route_event_content_sha256'])},
  {sql_literal(manifest['source_route_event_manifest_sha256'])}, {sql_literal(mapping)},
  ARRAY[]::text[], {sql_literal(manifest['route_event_implementation_id'])}, NULL, NULL, 'complete'
);
INSERT INTO domeye_data.dataset_registry VALUES (
  {sql_literal(route_state)}, {sql_literal(candidate)}, 'route_state', 'rrc25',
  timestamptz '{EXPECTED_WINDOW_START}', timestamptz '{EXPECTED_WINDOW_END}',
  {sql_literal(manifest['source_route_state_content_sha256'])},
  {sql_literal(manifest['source_route_state_manifest_sha256'])}, {sql_literal(mapping)},
  {sql_array([route_event])}, {sql_literal(manifest['route_state_implementation_id'])},
  'domeye_route_state_projector', '1.0.0', 'complete'
);
INSERT INTO domeye_data.dataset_registry VALUES (
  {sql_literal(metric)}, {sql_literal(candidate)}, 'route_metric', 'rrc25',
  timestamptz '{EXPECTED_WINDOW_START}', timestamptz '{EXPECTED_WINDOW_END}',
  {sql_literal(manifest['content_sha256'])}, {sql_literal(metric_manifest_sha256)},
  {sql_literal(mapping)}, {sql_array([route_state])}, {sql_literal(manifest['implementation_id'])},
  {sql_literal(manifest['projector_name'])}, {sql_literal(manifest['projector_version'])}, 'complete'
);
COMMIT;
"""
    database.psql(sql)


def insert_evidence_objects(
    database: CandidateDatabase,
    root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    files: list[dict[str, Any]],
    route_event_manifest: Path,
    route_state_manifest: Path,
) -> None:
    candidate = manifest["candidate_id"]
    rows: list[tuple[str, str, str, str, int, int | None, str | None]] = []
    for dataset_field, role, path, sha_field, content_field in (
        ("source_route_event_dataset_id", "route_event_manifest", route_event_manifest,
         "source_route_event_manifest_sha256", "source_route_event_content_sha256"),
        ("source_route_state_dataset_id", "route_state_manifest", route_state_manifest,
         "source_route_state_manifest_sha256", "source_route_state_content_sha256"),
    ):
        digest, size = sha256_file(path)
        rows.append((
            manifest[dataset_field], role, "domeye+file://" + str(path.resolve()),
            digest, size, None, manifest[content_field],
        ))
    metric_manifest_sha, metric_manifest_size = sha256_file(manifest_path)
    rows.append((
        manifest["dataset_id"], "route_metric_manifest",
        "domeye+file://" + str(manifest_path.resolve()), metric_manifest_sha,
        metric_manifest_size, None, manifest["content_sha256"],
    ))
    for item in files:
        rows.append((
            manifest["dataset_id"], item["role"],
            "domeye+file://" + str((root / item["path"]).resolve()),
            item["sha256"], item["size_bytes"], item["row_count"], item["content_sha256"],
        ))
    statements = ["BEGIN;"]
    for dataset, role, uri, digest, size, row_count, content in rows:
        statements.append(
            "INSERT INTO domeye_data.evidence_object VALUES "
            "({}, {}, {}, {}, {}, {}, {}, 'verified');".format(
                sql_literal(dataset), sql_literal(candidate), sql_literal(role), sql_literal(uri),
                sql_literal(digest), size, "NULL" if row_count is None else row_count,
                sql_literal(content),
            )
        )
    statements.append("COMMIT;")
    database.psql("\n".join(statements))


def load_files(
    database: CandidateDatabase,
    root: Path,
    files: list[dict[str, Any]],
) -> None:
    roles = {
        "metric_subject": ("domeye_data.metric_subject", SUBJECT_COLUMNS),
        "metric_slot": ("domeye_data.metric_slot_5m", SLOT_COLUMNS),
        "country_metric": ("domeye_data.route_metric_5m", METRIC_COLUMNS),
        "asn_metric_change": ("domeye_data.route_metric_5m", METRIC_COLUMNS),
        "collector_metric": ("domeye_data.route_metric_5m", METRIC_COLUMNS),
    }
    order = {"metric_subject": 0, "metric_slot": 1, "country_metric": 2,
             "asn_metric_change": 3, "collector_metric": 4}
    for item in sorted(files, key=lambda value: (order[value["role"]], value["path"])):
        table, columns = roles[item["role"]]
        database.copy_gzip(root / item["path"], table, columns)


def validate_database(database: CandidateDatabase, manifest: dict[str, Any]) -> dict[str, Any]:
    dataset = sql_literal(manifest["dataset_id"])
    scalar_queries = {
        "slot_count": f"SELECT count(*) FROM domeye_data.metric_slot_5m WHERE metric_dataset_id={dataset}",
        "country_row_count": (
            "SELECT count(*) FROM domeye_data.route_metric_5m "
            f"WHERE metric_dataset_id={dataset} AND subject_type='country'"
        ),
        "collector_row_count": (
            "SELECT count(*) FROM domeye_data.route_metric_5m "
            f"WHERE metric_dataset_id={dataset} AND subject_type='collector'"
        ),
        "asn_change_row_count": (
            "SELECT count(*) FROM domeye_data.route_metric_5m "
            f"WHERE metric_dataset_id={dataset} AND subject_type='asn'"
        ),
        "subject_count": f"SELECT count(*) FROM domeye_data.metric_subject WHERE metric_dataset_id={dataset}",
        "gap_count": f"SELECT count(*) FROM domeye_data.quality_gap WHERE metric_dataset_id={dataset}",
        "country_slot_population_mismatch": (
            "SELECT count(*) FROM (SELECT state_point_utc,count(*) AS n "
            "FROM domeye_data.route_metric_5m "
            f"WHERE metric_dataset_id={dataset} AND subject_type='country' GROUP BY 1 HAVING count(*)<>241) x"
        ),
        "country_collector_conservation_mismatch": (
            "WITH country AS (SELECT state_point_utc,"
            "sum(baseline_route_state_count_v4) b4,sum(baseline_route_state_count_v6) b6,"
            "sum(cohort_visible_route_state_count_v4) cv4,sum(cohort_visible_route_state_count_v6) cv6,"
            "sum(current_visible_route_state_count_v4) cur4,sum(current_visible_route_state_count_v6) cur6,"
            "sum(announcement_count_v4) a4,sum(announcement_count_v6) a6,"
            "sum(withdrawal_count_v4) w4,sum(withdrawal_count_v6) w6 "
            "FROM domeye_data.route_metric_5m "
            f"WHERE metric_dataset_id={dataset} AND subject_type='country' GROUP BY 1) "
            "SELECT count(*) FROM country c JOIN domeye_data.route_metric_5m g USING(state_point_utc) "
            f"WHERE g.metric_dataset_id={dataset} AND g.subject_type='collector' AND "
            "(c.b4<>g.baseline_route_state_count_v4 OR c.b6<>g.baseline_route_state_count_v6 OR "
            "c.cv4<>g.cohort_visible_route_state_count_v4 OR c.cv6<>g.cohort_visible_route_state_count_v6 OR "
            "c.cur4<>g.current_visible_route_state_count_v4 OR c.cur6<>g.current_visible_route_state_count_v6 OR "
            "c.a4<>g.announcement_count_v4 OR c.a6<>g.announcement_count_v6 OR "
            "c.w4<>g.withdrawal_count_v4 OR c.w6<>g.withdrawal_count_v6)"
        ),
        "collector_slot_conservation_mismatch": (
            "SELECT count(*) FROM domeye_data.route_metric_5m metric "
            "JOIN domeye_data.metric_slot_5m slot USING(metric_dataset_id,state_point_utc) "
            f"WHERE metric.metric_dataset_id={dataset} AND metric.subject_type='collector' AND "
            "(metric.announcement_count_v4+metric.announcement_count_v6<>slot.announce_count OR "
            "metric.withdrawal_count_v4+metric.withdrawal_count_v6<>slot.withdraw_count OR "
            "metric.current_visible_route_state_count_v4+metric.current_visible_route_state_count_v6<>slot.visible_route_count)"
        ),
        "unexpected_business_table_count": (
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='domeye_data' "
            "AND table_name NOT IN ('candidate_registry','dataset_registry','evidence_object','metric_subject',"
            "'metric_slot_5m','route_metric_5m','quality_gap','load_receipt')"
        ),
        "hypertable_count": (
            "SELECT count(*) FROM timescaledb_information.hypertables "
            "WHERE hypertable_schema='domeye_data' AND hypertable_name='route_metric_5m'"
        ),
    }
    summary: dict[str, Any] = {}
    for name, query in scalar_queries.items():
        value = database.psql(query, tuples=True)
        try:
            summary[name] = int(value)
        except ValueError as error:
            raise LoadError(f"数据库验证结果不是整数：{name}={value}") from error
    expected = {
        "slot_count": EXPECTED_SLOTS,
        "country_row_count": EXPECTED_COUNTRY_ROWS,
        "collector_row_count": EXPECTED_SLOTS,
        "asn_change_row_count": manifest["asn_metric_change_row_count"],
        "subject_count": manifest["metric_subject_count"],
        "gap_count": 0,
        "country_slot_population_mismatch": 0,
        "country_collector_conservation_mismatch": 0,
        "collector_slot_conservation_mismatch": 0,
        "unexpected_business_table_count": 0,
        "hypertable_count": 1,
    }
    if summary != expected:
        raise LoadError(f"数据库人口、守恒或物理结构验证失败：{summary} != {expected}")
    bounds = database.psql(
        "SELECT min(slot),max(slot),min(state_point_utc),max(state_point_utc),"
        "max(attempted_through),max(data_through) FROM domeye_data.metric_slot_5m "
        f"WHERE metric_dataset_id={dataset}", tuples=True,
    ).split("|")
    if bounds != [
        "1", "4320", "2026-02-24 00:05:00+00", "2026-03-11 00:00:00+00",
        "2026-03-11 00:00:00+00", "2026-03-11 00:00:00+00",
    ]:
        raise LoadError(f"数据库时间范围或双水位错误：{bounds}")
    query_count = int(database.psql(
        "SELECT count(*) FROM domeye_data.query_route_metric_5m("
        f"{dataset},'country','IR',timestamptz '{EXPECTED_WINDOW_START}',"
        "timestamptz '2026-03-11T00:00:01Z')", tuples=True,
    ))
    if query_count != EXPECTED_SLOTS:
        raise LoadError("统一查询函数没有返回完整 4,320 点")
    summary["query_ir_state_point_count"] = query_count
    summary["first_state_point_utc"] = bounds[2]
    summary["last_state_point_utc"] = bounds[3]
    return summary


def schema_fingerprint(database: CandidateDatabase) -> str:
    command = [
        "docker", "exec", database.container, "pg_dump", "-U", database.user,
        "-d", database.database, "--schema-only", "--no-owner", "--no-privileges",
    ]
    raw = run_command(command).stdout
    return hashlib.sha256(raw).hexdigest()


def write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    if path.exists():
        raise LoadError("数据库装载回执已经存在")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = dict(receipt)
    content["content_sha256"] = ""
    content_sha = hashlib.sha256(canonical_bytes(content)).hexdigest()
    receipt["content_sha256"] = content_sha
    with path.open("x", encoding="utf-8") as target:
        json.dump(receipt, target, ensure_ascii=False, sort_keys=True, indent=2)
        target.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--ddl", required=True, type=Path)
    parser.add_argument("--route-event-manifest", required=True, type=Path)
    parser.add_argument("--route-state-manifest", required=True, type=Path)
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()

    if not DATABASE_RE.fullmatch(args.database) or args.database == "bgp_project":
        raise LoadError("候选数据库名无效或指向旧 bgp_project")
    if not args.ddl.is_file() or not args.manifest.is_file():
        raise LoadError("DDL 或 S3 manifest 不存在")
    if args.receipt.exists():
        raise LoadError("数据库装载回执已经存在，create-only 装载拒绝覆盖")
    manifest, manifest_sha = validate_manifest(args.manifest)
    root = args.manifest.parent
    files = validate_metric_files(root, manifest)
    verify_source_manifest(args.route_event_manifest, manifest["source_route_event_manifest_sha256"])
    verify_source_manifest(args.route_state_manifest, manifest["source_route_state_manifest_sha256"])

    database = CandidateDatabase(args.container, args.database, args.user)
    exists = database.psql(
        f"SELECT count(*) FROM pg_database WHERE datname={sql_literal(args.database)}",
        database="postgres", tuples=True,
    )
    if exists != "0":
        raise LoadError("候选数据库已经存在，create-only 装载拒绝覆盖")
    database.psql(f'CREATE DATABASE "{args.database}" TEMPLATE template0 ENCODING \'UTF8\'', database="postgres")
    try:
        database.apply_file(args.ddl)
        register_identity(database, manifest, manifest_sha)
        load_files(database, root, files)
        insert_evidence_objects(
            database, root, args.manifest, manifest, files,
            args.route_event_manifest, args.route_state_manifest,
        )
        summary = validate_database(database, manifest)
        schema_sha = schema_fingerprint(database)
        database_fingerprint = hashlib.sha256(canonical_bytes({
            "candidate_id": manifest["candidate_id"],
            "metric_dataset_id": manifest["dataset_id"],
            "metric_content_sha256": manifest["content_sha256"],
            "manifest_sha256": manifest_sha,
            "schema_sha256": schema_sha,
            "summary": summary,
        })).hexdigest()
        loaded_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        receipt_id = "route_metric_load_receipt_v1_" + hashlib.sha256(canonical_bytes({
            "database": args.database,
            "candidate_id": manifest["candidate_id"],
            "metric_dataset_id": manifest["dataset_id"],
            "database_fingerprint_sha256": database_fingerprint,
        })).hexdigest()[:32]
        database.psql(f"""
BEGIN;
UPDATE domeye_data.candidate_registry SET status='complete'
 WHERE candidate_id={sql_literal(manifest['candidate_id'])} AND status='loading';
INSERT INTO domeye_data.load_receipt VALUES (
  {sql_literal(receipt_id)}, {sql_literal(manifest['candidate_id'])},
  {sql_literal(manifest['dataset_id'])}, {sql_literal(manifest_sha)},
  {sql_literal(schema_sha)}, {sql_literal(database_fingerprint)},
  timestamptz {sql_literal(loaded_at)}, 'complete'
);
COMMIT;
""")
        container_identity = run_command([
            "docker", "inspect", "-f", "{{.Id}} {{.Config.Image}}", args.container,
        ]).stdout.decode("utf-8").strip().split(" ", 1)
        postgres_version = database.psql("SHOW server_version", tuples=True)
        timescale_version = database.psql(
            "SELECT extversion FROM pg_extension WHERE extname='timescaledb'", tuples=True,
        )
        receipt = {
            "schema_version": "rrc25-route-metric-database-load-receipt/v1",
            "status": "complete",
            "receipt_id": receipt_id,
            "candidate_id": manifest["candidate_id"],
            "metric_dataset_id": manifest["dataset_id"],
            "metric_content_sha256": manifest["content_sha256"],
            "manifest_sha256": manifest_sha,
            "database_name": args.database,
            "database_model": "domeye-data-postgresql-timescaledb/v1",
            "container_id": container_identity[0],
            "container_image": container_identity[1] if len(container_identity) > 1 else "",
            "postgresql_version": postgres_version,
            "timescaledb_version": timescale_version,
            "schema_sha256": schema_sha,
            "database_fingerprint_sha256": database_fingerprint,
            "summary": summary,
            "loaded_at": loaded_at,
            "old_database_written": False,
            "selected_by_runtime": False,
        }
        write_receipt(args.receipt, receipt)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    except BaseException:
        try:
            database.psql(
                "UPDATE domeye_data.candidate_registry SET status='failed' WHERE status='loading'"
            )
        except BaseException:
            pass
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LoadError as error:
        print(json.dumps({
            "status": "failed", "error_type": type(error).__name__, "error": str(error),
        }, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
