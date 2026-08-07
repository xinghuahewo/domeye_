#!/usr/bin/env python3
"""机器核对国家中断通用观测页 S4 的不可变读模型与有界 API。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_RECORD = REPOSITORY_ROOT / "docs" / "国家中断通用观测页S4验收记录.md"
READ_MODEL_SCHEMA = (
    REPOSITORY_ROOT
    / "contracts"
    / "data"
    / "country-outage-general-read-model.schema.json"
)
OPENAPI_PATH = REPOSITORY_ROOT / "contracts" / "openapi.json"
REMOTE_HOST = "root@10.99.8.16"
REMOTE_ROOT = (
    "/home/bgpdata/Domeye-Core-dev-data/research-runs/"
    "country-outage-general-page-s4-d1f4d11/read-model"
)
REMOTE_AUDIT_PATH = REMOTE_ROOT + "/S4-AUDIT.json"
REMOTE_SOURCE_ROOT = "/tmp/domeye-s4-d1f4d11.gPZdTL"

EXPECTED = {
    "implementation_id": "git:d1f4d11fa7467dad2612e3776bbb5573c633064b",
    "dataset_id": "general_read_model_dataset_v1_63be5d12ef847d74824efe5be9892f8a",
    "content_sha256": "f7c7a8797348dfab3766f7514a54022c4d8d5582610b0d102c7e5889bbb030c7",
    "manifest_sha256": "1d1e0463af18cf1320ce37f918ed47fdfb4e1a4f78a73f198923db50a4fca5b9",
    "cohort_dataset_id": "event_cohort_dataset_v1_11c18b460a735c1acfa5f925d09c1bd8",
    "cohort_content_sha256": "75d53ae4ba355c859d70b79aabf3ca597915fbfc66239f9eaa050b7c6004dd6c",
    "cohort_manifest_sha256": "3bebb14181912e645e0e1d25439edda9be2e327e059e715655852d102455fef6",
    "metric_dataset_id": "event_metric_dataset_v1_136ef94a1068d83f25f844c0fc85f756",
    "metric_content_sha256": "1b38ec27bc444e6086c3e34c363089b1d739a6ef3bd0dbf156a027d234c70905",
    "metric_manifest_sha256": "c745153178e7e8a0ccf8ba4e5ac285aa76b66cc3980bdeacb28b40939b0d23d5",
    "as_path_dataset_id": "event_as_path_dataset_v1_027b658b0a3121f9ec41d33da3a01504",
    "as_path_content_sha256": "1398f13b5271ee96fa6591bf62f3bf9fa94dc2dd594a7cc8673a64b1c65ed797",
    "as_path_manifest_sha256": "b286c8973ac0af139e1f309c6362c77e951961b3691887c77cbdf4365b64bfa5",
    "lifecycle_snapshot_id": "event_lifecycle_snapshot_v1_7a76c506bd8641406c0d87ba2fdd98f4",
    "events": 81,
    "state_points": 13_488,
    "affected_as": 2_112,
    "relations": 12_447,
    "path_samples": 37_205,
    "file_count": 326,
}

REQUIRED_RECORD_PHRASES = (
    "版本：2.1",
    "RRC25-only",
    "81 个事件",
    "13,488",
    "2,112",
    "12,447",
    "37,205",
    "4.7 MB",
    "最多 60 条",
    "最多 3 个真实路径样本",
    "383,693 字节",
    "106.824 ms",
    "不重扫 MRT",
    "不重放 RouteEvent 或 RouteState",
    "候选不等于生产",
    "通用观测页最终验收回检：S4 一致",
)

REMOTE_DEEP_AUDIT_SCRIPT = r'''
import gzip
import hashlib
import json
import os

ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s4-d1f4d11/read-model"
COHORT_ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s1-711350f/event-cohorts"
METRIC_ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s2-4b3559a/event-metrics"
PATH_ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s3-18dfdf3/event-as-path"
SAMPLES = {
    "IR": "country_outage/2026-02-27 09:12:32/IR/1/r",
    "MW": "country_outage/2026-03-09 22:09:38/MW/2/r",
}
TRACKS = (
    "interrupted_prefix_count", "completely_interrupted_prefix_count",
    "invisible_direction_count", "affected_asn_count",
    "route_interrupted_asn_count", "fixed_visible_ipv4_address_count",
    "fixed_visible_ipv6_slash48_count", "new_visible_ipv4_prefix_count",
    "new_visible_ipv6_prefix_count", "new_visible_ipv4_address_count",
    "new_visible_ipv6_slash48_count", "new_cumulative_ipv4_prefix_count",
    "new_cumulative_ipv6_prefix_count", "new_cumulative_ipv4_address_count",
    "new_cumulative_ipv6_slash48_count",
)
ASN_FIELDS = (
    "asn", "as_name", "organization", "nature", "name_state",
    "organization_state", "nature_state", "event_classification",
    "fixed_prefix_count", "peak_partial_prefix_count",
    "peak_complete_prefix_count", "peak_invisible_direction_count",
    "path_downstream_asn_count", "concurrent_downstream_asn_count",
)
DOWNSTREAM_FIELDS = (
    "affected_asn", "downstream_asn", "downstream_as_name",
    "downstream_organization", "downstream_nature", "downstream_name_state",
    "downstream_organization_state", "downstream_nature_state",
    "observed_path_count", "associated_fixed_prefix_count",
    "independent_direction_count", "route_observation_count",
    "concurrent_state_point_count", "first_concurrent_state_point_utc",
    "last_concurrent_state_point_utc",
    "peak_concurrent_interrupted_prefix_count",
    "peak_concurrent_ipv4_address_count",
    "peak_concurrent_ipv6_slash48_count",
)
SAMPLE_FIELDS = (
    "prefix", "address_family", "as_path_id", "as_path_canonical",
    "independent_peer_asns", "route_observation_count",
)

def raw(path):
    with open(path, "rb") as stream:
        return stream.read()

def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

def content_sha(value):
    copy = dict(value)
    copy.pop("content_sha256", None)
    return hashlib.sha256(canonical(copy)).hexdigest()

def load_twin(root):
    left = raw(os.path.join(root, "manifest.json"))
    right = raw(os.path.join(root, "COMPLETE.json"))
    assert left == right
    return json.loads(left), hashlib.sha256(left).hexdigest()

def source_rows(root, meta):
    path = os.path.join(root, meta["path"])
    assert os.path.isfile(path) and not os.path.islink(path)
    assert os.path.getsize(path) == meta["size_bytes"] and digest(path) == meta["sha256"]
    count = 0
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            count += 1
            yield json.loads(line)
    assert count == meta["row_count"]

def read_object(meta):
    path = os.path.join(ROOT, meta["path"])
    compressed = raw(path)
    assert len(compressed) == meta["size_bytes"]
    assert hashlib.sha256(compressed).hexdigest() == meta["sha256"]
    value = gzip.decompress(compressed)
    assert hashlib.sha256(value).hexdigest() == meta["content_sha256"]
    return json.loads(value)

def read_rows(meta):
    path = os.path.join(ROOT, meta["path"])
    compressed = raw(path)
    assert len(compressed) == meta["size_bytes"]
    assert hashlib.sha256(compressed).hexdigest() == meta["sha256"]
    value = gzip.decompress(compressed)
    assert hashlib.sha256(value).hexdigest() == meta["content_sha256"]
    rows = [json.loads(line) for line in value.splitlines()]
    assert len(rows) == meta["row_count"]
    return rows

manifest, manifest_sha = load_twin(ROOT)
cohorts, cohort_sha = load_twin(COHORT_ROOT)
metrics, metric_sha = load_twin(METRIC_ROOT)
paths, path_sha = load_twin(PATH_ROOT)
lifecycle = json.loads(raw(os.path.join(COHORT_ROOT, "event-lifecycle-snapshot.json")))
assert manifest["content_sha256"] == content_sha(manifest)
assert manifest["schema_version"] == "country-outage-general-read-model-store/v1"
assert manifest["collector_id"] == "rrc25"
assert manifest["window_start_utc"] == "2026-02-24T00:00:00Z"
assert manifest["window_end_exclusive_utc"] == "2026-03-11T00:00:00Z"
assert manifest["source_event_cohort_dataset_id"] == cohorts["dataset_id"]
assert manifest["source_event_cohort_content_sha256"] == cohorts["content_sha256"]
assert manifest["source_event_cohort_manifest_sha256"] == cohort_sha
assert manifest["source_event_metric_dataset_id"] == metrics["dataset_id"]
assert manifest["source_event_metric_content_sha256"] == metrics["content_sha256"]
assert manifest["source_event_metric_manifest_sha256"] == metric_sha
assert manifest["source_event_as_path_dataset_id"] == paths["dataset_id"]
assert manifest["source_event_as_path_content_sha256"] == paths["content_sha256"]
assert manifest["source_event_as_path_manifest_sha256"] == path_sha
assert manifest["source_lifecycle_snapshot_id"] == lifecycle["snapshot_id"]
assert manifest["path_evidence_semantics"] == "bounded_real_path_samples_full_evidence_remains_in_s3_audit_artifact"
assert manifest["causal_boundary"] == "rrc25_path_association_is_not_dependency_propagation_user_impact_or_cause"

metric_by_id = {event["event_metric_id"]: event for event in metrics["events"]}
path_by_id = {event["event_metric_id"]: event for event in paths["events"]}
lifecycle_by_ref = {event["legacy_reference"]: event for event in lifecycle["events"]}
assert len(metric_by_id) == len(path_by_id) == len(lifecycle_by_ref) == len(manifest["events"])

totals = {"state_points": 0, "affected_as": 0, "relations": 0, "path_samples": 0}
sample_results = {}
references = set()
for event in manifest["events"]:
    assert event["content_sha256"] == content_sha(event)
    reference = event["legacy_reference"]
    assert reference not in references
    references.add(reference)
    metric = metric_by_id[event["event_metric_id"]]
    path_event = path_by_id[event["event_metric_id"]]
    life = lifecycle_by_ref[reference]
    assert event["cohort_id"] == metric["cohort_id"] == path_event["cohort_id"]
    assert event["window_start_utc"] == metric["window_start_utc"] == life["window_start_utc"]
    assert event["window_end_utc"] == metric["projection_end_state_point_utc"] == life["projection_end_state_point_utc"]
    assert event["data_through"] == event["window_end_utc"]
    assert event["lifecycle_state"] == life["lifecycle_state"]
    assert event["is_final_in_data_range"] == life["is_final_in_data_range"]

    overview = read_object(event["overview"])
    series = read_object(event["series"])
    as_rows = read_rows(event["affected_as"])
    downstream_rows = read_rows(event["path_downstreams"])
    assert overview["content_sha256"] == content_sha(overview)
    assert series["content_sha256"] == content_sha(series)
    assert overview["event_read_model_id"] == series["event_read_model_id"] == event["event_read_model_id"]
    assert overview["publication_id"] == series["publication_id"] == event["publication_id"]
    assert overview["final_values"] == metric["final_values"]
    assert overview["affected_as_count"] == event["affected_as_count"] == len(as_rows)
    assert overview["path_downstream_relation_count"] == event["path_downstream_relation_count"] == len(downstream_rows)

    source_points = list(source_rows(METRIC_ROOT, metric["series"]))
    assert len(source_points) == event["state_point_count"] == series["point_count"]
    assert series["timestamps"] == [point["state_point_utc"] for point in source_points]
    for track in TRACKS:
        assert series["tracks"][track] == [point["values"][track] for point in source_points]
    assert set(series["tracks"]) == set(TRACKS)

    source_as = list(source_rows(PATH_ROOT, path_event["affected_as"]))
    assert len(source_as) == len(as_rows)
    for rank, (source, row) in enumerate(zip(source_as, as_rows), start=1):
        assert row["schema_version"] == "country-outage-general-affected-as/v1"
        assert row["event_read_model_id"] == event["event_read_model_id"]
        assert row["publication_id"] == event["publication_id"] and row["rank"] == rank
        assert all(row[field] == source[field] for field in ASN_FIELDS)

    source_downstream = list(source_rows(PATH_ROOT, path_event["path_downstreams"]))
    source_by_key = {(row["affected_asn"], row["downstream_asn"]): row for row in source_downstream}
    output_by_key = {(row["affected_asn"], row["downstream_asn"]): row for row in downstream_rows}
    assert len(source_by_key) == len(output_by_key) == len(downstream_rows)
    expected_samples = {key: [] for key in source_by_key}
    evidence_count = 0
    for evidence in source_rows(PATH_ROOT, path_event["path_evidence"]):
        evidence_count += 1
        key = (evidence["affected_asn"], evidence["downstream_asn"])
        values = expected_samples[key]
        if len(values) < 3:
            values.append({field: evidence[field] for field in SAMPLE_FIELDS})
    assert evidence_count == path_event["path_evidence_count"]
    for key, source in source_by_key.items():
        row = output_by_key[key]
        assert row["schema_version"] == "country-outage-general-path-downstream/v1"
        assert row["event_read_model_id"] == event["event_read_model_id"]
        assert row["publication_id"] == event["publication_id"]
        assert all(row[field] == source[field] for field in DOWNSTREAM_FIELDS)
        assert row["path_samples"] == expected_samples[key]
        assert 1 <= len(row["path_samples"]) <= 3
        assert row["relationship_semantics"] == "observed_ordered_rrc25_path_association_not_dependency_or_cause"
    expected_order = sorted(
        downstream_rows,
        key=lambda row: (
            0 if row["concurrent_state_point_count"] > 0 else 1,
            -row["peak_concurrent_interrupted_prefix_count"],
            -row["associated_fixed_prefix_count"],
            row["affected_asn"], row["downstream_asn"],
        ),
    )
    assert downstream_rows == expected_order

    totals["state_points"] += event["state_point_count"]
    totals["affected_as"] += event["affected_as_count"]
    totals["relations"] += event["path_downstream_relation_count"]
    totals["path_samples"] += event["path_sample_count"]
    if reference in SAMPLES.values():
        code = next(code for code, value in SAMPLES.items() if value == reference)
        sample_results[code] = {
            "incident_id": event["incident_id"],
            "publication_id": event["publication_id"],
            "window_start_utc": event["window_start_utc"],
            "window_end_utc": event["window_end_utc"],
            "is_final_in_data_range": event["is_final_in_data_range"],
            "state_points": event["state_point_count"],
            "affected_as": event["affected_as_count"],
            "relations": event["path_downstream_relation_count"],
            "peak_interrupted_prefixes": overview["peaks"]["interrupted_prefix_count"]["value"],
            "peak_route_interrupted_as": overview["peaks"]["route_interrupted_asn_count"]["value"],
        }

assert references == set(metric["legacy_reference"] for metric in metrics["events"])
assert totals["state_points"] == manifest["state_point_count"]
assert totals["affected_as"] == manifest["affected_as_count"]
assert totals["relations"] == manifest["path_downstream_relation_count"]
assert totals["path_samples"] == manifest["path_sample_count"]
file_count = sum(len(files) for _, _, files in os.walk(ROOT))
size_bytes = sum(os.path.getsize(os.path.join(base, name)) for base, _, files in os.walk(ROOT) for name in files)
assert file_count == 326 and size_bytes < 10_000_000
print(json.dumps({
    "status": "pass",
    "read_model": {
        "implementation_id": manifest["implementation_id"],
        "dataset_id": manifest["dataset_id"],
        "content_sha256": manifest["content_sha256"],
        "manifest_sha256": manifest_sha,
        "source_event_cohort_dataset_id": manifest["source_event_cohort_dataset_id"],
        "source_event_cohort_content_sha256": manifest["source_event_cohort_content_sha256"],
        "source_event_cohort_manifest_sha256": manifest["source_event_cohort_manifest_sha256"],
        "source_event_metric_dataset_id": manifest["source_event_metric_dataset_id"],
        "source_event_metric_content_sha256": manifest["source_event_metric_content_sha256"],
        "source_event_metric_manifest_sha256": manifest["source_event_metric_manifest_sha256"],
        "source_event_as_path_dataset_id": manifest["source_event_as_path_dataset_id"],
        "source_event_as_path_content_sha256": manifest["source_event_as_path_content_sha256"],
        "source_event_as_path_manifest_sha256": manifest["source_event_as_path_manifest_sha256"],
        "source_lifecycle_snapshot_id": manifest["source_lifecycle_snapshot_id"],
        "events": manifest["event_count"],
        "state_points": manifest["state_point_count"],
        "affected_as": manifest["affected_as_count"],
        "relations": manifest["path_downstream_relation_count"],
        "path_samples": manifest["path_sample_count"],
        "file_count": file_count,
        "size_bytes": size_bytes,
    },
    "samples": sample_results,
    "source_path_evidence_rows_checked": paths["path_evidence_count"],
    "normal_read_model_contains_full_path_evidence": False,
}, ensure_ascii=False, separators=(",", ":")))
'''

REMOTE_HTTP_AUDIT_SCRIPT = r'''
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE = "/tmp/domeye-s4-d1f4d11.gPZdTL"
ROOT = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s4-d1f4d11/read-model"
PYTHON = "/home/bgpdata/Domeye-Core/backend/.venv/bin/python"
PORT = 38672
BASE = "http://127.0.0.1:38672"
REFS = {
    "IR": "country_outage/2026-02-27 09:12:32/IR/1/r",
    "MW": "country_outage/2026-03-09 22:09:38/MW/2/r",
}
environment = dict(os.environ)
environment.update({
    "DOMEYE_CORE_SKIP_LOCAL_ENV": "true", "FLASK_CONFIG": "testing",
    "AUTO_INIT_DB": "false", "LOAD_CORE_DATA_ON_STARTUP": "false",
    "DEBUG": "false", "HOST": "127.0.0.1", "PORT": str(PORT),
    "DOMEYE_COUNTRY_OUTAGE_GENERAL_READ_MODEL": ROOT,
})
process = subprocess.Popen(
    [PYTHON, "run.py"], cwd=os.path.join(SOURCE, "backend"), env=environment,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
records = []
def call(path, query=None, headers=None, expected=200):
    url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
    request = urllib.request.Request(url, headers=headers or {})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(); status = response.status; response_headers = dict(response.headers)
    except urllib.error.HTTPError as error:
        raw = error.read(); status = error.code; response_headers = dict(error.headers)
    elapsed = (time.perf_counter() - started) * 1000
    assert status == expected, (url, status, raw[:300])
    records.append({"path": path, "status": status, "bytes": len(raw), "latency_ms": round(elapsed, 3), "sha256": hashlib.sha256(raw).hexdigest()})
    return (json.loads(raw) if raw else None), response_headers, raw
try:
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("隔离候选进程提前退出")
        try:
            call("/api/v1/healthz")
            records.clear()
            break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("隔离候选未在 10 秒内就绪")
    samples = {}
    for code, reference in REFS.items():
        resolution, _, _ = call("/api/v2/events/resolve", {"ref": reference})
        assert resolution["schema_version"] == "country_outage_general_resolution_v1"
        assert resolution["collector_id"] == "rrc25" and resolution["country_code"] == code
        incident = resolution["incident_id"]; publication = resolution["publication_id"]
        common = {"publication_id": publication}
        prefix = "/api/v2/country-outages/" + urllib.parse.quote(incident, safe="")
        overview, overview_headers, overview_raw = call(prefix + "/overview", common)
        series, _, series_raw = call(prefix + "/series", common)
        as_first, _, as_raw = call(prefix + "/asns", {**common, "page": 1, "page_size": 60})
        path_first, _, path_raw = call(prefix + "/path-downstreams", {**common, "page": 1, "page_size": 60})
        audit, _, _ = call(prefix + "/audit", common)
        for payload in (overview, series, as_first, path_first, audit):
            assert payload["incident_id"] == incident and payload["publication_id"] == publication
            assert payload["window_start_utc"] == resolution["window_start_utc"]
            assert payload["window_end_utc"] == resolution["window_end_utc"]
        assert len(series["timestamps"]) == series["point_count"]
        assert all(len(values) == series["point_count"] for values in series["tracks"].values())
        as_count = 0
        for page in range(1, as_first["page_count"] + 1):
            payload = as_first if page == 1 else call(prefix + "/asns", {**common, "page": page, "page_size": 60})[0]
            as_count += len(payload["items"])
            assert len(payload["items"]) <= 60
        path_count = 0
        for page in range(1, path_first["page_count"] + 1):
            payload = path_first if page == 1 else call(prefix + "/path-downstreams", {**common, "page": page, "page_size": 60})[0]
            path_count += len(payload["items"])
            assert len(payload["items"]) <= 60
            assert all(1 <= len(row["path_samples"]) <= 3 for row in payload["items"])
        assert as_count == as_first["total"] == overview["affected_as_count"]
        assert path_count == path_first["total"] == overview["path_downstream_relation_count"]
        repeated, _, repeated_raw = call(prefix + "/asns", {**common, "page": 1, "page_size": 60})
        assert repeated == as_first and repeated_raw == as_raw
        etag = overview_headers.get("ETag")
        assert etag
        not_modified, _, _ = call(prefix + "/overview", common, {"If-None-Match": etag}, 304)
        assert not_modified is None
        call(prefix + "/overview", {"publication_id": "wrong-publication"}, expected=404)
        call(prefix + "/path-downstreams", {**common, "scope": "invalid"}, expected=400)
        samples[code] = {
            "incident_id": incident, "publication_id": publication,
            "window_start_utc": resolution["window_start_utc"],
            "window_end_utc": resolution["window_end_utc"],
            "is_final_in_data_range": resolution["is_final_in_data_range"],
            "state_points": series["point_count"], "affected_as": as_first["total"],
            "path_relations": path_first["total"], "overview_bytes": len(overview_raw),
            "series_bytes": len(series_raw), "asn_page_bytes": len(as_raw),
            "path_page_bytes": len(path_raw),
            "peak_interrupted_prefixes": overview["peaks"]["interrupted_prefix_count"]["value"],
            "peak_route_interrupted_as": overview["peaks"]["route_interrupted_asn_count"]["value"],
        }
    call("/api/v2/country-outages/not-owned/path-downstreams", expected=503)
    latencies = sorted(row["latency_ms"] for row in records)
    print(json.dumps({
        "status": "pass", "candidate": BASE, "samples": samples,
        "request_count": len(records),
        "maximum_response_bytes": max(row["bytes"] for row in records),
        "maximum_latency_ms": max(latencies),
        "p95_latency_ms": latencies[max(0, int(len(latencies) * 0.95) - 1)],
        "records_sha256": hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }, ensure_ascii=False, separators=(",", ":")))
finally:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(timeout=5)
'''

REMOTE_AUDIT_SCRIPT = r'''
import hashlib
import json
import os
path = "/home/bgpdata/Domeye-Core-dev-data/research-runs/country-outage-general-page-s4-d1f4d11/read-model/S4-AUDIT.json"
assert os.path.isfile(path) and not os.path.islink(path)
audit = json.load(open(path, "r", encoding="utf-8"))
copy = dict(audit)
copy.pop("content_sha256", None)
copy.pop("audit_id", None)
content = hashlib.sha256(json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
assert audit["schema_version"] == "country_outage_generalization_s4_formal_audit/v1"
assert audit["status"] == "complete"
assert audit["content_sha256"] == content
assert audit["audit_id"] == "country_outage_s4_audit_v1_" + content[:32]
print(json.dumps(audit["evidence"], ensure_ascii=False, separators=(",", ":")))
'''


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_remote_script(script: str, timeout: int) -> dict[str, Any]:
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", REMOTE_HOST, "python3", "-"],
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()
        raise RuntimeError(f"远端 S4 审计失败：{detail or '无错误详情'}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"远端 S4 审计输出无效：{error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("远端 S4 审计输出必须是对象")
    return payload


def create_remote_audit() -> dict[str, Any]:
    data_evidence = run_remote_script(REMOTE_DEEP_AUDIT_SCRIPT, 3600)
    http_evidence = run_remote_script(REMOTE_HTTP_AUDIT_SCRIPT, 300)
    if data_evidence.get("status") != "pass" or http_evidence.get("status") != "pass":
        raise RuntimeError("S4 数据深审或隔离 HTTP 候选未通过")
    payload: dict[str, Any] = {
        "schema_version": "country_outage_generalization_s4_formal_audit/v1",
        "status": "complete",
        "evidence": {"data": data_evidence, "http": http_evidence},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    content_sha = hashlib.sha256(canonical).hexdigest()
    payload["content_sha256"] = content_sha
    payload["audit_id"] = "country_outage_s4_audit_v1_" + content_sha[:32]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    encoded = base64.b64encode(raw).decode("ascii")
    writer = f'''
import base64
import os
path = {REMOTE_AUDIT_PATH!r}
raw = base64.b64decode({encoded!r})
if os.path.exists(path):
    if open(path, "rb").read() != raw:
        raise SystemExit("S4-AUDIT.json 已存在且内容不同")
else:
    temporary = path + ".tmp"
    if os.path.exists(temporary):
        raise SystemExit("S4-AUDIT.json 临时文件已存在")
    with open(temporary, "xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)
print("ok")
'''
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", REMOTE_HOST, "python3", "-"],
        input=writer,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stdout + result.stderr).strip() or "无法写入正式 S4 审计")
    return payload


def verify_local() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    checks: list[str] = []
    for path in (ACCEPTANCE_RECORD, READ_MODEL_SCHEMA, OPENAPI_PATH):
        if not path.is_file():
            errors.append(f"缺少 S4 文件：{path.relative_to(REPOSITORY_ROOT)}")
    if errors:
        return errors, checks
    record = read_text(ACCEPTANCE_RECORD)
    for phrase in REQUIRED_RECORD_PHRASES:
        if phrase not in record:
            errors.append(f"S4 验收记录缺少：{phrase}")
    checks.append("S4 验收记录包含正式人口、有界响应、性能、重跑边界和候选边界")
    try:
        schema = json.loads(read_text(READ_MODEL_SCHEMA))
        openapi = json.loads(read_text(OPENAPI_PATH))
    except json.JSONDecodeError as error:
        errors.append(f"S4 合同不是合法 JSON：{error}")
    else:
        if schema.get("additionalProperties") is not False:
            errors.append("S4 读模型合同顶层没有关闭额外字段")
        event = schema.get("$defs", {}).get("event", {})
        if event.get("additionalProperties") is not False:
            errors.append("S4 事件读模型合同没有关闭额外字段")
        path = openapi.get("paths", {}).get(
            "/api/v2/country-outages/{incident_id}/path-downstreams", {}
        ).get("get", {})
        parameters = {item.get("name"): item for item in path.get("parameters", [])}
        if parameters.get("page_size", {}).get("schema", {}).get("maximum") != 60:
            errors.append("S4 路径分页上限漂移")
        item = openapi.get("components", {}).get("schemas", {}).get(
            "CountryOutageGeneralPathDownstreamItemV1", {}
        )
        if item.get("properties", {}).get("path_samples", {}).get("maxItems") != 3:
            errors.append("S4 路径真实样本上限漂移")
    checks.append("读模型和 OpenAPI 冻结同 Publication、最多 60 行与最多 3 个路径样本")
    for phrase in (
        "S5 页面已完成", "S6 生产已切换", "生产已上线", "全量 MRT 已重跑",
        "customer cone", "确认依赖", "全国断网", "根因是", "TODO",
    ):
        if phrase in record:
            errors.append(f"S4 验收记录越级或改变事实：{phrase}")
    checks.append("S4 未越级声明页面、生产、依赖、因果或额外重跑")
    return errors, checks


def validate_remote(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    data = payload.get("data", {})
    http = payload.get("http", {})
    if data.get("status") != "pass" or http.get("status") != "pass":
        return ["S4 冻结数据或 HTTP 证据未通过"]
    read_model = data.get("read_model", {})
    pairs = (
        ("implementation_id", "implementation_id"),
        ("dataset_id", "dataset_id"),
        ("content_sha256", "content_sha256"),
        ("manifest_sha256", "manifest_sha256"),
        ("source_event_cohort_dataset_id", "cohort_dataset_id"),
        ("source_event_cohort_content_sha256", "cohort_content_sha256"),
        ("source_event_cohort_manifest_sha256", "cohort_manifest_sha256"),
        ("source_event_metric_dataset_id", "metric_dataset_id"),
        ("source_event_metric_content_sha256", "metric_content_sha256"),
        ("source_event_metric_manifest_sha256", "metric_manifest_sha256"),
        ("source_event_as_path_dataset_id", "as_path_dataset_id"),
        ("source_event_as_path_content_sha256", "as_path_content_sha256"),
        ("source_event_as_path_manifest_sha256", "as_path_manifest_sha256"),
        ("source_lifecycle_snapshot_id", "lifecycle_snapshot_id"),
        ("events", "events"), ("state_points", "state_points"),
        ("affected_as", "affected_as"), ("relations", "relations"),
        ("path_samples", "path_samples"), ("file_count", "file_count"),
    )
    for actual, expected in pairs:
        if read_model.get(actual) != EXPECTED[expected]:
            errors.append(f"S4 正式身份或人口冲突：{actual}={read_model.get(actual)!r}")
    if data.get("source_path_evidence_rows_checked") != 5_093_251:
        errors.append("S4 没有逐行复核 S3 路径证据")
    if data.get("normal_read_model_contains_full_path_evidence") is not False:
        errors.append("S4 正常读模型混入完整路径证据")
    samples = http.get("samples", {})
    if set(samples) != {"IR", "MW"}:
        errors.append("S4 HTTP 缺少 IR/MW 同合同样本")
    else:
        if samples["IR"].get("state_points") != 3455 or samples["IR"].get("affected_as") != 525:
            errors.append("S4 IR HTTP 人口冲突")
        if samples["MW"].get("state_points") != 57 or samples["MW"].get("affected_as") != 8:
            errors.append("S4 MW HTTP 人口冲突")
    if http.get("maximum_response_bytes", 10**9) > 500_000:
        errors.append("S4 候选存在超过 500 KB 的默认响应")
    if http.get("maximum_latency_ms", 10**9) > 500:
        errors.append("S4 隔离候选请求超过 500 ms 验收线")
    return errors


def verify() -> dict[str, Any]:
    errors, checks = verify_local()
    remote: dict[str, Any] | None = None
    if not errors:
        try:
            remote = run_remote_script(REMOTE_AUDIT_SCRIPT, 30)
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            errors.append(str(error))
        else:
            errors.extend(validate_remote(remote))
            checks.append("81 个事件逐文件、逐趋势、逐 AS、逐路径关系和逐路径样本深审")
            checks.append("IR 与 MW 隔离 HTTP 候选同身份、稳定分页、错误状态、响应大小与时延通过")
    return {
        "schema_version": "country_outage_generalization_s4_verification_v1",
        "status": "pass" if not errors else "fail",
        "stage": "S4",
        "check_count": len(checks),
        "checks": checks,
        "remote_evidence": remote,
        "errors": errors,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create-remote-audit",
        action="store_true",
        help="逐行深审正式制品并启动一次隔离 HTTP 候选，以 create-only 方式冻结结果。",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if arguments.create_remote_audit:
        try:
            payload = create_remote_audit()
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            json.dump({"status": "fail", "error": str(error)}, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return 1
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    payload = verify()
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
