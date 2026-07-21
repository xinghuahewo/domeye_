#!/usr/bin/env python3
"""由 D2 全量候选生成六类 Evidence Bundle v2 合同/调查样本。

本 CLI 只读 D2 规范化候选和 D3 MRT 文件级 manifest。它不连接
数据库，不解析 MRT，不生成 RouteEvent 或 MetricSeries。因此输出只是
六类事件各一个的 ``legacy_compatible`` 合同/调查样本，不代表全量
Evidence 已组装，也不能用于提升原始追溯准入等级。

输出先写同级临时目录，完成 Schema、引用闭合、manifest 和 SHA256SUMS
后才原子改名。目标目录必须不存在，任何失败都清理本进程创建的
临时目录。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
from itertools import zip_longest
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from types import ModuleType
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVENT_TYPES = (
    "hijack",
    "sub_hijack",
    "leak",
    "prefix_outage",
    "as_outage",
    "country_outage",
)
JSONL_FILES = (
    "incidents.jsonl.gz",
    "links.jsonl.gz",
    "collision_groups.jsonl.gz",
    "quarantine.jsonl.gz",
)
D2_FILES = frozenset((*JSONL_FILES, "manifest.json", "摘要.md", "SHA256SUMS"))
EVIDENCE_PACKAGE = Path("backend/data_pipeline/evidence")
SCHEMA_RELATIVE = Path("contracts/data/evidence-bundle-v2.schema.json")
AJV_RELATIVE = Path("frontend/node_modules/@redocly/ajv/dist/2020")
UTC = timezone.utc
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
INCIDENT_ID_RE = re.compile(r"^inc_v1_[0-9a-f]{24}$")
EVIDENCE_ID_RE = re.compile(r"^ev_v2_[0-9a-f]{32}$")
MAX_JSON_BYTES = 256 * 1024 * 1024
RECONCILIATION_FILE = "evidence-reconciliation-summary.json"
MISSING_VALUE_STATES = frozenset(
    {
        "not_observed",
        "not_retained",
        "not_applicable",
        "source_unavailable",
        "processing_gap",
        "parse_failed",
        "legacy_unknown",
        "source_fact_collision",
        "invalid_identity",
        "legacy_window_contamination",
    }
)
VALID_MISSING_REASONS = frozenset(
    {
        "not_observed",
        "not_retained",
        "not_applicable",
        "source_unavailable",
        "parse_failed",
        "legacy_unknown",
        "processing_gap",
        "source_fact_collision",
        "invalid_identity",
        "legacy_window_contamination",
        "source_fact_orphan",
        "quarantined",
    }
)


class EvidenceCandidateError(RuntimeError):
    """候选输入、安全边界或输出不符合 P0 D4 要求。"""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _reject_constant(value: str) -> None:
    raise EvidenceCandidateError("JSON 禁止非有限数值：{}".format(value))


def _unique_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceCandidateError("JSON 对象字段重复：{}".format(key))
        result[key] = value
    return result


def _strict_json_bytes(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EvidenceCandidateError("{} 必须是严格 UTF-8".format(label)) from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise EvidenceCandidateError("{} 不是合法 JSON：{}".format(label, error.msg)) from error


def _lstat_regular(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise EvidenceCandidateError("无法读取{}：{}".format(label, path)) from error
    if stat.S_ISLNK(metadata.st_mode):
        raise EvidenceCandidateError("{}不得是符号链接：{}".format(label, path))
    if not stat.S_ISREG(metadata.st_mode):
        raise EvidenceCandidateError("{}必须是普通文件：{}".format(label, path))
    return metadata


def _lstat_directory(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise EvidenceCandidateError("无法读取{}：{}".format(label, path)) from error
    if stat.S_ISLNK(metadata.st_mode):
        raise EvidenceCandidateError("{}不得是符号链接：{}".format(label, path))
    if not stat.S_ISDIR(metadata.st_mode):
        raise EvidenceCandidateError("{}必须是目录：{}".format(label, path))
    return metadata


@contextmanager
def _open_regular(path: Path, label: str) -> Iterator[Any]:
    initial = _lstat_regular(path, label)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise EvidenceCandidateError("无法只读打开{}：{}".format(label, path)) from error
    stream = None
    try:
        before = os.fstat(descriptor)
        immutable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if not stat.S_ISREG(before.st_mode) or any(
            getattr(initial, field) != getattr(before, field) for field in immutable
        ):
            raise EvidenceCandidateError("打开前{}发生变化：{}".format(label, path))
        stream = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        yield stream
        after = os.fstat(stream.fileno())
        if any(getattr(before, field) != getattr(after, field) for field in immutable):
            raise EvidenceCandidateError("读取期间{}发生变化：{}".format(label, path))
    finally:
        if stream is not None:
            stream.close()
        elif descriptor >= 0:
            os.close(descriptor)


def _sha256_file(path: Path, label: str) -> str:
    digest = hashlib.sha256()
    with _open_regular(path, label) as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _read_regular(path: Path, label: str, *, maximum: int = MAX_JSON_BYTES) -> bytes:
    chunks = []
    total = 0
    with _open_regular(path, label) as stream:
        while True:
            block = stream.read(128 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum:
                raise EvidenceCandidateError("{} 超过 {} 字节限制".format(label, maximum))
            chunks.append(block)
    return b"".join(chunks)


def _load_json(path: Path, label: str) -> Mapping[str, Any]:
    value = _strict_json_bytes(_read_regular(path, label), label)
    if not isinstance(value, Mapping):
        raise EvidenceCandidateError("{} 顶层必须是 JSON 对象".format(label))
    return value


def _parse_sha256sums(path: Path, expected_names: Iterable[str]) -> Dict[str, str]:
    payload = _read_regular(path, "SHA256SUMS", maximum=1024 * 1024)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EvidenceCandidateError("SHA256SUMS 必须是 UTF-8") from error
    result: Dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        matched = re.fullmatch(r"([0-9a-f]{64})  ([^/\\\r\n]+)", line)
        if matched is None:
            raise EvidenceCandidateError("SHA256SUMS 第 {} 行非法".format(line_number))
        digest, name = matched.groups()
        if name in result:
            raise EvidenceCandidateError("SHA256SUMS 文件名重复：{}".format(name))
        result[name] = digest
    expected = set(expected_names)
    if set(result) != expected:
        raise EvidenceCandidateError(
            "SHA256SUMS 闭包不一致；缺少={} 多出={}".format(
                sorted(expected - set(result)), sorted(set(result) - expected)
            )
        )
    return result


def _verify_directory_files(directory: Path, expected: Iterable[str]) -> None:
    expected_names = set(expected)
    actual = set()
    for path in directory.iterdir():
        if path.is_symlink():
            raise EvidenceCandidateError("输入目录禁止符号链接：{}".format(path))
        actual.add(path.name)
    if actual != expected_names:
        raise EvidenceCandidateError(
            "输入目录文件集不一致；缺少={} 多出={}".format(
                sorted(expected_names - actual), sorted(actual - expected_names)
            )
        )


def _verify_checksums(directory: Path, expected_names: Iterable[str]) -> Dict[str, str]:
    checksums = _parse_sha256sums(directory / "SHA256SUMS", expected_names)
    for name, expected in checksums.items():
        actual = _sha256_file(directory / name, name)
        if actual != expected:
            raise EvidenceCandidateError("SHA256 校验失败：{}".format(name))
    return checksums


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise EvidenceCandidateError("{} 必须是 64 位小写 SHA256".format(field))
    return value


def _utc_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or "T" not in value:
        raise EvidenceCandidateError("{} 必须是带时区时间".format(field))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceCandidateError("{} 时间非法".format(field)) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond:
        raise EvidenceCandidateError("{} 必须带时区且精确到秒".format(field))
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_evidence_module(pipeline_root: Path) -> Tuple[ModuleType, Dict[str, str]]:
    root = pipeline_root.absolute()
    _lstat_directory(root, "pipeline-root")
    package = root / EVIDENCE_PACKAGE
    _lstat_directory(package, "Evidence package")
    init_path = package / "__init__.py"
    bundle_path = package / "bundle.py"
    for path in (init_path, bundle_path):
        _lstat_regular(path, "Evidence 模块")
    package_name = "_domeye_p0_evidence_" + hashlib.sha256(
        os.fsencode(package.resolve(strict=True))
    ).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(
        package_name,
        init_path,
        submodule_search_locations=[str(package)],
    )
    if spec is None or spec.loader is None:
        raise EvidenceCandidateError("无法创建 Evidence 模块加载器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(package_name, None)
        raise
    actual = Path(str(module.__file__)).resolve(strict=True)
    if actual != init_path.resolve(strict=True):
        raise EvidenceCandidateError("Evidence 公共 API 不是来自 --pipeline-root")
    for name in (
        "build_evidence_bundle_v2",
        "canonical_evidence_bundle_bytes",
        "validate_reference_closure",
    ):
        if not callable(getattr(module, name, None)):
            raise EvidenceCandidateError("Evidence 公共 API 缺少：{}".format(name))
    return module, {
        str(init_path.relative_to(root)): _sha256_file(init_path, "Evidence __init__"),
        str(bundle_path.relative_to(root)): _sha256_file(bundle_path, "Evidence bundle"),
    }


def _validate_d2_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != "p0_normalization_candidate_v1":
        raise EvidenceCandidateError("D2 manifest schema_version 非法")
    if manifest.get("candidate_kind") != "readonly_legacy_fact_normalization":
        raise EvidenceCandidateError("D2 candidate_kind 非法")
    if manifest.get("classification") != "observation_only" or manifest.get(
        "causal_conclusion"
    ) is not None:
        raise EvidenceCandidateError("D2 观测/因果边界非法")
    _sha(manifest.get("candidate_fingerprint_sha256"), "D2 candidate fingerprint")
    sample = manifest.get("sample")
    if not isinstance(sample, Mapping) or sample.get("enabled") is not False or sample.get(
        "admissible"
    ) is not True:
        raise EvidenceCandidateError("D2 必须是全量可对账候选，禁止使用 max-events 样本")
    admission = manifest.get("admission")
    if (
        not isinstance(admission, Mapping)
        or admission.get("status") != "legacy_candidate_ready"
        or admission.get("eligible_for_release_gate") is not True
        or admission.get("blocking_reasons") != []
        or admission.get("raw_traceable") is not False
    ):
        raise EvidenceCandidateError("D2 全量候选未通过 legacy_candidate_ready 门禁")
    summary = manifest.get("summary")
    if not isinstance(summary, Mapping):
        raise EvidenceCandidateError("D2 summary 缺失")
    for field in (
        "unexplained_reverse_orphan_count",
        "unexplained_forward_reference_count",
    ):
        if summary.get(field) != 0:
            raise EvidenceCandidateError("D2 存在未解释引用：{}".format(field))
    counts = summary.get("event_type_counts")
    if not isinstance(counts, Mapping) or any(
        isinstance(counts.get(event_type), bool)
        or not isinstance(counts.get(event_type), int)
        or counts.get(event_type) < 1
        for event_type in EVENT_TYPES
    ):
        raise EvidenceCandidateError("D2 六类事件计数不完整")
    if summary.get("incident_count") != summary.get("link_count"):
        raise EvidenceCandidateError("D2 Incident/Link 计数不一致")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != set(JSONL_FILES):
        raise EvidenceCandidateError("D2 JSONL inventory 不完整")


def _load_d2_candidate(directory: Path) -> Tuple[Mapping[str, Any], Dict[str, str]]:
    _lstat_directory(directory, "D2 candidate")
    _verify_directory_files(directory, D2_FILES)
    checksums = _verify_checksums(directory, D2_FILES - {"SHA256SUMS"})
    manifest = _load_json(directory / "manifest.json", "D2 manifest")
    _validate_d2_manifest(manifest)
    for name in JSONL_FILES:
        inventory = manifest["files"][name]
        if not isinstance(inventory, Mapping):
            raise EvidenceCandidateError("D2 file inventory 非对象：{}".format(name))
        if inventory.get("name") != name:
            raise EvidenceCandidateError("D2 file inventory name 不一致：{}".format(name))
        if inventory.get("sha256") != checksums[name]:
            raise EvidenceCandidateError("D2 manifest/SHA256SUMS 不一致：{}".format(name))
        if inventory.get("size_bytes") != (directory / name).stat().st_size:
            raise EvidenceCandidateError("D2 file size 不一致：{}".format(name))
        _sha(inventory.get("content_sha256"), name + ".content_sha256")
    return manifest, checksums


def _iter_jsonl(
    path: Path, inventory: Mapping[str, Any], label: str
) -> Iterator[Mapping[str, Any]]:
    row_count = 0
    content_hash = hashlib.sha256()
    try:
        with _open_regular(path, label) as raw:
            with gzip.GzipFile(fileobj=raw, mode="rb") as compressed:
                for line in compressed:
                    row_count += 1
                    content_hash.update(line)
                    if not line.endswith(b"\n") or line == b"\n":
                        raise EvidenceCandidateError("{} 第 {} 行不是规范 JSONL".format(label, row_count))
                    value = _strict_json_bytes(line[:-1], "{} 第 {} 行".format(label, row_count))
                    if not isinstance(value, Mapping):
                        raise EvidenceCandidateError("{} 第 {} 行顶层非对象".format(label, row_count))
                    yield value
    except (OSError, EOFError, gzip.BadGzipFile) as error:
        raise EvidenceCandidateError("{} gzip 解压或读取失败".format(label)) from error
    if row_count != inventory.get("row_count"):
        raise EvidenceCandidateError("{} row_count 不一致".format(label))
    if content_hash.hexdigest() != inventory.get("content_sha256"):
        raise EvidenceCandidateError("{} content_sha256 不一致".format(label))


def _boundary(record: Mapping[str, Any], label: str) -> None:
    if record.get("classification") != "observation_only" or record.get(
        "causal_conclusion"
    ) is not None:
        raise EvidenceCandidateError("{} 违反 observation_only/因果边界".format(label))


def _select_six(
    directory: Path, manifest: Mapping[str, Any]
) -> Dict[str, Mapping[str, Any]]:
    selected: Dict[str, Mapping[str, Any]] = {}
    incident_stream = _iter_jsonl(
        directory / "incidents.jsonl.gz",
        manifest["files"]["incidents.jsonl.gz"],
        "D2 incidents",
    )
    link_stream = _iter_jsonl(
        directory / "links.jsonl.gz",
        manifest["files"]["links.jsonl.gz"],
        "D2 links",
    )
    sentinel = object()
    for ordinal, pair in enumerate(zip_longest(incident_stream, link_stream, fillvalue=sentinel), 1):
        incident, link = pair
        if incident is sentinel or link is sentinel:
            raise EvidenceCandidateError("D2 Incident/Link 流长度不一致")
        assert isinstance(incident, Mapping) and isinstance(link, Mapping)
        _boundary(incident, "D2 Incident #{}".format(ordinal))
        _boundary(link, "D2 Link #{}".format(ordinal))
        if incident.get("incident_id") != link.get("incident_id"):
            raise EvidenceCandidateError("D2 Incident/Link 顺序或 ID 不一致")
        event_type = incident.get("event_type")
        if event_type not in EVENT_TYPES or event_type != link.get("event_type"):
            raise EvidenceCandidateError("D2 Incident/Link event_type 非法")
        if event_type in selected:
            continue
        safe = (
            incident.get("fact_link_status") == "matched"
            and link.get("status") == "matched"
            and incident.get("collision_group_id") is None
            and link.get("collision_group_id") is None
            and incident.get("quarantine_id") is None
            and isinstance(incident.get("source_primary_key"), Mapping)
            and incident.get("source_primary_key") == link.get("matched_source_primary_key")
            and incident.get("source_table") == link.get("source_table")
        )
        if safe:
            selected[event_type] = incident
    for name in ("collision_groups.jsonl.gz", "quarantine.jsonl.gz"):
        for ordinal, record in enumerate(
            _iter_jsonl(directory / name, manifest["files"][name], "D2 " + name), 1
        ):
            _boundary(record, "D2 {} #{}".format(name, ordinal))
    missing = [event_type for event_type in EVENT_TYPES if event_type not in selected]
    if missing:
        raise EvidenceCandidateError("任一类型无安全 matched 非碰撞样本，失败关闭：{}".format(",".join(missing)))
    return selected


def _load_artifact_manifest(path: Path, d2: Mapping[str, Any]) -> Dict[str, Any]:
    directory = path.parent
    _lstat_directory(directory, "D3 artifact manifest 目录")
    summary_path = path.with_name(path.stem + ".summary.zh.json")
    expected = {path.name, summary_path.name, "SHA256SUMS"}
    _verify_directory_files(directory, expected)
    checksums = _verify_checksums(directory, expected - {"SHA256SUMS"})
    manifest = _load_json(path, "D3 artifact manifest")
    summary = _load_json(summary_path, "D3 artifact summary")
    if manifest.get("schema_version") != 1 or manifest.get("manifest_kind") != "mrt_artifact_manifest":
        raise EvidenceCandidateError("D3 artifact manifest 版本或类型非法")
    scan_policy = manifest.get("scan_policy")
    if (
        not isinstance(scan_policy, Mapping)
        or scan_policy.get("compression_envelope_validation")
        != "full_stream_to_eof_crc_or_equivalent"
    ):
        raise EvidenceCandidateError("D3 未冻结压缩流 EOF/CRC 完整性准入策略")
    payload = dict(manifest)
    fingerprint = payload.pop("manifest_fingerprint_sha256", None)
    _sha(fingerprint, "D3 manifest fingerprint")
    expected_fingerprint = _canonical_sha256(
        {"schema": "mrt_artifact_manifest_fingerprint_v1", "manifest": payload}
    )
    if fingerprint != expected_fingerprint:
        raise EvidenceCandidateError("D3 artifact manifest fingerprint 校验失败")
    if summary.get("manifest", {}).get("sha256") != checksums[path.name]:
        raise EvidenceCandidateError("D3 summary/manifest SHA256 不一致")
    if summary.get("manifest", {}).get("fingerprint_sha256") != fingerprint:
        raise EvidenceCandidateError("D3 summary/manifest fingerprint 不一致")
    verification = summary.get("verification")
    if (
        not isinstance(verification, Mapping)
        or verification.get("verified") is not True
        or verification.get("manifest_fingerprint_sha256") != fingerprint
    ):
        raise EvidenceCandidateError("D3 artifact manifest 没有 verify_artifact_manifest 成功证据")
    d2_profile = d2.get("data_profile")
    d3_profile = manifest.get("data_profile")
    if not isinstance(d2_profile, Mapping) or not isinstance(d3_profile, Mapping):
        raise EvidenceCandidateError("D2/D3 data_profile 缺失")
    for field in ("id", "timezone", "window_start", "window_end_exclusive"):
        if d2_profile.get(field) != d3_profile.get(field):
            raise EvidenceCandidateError("D2/D3 data_profile 不一致：{}".format(field))
    provenance = d2.get("source", {}).get("provenance", {})
    profile_sha = provenance.get("data_profile_sha256")
    if summary.get("provenance", {}).get("data_profile", {}).get("sha256") != profile_sha:
        raise EvidenceCandidateError("D2/D3 data-profile SHA256 不一致")

    coverage = manifest.get("coverage")
    if not isinstance(coverage, Mapping):
        raise EvidenceCandidateError("D3 coverage 缺失")
    update_expected = 0
    update_observed = 0
    by_collector = coverage.get("by_collector")
    if not isinstance(by_collector, list) or not by_collector:
        raise EvidenceCandidateError("D3 collector coverage 缺失")
    for row in by_collector:
        update = row.get("by_artifact_type", {}).get("update") if isinstance(row, Mapping) else None
        if not isinstance(update, Mapping):
            raise EvidenceCandidateError("D3 update coverage 缺失")
        expected_count = update.get("expected_slots")
        observed_count = update.get("available_slots")
        if (
            isinstance(expected_count, bool)
            or not isinstance(expected_count, int)
            or expected_count < 0
            or isinstance(observed_count, bool)
            or not isinstance(observed_count, int)
            or not 0 <= observed_count <= expected_count
        ):
            raise EvidenceCandidateError("D3 update coverage 计数非法")
        update_expected += expected_count
        update_observed += observed_count
    if update_expected < 1:
        raise EvidenceCandidateError("D3 update expected_count 不得为 0")
    raw_status = (
        "unavailable"
        if update_observed == 0
        else ("full" if update_observed == update_expected else "partial")
    )
    return {
        "manifest": manifest,
        "manifest_sha256": checksums[path.name],
        "summary_sha256": checksums[summary_path.name],
        "raw_source_status": raw_status,
        "raw_source_coverage": {
            "expected_count": update_expected,
            "observed_count": update_observed,
        },
        "source_hash_verification_status": (
            "not_available"
            if raw_status == "unavailable"
            else ("verified" if raw_status == "full" else "partial")
        ),
    }


def _schema_validate(
    payloads: Sequence[Mapping[str, Any]], schema_path: Path, ajv_module: Path
) -> str:
    _lstat_regular(schema_path, "Evidence Bundle v2 Schema")
    schema_sha = _sha256_file(schema_path, "Evidence Bundle v2 Schema")
    resolved_ajv_module = ajv_module
    if not ajv_module.exists() and not ajv_module.is_symlink():
        resolved_ajv_module = Path("{}.js".format(ajv_module))
    try:
        module_metadata = resolved_ajv_module.lstat()
    except OSError as error:
        raise EvidenceCandidateError(
            "AJV 2020 模块路径非法：{}".format(ajv_module)
        ) from error
    if stat.S_ISLNK(module_metadata.st_mode) or not (
        stat.S_ISREG(module_metadata.st_mode) or stat.S_ISDIR(module_metadata.st_mode)
    ):
        raise EvidenceCandidateError("AJV 2020 模块路径非法：{}".format(ajv_module))
    script = r"""
