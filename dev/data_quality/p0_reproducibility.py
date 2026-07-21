#!/usr/bin/env python3
"""生成 P0 候选制品 A/B 重跑的可复现性摘要。

本程序只读取已经落盘的 D2、D3、D4 与 Metric 候选目录，可选读取
RouteEvent 索引目录。每个输入目录都必须是由 ``SHA256SUMS`` 覆盖全部其余
普通文件的扁平、无链接闭包。程序不连接来源数据库、不读取原始 MRT、不
修改输入；稳定 ID 仅写入输出 staging 内、发布前删除的临时 SQLite。任何
身份、哈希、清单、计数或 TOCTOU 校验不足都会失败关闭。

相同输入产生不同输出是质量结论，不是输入损坏：程序会如实输出 ``false``
供 D5 门禁拒绝。只有 A/B 并非同一输入，或任一候选本身不能被验证时，才
拒绝生成摘要。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import sys
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote


SCHEMA_VERSION = "p0_reproducibility_summary_v2"
OUTPUT_JSON = "reproducibility-summary.json"
OUTPUT_SUMMARY = "摘要.md"
OUTPUT_CHECKSUMS = "SHA256SUMS"

D2_FILES = frozenset(
    {
        "incidents.jsonl.gz",
        "links.jsonl.gz",
        "collision_groups.jsonl.gz",
        "quarantine.jsonl.gz",
        "manifest.json",
        "摘要.md",
        "SHA256SUMS",
    }
)
METRIC_FILES = frozenset(
    {
        "metric-series.jsonl.gz",
        "metric-reconciliation-summary.json",
        "manifest.json",
        "摘要.md",
        "SHA256SUMS",
    }
)
D2_JSONL_IDS = {
    "incidents.jsonl.gz": ("d2_incident", "incident_id"),
    "collision_groups.jsonl.gz": ("d2_collision_group", "collision_group_id"),
    "quarantine.jsonl.gz": ("d2_quarantine", "quarantine_id"),
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME_RE = re.compile(r"^[^/\\\x00-\x1f\x7f]{1,255}$")
INCIDENT_ID_RE = re.compile(r"^inc_v1_[0-9a-f]{24}$")
COLLISION_ID_RE = re.compile(r"^lcg_v1_[0-9a-f]{32}$")
QUARANTINE_ID_RE = re.compile(r"^qr_v1_[0-9a-f]{32}$")
ARTIFACT_ID_RE = re.compile(r"^art_v1_[0-9a-f]{32}$")
BUNDLE_ID_RE = re.compile(r"^eb_v2_[0-9a-f]{32}$")
EVIDENCE_ID_RE = re.compile(r"^ev_v2_[0-9a-f]{32}$")
ROUTE_EVENT_ID_RE = re.compile(r"^rte_v1_[0-9a-f]{32}$")
FINGERPRINT_SCHEMAS = {
    "d3": "mrt_artifact_manifest_fingerprint_v1",
    "d4": "p0_evidence_candidate_v1",
    "d4_reconciliation": "evidence_reconciliation_fingerprint_v1",
    "metric": "p0_metric_candidate_v1",
    "metric_reconciliation": "metric_reconciliation_summary_fingerprint_v1",
}
MAX_JSON_BYTES = 512 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 64 * 1024 * 1024
FILE_IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)
D4_EVENT_TYPES = (
    "hijack",
    "sub_hijack",
    "leak",
    "prefix_outage",
    "as_outage",
    "country_outage",
)
D4_SELECTION_RULE = "first_safe_matched_non_collision_per_event_type_v1"
D4_RECONCILIATION_COUNT_FIELDS = (
    "schema_invalid_count",
    "classification_violation_count",
    "causal_conclusion_nonnull_count",
    "evidence_id_conflict_count",
    "unresolved_evidence_reference_count",
    "unresolved_route_event_reference_count",
    "outside_window_record_count",
    "unknown_missing_reason_count",
    "legacy_unknown_value_count",
    "auto_zero_fill_count",
)
D4_ADMISSION = {
    "status": "sample_only_not_full_population",
    "represents_full_evidence_population": False,
    "eligible_for_release_gate": False,
    "raw_traceable": False,
    "blocking_reasons": [
        "six_event_sample_not_full_evidence_population",
        "route_event_index_not_provided",
        "metric_series_not_provided",
    ],
}


class ReproducibilityError(RuntimeError):
    """输入不能构成可信、同输入的可复现性比较。"""


def _reject_json_constant(value: str) -> None:
    raise ReproducibilityError("禁止非有限 JSON 常量：{}".format(value))


def _unique_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReproducibilityError("JSON 存在重复字段：{}".format(key))
        result[key] = value
    return result


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_bytes(value: Any) -> bytes:
    return (_canonical_json(value) + "\n").encode("utf-8")


def _d2_producer_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _identity(metadata: os.stat_result) -> Tuple[int, ...]:
    return tuple(getattr(metadata, field) for field in FILE_IDENTITY_FIELDS)


def _lstat_directory(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ReproducibilityError("无法读取{}：{}".format(label, path)) from error
    if stat.S_ISLNK(metadata.st_mode):
        raise ReproducibilityError("{}不得是符号链接：{}".format(label, path))
    if not stat.S_ISDIR(metadata.st_mode):
        raise ReproducibilityError("{}必须是目录：{}".format(label, path))
    return metadata


def _lstat_regular(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ReproducibilityError("无法读取{}：{}".format(label, path)) from error
    if stat.S_ISLNK(metadata.st_mode):
        raise ReproducibilityError("{}不得是符号链接：{}".format(label, path))
    if not stat.S_ISREG(metadata.st_mode):
        raise ReproducibilityError("{}必须是普通文件：{}".format(label, path))
    return metadata


def _open_regular(path: Path, label: str) -> Tuple[int, os.stat_result]:
    initial = _lstat_regular(path, label)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReproducibilityError("无法只读打开{}：{}".format(label, path)) from error
    current = os.fstat(descriptor)
    if not stat.S_ISREG(current.st_mode) or _identity(initial) != _identity(current):
        os.close(descriptor)
        raise ReproducibilityError("打开前{}发生变化：{}".format(label, path))
    return descriptor, current


def _read_regular(path: Path, label: str, maximum_bytes: int = MAX_JSON_BYTES) -> bytes:
    descriptor, before = _open_regular(path, label)
    chunks = []
    total = 0
    try:
        while True:
            block = os.read(descriptor, 128 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum_bytes:
                raise ReproducibilityError("{}超过 {} 字节限制".format(label, maximum_bytes))
            chunks.append(block)
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after):
            raise ReproducibilityError("读取期间{}发生变化：{}".format(label, path))
    finally:
        os.close(descriptor)
    if _identity(_lstat_regular(path, label)) != _identity(before):
        raise ReproducibilityError("读取后{}发生变化：{}".format(label, path))
    return b"".join(chunks)


def _hash_regular(path: Path, label: str) -> Tuple[str, os.stat_result]:
    descriptor, before = _open_regular(path, label)
    digest = hashlib.sha256()
    try:
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after):
            raise ReproducibilityError("哈希期间{}发生变化：{}".format(label, path))
    finally:
        os.close(descriptor)
    if _identity(_lstat_regular(path, label)) != _identity(before):
        raise ReproducibilityError("哈希后{}发生变化：{}".format(label, path))
    return digest.hexdigest(), before


def _load_json_bytes(payload: bytes, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReproducibilityError("{}不是严格 UTF-8 JSON".format(label)) from error
    if not isinstance(value, dict):
        raise ReproducibilityError("{}必须是 JSON 对象".format(label))
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, float) and not math.isfinite(current):
            raise ReproducibilityError("{}禁止非有限 JSON 数值".format(label))
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return value


def _load_json(
    path: Path, label: str, *, serialization: str = "canonical"
) -> Dict[str, Any]:
    payload = _read_regular(path, label)
    value = _load_json_bytes(payload, label)
    if serialization == "canonical":
        expected = _canonical_bytes(value)
    elif serialization in ("d2_producer_v1", "indent2_sorted_v1"):
        expected = _d2_producer_bytes(value)
    else:
        raise AssertionError(serialization)
    if payload != expected:
        raise ReproducibilityError("{}不是规范 JSON 字节".format(label))
    return value


def _safe_filename(name: str, label: str) -> None:
    if (
        name in ("", ".", "..")
        or name.startswith("._")
        or SAFE_NAME_RE.fullmatch(name) is None
        or Path(name).name != name
    ):
        raise ReproducibilityError("{}含非法文件名：{}".format(label, name))


def _parse_checksums(payload: bytes, label: str) -> Dict[str, str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ReproducibilityError("{}必须是 UTF-8".format(label)) from error
    if not text.endswith("\n"):
        raise ReproducibilityError("{}必须以换行结束".format(label))
    result: Dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if len(line) < 67 or line[64:66] != "  ":
            raise ReproducibilityError("{}第 {} 行格式非法".format(label, number))
        digest, name = line[:64], line[66:]
        if SHA256_RE.fullmatch(digest) is None:
            raise ReproducibilityError("{}第 {} 行 SHA256 非法".format(label, number))
        _safe_filename(name, label)
        if name == OUTPUT_CHECKSUMS or name in result:
            raise ReproducibilityError("{}含重复或自引用文件：{}".format(label, name))
        result[name] = digest
    if not result:
        raise ReproducibilityError("{}不能为空".format(label))
    return result


class VerifiedDirectory:
    """已经完成 SHA256 闭包和文件身份冻结的扁平目录。"""

    def __init__(self, path: Path, label: str):
        self.path = path.absolute()
        self.label = label
        self.directory_identity = _identity(_lstat_directory(self.path, label))
        try:
            entries = sorted(os.scandir(self.path), key=lambda item: item.name)
        except OSError as error:
            raise ReproducibilityError("无法枚举{}：{}".format(label, self.path)) from error
        names = []
        for entry in entries:
            _safe_filename(entry.name, label)
            metadata = entry.stat(follow_symlinks=False)
            if entry.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise ReproducibilityError("{}只允许顶层普通文件：{}".format(label, entry.name))
            names.append(entry.name)
        if OUTPUT_CHECKSUMS not in names:
            raise ReproducibilityError("{}缺少 SHA256SUMS".format(label))
        checksum_path = self.path / OUTPUT_CHECKSUMS
        self.checksum_payload = _read_regular(checksum_path, label + " SHA256SUMS", 4 * 1024 * 1024)
        self.checksum_sha256 = hashlib.sha256(self.checksum_payload).hexdigest()
        self.checksums = _parse_checksums(self.checksum_payload, label + " SHA256SUMS")
        actual = set(names) - {OUTPUT_CHECKSUMS}
        if set(self.checksums) != actual:
            raise ReproducibilityError(
                "{} SHA256SUMS 闭包不一致；缺少={}，多出={}".format(
                    label,
                    sorted(actual - set(self.checksums)),
                    sorted(set(self.checksums) - actual),
                )
            )
        self.file_identities: Dict[str, Tuple[int, ...]] = {}
        for name in sorted(actual):
            digest, metadata = _hash_regular(self.path / name, "{} {}".format(label, name))
            if digest != self.checksums[name]:
                raise ReproducibilityError("{}文件 SHA256 不一致：{}".format(label, name))
            self.file_identities[name] = _identity(metadata)
        self.file_identities[OUTPUT_CHECKSUMS] = _identity(
            _lstat_regular(checksum_path, label + " SHA256SUMS")
        )

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self.file_identities)

    def require_exact(self, expected: Iterable[str]) -> None:
        expected_set = set(expected)
        if set(self.names) != expected_set:
            raise ReproducibilityError(
                "{}文件集合不符合冻结合同；缺少={}，多出={}".format(
                    self.label,
                    sorted(expected_set - set(self.names)),
                    sorted(set(self.names) - expected_set),
                )
            )

    def json(
        self,
        name: str,
        label: Optional[str] = None,
        *,
        serialization: str = "canonical",
    ) -> Dict[str, Any]:
        if name not in self.file_identities:
            raise ReproducibilityError("{}缺少文件：{}".format(self.label, name))
        return _load_json(
            self.path / name,
            label or "{} {}".format(self.label, name),
            serialization=serialization,
        )

    def verify_unchanged(self) -> None:
        if _identity(_lstat_directory(self.path, self.label)) != self.directory_identity:
            raise ReproducibilityError("{}在比较期间发生变化".format(self.label))
        try:
            current_names = {entry.name for entry in os.scandir(self.path)}
        except OSError as error:
            raise ReproducibilityError("无法复核{}".format(self.label)) from error
        if current_names != set(self.file_identities):
            raise ReproducibilityError("{}文件集合在比较期间发生变化".format(self.label))
        for name, expected in self.file_identities.items():
            current = _identity(_lstat_regular(self.path / name, "{} {}".format(self.label, name)))
            if current != expected:
                raise ReproducibilityError("{}文件在比较期间发生变化：{}".format(self.label, name))
        if _read_regular(
            self.path / OUTPUT_CHECKSUMS,
            self.label + " SHA256SUMS 复核",
            4 * 1024 * 1024,
        ) != self.checksum_payload:
            raise ReproducibilityError("{} SHA256SUMS 在比较期间发生变化".format(self.label))


class StableIdIndex:
    """用临时 SQLite 计算 A/B 稳定标识集合的真实交并比。"""

    def __init__(self, path: Path):
        self.path = path
        self.connection = sqlite3.connect(str(path))
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA trusted_schema=OFF")
        self.connection.execute(
            "CREATE TABLE stable_id(side TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, "
            "PRIMARY KEY(side,kind,value)) WITHOUT ROWID"
        )

    def add(self, side: str, kind: str, value: Any) -> None:
        if side not in ("a", "b") or not isinstance(value, str) or not value:
            raise ReproducibilityError("稳定 ID 类型或值非法")
        try:
            self.connection.execute(
                "INSERT INTO stable_id(side,kind,value) VALUES(?,?,?)",
                (side, kind, value),
            )
        except sqlite3.IntegrityError as error:
            raise ReproducibilityError(
                "稳定 ID 在同一重跑中重复：{}:{}".format(kind, value)
            ) from error

    def commit(self) -> None:
        self.connection.commit()

    def summary(self) -> Dict[str, Any]:
        self.commit()
        counts = {
            side: self.connection.execute(
                "SELECT COUNT(*) FROM stable_id WHERE side=?", (side,)
            ).fetchone()[0]
            for side in ("a", "b")
        }
        intersection = self.connection.execute(
            "SELECT COUNT(*) FROM stable_id a JOIN stable_id b "
            "ON b.side='b' AND a.side='a' AND a.kind=b.kind AND a.value=b.value"
        ).fetchone()[0]
        union = counts["a"] + counts["b"] - intersection
        ratio = 1.0 if union == 0 else round(intersection / union, 12)
        kinds = {}
        for kind, side, count in self.connection.execute(
            "SELECT kind,side,COUNT(*) FROM stable_id GROUP BY kind,side ORDER BY kind,side"
        ):
            kinds.setdefault(kind, {"a": 0, "b": 0})[side] = count
        return {
            "method": "typed_stable_id_set_jaccard_v1",
            "a_count": counts["a"],
            "b_count": counts["b"],
            "intersection_count": intersection,
            "union_count": union,
            "by_kind": kinds,
            "match_ratio": ratio,
        }

    def close(self) -> None:
        self.connection.close()


def _valid_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ReproducibilityError("{}不是合法 SHA256".format(label))
    return value


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReproducibilityError("{}必须是对象".format(label))
    return value


def _require_exact_mapping(
    value: Any,
    expected_keys: Iterable[str],
    label: str,
) -> Mapping[str, Any]:
    mapping = _require_mapping(value, label)
    expected = set(expected_keys)
    if set(mapping) != expected:
        raise ReproducibilityError(
            "{}字段集合非法：期望 {}，实际 {}".format(
                label,
                sorted(expected),
                sorted(str(key) for key in mapping),
            )
        )
    return mapping


def _require_count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReproducibilityError("{}必须是非负整数".format(label))
    return value


def _inventory_matches(
    directory: VerifiedDirectory,
    name: str,
    inventory: Mapping[str, Any],
    *,
    row_count: Optional[int] = None,
    content_sha256: Optional[str] = None,
) -> None:
    if inventory.get("name") != name:
        raise ReproducibilityError("{} inventory.name 不一致：{}".format(directory.label, name))
    if inventory.get("sha256") != directory.checksums.get(name):
        raise ReproducibilityError("{} inventory.sha256 不一致：{}".format(directory.label, name))
    size = _require_count(inventory.get("size_bytes"), "{} {} size_bytes".format(directory.label, name))
    if size != directory.file_identities[name][3]:
        raise ReproducibilityError("{} inventory.size_bytes 不一致：{}".format(directory.label, name))
    if row_count is not None and inventory.get("row_count") != row_count:
        raise ReproducibilityError("{} inventory.row_count 不一致：{}".format(directory.label, name))
    if content_sha256 is not None and inventory.get("content_sha256") != content_sha256:
        raise ReproducibilityError("{} inventory.content_sha256 不一致：{}".format(directory.label, name))


@contextmanager
def _gzip_text(path: Path, label: str) -> Iterator[Iterable[bytes]]:
    descriptor, before = _open_regular(path, label)
    raw = os.fdopen(descriptor, "rb", closefd=True)
    stream = gzip.GzipFile(fileobj=raw, mode="rb")
    try:
        yield stream
        # 强制读取 gzip trailer，截断或尾部损坏必须报错。
        if stream.read(1):
            raise ReproducibilityError("{}读取未结束".format(label))
        after = os.fstat(raw.fileno())
        if _identity(before) != _identity(after):
            raise ReproducibilityError("读取期间{}发生变化".format(label))
    except (OSError, EOFError, gzip.BadGzipFile) as error:
        raise ReproducibilityError("{}不是完整 gzip".format(label)) from error
    finally:
        stream.close()
        raw.close()
    if _identity(_lstat_regular(path, label)) != _identity(before):
        raise ReproducibilityError("读取后{}发生变化".format(label))


def _stream_jsonl(
    directory: VerifiedDirectory,
    name: str,
    callback: Optional[Any] = None,
) -> Tuple[int, str]:
    count = 0
    content_digest = hashlib.sha256()
    with _gzip_text(directory.path / name, "{} {}".format(directory.label, name)) as stream:
        for line_number, line in enumerate(stream, 1):
            if len(line) > MAX_JSONL_LINE_BYTES:
                raise ReproducibilityError(
                    "{} {} 第 {} 行过长".format(directory.label, name, line_number)
                )
            if not line.endswith(b"\n") or not line.strip():
                raise ReproducibilityError(
                    "{} {} 第 {} 行格式非法".format(directory.label, name, line_number)
                )
            content_digest.update(line)
            value = _load_json_bytes(line, "{} {} 第 {} 行".format(directory.label, name, line_number))
            if line != _canonical_bytes(value):
                raise ReproducibilityError(
                    "{} {} 第 {} 行不是规范 JSONL".format(directory.label, name, line_number)
                )
            if callback is not None:
                callback(value)
            count += 1
    return count, content_digest.hexdigest()


def _sample_jsonl_prefix(
    directory: VerifiedDirectory,
    name: str,
    *,
    declared_count: int,
    limit: int,
    callback: Optional[Any] = None,
) -> Tuple[int, str]:
    """只解析已签名 gzip JSONL 的确定性前缀，不冒充全量语义扫描。"""

    expected = min(declared_count, limit)
    path = directory.path / name
    label = "{} {} 抽样".format(directory.label, name)
    descriptor, before = _open_regular(path, label)
    raw = os.fdopen(descriptor, "rb", closefd=True)
    stream = gzip.GzipFile(fileobj=raw, mode="rb")
    count = 0
    digest = hashlib.sha256()
    try:
        for line_number in range(1, expected + 1):
            line = stream.readline(MAX_JSONL_LINE_BYTES + 1)
            if not line:
                raise ReproducibilityError(
                    "{} 在声明记录数之前结束（期望至少 {} 行）".format(label, expected)
                )
            if len(line) > MAX_JSONL_LINE_BYTES:
                raise ReproducibilityError("{} 第 {} 行过长".format(label, line_number))
            if not line.endswith(b"\n") or not line.strip():
                raise ReproducibilityError("{} 第 {} 行格式非法".format(label, line_number))
            value = _load_json_bytes(line, "{} 第 {} 行".format(label, line_number))
            if line != _canonical_bytes(value):
                raise ReproducibilityError("{} 第 {} 行不是规范 JSONL".format(label, line_number))
            if callback is not None:
                callback(value)
            digest.update(line)
            count += 1
        if declared_count <= limit and stream.read(1):
            raise ReproducibilityError("{} 实际记录数超过 inventory 声明".format(label))
        after = os.fstat(raw.fileno())
        if _identity(before) != _identity(after):
            raise ReproducibilityError("读取期间{}发生变化：{}".format(label, path))
    except (OSError, EOFError, gzip.BadGzipFile) as error:
        raise ReproducibilityError("{}抽样读取失败".format(label)) from error
    finally:
        stream.close()
        raw.close()
    if _identity(_lstat_regular(path, label)) != _identity(before):
        raise ReproducibilityError("读取后{}发生变化：{}".format(label, path))
    return count, digest.hexdigest()


def _d2_identity(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        key: manifest.get(key)
        for key in (
            "schema_version",
            "candidate_kind",
            "data_profile",
            "window_utc",
            "source",
            "source_table_counts",
            "sample",
            "materialization_policy",
            "classification",
            "causal_conclusion",
        )
    }


def _d2_fingerprint_payload(manifest: Mapping[str, Any], label: str) -> Dict[str, Any]:
    source = _require_mapping(manifest.get("source"), label + " source")
    database = _require_mapping(source.get("database"), label + " source.database")
    provenance = _require_mapping(source.get("provenance"), label + " source.provenance")
    runner_sha = _valid_sha(
        provenance.get("probe_sha256"), label + " source.provenance.probe_sha256"
    )
    return {
        "schema_version": "p0_normalization_candidate_v1",
        "data_profile": manifest.get("data_profile"),
        "source_release": {
            "release_id": source.get("release_id"),
            "system_identifier": database.get("system_identifier"),
            "state_sha256": source.get("state_sha256"),
            "manifest_sha256": source.get("manifest_sha256"),
            "database_manifest_sha256": source.get("database_manifest_sha256"),
            "inventory_sha256": source.get("inventory_sha256"),
        },
        "runner_sha256": runner_sha,
        "normalizer_hashes": source.get("normalizer_hashes"),
        "source_table_counts": manifest.get("source_table_counts"),
        "files": manifest.get("files"),
        "summary": manifest.get("summary"),
        "sample": manifest.get("sample"),
        "classification": manifest.get("classification"),
        "causal_conclusion": manifest.get("causal_conclusion"),
    }


def _validate_d2_admission(manifest: Mapping[str, Any], label: str) -> None:
    summary = _require_mapping(manifest.get("summary"), label + " summary")
    sample = _require_mapping(manifest.get("sample"), label + " sample")
    admission = _require_mapping(manifest.get("admission"), label + " admission")
    if set(sample) != {"enabled", "max_events", "admissible"}:
        raise ReproducibilityError("{} sample 字段集合非法".format(label))
    max_events = sample.get("max_events")
    if max_events is not None and (
        isinstance(max_events, bool)
        or not isinstance(max_events, int)
        or max_events < 1
    ):
        raise ReproducibilityError("{} sample.max_events 必须为 null 或正整数".format(label))
    sample_enabled = max_events is not None
    if (
        sample.get("enabled") is not sample_enabled
        or sample.get("admissible") is sample_enabled
    ):
        raise ReproducibilityError("{} sample 与 max_events 的 producer 规则不一致".format(label))
    failures = []
    if sample_enabled:
        failures.append("fixture_sample_not_admissible")
    if _require_count(
        summary.get("unexplained_reverse_orphan_count"),
        label + " unexplained_reverse_orphan_count",
    ):
        failures.append("unexplained_reverse_references")
    if _require_count(
        summary.get("unexplained_forward_reference_count"),
        label + " unexplained_forward_reference_count",
    ):
        failures.append("unexplained_forward_references")
    expected = {
        "status": "not_eligible" if failures else "legacy_candidate_ready",
        "eligible_for_release_gate": not failures,
        "blocking_reasons": failures,
        "raw_traceable": False,
    }
    if dict(admission) != expected:
        raise ReproducibilityError("{} admission 与规范化对账结果不一致".format(label))


def _validate_d2(
    directory: VerifiedDirectory,
    side: str,
    stable: StableIdIndex,
    *,
    record_limit: Optional[int] = None,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    directory.require_exact(D2_FILES)
    # D2 的冻结 producer 使用 ``indent=2, sort_keys=True``；精确重建该字节格式，
    # 不接受 compact、乱序键、不同数字词法或其他“语义等价”编码。
    manifest = directory.json("manifest.json", serialization="d2_producer_v1")
    if manifest.get("schema_version") != "p0_normalization_candidate_v1":
        raise ReproducibilityError("{} D2 schema_version 非法".format(directory.label))
    if manifest.get("candidate_kind") != "readonly_legacy_fact_normalization":
        raise ReproducibilityError("{} D2 candidate_kind 非法".format(directory.label))
    if manifest.get("classification") != "observation_only" or manifest.get("causal_conclusion") is not None:
        raise ReproducibilityError("{} D2 观测/因果边界非法".format(directory.label))
    policy = _require_mapping(
        manifest.get("materialization_policy"), directory.label + " D2 materialization_policy"
    )
    if policy.get("missing_values_coerced_to_zero") is not False:
        raise ReproducibilityError("{} D2 禁止缺失补零".format(directory.label))
    fingerprint = _valid_sha(
        manifest.get("candidate_fingerprint_sha256"), directory.label + " D2 fingerprint"
    )
    expected_fingerprint = _canonical_sha256(
        _d2_fingerprint_payload(manifest, directory.label + " D2")
    )
    if fingerprint != expected_fingerprint:
        raise ReproducibilityError("{} D2 candidate fingerprint 复算不一致".format(directory.label))
    _validate_d2_admission(manifest, directory.label + " D2")
    files = _require_mapping(manifest.get("files"), directory.label + " D2 files")
    expected_jsonl = set(D2_FILES) - {"manifest.json", "摘要.md", "SHA256SUMS"}
    if set(files) != expected_jsonl:
        raise ReproducibilityError("{} D2 files inventory 不闭合".format(directory.label))
    id_patterns = {
        "incident_id": INCIDENT_ID_RE,
        "collision_group_id": COLLISION_ID_RE,
        "quarantine_id": QUARANTINE_ID_RE,
    }
    counts: Dict[str, int] = {}
    for name in sorted(expected_jsonl):
        callback = None
        if name in D2_JSONL_IDS:
            kind, field = D2_JSONL_IDS[name]

            def callback(row: Mapping[str, Any], *, kind: str = kind, field: str = field) -> None:
                value = row.get(field)
                if not isinstance(value, str) or id_patterns[field].fullmatch(value) is None:
                    raise ReproducibilityError(
                        "{} {} 缺少合法 {}".format(directory.label, name, field)
                    )
                stable.add(side, kind, value)

        inventory = _require_mapping(files.get(name), directory.label + " " + name)
        if record_limit is None:
            count, content_sha = _stream_jsonl(directory, name, callback)
            _inventory_matches(
                directory,
                name,
                inventory,
                row_count=count,
                content_sha256=content_sha,
            )
        else:
            count = _require_count(
                inventory.get("row_count"), directory.label + " " + name + " row_count"
            )
            _valid_sha(
                inventory.get("content_sha256"),
                directory.label + " " + name + " content_sha256",
            )
            _inventory_matches(directory, name, inventory)
            sampled, sampled_sha = _sample_jsonl_prefix(
                directory,
                name,
                declared_count=count,
                limit=record_limit,
                callback=callback,
            )
            manifest.setdefault("_validated_sample_counts", {})[name] = sampled
            manifest.setdefault("_validated_sample_content_sha256", {})[
                name
            ] = sampled_sha
        counts[name] = count
    summary = _require_mapping(manifest.get("summary"), directory.label + " D2 summary")
    expected_counts = {
        "incidents.jsonl.gz": summary.get("incident_count"),
        "links.jsonl.gz": summary.get("link_count"),
        "collision_groups.jsonl.gz": summary.get("collision_group_count"),
        "quarantine.jsonl.gz": summary.get("quarantine_count"),
    }
    if counts != expected_counts:
        raise ReproducibilityError("{} D2 流式记录数与 summary 不一致".format(directory.label))
    manifest["_validated_fingerprint"] = fingerprint
    manifest["_validated_manifest_sha256"] = directory.checksums["manifest.json"]
    manifest["_validated_checksums_sha256"] = directory.checksum_sha256
    manifest["_validated_incidents_sha256"] = directory.checksums["incidents.jsonl.gz"]
    return manifest, counts


def _artifact_id(file_sha256: str) -> str:
    return "art_v1_" + _canonical_sha256(
        {"schema": "artifact_id_v1", "file_sha256": file_sha256}
    )[:32]


def _find_d3_manifest(directory: VerifiedDirectory) -> Tuple[str, Dict[str, Any]]:
    candidates = []
    for name in sorted(directory.checksums):
        if not name.endswith(".json"):
            continue
        value = directory.json(name)
        if value.get("manifest_kind") == "mrt_artifact_manifest":
            candidates.append((name, value))
    if len(candidates) != 1:
        raise ReproducibilityError("{}必须有且仅有一个 D3 MRT manifest".format(directory.label))
    return candidates[0]


def _d3_identity(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        key: manifest.get(key)
        for key in (
            "schema_version",
            "manifest_kind",
            "artifact_id_schema",
            "data_profile",
            "filename_timestamp_timezone",
            "collector_allowlist",
            "scan_policy",
        )
    }


def _validate_d3(
    directory: VerifiedDirectory,
    side: str,
    stable: StableIdIndex,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    if len(directory.names) != 3:
        raise ReproducibilityError("{} D3 目录必须恰含 manifest、中文 JSON 摘要和 SHA256SUMS".format(directory.label))
    manifest_name, manifest = _find_d3_manifest(directory)
    other_names = set(directory.checksums) - {manifest_name}
    if len(other_names) != 1 or not next(iter(other_names)).endswith(".json"):
        raise ReproducibilityError("{} D3 缺少唯一中文 JSON 摘要".format(directory.label))
    summary_name = next(iter(other_names))
    verification_summary = directory.json(summary_name)
    fingerprint = _valid_sha(
        manifest.get("manifest_fingerprint_sha256"), directory.label + " D3 fingerprint"
    )
    payload = dict(manifest)
    payload.pop("manifest_fingerprint_sha256", None)
    expected = _canonical_sha256(
        {"schema": FINGERPRINT_SCHEMAS["d3"], "manifest": payload}
    )
    if fingerprint != expected:
        raise ReproducibilityError("{} D3 manifest fingerprint 复算不一致".format(directory.label))
    summary_manifest = _require_mapping(
        verification_summary.get("manifest"), directory.label + " D3 摘要 manifest"
    )
    verification = _require_mapping(
        verification_summary.get("verification"), directory.label + " D3 verification"
    )
    if (
        summary_manifest.get("sha256") != directory.checksums[manifest_name]
        or summary_manifest.get("fingerprint_sha256") != fingerprint
        or verification.get("verified") is not True
        or verification.get("manifest_fingerprint_sha256") != fingerprint
    ):
        raise ReproducibilityError("{} D3 摘要未闭合 manifest 身份".format(directory.label))
    scan_policy = _require_mapping(
        manifest.get("scan_policy"), directory.label + " D3 scan_policy"
    )
    if (
        scan_policy.get("compression_envelope_validation")
        != "full_stream_to_eof_crc_or_equivalent"
    ):
        raise ReproducibilityError("{} D3 未冻结压缩流完整性策略".format(directory.label))
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ReproducibilityError("{} D3 artifacts 必须是数组".format(directory.label))
    total_size = 0
    for artifact in artifacts:
        row = _require_mapping(artifact, directory.label + " D3 artifact")
        artifact_id = row.get("artifact_id")
        file_sha = _valid_sha(row.get("file_sha256"), directory.label + " D3 file_sha256")
        if (
            not isinstance(artifact_id, str)
            or ARTIFACT_ID_RE.fullmatch(artifact_id) is None
            or artifact_id != _artifact_id(file_sha)
        ):
            raise ReproducibilityError("{} D3 artifact_id 复算不一致".format(directory.label))
        stable.add(side, "d3_artifact", artifact_id)
        total_size += _require_count(row.get("size_bytes"), directory.label + " D3 size_bytes")
    summary = _require_mapping(manifest.get("summary"), directory.label + " D3 summary")
    if summary.get("artifact_count") != len(artifacts) or summary.get("size_bytes") != total_size:
        raise ReproducibilityError("{} D3 summary 与 artifacts 不闭合".format(directory.label))
    invalid_records = manifest.get("invalid_in_window")
    if not isinstance(invalid_records, list):
        raise ReproducibilityError("{} D3 invalid_in_window 必须是数组".format(directory.label))
    invalid_reasons = (
        "compressed_stream_invalid",
        "compression_magic_mismatch",
        "empty_file",
    )
    reason_counts = {reason: 0 for reason in invalid_reasons}
    reason_sizes = {reason: 0 for reason in invalid_reasons}
    invalid_coordinates = set()
    artifact_coordinates = {
        (
            row.get("collector_id"),
            row.get("artifact_type"),
            row.get("artifact_time_utc"),
        )
        for row in artifacts
        if isinstance(row, Mapping)
    }
    for invalid in invalid_records:
        row = _require_mapping(invalid, directory.label + " D3 invalid artifact")
        reason = row.get("missing_reason")
        size = _require_count(row.get("size_bytes"), directory.label + " D3 invalid size")
        coordinate = (
            row.get("collector_id"),
            row.get("artifact_type"),
            row.get("artifact_time_utc"),
        )
        _valid_sha(row.get("file_sha256"), directory.label + " D3 invalid file_sha256")
        if (
            row.get("value_state") != "parse_failed"
            or reason not in reason_counts
            or coordinate in artifact_coordinates
            or coordinate in invalid_coordinates
            or (reason == "empty_file" and size != 0)
            or (reason != "empty_file" and size == 0)
        ):
            raise ReproducibilityError("{} D3 invalid artifact 分类非法".format(directory.label))
        invalid_coordinates.add(coordinate)
        reason_counts[reason] += 1
        reason_sizes[reason] += size
    invalid_summary = _require_mapping(
        summary.get("invalid_in_window"), directory.label + " D3 invalid summary"
    )
    expected_invalid_summary = {
        "file_count": len(invalid_records),
        "size_bytes": sum(reason_sizes.values()),
        "by_missing_reason": {
            reason: {
                "file_count": reason_counts[reason],
                "size_bytes": reason_sizes[reason],
            }
            for reason in invalid_reasons
        },
    }
    if invalid_summary != expected_invalid_summary:
        raise ReproducibilityError("{} D3 invalid summary 不闭合".format(directory.label))
    coverage = _require_mapping(manifest.get("coverage"), directory.label + " D3 coverage")
    expected_slots = _require_count(coverage.get("expected_slots"), directory.label + " D3 expected")
    available_slots = _require_count(coverage.get("available_slots"), directory.label + " D3 available")
    missing_slots = _require_count(coverage.get("missing_slots"), directory.label + " D3 missing")
    ranges = coverage.get("missing_ranges")
    if expected_slots != available_slots + missing_slots or not isinstance(ranges, list):
        raise ReproducibilityError("{} D3 coverage 计数非法".format(directory.label))
    range_counts = {"source_unavailable": 0, "parse_failed": 0}
    for row in ranges:
        item = _require_mapping(row, directory.label + " D3 missing range")
        state = item.get("value_state")
        if state not in range_counts:
            raise ReproducibilityError("{} D3 missing range 状态非法".format(directory.label))
        range_counts[state] += _require_count(
            item.get("slot_count"), directory.label + " D3 range slot_count"
        )
    if (
        sum(range_counts.values()) != missing_slots
        or range_counts["parse_failed"] != len(invalid_records)
    ):
        raise ReproducibilityError("{} D3 缺口状态与 invalid 明细不闭合".format(directory.label))
    manifest["_validated_fingerprint"] = fingerprint
    manifest["_validated_manifest_sha256"] = directory.checksums[manifest_name]
    manifest["_validated_summary_sha256"] = directory.checksums[summary_name]
    manifest["_validated_checksums_sha256"] = directory.checksum_sha256
    return manifest, {
        "artifacts": len(artifacts),
        "invalid_in_window": len(invalid_records),
    }


def _candidate_fingerprint_payload(manifest: Mapping[str, Any], kind: str) -> Dict[str, Any]:
    if kind == "d4":
        inputs = _require_mapping(manifest.get("inputs"), "D4 inputs")
        d2 = _require_mapping(inputs.get("d2"), "D4 inputs.d2")
        d3 = _require_mapping(inputs.get("d3_artifacts"), "D4 inputs.d3_artifacts")
        registry = _require_mapping(manifest.get("registry"), "D4 registry")
        return {
            "schema_version": manifest.get("schema_version"),
            "candidate_kind": manifest.get("candidate_kind"),
            "data_profile": manifest.get("data_profile"),
            "generated_at": manifest.get("generated_at"),
            "inputs": {
                "d2_manifest_sha256": d2.get("manifest_sha256"),
                "d2_candidate_fingerprint_sha256": d2.get("candidate_fingerprint_sha256"),
                "d3_artifact_manifest_sha256": d3.get("manifest_sha256"),
                "d3_artifact_fingerprint_sha256": d3.get("manifest_fingerprint_sha256"),
            },
            "generator": manifest.get("generator"),
            "selection": manifest.get("selection"),
            "files": manifest.get("files"),
            "registry_entry_count": registry.get("entry_count"),
            "classification": manifest.get("classification"),
            "causal_conclusion": manifest.get("causal_conclusion"),
        }
    if kind == "metric":
        return {
            "schema_version": manifest.get("schema_version"),
            "data_profile": manifest.get("data_profile"),
            "metric_window_utc": manifest.get("metric_window_utc"),
            "generated_at": manifest.get("generated_at"),
            "sources": manifest.get("sources"),
            "files": manifest.get("files"),
            "summary": manifest.get("summary"),
            "sample": manifest.get("sample"),
            "classification": manifest.get("classification"),
            "causal_conclusion": manifest.get("causal_conclusion"),
        }
    raise AssertionError(kind)


def _validate_summary_fingerprint(summary: Mapping[str, Any], kind: str) -> str:
    fingerprint = _valid_sha(
        summary.get("summary_fingerprint_sha256"), kind + " summary fingerprint"
    )
    payload = dict(summary)
    payload.pop("summary_fingerprint_sha256", None)
    # Metric 的生成器把 fingerprint 字段加入前的整个 summary 作为 identity；
    # Evidence 则显式包裹去掉 fingerprint 后的 payload。
    expected = _canonical_sha256(
        {
            "schema": FINGERPRINT_SCHEMAS[kind + "_reconciliation"],
            "summary": payload,
        }
    )
    if fingerprint != expected:
        raise ReproducibilityError("{} summary fingerprint 复算不一致".format(kind))
    return fingerprint


def _d4_identity(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    # D2/D3 指纹属于上游重跑输出。若上游本身不确定，D4 的 inputs 理应随之
    # 不同；这应落为 fingerprint=false，而不是把整条流水线误判成不可比较。
    return {
        "schema_version": manifest.get("schema_version"),
        "candidate_kind": manifest.get("candidate_kind"),
        "data_profile": manifest.get("data_profile"),
        "generated_at": manifest.get("generated_at"),
        "generator": manifest.get("generator"),
        "classification": manifest.get("classification"),
        "causal_conclusion": manifest.get("causal_conclusion"),
    }


def _validate_d4(
    directory: VerifiedDirectory,
    side: str,
    stable: StableIdIndex,
) -> Tuple[Dict[str, Any], Dict[str, int], Dict[str, Any]]:
    manifest = directory.json("manifest.json")
    _require_exact_mapping(
        manifest,
        {
            "schema_version",
            "candidate_kind",
            "candidate_fingerprint_sha256",
            "data_profile",
            "generated_at",
            "inputs",
            "generator",
            "selection",
            "files",
            "registry",
            "reconciliation",
            "validation",
            "admission",
            "classification",
            "causal_conclusion",
        },
        directory.label + " D4 manifest",
    )
    if manifest.get("schema_version") != "p0_evidence_candidate_v1":
        raise ReproducibilityError("{} D4 schema_version 非法".format(directory.label))
    if manifest.get("candidate_kind") != "six_event_contract_investigation_sample":
        raise ReproducibilityError("{} D4 candidate_kind 非法".format(directory.label))
    if (
        manifest.get("classification") != "observation_only"
        or manifest.get("causal_conclusion") is not None
    ):
        raise ReproducibilityError("{} D4 观测/因果边界非法".format(directory.label))

    inputs = _require_exact_mapping(
        manifest.get("inputs"),
        {"d2", "d3_artifacts", "route_event_index", "metric_series"},
        directory.label + " D4 inputs",
    )
    d2_input = _require_exact_mapping(
        inputs.get("d2"),
        {
            "manifest_sha256",
            "candidate_fingerprint_sha256",
            "admission_status",
            "sample_enabled",
            "sha256_closure",
            "content_hash_closure",
        },
        directory.label + " D4 inputs.d2",
    )
    _valid_sha(d2_input.get("manifest_sha256"), directory.label + " D4 D2 manifest")
    _valid_sha(
        d2_input.get("candidate_fingerprint_sha256"),
        directory.label + " D4 D2 fingerprint",
    )
    if (
        d2_input.get("admission_status") != "legacy_candidate_ready"
        or d2_input.get("sample_enabled") is not False
        or d2_input.get("sha256_closure") != "passed"
        or d2_input.get("content_hash_closure") != "passed"
    ):
        raise ReproducibilityError("{} D4 D2 派生状态非法".format(directory.label))
    d3_input = _require_exact_mapping(
        inputs.get("d3_artifacts"),
        {
            "manifest_sha256",
            "summary_sha256",
            "manifest_fingerprint_sha256",
            "raw_source_status",
            "update_coverage",
            "sha256_closure",
            "verification_status",
        },
        directory.label + " D4 inputs.d3_artifacts",
    )
    for field in ("manifest_sha256", "summary_sha256", "manifest_fingerprint_sha256"):
        _valid_sha(d3_input.get(field), directory.label + " D4 D3 " + field)
    update_coverage = _require_exact_mapping(
        d3_input.get("update_coverage"),
        {"expected_count", "observed_count"},
        directory.label + " D4 D3 update_coverage",
    )
    update_expected = _require_count(
        update_coverage.get("expected_count"),
        directory.label + " D4 D3 update expected_count",
    )
    update_observed = _require_count(
        update_coverage.get("observed_count"),
        directory.label + " D4 D3 update observed_count",
    )
    if (
        update_expected < 1
        or update_observed > update_expected
        or d3_input.get("raw_source_status") not in {"unavailable", "partial", "full"}
        or d3_input.get("sha256_closure") != "passed"
        or d3_input.get("verification_status") != "verified"
    ):
        raise ReproducibilityError("{} D4 D3 派生状态非法".format(directory.label))
    route_input = _require_exact_mapping(
        inputs.get("route_event_index"),
        {"status", "missing_reason"},
        directory.label + " D4 inputs.route_event_index",
    )
    if dict(route_input) != {
        "status": "not_provided",
        "missing_reason": "route_event_index_not_available_for_candidate",
    }:
        raise ReproducibilityError("{} D4 RouteEvent 输入边界非法".format(directory.label))
    metric_input = _require_exact_mapping(
        inputs.get("metric_series"),
        {"status", "missing_reason"},
        directory.label + " D4 inputs.metric_series",
    )
    if dict(metric_input) != {
        "status": "not_provided",
        "missing_reason": "metric_series_not_available_for_candidate",
    }:
        raise ReproducibilityError("{} D4 MetricSeries 输入边界非法".format(directory.label))

    generator = _require_exact_mapping(
        manifest.get("generator"),
        {"runner_sha256", "evidence_module_hashes", "schema_sha256"},
        directory.label + " D4 generator",
    )
    _valid_sha(generator.get("runner_sha256"), directory.label + " D4 runner_sha256")
    schema_sha = _valid_sha(
        generator.get("schema_sha256"), directory.label + " D4 schema_sha256"
    )
    module_hashes = _require_mapping(
        generator.get("evidence_module_hashes"),
        directory.label + " D4 evidence_module_hashes",
    )
    if not module_hashes:
        raise ReproducibilityError("{} D4 evidence_module_hashes 不能为空".format(directory.label))
    for module_name, digest in module_hashes.items():
        if not isinstance(module_name, str) or not module_name:
            raise ReproducibilityError("{} D4 evidence module 路径非法".format(directory.label))
        _valid_sha(digest, directory.label + " D4 evidence module " + module_name)

    admission = _require_exact_mapping(
        manifest.get("admission"), D4_ADMISSION, directory.label + " D4 admission"
    )
    if dict(admission) != D4_ADMISSION:
        raise ReproducibilityError("{} D4 admission 与样本边界不一致".format(directory.label))

    files = _require_mapping(manifest.get("files"), directory.label + " D4 files")
    expected = set(files) | {"manifest.json", "摘要.md", "SHA256SUMS"}
    directory.require_exact(expected)
    for name, inventory_value in files.items():
        if not isinstance(name, str):
            raise ReproducibilityError("{} D4 files 文件名非法".format(directory.label))
        inventory = _require_mapping(inventory_value, directory.label + " D4 " + name)
        _inventory_matches(directory, name, inventory)
    fingerprint = _valid_sha(
        manifest.get("candidate_fingerprint_sha256"), directory.label + " D4 fingerprint"
    )
    expected_fingerprint = _canonical_sha256(_candidate_fingerprint_payload(manifest, "d4"))
    if fingerprint != expected_fingerprint:
        raise ReproducibilityError("{} D4 candidate fingerprint 复算不一致".format(directory.label))

    reconciliation = directory.json("evidence-reconciliation-summary.json")
    _require_exact_mapping(
        reconciliation,
        {
            "schema_version",
            "scope",
            "sample_only",
            "population_coverage_claimed",
            "bundle_count",
            "event_type_count",
            "event_types",
            "bundle_ids",
            "strict_schema_status",
            "schema_sha256",
            "reference_closure_status",
            *D4_RECONCILIATION_COUNT_FIELDS,
            "classification",
            "causal_conclusion",
            "summary_fingerprint_sha256",
        },
        directory.label + " D4 reconciliation",
    )
    if (
        reconciliation.get("schema_version") != "evidence_reconciliation_v1"
        or reconciliation.get("scope") != "six_event_contract_investigation_sample"
        or reconciliation.get("sample_only") is not True
        or reconciliation.get("population_coverage_claimed") is not False
        or reconciliation.get("strict_schema_status") != "passed"
        or reconciliation.get("reference_closure_status") != "passed"
        or reconciliation.get("classification") != "observation_only"
        or reconciliation.get("causal_conclusion") is not None
        or reconciliation.get("event_types") != sorted(D4_EVENT_TYPES)
    ):
        raise ReproducibilityError("{} D4 reconciliation 元数据非法".format(directory.label))
    if _valid_sha(
        reconciliation.get("schema_sha256"), directory.label + " D4 reconciliation schema"
    ) != schema_sha:
        raise ReproducibilityError("{} D4 reconciliation 未绑定 generator schema".format(directory.label))
    for field in D4_RECONCILIATION_COUNT_FIELDS:
        count = _require_count(
            reconciliation.get(field), directory.label + " D4 reconciliation " + field
        )
        if field != "legacy_unknown_value_count" and count != 0:
            raise ReproducibilityError(
                "{} D4 reconciliation 存在阻断计数 {}".format(directory.label, field)
            )
    reconciliation_fingerprint = _validate_summary_fingerprint(reconciliation, "d4")

    selection = _require_exact_mapping(
        manifest.get("selection"), D4_EVENT_TYPES, directory.label + " D4 selection"
    )
    bundle_files = []
    bundle_ids = set()
    incident_ids = set()
    evidence_ids = set()
    expected_registry_entries: Dict[str, Any] = {}
    for event_type in D4_EVENT_TYPES:
        row = _require_exact_mapping(
            selection[event_type],
            {
                "incident_id",
                "source_table",
                "source_primary_key",
                "bundle_id",
                "bundle_file",
                "fact_link_status",
                "source_fact_record_hash",
                "selection_rule",
            },
            directory.label + " D4 selection." + event_type,
        )
        if (
            not isinstance(row.get("source_table"), str)
            or not row.get("source_table")
            or not isinstance(row.get("source_primary_key"), Mapping)
            or row.get("fact_link_status") != "matched"
            or row.get("source_fact_record_hash") is not None
            or row.get("selection_rule") != D4_SELECTION_RULE
        ):
            raise ReproducibilityError(
                "{} D4 selection.{} 来源定位非法".format(directory.label, event_type)
            )
        name = row.get("bundle_file")
        if not isinstance(name, str) or name not in files or name in bundle_files:
            raise ReproducibilityError("{} D4 bundle_file 非法或重复".format(directory.label))
        bundle_files.append(name)
        bundle = directory.json(name)
        if bundle.get("bundle_version") != "evidence_bundle_v2":
            raise ReproducibilityError("{} D4 bundle_version 非法".format(directory.label))
        bundle_id = bundle.get("bundle_id")
        if not isinstance(bundle_id, str) or BUNDLE_ID_RE.fullmatch(bundle_id) is None:
            raise ReproducibilityError("{} D4 bundle_id 非法".format(directory.label))
        if row.get("bundle_id") != bundle_id or bundle_id in bundle_ids:
            raise ReproducibilityError("{} D4 selection.bundle_id 不一致".format(directory.label))
        bundle_ids.add(bundle_id)
        stable.add(side, "d4_bundle", bundle_id)
        incident = _require_mapping(bundle.get("incident"), directory.label + " D4 incident")
        incident_id = incident.get("incident_id")
        if not isinstance(incident_id, str) or INCIDENT_ID_RE.fullmatch(incident_id) is None:
            raise ReproducibilityError("{} D4 incident_id 非法".format(directory.label))
        if (
            row.get("incident_id") != incident_id
            or incident.get("event_type") != event_type
            or incident_id in incident_ids
        ):
            raise ReproducibilityError("{} D4 selection.incident_id 不一致".format(directory.label))
        incident_ids.add(incident_id)
        coverage_summary = _require_mapping(
            bundle.get("coverage_summary"), directory.label + " D4 coverage_summary"
        )
        conclusion = _require_mapping(
            bundle.get("conclusion"), directory.label + " D4 conclusion"
        )
        if (
            coverage_summary.get("admission_level") != "legacy_compatible"
            or bundle.get("route_event_refs") != []
            or bundle.get("raw_record_refs") != []
            or bundle.get("metric_windows") != []
            or conclusion.get("classification") != "observation_only"
            or conclusion.get("causal_conclusion") is not None
        ):
            raise ReproducibilityError("{} D4 bundle 超出 legacy 样本边界".format(directory.label))
        registry = bundle.get("evidence_registry")
        if not isinstance(registry, list):
            raise ReproducibilityError("{} D4 evidence_registry 非数组".format(directory.label))
        for item in registry:
            evidence = _require_mapping(item, directory.label + " D4 evidence")
            evidence_id = evidence.get("evidence_id")
            if not isinstance(evidence_id, str) or EVIDENCE_ID_RE.fullmatch(evidence_id) is None:
                raise ReproducibilityError("{} D4 evidence_id 非法".format(directory.label))
            if evidence_id in evidence_ids:
                raise ReproducibilityError("{} D4 evidence_id 重复".format(directory.label))
            evidence_ids.add(evidence_id)
            stable.add(side, "d4_evidence", evidence_id)
            expected_registry_entries[evidence_id] = {
                "bundle_id": bundle_id,
                "bundle_file": name,
                "registry_item": dict(evidence),
            }

    if set(files) != set(bundle_files) | {
        "evidence-registry.json",
        "evidence-reconciliation-summary.json",
    }:
        raise ReproducibilityError("{} D4 files 未精确闭合六类 Bundle".format(directory.label))

    registry = directory.json("evidence-registry.json")
    _require_exact_mapping(
        registry,
        {
            "schema_version",
            "candidate_scope",
            "entry_count",
            "entries",
            "classification",
            "causal_conclusion",
        },
        directory.label + " D4 registry",
    )
    registry_entries = _require_mapping(
        registry.get("entries"), directory.label + " D4 registry.entries"
    )
    if (
        registry.get("schema_version") != "p0_evidence_registry_index_v1"
        or registry.get("candidate_scope") != "six_event_contract_investigation_sample"
        or registry.get("classification") != "observation_only"
        or registry.get("causal_conclusion") is not None
        or registry.get("entry_count") != len(evidence_ids)
        or dict(registry_entries) != expected_registry_entries
    ):
        raise ReproducibilityError("{} D4 evidence registry 引用不闭合".format(directory.label))

    registry_metadata = _require_exact_mapping(
        manifest.get("registry"),
        {
            "file",
            "entry_count",
            "evidence_id_conflict_count",
            "unresolved_evidence_reference_count",
            "unresolved_route_event_reference_count",
            "reference_closure_ratio",
        },
        directory.label + " D4 manifest.registry",
    )
    registry_count = _require_count(
        registry_metadata.get("entry_count"), directory.label + " D4 registry entry_count"
    )
    closure_ratio = _require_count(
        registry_metadata.get("reference_closure_ratio"),
        directory.label + " D4 reference_closure_ratio",
    )
    if dict(registry_metadata) != {
        "file": "evidence-registry.json",
        "entry_count": len(evidence_ids),
        "evidence_id_conflict_count": 0,
        "unresolved_evidence_reference_count": 0,
        "unresolved_route_event_reference_count": 0,
        "reference_closure_ratio": 1,
    } or registry_count != len(evidence_ids) or closure_ratio != 1:
        raise ReproducibilityError("{} D4 registry metadata 不闭合".format(directory.label))

    if (
        reconciliation.get("bundle_count") != len(bundle_files)
        or reconciliation.get("event_type_count") != len(D4_EVENT_TYPES)
        or reconciliation.get("bundle_ids") != sorted(bundle_ids)
    ):
        raise ReproducibilityError("{} D4 reconciliation Bundle 元数据不一致".format(directory.label))
    reconciliation_metadata = _require_exact_mapping(
        manifest.get("reconciliation"),
        {
            "file",
            "schema_version",
            "scope",
            "sample_only",
            "population_coverage_claimed",
            "summary_fingerprint_sha256",
        },
        directory.label + " D4 manifest.reconciliation",
    )
    expected_reconciliation_metadata = {
        "file": "evidence-reconciliation-summary.json",
        "schema_version": reconciliation["schema_version"],
        "scope": reconciliation["scope"],
        "sample_only": True,
        "population_coverage_claimed": False,
        "summary_fingerprint_sha256": reconciliation_fingerprint,
    }
    if dict(reconciliation_metadata) != expected_reconciliation_metadata:
        raise ReproducibilityError("{} D4 reconciliation metadata 不闭合".format(directory.label))

    validation = _require_exact_mapping(
        manifest.get("validation"),
        {
            "strict_schema_status",
            "schema_sha256",
            "bundle_count",
            "event_type_count",
            "classification_violation_count",
            "causal_conclusion_nonnull_count",
            "auto_zero_fill_count",
        },
        directory.label + " D4 validation",
    )
    expected_validation = {
        "strict_schema_status": "passed",
        "schema_sha256": schema_sha,
        "bundle_count": len(bundle_files),
        "event_type_count": len(D4_EVENT_TYPES),
        "classification_violation_count": 0,
        "causal_conclusion_nonnull_count": 0,
        "auto_zero_fill_count": 0,
    }
    if dict(validation) != expected_validation:
        raise ReproducibilityError("{} D4 validation 与实际 Bundle 不一致".format(directory.label))

    manifest["_validated_fingerprint"] = fingerprint
    manifest["_validated_reconciliation_fingerprint"] = reconciliation_fingerprint
    return (
        manifest,
        {"bundles": len(bundle_files), "evidence_registry_entries": len(evidence_ids)},
        reconciliation,
    )


def _metric_identity(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    sources = _require_mapping(manifest.get("sources"), "Metric sources")
    return {
        "schema_version": manifest.get("schema_version"),
        "candidate_kind": manifest.get("candidate_kind"),
        "data_profile": manifest.get("data_profile"),
        "metric_window_utc": manifest.get("metric_window_utc"),
        "generated_at": manifest.get("generated_at"),
        # 保留真正独立的数据库快照与合同身份；D2/D3 项是上游输出，不能
        # 因上游不确定而让外部 A/B 摘要在记录 false 前提前中止。
        "root_sources": {
            "database": sources.get("database"),
            "contracts": sources.get("contracts"),
        },
        "provenance": manifest.get("provenance"),
        "source_slot_policies": manifest.get("source_slot_policies"),
        "sample": manifest.get("sample"),
        "classification": manifest.get("classification"),
        "causal_conclusion": manifest.get("causal_conclusion"),
    }


def _validate_metric(
    directory: VerifiedDirectory,
    side: str,
    stable: StableIdIndex,
) -> Tuple[Dict[str, Any], Dict[str, int], Dict[str, Any]]:
    directory.require_exact(METRIC_FILES)
    manifest = directory.json("manifest.json", serialization="indent2_sorted_v1")
    if manifest.get("schema_version") != "p0_metric_candidate_v1":
        raise ReproducibilityError("{} Metric schema_version 非法".format(directory.label))
    files = _require_mapping(manifest.get("files"), directory.label + " Metric files")
    if set(files) != {"metric-series.jsonl.gz", "metric-reconciliation-summary.json"}:
        raise ReproducibilityError("{} Metric files inventory 不闭合".format(directory.label))
    series_names = set()
    point_count = 0

    def metric_row(row: Mapping[str, Any]) -> None:
        nonlocal point_count
        name = row.get("metric_name")
        if not isinstance(name, str) or not name:
            raise ReproducibilityError("{} Metric metric_name 非法".format(directory.label))
        if name in series_names:
            raise ReproducibilityError("{} Metric metric_name 重复".format(directory.label))
        series_names.add(name)
        stable.add(side, "metric_name", name)
        points = row.get("points")
        if not isinstance(points, list):
            raise ReproducibilityError("{} Metric points 非数组".format(directory.label))
        point_count += len(points)

    series_count, content_sha = _stream_jsonl(directory, "metric-series.jsonl.gz", metric_row)
    _inventory_matches(
        directory,
        "metric-series.jsonl.gz",
        _require_mapping(files["metric-series.jsonl.gz"], directory.label + " Metric series inventory"),
        row_count=series_count,
        content_sha256=content_sha,
    )
    reconciliation = directory.json(
        "metric-reconciliation-summary.json", serialization="indent2_sorted_v1"
    )
    _inventory_matches(
        directory,
        "metric-reconciliation-summary.json",
        _require_mapping(files["metric-reconciliation-summary.json"], directory.label + " Metric reconciliation inventory"),
    )
    if reconciliation.get("schema_version") != "metric_reconciliation_v1":
        raise ReproducibilityError("{} Metric reconciliation schema 非法".format(directory.label))
    reconciliation_fingerprint = _validate_summary_fingerprint(reconciliation, "metric")
    if reconciliation.get("series_count") != series_count or reconciliation.get("point_count") != point_count:
        raise ReproducibilityError("{} Metric reconciliation 记录数不一致".format(directory.label))
    fingerprint = _valid_sha(
        manifest.get("candidate_fingerprint_sha256"), directory.label + " Metric fingerprint"
    )
    if fingerprint != _canonical_sha256(_candidate_fingerprint_payload(manifest, "metric")):
        raise ReproducibilityError("{} Metric candidate fingerprint 复算不一致".format(directory.label))
    manifest["_validated_fingerprint"] = fingerprint
    manifest["_validated_reconciliation_fingerprint"] = reconciliation_fingerprint
    return manifest, {"series": series_count, "points": point_count}, reconciliation


def _route_index_fingerprint(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    digest.update(
        (_canonical_json({"schema": "route_event_index_fingerprint_v1"}) + "\n").encode("utf-8")
    )
    table_queries = (
        ("sqlite_schema", "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"),
        ("metadata", "SELECT key,value_json FROM metadata WHERE key <> 'index_fingerprint_sha256' ORDER BY key"),
        ("artifact", "SELECT * FROM artifact ORDER BY artifact_id"),
        ("raw_record", "SELECT * FROM raw_record ORDER BY artifact_id,record_ordinal"),
        ("vantage_point", "SELECT * FROM vantage_point ORDER BY vp_id"),
        ("as_path", "SELECT * FROM as_path ORDER BY path_id"),
        ("route_event", "SELECT * FROM route_event ORDER BY route_event_id"),
        ("incident_observation", "SELECT * FROM incident_observation ORDER BY incident_id"),
        ("incident_route_event_link", "SELECT * FROM incident_route_event_link ORDER BY incident_id,route_event_id,object_type,object_id"),
    )
    try:
        for table, query in table_queries:
            digest.update((table + "\n").encode("utf-8"))
            for row in connection.execute(query):
                digest.update((_canonical_json(list(row)) + "\n").encode("utf-8"))
    except sqlite3.Error as error:
        raise ReproducibilityError("RouteEvent SQLite schema 或内容不可复核") from error
    return digest.hexdigest()


def _find_route_summary(directory: VerifiedDirectory) -> Tuple[str, Dict[str, Any]]:
    name = "route-event-reconciliation-summary.json"
    value = directory.json(name)
    if value.get("schema_version") != "route_event_index_summary_v1":
        raise ReproducibilityError("{} RouteEvent 对账摘要 schema 非法".format(directory.label))
    # 允许 selection、中文摘要等任意已签名旁路制品，但禁止第二份机器摘要
    # 混淆 D5 输入身份。
    for candidate in sorted(directory.checksums):
        if candidate == name or not candidate.endswith(".json"):
            continue
        other = directory.json(candidate)
        if other.get("schema_version") == "route_event_index_summary_v1":
            raise ReproducibilityError("{}存在重复 RouteEvent 对账摘要".format(directory.label))
    return name, value


def _validate_route(
    directory: VerifiedDirectory,
    side: str,
    stable: StableIdIndex,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    _, summary = _find_route_summary(directory)
    sqlite_names = sorted(name for name in directory.checksums if name.endswith(".sqlite3"))
    expected_sqlite = "p0-route-event-pilot.sqlite3"
    if sqlite_names != [expected_sqlite]:
        raise ReproducibilityError(
            "{}必须只包含签名的 {}".format(directory.label, expected_sqlite)
        )
    index_path = directory.path / expected_sqlite
    uri = "file:{}?mode=ro&immutable=1".format(quote(index_path.absolute().as_posix(), safe="/"))
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ReproducibilityError("{} RouteEvent quick_check 失败".format(directory.label))
        metadata_row = connection.execute(
            "SELECT value_json FROM metadata WHERE key='index_fingerprint_sha256'"
        ).fetchone()
        if metadata_row is None:
            raise ReproducibilityError("{} RouteEvent 缺少 index fingerprint".format(directory.label))
        expected = json.loads(metadata_row[0])
        expected = _valid_sha(expected, directory.label + " RouteEvent fingerprint")
        actual = _route_index_fingerprint(connection)
        if actual != expected or summary.get("index_fingerprint_sha256") != actual:
            raise ReproducibilityError("{} RouteEvent fingerprint 复算不一致".format(directory.label))
        counts = {
            "route_events": connection.execute("SELECT COUNT(*) FROM route_event").fetchone()[0],
            "raw_records": connection.execute("SELECT COUNT(*) FROM raw_record").fetchone()[0],
        }
        if summary.get("route_event_count") != counts["route_events"] or summary.get("raw_record_count") != counts["raw_records"]:
            raise ReproducibilityError("{} RouteEvent summary 计数不一致".format(directory.label))
        for (route_event_id,) in connection.execute("SELECT route_event_id FROM route_event ORDER BY route_event_id"):
            if not isinstance(route_event_id, str) or ROUTE_EVENT_ID_RE.fullmatch(route_event_id) is None:
                raise ReproducibilityError("{} RouteEvent ID 非法".format(directory.label))
            stable.add(side, "route_event", route_event_id)
    except sqlite3.Error as error:
        raise ReproducibilityError("{} RouteEvent SQLite 无法只读复核".format(directory.label)) from error
    finally:
        if "connection" in locals():
            connection.close()
    summary["_validated_fingerprint"] = actual
    return summary, counts


def _assert_same_input(label: str, first: Mapping[str, Any], second: Mapping[str, Any]) -> None:
    if _canonical_bytes(first) != _canonical_bytes(second):
        raise ReproducibilityError("{} A/B 输入身份不同，不能声明重跑可复现性".format(label))


def _route_identity(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    scope = _require_mapping(summary.get("build_scope"), "RouteEvent build_scope")
    # parent/selection fingerprint 与 selection_summary 都是 D3 输出的派生结果；
    # 上游不确定时必须让本摘要落 false，而不能在这里提前中止。
    return {
        "schema_version": summary.get("schema_version"),
        "parser_capability": summary.get("parser_capability"),
        "parser_attestation_fingerprint_sha256": summary.get(
            "parser_attestation_fingerprint_sha256"
        ),
        "build_scope": {
            key: scope.get(key)
            for key in (
                "scope_mode",
                "pilot_only",
                "production_complete",
                "limits",
                "data_profile",
                "raw_reference_contract",
                "limitations",
            )
        },
        "classification": summary.get("classification"),
        "causal_conclusion": summary.get("causal_conclusion"),
    }


def _d3_update_projection_for_d4(
    d3: Mapping[str, Any], label: str
) -> Tuple[str, Dict[str, int]]:
    """按 D4 producer 规则从 D3 collector coverage 重建 update 状态。"""

    coverage = _require_mapping(d3.get("coverage"), label + " coverage")
    by_collector = coverage.get("by_collector")
    if not isinstance(by_collector, list) or not by_collector:
        raise ReproducibilityError("{} collector coverage 缺失".format(label))
    expected_count = 0
    observed_count = 0
    for index, collector_value in enumerate(by_collector):
        collector = _require_mapping(
            collector_value, "{} by_collector[{}]".format(label, index)
        )
        by_artifact_type = _require_mapping(
            collector.get("by_artifact_type"),
            "{} by_collector[{}].by_artifact_type".format(label, index),
        )
        update = _require_mapping(
            by_artifact_type.get("update"),
            "{} by_collector[{}].update".format(label, index),
        )
        current_expected = _require_count(
            update.get("expected_slots"),
            "{} by_collector[{}].update.expected_slots".format(label, index),
        )
        current_observed = _require_count(
            update.get("available_slots"),
            "{} by_collector[{}].update.available_slots".format(label, index),
        )
        if current_observed > current_expected:
            raise ReproducibilityError("{} update coverage 计数非法".format(label))
        expected_count += current_expected
        observed_count += current_observed
    if expected_count < 1:
        raise ReproducibilityError("{} update expected_count 不得为 0".format(label))
    status = (
        "unavailable"
        if observed_count == 0
        else ("full" if observed_count == expected_count else "partial")
    )
    return status, {
        "expected_count": expected_count,
        "observed_count": observed_count,
    }


def _cross_validate(
    side: str,
    d2: Mapping[str, Any],
    d3: Mapping[str, Any],
    d4: Mapping[str, Any],
    metric: Mapping[str, Any],
) -> None:
    d4_inputs = _require_mapping(d4.get("inputs"), side + " D4 inputs")
    d4_d2 = _require_mapping(d4_inputs.get("d2"), side + " D4 inputs.d2")
    d4_d3 = _require_mapping(d4_inputs.get("d3_artifacts"), side + " D4 inputs.d3_artifacts")
    if d4_d2.get("candidate_fingerprint_sha256") != d2["_validated_fingerprint"]:
        raise ReproducibilityError("{} D4 未绑定当前 D2 candidate".format(side))
    if d4_d2.get("manifest_sha256") != d2["_validated_manifest_sha256"]:
        raise ReproducibilityError("{} D4 未绑定当前 D2 manifest SHA256".format(side))
    if d4_d3.get("manifest_fingerprint_sha256") != d3["_validated_fingerprint"]:
        raise ReproducibilityError("{} D4 未绑定当前 D3 manifest".format(side))
    if d4_d3.get("manifest_sha256") != d3["_validated_manifest_sha256"]:
        raise ReproducibilityError("{} D4 未绑定当前 D3 manifest SHA256".format(side))
    if d4_d3.get("summary_sha256") != d3["_validated_summary_sha256"]:
        raise ReproducibilityError("{} D4 未绑定当前 D3 summary SHA256".format(side))
    d2_admission = _require_mapping(d2.get("admission"), side + " D2 admission")
    d2_sample = _require_mapping(d2.get("sample"), side + " D2 sample")
    expected_d4_d2 = {
        "manifest_sha256": d2["_validated_manifest_sha256"],
        "candidate_fingerprint_sha256": d2["_validated_fingerprint"],
        "admission_status": d2_admission.get("status"),
        "sample_enabled": d2_sample.get("enabled"),
        "sha256_closure": "passed",
        "content_hash_closure": "passed",
    }
    if dict(d4_d2) != expected_d4_d2:
        raise ReproducibilityError("{} D4 inputs.d2 未按当前 D2 派生".format(side))
    raw_source_status, update_coverage = _d3_update_projection_for_d4(
        d3, side + " D3"
    )
    expected_d4_d3 = {
        "manifest_sha256": d3["_validated_manifest_sha256"],
        "summary_sha256": d3["_validated_summary_sha256"],
        "manifest_fingerprint_sha256": d3["_validated_fingerprint"],
        "raw_source_status": raw_source_status,
        "update_coverage": update_coverage,
        "sha256_closure": "passed",
        "verification_status": "verified",
    }
    if dict(d4_d3) != expected_d4_d3:
        raise ReproducibilityError("{} D4 inputs.d3_artifacts 未按当前 D3 派生".format(side))
    if _canonical_bytes(d4.get("data_profile")) != _canonical_bytes(d2.get("data_profile")):
        raise ReproducibilityError("{} D4 data_profile 未完整继承当前 D2".format(side))
    sources = _require_mapping(metric.get("sources"), side + " Metric sources")
    metric_d2 = _require_mapping(sources.get("d2_normalization"), side + " Metric D2 source")
    metric_d3 = _require_mapping(sources.get("d3_artifacts"), side + " Metric D3 source")
    if metric_d2.get("fingerprint_sha256") != d2["_validated_fingerprint"]:
        raise ReproducibilityError("{} Metric 未绑定当前 D2 candidate".format(side))
    if (
        metric_d2.get("manifest_sha256") != d2["_validated_manifest_sha256"]
        or metric_d2.get("checksums_sha256") != d2["_validated_checksums_sha256"]
        or metric_d2.get("incidents_sha256") != d2["_validated_incidents_sha256"]
    ):
        raise ReproducibilityError("{} Metric 未闭合当前 D2 文件身份".format(side))
    if metric_d3.get("fingerprint_sha256") != d3["_validated_fingerprint"]:
        raise ReproducibilityError("{} Metric 未绑定当前 D3 manifest".format(side))
    if (
        metric_d3.get("manifest_sha256") != d3["_validated_manifest_sha256"]
        or metric_d3.get("summary_sha256") != d3["_validated_summary_sha256"]
        or metric_d3.get("checksums_sha256") != d3["_validated_checksums_sha256"]
    ):
        raise ReproducibilityError("{} Metric 未闭合当前 D3 文件身份".format(side))
    profiles = [d2.get("data_profile"), d3.get("data_profile"), d4.get("data_profile"), metric.get("data_profile")]
    profile_keys = ("id", "timezone", "window_start", "window_end_exclusive")
    normalized = []
    for index, value in enumerate(profiles):
        profile = _require_mapping(value, "{} data_profile {}".format(side, index + 1))
        identity = {key: profile.get(key) for key in profile_keys}
        if any(not isinstance(identity[key], str) or not identity[key] for key in profile_keys):
            raise ReproducibilityError("{} data_profile 身份字段不完整".format(side))
        normalized.append(_canonical_bytes(identity))
    if len(set(normalized)) != 1:
        raise ReproducibilityError("{} 四类候选 data_profile 不一致".format(side))


def _comparison_rows(first: Mapping[str, int], second: Mapping[str, int]) -> Dict[str, Any]:
    if set(first) != set(second):
        raise ReproducibilityError("A/B 记录计数维度不一致")
    return {
        key: {"a": first[key], "b": second[key], "match": first[key] == second[key]}
        for key in sorted(first)
    }


def _public_manifest(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: item for key, item in value.items() if not key.startswith("_validated_")}


def _build_summary(
    components: Mapping[str, Tuple[VerifiedDirectory, VerifiedDirectory]],
    staging: Path,
    *,
    d2_record_limit: Optional[int] = None,
) -> Dict[str, Any]:
    stable = StableIdIndex(staging / ".stable-id-audit.sqlite3")
    try:
        d2_a, d2_count_a = _validate_d2(
            components["d2"][0], "a", stable, record_limit=d2_record_limit
        )
        d2_b, d2_count_b = _validate_d2(
            components["d2"][1], "b", stable, record_limit=d2_record_limit
        )
        d3_a, d3_count_a = _validate_d3(components["d3"][0], "a", stable)
        d3_b, d3_count_b = _validate_d3(components["d3"][1], "b", stable)
        d4_a, d4_count_a, d4_summary_a = _validate_d4(components["d4"][0], "a", stable)
        d4_b, d4_count_b, d4_summary_b = _validate_d4(components["d4"][1], "b", stable)
        metric_a, metric_count_a, metric_summary_a = _validate_metric(
            components["metric"][0], "a", stable
        )
        metric_b, metric_count_b, metric_summary_b = _validate_metric(
            components["metric"][1], "b", stable
        )

        _assert_same_input("D2", _d2_identity(d2_a), _d2_identity(d2_b))
        _assert_same_input("D3", _d3_identity(d3_a), _d3_identity(d3_b))
        _assert_same_input("D4", _d4_identity(d4_a), _d4_identity(d4_b))
        _assert_same_input("Metric", _metric_identity(metric_a), _metric_identity(metric_b))
        _cross_validate("A", d2_a, d3_a, d4_a, metric_a)
        _cross_validate("B", d2_b, d3_b, d4_b, metric_b)

        route_a = route_b = None
        route_count_a = route_count_b = None
        if "route" in components:
            route_a, route_count_a = _validate_route(components["route"][0], "a", stable)
            route_b, route_count_b = _validate_route(components["route"][1], "b", stable)
            _assert_same_input("RouteEvent", _route_identity(route_a), _route_identity(route_b))
            if route_a.get("manifest_fingerprint_sha256") != d3_a["_validated_fingerprint"]:
                raise ReproducibilityError("RouteEvent A 未绑定当前 D3 manifest")
            if route_b.get("manifest_fingerprint_sha256") != d3_b["_validated_fingerprint"]:
                raise ReproducibilityError("RouteEvent B 未绑定当前 D3 manifest")

        stable_summary = stable.summary()
        record_counts = {
            "d2": _comparison_rows(d2_count_a, d2_count_b),
            "d3": _comparison_rows(d3_count_a, d3_count_b),
            "d4": _comparison_rows(d4_count_a, d4_count_b),
            "metric": _comparison_rows(metric_count_a, metric_count_b),
        }
        if route_count_a is not None and route_count_b is not None:
            record_counts["route_event"] = _comparison_rows(route_count_a, route_count_b)
        record_count_match = all(
            row["match"]
            for component in record_counts.values()
            for row in component.values()
        )
        aggregate_matches = {
            "d2_summary": _canonical_bytes(d2_a.get("summary")) == _canonical_bytes(d2_b.get("summary")),
            "d3_summary_and_coverage": _canonical_bytes(
                {"summary": d3_a.get("summary"), "coverage": d3_a.get("coverage")}
            )
            == _canonical_bytes({"summary": d3_b.get("summary"), "coverage": d3_b.get("coverage")}),
            "evidence_reconciliation": _canonical_bytes(d4_summary_a) == _canonical_bytes(d4_summary_b),
            "metric_manifest_and_reconciliation": _canonical_bytes(
                {"summary": metric_a.get("summary"), "reconciliation": metric_summary_a}
            )
            == _canonical_bytes(
                {"summary": metric_b.get("summary"), "reconciliation": metric_summary_b}
            ),
        }
        if route_a is not None and route_b is not None:
            aggregate_matches["route_event_summary"] = _canonical_bytes(_public_manifest(route_a)) == _canonical_bytes(_public_manifest(route_b))

        fingerprint_values = {
            "d2": {
                "a": d2_a["_validated_fingerprint"],
                "b": d2_b["_validated_fingerprint"],
                "file_inventory_match": _canonical_bytes(d2_a.get("files"))
                == _canonical_bytes(d2_b.get("files")),
            },
            "d3": {"a": d3_a["_validated_fingerprint"], "b": d3_b["_validated_fingerprint"]},
            "evidence": {
                "a": d4_a["_validated_fingerprint"],
                "b": d4_b["_validated_fingerprint"],
                "reconciliation_a": d4_a["_validated_reconciliation_fingerprint"],
                "reconciliation_b": d4_b["_validated_reconciliation_fingerprint"],
            },
            "metric": {
                "a": metric_a["_validated_fingerprint"],
                "b": metric_b["_validated_fingerprint"],
                "reconciliation_a": metric_a["_validated_reconciliation_fingerprint"],
                "reconciliation_b": metric_b["_validated_reconciliation_fingerprint"],
            },
        }
        if route_a is not None and route_b is not None:
            fingerprint_values["route_event"] = {
                "a": route_a["_validated_fingerprint"],
                "b": route_b["_validated_fingerprint"],
            }

        byte_components = {}
        for name, pair in sorted(components.items()):
            names = set(pair[0].checksums) | set(pair[1].checksums)
            mismatches = [
                filename
                for filename in sorted(names)
                if pair[0].checksums.get(filename) != pair[1].checksums.get(filename)
            ]
            byte_components[name] = {
                "a_sha256sums_sha256": pair[0].checksum_sha256,
                "b_sha256sums_sha256": pair[1].checksum_sha256,
                "a_signed_file_count": len(pair[0].checksums),
                "b_signed_file_count": len(pair[1].checksums),
                "a_signed_size_bytes": sum(
                    pair[0].file_identities[item][3] for item in pair[0].checksums
                ),
                "b_signed_size_bytes": sum(
                    pair[1].file_identities[item][3] for item in pair[1].checksums
                ),
                "sha256sums_bytes_match": pair[0].checksum_payload
                == pair[1].checksum_payload,
                "mismatch_count": len(mismatches),
                "mismatched_files": mismatches,
            }
        byte_identity_match = all(
            row["mismatch_count"] == 0 and row["sha256sums_bytes_match"] is True
            for row in byte_components.values()
        )
        sampled = d2_record_limit is not None
        fingerprint_matches = {
            "d2": fingerprint_values["d2"]["a"] == fingerprint_values["d2"]["b"]
            and fingerprint_values["d2"]["file_inventory_match"],
            "d3": fingerprint_values["d3"]["a"] == fingerprint_values["d3"]["b"],
            "evidence": fingerprint_values["evidence"]["a"]
            == fingerprint_values["evidence"]["b"]
            and fingerprint_values["evidence"]["reconciliation_a"]
            == fingerprint_values["evidence"]["reconciliation_b"],
            "metric": fingerprint_values["metric"]["a"]
            == fingerprint_values["metric"]["b"]
            and fingerprint_values["metric"]["reconciliation_a"]
            == fingerprint_values["metric"]["reconciliation_b"],
        }
        if route_a is not None and route_b is not None:
            fingerprint_matches["route_event"] = (
                fingerprint_values["route_event"]["a"]
                == fingerprint_values["route_event"]["b"]
            )
        semantic_result_values = [
            stable_summary["match_ratio"] == 1,
            record_count_match,
            all(aggregate_matches.values()),
            *fingerprint_matches.values(),
        ]
        semantic_match = all(semantic_result_values)
        plan: Dict[str, Any] = {
            "plan_version": "p0_bounded_semantic_comparison_v1",
            "d2": {
                "selector": "canonical_jsonl_prefix_per_stream"
                if sampled
                else "full_jsonl",
                "max_records_per_stream": d2_record_limit,
            },
            "d3": {"scope": "full_manifest_metadata", "raw_mrt_read": False},
            "d4": {"scope": "all_six_event_candidate_bundles"},
            "metric": {"scope": "full_emitted_metric_candidate"},
            "route_event": {
                "scope": "full_bounded_pilot" if route_a is not None else "not_provided"
            },
        }
        plan["plan_sha256"] = _canonical_sha256(
            {"schema": "p0_bounded_semantic_comparison_plan_v1", "plan": plan}
        )
        d2_sample_comparison = {}
        if sampled:
            sample_counts_a = d2_a["_validated_sample_counts"]
            sample_counts_b = d2_b["_validated_sample_counts"]
            sample_sha_a = d2_a["_validated_sample_content_sha256"]
            sample_sha_b = d2_b["_validated_sample_content_sha256"]
            for name in sorted(sample_counts_a):
                d2_sample_comparison[name] = {
                    "a_selected_count": sample_counts_a[name],
                    "b_selected_count": sample_counts_b[name],
                    "a_content_sha256": sample_sha_a[name],
                    "b_content_sha256": sample_sha_b[name],
                    "match": sample_counts_a[name] == sample_counts_b[name]
                    and sample_sha_a[name] == sample_sha_b[name],
                }
        result: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "comparison_mode": "existing_candidates_full_sha_plus_deterministic_semantic_sample_v1"
            if sampled
            else "existing_candidates_full_sha_plus_full_semantic_validation_v1",
            "execution_scope": {
                "candidates_regenerated": False,
                "source_database_access": "none",
                "source_database_connection_attempts": 0,
                "source_database_write_operations": 0,
                "raw_mrt_access": "none",
            },
            "external_cross_run_reproducibility": {
                "method": "independent_candidate_directories_a_b_v1",
                "metric_internal_roundtrip_used_as_cross_run_evidence": False,
                "candidates_regenerated_in_this_execution": False,
                "note": "本次只比较既有 A/B 候选；独立生成身份由原执行日志旁证，本摘要不声称重新生成候选。",
            },
            "local_ephemeral_working_store": {
                "engine": "sqlite",
                "purpose": "typed_stable_id_set_comparison_only",
                "published": False,
                "removed_before_output": True,
            },
            "input_closure": {
                name: {
                    "a_sha256sums_sha256": pair[0].checksum_sha256,
                    "b_sha256sums_sha256": pair[1].checksum_sha256,
                    "a_file_count": len(pair[0].checksums),
                    "b_file_count": len(pair[1].checksums),
                    "verified": True,
                }
                for name, pair in sorted(components.items())
            },
            "byte_identity": {
                "scope": "full_artifact_closure",
                "all_files_rehashed": True,
                "all_corresponding_files_match": byte_identity_match,
                "components": byte_components,
            },
            "semantic_validation": {
                "mode": "deterministic_bounded_sample_v1"
                if sampled
                else "full_population_v1",
                "sample_only": sampled,
                "population_coverage_claimed": not sampled,
                "plan": plan,
                "d2_sample_comparison": d2_sample_comparison,
                "stable_id_scope": stable_summary,
                "stable_id_match_ratio": stable_summary["match_ratio"],
                "record_counts": record_counts,
                "record_count_metadata_match": record_count_match,
                "aggregate_summary_matches": aggregate_matches,
                "aggregate_summary_match": all(aggregate_matches.values()),
                "fingerprints": fingerprint_values,
                "fingerprint_matches": fingerprint_matches,
                "failure_count": sum(value is not True for value in semantic_result_values),
                "all_results_match": semantic_match,
            },
            "full_semantic_validation": {
                "status": "not_run" if sampled else ("passed" if semantic_match else "failed"),
                "reason": "user_requested_bounded_sample"
                if sampled
                else "full_population_semantic_scan_executed",
            },
            "conclusion": {
                "byte_reproducibility_status": "passed" if byte_identity_match else "failed",
                "sampled_semantic_status": "passed" if semantic_match else "failed",
                "full_semantic_reproducibility_status": "not_run"
                if sampled
                else ("passed" if semantic_match else "failed"),
            },
            "classification": "observation_only",
            "causal_conclusion": None,
        }
        return result
    finally:
        stable.close()
        try:
            (staging / ".stable-id-audit.sqlite3").unlink()
        except FileNotFoundError:
            pass


def _summary_markdown(summary: Mapping[str, Any]) -> str:
    semantic = summary["semantic_validation"]
    conclusion = summary["conclusion"]
    return """# P0 可复现性重跑摘要

