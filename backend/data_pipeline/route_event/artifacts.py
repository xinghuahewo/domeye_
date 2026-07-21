"""P0 原始 MRT 制品的只读、确定性文件级清单。

本模块读取压缩文件字节，并把压缩流完整解码到 EOF 以校验容器完整性；
不解释 MRT/BGP 语义。调用方必须显式给出扫描根目录、固定数据档和
collector allowlist。目录布局约定为
``<raw_root>/<collector>/.../<artifact-file>``；扫描结果不包含绝对路径或
运行时钟，因此同一输入可在不同只读挂载点得到相同 fingerprint。
"""

from __future__ import annotations

import bz2
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import zlib
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UTC = timezone.utc
MANIFEST_SCHEMA_VERSION = 1
MANIFEST_KIND = "mrt_artifact_manifest"
ARTIFACT_ID_SCHEMA = "artifact_id_v1"
FINGERPRINT_SCHEMA = "mrt_artifact_manifest_fingerprint_v1"
SELECTION_SCHEMA_VERSION = 1
SELECTION_KIND = "mrt_update_pilot_selection"
SELECTION_FINGERPRINT_SCHEMA = "mrt_update_pilot_selection_fingerprint_v1"

# P0 只允许可快速回滚、不会被误认成全量生产索引的有界 pilot。调用方可以
# 选择更小的值，但不能通过参数把这些绝对上限放大。
PILOT_ABSOLUTE_MAX_ARTIFACTS = 5
PILOT_ABSOLUTE_MAX_COMPRESSED_BYTES = 3 * 1024 * 1024 * 1024
PILOT_ABSOLUTE_MAX_PHYSICAL_RECORDS = 2_000_000
PILOT_ABSOLUTE_MAX_ROUTE_EVENTS = 5_000_000
PILOT_ABSOLUTE_MAX_SPOOL_BYTES = 16 * 1024 * 1024 * 1024

COLLECTOR_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ARTIFACT_FILE_RE = re.compile(
    r"^(?P<family>updates|bview|rib)\."
    r"(?P<date>[0-9]{8})\."
    r"(?P<time>[0-9]{4})\."
    r"(?P<compression>gz|bz2)$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SLOT_INTERVALS = {
    "update": timedelta(minutes=5),
    "rib": timedelta(hours=8),
}
COMPRESSION_MAGIC = {
    "gz": b"\x1f\x8b",
    "bz2": b"BZh",
}
INVALID_IN_WINDOW_VALUE_STATE = "parse_failed"
INVALID_IN_WINDOW_MISSING_REASONS = frozenset(
    {
        "compressed_stream_invalid",
        "empty_file",
        "compression_magic_mismatch",
    }
)
DUPLICATE_CONTENT_POLICY = {
    "valid_artifact": "reject_across_paths",
    "invalid_compressed_stream_invalid": "reject_across_paths",
    "invalid_empty_file": "allow_across_unique_paths_and_slots",
    "invalid_compression_magic_mismatch": "reject_across_paths",
}
DIRECTORY_SCOPE_BASIS = (
    "utc_month_directories_intersecting_half_open_profile_window"
)


class ArtifactManifestError(ValueError):
    """扫描输入、目录内容或 manifest 不符合 P0 合同。"""


class ArtifactIntegrityError(ArtifactManifestError):
    """已记录 manifest 与当前原始文件不一致。"""


class _CompressedStreamInvalidError(Exception):
    """压缩容器内容无效；与真实文件系统 I/O 错误分开处理。"""


