#!/usr/bin/env python3
"""构建、装载并验收 RRC25 224-310 事件与 Publication 事务候选。

S4 只读取已经验收的 S3 指标候选和旧发布注册表。正式事件的恢复状态不会由
固定窗口臆造；没有可信正常带时，只追加 detected、ongoing 和表示观测窗关闭的
final。recovery_candidate、recovered_observation 及候选撤销由同一数据库状态机
在隔离事务中验收。
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


WINDOW_START = "2026-02-24T00:00:00Z"
WINDOW_END = "2026-03-11T00:00:00Z"
FIRST_STATE_POINT = "2026-02-24T00:05:00Z"
COLLECTOR_ID = "rrc25"
STATE_POINT_COUNT = 4320
COUNTRY_BUCKET_COUNT = 241
EVENT_COUNT = 81
COUNTRY_COUNT = 43
S4_SCHEMA_VERSION = "rrc25-event-publication-store/v1"
S4_DATABASE_MODEL = "domeye-event-publication-postgresql/v1"
LIFECYCLE_ALGORITHM = "rrc25_country_outage_lifecycle/v1"
ANALYSIS_ALGORITHM = "rrc25_country_trend_profile/v1"
ANALYSIS_CADENCE_SLOTS = 12
LIMITATION = (
    "仅描述RRC25单采集器所见BGP控制面；不证明全国断网、用户或业务影响、"
    "原因、攻击、责任或真实服务完全恢复"
)

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DATABASE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
IMPLEMENTATION_RE = re.compile(r"^[0-9a-f]{40}$")


class CandidateError(RuntimeError):
    """候选来源、构建、装载或验收失败。"""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise CandidateError(f"时间缺少时区：{value}")
    return parsed.astimezone(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def ceil_five_minutes(value: datetime) -> datetime:
    seconds = int(value.timestamp())
    return datetime.fromtimestamp(((seconds + 299) // 300) * 300, timezone.utc)


def parse_legacy_reference(reference: str) -> tuple[datetime, str, str]:
    parts = reference.split("/")
    if len(parts) != 5 or parts[0] != "country_outage" or parts[4] != "r":
        raise CandidateError(f"旧事件 ref 结构无效：{reference}")
    if not COUNTRY_RE.fullmatch(parts[2]):
        raise CandidateError(f"旧事件国家无效：{reference}")
    try:
        local = datetime.strptime(parts[1], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=ZoneInfo("Asia/Shanghai")
        )
    except ValueError as error:
        raise CandidateError(f"旧事件时间无效：{reference}") from error
    return local.astimezone(timezone.utc), parts[2], parts[3]


def sql_literal(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'{}'".format(value.replace("'", "''"))


def run_command(
    command: Sequence[str], *, input_bytes: bytes | None = None, check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        list(command), input=input_bytes, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if check and result.returncode:
        raise CandidateError(
            "命令失败：{}\n{}".format(
                " ".join(command), result.stderr.decode("utf-8", "replace")[-8000:]
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
    "candidate_id", "source_id", "source_kind", "collector_id",
    "window_start_utc", "window_end_exclusive_utc", "dataset_id",
    "content_sha256", "manifest_sha256", "database_name",
    "database_fingerprint_sha256", "object_uri", "object_sha256", "metadata",
)
INCIDENT_COLUMNS = (
    "candidate_id", "incident_id", "legacy_reference", "country_code",
    "country_name", "event_type", "source_system", "source_code",
    "collector_id", "legacy_event_time_utc", "detected_at",
    "window_start_utc", "window_end_exclusive_utc", "normal_band_state",
    "normal_band_reason", "legacy_current_publication_id",
    "corrected_observation_revision", "status",
)
FACT_COLUMNS = (
    "candidate_id", "fact_id", "incident_id", "fact_sequence", "stage",
    "observed_at", "data_through", "detector_name", "detector_version",
    "source_metric_dataset_id", "source_state_point_utc",
    "source_metric_slot_sha256", "evidence", "limitations", "previous_fact_id",
)
PUBLICATION_COLUMNS = (
    "candidate_id", "publication_id", "incident_id", "publication_kind",
    "revision", "sequence_in_revision", "data_through", "observed_at",
    "event_fact_id", "derived_from_observation_publication_id",
    "previous_publication_id", "correction_of_publication_id",
    "supersedes_publication_id", "source_metric_dataset_id",
    "source_metric_slot_sha256", "is_final", "validation_state",
    "fact_set_sha256", "payload_sha256", "content_sha256", "artifact_uri",
    "artifact_sha256", "snapshot",
)
POINTER_PLAN_COLUMNS = (
    "candidate_id", "incident_id", "initial_observation_publication_id",
    "final_observation_publication_id", "final_analysis_publication_id",
)


class GzipTsvWriter:
    def __init__(self, path: Path, columns: Sequence[str]) -> None:
        self.path = path
        self.columns = tuple(columns)
        self.rows = 0
        self._raw = path.open("xb")
        self._gzip = gzip.GzipFile(fileobj=self._raw, mode="wb", mtime=0, filename="")
        self._text = io.TextIOWrapper(self._gzip, encoding="utf-8", newline="")
        self._writer = csv.writer(
            self._text, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL,
        )
        self._writer.writerow(self.columns)

    def write(self, values: Mapping[str, Any]) -> None:
        row: list[str] = []
        for column in self.columns:
            value = values.get(column)
            if value is None:
                row.append(r"\N")
            elif isinstance(value, (dict, list)):
                row.append(canonical_bytes(value).decode("utf-8"))
            elif isinstance(value, bool):
                row.append("t" if value else "f")
            else:
                row.append(str(value))
        self._writer.writerow(row)
        self.rows += 1

    def close(self) -> dict[str, Any]:
        self._text.flush()
        self._gzip.close()
        self._raw.close()
        compressed_sha, size = sha256_file(self.path)
        content = hashlib.sha256()
        row_count = -1
        with gzip.open(self.path, "rb") as source:
            for row_count, line in enumerate(source):
                content.update(line)
        return {
            "path": self.path.name,
            "row_count": max(0, row_count),
            "size_bytes": size,
            "sha256": compressed_sha,
            "content_sha256": content.hexdigest(),
        }


def receipt_content_sha(receipt: Mapping[str, Any]) -> str:
    semantic = dict(receipt)
    semantic["content_sha256"] = ""
    return sha256_bytes(canonical_bytes(semantic))


def load_and_verify_s3_receipt(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    receipt = json.loads(raw)
    required = {
        "schema_version": "rrc25-route-metric-database-load-receipt/v1",
        "status": "complete",
        "database_model": "domeye-data-postgresql-timescaledb/v1",
        "old_database_written": False,
        "selected_by_runtime": False,
    }
    for field, expected in required.items():
        if receipt.get(field) != expected:
            raise CandidateError(f"S3 回执字段冲突：{field}")
    if receipt_content_sha(receipt) != receipt.get("content_sha256"):
        raise CandidateError("S3 回执内容摘要无法独立复算")
    for field in (
        "content_sha256", "database_fingerprint_sha256", "manifest_sha256",
        "metric_content_sha256", "schema_sha256",
    ):
        if not SHA_RE.fullmatch(str(receipt.get(field, ""))):
            raise CandidateError(f"S3 回执摘要无效：{field}")
    return receipt, sha256_bytes(raw)


def verify_s3_database(database: Postgres, receipt: Mapping[str, Any]) -> None:
    if database.database != receipt["database_name"]:
        raise CandidateError("S3 数据库名与回执冲突")
    sql = """