const fs = require('fs')
const Ajv2020 = require(process.argv[1]).default
const schema = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const ajv = new Ajv2020({allErrors: true, allowUnionTypes: true, strict: true, validateFormats: true})
ajv.addFormat('date-time', {
  type: 'string',
  validate: (value) => {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) return false
    const timestamp = Date.parse(value)
    return Number.isFinite(timestamp) && new Date(timestamp).toISOString().replace('.000Z', 'Z') === value
  },
})
const validate = ajv.compile(schema)
for (const payload of JSON.parse(fs.readFileSync(0, 'utf8'))) {
  if (!validate(payload)) {
    process.stderr.write(ajv.errorsText(validate.errors, {separator: '; '}))
    process.exit(1)
  }
}
"""
    try:
        result = subprocess.run(
            ["node", "-e", script, str(resolved_ajv_module), str(schema_path)],
            input=json.dumps(payloads, ensure_ascii=False, allow_nan=False),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise EvidenceCandidateError("无法执行 Node/AJV 严格 Schema 校验") from error
    if result.returncode != 0:
        raise EvidenceCandidateError("Evidence Bundle v2 严格 Schema 校验失败：{}".format(result.stderr))
    return schema_sha


def _pointer_value(value: Any, pointer: Any) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return None
    current = value
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                return None
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return None
    return current


def _inside_window(value: Any, start: datetime, end: datetime, *, allow_end: bool = False) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if observed.tzinfo is None or observed.utcoffset() is None:
        return False
    observed = observed.astimezone(UTC)
    return start <= observed <= end if allow_end else start <= observed < end


def _bundle_reconciliation_counts(bundle: Mapping[str, Any]) -> Dict[str, int]:
    """逐 Bundle 复算 D5 所需计数；显式 legacy_unknown 不算无原因。"""

    registry = bundle.get("evidence_registry")
    registry = registry if isinstance(registry, list) else []
    evidence_ids = []
    for item in registry:
        if isinstance(item, Mapping) and isinstance(item.get("evidence_id"), str):
            evidence_ids.append(item["evidence_id"])
    evidence_id_set = set(evidence_ids)
    referenced_evidence_ids = set()
    for field in ("supporting_evidence_refs", "counterevidence_refs"):
        values = bundle.get(field)
        if isinstance(values, list):
            referenced_evidence_ids.update(value for value in values if isinstance(value, str))
    phase_coverage = bundle.get("phase_coverage")
    phase_coverage = phase_coverage if isinstance(phase_coverage, Mapping) else {}
    referenced_route_ids = set()
    unknown_missing_reason_count = 0
    legacy_unknown_value_count = 0
    for phase in phase_coverage.values():
        if not isinstance(phase, Mapping):
            continue
        referenced_evidence_ids.update(
            value for value in phase.get("evidence_ids", []) if isinstance(value, str)
        )
        referenced_route_ids.update(
            value for value in phase.get("route_event_ref_ids", []) if isinstance(value, str)
        )
        reasons = phase.get("missing_reasons")
        valid_reasons = (
            [reason for reason in reasons if reason in VALID_MISSING_REASONS]
            if isinstance(reasons, list)
            else []
        )
        if phase.get("status") in {"not_available", "compromised"} and not valid_reasons:
            unknown_missing_reason_count += 1
        if "legacy_unknown" in valid_reasons:
            legacy_unknown_value_count += 1
    limitations = bundle.get("limitations")
    if isinstance(limitations, list):
        for limitation in limitations:
            if isinstance(limitation, Mapping):
                referenced_evidence_ids.update(
                    value
                    for value in limitation.get("evidence_refs", [])
                    if isinstance(value, str)
                )

    auto_zero_fill_count = 0
    field_quality = bundle.get("field_quality")
    field_quality = field_quality if isinstance(field_quality, list) else []
    for item in field_quality:
        if not isinstance(item, Mapping):
            continue
        state = item.get("value_state")
        reason = item.get("missing_reason")
        if state in MISSING_VALUE_STATES:
            if reason not in VALID_MISSING_REASONS:
                unknown_missing_reason_count += 1
            if reason == "legacy_unknown" or state == "legacy_unknown":
                legacy_unknown_value_count += 1
            actual = _pointer_value(bundle, item.get("field_path"))
            if isinstance(actual, (int, float)) and not isinstance(actual, bool) and actual == 0:
                auto_zero_fill_count += 1

    missing_counts = bundle.get("coverage_summary", {}).get("missing_counts", [])
    aggregate_legacy_unknown = 0
    if isinstance(missing_counts, list):
        for item in missing_counts:
            if (
                isinstance(item, Mapping)
                and item.get("reason") == "legacy_unknown"
                and isinstance(item.get("count"), int)
                and not isinstance(item.get("count"), bool)
            ):
                aggregate_legacy_unknown += item["count"]
    legacy_unknown_value_count = max(legacy_unknown_value_count, aggregate_legacy_unknown)
    declared_unknown = bundle.get("coverage_summary", {}).get(
        "unknown_missing_reason_count"
    )
    if isinstance(declared_unknown, int) and not isinstance(declared_unknown, bool):
        unknown_missing_reason_count = max(unknown_missing_reason_count, declared_unknown)

    route_refs = bundle.get("route_event_refs")
    route_refs = route_refs if isinstance(route_refs, list) else []
    route_ids = {
        item.get("route_event_id")
        for item in route_refs
        if isinstance(item, Mapping) and isinstance(item.get("route_event_id"), str)
    }
    snapshot = bundle.get("data_snapshot")
    if not isinstance(snapshot, Mapping):
        raise EvidenceCandidateError("Evidence Bundle 缺少 data_snapshot")
    start = datetime.fromisoformat(
        str(snapshot.get("window_start")).replace("Z", "+00:00")
    ).astimezone(UTC)
    end = datetime.fromisoformat(
        str(snapshot.get("window_end_exclusive")).replace("Z", "+00:00")
    ).astimezone(UTC)
    outside_window_record_count = 0
    incident = bundle.get("incident")
    if isinstance(incident, Mapping) and (
        not _inside_window(incident.get("start_time"), start, end)
        or not _inside_window(incident.get("end_time"), start, end, allow_end=True)
    ):
        outside_window_record_count += 1
    source_facts = bundle.get("source_fact_mapping", {}).get("source_facts", [])
    if isinstance(source_facts, list):
        for item in source_facts:
            if isinstance(item, Mapping) and (
                not _inside_window(item.get("start_time"), start, end)
                or not _inside_window(item.get("end_time"), start, end, allow_end=True)
            ):
                outside_window_record_count += 1
    for item in route_refs:
        if isinstance(item, Mapping) and not _inside_window(
            item.get("observed_at"), start, end
        ):
            outside_window_record_count += 1
    for item in registry:
        if isinstance(item, Mapping) and not _inside_window(item.get("observed_at"), start, end):
            outside_window_record_count += 1
    metric_windows = bundle.get("metric_windows")
    if isinstance(metric_windows, list):
        for item in metric_windows:
            if isinstance(item, Mapping) and (
                not _inside_window(item.get("window_start"), start, end)
                or not _inside_window(
                    item.get("window_end_exclusive"), start, end, allow_end=True
                )
            ):
                outside_window_record_count += 1
    return {
        "classification_violation_count": int(
            bundle.get("conclusion", {}).get("classification") != "observation_only"
        ),
        "causal_conclusion_nonnull_count": int(
            bundle.get("conclusion", {}).get("causal_conclusion") is not None
        ),
        "evidence_id_conflict_count": len(evidence_ids) - len(evidence_id_set),
        "unresolved_evidence_reference_count": len(
            referenced_evidence_ids - evidence_id_set
        ),
        "unresolved_route_event_reference_count": len(referenced_route_ids - route_ids),
        "outside_window_record_count": outside_window_record_count,
        "unknown_missing_reason_count": unknown_missing_reason_count,
        "legacy_unknown_value_count": legacy_unknown_value_count,
        "auto_zero_fill_count": auto_zero_fill_count,
    }


def _build_reconciliation_summary(
    bundles: Sequence[Mapping[str, Any]],
    evidence: ModuleType,
    schema_path: Path,
    ajv_module: Path,
) -> Tuple[Mapping[str, Any], str]:
    totals = {
        "schema_invalid_count": 0,
        "classification_violation_count": 0,
        "causal_conclusion_nonnull_count": 0,
        "evidence_id_conflict_count": 0,
        "unresolved_evidence_reference_count": 0,
        "unresolved_route_event_reference_count": 0,
        "outside_window_record_count": 0,
        "unknown_missing_reason_count": 0,
        "legacy_unknown_value_count": 0,
        "auto_zero_fill_count": 0,
    }
    global_evidence_ids = set()
    schema_sha = None
    event_types = set()
    bundle_ids = []
    for bundle in bundles:
        evidence.validate_reference_closure(bundle)
        current_schema_sha = _schema_validate([bundle], schema_path, ajv_module)
        if schema_sha is None:
            schema_sha = current_schema_sha
        elif schema_sha != current_schema_sha:
            raise EvidenceCandidateError("逐 Bundle Schema SHA256 不一致")
        counts = _bundle_reconciliation_counts(bundle)
        for field, value in counts.items():
            totals[field] += value
        for item in bundle["evidence_registry"]:
            evidence_id = item["evidence_id"]
            if evidence_id in global_evidence_ids:
                totals["evidence_id_conflict_count"] += 1
            global_evidence_ids.add(evidence_id)
        event_types.add(bundle["incident"]["event_type"])
        bundle_ids.append(bundle["bundle_id"])
    assert schema_sha is not None
    blocking_counts = {
        field: totals[field]
        for field in sorted(totals)
        if field != "legacy_unknown_value_count" and totals[field]
    }
    if blocking_counts:
        raise EvidenceCandidateError(
            "Evidence 对账发现阻断计数，候选失败关闭："
            + _canonical_json(blocking_counts)
        )
    payload = {
        "schema_version": "evidence_reconciliation_v1",
        "scope": "six_event_contract_investigation_sample",
        "sample_only": True,
        "population_coverage_claimed": False,
        "bundle_count": len(bundles),
        "event_type_count": len(event_types),
        "event_types": sorted(event_types),
        "bundle_ids": sorted(bundle_ids),
        "strict_schema_status": "passed",
        "schema_sha256": schema_sha,
        "reference_closure_status": "passed",
        **totals,
        "classification": "observation_only",
        "causal_conclusion": None,
    }
    summary = dict(payload)
    summary["summary_fingerprint_sha256"] = _canonical_sha256(
        {"schema": "evidence_reconciliation_fingerprint_v1", "summary": payload}
    )
    return summary, schema_sha


def _prepare_output(output_dir: Path) -> Tuple[Path, Path]:
    target = output_dir.absolute()
    if target.exists() or target.is_symlink():
        raise EvidenceCandidateError("输出目录必须新建，拒绝覆盖：{}".format(target))
    parent = target.parent
    _lstat_directory(parent, "输出父目录")
    staging = parent / ".{}.tmp.{}".format(target.name, os.getpid())
    if staging.exists() or staging.is_symlink():
        raise EvidenceCandidateError("临时目录已存在：{}".format(staging))
    staging.mkdir(mode=0o750)
    return target, staging


def _cleanup(staging: Path) -> None:
    if staging.exists() and staging.is_dir() and not staging.is_symlink() and ".tmp." in staging.name:
        shutil.rmtree(staging)


def _write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _write_new(path, (_canonical_json(payload) + "\n").encode("utf-8"))


def _file_inventory(path: Path) -> Dict[str, Any]:
    return {
        "name": path.name,
        "media_type": "application/json",
        "sha256": _sha256_file(path, path.name),
        "size_bytes": path.stat().st_size,
    }


def _summary_markdown(manifest: Mapping[str, Any]) -> str:
    selections = manifest["selection"]
    lines = [
        "# P0 D4 Evidence Bundle v2 六类候选样本摘要",
        "",
        "## 候选身份",
        "",
        "- 候选指纹：`{}`".format(manifest["candidate_fingerprint_sha256"]),
        "- D2 候选：`{}`".format(manifest["inputs"]["d2"]["candidate_fingerprint_sha256"]),
        "- D3 MRT manifest：`{}`".format(manifest["inputs"]["d3_artifacts"]["manifest_fingerprint_sha256"]),
        "- 数据档：`{}`".format(manifest["data_profile"]["id"]),
        "- 样本数：6",
        "- 准入语义：`sample_only_not_full_population`",
        "",
        "## 六类选样",
        "",
    ]
    for event_type in EVENT_TYPES:
        item = selections[event_type]
        lines.append(
            "- `{}`：`{}` → `{}`".format(event_type, item["incident_id"], item["bundle_file"])
        )
    lines.extend(
        [
            "",
            "## 证据边界",
            "",
            "本制品只用于六类 Evidence Bundle v2 合同、注册表和调查交互样本。",
            "它不是全量 Evidence 组装结果，不能作为 D4/D5/D6 全量发布通过证据。",
            "D3 本轮没有提供可按 Incident 解析的 RouteEvent，也没有 MetricSeries 输入；",
            "因此六个 Bundle 都诚实保持 `legacy_compatible`，RouteEvent/raw/metric 引用为空。",
            "缺失阶段保持 `not_available + missing_reason`，没有补 0；",
            "`classification=observation_only`，`causal_conclusion=null`。",
            "机器对账文件 `evidence-reconciliation-summary.json` 只覆盖这六个样本；",
            "`sample_only=true` 且 `population_coverage_claimed=false`，不得解释为全量。",
            "",
        ]
    )
    return "\n".join(lines)


def build_candidate(
    *,
    d2_candidate: Path,
    d3_artifact_manifest: Path,
    output_dir: Path,
    pipeline_root: Path,
    schema_path: Path,
    ajv_module: Path,
    generated_at: str,
) -> Mapping[str, Any]:
    """生成六类候选样本；所有输入必须已闭包且 D2 必须为全量状态。"""

    generated = _utc_text(generated_at, "generated_at")
    d2_manifest, d2_checksums = _load_d2_candidate(d2_candidate)
    artifact = _load_artifact_manifest(d3_artifact_manifest, d2_manifest)
    selected = _select_six(d2_candidate, d2_manifest)
    evidence, evidence_hashes = _load_evidence_module(pipeline_root)
    schema_sha = _sha256_file(schema_path, "Evidence Bundle v2 Schema")
    profile = d2_manifest["data_profile"]
    source = d2_manifest["source"]
    provenance = source.get("provenance")
    if not isinstance(provenance, Mapping):
        raise EvidenceCandidateError("D2 provenance 缺失")
    profile_sha = _sha(provenance.get("data_profile_sha256"), "data_profile_sha256")
    normalizer_hashes = source.get("normalizer_hashes")
    if not isinstance(normalizer_hashes, Mapping) or not normalizer_hashes:
        raise EvidenceCandidateError("D2 normalizer_hashes 缺失")
    for name, digest in normalizer_hashes.items():
        if not isinstance(name, str) or not name:
            raise EvidenceCandidateError("D2 normalizer 路径非法")
        _sha(digest, "D2 normalizer hash")
    normalizer_code_sha = _canonical_sha256(normalizer_hashes)
    bundle_code_sha = evidence_hashes[str(EVIDENCE_PACKAGE / "bundle.py")]
    data_snapshot = {
        "profile_id": profile["id"],
        "profile_sha256": profile_sha,
        "window_start": d2_manifest["window_utc"]["start"],
        "window_end_exclusive": d2_manifest["window_utc"]["end_exclusive"],
        "snapshot_time": profile["snapshot_time"],
        "business_timezone": profile["timezone"],
        "database_release_id": source["release_id"],
        "overlay_inventory_sha256": _sha(source["inventory_sha256"], "D2 inventory_sha256"),
        "raw_source_status": artifact["raw_source_status"],
    }
    lineage = {
        "parser": None,
        "importer": None,
        "detector": None,
        "normalizer": {
            "name": "p0-incident-normalizer",
            "version": "1.0.0",
            "code_sha256": normalizer_code_sha,
            "config_sha256": profile_sha,
        },
        "bundle_generator": {
            "name": "p0-evidence-bundle-generator",
            "version": "2.0.0",
            "code_sha256": bundle_code_sha,
            "config_sha256": schema_sha,
        },
        "import_run_id": None,
    }
    input_snapshot_sha = d2_checksums["manifest.json"]
    target, staging = _prepare_output(output_dir)
    completed = False
    try:
        bundles = []
        selections: Dict[str, Any] = {}
        files: Dict[str, Any] = {}
        registry_entries: Dict[str, Any] = {}
        for index, event_type in enumerate(EVENT_TYPES, 1):
            incident = selected[event_type]
            bundle = evidence.build_evidence_bundle_v2(
                incident,
                data_snapshot=data_snapshot,
                processing_lineage=lineage,
                raw_source_coverage=artifact["raw_source_coverage"],
                generated_at=generated,
                input_snapshot_sha256=input_snapshot_sha,
                query_fingerprint_sha256=d2_manifest["candidate_fingerprint_sha256"],
                source_hash_verification_status=artifact["source_hash_verification_status"],
                route_event_refs=(),
                raw_record_refs=(),
                route_event_records=(),
                metric_series=(),
                source_fact_record_hash=None,
                reproducibility_parameters={
                    "incident_id": incident["incident_id"],
                    "event_type": event_type,
                    "d2_candidate_fingerprint_sha256": d2_manifest[
                        "candidate_fingerprint_sha256"
                    ],
                    "sample_scope": "first_safe_matched_non_collision_per_event_type_v1",
                },
            )
            evidence.validate_reference_closure(bundle)
            if (
                bundle["coverage_summary"]["admission_level"] != "legacy_compatible"
                or bundle["route_event_refs"]
                or bundle["raw_record_refs"]
                or bundle["metric_windows"]
                or bundle["conclusion"]["classification"] != "observation_only"
                or bundle["conclusion"]["causal_conclusion"] is not None
            ):
                raise EvidenceCandidateError("{} Bundle 超出 legacy 样本边界".format(event_type))
            filename = "bundle-{:02d}-{}-{}.json".format(index, event_type, incident["incident_id"])
            bundle_path = staging / filename
            payload = evidence.canonical_evidence_bundle_bytes(bundle) + b"\n"
            _write_new(bundle_path, payload)
            files[filename] = _file_inventory(bundle_path)
            selections[event_type] = {
                "incident_id": incident["incident_id"],
                "source_table": incident["source_table"],
                "source_primary_key": incident["source_primary_key"],
                "bundle_id": bundle["bundle_id"],
                "bundle_file": filename,
                "fact_link_status": incident["fact_link_status"],
                "source_fact_record_hash": None,
                "selection_rule": "first_safe_matched_non_collision_per_event_type_v1",
            }
            for item in bundle["evidence_registry"]:
                evidence_id = item["evidence_id"]
                if not isinstance(evidence_id, str) or not EVIDENCE_ID_RE.fullmatch(evidence_id):
                    raise EvidenceCandidateError("Evidence ID 非法")
                if evidence_id in registry_entries:
                    raise EvidenceCandidateError("Evidence ID 跨 Bundle 冲突：{}".format(evidence_id))
                registry_entries[evidence_id] = {
                    "bundle_id": bundle["bundle_id"],
                    "bundle_file": filename,
                    "registry_item": item,
                }
            bundles.append(bundle)

        reconciliation, validated_schema_sha = _build_reconciliation_summary(
            bundles,
            evidence,
            schema_path,
            ajv_module,
        )
        if validated_schema_sha != schema_sha:
            raise EvidenceCandidateError("Schema 在候选生成期间发生变化")
        registry = {
            "schema_version": "p0_evidence_registry_index_v1",
            "candidate_scope": "six_event_contract_investigation_sample",
            "entry_count": len(registry_entries),
            "entries": {key: registry_entries[key] for key in sorted(registry_entries)},
            "classification": "observation_only",
            "causal_conclusion": None,
        }
        registry_path = staging / "evidence-registry.json"
        _write_json(registry_path, registry)
        files[registry_path.name] = _file_inventory(registry_path)
        reconciliation_path = staging / RECONCILIATION_FILE
        _write_json(reconciliation_path, reconciliation)
        files[reconciliation_path.name] = _file_inventory(reconciliation_path)

        fingerprint_payload = {
            "schema_version": "p0_evidence_candidate_v1",
            "candidate_kind": "six_event_contract_investigation_sample",
            "data_profile": profile,
            "generated_at": generated,
            "inputs": {
                "d2_manifest_sha256": d2_checksums["manifest.json"],
                "d2_candidate_fingerprint_sha256": d2_manifest[
                    "candidate_fingerprint_sha256"
                ],
                "d3_artifact_manifest_sha256": artifact["manifest_sha256"],
                "d3_artifact_fingerprint_sha256": artifact["manifest"][
                    "manifest_fingerprint_sha256"
                ],
            },
            "generator": {
                "runner_sha256": _sha256_file(Path(__file__), "D4 candidate runner"),
                "evidence_module_hashes": evidence_hashes,
                "schema_sha256": schema_sha,
            },
            "selection": selections,
            "files": files,
            "registry_entry_count": len(registry_entries),
            "classification": "observation_only",
            "causal_conclusion": None,
        }
        fingerprint = _canonical_sha256(fingerprint_payload)
        manifest = {
            "schema_version": "p0_evidence_candidate_v1",
            "candidate_kind": "six_event_contract_investigation_sample",
            "candidate_fingerprint_sha256": fingerprint,
            "data_profile": profile,
            "generated_at": generated,
            "inputs": {
                "d2": {
                    "manifest_sha256": d2_checksums["manifest.json"],
                    "candidate_fingerprint_sha256": d2_manifest[
                        "candidate_fingerprint_sha256"
                    ],
                    "admission_status": d2_manifest["admission"]["status"],
                    "sample_enabled": False,
                    "sha256_closure": "passed",
                    "content_hash_closure": "passed",
                },
                "d3_artifacts": {
                    "manifest_sha256": artifact["manifest_sha256"],
                    "summary_sha256": artifact["summary_sha256"],
                    "manifest_fingerprint_sha256": artifact["manifest"][
                        "manifest_fingerprint_sha256"
                    ],
                    "raw_source_status": artifact["raw_source_status"],
                    "update_coverage": artifact["raw_source_coverage"],
                    "sha256_closure": "passed",
                    "verification_status": "verified",
                },
                "route_event_index": {
                    "status": "not_provided",
                    "missing_reason": "route_event_index_not_available_for_candidate",
                },
                "metric_series": {
                    "status": "not_provided",
                    "missing_reason": "metric_series_not_available_for_candidate",
                },
            },
            "generator": fingerprint_payload["generator"],
            "selection": selections,
            "files": files,
            "registry": {
                "file": registry_path.name,
                "entry_count": len(registry_entries),
                "evidence_id_conflict_count": 0,
                "unresolved_evidence_reference_count": 0,
                "unresolved_route_event_reference_count": 0,
                "reference_closure_ratio": 1,
            },
            "reconciliation": {
                "file": reconciliation_path.name,
                "schema_version": reconciliation["schema_version"],
                "scope": reconciliation["scope"],
                "sample_only": True,
                "population_coverage_claimed": False,
                "summary_fingerprint_sha256": reconciliation[
                    "summary_fingerprint_sha256"
                ],
            },
            "validation": {
                "strict_schema_status": "passed",
                "schema_sha256": schema_sha,
                "bundle_count": len(bundles),
                "event_type_count": len(selections),
                "classification_violation_count": 0,
                "causal_conclusion_nonnull_count": 0,
                "auto_zero_fill_count": 0,
            },
            "admission": {
                "status": "sample_only_not_full_population",
                "represents_full_evidence_population": False,
                "eligible_for_release_gate": False,
                "raw_traceable": False,
                "blocking_reasons": [
                    "six_event_sample_not_full_evidence_population",
                    "route_event_index_not_provided",
                    "metric_series_not_provided",
                ],
            },
            "classification": "observation_only",
            "causal_conclusion": None,
        }
        manifest_path = staging / "manifest.json"
        _write_json(manifest_path, manifest)
        summary_path = staging / "摘要.md"
        _write_new(summary_path, _summary_markdown(manifest).encode("utf-8"))
        output_names = sorted((*files.keys(), manifest_path.name, summary_path.name))
        checksum_lines = [
            "{}  {}".format(_sha256_file(staging / name, name), name)
            for name in output_names
        ]
        _write_new(
            staging / "SHA256SUMS", ("\n".join(checksum_lines) + "\n").encode("utf-8")
        )
        for path in staging.iterdir():
            path.chmod(0o440)
        if target.exists() or target.is_symlink():
            raise EvidenceCandidateError("发布前输出目录已出现，拒绝覆盖")
        staging.rename(target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        completed = True
        return manifest
    finally:
        if not completed:
            _cleanup(staging)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 P0 D4 Evidence Bundle v2 六类候选样本")
    parser.add_argument("--d2-candidate", required=True, help="D2 全量规范化候选目录")
    parser.add_argument("--d3-artifact-manifest", required=True, help="D3 MRT 文件级 manifest JSON")
    parser.add_argument("--output-dir", required=True, help="必须不存在的新输出目录")
    parser.add_argument("--generated-at", required=True, help="确定性 UTC 候选生成时间")
    parser.add_argument(
        "--pipeline-root",
        default=str(PROJECT_ROOT),
        help="包含 backend/data_pipeline/evidence 的 staging 根",
    )
    parser.add_argument(
        "--schema",
        default=str(PROJECT_ROOT / SCHEMA_RELATIVE),
        help="Evidence Bundle v2 JSON Schema",
    )
    parser.add_argument(
        "--ajv-module",
        default=str(PROJECT_ROOT / AJV_RELATIVE),
        help="AJV 2020 Node 模块路径",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = build_candidate(
            d2_candidate=Path(args.d2_candidate),
            d3_artifact_manifest=Path(args.d3_artifact_manifest),
            output_dir=Path(args.output_dir),
            pipeline_root=Path(args.pipeline_root),
            schema_path=Path(args.schema),
            ajv_module=Path(args.ajv_module),
            generated_at=args.generated_at,
        )
    except Exception as error:
        print("错误：{}".format(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "状态": "通过",
                "candidate_fingerprint_sha256": manifest[
                    "candidate_fingerprint_sha256"
                ],
                "bundle_count": manifest["validation"]["bundle_count"],
                "registry_entry_count": manifest["registry"]["entry_count"],
                "admission": manifest["admission"]["status"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
