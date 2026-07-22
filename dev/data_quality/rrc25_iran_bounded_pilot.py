#!/usr/bin/env python3
"""RRC25 伊朗事件稀疏取证 pilot 的统一执行与核验入口。

本入口刻意只允许消费一个已验真的截断 seed RIB checkpoint，并完整读取 1..5
个显式 UPDATE 槽；它不会重新打开 seed RIB。未抽中的窗口槽和 seed 截断余量
保留为 ``gap/unknown``，因此输出固定是 ``bounded_pilot``、
``incomplete/not_accepted``；它用于跑通 Profile -> MRT -> RouteEvent -> raw ref
-> 国家样本 -> 对账 -> 研究包的真实数据闭环，不冒充连续窗口复算。

同一次 MRT worker 结果会在内存中派生两次并发布到两个空目录。所谓 A/B 只
比较纯派生语义，不会二次读取 MRT。入口不导入数据库客户端，不接受 DSN，
也不访问旧系统数据库。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.data_pipeline.route_event import (
    BGPDUMP_APPROVED_VERSION,
    make_bgpdump_record_stream_factory,
)
from backend.data_pipeline.normalize import LocatorError, parse_detail_url
from backend.data_pipeline.research.resource_gate import (
    ResourceLimits,
    ResourceUsage,
    WriteTarget,
    evaluate_resource_gate,
)
from backend.data_pipeline.research.rrc25_country_outage.bounded_pilot_worker import (
    BoundedPilotWorkerResult,
    SlotCount as WorkerSlotCount,
    run_bounded_pilot_worker,
)
from backend.data_pipeline.research.rrc25_country_outage.coordinator import (
    DEFAULT_PRODUCTION_ROOTS,
    DEFAULT_PROTECTED_ROOTS,
    build_worker_plan,
    effective_resource_limits,
    load_json_metadata,
    prepare_research_plan,
    verify_worker_plan,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    mapping_view_from_frozen_snapshot,
    snapshot_ids_v1,
)
from backend.data_pipeline.research.rrc25_country_outage.derived_assembly import (
    DerivedResearchAssembly,
    SlotResearchMetadata,
    assemble_derived_research,
)
from backend.data_pipeline.research.rrc25_country_outage.input_resolver import (
    canonical_json,
    resolve_research_inputs,
)
from backend.data_pipeline.research.rrc25_country_outage.mapped_compatible_projection import (
    MappedCompatibleProjection,
    build_mapped_compatible_projection_series,
)
from backend.data_pipeline.research.rrc25_country_outage.package_manifest import (
    build_package_manifest,
    verify_published_package,
)
from backend.data_pipeline.research.rrc25_country_outage.package_publisher import (
    publish_research_package,
)
from backend.data_pipeline.research.rrc25_country_outage.pilot_sampling import (
    build_sparse_pilot_selection,
)
from backend.data_pipeline.research.rrc25_country_outage.profile import (
    profile_sha256,
    validate_research_profile,
)
from backend.data_pipeline.research.rrc25_country_outage.research_quality import (
    DiagnosticFact,
    GATE_ORDER,
)
from backend.data_pipeline.research.rrc25_country_outage.sample_builder import (
    SampleSourceRef,
    observed_slot_count,
    unknown_slot_count,
)
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    ReplaySnapshot,
    ResearchRouteEvent,
)
from backend.data_pipeline.research.rrc25_country_outage.update_adapter import (
    RawRecordEvidence,
)


UTC = timezone.utc
PILOT_SCHEMA_VERSION = "rrc25-iran-sparse-evidence-pilot/v1"
CODE_IDENTITY_SCHEMA_VERSION = "domeye-research-code-identity/v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SLOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z$")
_MAX_UPDATE_ARTIFACTS = 5
_MAX_PHYSICAL_RECORDS = 2_000_000
_MAX_ROUTE_EVENTS = 5_000_000
_MAX_SPOOL_BYTES = 4_000_000_000


class SparsePilotError(ValueError):
    """真实稀疏 pilot 的输入、结果或发布边界不闭合。"""


def _require_cumulative_runtime_budget(
    *,
    stage: str,
    process_started_at: float,
    clock: Callable[[], float],
    limits: ResourceLimits,
    new_raw_read_bytes: int = 0,
    peak_temporary_bytes: int = 0,
) -> Mapping[str, Any]:
    """按同一 CLI 进程起点检查累计运行时，任何非 allowed 均失败关闭。"""

    if not isinstance(stage, str) or not stage:
        raise SparsePilotError("累计运行时门禁 stage 不能为空")
    now = clock()
    if (
        isinstance(process_started_at, bool)
        or not isinstance(process_started_at, (int, float))
        or isinstance(now, bool)
        or not isinstance(now, (int, float))
    ):
        raise SparsePilotError("累计运行时门禁 clock/start 必须是有限数")
    elapsed = float(now) - float(process_started_at)
    if not math.isfinite(elapsed) or elapsed < 0:
        raise SparsePilotError("累计运行时门禁 clock 不得倒退且必须返回有限数")
    result = evaluate_resource_gate(
        ResourceUsage(
            new_raw_read_bytes=new_raw_read_bytes,
            process_runtime_seconds=elapsed,
            temporary_bytes=peak_temporary_bytes,
            output_bytes=0,
            phase="observed",
        ),
        limits=limits,
    )
    if result.decision != "allowed":
        raise SparsePilotError(
            "累计运行时门禁拒绝继续："
            f"stage={stage}, decision={result.decision}, "
            f"elapsed_seconds={elapsed:.6f}"
        )
    return result.to_dict()


def _publish_ab_with_runtime_gate(
    *,
    output_a: str | Path,
    contents_a: Mapping[str, bytes],
    manifest_a: Mapping[str, Any],
    output_b: str | Path,
    contents_b: Mapping[str, bytes],
    manifest_b: Mapping[str, Any],
    process_started_at: float,
    clock: Callable[[], float],
    limits: ResourceLimits,
    new_raw_read_bytes: int,
    peak_temporary_bytes: int,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """在 A、B 各自开始写盘前复核同一个 CLI 累计时间门禁。"""

    common = {
        "process_started_at": process_started_at,
        "clock": clock,
        "limits": limits,
        "new_raw_read_bytes": new_raw_read_bytes,
        "peak_temporary_bytes": peak_temporary_bytes,
    }
    _require_cumulative_runtime_budget(stage="before_publish_a", **common)
    verified_a = publish_research_package(output_a, contents_a, manifest_a)
    _require_cumulative_runtime_budget(stage="before_publish_b", **common)
    verified_b = publish_research_package(output_b, contents_b, manifest_b)
    return verified_a, verified_b


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list, set, frozenset)):
        rows = [_jsonable(item) for item in value]
        if isinstance(value, (set, frozenset)):
            rows.sort(key=lambda item: canonical_json(item))
        return rows
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise SparsePilotError(f"不能安全序列化类型：{type(value).__name__}")


def _json_bytes(value: Any) -> bytes:
    return (canonical_json(_jsonable(value)) + "\n").encode("utf-8")


def _gzip_jsonl(rows: Iterable[Any]) -> bytes:
    body = b"".join(_json_bytes(row) for row in rows)
    return gzip.compress(body, compresslevel=9, mtime=0)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _raw_record_ref_id(
    file_sha256: str, record_ordinal: int, element_ordinal: int
) -> str:
    identity = {
        "schema": "raw_record_ref_id_v1",
        "file_sha256": file_sha256,
        "record_ordinal": record_ordinal,
        "element_ordinal": element_ordinal,
    }
    return "raw_v1_" + hashlib.sha256(
        canonical_json(identity).encode("utf-8")
    ).hexdigest()[:32]


def _utc_slot(value: str) -> str:
    if not isinstance(value, str) or _SLOT_RE.fullmatch(value) is None:
        raise SparsePilotError("UPDATE 抽样槽必须是五分钟对齐的秒级 UTC Z 时间")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError as error:
        raise SparsePilotError("UPDATE 抽样槽不是合法 UTC 时间") from error
    if parsed.minute % 5:
        raise SparsePilotError("UPDATE 抽样槽必须按五分钟对齐")
    return value


def _hash_regular_source(path: Path) -> tuple[str, int]:
    try:
        initial = path.lstat()
    except OSError as error:
        raise SparsePilotError(f"代码身份文件不可读：{path.name}") from error
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise SparsePilotError("代码身份只允许非符号链接普通文件")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        before = stream.fileno()
        opened = os.fstat(before)
        identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(initial, key) != getattr(opened, key) for key in identity_fields):
            raise SparsePilotError("代码文件在打开前发生变化")
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
        after = os.fstat(before)
        if any(getattr(opened, key) != getattr(after, key) for key in identity_fields):
            raise SparsePilotError("代码文件在读取期间发生变化")
    return digest.hexdigest(), size


def _read_regular_bytes(path_value: str | Path, *, maximum_bytes: int) -> bytes:
    path = Path(path_value)
    try:
        initial = path.lstat()
    except OSError as error:
        raise SparsePilotError(f"只读输入不可读：{path.name}") from error
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise SparsePilotError("只读输入必须是非符号链接普通文件")
    if initial.st_size <= 0 or initial.st_size > maximum_bytes:
        raise SparsePilotError("只读输入大小越界")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    chunks: list[bytes] = []
    total = 0
    try:
        before = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(initial, key) != getattr(before, key) for key in fields):
            raise SparsePilotError("只读输入在打开前发生变化")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > maximum_bytes:
                raise SparsePilotError("只读输入超过读取上限")
        after = os.fstat(descriptor)
        if any(getattr(before, key) != getattr(after, key) for key in fields):
            raise SparsePilotError("只读输入在读取期间发生变化")
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _assert_expected_sha256(payload: bytes, expected: str, field: str) -> str:
    if not isinstance(expected, str) or _SHA256_RE.fullmatch(expected) is None:
        raise SparsePilotError(f"{field} 必须是 64 位小写 SHA256")
    observed = _sha256_bytes(payload)
    if observed != expected:
        raise SparsePilotError(f"{field} 与只读输入字节不一致")
    return observed


def build_code_identity(repository_root: str | Path = REPOSITORY_ROOT) -> Mapping[str, Any]:
    root = Path(repository_root)
    try:
        root_meta = root.lstat()
    except OSError as error:
        raise SparsePilotError("代码仓库根目录不可读") from error
    if stat.S_ISLNK(root_meta.st_mode) or not stat.S_ISDIR(root_meta.st_mode):
        raise SparsePilotError("代码仓库根必须是非符号链接目录")
    fixed = {
        "backend/data_pipeline/__init__.py",
        "dev/data_quality/rrc25_iran_research.py",
        "dev/data_quality/rrc25_iran_bounded_pilot.py",
        "dev/data_quality/validate_research_contracts.cjs",
    }
    patterns = (
        "backend/data_pipeline/evidence/**/*.py",
        "backend/data_pipeline/route_event/**/*.py",
        "backend/data_pipeline/research/**/*.py",
        "config/research/**/*.json",
        "contracts/research/**/*.json",
    )
    paths = set(fixed)
    for pattern in patterns:
        paths.update(
            path.relative_to(root).as_posix()
            for path in root.glob(pattern)
            if path.is_file() and "__pycache__" not in path.parts
        )
    rows = []
    for relative in sorted(paths):
        pure = PurePosixPath(relative)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise SparsePilotError("代码身份路径不是安全相对路径")
        digest, size = _hash_regular_source(root.joinpath(*pure.parts))
        rows.append({"path": relative, "size_bytes": size, "sha256": digest})
    semantic = {"schema_version": CODE_IDENTITY_SCHEMA_VERSION, "files": rows}
    return {
        **semantic,
        "identity_sha256": hashlib.sha256(
            canonical_json(semantic).encode("utf-8")
        ).hexdigest(),
    }


def _load_code_identity(
    path: str | Path,
    expected_sha256: str,
    *,
    verify_current_files: bool = True,
) -> Mapping[str, Any]:
    identity = load_json_metadata(path)
    if not _SHA256_RE.fullmatch(expected_sha256):
        raise SparsePilotError("code_sha256 必须是 64 位小写 SHA256")
    if set(identity) != {"schema_version", "files", "identity_sha256"}:
        raise SparsePilotError("代码身份文件字段不闭合")
    if identity.get("schema_version") != CODE_IDENTITY_SCHEMA_VERSION:
        raise SparsePilotError("代码身份 schema_version 不支持")
    files = identity.get("files")
    if not isinstance(files, list) or not files:
        raise SparsePilotError("代码身份文件缺少逐文件清单")
    semantic = {"schema_version": identity["schema_version"], "files": files}
    actual_identity = hashlib.sha256(
        canonical_json(semantic).encode("utf-8")
    ).hexdigest()
    if identity.get("identity_sha256") != actual_identity:
        raise SparsePilotError("代码身份整体指纹不能由逐文件清单重算")
    if actual_identity != expected_sha256:
        raise SparsePilotError("代码身份文件与命令行 code_sha256 不一致")
    observed_paths: set[str] = set()
    for index, row in enumerate(files):
        if not isinstance(row, Mapping) or set(row) != {"path", "size_bytes", "sha256"}:
            raise SparsePilotError(f"代码身份 files[{index}] 字段不闭合")
        relative = row["path"]
        if not isinstance(relative, str):
            raise SparsePilotError("代码身份 path 必须是字符串")
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
            or relative in observed_paths
        ):
            raise SparsePilotError("代码身份 path 非法或重复")
        observed_paths.add(relative)
        if verify_current_files:
            digest, size = _hash_regular_source(REPOSITORY_ROOT.joinpath(*pure.parts))
            if row["sha256"] != digest or row["size_bytes"] != size:
                raise SparsePilotError(f"当前代码与冻结身份不一致：{relative}")
    return identity


def _pilot_resolver_profile(
    profile: Mapping[str, Any], pilot_end_exclusive: str
) -> Mapping[str, Any]:
    normalized = validate_research_profile(profile)
    end = _utc_slot(pilot_end_exclusive)
    start = normalized["window"]["start_utc"]
    if not start < end < normalized["window"]["end_exclusive_utc"]:
        raise SparsePilotError("pilot 终点必须严格位于冻结 Profile 窗口内")
    # resolver 只读取这四个字段；不篡改最终报告所绑定的冻结 Profile。
    return {
        "study_id": normalized["study_id"],
        "collector_id": normalized["collector_id"],
        "country_code": normalized["country_code"],
        "window": {
            "start_utc": start,
            "end_exclusive_utc": end,
            "granularity_seconds": 300,
        },
    }


def _selected_update_ids(
    selection: Mapping[str, Any], slots: Sequence[str]
) -> tuple[str, ...]:
    if not 1 <= len(slots) <= _MAX_UPDATE_ARTIFACTS:
        raise SparsePilotError("真实 pilot 必须显式选择 1..5 个 UPDATE 槽")
    normalized = tuple(_utc_slot(value) for value in slots)
    if len(set(normalized)) != len(normalized):
        raise SparsePilotError("UPDATE 抽样槽不得重复")
    updates = selection.get("roles", {}).get("analysis_updates", [])
    by_time = {
        row["artifact_time_utc"]: row["artifact_id"]
        for row in updates
        if isinstance(row, Mapping)
    }
    missing = sorted(set(normalized) - set(by_time))
    if missing:
        raise SparsePilotError("抽样槽不在已验证 selection：" + ",".join(missing))
    return tuple(by_time[value] for value in sorted(normalized))


def _execution_update_allowlist(
    sparse_selection: Mapping[str, Any], slots: Sequence[str]
) -> Mapping[str, Any]:
    artifact_ids = _selected_update_ids(sparse_selection, slots)
    updates = sparse_selection.get("roles", {}).get("analysis_updates", [])
    by_id = {
        row["artifact_id"]: row for row in updates if isinstance(row, Mapping)
    }
    rows = [by_id[artifact_id] for artifact_id in artifact_ids]
    semantic = {
        "schema_version": "rrc25-sparse-update-execution-allowlist/v1",
        "selection_id": sparse_selection.get("selection_id"),
        "selection_semantic_fingerprint_sha256": sparse_selection.get(
            "semantic_fingerprint_sha256"
        ),
        "artifact_ids": sorted(artifact_ids),
        "slots": sorted(row["artifact_time_utc"] for row in rows),
        "policy": "explicit_subset_preserve_unselected_as_gap_unknown",
    }
    return {
        **semantic,
        "semantic_fingerprint_sha256": hashlib.sha256(
            canonical_json(semantic).encode("utf-8")
        ).hexdigest(),
    }


def _worker_plan_artifacts(worker_plan: Mapping[str, Any]) -> Mapping[str, Any]:
    rows: dict[str, Mapping[str, Any]] = {}
    for chunk in worker_plan.get("chunks", []):
        for artifact in chunk.get("artifacts", []):
            artifact_id = artifact.get("artifact_id")
            if not isinstance(artifact_id, str) or artifact_id in rows:
                raise SparsePilotError("worker plan artifact 身份非法或重复")
            rows[artifact_id] = artifact
    return rows


def _verify_plan_bindings(
    worker_plan: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
    artifact_manifest: Mapping[str, Any],
    manifest_verification: Mapping[str, Any],
    mapping_snapshot: Mapping[str, Any],
    code_sha256: str,
    pilot_end_exclusive: str,
    sparse_selection: Mapping[str, Any],
    coordinator_output_root: str | Path,
) -> None:
    verify_worker_plan(worker_plan)
    if worker_plan.get("execution_mode") != "bounded_pilot":
        raise SparsePilotError("worker plan 必须是 bounded_pilot")
    if worker_plan.get("pilot_end_exclusive") != pilot_end_exclusive:
        raise SparsePilotError("worker plan pilot 终点与真实执行不一致")
    bindings = worker_plan["bindings"]
    # coordinator 对 mapping 使用无换行 canonical JSON。
    expected_mapping = hashlib.sha256(
        canonical_json(dict(mapping_snapshot)).encode("utf-8")
    ).hexdigest()
    if bindings.get("profile_sha256") != profile_sha256(profile):
        raise SparsePilotError("worker plan Profile 绑定不一致")
    if bindings.get("mapping_sha256") != expected_mapping:
        raise SparsePilotError("worker plan mapping 绑定不一致")
    if bindings.get("code_sha256") != code_sha256:
        raise SparsePilotError("worker plan code 绑定不一致")
    estimate = worker_plan.get("resource_estimate")
    chunks = worker_plan.get("chunks")
    if not isinstance(estimate, Mapping) or not isinstance(chunks, list) or not chunks:
        raise SparsePilotError("worker plan 缺少资源估算或分块")
    maximum_artifacts = max(int(chunk["artifact_count"]) for chunk in chunks)
    rebuilt_plan = prepare_research_plan(
        profile=profile,
        artifact_manifest=artifact_manifest,
        manifest_verification=manifest_verification,
        mapping_snapshot=mapping_snapshot,
        code_sha256=code_sha256,
        output_root=coordinator_output_root,
        maximum_artifacts_per_chunk=maximum_artifacts,
        pilot_end_exclusive=pilot_end_exclusive,
        estimated_worker_seconds=float(estimate["process_runtime_seconds"]),
        estimated_temporary_bytes=int(estimate["temporary_bytes"]),
    )
    rebuilt_worker_plan = build_worker_plan(rebuilt_plan)
    if rebuilt_worker_plan != dict(worker_plan):
        raise SparsePilotError(
            "worker plan 不能由当前 Profile/manifest/mapping/code 完整重算"
        )
    plan_artifacts = _worker_plan_artifacts(worker_plan)
    roles = sparse_selection["roles"]
    processed = [roles["state_seed_rib"], *roles["analysis_updates"]]
    for artifact in processed:
        if artifact is None or artifact["artifact_id"] not in plan_artifacts:
            raise SparsePilotError("真实 worker 制品不属于已验证 worker plan")
        planned = plan_artifacts[artifact["artifact_id"]]
        for field in (
            "artifact_id",
            "artifact_type",
            "artifact_time_utc",
            "collector_id",
            "relative_path",
            "file_sha256",
            "size_bytes",
            "compression",
        ):
            if planned.get(field) != artifact.get(field):
                raise SparsePilotError(f"worker plan artifact.{field} 绑定不一致")


def _load_seed_sample_provenance(
    *,
    checkpoint_path: str | Path,
    checkpoint_sha256: str,
    producer_code_identity_path: str | Path,
    producer_code_sha256: str,
    producer_worker_plan_path: str | Path,
    current_worker_plan: Mapping[str, Any],
    sparse_selection: Mapping[str, Any],
    pilot_end_exclusive: str,
) -> Mapping[str, Any]:
    """冻结并交叉验证截断 seed 样本的 producer 证据。

    checkpoint 本身由 worker 再做内容指纹、状态闭包与 mapping 绑定校验；这里
    额外固定它的精确文件字节，并证明 producer plan、producer code 与当前正式
    plan 使用同一 Profile、input selection、mapping 和 pilot 边界。两次读取必须
    完全一致，避免核验与研究包收录之间发生输入替换。
    """

    checkpoint_bytes = _read_regular_bytes(
        checkpoint_path, maximum_bytes=128 * 1024 * 1024
    )
    checkpoint_file_sha256 = _assert_expected_sha256(
        checkpoint_bytes,
        checkpoint_sha256,
        "seed_sample_checkpoint_sha256",
    )
    checkpoint = load_json_metadata(
        checkpoint_path, maximum_bytes=128 * 1024 * 1024
    )
    if (
        checkpoint_bytes
        != _read_regular_bytes(checkpoint_path, maximum_bytes=128 * 1024 * 1024)
    ):
        raise SparsePilotError("seed sample checkpoint 在核验期间发生变化")

    producer_code_bytes = _read_regular_bytes(
        producer_code_identity_path, maximum_bytes=8 * 1024 * 1024
    )
    producer_code_identity = _load_code_identity(
        producer_code_identity_path,
        producer_code_sha256,
        verify_current_files=False,
    )
    if producer_code_bytes != _read_regular_bytes(
        producer_code_identity_path, maximum_bytes=8 * 1024 * 1024
    ):
        raise SparsePilotError("seed producer code identity 在核验期间发生变化")

    producer_plan_bytes = _read_regular_bytes(
        producer_worker_plan_path, maximum_bytes=64 * 1024 * 1024
    )
    producer_worker_plan = load_json_metadata(
        producer_worker_plan_path, maximum_bytes=64 * 1024 * 1024
    )
    verify_worker_plan(producer_worker_plan)
    if producer_plan_bytes != _read_regular_bytes(
        producer_worker_plan_path, maximum_bytes=64 * 1024 * 1024
    ):
        raise SparsePilotError("seed producer worker plan 在核验期间发生变化")

    producer_bindings = producer_worker_plan.get("bindings", {})
    current_bindings = current_worker_plan.get("bindings", {})
    if producer_bindings.get("code_sha256") != producer_code_sha256:
        raise SparsePilotError("seed producer plan 与 producer code identity 不绑定")
    for key in ("profile_sha256", "input_selection_sha256", "mapping_sha256"):
        if producer_bindings.get(key) != current_bindings.get(key):
            raise SparsePilotError(f"seed producer plan 与当前 plan 的 {key} 不一致")
    for key in ("input_selection_id", "pilot_end_exclusive", "profile_window"):
        if producer_worker_plan.get(key) != current_worker_plan.get(key):
            raise SparsePilotError(f"seed producer plan 与当前 plan 的 {key} 不一致")
    if (
        producer_worker_plan.get("execution_mode") != "bounded_pilot"
        or producer_worker_plan.get("database_connections") != 0
        or producer_worker_plan.get("execution_allowed") is not True
    ):
        raise SparsePilotError("seed producer plan 不是可执行的零数据库 bounded pilot")
    if producer_worker_plan.get("pilot_end_exclusive") != pilot_end_exclusive:
        raise SparsePilotError("seed producer plan pilot 边界与命令行不一致")

    position = checkpoint.get("position")
    resources = checkpoint.get("resources")
    if not isinstance(position, Mapping) or not isinstance(resources, Mapping):
        raise SparsePilotError("seed sample checkpoint 缺少 position/resources")
    if checkpoint.get("selection_id") != sparse_selection.get("selection_id"):
        raise SparsePilotError("seed sample checkpoint 与稀疏 selection_id 不一致")
    if checkpoint.get("selection_semantic_fingerprint_sha256") != sparse_selection.get(
        "semantic_fingerprint_sha256"
    ):
        raise SparsePilotError("seed sample checkpoint 与稀疏 selection 语义不一致")
    if (
        checkpoint.get("pilot_start_utc")
        != sparse_selection.get("window", {}).get("start_utc")
        or checkpoint.get("pilot_end_exclusive_utc") != pilot_end_exclusive
    ):
        raise SparsePilotError("seed sample checkpoint 的 pilot 窗口不一致")

    seed = sparse_selection.get("roles", {}).get("state_seed_rib")
    if not isinstance(seed, Mapping):
        raise SparsePilotError("稀疏 selection 缺少 seed RIB")
    next_record_ordinal = position.get("next_record_ordinal")
    raw_bytes = resources.get("new_raw_read_bytes")
    cumulative_seconds = resources.get("cumulative_worker_runtime_seconds")
    maximum_seconds = resources.get("max_worker_elapsed_seconds")
    if (
        position.get("phase") != "seed_rib"
        or position.get("update_index") != 0
        or isinstance(next_record_ordinal, bool)
        or not isinstance(next_record_ordinal, int)
        or next_record_ordinal <= 0
        or isinstance(raw_bytes, bool)
        or not isinstance(raw_bytes, int)
        or raw_bytes <= 0
        or not isinstance(cumulative_seconds, (int, float))
        or isinstance(cumulative_seconds, bool)
        or not isinstance(maximum_seconds, (int, float))
        or isinstance(maximum_seconds, bool)
        or float(maximum_seconds) <= 0
        or float(maximum_seconds) > float(cumulative_seconds)
        or resources.get("database_writes") != 0
    ):
        raise SparsePilotError("seed sample checkpoint 不是可消费的截断 seed 边界")

    return {
        "checkpoint": checkpoint,
        "checkpoint_bytes": checkpoint_bytes,
        "checkpoint_file_sha256": checkpoint_file_sha256,
        "producer_code_identity": producer_code_identity,
        "producer_code_identity_bytes": producer_code_bytes,
        "producer_code_identity_file_sha256": _sha256_bytes(producer_code_bytes),
        "producer_worker_plan": producer_worker_plan,
        "producer_worker_plan_bytes": producer_plan_bytes,
        "producer_worker_plan_file_sha256": _sha256_bytes(producer_plan_bytes),
        "facts": {
            "seed_artifact_id": seed["artifact_id"],
            "seed_artifact_file_sha256": seed["file_sha256"],
            "checkpoint_fingerprint_sha256": checkpoint.get(
                "checkpoint_fingerprint_sha256"
            ),
            "checkpoint_file_sha256": checkpoint_file_sha256,
            "next_record_ordinal": next_record_ordinal,
            "route_event_ref_count": len(checkpoint.get("route_event_refs", [])),
            "raw_audit_count": len(checkpoint.get("raw_audits", [])),
            "tracked_prefix_count": len(checkpoint.get("tracked_prefixes", [])),
            "observed_vp_count": len(checkpoint.get("observed_vp_ids", [])),
            "producer_new_raw_read_bytes": raw_bytes,
            "producer_cumulative_worker_runtime_seconds": float(
                cumulative_seconds
            ),
            "producer_max_worker_elapsed_seconds": float(maximum_seconds),
            "producer_code_identity_sha256": producer_code_sha256,
            "producer_code_identity_file_sha256": _sha256_bytes(
                producer_code_bytes
            ),
            "producer_worker_plan_sha256": producer_worker_plan[
                "worker_plan_sha256"
            ],
            "producer_worker_plan_file_sha256": _sha256_bytes(
                producer_plan_bytes
            ),
        },
    }


def _slot_count(value: int | None, missing_reason: str | None):
    if value is None:
        reason = missing_reason or "source_value_not_retained"
        return unknown_slot_count("unknown_source_gap", reason)
    return observed_slot_count(value)


def _projection_summary(projection: MappedCompatibleProjection) -> Mapping[str, Any]:
    audit = projection.audit
    return {
        "projection_id": projection.projection_id,
        "projection_kind": projection.projection_kind,
        "source_kind": projection.source_kind,
        "continuity_state": projection.continuity_state,
        "missing_reasons": list(projection.missing_reasons),
        "route_count": projection.route_count,
        "input_entry_count": audit.input_entry_count,
        "retained_entry_count": audit.retained_entry_count,
        "excluded_entry_count": audit.excluded_entry_count,
        "input_change_count": audit.input_change_count,
        "retained_change_count": audit.retained_change_count,
        "excluded_change_count": audit.excluded_change_count,
        "excluded_reason_counts": [list(row) for row in audit.excluded_reason_counts],
        "limitations": list(projection.limitations),
        "blockers": list(projection.blockers),
    }


def _snapshot_record(
    projection: MappedCompatibleProjection,
    snapshot_id: str,
) -> Mapping[str, Any]:
    snapshot = projection.projected
    if not isinstance(snapshot, ReplaySnapshot):
        raise SparsePilotError("snapshot projection 类型错误")
    return {
        "snapshot_id": snapshot_id,
        "slot_start_utc": snapshot.slot_start_utc,
        "slot_end_exclusive_utc": snapshot.slot_end_exclusive_utc,
        "boundary": snapshot.boundary,
        "continuity_state": snapshot.continuity_state,
        "missing_reasons": list(snapshot.missing_reasons),
        "route_count": snapshot.route_count,
        "entry_count_retained_for_audit": len(snapshot.entries),
        "slot_change_route_event_ids": sorted(
            change.raw_ref.route_event_id for change in snapshot.slot_changes
        ),
        "projection": _projection_summary(projection),
    }


def _build_slot_metadata(
    worker: BoundedPilotWorkerResult,
    projections: Sequence[MappedCompatibleProjection],
) -> tuple[SlotResearchMetadata, ...]:
    if len(worker.snapshots) != len(projections) or len(worker.slot_counts) != len(
        projections
    ):
        raise SparsePilotError("worker 快照、投影与槽计数长度不一致")
    projected_snapshots = tuple(projection.projected for projection in projections)
    if any(not isinstance(row, ReplaySnapshot) for row in projected_snapshots):
        raise SparsePilotError("槽投影必须保持 ReplaySnapshot 类型")
    snapshot_ids = snapshot_ids_v1(projected_snapshots)
    rows = []
    for snapshot, projection, count, snapshot_id in zip(
        worker.snapshots, projections, worker.slot_counts, snapshot_ids
    ):
        if not isinstance(count, WorkerSlotCount):
            raise SparsePilotError("worker slot count 类型非法")
        projected = projection.projected
        if not isinstance(projected, ReplaySnapshot):
            raise SparsePilotError("槽投影必须保持 ReplaySnapshot 类型")
        summary = _snapshot_record(projection, snapshot_id)
        source = SampleSourceRef(
            ref_type="state_shard",
            ref_id=snapshot_id,
            sha256=_sha256_bytes(_json_bytes(summary)),
        )
        missing = count.missing_reasons[0] if count.missing_reasons else None
        # withdraw 的 RouteLastChange 按事实没有 AS_PATH，会被 mapped 影响量投影
        # 保守排除；证据链接必须仍来自原始 worker 槽变化，不能因此丢失撤回。
        route_ids = tuple(
            sorted({change.raw_ref.route_event_id for change in snapshot.slot_changes})
        )
        rows.append(
            SlotResearchMetadata(
                snapshot_id=snapshot_id,
                announce_count=_slot_count(count.retained_announce_count, missing),
                withdraw_count=_slot_count(count.retained_withdraw_count, missing),
                # baseline seed 保留了 VP 人口，但 worker 未保留逐槽参与 VP；
                # 不能从最终路由状态反推逐槽观测覆盖。
                vp_expected_count=unknown_slot_count(
                    "unknown_source_gap", "vp_slot_expected_population_not_retained"
                ),
                vp_observed_count=unknown_slot_count(
                    "unknown_source_gap", "vp_slot_observation_not_retained"
                ),
                source_refs=(source,),
                route_event_ids=route_ids,
                route_link_missing_reason_zh=(
                    None
                    if route_ids
                    else "该稀疏五分钟槽没有保留可链接的路由变化事件。"
                ),
            )
        )
    return tuple(rows)


def _evidence_rows(
    route_events: Sequence[ResearchRouteEvent],
    raw_audits: Sequence[RawRecordEvidence],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    audits = {
        (row.artifact_id, row.record_ordinal): row for row in raw_audits
    }
    if len(audits) != len(raw_audits):
        raise SparsePilotError("physical raw audit 坐标重复")
    route_rows = []
    raw_rows = []
    used_physical: set[tuple[str, int]] = set()
    seen_routes: set[str] = set()
    seen_raw: set[str] = set()
    for event in sorted(route_events, key=lambda row: row.route_event_id):
        if event.route_event_id in seen_routes:
            raise SparsePilotError("RouteEvent 身份重复")
        seen_routes.add(event.route_event_id)
        key = (event.artifact_id, event.record_ordinal)
        audit = audits.get(key)
        if audit is None or audit.file_sha256 != event.file_sha256:
            raise SparsePilotError("RouteEvent 缺少同制品同 ordinal raw audit")
        used_physical.add(key)
        raw_id = _raw_record_ref_id(
            event.file_sha256, event.record_ordinal, event.element_ordinal
        )
        if raw_id in seen_raw:
            raise SparsePilotError("raw record element 身份重复")
        seen_raw.add(raw_id)
        route_rows.append(
            {
                **_jsonable(event),
                "raw_record_ref_id": raw_id,
                "semantics": "route_observation",
                "lineage_status": "raw_traceable",
            }
        )
        raw_rows.append(
            {
                "raw_record_ref_id": raw_id,
                "artifact_id": audit.artifact_id,
                "file_sha256": audit.file_sha256,
                "collector_id": audit.collector_id,
                "artifact_slot_utc": audit.artifact_slot_utc,
                "record_ordinal": audit.record_ordinal,
                "element_ordinal": event.element_ordinal,
                "record_offset": audit.record_offset,
                "record_length": audit.record_length,
                "record_hash": audit.raw_record_sha256,
                "event_time_utc": audit.event_time_utc,
                "vp_id": event.vp_id,
                "vp_asn": event.peer_asn,
                "verification_status": "verified",
            }
        )
    raw_only = [
        {
            **_jsonable(row),
            "reason": "physical_record_without_retained_route_event",
        }
        for key, row in sorted(audits.items())
        if key not in used_physical
    ]
    return route_rows, raw_rows, raw_only


def _incident_source_fact(incident_ref: str) -> Mapping[str, Any]:
    """从旧系统 detail URL 构造未解析 Incident locator。

    Incident 身份严格复用 P0 事实规范化的 ``parse_detail_url``，后者会按
    ``event_type/start_time/problem/event_id/source`` 调用唯一的
    ``incident_id_v1`` 算法。这个 pilot 没有事实表只读快照输入，因此不能把
    locator 命中冒充事实核验成功，``fact_link_status`` 必须保持 unresolved。
    """

    try:
        locator = parse_detail_url(incident_ref)
    except LocatorError as error:
        raise SparsePilotError(
            "incident_ref 不是可规范化的五段式 detail URL"
        ) from error
    if (
        locator["event_type"] != "country_outage"
        or locator["normalized_problem"] != "IR"
    ):
        raise SparsePilotError("本 pilot 只接受伊朗 country_outage Incident")
    phase = {
        "source_field": "detail_url",
        "semantics": "route_observation_not_causal_trace",
        "supports_recovery": False,
        "status": "not_retained",
        "missing_reason": "legacy_field_not_retained",
        "observations": None,
    }
    return {
        "schema_version": "p0_incident_normalization_v1",
        "incident_id": locator["incident_id"],
        "incident_id_schema": "incident_id_v1",
        "event_type": locator["event_type"],
        "source_code": locator["source"],
        "source_table": locator["source_table"],
        "source_primary_key": dict(locator["locator_key"]),
        "detail_reference": locator["detail_reference"],
        "event_time_utc": locator["event_time_utc"],
        "end_time_utc": None,
        "duration_seconds": None,
        "risk_level": None,
        "affected_objects": [
            {
                "object_type": "country",
                "object_id": locator["normalized_problem"],
                "role": "affected",
                "source_field": "detail_url.problem",
            }
        ],
        "collection_quality": [
            "legacy_time_interpreted_as_asia_shanghai",
            "legacy_fact_snapshot_not_supplied",
        ],
        "phase_coverage": {"before": phase, "during": phase, "after": phase},
        "fact_link_status": "unresolved",
        "field_quality": [
            {
                "field": "source_fact",
                "status": "legacy_unknown",
                "missing_reason": "fact_snapshot_not_supplied",
            },
            {
                "field": "detector_version",
                "status": "not_retained",
                "missing_reason": "legacy_field_not_retained",
            }
        ],
        "collision_group_id": None,
        "quarantine_id": None,
        "detector_version": None,
        "classification": "observation_only",
        "causal_conclusion": None,
    }


def _quality_facts(worker: BoundedPilotWorkerResult) -> tuple[DiagnosticFact, ...]:
    passed = {
        "input_completeness": False,
        "parse_completeness": worker.status == "complete" and not worker.errors,
        "state_continuity": not worker.gaps,
        "vp_coverage": False,
        "mapping_coverage": not worker.ambiguity.quality_blockers,
        "stable_identity": True,
        "reference_closure": True,
        "missing_semantics": True,
        "resource_usage": (
            worker.resources.get("database_writes") == 0
            and worker.resources.get("resource_gate", {}).get("decision") == "allowed"
        ),
        "reproducibility": True,
    }
    details = {
        "input_completeness": "本次只抽取五个 UPDATE 槽，未覆盖槽明确保留为缺口。",
        "parse_completeness": "已处理制品均完成单次读取、文件哈希与解析状态核验。",
        "state_continuity": "稀疏抽样无法形成完整状态连续性，后续指标保持未知。",
        "vp_coverage": "当前 worker 未保留逐五分钟槽的 VP 参与人口，未伪造覆盖率。",
        "mapping_coverage": "冻结兼容映射及未决 origin 人口已显式记录。",
        "stable_identity": "RouteEvent、raw ref、selection 与研究包使用稳定内容身份。",
        "reference_closure": "本次发布前已核对 RouteEvent 到 raw record 与 artifact 坐标。",
        "missing_semantics": "所有输入缺口均以空值和缺失原因表达，没有补零。",
        "resource_usage": "实际读取、临时空间、单 worker 时间和数据库写入已通过门禁。",
        "reproducibility": "同一份真实 worker 内存结果已在两个空目录独立派生并比对。",
    }
    return tuple(
        DiagnosticFact(
            gate_id=gate,
            code=f"sparse_pilot.{gate}",
            passed=passed[gate],
            details_zh=details[gate],
        )
        for gate in GATE_ORDER
    )


def _worker_semantic_fingerprint(
    worker: BoundedPilotWorkerResult,
    seed_projection: MappedCompatibleProjection,
    snapshot_projections: Sequence[MappedCompatibleProjection],
) -> str:
    semantic = {
        "schema": "rrc25_sparse_worker_semantic_v1",
        "selection_id": worker.selection_id,
        "status": worker.status,
        "pilot_start_utc": worker.pilot_start_utc,
        "pilot_end_exclusive_utc": worker.pilot_end_exclusive_utc,
        "seed_projection_id": seed_projection.projection_id,
        "snapshot_projection_ids": [row.projection_id for row in snapshot_projections],
        "route_event_ids": sorted(row.route_event_id for row in worker.route_events),
        "raw_coordinates": sorted(
            [row.file_sha256, row.record_ordinal, row.raw_record_sha256]
            for row in worker.raw_audits
        ),
        "slot_counts": [_jsonable(row) for row in worker.slot_counts],
        "ambiguity": _jsonable(worker.ambiguity),
        "gaps": [_jsonable(row) for row in worker.gaps],
        "errors": list(worker.errors),
    }
    return hashlib.sha256(canonical_json(semantic).encode("utf-8")).hexdigest()


def _validated_parser_runtime_statistics(
    factory: Any,
    updates: Sequence[Mapping[str, Any]],
) -> Mapping[str, Mapping[str, Any]]:
    statistics = factory.statistics_by_artifact
    expected = {row["artifact_id"]: row for row in updates}
    if set(statistics) != set(expected):
        raise SparsePilotError("parser runtime statistics 未覆盖全部且仅覆盖抽样 UPDATE")
    counter_fields = (
        "physical_record_count",
        "route_record_count",
        "state_change_record_count",
        "open_record_count",
        "notification_record_count",
        "keepalive_record_count",
        "route_element_count",
        "announce_count",
        "withdraw_count",
    )
    normalized = {}
    for artifact_id in sorted(expected):
        row = statistics[artifact_id]
        artifact = expected[artifact_id]
        if (
            row.get("status") != "complete"
            or row.get("compressed_file_sha256") != artifact["file_sha256"]
            or row.get("compressed_size_bytes") != artifact["size_bytes"]
            or row.get("compressed_read_passes") != 1
        ):
            raise SparsePilotError("parser runtime statistics 缺少完整单 pass 证明")
        for field in counter_fields:
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SparsePilotError(f"parser runtime statistics.{field} 非法")
        physical_population = sum(
            int(row[field])
            for field in (
                "route_record_count",
                "state_change_record_count",
                "open_record_count",
                "notification_record_count",
                "keepalive_record_count",
            )
        )
        if physical_population != row["physical_record_count"]:
            raise SparsePilotError("parser physical record 分类人口不闭合")
        normalized[artifact_id] = _jsonable(row)
    return normalized


def _worker_summary(
    *,
    worker: BoundedPilotWorkerResult,
    full_selection: Mapping[str, Any],
    sparse_selection: Mapping[str, Any],
    execution_update_allowlist: Mapping[str, Any],
    worker_plan: Mapping[str, Any],
    seed_projection: MappedCompatibleProjection,
    snapshot_projections: Sequence[MappedCompatibleProjection],
    parser_attestation: Mapping[str, Any],
    parser_runtime_statistics: Mapping[str, Mapping[str, Any]],
    semantic_fingerprint: str,
    seed_sample_facts: Mapping[str, Any],
) -> Mapping[str, Any]:
    plan_artifacts = _worker_plan_artifacts(worker_plan)
    roles = sparse_selection["roles"]
    fully_processed_ids = set(execution_update_allowlist["artifact_ids"])
    sparse_update_ids = {
        row["artifact_id"] for row in roles["analysis_updates"]
    }
    partially_processed_ids = {seed_sample_facts["seed_artifact_id"]}
    return {
        "schema_version": PILOT_SCHEMA_VERSION,
        "execution_mode": "bounded_pilot",
        "sampling_mode": "explicit_sparse_update_slots_with_execution_allowlist",
        "acceptance_state": "not_accepted",
        "worker_status": worker.status,
        "incomplete_reason": worker.incomplete_reason,
        "selection_id": worker.selection_id,
        "worker_plan_sha256": worker_plan["worker_plan_sha256"],
        "pilot_start_utc": worker.pilot_start_utc,
        "pilot_end_exclusive_utc": worker.pilot_end_exclusive_utc,
        "full_pilot_selected_artifact_count": len(plan_artifacts),
        "processed_artifact_ids": sorted(fully_processed_ids),
        "partially_processed_artifact_ids": sorted(partially_processed_ids),
        "unprocessed_selected_artifact_ids": sorted(
            set(plan_artifacts) - fully_processed_ids
        ),
        "unopened_selected_artifact_ids": sorted(
            set(plan_artifacts) - fully_processed_ids - partially_processed_ids
        ),
        "selected_rib_count": sum(
            row.get("artifact_type") == "rib" for row in plan_artifacts.values()
        ),
        "processed_rib_count": 0,
        "bounded_seed_rib_sample_count": 1,
        "full_seed_rib_complete": False,
        "seed_sample": dict(seed_sample_facts),
        "selected_update_count": sum(
            row.get("artifact_type") == "update" for row in plan_artifacts.values()
        ),
        "processed_update_count": len(fully_processed_ids),
        "full_selection_coverage": full_selection.get("coverage"),
        "sparse_selection_coverage": sparse_selection.get("coverage"),
        "execution_update_allowlist": dict(execution_update_allowlist),
        "intentionally_unprocessed_sparse_update_ids": sorted(
            sparse_update_ids - fully_processed_ids
        ),
        "snapshot_count": len(worker.snapshots),
        "route_event_count": len(worker.route_events),
        "raw_physical_audit_count": len(worker.raw_audits),
        "gap_count": len(worker.gaps),
        "ambiguity": _jsonable(worker.ambiguity),
        "resources": _jsonable(worker.resources),
        "database_connections": 0,
        "database_write_operations": 0,
        "parser_attestation": dict(parser_attestation),
        "parser_runtime_statistics": dict(parser_runtime_statistics),
        "seed_projection": _projection_summary(seed_projection),
        "snapshot_projection_count": len(snapshot_projections),
        "worker_semantic_fingerprint_sha256": semantic_fingerprint,
        "limitations_zh": [
            "本次只消费一个明确截断的 seed RIB checkpoint，并完整处理 {:d} 个执行白名单 UPDATE 槽；没有重开或完整解析 seed RIB。".format(
                len(fully_processed_ids)
            ),
            "稀疏 selection 中未进入本次执行白名单的 UPDATE 槽保持 gap/unknown，不能被解释为源文件缺失。",
            "未抽中的窗口槽保持 gap/unknown，不能据此确认连续 episode、恢复或完整影响人口。",
            "worker plan 中另外三张 RIB 只完成选择与哈希绑定，未在本 worker 中解析。",
            "seed 样本只覆盖 checkpoint 前 {:,} 个完整物理记录边界，剩余 RIB 明确保留为 unknown。".format(
                int(seed_sample_facts["next_record_ordinal"])
            ),
            "逐槽 VP 参与人口未保留，因此 VP 覆盖指标保持未知。",
        ],
    }


def _snapshot_index(
    projections: Sequence[MappedCompatibleProjection],
) -> list[Mapping[str, Any]]:
    snapshots = tuple(projection.projected for projection in projections)
    if any(not isinstance(row, ReplaySnapshot) for row in snapshots):
        raise SparsePilotError("snapshot index 投影类型非法")
    return [
        _snapshot_record(projection, snapshot_id)
        for projection, snapshot_id in zip(projections, snapshot_ids_v1(snapshots))
    ]


def _content_ref(kind: str, path: str, payload: bytes, record_count: int) -> Mapping[str, Any]:
    return {
        "kind": kind,
        "path": path,
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
        "record_count": record_count,
    }


def _augmented_package(
    assembly: DerivedResearchAssembly,
    supplemental: Mapping[str, tuple[str, bytes, int]],
) -> tuple[Mapping[str, bytes], Mapping[str, Any]]:
    contents = dict(assembly.package_contents)
    kinds = {row["path"]: row["kind"] for row in assembly.package_manifest["contents"]}
    counts = {
        row["path"]: int(row["record_count"])
        for row in assembly.package_manifest["contents"]
    }
    for path, (kind, payload, count) in supplemental.items():
        if path in contents:
            raise SparsePilotError(f"补充研究内容路径冲突：{path}")
        contents[path] = payload
        kinds[path] = kind
        counts[path] = count
    refs = tuple(
        _content_ref(kinds[path], path, payload, counts[path])
        for path, payload in sorted(contents.items())
    )
    base = assembly.package_manifest
    manifest = build_package_manifest(
        run_id=base["run_id"],
        study_id=base["study_id"],
        incident_ref=base["incident_ref"],
        execution_mode=base["execution_mode"],
        acceptance_state=base["acceptance_state"],
        bindings=base["bindings"],
        contents=refs,
    )
    return dict(sorted(contents.items())), manifest


def _assemble_once(
    *,
    profile: Mapping[str, Any],
    run_id: str,
    baseline_state: Any,
    snapshots: Sequence[ReplaySnapshot],
    mapping: Any,
    slot_metadata: Sequence[SlotResearchMetadata],
    claims: Mapping[str, Any],
    worker: BoundedPilotWorkerResult,
    sparse_selection: Mapping[str, Any],
    semantic_fingerprint: str,
    package_bindings: Mapping[str, str],
) -> DerivedResearchAssembly:
    return assemble_derived_research(
        profile=profile,
        run_id=run_id,
        execution_mode="bounded_pilot",
        baseline_snapshot=baseline_state,
        snapshots=snapshots,
        mapping=mapping,
        slot_metadata=slot_metadata,
        incidents=(_incident_source_fact(str(claims["incident_ref"])),),
        claim_inventory=claims,
        reconciliation_assessments="auto",
        quality_facts=_quality_facts(worker),
        execution={
            "database_write_operations": 0,
            "new_raw_bytes_read": int(worker.resources["new_raw_read_bytes"]),
            "peak_temporary_bytes": int(worker.resources["peak_temporary_bytes"]),
            "max_worker_seconds": float(worker.resources["max_worker_elapsed_seconds"]),
        },
        semantic_fingerprints=(semantic_fingerprint, semantic_fingerprint),
        input_selection=sparse_selection,
        reproduction_commands=(
            "python3 dev/data_quality/rrc25_iran_bounded_pilot.py verify --package <研究包目录>",
        ),
        route_events_by_id={row.route_event_id: row for row in worker.route_events},
        confirmed_onset_at="2026-02-28T08:14:00Z",
        limitations_zh=(
            "这是五个 UPDATE 槽的稀疏真实取证 pilot，不是连续事件人口复算。",
            "前兆与主事件的因果关系在当前样本中不可判定。",
            "缺少流量、物理链路与政府意图遥测，因果主张不能确认。",
        ),
        package_bindings=package_bindings,
    )


def _assert_empty_directory(path_value: str | Path, field: str) -> Path:
    path = Path(path_value)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SparsePilotError(f"{field} 不存在或不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SparsePilotError(f"{field} 必须是非符号链接目录")
    try:
        if next(path.iterdir(), None) is not None:
            raise SparsePilotError(f"{field} 必须为空，拒绝覆盖或续跑混用")
    except OSError as error:
        raise SparsePilotError(f"{field} 无法扫描") from error
    return path.resolve(strict=True)


def _assert_research_write_targets(
    targets: Mapping[str, Path],
) -> None:
    """让 pilot 的所有写入目录复用 coordinator 的失败关闭路径门禁。"""

    gate = evaluate_resource_gate(
        ResourceUsage(
            new_raw_read_bytes=0,
            process_runtime_seconds=0,
            temporary_bytes=0,
            output_bytes=0,
            write_targets=tuple(
                WriteTarget(
                    label=label,
                    location=str(path.resolve(strict=False)),
                    kind=(
                        "checkpoint"
                        if label == "checkpoint_directory"
                        else "artifact"
                    ),
                )
                for label, path in sorted(targets.items())
            ),
            phase="estimated",
        ),
        limits=ResourceLimits(),
        protected_roots=tuple(
            str(Path(root).resolve(strict=False)) for root in DEFAULT_PROTECTED_ROOTS
        ),
        production_roots=tuple(
            str(Path(root).resolve(strict=False)) for root in DEFAULT_PRODUCTION_ROOTS
        ),
    )
    if gate.execution_allowed:
        return
    findings = ", ".join(
        "{}:{}".format(item.target_label or "unknown", item.code)
        for item in gate.findings
    )
    raise SparsePilotError(
        "研究写入路径未通过 coordinator 统一门禁：{}".format(findings)
    )


def _preflight_output_directories(args: argparse.Namespace) -> None:
    checkpoint = _assert_empty_directory(args.checkpoint_directory, "checkpoint_directory")
    output_a = _assert_empty_directory(args.output_a, "output_a")
    output_b = _assert_empty_directory(args.output_b, "output_b")
    coordinator_root = Path(args.coordinator_output_root).resolve(strict=True)
    raw_root = Path(args.raw_root).resolve(strict=True)
    repo_root = REPOSITORY_ROOT.resolve(strict=True)
    _assert_research_write_targets(
        {
            "checkpoint_directory": checkpoint,
            "coordinator_output_root": coordinator_root,
            "output_a": output_a,
            "output_b": output_b,
        }
    )
    if output_a == output_b or output_a in output_b.parents or output_b in output_a.parents:
        raise SparsePilotError("output_a 与 output_b 必须是互不嵌套的独立空目录")
    targets = (checkpoint, output_a, output_b)
    for target in targets:
        for protected, label in (
            (raw_root, "raw_root"),
            (repo_root, "repository_root"),
        ):
            if target == protected or target in protected.parents or protected in target.parents:
                raise SparsePilotError(f"研究输出不得与 {label} 重叠或嵌套")
    if coordinator_root in targets:
        raise SparsePilotError("coordinator_output_root 不得复用 worker/包输出目录")
    readonly_inputs = tuple(
        Path(value).resolve(strict=True)
        for value in (
            args.seed_sample_checkpoint,
            args.seed_producer_code_identity,
            args.seed_producer_worker_plan,
        )
    )
    for source in readonly_inputs:
        for target in targets:
            if source == target or source in target.parents or target in source.parents:
                raise SparsePilotError("seed producer 只读输入不得与研究输出重叠或嵌套")


def _run(
    args: argparse.Namespace,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    process_started_at = clock()
    _preflight_output_directories(args)
    profile = validate_research_profile(load_json_metadata(args.profile))
    resource_limits = effective_resource_limits(profile)
    manifest = load_json_metadata(args.manifest, maximum_bytes=512 * 1024 * 1024)
    verification = load_json_metadata(args.manifest_verification)
    mapping_snapshot = load_json_metadata(args.mapping, maximum_bytes=64 * 1024 * 1024)
    claims = load_json_metadata(args.claim_inventory)
    worker_plan = load_json_metadata(args.worker_plan, maximum_bytes=64 * 1024 * 1024)
    code_identity = _load_code_identity(args.code_identity, args.code_sha256)

    resolver_profile = _pilot_resolver_profile(profile, args.pilot_end_exclusive)
    full_selection = resolve_research_inputs(manifest, verification, resolver_profile)
    update_ids = _selected_update_ids(full_selection, args.update_slot)
    sparse_selection = build_sparse_pilot_selection(full_selection, update_ids)
    execution_update_allowlist = _execution_update_allowlist(
        sparse_selection, args.execute_update_slot
    )
    _verify_plan_bindings(
        worker_plan,
        profile=profile,
        artifact_manifest=manifest,
        manifest_verification=verification,
        mapping_snapshot=mapping_snapshot,
        code_sha256=args.code_sha256,
        pilot_end_exclusive=args.pilot_end_exclusive,
        sparse_selection=sparse_selection,
        coordinator_output_root=args.coordinator_output_root,
    )
    seed_provenance = _load_seed_sample_provenance(
        checkpoint_path=args.seed_sample_checkpoint,
        checkpoint_sha256=args.seed_sample_checkpoint_sha256,
        producer_code_identity_path=args.seed_producer_code_identity,
        producer_code_sha256=args.seed_producer_code_sha256,
        producer_worker_plan_path=args.seed_producer_worker_plan,
        current_worker_plan=worker_plan,
        sparse_selection=sparse_selection,
        pilot_end_exclusive=args.pilot_end_exclusive,
    )
    mapping = mapping_view_from_frozen_snapshot(mapping_snapshot)
    sparse_updates_by_id = {
        row["artifact_id"]: row
        for row in sparse_selection["roles"]["analysis_updates"]
    }
    updates = tuple(
        sparse_updates_by_id[artifact_id]
        for artifact_id in execution_update_allowlist["artifact_ids"]
    )
    factory = make_bgpdump_record_stream_factory(
        args.raw_root,
        updates,
        data_profile={
            "window_start_utc": sparse_selection["window"]["start_utc"],
            "window_end_exclusive_utc": sparse_selection["window"][
                "end_exclusive_utc"
            ],
        },
        pilot_limits={
            "max_artifact_count": len(updates),
            "max_compressed_bytes": sum(int(row["size_bytes"]) for row in updates),
            "max_physical_records": _MAX_PHYSICAL_RECORDS,
            "max_route_events": _MAX_ROUTE_EVENTS,
            "max_spool_bytes": _MAX_SPOOL_BYTES,
        },
        bgpdump_path=args.bgpdump_path,
        expected_version=BGPDUMP_APPROVED_VERSION,
        allowed_binary_sha256=(args.bgpdump_sha256,),
        queue_capacity=args.bgpdump_queue_capacity,
        max_stdout_queue_source_bytes=args.bgpdump_queue_source_bytes,
        idle_timeout_seconds=args.idle_timeout_seconds,
    )
    worker = run_bounded_pilot_worker(
        sparse_selection,
        artifact_root=args.raw_root,
        country_mapping=mapping,
        pilot_end_exclusive_utc=args.pilot_end_exclusive,
        update_record_stream_factory=factory,
        checkpoint_directory=args.checkpoint_directory,
        seed_sample_checkpoint_path=args.seed_sample_checkpoint,
        analysis_update_artifact_ids=execution_update_allowlist["artifact_ids"],
        resource_limits=resource_limits,
        clock=clock,
        process_started_at=process_started_at,
    )
    if worker.status != "complete":
        raise SparsePilotError(
            "真实 worker 未完整结束；已停止且不继续派生："
            + str(worker.incomplete_reason)
        )
    if worker.checkpoint_path is not None:
        raise SparsePilotError("完整 worker 不应留下恢复路径")
    if worker.seed_state_at_window_start is None:
        raise SparsePilotError("真实 worker 缺少窗口起点 seed 状态")

    def require_runtime(stage: str) -> Mapping[str, Any]:
        return _require_cumulative_runtime_budget(
            stage=stage,
            process_started_at=process_started_at,
            clock=clock,
            limits=resource_limits,
            new_raw_read_bytes=int(worker.resources["new_raw_read_bytes"]),
            peak_temporary_bytes=int(worker.resources["peak_temporary_bytes"]),
        )

    require_runtime("worker_complete_before_derived_work")
    if seed_provenance["checkpoint_bytes"] != _read_regular_bytes(
        args.seed_sample_checkpoint, maximum_bytes=128 * 1024 * 1024
    ):
        raise SparsePilotError("seed sample checkpoint 在 worker 执行期间发生变化")
    if seed_provenance["producer_code_identity_bytes"] != _read_regular_bytes(
        args.seed_producer_code_identity, maximum_bytes=8 * 1024 * 1024
    ):
        raise SparsePilotError("seed producer code identity 在 worker 执行期间发生变化")
    if seed_provenance["producer_worker_plan_bytes"] != _read_regular_bytes(
        args.seed_producer_worker_plan, maximum_bytes=64 * 1024 * 1024
    ):
        raise SparsePilotError("seed producer worker plan 在 worker 执行期间发生变化")
    parser_runtime_statistics = _validated_parser_runtime_statistics(
        factory, updates
    )

    require_runtime("before_mapped_projection")
    projection_series = build_mapped_compatible_projection_series(
        (worker.seed_state_at_window_start, *worker.snapshots), mapping
    )
    require_runtime("after_mapped_projection")
    seed_projection = projection_series[0]
    projections = projection_series[1:]
    projected_snapshots = tuple(row.projected for row in projections)
    if any(not isinstance(row, ReplaySnapshot) for row in projected_snapshots):
        raise SparsePilotError("投影未保持 ReplaySnapshot 类型")
    slot_metadata = _build_slot_metadata(worker, projections)
    semantic_fingerprint = _worker_semantic_fingerprint(
        worker, seed_projection, projections
    )
    mapping_binding = hashlib.sha256(
        canonical_json(dict(mapping_snapshot)).encode("utf-8")
    ).hexdigest()
    package_bindings = {
        "code": args.code_sha256,
        "worker-plan": worker_plan["worker_plan_sha256"],
        "sparse-selection": sparse_selection["semantic_fingerprint_sha256"],
        "execution-update-allowlist": execution_update_allowlist[
            "semantic_fingerprint_sha256"
        ],
        "mapping-snapshot": mapping_binding,
        "worker-semantic": semantic_fingerprint,
        "seed-sample-checkpoint": seed_provenance[
            "checkpoint_file_sha256"
        ],
        "seed-producer-code": seed_provenance["producer_code_identity"][
            "identity_sha256"
        ],
        "seed-producer-worker-plan": seed_provenance["producer_worker_plan"][
            "worker_plan_sha256"
        ],
    }
    run_id = worker_plan["run_id"]
    require_runtime("before_assembly_a")
    assembly_a = _assemble_once(
        profile=profile,
        run_id=run_id,
        baseline_state=seed_projection.projected,
        snapshots=projected_snapshots,
        mapping=mapping,
        slot_metadata=slot_metadata,
        claims=claims,
        worker=worker,
        sparse_selection=sparse_selection,
        semantic_fingerprint=semantic_fingerprint,
        package_bindings=package_bindings,
    )
    require_runtime("after_assembly_a")
    require_runtime("before_assembly_b")
    assembly_b = _assemble_once(
        profile=profile,
        run_id=run_id,
        baseline_state=seed_projection.projected,
        snapshots=projected_snapshots,
        mapping=mapping,
        slot_metadata=slot_metadata,
        claims=claims,
        worker=worker,
        sparse_selection=sparse_selection,
        semantic_fingerprint=semantic_fingerprint,
        package_bindings=package_bindings,
    )
    require_runtime("after_assembly_b")
    if (
        assembly_a.package_manifest != assembly_b.package_manifest
        or dict(assembly_a.package_contents) != dict(assembly_b.package_contents)
    ):
        raise SparsePilotError("同一 worker 结果的两次纯派生语义不一致")

    route_rows, raw_rows, raw_only_rows = _evidence_rows(
        worker.route_events, worker.raw_audits
    )
    summary = _worker_summary(
        worker=worker,
        full_selection=full_selection,
        sparse_selection=sparse_selection,
        execution_update_allowlist=execution_update_allowlist,
        worker_plan=worker_plan,
        seed_projection=seed_projection,
        snapshot_projections=projections,
        parser_attestation=factory.parser_attestation,
        parser_runtime_statistics=parser_runtime_statistics,
        semantic_fingerprint=semantic_fingerprint,
        seed_sample_facts=seed_provenance["facts"],
    )
    snapshot_rows = _snapshot_index(projections)
    supplemental = {
        "inputs/profile.json": ("profile", _json_bytes(profile), 1),
        "inputs/full-pilot-selection.json": (
            "input-selection",
            _json_bytes(full_selection),
            int(full_selection["selected_unique_artifact_count"]),
        ),
        "inputs/sparse-sample-selection.json": (
            "input-selection",
            _json_bytes(sparse_selection),
            int(sparse_selection["selected_unique_artifact_count"]),
        ),
        "inputs/execution-update-allowlist.json": (
            "input-selection",
            _json_bytes(execution_update_allowlist),
            len(execution_update_allowlist["artifact_ids"]),
        ),
        "inputs/mapping-snapshot.json": (
            "mapping",
            _json_bytes(mapping_snapshot),
            len(mapping_snapshot.get("rows", [])),
        ),
        "inputs/seed-sample-checkpoint.json": (
            "seed-sample-checkpoint",
            seed_provenance["checkpoint_bytes"],
            1,
        ),
        "execution/code-identity.json": ("code-identity", _json_bytes(code_identity), 1),
        "execution/seed-producer-code-identity.json": (
            "code-identity",
            seed_provenance["producer_code_identity_bytes"],
            1,
        ),
        "execution/seed-producer-worker-plan.json": (
            "worker-plan",
            seed_provenance["producer_worker_plan_bytes"],
            1,
        ),
        "execution/worker-plan.json": ("worker-plan", _json_bytes(worker_plan), 1),
        "execution/worker-summary.json": ("worker-summary", _json_bytes(summary), 1),
        "execution/parser-attestation.json": (
            "parser-attestation",
            _json_bytes(factory.parser_attestation),
            1,
        ),
        "execution/parser-runtime-statistics.json": (
            "parser-runtime-statistics",
            _json_bytes(parser_runtime_statistics),
            len(parser_runtime_statistics),
        ),
        "state/snapshot-index.jsonl.gz": (
            "snapshot-index",
            _gzip_jsonl(snapshot_rows),
            len(snapshot_rows),
        ),
        "data/route-events.jsonl.gz": (
            "route-events",
            _gzip_jsonl(route_rows),
            len(route_rows),
        ),
        "evidence/raw-record-refs.jsonl.gz": (
            "raw-record-refs",
            _gzip_jsonl(raw_rows),
            len(raw_rows),
        ),
        "evidence/raw-only-audits.jsonl.gz": (
            "raw-record-audits",
            _gzip_jsonl(raw_only_rows),
            len(raw_only_rows),
        ),
    }
    contents_a, manifest_a = _augmented_package(assembly_a, supplemental)
    contents_b, manifest_b = _augmented_package(assembly_b, supplemental)
    if manifest_a != manifest_b or contents_a != contents_b:
        raise SparsePilotError("补充证据后的两个研究包语义不一致")
    if manifest_a["acceptance_state"] != "not_accepted":
        raise SparsePilotError("稀疏 pilot 不得被标记为 accepted")

    # 540 秒软停为发布预留至少 60 秒安全裕量；达到软限或 600 秒硬限时，
    # 尚未开始的 A/B 研究目录不得写入。
    verified_a, verified_b = _publish_ab_with_runtime_gate(
        output_a=args.output_a,
        contents_a=contents_a,
        manifest_a=manifest_a,
        output_b=args.output_b,
        contents_b=contents_b,
        manifest_b=manifest_b,
        process_started_at=process_started_at,
        clock=clock,
        limits=resource_limits,
        new_raw_read_bytes=int(worker.resources["new_raw_read_bytes"]),
        peak_temporary_bytes=int(worker.resources["peak_temporary_bytes"]),
    )
    if verified_a != verified_b:
        raise SparsePilotError("两个发布目录的重新读取核验结果不一致")
    control_record_totals = {
        field: sum(int(row[field]) for row in parser_runtime_statistics.values())
        for field in (
            "state_change_record_count",
            "open_record_count",
            "notification_record_count",
            "keepalive_record_count",
        )
    }
    return {
        "ok": True,
        "schema_version": PILOT_SCHEMA_VERSION,
        "run_id": manifest_a["run_id"],
        "release_id": manifest_a["release_id"],
        "semantic_fingerprint_sha256": manifest_a[
            "semantic_fingerprint_sha256"
        ],
        "worker_semantic_fingerprint_sha256": semantic_fingerprint,
        "execution_mode": "bounded_pilot",
        "acceptance_state": "not_accepted",
        "processed_update_slots": list(execution_update_allowlist["slots"]),
        "sparse_selected_update_slots": sorted(args.update_slot),
        "processed_update_count": len(updates),
        "processed_rib_count": 0,
        "bounded_seed_rib_sample_count": 1,
        "full_seed_rib_complete": False,
        "seed_sample_next_record_ordinal": seed_provenance["facts"][
            "next_record_ordinal"
        ],
        "seed_sample_checkpoint_sha256": seed_provenance[
            "checkpoint_file_sha256"
        ],
        "full_pilot_selected_artifact_count": len(_worker_plan_artifacts(worker_plan)),
        "route_event_count": len(route_rows),
        "raw_record_ref_count": len(raw_rows),
        "parser_control_record_totals": control_record_totals,
        "snapshot_count": len(snapshot_rows),
        "episode_count": len(assembly_a.episodes),
        "quality_run_state": assembly_a.quality["run_state"],
        "resources": _jsonable(worker.resources),
        "package_a_verified": True,
        "package_b_verified": True,
        "database_connections": 0,
        "database_write_operations": 0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RRC25 伊朗事件最多五个 UPDATE 槽的真实稀疏证据 pilot"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="只读解析一次 MRT，并纯派生两个研究包")
    for name in (
        "profile",
        "manifest",
        "manifest-verification",
        "mapping",
        "claim-inventory",
        "worker-plan",
        "code-identity",
        "code-sha256",
        "seed-sample-checkpoint",
        "seed-sample-checkpoint-sha256",
        "seed-producer-code-identity",
        "seed-producer-code-sha256",
        "seed-producer-worker-plan",
        "raw-root",
        "checkpoint-directory",
        "coordinator-output-root",
        "output-a",
        "output-b",
        "bgpdump-path",
        "bgpdump-sha256",
        "pilot-end-exclusive",
    ):
        run.add_argument("--" + name, required=True)
    run.add_argument(
        "--update-slot",
        action="append",
        required=True,
        help="与 producer checkpoint 绑定的稀疏五分钟 UTC 槽，可重复 1..5 次",
    )
    run.add_argument(
        "--execute-update-slot",
        action="append",
        required=True,
        help="本次真实完整读取的稀疏槽子集，可重复 1..5 次；其余槽保持 gap/unknown",
    )
    run.add_argument("--idle-timeout-seconds", type=float, default=30.0)
    run.add_argument(
        "--bgpdump-queue-capacity",
        type=int,
        default=4096,
        help="研究运行 stdout 有界队列 item 上限；适配器绝对上限为 4096",
    )
    run.add_argument(
        "--bgpdump-queue-source-bytes",
        type=int,
        default=8 * 1024 * 1024,
        help="研究运行 stdout 队列原始行字节预算；适配器绝对上限为 8 MiB",
    )
    verify = subparsers.add_parser("verify", help="只读重新核验已发布研究包")
    verify.add_argument("--package", required=True)
    subparsers.add_parser("code-identity", help="输出当前研究处理代码的确定性身份")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "code-identity":
            print(
                json.dumps(
                    build_code_identity(),
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
            )
            return 0
        if args.command == "verify":
            manifest = verify_published_package(args.package)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "run_id": manifest["run_id"],
                        "release_id": manifest["release_id"],
                        "semantic_fingerprint_sha256": manifest[
                            "semantic_fingerprint_sha256"
                        ],
                        "acceptance_state": manifest["acceptance_state"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
            )
            return 0
        result = _run(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(error).__name__,
                    "message_zh": str(error),
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
