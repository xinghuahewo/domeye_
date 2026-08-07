#!/usr/bin/env python3
"""构建、装载并验收 RRC25 224-310 S5 只读模型候选。"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


COLLECTOR_ID = "rrc25"
WINDOW_START = "2026-02-24T00:00:00Z"
WINDOW_END = "2026-03-11T00:00:00Z"
FIRST_STATE_POINT = "2026-02-24T00:05:00Z"
STATE_POINT_COUNT = 4320
COUNTRY_BUCKET_COUNT = 241
EVENT_COUNT = 81
EVENT_COUNTRY_COUNT = 43
S4_DATABASE_MODEL = "domeye-event-publication-postgresql/v1"
READ_MODEL_DATABASE_MODEL = "domeye-read-model-postgresql/v1"
READ_MODEL_SCHEMA_VERSION = "rrc25-read-model-store/v1"
SERIES_SCHEMA_VERSION = "rrc25-compact-country-series/v1"
EVENT_SNAPSHOT_VERSION = "rrc25-event-read-model/v1"
REPORT_SNAPSHOT_VERSION = "rrc25-report-snapshot/v1"
EVIDENCE_VIEW_VERSION = "rrc25-prefix-vp-evidence-view/v1"
EVIDENCE_PROJECTOR_VERSION = "1.0.0"
SERIES_MAX_BYTES = 1 << 20
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{7,127}$")
DATABASE_RE = re.compile(r"^[a-z][a-z0-9_]{7,62}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
IMPLEMENTATION_RE = re.compile(r"^[0-9a-f]{40}$")


class CandidateError(RuntimeError):
    """候选违反冻结合同。"""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def ordered_json_bytes(value: Any) -> bytes:
    """复算 Go 结构体 json.Marshal 的声明字段顺序。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=False, separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(8 << 20):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def stable_id(prefix: str, value: Any, length: int = 32) -> str:
    return prefix + sha256_bytes(canonical_bytes(value))[:length]


def sql_literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize_utc_strings(value: Any) -> Any:
    if isinstance(value, str) and value.endswith("+00:00"):
        return value[:-6] + "Z"
    if isinstance(value, list):
        return [normalize_utc_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_utc_strings(item) for key, item in value.items()}
    return value