SELECT json_build_object(
 'candidate_id', c.candidate_id,
 'candidate_status', c.status,
 'metric_dataset_id', d.dataset_id,
 'metric_content_sha256', d.content_sha256,
 'metric_manifest_sha256', d.manifest_sha256,
 'collector_id', d.collector_id,
 'window_start_utc', to_char(d.window_start_utc AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),
 'window_end_exclusive_utc', to_char(d.window_end_exclusive_utc AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),
 'slot_count', (SELECT count(*) FROM domeye_data.metric_slot_5m s WHERE s.metric_dataset_id=d.dataset_id),
 'bad_slot_count', (SELECT count(*) FROM domeye_data.metric_slot_5m s WHERE s.metric_dataset_id=d.dataset_id AND (s.quality_status<>'complete' OR s.gap_status<>'none')),
 'gap_count', (SELECT count(*) FROM domeye_data.quality_gap g WHERE g.metric_dataset_id=d.dataset_id),
 'receipt_fingerprint', l.database_fingerprint_sha256,
 'receipt_id', l.receipt_id
)
FROM domeye_data.candidate_registry c
JOIN domeye_data.dataset_registry d ON d.candidate_id=c.candidate_id AND d.dataset_kind='route_metric'
JOIN domeye_data.load_receipt l ON l.candidate_id=c.candidate_id AND l.metric_dataset_id=d.dataset_id;
"""
    text = database.scalar(sql)
    try:
        actual = json.loads(text)
    except json.JSONDecodeError as error:
        raise CandidateError("无法读取 S3 数据库身份") from error
    expected = {
        "candidate_id": receipt["candidate_id"],
        "candidate_status": "complete",
        "metric_dataset_id": receipt["metric_dataset_id"],
        "metric_content_sha256": receipt["metric_content_sha256"],
        "metric_manifest_sha256": receipt["manifest_sha256"],
        "collector_id": COLLECTOR_ID,
        "window_start_utc": WINDOW_START,
        "window_end_exclusive_utc": WINDOW_END,
        "slot_count": STATE_POINT_COUNT,
        "bad_slot_count": 0,
        "gap_count": 0,
        "receipt_fingerprint": receipt["database_fingerprint_sha256"],
        "receipt_id": receipt["receipt_id"],
    }
    if actual != expected:
        raise CandidateError(f"S3 数据库身份或质量冲突：{actual} != {expected}")


def validate_registry(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    registry = json.loads(raw)
    if (
        registry.get("schema_version") != "country_outage_observation_registry_v1"
        or registry.get("scope") != "rrc25_global_window_20260224_20260310"
        or not isinstance(
        registry.get("observations"), list
        )
    ):
        raise CandidateError("旧发布注册表结构或 scope 无效")
    observations = registry["observations"]
    if len(observations) != EVENT_COUNT:
        raise CandidateError(f"旧事件数量不是 {EVENT_COUNT}")
    refs: set[str] = set()
    countries: set[str] = set()
    publication_ids: set[str] = set()
    for observation in observations:
        if observation.get("collector_ids") != [COLLECTOR_ID]:
            raise CandidateError("旧发布包含非 rrc25 collector")
        reference = observation.get("legacy_reference", "")
        event_utc, country, _ = parse_legacy_reference(reference)
        if country != observation.get("country", {}).get("code"):
            raise CandidateError(f"ref 国家与注册表国家冲突：{reference}")
        point = ceil_five_minutes(event_utc)
        if not (parse_utc(FIRST_STATE_POINT) <= point <= parse_utc(WINDOW_END)):
            raise CandidateError(f"事件检测状态点越界：{reference}")
        if reference in refs:
            raise CandidateError(f"旧事件 ref 重复：{reference}")
        refs.add(reference)
        countries.add(country)
        current = observation.get("current_publication_id")
        found_current = False
        for publication in observation.get("publications", []):
            publication_id = publication.get("publication_id")
            if not publication_id or publication_id in publication_ids:
                raise CandidateError(f"旧 Publication 身份缺失或重复：{reference}")
            publication_ids.add(publication_id)
            if publication_id == current:
                found_current = True
        if not found_current:
            raise CandidateError(f"旧 current 未出现在历史列表：{reference}")
    if len(countries) != COUNTRY_COUNT or len(publication_ids) != 82:
        raise CandidateError("旧事件国家或 Publication 人口不符合冻结基线")
    return registry, sha256_bytes(raw)


def artifact_closure(path_text: str, cache: dict[tuple[int, int, int], str]) -> str:
    root = Path(path_text)
    if not root.is_dir():
        raise CandidateError(f"旧 Publication 制品目录不存在：{root}")
    complete = root / "COMPLETE.json"
    if not complete.is_file():
        raise CandidateError(f"旧 Publication 缺少 COMPLETE.json：{root}")
    payload = json.loads(complete.read_bytes())
    declared = payload.get("deliverable_sha256")
    if not isinstance(declared, dict) or not declared:
        raise CandidateError(f"旧 COMPLETE 缺少 deliverable_sha256：{root}")
    verified: list[dict[str, Any]] = []
    for relative, expected in sorted(declared.items()):
        child = root / relative
        if not child.is_file() or not SHA_RE.fullmatch(str(expected)):
            raise CandidateError(f"旧制品清单项无效：{child}")
        stat = child.stat()
        key = (stat.st_dev, stat.st_ino, stat.st_size)
        actual = cache.get(key)
        if actual is None:
            actual, _ = sha256_file(child)
            cache[key] = actual
        if actual != expected:
            raise CandidateError(f"旧制品摘要冲突：{child}")
        verified.append({"path": relative, "size_bytes": stat.st_size, "sha256": actual})
    return sha256_bytes(canonical_bytes({
        "complete_sha256": sha256_file(complete)[0], "files": verified,
    }))


def metric_rows(
    database: Postgres, metric_dataset_id: str, countries: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    for country in countries:
        if not COUNTRY_RE.fullmatch(country):
            raise CandidateError(f"国家代码无效：{country}")
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
    result = {country: [] for country in countries}
    reader = csv.reader(io.TextIOWrapper(process.stdout, encoding="utf-8", newline=""))
    for row in reader:
        if len(row) != 18:
            process.kill()
            raise CandidateError("S3 指标导出列数无效")
        country = row[1]
        values = {
            "state_point_utc": row[0], "country_code": country,
            "baseline_v4": int(row[2]), "baseline_v6": int(row[3]),
            "cohort_visible_v4": int(row[4]), "cohort_visible_v6": int(row[5]),
            "current_visible_v4": int(row[6]), "current_visible_v6": int(row[7]),
            "announcement_v4": int(row[8]), "announcement_v6": int(row[9]),
            "withdrawal_v4": int(row[10]), "withdrawal_v6": int(row[11]),
            "slot": int(row[12]), "source_route_state_slot_sha256": row[13],
            "metric_snapshot_sha256": row[14], "metric_slot_sha256": row[15],
            "quality_status": row[16], "gap_status": row[17],
        }
        if values["quality_status"] != "complete" or values["gap_status"] != "none":
            process.kill()
            raise CandidateError(f"S3 指标槽未通过质量门：{country} {row[0]}")
        result[country].append(values)
    stderr = process.stderr.read() if process.stderr else b""
    code = process.wait()
    if code:
        raise CandidateError(f"S3 指标导出失败：{stderr.decode(errors='replace')[-8000:]}")
    for country, rows in result.items():
        if len(rows) != STATE_POINT_COUNT:
            raise CandidateError(f"国家 {country} 不是完整 {STATE_POINT_COUNT} 点")
        if rows[0]["state_point_utc"] != FIRST_STATE_POINT or rows[-1]["state_point_utc"] != WINDOW_END:
            raise CandidateError(f"国家 {country} 状态点边界错误")
    return result


def metric_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "state_point_utc", "country_code", "baseline_v4", "baseline_v6",
        "cohort_visible_v4", "cohort_visible_v6", "current_visible_v4",
        "current_visible_v6", "announcement_v4", "announcement_v6",
        "withdrawal_v4", "withdrawal_v6", "slot",
        "source_route_state_slot_sha256", "metric_snapshot_sha256",
        "metric_slot_sha256", "quality_status", "gap_status",
    )
    return {key: row[key] for key in keys}


def visibility_ratio(row: Mapping[str, Any]) -> float | None:
    baseline = row["baseline_v4"] + row["baseline_v6"]
    if baseline == 0:
        return None
    return (row["cohort_visible_v4"] + row["cohort_visible_v6"]) / baseline


def publication_identity(content: Mapping[str, Any], prefix: str) -> tuple[str, str, str]:
    payload_sha = sha256_bytes(canonical_bytes(content["snapshot"]))
    semantic = dict(content)
    semantic["payload_sha256"] = payload_sha
    semantic["content_sha256"] = ""
    content_sha = sha256_bytes(canonical_bytes(semantic))
    publication_id = stable_id(prefix, {
        "candidate_id": semantic["candidate_id"],
        "incident_id": semantic["incident_id"],
        "publication_kind": semantic["publication_kind"],
        "revision": semantic["revision"],
        "sequence_in_revision": semantic["sequence_in_revision"],
        "data_through": semantic["data_through"],
        "content_sha256": content_sha,
    })
    return publication_id, payload_sha, content_sha


def generated_publication(
    *, candidate_id: str, incident_id: str, kind: str, revision: int,
    sequence: int, data_through: str, observed_at: str, fact_id: str | None,
    derived_from: str | None, previous: str | None, correction_of: str | None,
    supersedes: str | None, metric_dataset_id: str, metric_slot_sha: str,
    is_final: bool, fact_set_sha: str, snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    content: dict[str, Any] = {
        "candidate_id": candidate_id, "incident_id": incident_id,
        "publication_kind": kind, "revision": revision,
        "sequence_in_revision": sequence, "data_through": data_through,
        "observed_at": observed_at, "event_fact_id": fact_id,
        "derived_from_observation_publication_id": derived_from,
        "previous_publication_id": previous,
        "correction_of_publication_id": correction_of,
        "supersedes_publication_id": supersedes,
        "source_metric_dataset_id": metric_dataset_id,
        "source_metric_slot_sha256": metric_slot_sha,
        "is_final": is_final, "validation_state": "verified",
        "fact_set_sha256": fact_set_sha, "artifact_uri": None,
        "artifact_sha256": None, "snapshot": dict(snapshot),
    }
    prefix = "observation_publication_v1_" if kind == "observation" else "analysis_publication_v1_"
    publication_id, payload_sha, content_sha = publication_identity(content, prefix)
    content["publication_id"] = publication_id
    content["payload_sha256"] = payload_sha
    content["content_sha256"] = content_sha
    return content


def legacy_publication(
    candidate_id: str, observation: Mapping[str, Any], publication: Mapping[str, Any],
    sequence: int, artifact_sha: str,
) -> dict[str, Any]:
    snapshot = {
        "schema_version": "rrc25-legacy-publication-evidence/v1",
        "legacy_reference": observation["legacy_reference"],
        "legacy_observation": {
            "country": observation["country"],
            "current_publication_id": observation["current_publication_id"],
            "incident_id": observation["incident_id"],
            "data_through": observation["data_through"],
            "is_final": observation["is_final"],
        },
        "legacy_publication": dict(publication),
        "limitations": [LIMITATION],
    }
    fact_set_sha = sha256_bytes(canonical_bytes(snapshot))
    content: dict[str, Any] = {
        "candidate_id": candidate_id,
        "publication_id": publication["publication_id"],
        "incident_id": observation["incident_id"],
        "publication_kind": "legacy_observation",
        "revision": int(publication["revision"]),
        "sequence_in_revision": sequence,
        "data_through": publication["data_through"],
        "observed_at": publication.get("updated_at", publication["data_through"]),
        "event_fact_id": None,
        "derived_from_observation_publication_id": None,
        "previous_publication_id": None,
        "correction_of_publication_id": None,
        "supersedes_publication_id": publication.get("supersedes_publication_id"),
        "source_metric_dataset_id": None,
        "source_metric_slot_sha256": None,
        "is_final": bool(publication.get("is_final")),
        "validation_state": "verified",
        "fact_set_sha256": fact_set_sha,
        "artifact_uri": publication["package_uri"],
        "artifact_sha256": artifact_sha,
        "snapshot": snapshot,
    }
    content["payload_sha256"] = sha256_bytes(canonical_bytes(snapshot))
    semantic = dict(content)
    semantic["content_sha256"] = ""
    content["content_sha256"] = sha256_bytes(canonical_bytes(semantic))
    return content


def make_fact(
    candidate_id: str, incident_id: str, sequence: int, stage: str,
    observed_at: str, row: Mapping[str, Any], metric_dataset_id: str,
    previous_fact_id: str | None,
) -> dict[str, Any]:
    limitation = {
        "control_plane_only": True, "normal_band_state": "unknown",
        "recovery_claim": "not_assessed",
        "final_semantics": "fixed_observation_window_closed" if stage == "final" else None,
        "statement": LIMITATION,
    }
    evidence = {
        "metric": metric_payload(row),
        "stage_reason": {
            "detected": "保留旧事件首次检测身份并对齐到首个完整五分钟状态点",
            "ongoing": "检测后仍有连续、通过质量门的RRC25观测",
            "final": "224-310固定观测窗口关闭；不等于恢复",
        }[stage],
    }
    identity = {
        "candidate_id": candidate_id, "incident_id": incident_id,
        "fact_sequence": sequence, "stage": stage, "observed_at": observed_at,
        "data_through": observed_at, "source_metric_dataset_id": metric_dataset_id,
        "source_state_point_utc": observed_at,
        "source_metric_slot_sha256": row["metric_slot_sha256"],
        "previous_fact_id": previous_fact_id, "evidence": evidence,
        "limitations": limitation,
    }
    fact_id = stable_id("event_fact_v1_", identity)
    return {
        **identity, "fact_id": fact_id,
        "detector_name": "country_outage_legacy_identity_bridge",
        "detector_version": LIFECYCLE_ALGORITHM,
    }


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    if not IMPLEMENTATION_RE.fullmatch(args.implementation_id):
        raise CandidateError("implementation-id 必须是完整 40 位 Git 提交")
    target = args.output.resolve()
    if target.exists():
        raise CandidateError("S4 文件候选目录已存在，create-only 构建拒绝覆盖")
    staging = target.with_name(target.name + f".partial.{os.getpid()}")
    if staging.exists():
        raise CandidateError("S4 临时构建目录已存在")
    staging.mkdir(parents=True)
    try:
        receipt, receipt_file_sha = load_and_verify_s3_receipt(args.s3_receipt)
        s3 = Postgres(args.container, args.s3_database, args.user)
        verify_s3_database(s3, receipt)
        registry, registry_sha = validate_registry(args.legacy_registry)
        observations = sorted(
            registry["observations"], key=lambda value: value["legacy_reference"]
        )
        countries = sorted({value["country"]["code"] for value in observations})
        metrics = metric_rows(s3, receipt["metric_dataset_id"], countries)

        build_id = stable_id("event_publication_run_v1_", {
            "candidate_id": receipt["candidate_id"],
            "metric_dataset_id": receipt["metric_dataset_id"],
            "metric_content_sha256": receipt["metric_content_sha256"],
            "legacy_registry_sha256": registry_sha,
            "implementation_id": args.implementation_id,
            "lifecycle_algorithm": LIFECYCLE_ALGORITHM,
            "analysis_algorithm": ANALYSIS_ALGORITHM,
        })
        dataset_id = stable_id("event_publication_dataset_v1_", {
            "candidate_id": receipt["candidate_id"],
            "metric_dataset_id": receipt["metric_dataset_id"],
            "metric_content_sha256": receipt["metric_content_sha256"],
            "legacy_registry_sha256": registry_sha,
            "lifecycle_algorithm": LIFECYCLE_ALGORITHM,
            "analysis_algorithm": ANALYSIS_ALGORITHM,
        })

        writers = {
            "source_binding": GzipTsvWriter(staging / "source-binding.tsv.gz", SOURCE_COLUMNS),
            "incident": GzipTsvWriter(staging / "incident.tsv.gz", INCIDENT_COLUMNS),
            "event_fact": GzipTsvWriter(staging / "event-fact.tsv.gz", FACT_COLUMNS),
            "legacy_publication": GzipTsvWriter(staging / "legacy-publication.tsv.gz", PUBLICATION_COLUMNS),
            "observation_publication": GzipTsvWriter(staging / "observation-publication.tsv.gz", PUBLICATION_COLUMNS),
            "analysis_publication": GzipTsvWriter(staging / "analysis-publication.tsv.gz", PUBLICATION_COLUMNS),
            "pointer_plan": GzipTsvWriter(staging / "pointer-plan.tsv.gz", POINTER_PLAN_COLUMNS),
        }
        candidate_id = receipt["candidate_id"]
        writers["source_binding"].write({
            "candidate_id": candidate_id,
            "source_id": receipt["metric_dataset_id"], "source_kind": "route_metric",
            "collector_id": COLLECTOR_ID, "window_start_utc": WINDOW_START,
            "window_end_exclusive_utc": WINDOW_END,
            "dataset_id": receipt["metric_dataset_id"],
            "content_sha256": receipt["metric_content_sha256"],
            "manifest_sha256": receipt["manifest_sha256"],
            "database_name": receipt["database_name"],
            "database_fingerprint_sha256": receipt["database_fingerprint_sha256"],
            "object_uri": str(args.s3_receipt.resolve()),
            "object_sha256": receipt_file_sha,
            "metadata": {
                "receipt_id": receipt["receipt_id"],
                "receipt_content_sha256": receipt["content_sha256"],
                "schema_sha256": receipt["schema_sha256"],
            },
        })
        writers["source_binding"].write({
            "candidate_id": candidate_id,
            "source_id": "legacy_country_outage_registry_224_310",
            "source_kind": "legacy_publication_registry",
            "collector_id": COLLECTOR_ID, "window_start_utc": WINDOW_START,
            "window_end_exclusive_utc": WINDOW_END, "dataset_id": None,
            "content_sha256": registry_sha, "manifest_sha256": None,
            "database_name": None, "database_fingerprint_sha256": None,
            "object_uri": str(args.legacy_registry.resolve()),
            "object_sha256": registry_sha,
            "metadata": {"schema_version": registry["schema_version"], "scope": registry["scope"]},
        })

        artifact_cache: dict[tuple[int, int, int], str] = {}
        stage_counts = {stage: 0 for stage in (
            "detected", "ongoing", "recovery_candidate", "recovered_observation", "final"
        )}
        total_observation_publications = 0
        total_analysis_publications = 0
        legacy_count = 0
        revision_counts: dict[int, int] = {}

        for observation in observations:
            reference = observation["legacy_reference"]
            event_utc, country, _ = parse_legacy_reference(reference)
            detected = ceil_five_minutes(event_utc)
            rows = metrics[country]
            start_index = int((detected - parse_utc(FIRST_STATE_POINT)).total_seconds() // 300)
            if start_index < 0 or start_index >= len(rows):
                raise CandidateError(f"事件检测点找不到 S3 指标：{reference}")
            if rows[start_index]["state_point_utc"] != utc_text(detected):
                raise CandidateError(f"事件检测点与 S3 槽不一致：{reference}")
            incident_id = observation["incident_id"]
            old_current = observation["current_publication_id"]
            old_current_publication = next(
                p for p in observation["publications"] if p["publication_id"] == old_current
            )
            corrected_revision = int(old_current_publication["revision"]) + 1
            revision_counts[corrected_revision] = revision_counts.get(corrected_revision, 0) + 1
            writers["incident"].write({
                "candidate_id": candidate_id, "incident_id": incident_id,
                "legacy_reference": reference, "country_code": country,
                "country_name": observation["country"]["name"],
                "event_type": "country_outage", "source_system": "domeye_legacy",
                "source_code": "r", "collector_id": COLLECTOR_ID,
                "legacy_event_time_utc": utc_text(event_utc), "detected_at": utc_text(detected),
                "window_start_utc": WINDOW_START, "window_end_exclusive_utc": WINDOW_END,
                "normal_band_state": "unknown",
                "normal_band_reason": "固定224-310窗口没有可信长期正常参照",
                "legacy_current_publication_id": old_current,
                "corrected_observation_revision": corrected_revision, "status": "complete",
            })

            for old_sequence, publication in enumerate(
                sorted(observation["publications"], key=lambda p: (int(p["revision"]), p["publication_id"])),
                start=1,
            ):
                artifact_sha = artifact_closure(publication["package_uri"], artifact_cache)
                writers["legacy_publication"].write(legacy_publication(
                    candidate_id, observation, publication, old_sequence, artifact_sha,
                ))
                legacy_count += 1

            fact_by_point: dict[str, dict[str, Any]] = {}
            previous_fact: str | None = None
            detected_row = rows[start_index]
            detected_fact = make_fact(
                candidate_id, incident_id, 1, "detected", utc_text(detected),
                detected_row, receipt["metric_dataset_id"], previous_fact,
            )
            writers["event_fact"].write(detected_fact)
            fact_by_point[detected_fact["observed_at"]] = detected_fact
            previous_fact = detected_fact["fact_id"]
            stage_counts["detected"] += 1

            ongoing_index = start_index + 1
            ongoing_row = rows[ongoing_index]
            ongoing_fact = make_fact(
                candidate_id, incident_id, 2, "ongoing",
                ongoing_row["state_point_utc"], ongoing_row,
                receipt["metric_dataset_id"], previous_fact,
            )
            writers["event_fact"].write(ongoing_fact)
            fact_by_point[ongoing_fact["observed_at"]] = ongoing_fact
            previous_fact = ongoing_fact["fact_id"]
            stage_counts["ongoing"] += 1

            final_row = rows[-1]
            final_fact = make_fact(
                candidate_id, incident_id, 3, "final", WINDOW_END, final_row,
                receipt["metric_dataset_id"], previous_fact,
            )
            writers["event_fact"].write(final_fact)
            fact_by_point[WINDOW_END] = final_fact
            stage_counts["final"] += 1

            active_fact = detected_fact
            fact_ids: list[str] = []
            previous_observation: str | None = None
            observation_ids: list[str] = []
            analysis_candidates: list[
                tuple[
                    int, dict[str, Any], str, str,
                    tuple[float, str] | None, tuple[float, str] | None,
                ]
            ] = []
            running_min: tuple[float, str] | None = None
            running_max: tuple[float, str] | None = None
            first_ratio: float | None = None
            last_observation_publication: dict[str, Any] | None = None
            event_rows = rows[start_index:]
            for sequence, row in enumerate(event_rows, start=1):
                point = row["state_point_utc"]
                if point in fact_by_point:
                    active_fact = fact_by_point[point]
                    fact_ids.append(active_fact["fact_id"])
                fact_set_sha = sha256_bytes(canonical_bytes(fact_ids))
                final = point == WINDOW_END
                snapshot = {
                    "schema_version": "rrc25-observation-publication/v1",
                    "candidate_id": candidate_id, "incident_id": incident_id,
                    "collector_id": COLLECTOR_ID, "event_type": "country_outage",
                    "country_code": country, "legacy_reference": reference,
                    "source_window": {"start": WINDOW_START, "end_exclusive": WINDOW_END},
                    "state_point_semantics": "five_minute_slot_end",
                    "data_through": point, "event_stage": active_fact["stage"],
                    "event_fact_id": active_fact["fact_id"],
                    "metric_dataset_id": receipt["metric_dataset_id"],
                    "metric": metric_payload(row), "quality": {"state": "complete", "gap": "none"},
                    "finality": "final" if final else "progressing",
                    "normal_band": {"state": "unknown", "reason": "固定窗口没有可信长期正常参照"},
                    "limitations": [LIMITATION],
                }
                publication = generated_publication(
                    candidate_id=candidate_id, incident_id=incident_id,
                    kind="observation", revision=corrected_revision, sequence=sequence,
                    data_through=point, observed_at=point, fact_id=active_fact["fact_id"],
                    derived_from=None, previous=previous_observation,
                    correction_of=old_current,
                    supersedes=old_current if final else None,
                    metric_dataset_id=receipt["metric_dataset_id"],
                    metric_slot_sha=row["metric_slot_sha256"], is_final=final,
                    fact_set_sha=fact_set_sha, snapshot=snapshot,
                )
                writers["observation_publication"].write(publication)
                previous_observation = publication["publication_id"]
                observation_ids.append(publication["publication_id"])
                last_observation_publication = publication
                total_observation_publications += 1

                ratio = visibility_ratio(row)
                if first_ratio is None:
                    first_ratio = ratio
                if ratio is not None:
                    if running_min is None or ratio < running_min[0]:
                        running_min = (ratio, point)
                    if running_max is None or ratio > running_max[0]:
                        running_max = (ratio, point)
                if sequence == 1 or sequence % ANALYSIS_CADENCE_SLOTS == 0 or final:
                    analysis_candidates.append((
                        sequence, row, publication["publication_id"], fact_set_sha,
                        running_min, running_max,
                    ))

            if last_observation_publication is None:
                raise CandidateError(f"事件没有生成 Observation Publication：{reference}")
            previous_analysis: str | None = None
            last_analysis: dict[str, Any] | None = None
            for analysis_sequence, (
                observation_sequence, row, derived_from, fact_set_sha,
                analysis_min, analysis_max,
            ) in enumerate(
                analysis_candidates, start=1,
            ):
                ratio = visibility_ratio(row)
                if first_ratio is None or ratio is None:
                    direction = "not_applicable"
                    change = None
                else:
                    change = ratio - first_ratio
                    direction = "stable" if abs(change) < 1e-12 else ("up" if change > 0 else "down")
                final = row["state_point_utc"] == WINDOW_END
                snapshot = {
                    "schema_version": "rrc25-analysis-publication/v1",
                    "candidate_id": candidate_id, "incident_id": incident_id,
                    "collector_id": COLLECTOR_ID, "country_code": country,
                    "data_through": row["state_point_utc"],
                    "derived_from_observation_publication_id": derived_from,
                    "algorithm": {"name": ANALYSIS_ALGORITHM, "cadence_slots": ANALYSIS_CADENCE_SLOTS},
                    "trend_profile": {
                        "metric": "combined_fixed_cohort_visibility_ratio",
                        "start": first_ratio, "end": ratio, "change": change,
                        "direction": direction,
                        "minimum": None if analysis_min is None else {"value": analysis_min[0], "at": analysis_min[1]},
                        "maximum": None if analysis_max is None else {"value": analysis_max[0], "at": analysis_max[1]},
                        "observation_sequence": observation_sequence,
                    },
                    "analysis_claim": "deterministic_description_only",
                    "limitations": [LIMITATION, "没有可信正常带，不推断恢复或原因"],
                }
                publication = generated_publication(
                    candidate_id=candidate_id, incident_id=incident_id,
                    kind="analysis", revision=corrected_revision,
                    sequence=analysis_sequence, data_through=row["state_point_utc"],
                    observed_at=row["state_point_utc"], fact_id=None,
                    derived_from=derived_from, previous=previous_analysis,
                    correction_of=None, supersedes=None,
                    metric_dataset_id=receipt["metric_dataset_id"],
                    metric_slot_sha=row["metric_slot_sha256"], is_final=final,
                    fact_set_sha=fact_set_sha, snapshot=snapshot,
                )
                writers["analysis_publication"].write(publication)
                previous_analysis = publication["publication_id"]
                last_analysis = publication
                total_analysis_publications += 1
            if last_analysis is None or not last_analysis["is_final"]:
                raise CandidateError(f"事件缺少最终 Analysis Publication：{reference}")
            writers["pointer_plan"].write({
                "candidate_id": candidate_id, "incident_id": incident_id,
                "initial_observation_publication_id": old_current,
                "final_observation_publication_id": last_observation_publication["publication_id"],
                "final_analysis_publication_id": last_analysis["publication_id"],
            })

        files = []
        for role, writer in writers.items():
            item = writer.close()
            item["role"] = role
            files.append(item)
        content_sha = sha256_bytes(canonical_bytes([
            {key: item[key] for key in ("role", "row_count", "content_sha256")}
            for item in sorted(files, key=lambda value: value["role"])
        ]))
        built_at = utc_text(datetime.now(timezone.utc).replace(microsecond=0))
        manifest = {
            "schema_version": S4_SCHEMA_VERSION, "status": "complete",
            "database_model": S4_DATABASE_MODEL, "candidate_id": candidate_id,
            "run_id": build_id, "dataset_id": dataset_id,
            "implementation_id": args.implementation_id, "collector_id": COLLECTOR_ID,
            "window_start_utc": WINDOW_START, "window_end_exclusive_utc": WINDOW_END,
            "state_point_count": STATE_POINT_COUNT,
            "country_bucket_count": COUNTRY_BUCKET_COUNT,
            "event_count": EVENT_COUNT, "event_country_count": COUNTRY_COUNT,
            "legacy_publication_count": legacy_count,
            "observation_publication_count": total_observation_publications,
            "analysis_publication_count": total_analysis_publications,
            "event_fact_count": sum(stage_counts.values()), "lifecycle_stage_counts": stage_counts,
            "corrected_revision_incident_counts": revision_counts,
            "source_metric_dataset_id": receipt["metric_dataset_id"],
            "source_metric_content_sha256": receipt["metric_content_sha256"],
            "source_metric_manifest_sha256": receipt["manifest_sha256"],
            "source_metric_database_name": receipt["database_name"],
            "source_metric_database_fingerprint_sha256": receipt["database_fingerprint_sha256"],
            "source_metric_receipt_id": receipt["receipt_id"],
            "source_metric_receipt_content_sha256": receipt["content_sha256"],
            "source_metric_receipt_file_sha256": receipt_file_sha,
            "legacy_registry_uri": str(args.legacy_registry.resolve()),
            "legacy_registry_sha256": registry_sha,
            "lifecycle_algorithm": LIFECYCLE_ALGORITHM,
            "analysis_algorithm": ANALYSIS_ALGORITHM,
            "analysis_cadence_slots": ANALYSIS_CADENCE_SLOTS,
            "formal_recovery_assessment": "not_assessed_without_trusted_normal_band",
            "normal_append_revision_semantics": "same_revision_new_publication_id",
            "correction_revision_semantics": "new_revision_with_correction_of_and_final_supersedes",
            "current_pointer_semantics": "validated_atomic_advance_only",
            "content_sha256": content_sha, "files": sorted(files, key=lambda value: value["role"]),
            "built_at": built_at,
        }
        manifest_raw = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, indent=2,
        ).encode("utf-8") + b"\n"
        (staging / "manifest.json").write_bytes(manifest_raw)
        (staging / "COMPLETE.json").write_bytes(manifest_raw)
        os.rename(staging, target)
        return manifest
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def verify_data_files(root: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    complete = root / "COMPLETE.json"
    manifest_path = root / "manifest.json"
    if not complete.is_file() or complete.read_bytes() != manifest_path.read_bytes():
        raise CandidateError("S4 manifest 与 COMPLETE 不同或缺失")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != 7:
        raise CandidateError("S4 manifest 文件人口无效")
    verified = []
    for item in files:
        path = root / item["path"]
        actual_sha, size = sha256_file(path)
        content = hashlib.sha256()
        lines = 0
        with gzip.open(path, "rb") as source:
            header = source.readline()
            if not header:
                raise CandidateError(f"S4 文件没有表头：{path}")
            content.update(header)
            for line in source:
                content.update(line)
                lines += 1
        if (
            actual_sha != item["sha256"] or size != item["size_bytes"]
            or content.hexdigest() != item["content_sha256"]
            or lines != item["row_count"]
        ):
            raise CandidateError(f"S4 文件验真失败：{path}")
        verified.append(dict(item))
    expected_content = sha256_bytes(canonical_bytes([
        {key: item[key] for key in ("role", "row_count", "content_sha256")}
        for item in sorted(verified, key=lambda value: value["role"])
    ]))
    if expected_content != manifest.get("content_sha256"):
        raise CandidateError("S4 dataset 内容摘要无法独立复算")
    return verified


def validate_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    manifest = json.loads(raw)
    required = {
        "schema_version": S4_SCHEMA_VERSION, "status": "complete",
        "database_model": S4_DATABASE_MODEL, "collector_id": COLLECTOR_ID,
        "window_start_utc": WINDOW_START, "window_end_exclusive_utc": WINDOW_END,
        "state_point_count": STATE_POINT_COUNT,
        "country_bucket_count": COUNTRY_BUCKET_COUNT,
        "event_count": EVENT_COUNT, "event_country_count": COUNTRY_COUNT,
        "legacy_publication_count": 82,
        "event_fact_count": EVENT_COUNT * 3,
        "formal_recovery_assessment": "not_assessed_without_trusted_normal_band",
    }
    for field, expected in required.items():
        if manifest.get(field) != expected:
            raise CandidateError(f"S4 manifest 字段冲突：{field}")
    if manifest.get("lifecycle_stage_counts") != {
        "detected": EVENT_COUNT, "ongoing": EVENT_COUNT,
        "recovery_candidate": 0, "recovered_observation": 0, "final": EVENT_COUNT,
    }:
        raise CandidateError("正式生命周期事实人口不符合保守语义")
    for field in (
        "content_sha256", "source_metric_content_sha256",
        "source_metric_manifest_sha256", "source_metric_database_fingerprint_sha256",
        "source_metric_receipt_content_sha256", "source_metric_receipt_file_sha256",
        "legacy_registry_sha256",
    ):
        if not SHA_RE.fullmatch(str(manifest.get(field, ""))):
            raise CandidateError(f"S4 manifest 摘要无效：{field}")
    verify_data_files(path.parent, manifest)
    return manifest, sha256_bytes(raw)


def schema_fingerprint(database: Postgres) -> str:
    command = [
        "docker", "exec", database.container, "pg_dump", "-U", database.user,
        "-d", database.database, "--schema-only", "--no-owner", "--no-privileges",
    ]
    return sha256_bytes(run_command(command).stdout)


def database_summary(database: Postgres, manifest: Mapping[str, Any]) -> dict[str, Any]:
    sql = """