## 结论

- 全制品字节复现：`{byte_status}`
- 有界语义复核：`{semantic_status}`
- 稳定 ID 一致率：`{stable}`
- 全量语义复现：`{full_status}`
- 语义复核范围：`{scope}`

## 证据边界

本摘要只比较已经由各自 `SHA256SUMS` 完整覆盖的既有 A/B 候选制品，不在
本次执行中重新生成候选。若范围为 `deterministic_bounded_sample_v1`，D2
稳定 ID 与逐行 JSON 语义只检查各流的
冻结前缀样本；文件 SHA、manifest 指纹、聚合摘要及其他明确标注的小型候选
仍按摘要中的范围复核，不得据此声明全量语义复现。稳定 ID
一致率使用带类型的 ID 集合 Jaccard 比实际计算；记录数来自压缩 JSONL、
manifest artifact、Evidence registry、Metric points 及可选 SQLite 的只读复核。
Metric 自身的内存/落盘重读只作为单次运行内部证据，不替代这里的外部 A/B
跨运行比较。
稳定 ID 集合只写入输出 staging 内的临时 SQLite，生成摘要前即删除；程序未
连接来源数据库、未修改输入，也不把可复现性解释为因果证据。
""".format(
        byte_status=conclusion["byte_reproducibility_status"],
        semantic_status=conclusion["sampled_semantic_status"],
        stable=semantic["stable_id_match_ratio"],
        full_status=conclusion["full_semantic_reproducibility_status"],
        scope=semantic["mode"],
    )


def _atomic_write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run(args: argparse.Namespace) -> Dict[str, Any]:
    route_values = (args.route_a, args.route_b)
    if bool(route_values[0]) != bool(route_values[1]):
        raise ReproducibilityError("RouteEvent A/B 必须同时提供")
    target = Path(args.output_dir).absolute()
    if target.exists() or target.is_symlink():
        raise ReproducibilityError("输出目录必须不存在，拒绝覆盖：{}".format(target))
    parent = target.parent
    _lstat_directory(parent, "输出父目录")
    staging = parent / ".{}.tmp.{}.{}".format(target.name, os.getpid(), secrets.token_hex(4))
    staging.mkdir(mode=0o750)
    completed = False
    directories = []
    try:
        paths = {
            "d2": (args.d2_a, args.d2_b),
            "d3": (args.d3_a, args.d3_b),
            "d4": (args.d4_a, args.d4_b),
            "metric": (args.metric_a, args.metric_b),
        }
        if route_values[0]:
            paths["route"] = route_values
        components = {}
        seen_paths = set()
        seen_directory_ids = set()
        for name, pair in paths.items():
            verified_pair = []
            for side, raw_path in zip(("A", "B"), pair):
                path = Path(raw_path).absolute()
                normalized = os.path.normcase(str(path))
                if normalized in seen_paths:
                    raise ReproducibilityError("A/B 或组件目录不得复用同一路径：{}".format(path))
                seen_paths.add(normalized)
                verified = VerifiedDirectory(path, "{} {}".format(name, side))
                directory_id = verified.directory_identity[:2]
                if directory_id in seen_directory_ids:
                    raise ReproducibilityError("A/B 或组件目录不得复用同一目录 inode：{}".format(path))
                seen_directory_ids.add(directory_id)
                directories.append(verified)
                verified_pair.append(verified)
            components[name] = (verified_pair[0], verified_pair[1])
        d2_record_limit = getattr(args, "d2_record_limit", None)
        if d2_record_limit is not None and (
            isinstance(d2_record_limit, bool)
            or not isinstance(d2_record_limit, int)
            or d2_record_limit < 1
        ):
            raise ReproducibilityError("D2 抽样上限必须为正整数")
        summary = _build_summary(
            components, staging, d2_record_limit=d2_record_limit
        )
        for directory in directories:
            directory.verify_unchanged()
        json_payload = _canonical_bytes(summary)
        markdown_payload = _summary_markdown(summary).encode("utf-8")
        _atomic_write_new(staging / OUTPUT_JSON, json_payload)
        _atomic_write_new(staging / OUTPUT_SUMMARY, markdown_payload)
        checksum_payload = (
            "{}  {}\n{}  {}\n".format(
                hashlib.sha256(json_payload).hexdigest(),
                OUTPUT_JSON,
                hashlib.sha256(markdown_payload).hexdigest(),
                OUTPUT_SUMMARY,
            )
        ).encode("utf-8")
        _atomic_write_new(staging / OUTPUT_CHECKSUMS, checksum_payload)
        for path in staging.iterdir():
            if not path.is_file() or path.is_symlink():
                raise ReproducibilityError("输出 staging 出现非普通文件")
            path.chmod(0o440)
        if target.exists() or target.is_symlink():
            raise ReproducibilityError("发布前输出目录已出现，拒绝覆盖")
        staging.rename(target)
        _fsync_directory(parent)
        completed = True
        return summary
    finally:
        if not completed and staging.exists():
            shutil.rmtree(staging)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 P0 A/B 候选制品可复现性摘要")
    for component in ("d2", "d3", "d4", "metric"):
        parser.add_argument("--{}-a".format(component), required=True)
        parser.add_argument("--{}-b".format(component), required=True)
    parser.add_argument("--route-a")
    parser.add_argument("--route-b")
    parser.add_argument(
        "--d2-record-limit",
        type=int,
        help="只做 D2 各 JSONL 确定性前缀语义抽样；省略时执行全量语义扫描",
    )
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except Exception as error:
        print("错误：{}".format(error), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "字节复现": result["conclusion"]["byte_reproducibility_status"],
                "语义复核范围": result["semantic_validation"]["mode"],
                "有界语义复核": result["conclusion"]["sampled_semantic_status"],
                "全量语义复现": result["conclusion"][
                    "full_semantic_reproducibility_status"
                ],
                "schema_version": result["schema_version"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