def run_command(
    command: Sequence[str], *, input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        list(command), input=input_bytes, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if check and result.returncode:
        raise CandidateError(
            "命令失败：{}\n{}".format(
                " ".join(command),
                result.stderr.decode("utf-8", "replace")[-8000:],
            )
        )
    return result


class Postgres:
    def __init__(self, container: str, database: str, user: str = "postgres") -> None:
        self.container = container
        self.database = database
        self.user = user

    def psql(
        self, sql: str, *, database: str | None = None, tuples: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        command = [
            "docker", "exec", "-i", self.container, "psql", "-X",
            "-v", "ON_ERROR_STOP=1", "-U", self.user,
            "-d", database or self.database,
        ]
        if tuples:
            command.extend(["-A", "-t"])
        command.extend(["-c", sql])
        return run_command(command, check=check)

    def scalar(self, sql: str, *, database: str | None = None) -> str:
        return self.psql(sql, database=database, tuples=True).stdout.decode().strip()

    def apply_file(self, path: Path) -> None:
        command = [
            "docker", "exec", "-i", self.container, "psql", "-X",
            "-v", "ON_ERROR_STOP=1", "-U", self.user, "-d", self.database,
            "-f", "-",
        ]
        run_command(command, input_bytes=path.read_bytes())

    def copy_gzip(self, path: Path, table: str, columns: Sequence[str]) -> None:
        copy = (
            "\\copy {} ({}) FROM STDIN WITH "
            "(FORMAT csv, DELIMITER E'\\t', HEADER true, NULL '\\N')"
        ).format(table, ",".join(columns))
        command = [
            "docker", "exec", "-i", self.container, "psql", "-X", "-q",
            "-v", "ON_ERROR_STOP=1", "-U", self.user, "-d", self.database,
            "-c", copy,
        ]
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdin is not None
        try:
            with gzip.open(path, "rb") as source:
                shutil.copyfileobj(source, process.stdin, length=8 << 20)
            process.stdin.close()
            code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        stdout = process.stdout.read() if process.stdout else b""
        stderr = process.stderr.read() if process.stderr else b""
        if code:
            raise CandidateError(
                f"COPY 失败：{path}\n{stdout.decode(errors='replace')[-1000:]}\n"
                f"{stderr.decode(errors='replace')[-8000:]}"
            )


SOURCE_COLUMNS = (
    "candidate_id", "source_id", "source_kind", "dataset_id",
    "content_sha256", "manifest_sha256", "database_name",
    "database_fingerprint_sha256", "object_uri", "object_sha256", "metadata",
)
SERIES_COLUMNS = (
    "candidate_id", "series_id", "country_code", "point_count",
    "artifact_uri", "artifact_sha256", "content_sha256",
    "compressed_size_bytes", "payload",
)
EVIDENCE_COLUMNS = (
    "candidate_id", "evidence_view_id", "incident_id", "country_code",
    "publication_id", "derived_from_route_state_id", "projector_version",
    "row_count", "page_count", "content_sha256", "artifact_uri", "payload",
)
EVENT_COLUMNS = (
    "candidate_id", "snapshot_id", "incident_id", "legacy_reference",
    "country_code", "observation_publication_id", "analysis_publication_id",
    "observation_revision", "analysis_revision", "fact_set_sha256",
    "series_id", "evidence_view_id", "snapshot_sha256", "payload",
)
REPORT_COLUMNS = (
    "candidate_id", "report_snapshot_id", "incident_id", "report_version",
    "event_snapshot_id", "observation_publication_id",
    "analysis_publication_id", "snapshot_sha256", "payload",
)
POINTER_COLUMNS = (
    "candidate_id", "incident_id", "current_report_snapshot_id",
    "pointer_version", "reason",
)


def load_exact_json_pair(root: Path) -> tuple[dict[str, Any], str]:
    manifest_path = root / "manifest.json"
    complete_path = root / "COMPLETE.json"
    if not manifest_path.is_file() or not complete_path.is_file():
        raise CandidateError(f"完成候选缺少双清单：{root}")
    manifest_raw = manifest_path.read_bytes()
    complete_raw = complete_path.read_bytes()
    if manifest_raw != complete_raw:
        raise CandidateError(f"manifest 与 COMPLETE 不逐字一致：{root}")
    value = json.loads(manifest_raw)
    if not isinstance(value, dict) or value.get("status") != "complete":
        raise CandidateError(f"候选完成状态无效：{root}")
    return value, sha256_bytes(manifest_raw)


def load_exact_catalog_pair(root: Path) -> tuple[dict[str, Any], str]:
    catalog_path = root / "catalog.json"
    complete_path = root / "COMPLETE.json"
    if not catalog_path.is_file() or not complete_path.is_file():
        raise CandidateError(f"Evidence 完成候选缺少双清单：{root}")
    catalog_raw = catalog_path.read_bytes()
    complete_raw = complete_path.read_bytes()
    if catalog_raw != complete_raw:
        raise CandidateError(f"Evidence catalog 与 COMPLETE 不逐字一致：{root}")
    value = json.loads(catalog_raw)
    if not isinstance(value, dict) or value.get("status") != "complete":
        raise CandidateError(f"Evidence 候选完成状态无效：{root}")
    return value, sha256_bytes(catalog_raw)


def verify_s4_source(
    root: Path, database: Postgres,
) -> tuple[dict[str, Any], str, str]:
    manifest, manifest_sha = load_exact_json_pair(root)
    required = {
        "schema_version": "rrc25-event-publication-store/v1",
        "database_model": S4_DATABASE_MODEL,
        "collector_id": COLLECTOR_ID,
        "window_start_utc": WINDOW_START,
        "window_end_exclusive_utc": WINDOW_END,
        "state_point_count": STATE_POINT_COUNT,
        "country_bucket_count": COUNTRY_BUCKET_COUNT,
        "event_count": EVENT_COUNT,
        "event_country_count": EVENT_COUNTRY_COUNT,
    }
    for key, expected in required.items():
        if manifest.get(key) != expected:
            raise CandidateError(f"S4 来源字段冲突：{key}")
    row = json.loads(database.scalar("""
SELECT json_build_object(
 'candidate_id',c.candidate_id,'dataset_id',c.dataset_id,
 'collector_id',c.collector_id,'status',c.status,
 'manifest_sha256',c.manifest_sha256,'content_sha256',c.content_sha256,
 'receipt_manifest_sha256',l.manifest_sha256,
 'database_fingerprint_sha256',l.database_fingerprint_sha256,
 'receipt_status',l.status,
 'event_count',(SELECT count(*) FROM domeye_event.incident),
 'pointer_count',(SELECT count(*) FROM domeye_event.publication_pointer),
 'unaligned_count',(SELECT count(*) FROM domeye_event.publication_pointer p
   JOIN domeye_event.publication a ON a.candidate_id=p.candidate_id
    AND a.publication_id=p.current_analysis_publication_id
   WHERE a.derived_from_observation_publication_id<>p.current_observation_publication_id)
)
FROM domeye_event.candidate_registry c
JOIN domeye_event.load_receipt l USING(candidate_id,dataset_id)
WHERE c.status='complete' AND l.status='complete'
"""))
    if (
        row["candidate_id"] != manifest["candidate_id"]
        or row["dataset_id"] != manifest["dataset_id"]
        or row["collector_id"] != COLLECTOR_ID
        or row["status"] != "complete"
        or row["manifest_sha256"] != manifest_sha
        or row["receipt_manifest_sha256"] != manifest_sha
        or row["content_sha256"] != manifest["content_sha256"]
        or row["event_count"] != EVENT_COUNT
        or row["pointer_count"] != EVENT_COUNT
        or row["unaligned_count"] != 0
    ):
        raise CandidateError("S4 文件与事务数据库身份不闭合")
    return manifest, manifest_sha, row["database_fingerprint_sha256"]


def export_current_events(database: Postgres) -> list[dict[str, Any]]:
    sql = """COPY (
SELECT jsonb_build_object(
 'incident',to_jsonb(i),
 'pointer',to_jsonb(pp),
 'observation',jsonb_build_object(
   'publication_id',op.publication_id,'revision',op.revision,
   'sequence_in_revision',op.sequence_in_revision,'data_through',op.data_through,
   'fact_set_sha256',op.fact_set_sha256,'content_sha256',op.content_sha256,
   'artifact_sha256',op.artifact_sha256,'snapshot',op.snapshot),
 'analysis',jsonb_build_object(
   'publication_id',ap.publication_id,'revision',ap.revision,
   'sequence_in_revision',ap.sequence_in_revision,'data_through',ap.data_through,
   'derived_from_observation_publication_id',ap.derived_from_observation_publication_id,
   'fact_set_sha256',ap.fact_set_sha256,'content_sha256',ap.content_sha256,
   'artifact_sha256',ap.artifact_sha256,'snapshot',ap.snapshot),
 'facts',(SELECT jsonb_agg(to_jsonb(f) ORDER BY f.fact_sequence)
          FROM domeye_event.event_fact f
          WHERE f.candidate_id=i.candidate_id AND f.incident_id=i.incident_id)
)
FROM domeye_event.incident i
JOIN domeye_event.publication_pointer pp USING(candidate_id,incident_id)
JOIN domeye_event.publication op
  ON op.candidate_id=pp.candidate_id
 AND op.publication_id=pp.current_observation_publication_id
JOIN domeye_event.publication ap
  ON ap.candidate_id=pp.candidate_id
 AND ap.publication_id=pp.current_analysis_publication_id
WHERE i.status='complete'
ORDER BY i.incident_id
) TO STDOUT"""
    result = database.psql(sql)
    events = [
        normalize_utc_strings(json.loads(line))
        for line in result.stdout.decode().splitlines() if line
    ]
    if len(events) != EVENT_COUNT:
        raise CandidateError("S4 当前事件人口不是 81")
    countries = {event["incident"]["country_code"] for event in events}
    if len(countries) != EVENT_COUNTRY_COUNT:
        raise CandidateError("S4 当前事件国家人口不是 43")
    for event in events:
        incident = event["incident"]
        observation = event["observation"]
        analysis = event["analysis"]
        facts = event["facts"]
        if (
            incident["collector_id"] != COLLECTOR_ID
            or len(facts) != 3
            or [fact["stage"] for fact in facts] != ["detected", "ongoing", "final"]
            or analysis["derived_from_observation_publication_id"]
            != observation["publication_id"]
            or observation["data_through"] != WINDOW_END
            or analysis["data_through"] != WINDOW_END
        ):
            raise CandidateError(f"S4 当前事件组合未对齐：{incident['incident_id']}")
    return events


def export_country_metrics(
    database: Postgres, metric_dataset_id: str, countries: Sequence[str],
) -> dict[str, list[list[int]]]:
    country_list = ",".join(sql_literal(country) for country in countries)
    sql = f"""COPY (
SELECT to_char(m.state_point_utc AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),
       m.subject_id,
       m.baseline_route_state_count_v4,m.baseline_route_state_count_v6,
       m.cohort_visible_route_state_count_v4,m.cohort_visible_route_state_count_v6,
       m.current_visible_route_state_count_v4,m.current_visible_route_state_count_v6,
       m.announcement_count_v4,m.announcement_count_v6,
       m.withdrawal_count_v4,m.withdrawal_count_v6,
       s.slot,s.source_route_state_slot_sha256,s.metric_snapshot_sha256,
       s.content_sha256,s.quality_status,s.gap_status
FROM domeye_data.route_metric_5m m
JOIN domeye_data.metric_slot_5m s USING(metric_dataset_id,state_point_utc)
WHERE m.metric_dataset_id={sql_literal(metric_dataset_id)}
  AND m.subject_type='country' AND m.subject_id IN ({country_list})
ORDER BY m.subject_id,m.state_point_utc
) TO STDOUT WITH (FORMAT csv)"""
    command = [
        "docker", "exec", "-i", database.container, "psql", "-X", "-q",
        "-v", "ON_ERROR_STOP=1", "-U", database.user, "-d", database.database,
        "-c", sql,
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    rows: dict[str, list[list[int]]] = {country: [] for country in countries}
    audit: dict[str, dict[str, str]] = {}
    reader = csv.reader(io.TextIOWrapper(process.stdout, encoding="utf-8", newline=""))
    for row in reader:
        if len(row) != 18 or row[16] != "complete" or row[17] != "none":
            process.kill()
            raise CandidateError("S3 国家指标槽未通过质量门")
        country = row[1]
        rows[country].append([int(value) for value in row[2:12]])
        info = audit.setdefault(country, {})
        if row[12] == "1":
            info.update(first_state_point=row[0], first_slot_sha256=row[15])
        if row[12] == str(STATE_POINT_COUNT):
            info.update(
                last_state_point=row[0], last_slot_sha256=row[15],
                final_route_state_slot_sha256=row[13],
                final_metric_snapshot_sha256=row[14],
            )
    stderr = process.stderr.read() if process.stderr else b""
    code = process.wait()
    if code:
        raise CandidateError(f"S3 指标导出失败：{stderr.decode(errors='replace')[-8000:]}")
    for country in countries:
        if len(rows[country]) != STATE_POINT_COUNT:
            raise CandidateError(f"{country} 紧凑序列不是 4,320 点")
        if (
            audit[country].get("first_state_point") != FIRST_STATE_POINT
            or audit[country].get("last_state_point") != WINDOW_END
        ):
            raise CandidateError(f"{country} 紧凑序列窗口错误")
    rows["__audit__"] = audit  # type: ignore[assignment]
    return rows


def deterministic_gzip(raw: bytes, level: int = 9) -> bytes:
    target = io.BytesIO()
    with gzip.GzipFile(fileobj=target, mode="wb", filename="", mtime=0, compresslevel=level) as output:
        output.write(raw)
    return target.getvalue()


def write_bytes_create_only(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def write_json_create_only(path: Path, value: Any) -> None:
    write_bytes_create_only(path, canonical_bytes(value) + b"\n")


def tsv_cell(value: Any) -> Any:
    if value is None:
        return r"\N"
    if isinstance(value, (dict, list)):
        return canonical_bytes(value).decode()
    if isinstance(value, bool):
        return "t" if value else "f"
    return value


def write_tsv_gzip(
    path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    raw = io.StringIO(newline="")
    writer = csv.writer(raw, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    count = 0
    for row in rows:
        writer.writerow([tsv_cell(row.get(column)) for column in columns])
        count += 1
    uncompressed = raw.getvalue().encode()
    compressed = deterministic_gzip(uncompressed)
    write_bytes_create_only(path, compressed)
    return {
        "path": path.name, "row_count": count, "size_bytes": len(compressed),
        "sha256": sha256_bytes(compressed),
        "content_sha256": sha256_bytes(uncompressed),
    }


def build_series(
    root: Path, candidate_id: str, metric_dataset_id: str,
    metrics: Mapping[str, Any], countries: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    by_country: dict[str, dict[str, Any]] = {}
    files: list[dict[str, Any]] = []
    columns = [
        "baseline_v4", "baseline_v6", "cohort_visible_v4", "cohort_visible_v6",
        "current_visible_v4", "current_visible_v6", "announcement_v4",
        "announcement_v6", "withdrawal_v4", "withdrawal_v6",
    ]
    audit = metrics["__audit__"]
    for country in sorted(countries):
        matrix = metrics[country]
        values = [[point[index] for point in matrix] for index in range(len(columns))]
        semantic = {
            "schema_version": SERIES_SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "metric_dataset_id": metric_dataset_id,
            "collector_id": COLLECTOR_ID,
            "country_code": country,
            "window_start_utc": WINDOW_START,
            "window_end_exclusive_utc": WINDOW_END,
            "first_state_point_utc": FIRST_STATE_POINT,
            "step_seconds": 300,
            "point_count": STATE_POINT_COUNT,
            "columns": columns,
            "values": values,
            "quality": {"status": "complete", "missing": 0, "finality": "final"},
            "audit": audit[country],
        }
        content_sha = sha256_bytes(canonical_bytes(semantic))
        series_id = stable_id("compact_series_v1_", {
            "candidate_id": candidate_id, "metric_dataset_id": metric_dataset_id,
            "country_code": country, "content_sha256": content_sha,
        })
        payload = dict(semantic)
        payload.update(series_id=series_id, content_sha256=content_sha)
        uncompressed = canonical_bytes(payload) + b"\n"
        compressed = deterministic_gzip(uncompressed)
        if len(compressed) > SERIES_MAX_BYTES:
            raise CandidateError(f"{country} 4,320 点压缩序列超过 1 MiB")
        relative = f"series/{country}.json.gz"
        write_bytes_create_only(root / relative, compressed)
        physical_sha = sha256_bytes(compressed)
        metadata = {
            "candidate_id": candidate_id, "series_id": series_id,
            "country_code": country, "point_count": STATE_POINT_COUNT,
            "artifact_uri": relative, "artifact_sha256": physical_sha,
            "content_sha256": content_sha, "compressed_size_bytes": len(compressed),
            "payload": {
                "schema_version": SERIES_SCHEMA_VERSION,
                "window_start_utc": WINDOW_START,
                "window_end_exclusive_utc": WINDOW_END,
                "columns": columns, "quality": payload["quality"], "audit": audit[country],
            },
        }
        rows.append(metadata)
        by_country[country] = metadata
        files.append({
            "role": "compact_series", "path": relative,
            "row_count": STATE_POINT_COUNT, "size_bytes": len(compressed),
            "sha256": physical_sha, "content_sha256": sha256_bytes(uncompressed),
        })
    return rows, by_country, files


def run_evidence_builder(
    binary: Path, route_state_root: Path, compatible_mapping: Path,
    revised_mapping: Path, countries: Sequence[str], output: Path,
) -> dict[str, Any]:
    result = run_command([
        str(binary), "--route-state-root", str(route_state_root),
        "--compatible-mapping", str(compatible_mapping),
        "--revised-mapping", str(revised_mapping),
        "--countries", ",".join(sorted(countries)),
        "--output", str(output), "--page-size", "1000",
    ])
    catalog = json.loads(result.stdout)
    persisted, _ = load_exact_catalog_pair(output)
    if catalog != persisted:
        raise CandidateError("Prefix×VP Evidence 构建输出与完成清单不一致")
    return catalog


def build_evidence_views(
    candidate_id: str, events: Sequence[Mapping[str, Any]],
    catalog: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    country_entries = {row["country_code"]: row for row in catalog["countries"]}
    rows: list[dict[str, Any]] = []
    by_incident: dict[str, dict[str, Any]] = {}
    for event in events:
        incident = event["incident"]
        observation = event["observation"]
        country = incident["country_code"]
        entry = country_entries[country]
        semantic = {
            "schema_version": EVIDENCE_VIEW_VERSION,
            "candidate_id": candidate_id,
            "incident_id": incident["incident_id"],
            "country_code": country,
            "publication_id": observation["publication_id"],
            "derived_from_route_state_id": catalog["derived_from_route_state_id"],
            "source_route_state_dataset_id": catalog["source_route_state_dataset_id"],
            "source_route_state_content_sha256": catalog["source_route_state_content_sha256"],
            "seed_route_state_id": catalog["seed_route_state_id"],
            "mapping_version": catalog["mapping_version"],
            "projector": {
                "name": catalog["projector_name"],
                "version": catalog["projector_version"],
            },
            "row_count": entry["row_count"],
            "page_count": entry["page_count"],
            "page_size": catalog["page_size"],
            "page_uri_template": f"prefix-vp/pages/{country}/page-{{page:06d}}.json.gz",
            "country_content_sha256": entry["content_sha256"],
            "limitations": [
                "这是指定 Seed cohort 在终点 RouteState 的派生下钻视图，不是第二套 RouteState 事实",
                "仅描述 RRC25 单采集器所见 BGP 控制面，不证明用户影响、原因或完全恢复",
            ],
        }
        content_sha = sha256_bytes(canonical_bytes(semantic))
        evidence_id = stable_id("prefix_vp_evidence_view_v1_", {
            "incident_id": incident["incident_id"],
            "publication_id": observation["publication_id"],
            "derived_from_route_state_id": catalog["derived_from_route_state_id"],
            "content_sha256": content_sha,
        })
        payload = dict(semantic)
        payload.update(evidence_view_id=evidence_id, content_sha256=content_sha)
        row = {
            "candidate_id": candidate_id, "evidence_view_id": evidence_id,
            "incident_id": incident["incident_id"], "country_code": country,
            "publication_id": observation["publication_id"],
            "derived_from_route_state_id": catalog["derived_from_route_state_id"],
            "projector_version": catalog["projector_version"],
            "row_count": entry["row_count"], "page_count": entry["page_count"],
            "content_sha256": content_sha, "artifact_uri": "prefix-vp/catalog.json",
            "payload": payload,
        }
        rows.append(row)
        by_incident[incident["incident_id"]] = row
    return rows, by_incident


def normalized_limitations(event: Mapping[str, Any], evidence: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for source in (
        event["observation"]["snapshot"].get("limitations", []),
        event["analysis"]["snapshot"].get("limitations", []),
        evidence["payload"].get("limitations", []),
    ):
        for value in source:
            if value not in values:
                values.append(value)
    return values


def snapshot_hash(payload: Mapping[str, Any]) -> str:
    semantic = dict(payload)
    semantic["snapshot_sha256"] = ""
    semantic.pop("snapshot_id", None)
    semantic.pop("report_snapshot_id", None)
    return sha256_bytes(canonical_bytes(semantic))


def build_event_and_reports(
    candidate_id: str, dataset_id: str, events: Sequence[Mapping[str, Any]],
    series_by_country: Mapping[str, Mapping[str, Any]],
    evidence_by_incident: Mapping[str, Mapping[str, Any]],
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
    dict[str, dict[str, Any]], dict[str, dict[str, Any]],
]:
    event_rows: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    pointer_rows: list[dict[str, Any]] = []
    events_by_incident: dict[str, dict[str, Any]] = {}
    reports_by_id: dict[str, dict[str, Any]] = {}
    refresh_incident = sorted(
        event["incident"]["incident_id"] for event in events
        if event["incident"]["country_code"] == "IR"
    )[0]
    for event in events:
        incident = event["incident"]
        observation = event["observation"]
        analysis = event["analysis"]
        facts = event["facts"]
        series = series_by_country[incident["country_code"]]
        evidence = evidence_by_incident[incident["incident_id"]]
        base = {
            "schema_version": EVENT_SNAPSHOT_VERSION,
            "candidate_id": candidate_id,
            "read_model_dataset_id": dataset_id,
            "incident": {
                "incident_id": incident["incident_id"],
                "legacy_reference": incident["legacy_reference"],
                "country_code": incident["country_code"],
                "country_name": incident["country_name"],
                "event_type": incident["event_type"],
                "detected_at": incident["detected_at"],
                "source_system": incident["source_system"],
            },
            "observation_publication": {
                "publication_id": observation["publication_id"],
                "revision": observation["revision"],
                "sequence_in_revision": observation["sequence_in_revision"],
                "data_through": observation["data_through"],
                "fact_set_sha256": observation["fact_set_sha256"],
                "content_sha256": observation["content_sha256"],
                "snapshot": observation["snapshot"],
            },
            "analysis_publication": {
                "publication_id": analysis["publication_id"],
                "revision": analysis["revision"],
                "sequence_in_revision": analysis["sequence_in_revision"],
                "data_through": analysis["data_through"],
                "derived_from_observation_publication_id": analysis[
                    "derived_from_observation_publication_id"
                ],
                "content_sha256": analysis["content_sha256"],
                "trend_profile": analysis["snapshot"]["trend_profile"],
            },
            "window_contract": {
                "collector_id": COLLECTOR_ID,
                "start_utc": WINDOW_START,
                "end_exclusive_utc": WINDOW_END,
                "state_point_count": STATE_POINT_COUNT,
                "state_point_semantics": "five_minute_slot_end",
                "quality_status": "complete", "missing": 0, "finality": "final",
            },
            "fact_set": facts,
            "fact_set_sha256": observation["fact_set_sha256"],
            "quality": observation["snapshot"]["quality"],
            "audit": {
                "publication_pointer_version": event["pointer"]["pointer_version"],
                "observation_content_sha256": observation["content_sha256"],
                "analysis_content_sha256": analysis["content_sha256"],
                "metric_dataset_id": observation["snapshot"]["metric_dataset_id"],
            },
            "series_ref": {
                key: series[key] for key in (
                    "series_id", "artifact_uri", "artifact_sha256",
                    "content_sha256", "compressed_size_bytes", "point_count",
                )
            },
            "evidence_refs": [{
                "evidence_view_id": evidence["evidence_view_id"],
                "publication_id": evidence["publication_id"],
                "derived_from_route_state_id": evidence["derived_from_route_state_id"],
                "projector_version": evidence["projector_version"],
                "content_sha256": evidence["content_sha256"],
                "row_count": evidence["row_count"], "page_count": evidence["page_count"],
            }],
            "limitations": normalized_limitations(event, evidence),
            "snapshot_sha256": "",
        }
        digest = snapshot_hash(base)
        snapshot_id = stable_id("event_read_model_v1_", {
            "incident_id": incident["incident_id"],
            "observation_publication_id": observation["publication_id"],
            "analysis_publication_id": analysis["publication_id"],
            "snapshot_sha256": digest,
        })
        payload = dict(base)
        payload.update(snapshot_id=snapshot_id, snapshot_sha256=digest)
        row = {
            "candidate_id": candidate_id, "snapshot_id": snapshot_id,
            "incident_id": incident["incident_id"],
            "legacy_reference": incident["legacy_reference"],
            "country_code": incident["country_code"],
            "observation_publication_id": observation["publication_id"],
            "analysis_publication_id": analysis["publication_id"],
            "observation_revision": observation["revision"],
            "analysis_revision": analysis["revision"],
            "fact_set_sha256": observation["fact_set_sha256"],
            "series_id": series["series_id"],
            "evidence_view_id": evidence["evidence_view_id"],
            "snapshot_sha256": digest, "payload": payload,
        }
        event_rows.append(row)
        events_by_incident[incident["incident_id"]] = row

        max_version = 2 if incident["incident_id"] == refresh_incident else 1
        current_report_id = ""
        for version in range(1, max_version + 1):
            report = {
                "schema_version": REPORT_SNAPSHOT_VERSION,
                "candidate_id": candidate_id,
                "read_model_dataset_id": dataset_id,
                "report_version": version,
                "report_request": {
                    "kind": "initial" if version == 1 else "explicit_refresh",
                    "reason": "initial_snapshot" if version == 1 else "acceptance_refresh",
                },
                "incident": payload["incident"],
                "observation_publication": payload["observation_publication"],
                "analysis_publication": payload["analysis_publication"],
                "revision": {
                    "observation": observation["revision"],
                    "analysis": analysis["revision"],
                },
                "window_contract": payload["window_contract"],
                "fact_set": facts,
                "fact_set_sha256": observation["fact_set_sha256"],
                "trend_profile": analysis["snapshot"]["trend_profile"],
                "evidence_refs": payload["evidence_refs"],
                "limitations": payload["limitations"],
                "model_contract": {
                    "role": "narrate_validated_whitelisted_facts_only",
                    "forbidden": [
                        "recompute_metrics", "change_numbers", "fill_unknown",
                        "choose_publication", "promote_causal_claims",
                    ],
                },
                "event_snapshot_id": snapshot_id,
                "event_snapshot_sha256": digest,
                "snapshot_sha256": "",
            }
            report_digest = snapshot_hash(report)
            report_id = stable_id("report_snapshot_v1_", {
                "incident_id": incident["incident_id"], "report_version": version,
                "snapshot_sha256": report_digest,
            })
            report_payload = dict(report)
            report_payload.update(
                report_snapshot_id=report_id, snapshot_sha256=report_digest,
            )
            report_row = {
                "candidate_id": candidate_id, "report_snapshot_id": report_id,
                "incident_id": incident["incident_id"], "report_version": version,
                "event_snapshot_id": snapshot_id,
                "observation_publication_id": observation["publication_id"],
                "analysis_publication_id": analysis["publication_id"],
                "snapshot_sha256": report_digest, "payload": report_payload,
            }
            report_rows.append(report_row)
            reports_by_id[report_id] = report_row
            current_report_id = report_id
        pointer_rows.append({
            "candidate_id": candidate_id, "incident_id": incident["incident_id"],
            "current_report_snapshot_id": current_report_id,
            "pointer_version": max_version,
            "reason": "explicit_refresh" if max_version == 2 else "initial_snapshot",
        })
    return event_rows, report_rows, pointer_rows, events_by_incident, reports_by_id


def source_rows(
    candidate_id: str, s4_manifest: Mapping[str, Any], s4_manifest_sha: str,
    s4_database: str, s4_database_fingerprint: str,
    route_state_manifest: Mapping[str, Any], route_state_manifest_sha: str,
    route_state_root: Path, evidence_catalog: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": candidate_id, "source_id": s4_manifest["dataset_id"],
            "source_kind": "event_publication", "dataset_id": s4_manifest["dataset_id"],
            "content_sha256": s4_manifest["content_sha256"],
            "manifest_sha256": s4_manifest_sha, "database_name": s4_database,
            "database_fingerprint_sha256": s4_database_fingerprint,
            "object_uri": str(s4_manifest.get("source_metric_database_name")),
            "object_sha256": s4_manifest["source_metric_database_fingerprint_sha256"],
            "metadata": {"database_model": S4_DATABASE_MODEL, "status": "complete"},
        },
        {
            "candidate_id": candidate_id, "source_id": route_state_manifest["dataset_id"],
            "source_kind": "route_state", "dataset_id": route_state_manifest["dataset_id"],
            "content_sha256": route_state_manifest["content_sha256"],
            "manifest_sha256": route_state_manifest_sha, "database_name": None,
            "database_fingerprint_sha256": None, "object_uri": str(route_state_root),
            "object_sha256": route_state_manifest_sha,
            "metadata": {
                "logical_authority": "single_route_state",
                "final_checkpoint_id": evidence_catalog["derived_from_route_state_id"],
                "final_checkpoint_content_sha256": evidence_catalog["route_state_content_sha256"],
                "mapping_version": evidence_catalog["mapping_version"],
            },
        },
    ]


def manifest_content_sha(manifest: Mapping[str, Any]) -> str:
    value = dict(manifest)
    value["content_sha256"] = ""
    value["files"] = [
        {key: file[key] for key in ("role", "path", "row_count", "content_sha256")}
        for file in manifest["files"]
    ]
    return sha256_bytes(canonical_bytes(value))


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    if not IMPLEMENTATION_RE.fullmatch(args.implementation_id):
        raise CandidateError("S5 implementation-id 必须是 40 位提交 SHA")
    output = Path(args.output)
    temporary = Path(str(output) + ".tmp")
    if output.exists() or temporary.exists():
        raise CandidateError("S5 输出或临时目录已存在；create-only 拒绝覆盖")
    s4_database = Postgres(args.container, args.s4_database, args.user)
    s3_database = Postgres(args.container, args.s3_database, args.user)
    s4_manifest, s4_manifest_sha, s4_db_fingerprint = verify_s4_source(
        Path(args.s4_root), s4_database,
    )
    if args.s3_database != s4_manifest["source_metric_database_name"]:
        raise CandidateError("S3 数据库不是 S4 登记来源")
    s3_fingerprint = s3_database.scalar("""
SELECT database_fingerprint_sha256 FROM domeye_data.load_receipt
WHERE status='complete'
""")
    if s3_fingerprint != s4_manifest["source_metric_database_fingerprint_sha256"]:
        raise CandidateError("S3 数据库指纹与 S4 来源绑定冲突")
    route_state_manifest, route_state_manifest_sha = load_exact_json_pair(
        Path(args.route_state_root)
    )
    if (
        route_state_manifest.get("schema_version") != "rrc25-route-state-store/v1"
        or route_state_manifest.get("collector_id") != COLLECTOR_ID
        or route_state_manifest.get("window_start_utc") != WINDOW_START
        or route_state_manifest.get("window_end_exclusive_utc") != WINDOW_END
        or route_state_manifest.get("data_through") != WINDOW_END
    ):
        raise CandidateError("S2 RouteState 来源窗口或完成身份冲突")
    events = export_current_events(s4_database)
    countries = sorted({event["incident"]["country_code"] for event in events})
    candidate_id = s4_manifest["candidate_id"]
    dataset_id = stable_id("read_model_dataset_v1_", {
        "candidate_id": candidate_id, "implementation_id": args.implementation_id,
        "source_event_publication_dataset_id": s4_manifest["dataset_id"],
        "source_event_publication_content_sha256": s4_manifest["content_sha256"],
        "source_route_state_dataset_id": route_state_manifest["dataset_id"],
        "source_route_state_content_sha256": route_state_manifest["content_sha256"],
        "window": [WINDOW_START, WINDOW_END],
    })
    run_id = stable_id("read_model_run_v1_", {"dataset_id": dataset_id})
    temporary.mkdir(parents=True)
    try:
        metrics = export_country_metrics(
            s3_database, s4_manifest["source_metric_dataset_id"], countries,
        )
        series_rows, series_by_country, series_files = build_series(
            temporary, candidate_id, s4_manifest["source_metric_dataset_id"],
            metrics, countries,
        )
        evidence_catalog = run_evidence_builder(
            Path(args.evidence_binary), Path(args.route_state_root),
            Path(args.compatible_mapping), Path(args.revised_mapping), countries,
            temporary / "prefix-vp",
        )
        baseline_by_country = {
            country: metrics[country][0][0] + metrics[country][0][1]
            for country in countries
        }
        actual_by_country = {
            row["country_code"]: row["row_count"] for row in evidence_catalog["countries"]
        }
        if baseline_by_country != actual_by_country:
            raise CandidateError("Prefix×VP Evidence 人口与 S3 RouteState cohort 不一致")
        evidence_rows, evidence_by_incident = build_evidence_views(
            candidate_id, events, evidence_catalog,
        )
        event_rows, report_rows, pointer_rows, _, _ = build_event_and_reports(
            candidate_id, dataset_id, events, series_by_country, evidence_by_incident,
        )
        sources = source_rows(
            candidate_id, s4_manifest, s4_manifest_sha, args.s4_database,
            s4_db_fingerprint, route_state_manifest, route_state_manifest_sha,
            Path(args.route_state_root), evidence_catalog,
        )
        files = list(series_files)
        for filename, role, columns, rows in (
            ("source-binding.tsv.gz", "source_binding", SOURCE_COLUMNS, sources),
            ("series-object.tsv.gz", "series_object", SERIES_COLUMNS, series_rows),
            ("prefix-vp-evidence-view.tsv.gz", "prefix_vp_evidence_view", EVIDENCE_COLUMNS, evidence_rows),
            ("event-read-model.tsv.gz", "event_read_model", EVENT_COLUMNS, event_rows),
            ("report-snapshot.tsv.gz", "report_snapshot", REPORT_COLUMNS, report_rows),
            ("report-pointer.tsv.gz", "report_pointer", POINTER_COLUMNS, pointer_rows),
        ):
            metadata = write_tsv_gzip(temporary / filename, columns, rows)
            metadata["role"] = role
            files.append(metadata)
        catalog_path = temporary / "prefix-vp" / "catalog.json"
        catalog_sha, catalog_size = sha256_file(catalog_path)
        files.append({
            "role": "prefix_vp_evidence_catalog", "path": "prefix-vp/catalog.json",
            "row_count": evidence_catalog["row_count"], "size_bytes": catalog_size,
            "sha256": catalog_sha,
            "content_sha256": evidence_catalog["content_sha256"],
        })
        files.sort(key=lambda item: (item["role"], item["path"]))
        manifest = {
            "schema_version": READ_MODEL_SCHEMA_VERSION, "status": "complete",
            "candidate_id": candidate_id, "run_id": run_id, "dataset_id": dataset_id,
            "implementation_id": args.implementation_id,
            "database_model": READ_MODEL_DATABASE_MODEL,
            "collector_id": COLLECTOR_ID, "window_start_utc": WINDOW_START,
            "window_end_exclusive_utc": WINDOW_END,
            "state_point_count": STATE_POINT_COUNT,
            "country_bucket_count": COUNTRY_BUCKET_COUNT,
            "event_count": len(event_rows), "event_country_count": len(countries),
            "series_count": len(series_rows), "report_snapshot_count": len(report_rows),
            "report_pointer_count": len(pointer_rows),
            "prefix_vp_evidence_view_count": len(evidence_rows),
            "prefix_vp_evidence_row_count": evidence_catalog["row_count"],
            "prefix_vp_evidence_catalog_sha256": catalog_sha,
            "prefix_vp_evidence_content_sha256": evidence_catalog["content_sha256"],
            "derived_from_route_state_id": evidence_catalog["derived_from_route_state_id"],
            "source_route_state_dataset_id": route_state_manifest["dataset_id"],
            "source_route_state_content_sha256": route_state_manifest["content_sha256"],
            "source_route_state_manifest_sha256": route_state_manifest_sha,
            "source_event_publication_dataset_id": s4_manifest["dataset_id"],
            "source_event_publication_content_sha256": s4_manifest["content_sha256"],
            "source_event_publication_manifest_sha256": s4_manifest_sha,
            "source_event_publication_database_name": args.s4_database,
            "source_event_publication_database_fingerprint_sha256": s4_db_fingerprint,
            "source_metric_database_name": args.s3_database,
            "source_metric_database_fingerprint_sha256": s3_fingerprint,
            "api_read_semantics": "precompiled_read_model_only",
            "report_semantics": "immutable_snapshot_new_version_on_refresh",
            "prefix_vp_semantics": "derived_view_not_independent_fact",
            "files": files, "content_sha256": "",
        }
        manifest["content_sha256"] = manifest_content_sha(manifest)
        raw = canonical_bytes(manifest) + b"\n"
        write_bytes_create_only(temporary / "manifest.json", raw)
        write_bytes_create_only(temporary / "COMPLETE.json", raw)
        os.rename(temporary, output)
        return manifest
    except BaseException:
        raise


def read_tsv_gzip(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source, delimiter="\t"))


def verify_evidence_catalog(root: Path, expected: Mapping[str, Any]) -> dict[str, Any]:
    catalog, _ = load_exact_catalog_pair(root / "prefix-vp")
    if (
        catalog.get("schema_version") != "rrc25-prefix-vp-evidence-catalog/v1"
        or catalog.get("status") != "complete"
        or catalog.get("collector_id") != COLLECTOR_ID
        or catalog.get("content_sha256") != expected["prefix_vp_evidence_content_sha256"]
        or catalog.get("derived_from_route_state_id") != expected["derived_from_route_state_id"]
    ):
        raise CandidateError("Prefix×VP Evidence catalog 身份冲突")
    row_count = 0
    for country in catalog["countries"]:
        country_hash = hashlib.sha256()
        country_rows = 0
        for page in country["pages"]:
            path = root / "prefix-vp" / "pages" / page["path"]
            actual_sha, actual_size = sha256_file(path)
            if actual_sha != page["sha256"] or actual_size != page["size_bytes"]:
                raise CandidateError(f"Prefix×VP Evidence 页摘要冲突：{path}")
            raw = gzip.decompress(path.read_bytes())
            if sha256_bytes(raw) != page["content_sha256"]:
                raise CandidateError(f"Prefix×VP Evidence 页内容冲突：{path}")
            payload = json.loads(raw)
            if (
                payload["country_code"] != country["country_code"]
                or payload["page"] != page["page"]
                or len(payload["rows"]) != page["row_count"]
            ):
                raise CandidateError(f"Prefix×VP Evidence 页人口冲突：{path}")
            for row in payload["rows"]:
                country_hash.update(ordered_json_bytes(row) + b"\n")
            country_rows += len(payload["rows"])
        if country_rows != country["row_count"] or country_hash.hexdigest() != country["content_sha256"]:
            raise CandidateError(f"Prefix×VP Evidence 国家闭合失败：{country['country_code']}")
        row_count += country_rows
    if row_count != catalog["row_count"]:
        raise CandidateError("Prefix×VP Evidence 总人口不闭合")
    semantic = dict(catalog)
    semantic["content_sha256"] = ""
    if sha256_bytes(ordered_json_bytes(semantic)) != catalog["content_sha256"]:
        raise CandidateError("Prefix×VP Evidence catalog 内容身份错误")
    return catalog


def verify_candidate(root: Path, *, deep_evidence: bool = True) -> dict[str, Any]:
    manifest, manifest_sha = load_exact_json_pair(root)
    required = {
        "schema_version": READ_MODEL_SCHEMA_VERSION, "collector_id": COLLECTOR_ID,
        "window_start_utc": WINDOW_START, "window_end_exclusive_utc": WINDOW_END,
        "state_point_count": STATE_POINT_COUNT, "country_bucket_count": COUNTRY_BUCKET_COUNT,
        "event_count": EVENT_COUNT, "event_country_count": EVENT_COUNTRY_COUNT,
        "series_count": EVENT_COUNTRY_COUNT, "report_snapshot_count": EVENT_COUNT + 1,
        "report_pointer_count": EVENT_COUNT, "prefix_vp_evidence_view_count": EVENT_COUNT,
    }
    for key, expected in required.items():
        if manifest.get(key) != expected:
            raise CandidateError(f"S5 manifest 字段冲突：{key}")
    if manifest.get("content_sha256") != manifest_content_sha(manifest):
        raise CandidateError("S5 manifest 内容身份错误")
    for entry in manifest["files"]:
        path = root / entry["path"]
        actual_sha, actual_size = sha256_file(path)
        if actual_sha != entry["sha256"] or actual_size != entry["size_bytes"]:
            raise CandidateError(f"S5 文件身份冲突：{path}")
        if entry["role"] == "compact_series":
            raw = gzip.decompress(path.read_bytes())
            if sha256_bytes(raw) != entry["content_sha256"]:
                raise CandidateError(f"紧凑序列内容身份冲突：{path}")
            payload = json.loads(raw)
            if payload["point_count"] != STATE_POINT_COUNT or len(payload["values"]) != 10:
                raise CandidateError(f"紧凑序列结构错误：{path}")
            if entry["size_bytes"] > SERIES_MAX_BYTES:
                raise CandidateError(f"紧凑序列超过 1 MiB：{path}")
        elif entry["role"] != "prefix_vp_evidence_catalog":
            with gzip.open(path, "rb") as source:
                raw = source.read()
            if sha256_bytes(raw) != entry["content_sha256"]:
                raise CandidateError(f"S5 TSV 内容身份冲突：{path}")
    if deep_evidence:
        verify_evidence_catalog(root, manifest)
    event_rows = read_tsv_gzip(root / "event-read-model.tsv.gz")
    report_rows = read_tsv_gzip(root / "report-snapshot.tsv.gz")
    evidence_rows = read_tsv_gzip(root / "prefix-vp-evidence-view.tsv.gz")
    if len(event_rows) != EVENT_COUNT or len(report_rows) != EVENT_COUNT + 1 or len(evidence_rows) != EVENT_COUNT:
        raise CandidateError("S5 读模型人口不闭合")
    for row in event_rows:
        payload = json.loads(row["payload"])
        if snapshot_hash(payload) != row["snapshot_sha256"]:
            raise CandidateError("事件快照内容身份错误")
        if (
            payload["window_contract"]["collector_id"] != COLLECTOR_ID
            or payload["window_contract"]["state_point_count"] != STATE_POINT_COUNT
            or payload["observation_publication"]["publication_id"] != row["observation_publication_id"]
            or payload["analysis_publication"]["derived_from_observation_publication_id"]
            != row["observation_publication_id"]
        ):
            raise CandidateError("事件快照发布组合未对齐")
    for row in report_rows:
        payload = json.loads(row["payload"])
        if snapshot_hash(payload) != row["snapshot_sha256"]:
            raise CandidateError("报告快照内容身份错误")
        if payload["model_contract"]["role"] != "narrate_validated_whitelisted_facts_only":
            raise CandidateError("报告模型事实边界缺失")
    return {"manifest": manifest, "manifest_sha256": manifest_sha}


def schema_fingerprint(database: Postgres) -> str:
    result = run_command([
        "docker", "exec", database.container, "pg_dump", "-U", database.user,
        "-d", database.database, "--schema-only", "--no-owner", "--no-privileges",
    ])
    return sha256_bytes(result.stdout)


def database_summary(database: Postgres) -> dict[str, Any]:
    return json.loads(database.scalar("""
SELECT json_build_object(
 'candidate_count',(SELECT count(*) FROM domeye_read.candidate_registry WHERE status='complete'),
 'source_count',(SELECT count(*) FROM domeye_read.source_binding),
 'series_count',(SELECT count(*) FROM domeye_read.series_object),
 'series_max_bytes',(SELECT max(compressed_size_bytes) FROM domeye_read.series_object),
 'evidence_view_count',(SELECT count(*) FROM domeye_read.prefix_vp_evidence_view),
 'evidence_row_count',(SELECT sum(row_count) FROM domeye_read.prefix_vp_evidence_view),
 'event_count',(SELECT count(*) FROM domeye_read.event_read_model),
 'report_count',(SELECT count(*) FROM domeye_read.report_snapshot),
 'report_pointer_count',(SELECT count(*) FROM domeye_read.report_pointer),
 'report_v2_count',(SELECT count(*) FROM domeye_read.report_snapshot WHERE report_version=2),
 'binding_mismatch',(SELECT count(*) FROM domeye_read.event_read_model e
   JOIN domeye_read.prefix_vp_evidence_view v USING(candidate_id,evidence_view_id)
   WHERE e.incident_id<>v.incident_id OR e.observation_publication_id<>v.publication_id),
 'report_mismatch',(SELECT count(*) FROM domeye_read.report_snapshot r
   JOIN domeye_read.event_read_model e ON e.candidate_id=r.candidate_id AND e.snapshot_id=r.event_snapshot_id
   WHERE r.incident_id<>e.incident_id OR r.observation_publication_id<>e.observation_publication_id
      OR r.analysis_publication_id<>e.analysis_publication_id)
)
"""))


def load_candidate(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root)
    verified = verify_candidate(root, deep_evidence=True)
    manifest = verified["manifest"]
    manifest_sha = verified["manifest_sha256"]
    if not DATABASE_RE.fullmatch(args.database) or args.database in {
        "bgp_project", manifest["source_event_publication_database_name"],
        manifest["source_metric_database_name"],
    }:
        raise CandidateError("S5 目标数据库名无效或指向来源/旧库")
    database = Postgres(args.container, args.database, args.user)
    exists = database.scalar(
        f"SELECT count(*) FROM pg_database WHERE datname={sql_literal(args.database)}",
        database="postgres",
    )
    if exists != "0":
        raise CandidateError("S5 目标数据库已存在；create-only 拒绝覆盖")
    database.psql(
        f'CREATE DATABASE "{args.database}" TEMPLATE template0 ENCODING \'UTF8\'',
        database="postgres",
    )
    try:
        database.apply_file(Path(args.ddl))
        database.psql(f"""
INSERT INTO domeye_read.candidate_registry(
 candidate_id,dataset_id,collector_id,window_start_utc,window_end_exclusive_utc,
 state_point_count,event_count,implementation_id,manifest_sha256,content_sha256,status
) VALUES (
 {sql_literal(manifest['candidate_id'])},{sql_literal(manifest['dataset_id'])},'rrc25',
 TIMESTAMPTZ '{WINDOW_START}',TIMESTAMPTZ '{WINDOW_END}',{STATE_POINT_COUNT},{EVENT_COUNT},
 {sql_literal(manifest['implementation_id'])},{sql_literal(manifest_sha)},
 {sql_literal(manifest['content_sha256'])},'loading'
)
""")
        for filename, table, columns in (
            ("source-binding.tsv.gz", "domeye_read.source_binding", SOURCE_COLUMNS),
            ("series-object.tsv.gz", "domeye_read.series_object", SERIES_COLUMNS),
            ("prefix-vp-evidence-view.tsv.gz", "domeye_read.prefix_vp_evidence_view", EVIDENCE_COLUMNS),
            ("event-read-model.tsv.gz", "domeye_read.event_read_model", EVENT_COLUMNS),
            ("report-snapshot.tsv.gz", "domeye_read.report_snapshot", REPORT_COLUMNS),
            ("report-pointer.tsv.gz", "domeye_read.report_pointer", POINTER_COLUMNS),
        ):
            database.copy_gzip(root / filename, table, columns)
        database.psql("UPDATE domeye_read.candidate_registry SET status='complete' WHERE status='loading'")
        summary = database_summary(database)
        expected = {
            "candidate_count": 1, "source_count": 2, "series_count": EVENT_COUNTRY_COUNT,
            "evidence_view_count": EVENT_COUNT, "event_count": EVENT_COUNT,
            "report_count": EVENT_COUNT + 1, "report_pointer_count": EVENT_COUNT,
            "report_v2_count": 1, "binding_mismatch": 0, "report_mismatch": 0,
        }
        for key, value in expected.items():
            if summary.get(key) != value:
                raise CandidateError(f"S5 数据库人口或绑定不闭合：{key}")
        if summary["series_max_bytes"] > SERIES_MAX_BYTES:
            raise CandidateError("数据库登记紧凑序列超过 1 MiB")
        schema_sha = schema_fingerprint(database)
        fingerprint = sha256_bytes(canonical_bytes({
            "candidate_id": manifest["candidate_id"], "dataset_id": manifest["dataset_id"],
            "manifest_sha256": manifest_sha, "content_sha256": manifest["content_sha256"],
            "schema_sha256": schema_sha, "summary": summary,
        }))
        receipt_id = stable_id("read_model_load_receipt_v1_", {
            "candidate_id": manifest["candidate_id"], "dataset_id": manifest["dataset_id"],
            "database_fingerprint_sha256": fingerprint,
        })
        loaded_at = utc_now()
        database.psql(f"""
INSERT INTO domeye_read.load_receipt(
 receipt_id,candidate_id,dataset_id,manifest_sha256,schema_sha256,
 database_fingerprint_sha256,loaded_at,status
) VALUES (
 {sql_literal(receipt_id)},{sql_literal(manifest['candidate_id'])},
 {sql_literal(manifest['dataset_id'])},{sql_literal(manifest_sha)},
 {sql_literal(schema_sha)},{sql_literal(fingerprint)},
 TIMESTAMPTZ {sql_literal(loaded_at)},'complete'
)
""")
        receipt = {
            "schema_version": "rrc25-read-model-database-load-receipt/v1",
            "status": "complete", "receipt_id": receipt_id,
            "candidate_id": manifest["candidate_id"], "dataset_id": manifest["dataset_id"],
            "manifest_sha256": manifest_sha, "content_sha256": manifest["content_sha256"],
            "database_name": args.database, "database_model": READ_MODEL_DATABASE_MODEL,
            "schema_sha256": schema_sha, "database_fingerprint_sha256": fingerprint,
            "postgresql_version": database.scalar("SHOW server_version"),
            "loaded_at": loaded_at, "summary": summary,
            "old_database_written": False, "s3_database_written": False,
            "s4_database_written": False, "selected_by_runtime": False,
        }
        receipt["receipt_content_sha256"] = sha256_bytes(canonical_bytes(receipt))
        write_json_create_only(Path(args.receipt), receipt)
        return receipt
    except BaseException:
        try:
            database.psql("UPDATE domeye_read.candidate_registry SET status='failed' WHERE status='loading'")
        except BaseException:
            pass
        raise


class ReadModelRuntime:
    def __init__(self, root: Path, access_log: Path | None = None) -> None:
        verified = verify_candidate(root, deep_evidence=False)
        self.root = root
        self.manifest = verified["manifest"]
        self.access_log = access_log
        self.events: dict[str, dict[str, Any]] = {}
        self.events_by_ref: dict[str, str] = {}
        self.evidence: dict[str, dict[str, Any]] = {}
        self.reports: dict[str, dict[str, Any]] = {}
        self.report_pointer: dict[str, str] = {}
        self.series: dict[str, bytes] = {}
        for row in read_tsv_gzip(root / "event-read-model.tsv.gz"):
            payload = json.loads(row["payload"])
            self.events[row["incident_id"]] = payload
            self.events_by_ref[row["legacy_reference"]] = row["incident_id"]
        for row in read_tsv_gzip(root / "prefix-vp-evidence-view.tsv.gz"):
            self.evidence[row["incident_id"]] = json.loads(row["payload"])
        for row in read_tsv_gzip(root / "report-snapshot.tsv.gz"):
            self.reports[row["report_snapshot_id"]] = json.loads(row["payload"])
        for row in read_tsv_gzip(root / "report-pointer.tsv.gz"):
            self.report_pointer[row["incident_id"]] = row["current_report_snapshot_id"]
        for row in read_tsv_gzip(root / "series-object.tsv.gz"):
            self.series[row["series_id"]] = (root / row["artifact_uri"]).read_bytes()

    def log(self, route: str, sources: Sequence[str]) -> None:
        if self.access_log is None:
            return
        record = canonical_bytes({
            "route": route, "sources": list(sources),
            "raw_mrt_scanned": False, "route_event_scanned": False,
            "full_asn_state_scanned": False, "publication_recomputed": False,
        }) + b"\n"
        with self.access_log.open("ab") as output:
            output.write(record)


def make_handler(runtime: ReadModelRuntime) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "DomeyeS5ReadModel/1.0"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def send_json(self, payload: Any, status: int = 200) -> None:
            raw = canonical_bytes(payload) + b"\n"
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
            if path == "/healthz":
                runtime.log(path, ["manifest.json"])
                self.send_json({
                    "status": "ok", "collector_id": COLLECTOR_ID,
                    "candidate_id": runtime.manifest["candidate_id"],
                    "dataset_id": runtime.manifest["dataset_id"],
                    "selected_by_runtime": False,
                })
                return
            if path == "/api/v1/events/by-ref":
                reference = urllib.parse.parse_qs(parsed.query).get("ref", [""])[0]
                incident_id = runtime.events_by_ref.get(reference)
                if incident_id is None:
                    self.fail("event reference not found")
                    return
                runtime.log(path, ["event-read-model.tsv.gz"])
                self.send_json(runtime.events[incident_id])
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) >= 4 and parts[:3] == ["api", "v1", "events"]:
                incident_id = parts[3]
                event = runtime.events.get(incident_id)
                if event is None:
                    self.fail("incident not found")
                    return
                if len(parts) == 4:
                    runtime.log(path, ["event-read-model.tsv.gz"])
                    self.send_json(event)
                    return
                if len(parts) == 5 and parts[4] == "series":
                    series_id = event["series_ref"]["series_id"]
                    runtime.log(path, [event["series_ref"]["artifact_uri"]])
                    self.send_gzip(runtime.series[series_id])
                    return
                if len(parts) == 5 and parts[4] == "report":
                    report_id = runtime.report_pointer[incident_id]
                    runtime.log(path, ["report-pointer.tsv.gz", "report-snapshot.tsv.gz"])
                    self.send_json(runtime.reports[report_id])
                    return
                if len(parts) == 5 and parts[4] == "evidence":
                    runtime.log(path, ["prefix-vp-evidence-view.tsv.gz"])
                    self.send_json(runtime.evidence[incident_id])
                    return
                if len(parts) == 7 and parts[4:6] == ["evidence", "pages"]:
                    try:
                        page = int(parts[6])
                    except ValueError:
                        self.fail("invalid evidence page", 400)
                        return
                    evidence = runtime.evidence[incident_id]
                    if page < 1 or page > evidence["page_count"]:
                        self.fail("evidence page not found")
                        return
                    relative = evidence["page_uri_template"].format(page=page)
                    target = runtime.root / relative
                    runtime.log(path, [relative])
                    self.send_gzip(target.read_bytes())
                    return
            if len(parts) == 4 and parts[:3] == ["api", "v1", "reports"]:
                report = runtime.reports.get(parts[3])
                if report is None:
                    self.fail("report snapshot not found")
                    return
                runtime.log(path, ["report-snapshot.tsv.gz"])
                self.send_json(report)
                return
            self.fail("route not found")

    return Handler


def serve_candidate(args: argparse.Namespace) -> None:
    access_log = Path(args.access_log) if args.access_log else None
    runtime = ReadModelRuntime(Path(args.root), access_log)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(runtime))
    print(canonical_bytes({
        "status": "ready", "host": args.host, "port": server.server_port,
        "candidate_id": runtime.manifest["candidate_id"],
        "dataset_id": runtime.manifest["dataset_id"],
    }).decode(), flush=True)
    server.serve_forever()


def percentile_95(values: Sequence[float]) -> float:
    if not values:
        raise CandidateError("性能样本为空")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def benchmark_candidate(args: argparse.Namespace) -> dict[str, Any]:
    runtime = ReadModelRuntime(Path(args.root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(runtime))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(path: str) -> tuple[float, int, Mapping[str, str]]:
        started = time.perf_counter()
        with urllib.request.urlopen(base + path, timeout=5) as response:
            raw = response.read()
            headers = dict(response.headers.items())
        return (time.perf_counter() - started) * 1000, len(raw), headers

    try:
        incidents = sorted(runtime.events)
        cold = [request(f"/api/v1/events/{incident}")[0] for incident in incidents]
        hot: list[float] = []
        for _ in range(5):
            hot.extend(request(f"/api/v1/events/{incident}")[0] for incident in incidents)
        series_results = [
            request(f"/api/v1/events/{incident}/series")
            for incident in incidents[:1]
        ]
        seen_countries: set[str] = set()
        for incident in incidents:
            country = runtime.events[incident]["incident"]["country_code"]
            if country in seen_countries:
                continue
            seen_countries.add(country)
            series_results.append(request(f"/api/v1/events/{incident}/series"))
        report_times = [request(f"/api/v1/events/{incident}/report")[0] for incident in incidents]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    result = {
        "schema_version": "rrc25-read-model-performance/v1",
        "environment": {
            "definition": "server_started_empty_http_connection_cache_first_unique_incident",
            "python": sys.version.split()[0], "host": os.uname().nodename,
            "event_cold_sample_count": len(cold), "event_hot_sample_count": len(hot),
            "series_sample_count": len(series_results),
        },
        "event_snapshot_cold_p95_ms": percentile_95(cold),
        "event_snapshot_hot_p95_ms": percentile_95(hot),
        "report_snapshot_p95_ms": percentile_95(report_times),
        "compact_series_p95_ms": percentile_95([row[0] for row in series_results]),
        "compact_series_max_response_bytes": max(row[1] for row in series_results),
        "budgets": {
            "event_snapshot_cold_p95_ms": 2000,
            "event_snapshot_hot_p95_ms": 500,
            "compact_series_p95_ms": 2000,
            "compact_series_max_response_bytes": SERIES_MAX_BYTES,
            "sidecar_budget_ms": 5000,
        },
    }
    result["passed"] = (
        result["event_snapshot_cold_p95_ms"] <= 2000
        and result["event_snapshot_hot_p95_ms"] <= 500
        and result["compact_series_p95_ms"] <= 2000
        and result["compact_series_max_response_bytes"] <= SERIES_MAX_BYTES
    )
    if not result["passed"]:
        raise CandidateError("S5 API 性能或载荷预算未通过")
    if args.output:
        write_json_create_only(Path(args.output), result)
    return result


def expect_sql_failure(database: Postgres, sql: str, expected: str) -> dict[str, Any]:
    result = database.psql(sql, check=False)
    stderr = result.stderr.decode("utf-8", "replace")
    if result.returncode == 0 or expected not in stderr:
        raise CandidateError(f"预期数据库拒绝未发生：{expected}")
    return {"returncode": result.returncode, "matched": expected}


def drill_candidate(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root)
    verified = verify_candidate(root, deep_evidence=True)
    manifest = verified["manifest"]
    receipt = json.loads(Path(args.receipt).read_bytes())
    database = Postgres(args.container, args.database, args.user)
    summary = database_summary(database)
    if (
        receipt["candidate_id"] != manifest["candidate_id"]
        or receipt["dataset_id"] != manifest["dataset_id"]
        or receipt["manifest_sha256"] != verified["manifest_sha256"]
        or receipt["summary"] != summary
    ):
        raise CandidateError("S5 文件、数据库与回执身份不一致")
    report_identity_sql = """
SELECT string_agg(report_snapshot_id||':'||snapshot_sha256,',' ORDER BY report_snapshot_id)
FROM domeye_read.report_snapshot
"""
    before = sha256_bytes(database.scalar(report_identity_sql).encode())
    immutable_event = expect_sql_failure(
        database,
        "UPDATE domeye_read.event_read_model SET payload='{}'::jsonb WHERE snapshot_id=(SELECT min(snapshot_id) FROM domeye_read.event_read_model)",
        "immutable read-model object",
    )
    immutable_report = expect_sql_failure(
        database,
        "DELETE FROM domeye_read.report_snapshot WHERE report_snapshot_id=(SELECT min(report_snapshot_id) FROM domeye_read.report_snapshot)",
        "immutable read-model object",
    )
    pointer_regression = expect_sql_failure(
        database,
        """UPDATE domeye_read.report_pointer p SET current_report_snapshot_id=(
SELECT r.report_snapshot_id FROM domeye_read.report_snapshot r
WHERE r.candidate_id=p.candidate_id AND r.incident_id=p.incident_id AND r.report_version=1
LIMIT 1), pointer_version=pointer_version+1
WHERE p.pointer_version=2""",
        "next immutable report version",
    )
    pointer_stale = expect_sql_failure(
        database,
        "UPDATE domeye_read.report_pointer SET pointer_version=pointer_version+2 WHERE pointer_version=1",
        "version must advance by one",
    )
    after = sha256_bytes(database.scalar(report_identity_sql).encode())
    if before != after:
        raise CandidateError("失败演练改变了不可变报告快照")
    reports = read_tsv_gzip(root / "report-snapshot.tsv.gz")
    by_incident: dict[str, list[dict[str, str]]] = {}
    for row in reports:
        by_incident.setdefault(row["incident_id"], []).append(row)
    versioned = [rows for rows in by_incident.values() if len(rows) == 2]
    if len(versioned) != 1:
        raise CandidateError("报告更新版本演练人口错误")
    versioned[0].sort(key=lambda row: int(row["report_version"]))
    old_report_unchanged = (
        versioned[0][0]["snapshot_sha256"]
        != versioned[0][1]["snapshot_sha256"]
        and json.loads(versioned[0][0]["payload"])["report_version"] == 1
        and json.loads(versioned[0][1]["payload"])["report_version"] == 2
    )
    if not old_report_unchanged:
        raise CandidateError("报告刷新没有形成新版本或旧版本丢失")
    catalog = json.loads((root / "prefix-vp" / "catalog.json").read_bytes())
    first_page = root / "prefix-vp" / "pages" / catalog["countries"][0]["pages"][0]["path"]
    tampered = bytearray(first_page.read_bytes())
    tampered[len(tampered) // 2] ^= 1
    tamper_rejected = sha256_bytes(bytes(tampered)) != catalog["countries"][0]["pages"][0]["sha256"]
    if not tamper_rejected:
        raise CandidateError("Evidence 篡改未被摘要检测")
    result = {
        "schema_version": "rrc25-read-model-drill/v1", "status": "passed",
        "candidate_id": manifest["candidate_id"], "dataset_id": manifest["dataset_id"],
        "database_name": args.database,
        "immutable_event_update_rejected": immutable_event,
        "immutable_report_delete_rejected": immutable_report,
        "pointer_regression_rejected": pointer_regression,
        "pointer_stale_version_rejected": pointer_stale,
        "old_report_unchanged": old_report_unchanged,
        "report_snapshot_set_sha256_before": before,
        "report_snapshot_set_sha256_after": after,
        "prefix_vp_page_tamper_rejected": tamper_rejected,
        "source_route_state_id": manifest["derived_from_route_state_id"],
        "prefix_vp_rebuild_semantics": "derived_from_registered_route_state_and_publication_only",
        "database_summary": summary,
    }
    result["content_sha256"] = sha256_bytes(canonical_bytes(result))
    write_json_create_only(Path(args.output), result)
    return result


def configure_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--s4-root", required=True)
    build.add_argument("--s4-database", required=True)
    build.add_argument("--s3-database", required=True)
    build.add_argument("--route-state-root", required=True)
    build.add_argument("--compatible-mapping", required=True)
    build.add_argument("--revised-mapping", required=True)
    build.add_argument("--evidence-binary", required=True)
    build.add_argument("--implementation-id", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--container", default="domeye_core_dev_pg")
    build.add_argument("--user", default="postgres")
    load = subparsers.add_parser("load")
    load.add_argument("--root", required=True)
    load.add_argument("--database", required=True)
    load.add_argument("--ddl", required=True)
    load.add_argument("--receipt", required=True)
    load.add_argument("--container", default="domeye_core_dev_pg")
    load.add_argument("--user", default="postgres")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", required=True)
    verify.add_argument("--shallow-evidence", action="store_true")
    serve = subparsers.add_parser("serve")
    serve.add_argument("--root", required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=28651)
    serve.add_argument("--access-log")
    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument("--root", required=True)
    benchmark.add_argument("--output")
    drill = subparsers.add_parser("drill")
    drill.add_argument("--root", required=True)
    drill.add_argument("--database", required=True)
    drill.add_argument("--receipt", required=True)
    drill.add_argument("--output", required=True)
    drill.add_argument("--container", default="domeye_core_dev_pg")
    drill.add_argument("--user", default="postgres")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = configure_parser().parse_args(argv)
    try:
        if args.command == "build":
            result = build_candidate(args)
        elif args.command == "load":
            result = load_candidate(args)
        elif args.command == "verify":
            result = verify_candidate(
                Path(args.root), deep_evidence=not args.shallow_evidence,
            )
        elif args.command == "serve":
            serve_candidate(args)
            return 0
        elif args.command == "benchmark":
            result = benchmark_candidate(args)
        elif args.command == "drill":
            result = drill_candidate(args)
        else:
            raise CandidateError("未知命令")
        print(canonical_bytes(result).decode())
        return 0
    except CandidateError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