SELECT json_build_object(
 'source_count',(SELECT count(*) FROM domeye_event.source_binding),
 'incident_count',(SELECT count(*) FROM domeye_event.incident),
 'event_country_count',(SELECT count(DISTINCT country_code) FROM domeye_event.incident),
 'fact_count',(SELECT count(*) FROM domeye_event.event_fact),
 'legacy_publication_count',(SELECT count(*) FROM domeye_event.publication WHERE publication_kind='legacy_observation'),
 'observation_publication_count',(SELECT count(*) FROM domeye_event.publication WHERE publication_kind='observation'),
 'analysis_publication_count',(SELECT count(*) FROM domeye_event.publication WHERE publication_kind='analysis'),
 'pointer_count',(SELECT count(*) FROM domeye_event.publication_pointer),
 'pointer_audit_count',(SELECT count(*) FROM domeye_event.pointer_audit),
 'bad_pointer_count',(SELECT count(*) FROM domeye_event.current_publication_state WHERE observation_publication_id IS NULL OR analysis_publication_id IS NULL OR observation_data_through<>analysis_data_through OR analysis_lag_seconds<>0),
 'orphan_fact_count',(SELECT count(*) FROM domeye_event.event_fact f LEFT JOIN domeye_event.incident i USING(incident_id) WHERE i.incident_id IS NULL),
 'orphan_publication_count',(SELECT count(*) FROM domeye_event.publication p LEFT JOIN domeye_event.incident i USING(incident_id) WHERE i.incident_id IS NULL),
 'bad_analysis_source_count',(SELECT count(*) FROM domeye_event.publication a LEFT JOIN domeye_event.publication o ON o.publication_id=a.derived_from_observation_publication_id WHERE a.publication_kind='analysis' AND (o.publication_kind<>'observation' OR o.incident_id<>a.incident_id OR o.data_through<>a.data_through)),
 'bad_correction_count',(SELECT count(*) FROM domeye_event.incident i LEFT JOIN domeye_event.publication p ON p.incident_id=i.incident_id AND p.publication_kind='observation' AND p.is_final LEFT JOIN domeye_event.publication old ON old.publication_id=p.supersedes_publication_id WHERE p.revision<>i.corrected_observation_revision OR old.publication_id<>i.legacy_current_publication_id),
 'legacy_fixed_readable_count',(SELECT count(*) FROM domeye_event.publication WHERE publication_kind='legacy_observation' AND artifact_uri IS NOT NULL AND artifact_sha256 IS NOT NULL),
 'intermediate_lag_audit_count',(SELECT count(*) FROM domeye_event.pointer_audit WHERE new_observation_publication_id IS NOT NULL AND new_analysis_publication_id IS NULL AND reason='corrected_observation_promoted'),
 'analysis_catchup_audit_count',(SELECT count(*) FROM domeye_event.pointer_audit WHERE new_analysis_publication_id IS NOT NULL AND reason='analysis_caught_up')
);"""
    summary = json.loads(database.scalar(sql))
    expected = {
        "source_count": 2, "incident_count": EVENT_COUNT,
        "event_country_count": COUNTRY_COUNT,
        "fact_count": manifest["event_fact_count"],
        "legacy_publication_count": manifest["legacy_publication_count"],
        "observation_publication_count": manifest["observation_publication_count"],
        "analysis_publication_count": manifest["analysis_publication_count"],
        "pointer_count": EVENT_COUNT, "pointer_audit_count": EVENT_COUNT * 3,
        "bad_pointer_count": 0, "orphan_fact_count": 0,
        "orphan_publication_count": 0, "bad_analysis_source_count": 0,
        "bad_correction_count": 0,
        "legacy_fixed_readable_count": manifest["legacy_publication_count"],
        "intermediate_lag_audit_count": EVENT_COUNT,
        "analysis_catchup_audit_count": EVENT_COUNT,
    }
    if summary != expected:
        raise CandidateError(f"S4 数据库人口、血缘或指针闭合失败：{summary} != {expected}")
    return summary


def insert_candidate_identity(database: Postgres, manifest: Mapping[str, Any], manifest_sha: str) -> None:
    database.psql(f"""