def canonical_json(value: Any) -> str:
    """输出稳定、无空白、禁止 NaN 的 UTF-8 JSON 文本。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def artifact_id_v1(file_sha256: str) -> str:
    """按 ``artifact_id_v1`` identity 由完整文件 SHA256 生成稳定 ID。"""

    if not isinstance(file_sha256, str) or not SHA256_RE.fullmatch(file_sha256):
        raise ArtifactManifestError("file_sha256 必须是 64 位小写十六进制")
    identity = {
        "schema": ARTIFACT_ID_SCHEMA,
        "file_sha256": file_sha256,
    }
    digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return "art_v1_" + digest[:32]


def _parse_profile_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        raise ArtifactManifestError(f"{field} 必须是带时区的 ISO 8601 秒级时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ArtifactManifestError(f"{field} 不是有效时间") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond:
        raise ArtifactManifestError(f"{field} 必须带时区且只能精确到秒")
    return parsed


def _normalize_profile(data_profile: Mapping[str, Any]) -> Tuple[Dict[str, str], datetime, datetime]:
    if not isinstance(data_profile, Mapping):
        raise ArtifactManifestError("data_profile 必须是映射")
    required = ("id", "timezone", "window_start", "window_end_exclusive")
    missing = [field for field in required if field not in data_profile]
    if missing:
        raise ArtifactManifestError("data_profile 缺少字段：" + ",".join(missing))
    if not isinstance(data_profile["id"], str) or not data_profile["id"]:
        raise ArtifactManifestError("data_profile.id 不能为空")
    if not isinstance(data_profile["timezone"], str) or not data_profile["timezone"]:
        raise ArtifactManifestError("data_profile.timezone 不能为空")
    try:
        profile_timezone = ZoneInfo(data_profile["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ArtifactManifestError("data_profile.timezone 不是有效 IANA 时区") from error
    start = _parse_profile_time(data_profile["window_start"], "window_start")
    end = _parse_profile_time(data_profile["window_end_exclusive"], "window_end_exclusive")
    if start >= end:
        raise ArtifactManifestError("数据档窗口起点必须早于终点")
    if start.utcoffset() != start.astimezone(profile_timezone).utcoffset():
        raise ArtifactManifestError("window_start 偏移与 data_profile.timezone 不一致")
    if end.utcoffset() != end.astimezone(profile_timezone).utcoffset():
        raise ArtifactManifestError("window_end_exclusive 偏移与 data_profile.timezone 不一致")
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    normalized = {
        "id": data_profile["id"],
        "timezone": data_profile["timezone"],
        "window_start": data_profile["window_start"],
        "window_end_exclusive": data_profile["window_end_exclusive"],
        "window_start_utc": _utc_text(start_utc),
        "window_end_exclusive_utc": _utc_text(end_utc),
    }
    return normalized, start_utc, end_utc


def _normalize_collectors(collector_allowlist: Sequence[str]) -> Tuple[str, ...]:
    if isinstance(collector_allowlist, (str, bytes)) or not isinstance(
        collector_allowlist, Sequence
    ):
        raise ArtifactManifestError("collector_allowlist 必须是显式字符串序列")
    collectors = tuple(collector_allowlist)
    if not collectors:
        raise ArtifactManifestError("collector_allowlist 不能为空")
    if any(not isinstance(value, str) or not COLLECTOR_RE.fullmatch(value) for value in collectors):
        raise ArtifactManifestError("collector_allowlist 含非法 collector ID")
    if len(set(collectors)) != len(collectors):
        raise ArtifactManifestError("collector_allowlist 不得重复")
    return tuple(sorted(collectors))


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_month_directories(start: datetime, end: datetime) -> Tuple[str, ...]:
    """返回与 ``[start,end)`` 相交的有限 UTC 月目录集合。"""

    current = datetime(start.year, start.month, 1, tzinfo=UTC)
    months = []
    while current < end:
        months.append(current.strftime("%Y.%m"))
        current = (
            datetime(current.year + 1, 1, 1, tzinfo=UTC)
            if current.month == 12
            else datetime(current.year, current.month + 1, 1, tzinfo=UTC)
        )
    return tuple(months)


def _directory_scope_policy(start: datetime, end: datetime) -> Dict[str, Any]:
    return {
        "basis": DIRECTORY_SCOPE_BASIS,
        "included_month_directories": list(_utc_month_directories(start, end)),
        "missing_included_month_directory": "treat_as_empty",
        "other_month_directories": "excluded_without_inventory",
        "filename_utc_month_must_match_directory": True,
    }


def _parse_filename(filename: str) -> Dict[str, Any]:
    matched = ARTIFACT_FILE_RE.fullmatch(filename)
    if matched is None:
        raise ArtifactManifestError(f"未知原始制品命名：{filename}")
    try:
        slot_time = datetime.strptime(
            matched.group("date") + matched.group("time"), "%Y%m%d%H%M"
        ).replace(tzinfo=UTC)
    except ValueError as error:
        raise ArtifactManifestError(f"原始制品时间非法：{filename}") from error

    family = matched.group("family")
    artifact_type = "update" if family == "updates" else "rib"
    interval = SLOT_INTERVALS[artifact_type]
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    if (slot_time - epoch) % interval:
        raise ArtifactManifestError(f"原始制品时间未对齐 {artifact_type} 槽：{filename}")
    return {
        "artifact_type": artifact_type,
        "filename_family": family,
        "artifact_time": slot_time,
        "compression": matched.group("compression"),
    }


def _assert_directory(path: Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise ArtifactManifestError(f"无法读取{label}：{path}") from error
    if stat.S_ISLNK(mode):
        raise ArtifactManifestError(f"{label}不得是符号链接：{path}")
    if not stat.S_ISDIR(mode):
        raise ArtifactManifestError(f"{label}必须是目录：{path}")


def _optional_directory(path: Path, label: str) -> bool:
    """检查有限 scope 目录；不存在等价于该月无文件，其他错误硬失败。"""

    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ArtifactManifestError(f"无法读取{label}：{path}") from error
    if stat.S_ISLNK(mode):
        raise ArtifactManifestError(f"{label}不得是符号链接：{path}")
    if not stat.S_ISDIR(mode):
        raise ArtifactManifestError(f"{label}必须是目录：{path}")
    return True


def _iter_regular_files(collector_root: Path) -> Iterable[Path]:
    """确定性遍历 collector；拒绝链接和所有非普通文件。"""

    def fail_walk(error: OSError) -> None:
        raise ArtifactManifestError(f"无法遍历 collector 目录：{collector_root}") from error

    for current, dirnames, filenames in os.walk(
        collector_root, topdown=True, onerror=fail_walk, followlinks=False
    ):
        current_path = Path(current)
        dirnames.sort()
        filenames.sort()
        for dirname in dirnames:
            child = current_path / dirname
            try:
                mode = child.lstat().st_mode
            except OSError as error:
                raise ArtifactManifestError(f"无法读取目录条目：{child}") from error
            if stat.S_ISLNK(mode):
                raise ArtifactManifestError(f"原始目录内禁止符号链接：{child}")
            if not stat.S_ISDIR(mode):
                raise ArtifactManifestError(f"原始目录内存在非目录条目：{child}")
        for filename in filenames:
            path = current_path / filename
            try:
                mode = path.lstat().st_mode
            except OSError as error:
                raise ArtifactManifestError(f"无法读取文件条目：{path}") from error
            if stat.S_ISLNK(mode):
                raise ArtifactManifestError(f"原始目录内禁止符号链接：{path}")
            if not stat.S_ISREG(mode):
                raise ArtifactManifestError(f"原始目录内仅允许普通文件：{path}")
            yield path


def _file_snapshot(metadata: os.stat_result) -> Tuple[int, int, int, int, int]:
    return tuple(
        getattr(metadata, field)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    )


def _hash_regular_file(
    path: Path, compression: str
) -> Tuple[str, int, str | None, Tuple[int, int, int, int, int]]:
    """完整哈希普通文件，并区分身份错误与可隔离的内容错误。

    文件为空或压缩 magic 与扩展名不符都属于窗口内已发现、但内容不可供
    下游解析的制品。二者仍必须读到 EOF、完成 SHA256 并通过同一 TOCTOU
    检查；文件类型变化、读取错误或扫描中变化仍硬失败。
    """

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ArtifactManifestError(f"无法只读打开原始制品：{path}") from error
    digest = hashlib.sha256()
    prefix = bytearray()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ArtifactManifestError(f"原始制品不是普通文件：{path}")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            if len(prefix) < 3:
                prefix.extend(block[: 3 - len(prefix)])
            digest.update(block)
        after = os.fstat(descriptor)
    except OSError as error:
        raise ArtifactManifestError(f"读取原始制品失败：{path}") from error
    finally:
        os.close(descriptor)
    before_snapshot = _file_snapshot(before)
    after_snapshot = _file_snapshot(after)
    if before_snapshot != after_snapshot:
        raise ArtifactManifestError(f"扫描期间原始制品发生变化：{path}")
    if after.st_size == 0:
        missing_reason = "empty_file"
    elif not bytes(prefix).startswith(COMPRESSION_MAGIC[compression]):
        missing_reason = "compression_magic_mismatch"
    else:
        missing_reason = None
    return digest.hexdigest(), after.st_size, missing_reason, after_snapshot


def _consume_bz2_members(raw: Any, *, block_size: int = 1024 * 1024) -> None:
    """有界内存解码所有 bzip2 member，并拒绝 member 后的非容器字节。"""

    decompressor = bz2.BZ2Decompressor()
    pending = b""
    while True:
        if decompressor.needs_input:
            if pending:
                block = pending
                pending = b""
            else:
                block = raw.read(block_size)
                if not block:
                    raise _CompressedStreamInvalidError("bzip2 member 在 EOF 前未结束")
        else:
            # max_length 可能让解压器保留输入；空输入会继续排空内部缓冲，
            # 避免为高压缩比 member 一次分配全部解压结果。
            block = b""
        try:
            decompressor.decompress(block, max_length=block_size)
        except OSError as error:
            raise _CompressedStreamInvalidError("bzip2 member 内容无效") from error
        if not decompressor.eof:
            continue

        pending = decompressor.unused_data
        if not pending:
            pending = raw.read(block_size)
            if not pending:
                return
        # BZ2Decompressor 只处理一个 member；任何剩余字节都必须能完整构成
        # 下一个 member。垃圾、残缺 header 或截断 member 最终都会失败。
        decompressor = bz2.BZ2Decompressor()


def _validate_compressed_stream(
    path: Path,
    compression: str,
    expected_snapshot: Tuple[int, int, int, int, int],
) -> str | None:
    """流式读到压缩尾并校验 CRC/trailer，不解释 MRT/BGP 语义。"""

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ArtifactManifestError(f"无法只读打开原始制品：{path}") from error
    invalid = False
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ArtifactManifestError(f"原始制品不是普通文件：{path}")
        if _file_snapshot(before) != expected_snapshot:
            raise ArtifactManifestError(f"完整性校验前原始制品发生变化：{path}")
        with os.fdopen(os.dup(descriptor), "rb") as raw:
            if compression == "bz2":
                try:
                    _consume_bz2_members(raw)
                except _CompressedStreamInvalidError:
                    invalid = True
                except OSError as error:
                    raise ArtifactManifestError(
                        f"读取原始制品压缩流失败：{path}"
                    ) from error
            else:
                compressed = gzip.GzipFile(fileobj=raw, mode="rb")
                try:
                    while compressed.read(1024 * 1024):
                        pass
                except (EOFError, gzip.BadGzipFile, OSError, zlib.error) as error:
                    # 压缩库对 CRC、trailer、EOF、deflate 数据错误均使用
                    # 无 errno 的异常；真实文件系统 I/O 错误继续硬失败。
                    if isinstance(error, OSError) and error.errno is not None:
                        raise ArtifactManifestError(
                            f"读取原始制品压缩流失败：{path}"
                        ) from error
                    invalid = True
                finally:
                    compressed.close()
        after = os.fstat(descriptor)
        if _file_snapshot(after) != expected_snapshot:
            raise ArtifactManifestError(f"完整性校验期间原始制品发生变化：{path}")
    except OSError as error:
        if isinstance(error, ArtifactManifestError):
            raise
        raise ArtifactManifestError(f"读取原始制品失败：{path}") from error
    finally:
        os.close(descriptor)
    return "compressed_stream_invalid" if invalid else None


def _normalize_integrity_workers(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 32:
        raise ArtifactManifestError("integrity_workers 必须是 1..32 的整数")
    return value


def _ceil_slot(start: datetime, interval: timedelta) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = start - epoch
    remainder = elapsed % interval
    return start if not remainder else start + (interval - remainder)


def _expected_slots(start: datetime, end: datetime, interval: timedelta) -> Tuple[datetime, ...]:
    slots = []
    current = _ceil_slot(start, interval)
    while current < end:
        slots.append(current)
        current += interval
    return tuple(slots)


def _compress_missing(
    slots: Iterable[datetime],
    interval: timedelta,
    slot_states: Mapping[datetime, str],
) -> list[Dict[str, Any]]:
    ordered = sorted(set(slots))
    if not ordered:
        return []
    ranges = []
    range_start = ordered[0]
    previous = ordered[0]
    range_state = slot_states.get(range_start, "source_unavailable")
    count = 1
    for slot in ordered[1:]:
        state = slot_states.get(slot, "source_unavailable")
        if slot == previous + interval and state == range_state:
            previous = slot
            count += 1
            continue
        ranges.append(
            {
                "start_time_utc": _utc_text(range_start),
                "end_time_exclusive_utc": _utc_text(previous + interval),
                "slot_count": count,
                "value_state": range_state,
            }
        )
        range_start = previous = slot
        range_state = state
        count = 1
    ranges.append(
        {
            "start_time_utc": _utc_text(range_start),
            "end_time_exclusive_utc": _utc_text(previous + interval),
            "slot_count": count,
            "value_state": range_state,
        }
    )
    return ranges


def _coverage_record(
    expected: Sequence[datetime],
    available: Iterable[datetime],
    interval: timedelta,
    invalid_slots: Iterable[datetime],
) -> Dict[str, Any]:
    expected_set = set(expected)
    available_set = set(available)
    missing = expected_set - available_set
    invalid_set = set(invalid_slots)
    if not invalid_set.issubset(missing):
        raise ArtifactManifestError("无效制品槽必须属于 coverage 缺口")
    slot_states = {slot: "parse_failed" for slot in invalid_set}
    expected_count = len(expected_set)
    available_count = len(available_set)
    return {
        "expected_slots": expected_count,
        "available_slots": available_count,
        "missing_slots": len(missing),
        "coverage_ratio": round(available_count / expected_count, 8) if expected_count else 1.0,
        "coverage_status": "complete" if not missing else "partial",
        "missing_ranges": _compress_missing(missing, interval, slot_states),
    }


def _manifest_fingerprint(payload: Mapping[str, Any]) -> str:
    identity = {
        "schema": FINGERPRINT_SCHEMA,
        "manifest": payload,
    }
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def _selection_fingerprint(payload: Mapping[str, Any]) -> str:
    identity = {
        "schema": SELECTION_FINGERPRINT_SCHEMA,
        "selection": payload,
    }
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def scan_mrt_artifacts(
    raw_root: os.PathLike[str] | str,
    data_profile: Mapping[str, Any],
    collector_allowlist: Sequence[str],
    *,
    strict_out_of_window: bool = False,
    integrity_workers: int = 1,
) -> Dict[str, Any]:
    """扫描 allowlist collector 并返回不含运行时钟的确定性 manifest。

    仅遍历与固定半开窗口相交的 UTC 月目录；其他月份不枚举、不计数，明确
    excluded_without_inventory。所选月内合法命名但位于精确窗口外的普通文件
    默认只计数和记录边界样本，不读取内容，也不进入 artifact/coverage。
    审计场景可显式启用严格拒绝。
    """

    root = Path(raw_root)
    _assert_directory(root, "扫描根目录")
    if not isinstance(strict_out_of_window, bool):
        raise ArtifactManifestError("strict_out_of_window 必须是布尔值")
    workers = _normalize_integrity_workers(integrity_workers)
    profile, window_start, window_end = _normalize_profile(data_profile)
    collectors = _normalize_collectors(collector_allowlist)
    included_month_directories = _utc_month_directories(window_start, window_end)

    artifacts = []
    slots: Dict[Tuple[str, str], set[datetime]] = {
        (collector, artifact_type): set()
        for collector in collectors
        for artifact_type in SLOT_INTERVALS
    }
    slot_paths: Dict[Tuple[str, str, datetime], str] = {}
    artifact_paths: Dict[str, str] = {}
    content_paths: Dict[str, str] = {}
    excluded_out_of_window = []
    invalid_in_window = []
    artifact_snapshots: Dict[str, Tuple[int, int, int, int, int]] = {}

    for collector in collectors:
        collector_root = root / collector
        _assert_directory(collector_root, f"collector {collector} 目录")
        for month_name in included_month_directories:
            month_root = collector_root / month_name
            if not _optional_directory(
                month_root, f"collector {collector} UTC 月目录 {month_name}"
            ):
                continue
            for path in _iter_regular_files(month_root):
                parsed = _parse_filename(path.name)
                slot_time = parsed["artifact_time"]
                relative_path = path.relative_to(root).as_posix()
                if slot_time.strftime("%Y.%m") != month_name:
                    raise ArtifactManifestError(
                        "原始制品文件名 UTC 年月与所属月目录不一致："
                        f"{relative_path}"
                    )
                slot_key = (collector, parsed["artifact_type"], slot_time)
                if slot_key in slot_paths:
                    raise ArtifactManifestError(
                        "同一 collector/type/time 存在重复槽："
                        f"{slot_paths[slot_key]} 与 {relative_path}"
                    )
                slot_paths[slot_key] = relative_path
                if not window_start <= slot_time < window_end:
                    if strict_out_of_window:
                        raise ArtifactManifestError(
                            f"原始制品时间越出数据档窗口：{relative_path}"
                        )
                    try:
                        size_bytes = path.lstat().st_size
                    except OSError as error:
                        raise ArtifactManifestError(
                            f"无法读取窗口外制品元数据：{path}"
                        ) from error
                    excluded_out_of_window.append(
                        {
                            "collector_id": collector,
                            "artifact_type": parsed["artifact_type"],
                            "artifact_time_utc": _utc_text(slot_time),
                            "relative_path": relative_path,
                            "size_bytes": size_bytes,
                            "reason": "before_window"
                            if slot_time < window_start
                            else "at_or_after_window_end",
                        }
                    )
                    continue
                file_sha256, size_bytes, invalid_reason, file_snapshot = _hash_regular_file(
                    path, parsed["compression"]
                )
                artifact_id = artifact_id_v1(file_sha256)
                if invalid_reason is not None:
                    if invalid_reason not in INVALID_IN_WINDOW_MISSING_REASONS:
                        raise ArtifactManifestError("窗口内无效制品原因未被合同枚举")
                    # 空内容的 e3b0... 只说明“该槽没有字节”，不是可用制品身份；
                    # 每个唯一 path/slot 必须独立隔离记录。非空 magic mismatch
                    # 仍保留跨路径内容复用硬失败，避免同一错误载荷被伪装成多个
                    # 独立原始制品。该策略不依赖遍历先后顺序。
                    if invalid_reason != "empty_file":
                        if file_sha256 in content_paths:
                            raise ArtifactManifestError(
                                "不同路径复用同一有效或非空无效原始内容："
                                f"{content_paths[file_sha256]} 与 {relative_path}"
                            )
                        content_paths[file_sha256] = relative_path
                    invalid_in_window.append(
                        {
                            "collector_id": collector,
                            "artifact_type": parsed["artifact_type"],
                            "artifact_time_utc": _utc_text(slot_time),
                            "relative_path": relative_path,
                            "filename_family": parsed["filename_family"],
                            "compression": parsed["compression"],
                            "size_bytes": size_bytes,
                            "file_sha256": file_sha256,
                            "value_state": INVALID_IN_WINDOW_VALUE_STATE,
                            "missing_reason": invalid_reason,
                        }
                    )
                    continue
                if file_sha256 in content_paths:
                    raise ArtifactManifestError(
                        "不同路径复用同一有效或非空无效原始内容："
                        f"{content_paths[file_sha256]} 与 {relative_path}"
                    )
                content_paths[file_sha256] = relative_path
                if artifact_id in artifact_paths:
                    raise ArtifactManifestError(
                        "不同路径复用同一 artifact ID："
                        f"{artifact_paths[artifact_id]} 与 {relative_path}"
                    )
                artifact_paths[artifact_id] = relative_path
                slots[(collector, parsed["artifact_type"])].add(slot_time)
                artifact_snapshots[relative_path] = file_snapshot
                artifacts.append(
                    {
                        "artifact_id": artifact_id,
                        "artifact_id_schema": ARTIFACT_ID_SCHEMA,
                        "collector_id": collector,
                        "artifact_type": parsed["artifact_type"],
                        "artifact_time_utc": _utc_text(slot_time),
                        "relative_path": relative_path,
                        "filename_family": parsed["filename_family"],
                        "compression": parsed["compression"],
                        "size_bytes": size_bytes,
                        "file_sha256": file_sha256,
                    }
                )

    # 文件 SHA/magic 通过后仍必须把压缩流读到 EOF，才能确认 gzip CRC/trailer
    # 或 bzip2 等价完整性。并发数只影响执行速度，不进入 manifest 身份。
    def validate_artifact(artifact: Mapping[str, Any]) -> str | None:
        relative_path = artifact["relative_path"]
        return _validate_compressed_stream(
            root / PurePosixPath(relative_path),
            artifact["compression"],
            artifact_snapshots[relative_path],
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        integrity_results = tuple(executor.map(validate_artifact, artifacts))
    integrity_valid_artifacts = []
    for artifact, invalid_reason in zip(artifacts, integrity_results):
        if invalid_reason is None:
            integrity_valid_artifacts.append(artifact)
            continue
        slot_time = datetime.strptime(
            artifact["artifact_time_utc"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=UTC)
        slots[(artifact["collector_id"], artifact["artifact_type"])].remove(slot_time)
        invalid_in_window.append(
            {
                key: value
                for key, value in artifact.items()
                if key not in {"artifact_id", "artifact_id_schema"}
            }
            | {
                "value_state": INVALID_IN_WINDOW_VALUE_STATE,
                "missing_reason": invalid_reason,
            }
        )
    artifacts = integrity_valid_artifacts

    artifacts.sort(
        key=lambda row: (
            row["collector_id"],
            row["artifact_type"],
            row["artifact_time_utc"],
            row["relative_path"],
        )
    )
    excluded_out_of_window.sort(
        key=lambda row: (
            row["artifact_time_utc"],
            row["collector_id"],
            row["artifact_type"],
            row["relative_path"],
        )
    )
    invalid_in_window.sort(
        key=lambda row: (
            row["collector_id"],
            row["artifact_type"],
            row["artifact_time_utc"],
            row["relative_path"],
        )
    )
    invalid_slots: Dict[Tuple[str, str], set[datetime]] = {
        (collector, artifact_type): set()
        for collector in collectors
        for artifact_type in SLOT_INTERVALS
    }
    for invalid in invalid_in_window:
        invalid_slots[(invalid["collector_id"], invalid["artifact_type"])].add(
            datetime.strptime(
                invalid["artifact_time_utc"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=UTC)
        )
    expected_by_type = {
        artifact_type: _expected_slots(window_start, window_end, interval)
        for artifact_type, interval in SLOT_INTERVALS.items()
    }
    summary_by_type = {}
    for artifact_type in sorted(SLOT_INTERVALS):
        typed = [row for row in artifacts if row["artifact_type"] == artifact_type]
        summary_by_type[artifact_type] = {
            "artifact_count": len(typed),
            "size_bytes": sum(row["size_bytes"] for row in typed),
        }
    summary_by_collector = []
    for collector in collectors:
        collected = [row for row in artifacts if row["collector_id"] == collector]
        summary_by_collector.append(
            {
                "collector_id": collector,
                "artifact_count": len(collected),
                "size_bytes": sum(row["size_bytes"] for row in collected),
            }
        )
    excluded_by_reason = {}
    for reason in ("before_window", "at_or_after_window_end"):
        rows = [row for row in excluded_out_of_window if row["reason"] == reason]
        excluded_by_reason[reason] = {
            "file_count": len(rows),
            "size_bytes": sum(row["size_bytes"] for row in rows),
        }
    invalid_by_reason = {}
    for reason in sorted(INVALID_IN_WINDOW_MISSING_REASONS):
        rows = [row for row in invalid_in_window if row["missing_reason"] == reason]
        invalid_by_reason[reason] = {
            "file_count": len(rows),
            "size_bytes": sum(row["size_bytes"] for row in rows),
        }
    boundary_samples = []
    before_rows = [
        row for row in excluded_out_of_window if row["reason"] == "before_window"
    ]
    after_rows = [
        row
        for row in excluded_out_of_window
        if row["reason"] == "at_or_after_window_end"
    ]
    if before_rows:
        boundary_samples.append(before_rows[-1])  # 最接近窗口起点的前置文件。
    if after_rows:
        boundary_samples.append(after_rows[0])  # 最接近排他窗口终点的后置文件。
    by_collector = []
    total_expected = total_available = 0
    total_missing_ranges = []
    for collector in collectors:
        type_coverage = {}
        for artifact_type in sorted(SLOT_INTERVALS):
            coverage = _coverage_record(
                expected_by_type[artifact_type],
                slots[(collector, artifact_type)],
                SLOT_INTERVALS[artifact_type],
                invalid_slots[(collector, artifact_type)],
            )
            type_coverage[artifact_type] = coverage
            total_expected += coverage["expected_slots"]
            total_available += coverage["available_slots"]
            for missing_range in coverage["missing_ranges"]:
                total_missing_ranges.append(
                    {
                        "collector_id": collector,
                        "artifact_type": artifact_type,
                        **missing_range,
                    }
                )
        by_collector.append({"collector_id": collector, "by_artifact_type": type_coverage})

    total_invalid_slots = sum(len(value) for value in invalid_slots.values())
    total_missing = total_expected - total_available
    if total_missing == 0:
        aggregate_missing_state = None
    elif total_invalid_slots == 0:
        aggregate_missing_state = "source_unavailable"
    elif total_invalid_slots == total_missing:
        aggregate_missing_state = "parse_failed"
    else:
        aggregate_missing_state = "mixed"
    manifest: Dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_kind": MANIFEST_KIND,
        "artifact_id_schema": ARTIFACT_ID_SCHEMA,
        "data_profile": profile,
        "filename_timestamp_timezone": "UTC",
        "collector_allowlist": list(collectors),
        "scan_policy": {
            "out_of_window": "reject" if strict_out_of_window else "exclude_without_hash",
            "invalid_in_window": "full_hash_quarantine_exclude_from_available_slots",
            "compression_envelope_validation": "full_stream_to_eof_crc_or_equivalent",
            "duplicate_content": dict(DUPLICATE_CONTENT_POLICY),
            "directory_scope": _directory_scope_policy(window_start, window_end),
        },
        "artifacts": artifacts,
        "invalid_in_window": invalid_in_window,
        "summary": {
            "artifact_count": len(artifacts),
            "size_bytes": sum(row["size_bytes"] for row in artifacts),
            "by_artifact_type": summary_by_type,
            "by_collector": summary_by_collector,
            "excluded_out_of_window": {
                "file_count": len(excluded_out_of_window),
                "size_bytes": sum(row["size_bytes"] for row in excluded_out_of_window),
                "by_reason": excluded_by_reason,
                "boundary_samples": boundary_samples,
            },
            "invalid_in_window": {
                "file_count": len(invalid_in_window),
                "size_bytes": sum(row["size_bytes"] for row in invalid_in_window),
                "by_missing_reason": invalid_by_reason,
            },
        },
        "coverage": {
            "expected_slots": total_expected,
            "available_slots": total_available,
            "missing_slots": total_expected - total_available,
            "coverage_ratio": round(total_available / total_expected, 8)
            if total_expected
            else 1.0,
            "coverage_status": "complete" if total_available == total_expected else "partial",
            "missing_value_state": aggregate_missing_state,
            "by_collector": by_collector,
            "missing_ranges": total_missing_ranges,
        },
    }
    manifest["manifest_fingerprint_sha256"] = _manifest_fingerprint(manifest)
    return manifest


def _manifest_without_fingerprint(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise ArtifactIntegrityError("manifest 必须是 JSON 对象")
    payload = dict(manifest)
    fingerprint = payload.pop("manifest_fingerprint_sha256", None)
    if not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint):
        raise ArtifactIntegrityError("manifest fingerprint 缺失或非法")
    if _manifest_fingerprint(payload) != fingerprint:
        raise ArtifactIntegrityError("manifest fingerprint 校验失败")
    return payload


def _require_positive_limit(value: Any, field: str, absolute_maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > absolute_maximum
    ):
        raise ArtifactManifestError(
            f"{field} 必须是 1..{absolute_maximum} 的整数"
        )
    return value


def _validate_parent_verification(
    manifest: Mapping[str, Any], verification: Mapping[str, Any]
) -> Tuple[Dict[str, Any], list[Mapping[str, Any]]]:
    payload = _manifest_without_fingerprint(manifest)
    fingerprint = manifest["manifest_fingerprint_sha256"]
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ArtifactIntegrityError("父 manifest schema_version 不受支持")
    if payload.get("manifest_kind") != MANIFEST_KIND:
        raise ArtifactIntegrityError("父 manifest_kind 不受支持")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ArtifactIntegrityError("父 manifest artifacts 必须是数组")
    if (
        not isinstance(verification, Mapping)
        or verification.get("verified") is not True
        or verification.get("manifest_fingerprint_sha256") != fingerprint
        or verification.get("artifact_count") != len(artifacts)
    ):
        raise ArtifactIntegrityError("父 manifest verification 与完整 manifest 不一致")
    return payload, artifacts


def derive_update_pilot_selection(
    manifest: Mapping[str, Any],
    manifest_verification: Mapping[str, Any],
    selected_artifact_ids: Sequence[str],
    *,
    max_artifact_count: int,
    max_compressed_bytes: int,
    max_physical_records: int,
    max_route_events: int,
    max_spool_bytes: int,
) -> Dict[str, Any]:
    """从完整、已验证父 manifest 派生确定性的 UPDATE-only pilot selection。

    selection 只声明“选择了哪些已验证文件”，绝不重新计算或缩小父 manifest
    的覆盖率。未选择 UPDATE 与不支持的 RIB 都以独立原因计数保留。
    """

    payload, artifacts = _validate_parent_verification(
        manifest, manifest_verification
    )
    if isinstance(selected_artifact_ids, (str, bytes)) or not isinstance(
        selected_artifact_ids, Sequence
    ):
        raise ArtifactManifestError("selected_artifact_ids 必须是显式序列")
    selected_ids = tuple(selected_artifact_ids)
    if not selected_ids:
        raise ArtifactManifestError("UPDATE pilot 至少选择一个 artifact")
    if any(not isinstance(value, str) or not value for value in selected_ids):
        raise ArtifactManifestError("selected_artifact_ids 含非法值")
    if len(set(selected_ids)) != len(selected_ids):
        raise ArtifactManifestError("selected_artifact_ids 不得重复")

    limits = {
        "max_artifact_count": _require_positive_limit(
            max_artifact_count,
            "max_artifact_count",
            PILOT_ABSOLUTE_MAX_ARTIFACTS,
        ),
        "max_compressed_bytes": _require_positive_limit(
            max_compressed_bytes,
            "max_compressed_bytes",
            PILOT_ABSOLUTE_MAX_COMPRESSED_BYTES,
        ),
        "max_physical_records": _require_positive_limit(
            max_physical_records,
            "max_physical_records",
            PILOT_ABSOLUTE_MAX_PHYSICAL_RECORDS,
        ),
        "max_route_events": _require_positive_limit(
            max_route_events,
            "max_route_events",
            PILOT_ABSOLUTE_MAX_ROUTE_EVENTS,
        ),
        "max_spool_bytes": _require_positive_limit(
            max_spool_bytes,
            "max_spool_bytes",
            PILOT_ABSOLUTE_MAX_SPOOL_BYTES,
        ),
    }
    if len(selected_ids) > limits["max_artifact_count"]:
        raise ArtifactManifestError("选择 artifact 数超过 pilot 硬上限")

    artifact_map: Dict[str, Mapping[str, Any]] = {}
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise ArtifactIntegrityError("父 manifest artifact 必须是对象")
        artifact_id = artifact.get("artifact_id")
        file_hash = artifact.get("file_sha256")
        size_bytes = artifact.get("size_bytes")
        if (
            not isinstance(file_hash, str)
            or artifact_id != artifact_id_v1(file_hash)
            or artifact_id in artifact_map
            or artifact.get("artifact_type") not in {"update", "rib"}
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes <= 0
        ):
            raise ArtifactIntegrityError(
                "父 manifest artifact ID/SHA/type/size 非法或重复"
            )
        artifact_map[artifact_id] = artifact

    missing = sorted(set(selected_ids) - set(artifact_map))
    if missing:
        raise ArtifactManifestError("选择了父 manifest 不存在的 artifact")
    selected = []
    for artifact_id in sorted(selected_ids):
        artifact = artifact_map[artifact_id]
        if artifact.get("artifact_type") != "update":
            raise ArtifactManifestError("UPDATE pilot 不得选择 RIB artifact")
        if artifact.get("compression") != "gz":
            raise ArtifactManifestError("bgpdump P0 pilot 只选择 gzip UPDATE")
        required = (
            "collector_id",
            "artifact_time_utc",
            "relative_path",
            "size_bytes",
            "file_sha256",
        )
        if any(field not in artifact for field in required):
            raise ArtifactIntegrityError("父 manifest UPDATE 缺少 pilot 所需字段")
        selected.append(copy.deepcopy(dict(artifact)))
    selected_bytes = sum(row["size_bytes"] for row in selected)
    if selected_bytes > limits["max_compressed_bytes"]:
        raise ArtifactManifestError("选择 UPDATE 压缩字节超过 pilot 硬上限")

    selected_set = set(selected_ids)
    excluded_rib = [row for row in artifacts if row.get("artifact_type") == "rib"]
    excluded_update = [
        row
        for row in artifacts
        if row.get("artifact_type") == "update"
        and row.get("artifact_id") not in selected_set
    ]
    data_profile = payload.get("data_profile")
    parent_coverage = payload.get("coverage")
    if not isinstance(data_profile, Mapping) or not isinstance(parent_coverage, Mapping):
        raise ArtifactIntegrityError("父 manifest 缺少 data_profile/coverage")

    selection: Dict[str, Any] = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "selection_kind": SELECTION_KIND,
        "selection_mode": "explicit_update_pilot",
        "pilot_only": True,
        "production_complete": False,
        "parent_manifest_fingerprint_sha256": manifest[
            "manifest_fingerprint_sha256"
        ],
        "parent_manifest_artifact_count": len(artifacts),
        "data_profile": copy.deepcopy(dict(data_profile)),
        "selected_artifacts": selected,
        "limits": limits,
        "selection_summary": {
            "selected_artifact_count": len(selected),
            "selected_compressed_bytes": selected_bytes,
            "excluded": {
                "rib_not_supported": {
                    "artifact_count": len(excluded_rib),
                    "compressed_bytes": sum(row["size_bytes"] for row in excluded_rib),
                },
                "update_not_selected_for_bounded_pilot": {
                    "artifact_count": len(excluded_update),
                    "compressed_bytes": sum(row["size_bytes"] for row in excluded_update),
                },
            },
        },
        "coverage_semantics": {
            "selection_coverage_claim": "none_pilot_subset",
            "parent_manifest_coverage": copy.deepcopy(dict(parent_coverage)),
            "missing_values_unchanged": True,
        },
        "raw_reference_contract": {
            "record_ordinal_basis": (
                "zero_based_physical_mrt_record_in_decompressed_stream"
            ),
            "record_offset_basis": "decompressed_mrt_stream",
            "record_length_basis": "complete_mrt_common_header_plus_payload",
            "record_hash_algorithm": "sha256_complete_mrt_record_bytes",
            "compressed_file_identity_algorithm": "sha256_compressed_file_bytes",
            "post_build_verification": "independent_second_read_required",
            "max_frame_bytes": 64 * 1024 * 1024,
        },
        "limitations": [
            "pilot selection 不是全量 RouteEvent 索引，不得用于生产完整性声明",
            "RIB 未被解析或伪造成 RouteEvent，保持明确未物化",
            "未选择 UPDATE 仍属于父 manifest，仅因 pilot 硬上限未处理",
            "raw_ref.record_offset 是解压 MRT 字节流偏移，不是 gzip 压缩文件偏移",
            "max_spool_bytes 是每个 artifact 匿名临时 spool 的独立峰值硬上限",
        ],
    }
    selection["selection_fingerprint_sha256"] = _selection_fingerprint(selection)
    return selection


def verify_update_pilot_selection(
    manifest: Mapping[str, Any],
    manifest_verification: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> Dict[str, Any]:
    """复算 UPDATE selection，并证明它绑定同一个完整父 manifest。"""

    if not isinstance(selection, Mapping):
        raise ArtifactIntegrityError("UPDATE selection 必须是对象")
    payload = dict(selection)
    fingerprint = payload.pop("selection_fingerprint_sha256", None)
    if not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint):
        raise ArtifactIntegrityError("UPDATE selection fingerprint 缺失或非法")
    if _selection_fingerprint(payload) != fingerprint:
        raise ArtifactIntegrityError("UPDATE selection fingerprint 校验失败")
    if (
        payload.get("schema_version") != SELECTION_SCHEMA_VERSION
        or payload.get("selection_kind") != SELECTION_KIND
        or payload.get("selection_mode") != "explicit_update_pilot"
        or payload.get("pilot_only") is not True
        or payload.get("production_complete") is not False
    ):
        raise ArtifactIntegrityError("UPDATE selection 类型或 pilot 边界非法")
    selected = payload.get("selected_artifacts")
    limits = payload.get("limits")
    if not isinstance(selected, list) or not isinstance(limits, Mapping):
        raise ArtifactIntegrityError("UPDATE selection 缺少 selected_artifacts/limits")
    expected = derive_update_pilot_selection(
        manifest,
        manifest_verification,
        [row.get("artifact_id") if isinstance(row, Mapping) else None for row in selected],
        max_artifact_count=limits.get("max_artifact_count"),
        max_compressed_bytes=limits.get("max_compressed_bytes"),
        max_physical_records=limits.get("max_physical_records"),
        max_route_events=limits.get("max_route_events"),
        max_spool_bytes=limits.get("max_spool_bytes"),
    )
    if canonical_json(expected) != canonical_json(dict(selection)):
        raise ArtifactIntegrityError("UPDATE selection 与完整父 manifest 派生结果不一致")
    return {
        "verified": True,
        "selection_kind": SELECTION_KIND,
        "selection_fingerprint_sha256": fingerprint,
        "parent_manifest_fingerprint_sha256": manifest[
            "manifest_fingerprint_sha256"
        ],
        "selected_artifact_count": len(selected),
        "selected_compressed_bytes": expected["selection_summary"][
            "selected_compressed_bytes"
        ],
        "pilot_only": True,
    }


def verify_artifact_manifest(
    raw_root: os.PathLike[str] | str,
    manifest: Mapping[str, Any],
    *,
    integrity_workers: int = 1,
) -> Dict[str, Any]:
    """重新扫描原始目录，验证 manifest、文件哈希及完整槽集合。"""

    payload = _manifest_without_fingerprint(manifest)
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ArtifactIntegrityError("manifest schema_version 不受支持")
    if payload.get("manifest_kind") != MANIFEST_KIND:
        raise ArtifactIntegrityError("manifest_kind 不受支持")
    profile = payload.get("data_profile")
    collectors = payload.get("collector_allowlist")
    if not isinstance(profile, Mapping) or not isinstance(collectors, list):
        raise ArtifactIntegrityError("manifest 缺少 data_profile 或 collector_allowlist")
    try:
        _, window_start, window_end = _normalize_profile(profile)
    except ArtifactManifestError as error:
        raise ArtifactIntegrityError("manifest data_profile 非法") from error

    # 先拒绝可疑相对路径；随后完整重扫会同时发现文件篡改、新增、删除或改名。
    artifacts = payload.get("artifacts")
    invalid_in_window = payload.get("invalid_in_window")
    if not isinstance(artifacts, list):
        raise ArtifactIntegrityError("manifest artifacts 必须是数组")
    if not isinstance(invalid_in_window, list):
        raise ArtifactIntegrityError("manifest invalid_in_window 必须是数组")
    for label, records in (
        ("artifact", artifacts),
        ("invalid_in_window", invalid_in_window),
    ):
        for artifact in records:
            if not isinstance(artifact, Mapping):
                raise ArtifactIntegrityError(f"manifest {label} 必须是对象")
            relative = artifact.get("relative_path")
            if not isinstance(relative, str):
                raise ArtifactIntegrityError("manifest relative_path 非法")
            pure = PurePosixPath(relative)
            if pure.is_absolute() or ".." in pure.parts or not pure.parts:
                raise ArtifactIntegrityError("manifest relative_path 不安全")
            if pure.parts[0] not in collectors:
                raise ArtifactIntegrityError("manifest relative_path 越出 collector allowlist")
    for invalid in invalid_in_window:
        relative = invalid.get("relative_path")
        if not isinstance(relative, str):
            raise ArtifactIntegrityError("manifest relative_path 非法")
        if (
            invalid.get("value_state") != INVALID_IN_WINDOW_VALUE_STATE
            or invalid.get("missing_reason") not in INVALID_IN_WINDOW_MISSING_REASONS
            or not isinstance(invalid.get("file_sha256"), str)
            or not SHA256_RE.fullmatch(invalid["file_sha256"])
        ):
            raise ArtifactIntegrityError("manifest invalid_in_window 内容状态非法")

    policy = payload.get("scan_policy")
    if (
        not isinstance(policy, Mapping)
        or policy.get("out_of_window") not in {"exclude_without_hash", "reject"}
        or policy.get("invalid_in_window")
        != "full_hash_quarantine_exclude_from_available_slots"
        or policy.get("compression_envelope_validation")
        != "full_stream_to_eof_crc_or_equivalent"
        or policy.get("duplicate_content") != DUPLICATE_CONTENT_POLICY
        or policy.get("directory_scope")
        != _directory_scope_policy(window_start, window_end)
    ):
        raise ArtifactIntegrityError("manifest scan_policy 非法")
    try:
        rescanned = scan_mrt_artifacts(
            raw_root,
            profile,
            collectors,
            strict_out_of_window=policy["out_of_window"] == "reject",
            integrity_workers=integrity_workers,
        )
    except ArtifactManifestError as error:
        raise ArtifactIntegrityError("重新扫描原始制品失败") from error
    if canonical_json(rescanned) != canonical_json(dict(manifest)):
        raise ArtifactIntegrityError("manifest 与当前原始制品不一致")
    return {
        "verified": True,
        "artifact_count": len(artifacts),
        "invalid_in_window_count": len(invalid_in_window),
        "manifest_fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
    }


def atomic_write_manifest(
    destination: os.PathLike[str] | str, manifest: Mapping[str, Any], *, mode: int = 0o640
) -> Path:
    """原子发布新 manifest；目标已存在时拒绝覆盖。"""

    _manifest_without_fingerprint(manifest)
    target = Path(destination)
    _assert_directory(target.parent, "manifest 目标父目录")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"manifest 已存在，拒绝覆盖：{target}")
    encoded = (canonical_json(dict(manifest)) + "\n").encode("utf-8")
    temporary = target.parent / f".{target.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        # hard link 是同文件系统内的原子“仅在目标不存在时创建”。
        os.link(temporary, target, follow_symlinks=False)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return target


__all__ = (
    "ARTIFACT_ID_SCHEMA",
    "DUPLICATE_CONTENT_POLICY",
    "INVALID_IN_WINDOW_MISSING_REASONS",
    "INVALID_IN_WINDOW_VALUE_STATE",
    "PILOT_ABSOLUTE_MAX_ARTIFACTS",
    "PILOT_ABSOLUTE_MAX_COMPRESSED_BYTES",
    "PILOT_ABSOLUTE_MAX_PHYSICAL_RECORDS",
    "PILOT_ABSOLUTE_MAX_ROUTE_EVENTS",
    "PILOT_ABSOLUTE_MAX_SPOOL_BYTES",
    "SELECTION_KIND",
    "ArtifactIntegrityError",
    "ArtifactManifestError",
    "artifact_id_v1",
    "atomic_write_manifest",
    "canonical_json",
    "derive_update_pilot_selection",
    "scan_mrt_artifacts",
    "verify_artifact_manifest",
    "verify_update_pilot_selection",
)