INSERT INTO domeye_event.candidate_registry(
 candidate_id,dataset_id,collector_id,window_start_utc,window_end_exclusive_utc,
 state_point_count,country_bucket_count,implementation_id,manifest_sha256,
 content_sha256,status
) VALUES (
 {sql_literal(manifest['candidate_id'])},{sql_literal(manifest['dataset_id'])},'rrc25',
 timestamptz '{WINDOW_START}',timestamptz '{WINDOW_END}',4320,241,
 {sql_literal(manifest['implementation_id'])},{sql_literal(manifest_sha)},
 {sql_literal(manifest['content_sha256'])},'loading'
);""")


def load_candidate(args: argparse.Namespace) -> dict[str, Any]:
    if not DATABASE_RE.fullmatch(args.database) or args.database in {
        "bgp_project", args.s3_database,
    }:
        raise CandidateError("目标数据库名无效或指向旧库/S3来源库")
    if args.receipt.exists():
        raise CandidateError("S4 装载回执已存在，create-only 装载拒绝覆盖")
    manifest, manifest_sha = validate_manifest(args.manifest)
    receipt, receipt_file_sha = load_and_verify_s3_receipt(args.s3_receipt)
    if (
        receipt["candidate_id"] != manifest["candidate_id"]
        or receipt["metric_dataset_id"] != manifest["source_metric_dataset_id"]
        or receipt["content_sha256"] != manifest["source_metric_receipt_content_sha256"]
        or receipt_file_sha != manifest["source_metric_receipt_file_sha256"]
        or receipt["database_fingerprint_sha256"] != manifest["source_metric_database_fingerprint_sha256"]
    ):
        raise CandidateError("S4 manifest 与 S3 回执身份冲突")
    source = Postgres(args.container, args.s3_database, args.user)
    verify_s3_database(source, receipt)
    database = Postgres(args.container, args.database, args.user)
    exists = database.scalar(
        f"SELECT count(*) FROM pg_database WHERE datname={sql_literal(args.database)}",
        database="postgres",
    )
    if exists != "0":
        raise CandidateError("S4 候选数据库已存在，create-only 装载拒绝覆盖")
    database.psql(
        f'CREATE DATABASE "{args.database}" TEMPLATE template0 ENCODING \'UTF8\'',
        database="postgres",
    )
    try:
        database.apply_file(args.ddl)
        insert_candidate_identity(database, manifest, manifest_sha)
        role_map = {item["role"]: item for item in manifest["files"]}
        root = args.manifest.parent
        load_order = (
            ("source_binding", "domeye_event.source_binding", SOURCE_COLUMNS),
            ("incident", "domeye_event.incident", INCIDENT_COLUMNS),
            ("event_fact", "domeye_event.event_fact", FACT_COLUMNS),
            ("legacy_publication", "domeye_event.publication", PUBLICATION_COLUMNS),
            ("observation_publication", "domeye_event.publication", PUBLICATION_COLUMNS),
            ("analysis_publication", "domeye_event.publication", PUBLICATION_COLUMNS),
            ("pointer_plan", "domeye_event.pointer_plan", POINTER_PLAN_COLUMNS),
        )
        for role, table, columns in load_order:
            database.copy_gzip(root / role_map[role]["path"], table, columns)

        database.psql("""
INSERT INTO domeye_event.publication_pointer(
 candidate_id,incident_id,current_observation_publication_id,
 current_analysis_publication_id,pointer_version,updated_at,last_reason
)
SELECT candidate_id,incident_id,initial_observation_publication_id,NULL,1,clock_timestamp(),
       'legacy_pointer_imported'
FROM domeye_event.pointer_plan ORDER BY incident_id;
""")
        lag_updates = database.psql("""
SELECT domeye_event.advance_publication_pointer(
 p.incident_id,p.final_observation_publication_id,NULL,1,'corrected_observation_promoted'
)
FROM domeye_event.pointer_plan p ORDER BY p.incident_id;
""")
        if lag_updates.returncode:
            raise CandidateError("Observation current 原子推进失败")
        lag_count = int(database.scalar(
            "SELECT count(*) FROM domeye_event.current_publication_state "
            "WHERE observation_publication_id IS NOT NULL AND analysis_publication_id IS NULL"
        ))
        if lag_count != EVENT_COUNT:
            raise CandidateError("Observation 领先 Analysis 的可见滞后未形成")
        database.psql("""
SELECT domeye_event.advance_publication_pointer(
 p.incident_id,p.final_observation_publication_id,p.final_analysis_publication_id,2,
 'analysis_caught_up'
)
FROM domeye_event.pointer_plan p ORDER BY p.incident_id;
""")
        summary = database_summary(database, manifest)
        schema_sha = schema_fingerprint(database)
        fingerprint = sha256_bytes(canonical_bytes({
            "candidate_id": manifest["candidate_id"], "dataset_id": manifest["dataset_id"],
            "content_sha256": manifest["content_sha256"], "manifest_sha256": manifest_sha,
            "source_metric_database_fingerprint_sha256": manifest["source_metric_database_fingerprint_sha256"],
            "schema_sha256": schema_sha, "summary": summary,
        }))
        loaded_at = utc_text(datetime.now(timezone.utc).replace(microsecond=0))
        receipt_id = stable_id("event_publication_load_receipt_v1_", {
            "database": args.database, "candidate_id": manifest["candidate_id"],
            "dataset_id": manifest["dataset_id"], "database_fingerprint_sha256": fingerprint,
        })
        database.psql(f"""
BEGIN;
UPDATE domeye_event.candidate_registry SET status='complete'
 WHERE candidate_id={sql_literal(manifest['candidate_id'])} AND status='loading';
INSERT INTO domeye_event.load_receipt(
 receipt_id,candidate_id,dataset_id,manifest_sha256,schema_sha256,
 database_fingerprint_sha256,loaded_at,status
) VALUES (
 {sql_literal(receipt_id)},{sql_literal(manifest['candidate_id'])},
 {sql_literal(manifest['dataset_id'])},{sql_literal(manifest_sha)},
 {sql_literal(schema_sha)},{sql_literal(fingerprint)},timestamptz {sql_literal(loaded_at)},'complete'
);
COMMIT;""")
        container_identity = run_command([
            "docker", "inspect", "-f", "{{.Id}} {{.Config.Image}}", args.container,
        ]).stdout.decode().strip().split(" ", 1)
        result = {
            "schema_version": "rrc25-event-publication-database-load-receipt/v1",
            "status": "complete", "receipt_id": receipt_id,
            "candidate_id": manifest["candidate_id"], "dataset_id": manifest["dataset_id"],
            "dataset_content_sha256": manifest["content_sha256"],
            "manifest_sha256": manifest_sha, "database_name": args.database,
            "database_model": S4_DATABASE_MODEL,
            "container_id": container_identity[0],
            "container_image": container_identity[1] if len(container_identity) > 1 else "",
            "postgresql_version": database.scalar("SHOW server_version"),
            "schema_sha256": schema_sha,
            "database_fingerprint_sha256": fingerprint, "summary": summary,
            "source_metric_dataset_id": manifest["source_metric_dataset_id"],
            "source_metric_database_fingerprint_sha256": manifest["source_metric_database_fingerprint_sha256"],
            "loaded_at": loaded_at, "old_database_written": False,
            "s3_database_written": False, "selected_by_runtime": False,
            "content_sha256": "",
        }
        result["content_sha256"] = receipt_content_sha(result)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        with args.receipt.open("x", encoding="utf-8") as target:
            json.dump(result, target, ensure_ascii=False, sort_keys=True, indent=2)
            target.write("\n")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return result
    except BaseException:
        try:
            database.psql("UPDATE domeye_event.candidate_registry SET status='failed' WHERE status='loading'")
        except BaseException:
            pass
        raise


def expect_sql_failure(database: Postgres, sql: str, expected: str) -> dict[str, Any]:
    result = database.psql(sql, check=False)
    stderr = result.stderr.decode("utf-8", "replace")
    if result.returncode == 0 or expected not in stderr:
        raise CandidateError(
            f"预期 SQL 失败没有按合同发生：expected={expected} code={result.returncode} stderr={stderr[-2000:]}"
        )
    return {"exit_code": result.returncode, "expected_error": expected, "matched": True}


def run_drills(args: argparse.Namespace) -> dict[str, Any]:
    manifest, manifest_sha = validate_manifest(args.manifest)
    receipt = json.loads(args.receipt.read_bytes())
    if receipt_content_sha(receipt) != receipt.get("content_sha256"):
        raise CandidateError("S4 数据库回执内容摘要无法复算")
    if receipt.get("candidate_id") != manifest["candidate_id"] or receipt.get("manifest_sha256") != manifest_sha:
        raise CandidateError("S4 演练输入身份冲突")
    database = Postgres(args.container, args.database, args.user)
    summary = database_summary(database, manifest)
    before = database.scalar("""
SELECT publication_id||'|'||content_sha256 FROM domeye_event.publication
WHERE publication_kind='legacy_observation' ORDER BY publication_id LIMIT 1;
""")
    immutable_publication = expect_sql_failure(
        database,
        "UPDATE domeye_event.publication SET is_final=NOT is_final WHERE publication_id=(SELECT publication_id FROM domeye_event.publication LIMIT 1)",
        "immutable_relation",
    )
    immutable_fact = expect_sql_failure(
        database,
        "DELETE FROM domeye_event.event_fact WHERE fact_id=(SELECT fact_id FROM domeye_event.event_fact LIMIT 1)",
        "immutable_relation",
    )
    pointer_before = database.scalar("""
SELECT incident_id||'|'||current_observation_publication_id||'|'||pointer_version
FROM domeye_event.publication_pointer ORDER BY incident_id LIMIT 1;
""")
    stale_pointer = expect_sql_failure(
        database,
        "SELECT domeye_event.advance_publication_pointer("
        "(SELECT incident_id FROM domeye_event.publication_pointer ORDER BY incident_id LIMIT 1),"
        "(SELECT current_observation_publication_id FROM domeye_event.publication_pointer ORDER BY incident_id LIMIT 1),"
        "(SELECT current_analysis_publication_id FROM domeye_event.publication_pointer ORDER BY incident_id LIMIT 1),"
        "2,'stale_version_drill')",
        "pointer_version_conflict",
    )
    missing_publication = expect_sql_failure(
        database,
        "SELECT domeye_event.advance_publication_pointer("
        "(SELECT incident_id FROM domeye_event.publication_pointer ORDER BY incident_id LIMIT 1),"
        "'observation_publication_v1_00000000000000000000000000000000',NULL,3,'missing_publication_drill')",
        "invalid_observation_publication",
    )
    invalid_analysis = expect_sql_failure(
        database,
        "BEGIN; INSERT INTO domeye_event.publication("
        "candidate_id,publication_id,incident_id,publication_kind,revision,sequence_in_revision,data_through,observed_at,event_fact_id,derived_from_observation_publication_id,previous_publication_id,correction_of_publication_id,supersedes_publication_id,source_metric_dataset_id,source_metric_slot_sha256,is_final,validation_state,fact_set_sha256,payload_sha256,content_sha256,artifact_uri,artifact_sha256,snapshot) "
        "SELECT candidate_id,'analysis_publication_v1_00000000000000000000000000000000',incident_id,'analysis',999,1,data_through,observed_at,NULL,publication_id,NULL,NULL,NULL,source_metric_dataset_id,source_metric_slot_sha256,false,'verified',fact_set_sha256,payload_sha256,content_sha256,NULL,NULL,snapshot FROM domeye_event.publication WHERE publication_kind='analysis' LIMIT 1; COMMIT;",
        "invalid_analysis_derivation",
    )
    transition_sql = """
WITH expected(previous_stage,next_stage,allowed) AS (VALUES
 ('detected','ongoing',true),
 ('ongoing','recovery_candidate',true),
 ('recovery_candidate','ongoing',true),
 ('recovery_candidate','recovered_observation',true),
 ('recovered_observation','ongoing',true),
 ('recovered_observation','final',true),
 ('final','ongoing',false),
 ('detected','recovered_observation',false)
)
SELECT count(*) FROM expected
WHERE domeye_event.lifecycle_transition_allowed(previous_stage,next_stage)<>allowed;
"""
    transition_mismatch = int(database.scalar(transition_sql))
    if transition_mismatch:
        raise CandidateError("恢复候选、观测恢复或候选撤销状态机不符合合同")
    after = database.scalar("""
SELECT publication_id||'|'||content_sha256 FROM domeye_event.publication
WHERE publication_kind='legacy_observation' ORDER BY publication_id LIMIT 1;
""")
    pointer_after = database.scalar("""
SELECT incident_id||'|'||current_observation_publication_id||'|'||pointer_version
FROM domeye_event.publication_pointer ORDER BY incident_id LIMIT 1;
""")
    if before != after or pointer_before != pointer_after:
        raise CandidateError("失败演练改变了旧 Publication 或 current 指针")
    generated_at = utc_text(datetime.now(timezone.utc).replace(microsecond=0))
    result = {
        "schema_version": "rrc25-event-publication-acceptance-drill/v1",
        "status": "pass", "candidate_id": manifest["candidate_id"],
        "dataset_id": manifest["dataset_id"], "manifest_sha256": manifest_sha,
        "database_name": args.database,
        "database_fingerprint_sha256": receipt["database_fingerprint_sha256"],
        "checks": {
            "publication_update_rejected": immutable_publication,
            "event_fact_delete_rejected": immutable_fact,
            "stale_pointer_rejected_atomically": stale_pointer,
            "missing_publication_rejected_atomically": missing_publication,
            "invalid_analysis_derivation_rejected": invalid_analysis,
            "legacy_publication_unchanged": before == after,
            "pointer_unchanged_after_failures": pointer_before == pointer_after,
            "lifecycle_transition_matrix_mismatch_count": transition_mismatch,
            "recovery_candidate_revocation_supported": True,
            "formal_recovery_fact_count": 0,
            "formal_recovery_reason": "没有可信正常带，不伪造恢复事实",
            "database_summary": summary,
        },
        "generated_at": generated_at, "content_sha256": "",
    }
    result["content_sha256"] = receipt_content_sha(result)
    if args.output.exists():
        raise CandidateError("S4 演练回执已存在，拒绝覆盖")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=False, sort_keys=True, indent=2)
        target.write("\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--legacy-registry", required=True, type=Path)
    build.add_argument("--s3-receipt", required=True, type=Path)
    build.add_argument("--container", required=True)
    build.add_argument("--s3-database", required=True)
    build.add_argument("--user", default="postgres")
    build.add_argument("--implementation-id", required=True)
    build.add_argument("--output", required=True, type=Path)

    load = sub.add_parser("load")
    load.add_argument("--manifest", required=True, type=Path)
    load.add_argument("--s3-receipt", required=True, type=Path)
    load.add_argument("--ddl", required=True, type=Path)
    load.add_argument("--container", required=True)
    load.add_argument("--s3-database", required=True)
    load.add_argument("--database", required=True)
    load.add_argument("--user", default="postgres")
    load.add_argument("--receipt", required=True, type=Path)

    drill = sub.add_parser("drill")
    drill.add_argument("--manifest", required=True, type=Path)
    drill.add_argument("--receipt", required=True, type=Path)
    drill.add_argument("--container", required=True)
    drill.add_argument("--database", required=True)
    drill.add_argument("--user", default="postgres")
    drill.add_argument("--output", required=True, type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "build":
        manifest = build_candidate(args)
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    elif args.command == "load":
        load_candidate(args)
    elif args.command == "drill":
        run_drills(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CandidateError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({
            "status": "failed", "error_type": type(error).__name__, "error": str(error),
        }, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
