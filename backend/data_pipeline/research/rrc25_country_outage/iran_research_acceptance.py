"""伊朗国家路由中断研究闭环的有界对账与总验收。

本模块把两个已经独立闭合的证据域连接起来：

* 严格的 ``rrc25-full-window-reproduction-acceptance/v2`` 同时绑定两目录的
  完整业务语义 core 与同一 sealed finalization segment core；它明确不是
  raw MRT A/B，旧 v1 receipt 不具备总验收准入资格；
* ``analysis_rib_anchor`` 的 21 张窗口内 RIB 与 1 张窗口前 baseline RIB
  提供与 UPDATE 回放完全独立的路由快照。

为了不让一个进程跨越十分钟门，1928 个 UPDATE 槽不会在总装进程中一次性
重放。workspace 把工作拆成 22 个可恢复 segment：窗口起点、20 个八小时
区间以及最后 40 分钟 tail。每个 segment 只消费 v2 自包含包中已经深验的
sealed payload/receipt，以前一 segment 的内容寻址 compact state 为起点，
并由 CLI 的 420/540/590 秒父 supervisor 独立执行，且父进程在 596 秒前
有界退出。总装与离线 verify 只
核验这些 create-only segment 的链和来源哈希，不读取 journal_root，也不
回读 record observations。

安全边界：不打开 MRT、不连接数据库、不写生产目录、不把 baseline RIB
冒充 UPDATE 边界，也绝不以 RIB 重置 UPDATE 曲线。最终 receipt 的 accepted
含义固定为 ``research_loop_accepted_not_production_or_causal_truth``。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple

from .analysis_rib_anchor import (
    ANCHOR_RECEIPT_SCHEMA_VERSION,
    DEFAULT_MAX_RAW_READ_BYTES,
    DEFAULT_MAX_TEMPORARY_BYTES,
    EXPECTED_ANALYSIS_RIB_COUNT,
    EXPECTED_ANCHOR_COUNT,
    EXPECTED_BASELINE_RIB_COUNT,
    PROJECTION_SEMANTICS,
    _load_canonical_json as _load_anchor_json,
    _load_genesis as _load_anchor_genesis,
    _projection_sha256,
    _verify_shard_ref as _verify_anchor_shard_ref,
    build_source_independent_route_projection,
    verify_analysis_rib_anchor_root,
)
from .country_impact import (
    mapping_bundle_sha256,
    mapping_view_from_frozen_snapshot,
    mapping_view_from_revised_snapshot,
)
from .coordinator import DEFAULT_PRODUCTION_ROOTS, DEFAULT_PROTECTED_ROOTS
from .file_artifacts import canonical_json, write_canonical_json, write_canonical_jsonl_gzip
from .full_window_finalize import (
    _canonical_hash,
    _iter_shard_rows,
    _load_json,
    _read_stable_regular,
    _rename_directory_no_replace,
)
from .full_window_worker import compact_state_from_payload
from . import full_window_finalize_workspace as _finalization_workspace
from . import full_window_journal as _journal_contract
from .package_manifest import _manifest_semantic
from .profile import profile_sha256
from .full_window_selection import validate_complete_selection_against_profile


UTC = timezone.utc

WORKSPACE_GENESIS_SCHEMA_VERSION = "rrc25-iran-research-acceptance-workspace/v1"
ANCHOR_GATE_SCHEMA_VERSION = "rrc25-iran-analysis-rib-deep-verification-gate/v1"
SEGMENT_SCHEMA_VERSION = "rrc25-iran-update-rib-reconciliation-segment/v1"
OVERALL_ACCEPTANCE_SCHEMA_VERSION = "rrc25-iran-overall-research-acceptance/v1"
UPDATE_ACCEPTANCE_SCHEMA_VERSION = (
    _finalization_workspace.WORKSPACE_REPRODUCTION_ACCEPTANCE_SCHEMA
)
UPDATE_REPRODUCTION_SCOPE = (
    "independent_package_assembly_from_same_verified_finalization_segments"
)
ACCEPTANCE_SEMANTICS = "research_loop_accepted_not_production_or_causal_truth"
RECONCILIATION_SEMANTICS = (
    "independent_rib_vs_carried_update_projection_by_vp_afi_prefix_v1"
)
SCAN_SEMANTICS = "compatible_episode_as_four_category_full_population_scan_v1"
SUPERVISOR_SEMANTICS = (
    "independent_child_420_observe_540_term_590_kill_596_exit_v1"
)

DEFAULT_OBSERVATION_SECONDS = 420.0
DEFAULT_TERM_SECONDS = 540.0
DEFAULT_KILL_SECONDS = 590.0
DEFAULT_PARENT_EXIT_SECONDS = 596.0
DEFAULT_SEGMENT_SELF_STOP_SECONDS = 535.0
MAX_DIFFERENCE_SAMPLES_PER_CLASS = 8
MAX_CATEGORY_SAMPLES = 3

EXPECTED_BOUNDARY_COUNT = 21
EXPECTED_SEGMENT_COUNT = 22

_REQUIRED_BUSINESS_JSON_PATHS = frozenset(
    {
        "metadata/finalization.json",
        "quality-and-accounting.json",
        "reconciliation.json",
        "frozen/profile.json",
        "frozen/source-fact.json",
        "frozen/incident-policy.json",
        "frozen/compatible-mapping.json",
        "frozen/revised-mapping.json",
        "frozen/code-identity.json",
        "frozen/input-selection.json",
        "frozen/claim-inventory.json",
        "frozen/bindings.json",
        "data/compatible-baseline.json",
        "data/revised-baseline.json",
    }
)
_REQUIRED_BUSINESS_SEQUENCE_PATHS = frozenset(
    {
        "data/compatible-country-samples.jsonl.gz",
        "data/revised-country-samples.jsonl.gz",
        "data/compatible-sample-measurement-semantics.jsonl.gz",
        "data/revised-sample-measurement-semantics.jsonl.gz",
        "data/compatible-episodes.jsonl.gz",
        "data/compatible-waves.jsonl.gz",
        "data/revised-episodes.jsonl.gz",
        "data/revised-waves.jsonl.gz",
        "data/compatible-episode-as.jsonl.gz",
        "data/compatible-episode-as-measurement-semantics.jsonl.gz",
        "data/compatible-prefix-impact.jsonl.gz",
        "data/revised-episode-as.jsonl.gz",
        "data/revised-episode-as-measurement-semantics.jsonl.gz",
        "data/revised-prefix-impact.jsonl.gz",
        "data/incident-episode-mappings.jsonl.gz",
    }
)
_REQUIRED_BUSINESS_REPORT_PATH = (
    "报告/RRC25伊朗国家路由中断事件复算与对账报告.md"
)
_REQUIRED_BUSINESS_PATHS = frozenset(
    {
        *_REQUIRED_BUSINESS_JSON_PATHS,
        *_REQUIRED_BUSINESS_SEQUENCE_PATHS,
        _REQUIRED_BUSINESS_REPORT_PATH,
    }
)
_BUSINESS_NONEMPTY_SEQUENCE_PATHS = frozenset(
    {
        "data/compatible-country-samples.jsonl.gz",
        "data/compatible-episodes.jsonl.gz",
        "data/compatible-waves.jsonl.gz",
        "data/compatible-episode-as.jsonl.gz",
        "data/compatible-prefix-impact.jsonl.gz",
        "data/incident-episode-mappings.jsonl.gz",
    }
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MUTATION_PROTECTED_ROOTS = tuple(
    Path(value).expanduser().resolve(strict=False)
    for value in (*DEFAULT_PROTECTED_ROOTS, *DEFAULT_PRODUCTION_ROOTS)
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


class IranResearchAcceptanceError(ValueError):
    """研究总验收的来源、segment 链或证据引用不能闭合。"""


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise IranResearchAcceptanceError(f"{field} 必须是 64 位小写 SHA256")
    return value


def _nonnegative(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IranResearchAcceptanceError(f"{field} 必须是非负整数")
    return value


def _positive_seconds(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IranResearchAcceptanceError(f"{field} 必须是正数")
    result = float(value)
    if result <= 0 or result != result or result in {float("inf"), float("-inf")}:
        raise IranResearchAcceptanceError(f"{field} 必须是有限正数")
    return result


def _utc(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise IranResearchAcceptanceError(f"{field} 必须是 UTC Z 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise IranResearchAcceptanceError(f"{field} 不是合法 UTC 时间") from error
    if parsed.utcoffset() != UTC.utcoffset(parsed) or parsed.microsecond:
        raise IranResearchAcceptanceError(f"{field} 必须是秒级 UTC")
    canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    if value != canonical:
        raise IranResearchAcceptanceError(f"{field} 不是规范 UTC")
    return value


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _safe_relative(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise IranResearchAcceptanceError(f"{field} 必须是非空相对路径")
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(
        part in {"", ".", ".."} for part in pure.parts
    ):
        raise IranResearchAcceptanceError(f"{field} 不是安全相对路径")
    return pure.as_posix()


def _assert_directory(path: Path, field: str) -> Path:
    target = path.expanduser().absolute()
    try:
        metadata = target.lstat()
    except OSError as error:
        raise IranResearchAcceptanceError(f"{field} 不存在或不可读") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise IranResearchAcceptanceError(f"{field} 必须是非符号链接目录")
    return target


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _assert_safe_mutation_target(
    path: Path,
    field: str,
    *,
    source_roots: Sequence[Path] = (),
) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    for protected in _MUTATION_PROTECTED_ROOTS:
        if resolved == protected or protected in resolved.parents:
            raise IranResearchAcceptanceError(
                f"{field} 不得写入受保护或生产目录：{protected}"
            )
    repository = _REPOSITORY_ROOT.resolve(strict=False)
    if _paths_overlap(resolved, repository):
        raise IranResearchAcceptanceError(f"{field} 不得与代码仓库重叠")
    for source in source_roots:
        normalized = source.expanduser().resolve(strict=False)
        if _paths_overlap(resolved, normalized):
            raise IranResearchAcceptanceError(
                f"{field} 不得与只读证据来源重叠：{normalized}"
            )
    return resolved


def _assert_mutation_target_not_old_project(path: Path, field: str) -> None:
    """兼容旧私有名称；实际统一保护旧项目、生产根和代码仓库。"""

    _assert_safe_mutation_target(path, field)


def _assert_safe_workspace_mutation(
    workspace_root: os.PathLike[str] | str,
) -> Path:
    root = _assert_safe_mutation_target(
        Path(workspace_root), "acceptance workspace"
    )
    return _assert_directory(root, "acceptance_workspace")


def _file_ref(path: Path, *, maximum_bytes: int = 64 * 1024 * 1024) -> Mapping[str, Any]:
    raw = _read_stable_regular(path, maximum_bytes=maximum_bytes)
    return {
        "path": str(path.absolute()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }


def _fingerprinted(schema_version: str, semantic: Mapping[str, Any]) -> dict[str, Any]:
    payload = {"schema_version": schema_version, **dict(semantic)}
    return {
        **payload,
        "fingerprint_sha256": hashlib.sha256(
            canonical_json(
                {"schema": schema_version.replace("-", "_").replace("/", "_"), "value": payload}
            ).encode("utf-8")
        ).hexdigest(),
    }


def _verify_fingerprint(
    value: Any, schema_version: str, field: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IranResearchAcceptanceError(f"{field} 必须是对象")
    semantic = dict(value)
    supplied = semantic.pop("fingerprint_sha256", None)
    if semantic.get("schema_version") != schema_version:
        raise IranResearchAcceptanceError(f"{field} schema_version 非法")
    expected = _fingerprinted(
        schema_version,
        {key: item for key, item in semantic.items() if key != "schema_version"},
    )["fingerprint_sha256"]
    if supplied != expected:
        raise IranResearchAcceptanceError(f"{field} fingerprint 不闭合")
    return dict(value)


def _load_one_record_gzip(path: Path, *, maximum_bytes: int = 2_000_000_000) -> Mapping[str, Any]:
    """读取本模块发布的单行 JSONL gzip，并拒绝符号链接和多行。"""

    import gzip
    import io

    try:
        initial = path.lstat()
    except OSError as error:
        raise IranResearchAcceptanceError(f"segment 不可读：{path}") from error
    if (
        stat.S_ISLNK(initial.st_mode)
        or not stat.S_ISREG(initial.st_mode)
        or initial.st_size >= maximum_bytes
    ):
        raise IranResearchAcceptanceError("segment 必须是小于 2GB 的非符号链接普通文件")
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            lines = stream.readlines(2)
    except (OSError, EOFError, UnicodeDecodeError) as error:
        raise IranResearchAcceptanceError("segment gzip 损坏") from error
    if len(lines) != 1 or not lines[0].endswith("\n"):
        raise IranResearchAcceptanceError("segment 必须恰有一条规范 JSONL 记录")
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError as error:
        raise IranResearchAcceptanceError("segment JSON 非法") from error
    if not isinstance(payload, Mapping) or canonical_json(payload) + "\n" != lines[0]:
        raise IranResearchAcceptanceError("segment 不是规范 JSON")
    # gzip header/trailer 也属于 create-only 证据字节。即使篡改不改变解压文本，
    # 仍须被拒绝；因此按发布器相同参数重建确定性字节并逐字节比较。
    buffer = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", compresslevel=9, fileobj=buffer, mtime=0
    ) as compressed:
        compressed.write(lines[0].encode("utf-8"))
    if path.read_bytes() != buffer.getvalue():
        raise IranResearchAcceptanceError("segment gzip 字节不是确定性发布结果")
    after = path.lstat()
    identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(initial, name) != getattr(after, name) for name in identity):
        raise IranResearchAcceptanceError("segment 在读取期间发生变化")
    return dict(payload)


def _content_ref_from_manifest(
    package_root: Path,
    manifest: Mapping[str, Any],
    relative: str,
    *,
    maximum_bytes: int = 256 * 1024 * 1024,
) -> tuple[Mapping[str, Any], bytes]:
    relative = _safe_relative(relative, "package content path")
    matches = [
        row
        for row in manifest.get("contents", ())
        if isinstance(row, Mapping) and row.get("path") == relative
    ]
    if len(matches) != 1:
        raise IranResearchAcceptanceError(f"package manifest 未唯一登记：{relative}")
    row = matches[0]
    raw = _read_stable_regular(
        package_root.joinpath(*PurePosixPath(relative).parts),
        maximum_bytes=maximum_bytes,
    )
    if (
        len(raw) != row.get("size_bytes")
        or hashlib.sha256(raw).hexdigest() != row.get("sha256")
    ):
        raise IranResearchAcceptanceError(f"package content SHA/size 漂移：{relative}")
    return dict(row), raw


def _json_content(
    package_root: Path, manifest: Mapping[str, Any], relative: str
) -> Mapping[str, Any]:
    _ref, raw = _content_ref_from_manifest(package_root, manifest, relative)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IranResearchAcceptanceError(f"package JSON 非法：{relative}") from error
    if not isinstance(value, Mapping):
        raise IranResearchAcceptanceError(f"package JSON 顶层必须是对象：{relative}")
    return dict(value)


def _business_sequence_rows(
    package_root: Path,
    manifest_index: Mapping[str, Mapping[str, Any]],
    relative: str,
) -> Tuple[Mapping[str, Any], ...]:
    ref = manifest_index.get(relative)
    if not isinstance(ref, Mapping):
        raise IranResearchAcceptanceError(f"业务包缺少序列制品：{relative}")
    try:
        rows = tuple(_iter_shard_rows(package_root, ref))
    except (OSError, EOFError, UnicodeDecodeError, ValueError) as error:
        raise IranResearchAcceptanceError(
            f"业务包序列制品不可离线核验：{relative}"
        ) from error
    if len(rows) != ref.get("record_count"):
        raise IranResearchAcceptanceError(
            f"业务包序列制品 record_count 漂移：{relative}"
        )
    return rows


def _verify_business_package_gate(
    *,
    package_root: Path,
    manifest: Mapping[str, Any],
    finalization: Mapping[str, Any],
    quality: Mapping[str, Any],
    bindings: Mapping[str, Any],
    business_core: str,
    segment_core: str,
) -> Mapping[str, Any]:
    """核验研究业务包人口、质量门与双 core；不读取 journal/observation。

    segmented-finalization verifier 负责 TERMINAL/DEEP/segment/resource 的物理
    闭合；本函数只消费已由 manifest 内容寻址的最终业务制品。它不会调用旧
    ``verify_finalized_package``，因为旧 verifier 会回到 journal ancestry 并
    重读 record observations。
    """

    index = _manifest_index(manifest)
    missing = sorted(_REQUIRED_BUSINESS_PATHS - set(index))
    if missing:
        raise IranResearchAcceptanceError(
            f"v2 业务包缺少完整 frozen/quality/episode-AS 制品：{missing}"
        )
    for relative in _BUSINESS_NONEMPTY_SEQUENCE_PATHS:
        ref = index[relative]
        count = ref.get("record_count")
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise IranResearchAcceptanceError(
                f"伊朗验收业务序列必须非空：{relative}"
            )
    for relative in _REQUIRED_BUSINESS_JSON_PATHS:
        _json_content(package_root, manifest, relative)

    if (
        finalization.get("business_semantic_core_sha256") != business_core
        or finalization.get("finalization_segment_core_sha256") != segment_core
        or quality.get("business_semantic_core_sha256") != business_core
        or quality.get("finalization_segment_core_sha256") != segment_core
        or quality.get("schema_version")
        != "rrc25-full-window-quality-and-accounting/v1"
        or quality.get("acceptance_state") != "not_accepted"
    ):
        raise IranResearchAcceptanceError(
            "业务 finalization/quality 未全链绑定双 core 或错误标记 accepted"
        )
    external = quality.get("external_reproduction")
    if (
        not isinstance(external, Mapping)
        or external.get("state") != "reproduction_pending_not_accepted"
        or external.get("semantic_core_sha256") != business_core
    ):
        raise IranResearchAcceptanceError(
            "quality.external_reproduction 未绑定业务 semantic core"
        )
    accounting = quality.get("raw_accounting")
    if (
        not isinstance(accounting, Mapping)
        or _nonnegative(
            accounting.get("cumulative_reserved_raw_bytes_upper_bound"),
            "quality cumulative raw",
        )
        >= DEFAULT_MAX_RAW_READ_BYTES
        or _nonnegative(
            accounting.get("peak_temporary_bytes"), "quality peak temporary"
        )
        >= DEFAULT_MAX_TEMPORARY_BYTES
        or _nonnegative(
            accounting.get("database_write_operations"), "quality DB writes"
        )
        != 0
        or _nonnegative(
            accounting.get("unclosed_attempt_count"), "quality unclosed attempts"
        )
        != 0
    ):
        raise IranResearchAcceptanceError("业务 quality 未闭合 50GB/5GB/DB=0/raw ledger 门")
    maximum_worker_seconds = accounting.get("max_worker_seconds")
    if (
        isinstance(maximum_worker_seconds, bool)
        or not isinstance(maximum_worker_seconds, (int, float))
        or not 0 <= float(maximum_worker_seconds) < DEFAULT_KILL_SECONDS
    ):
        raise IranResearchAcceptanceError("业务 quality 单 worker 未闭合 590 秒排他门")

    research_quality = quality.get("research_quality")
    gates = (
        research_quality.get("gates")
        if isinstance(research_quality, Mapping)
        else None
    )
    if not isinstance(gates, list):
        raise IranResearchAcceptanceError("业务 quality 缺少研究质量门明细")
    reproduction_gates = [
        gate
        for gate in gates
        if isinstance(gate, Mapping) and gate.get("gate_id") == "reproducibility"
    ]
    blockers = [
        str(gate.get("gate_id"))
        for gate in gates
        if isinstance(gate, Mapping)
        and gate.get("gate_id") != "reproducibility"
        and gate.get("blocking") is True
        and gate.get("status") == "fail"
    ]
    if (
        len(reproduction_gates) != 1
        or reproduction_gates[0].get("blocking") is not True
        or reproduction_gates[0].get("status") != "fail"
        or blockers
    ):
        raise IranResearchAcceptanceError(
            f"业务 quality 除待外部复现外仍有阻断门：{blockers}"
        )

    schema_by_path = {
        "data/compatible-episodes.jsonl.gz": "country-outage-episode/v1",
        "data/compatible-waves.jsonl.gz": "country-outage-wave/v1",
        "data/compatible-episode-as.jsonl.gz": "country-outage-episode-as/v1",
        "data/compatible-prefix-impact.jsonl.gz": (
            "rrc25-full-window-episode-prefix-impact/v1"
        ),
        "data/incident-episode-mappings.jsonl.gz": "incident-episode-mapping/v1",
    }
    verified_counts = {}
    for relative, schema in schema_by_path.items():
        rows = _business_sequence_rows(package_root, index, relative)
        if not rows or any(row.get("schema_version") != schema for row in rows):
            raise IranResearchAcceptanceError(
                f"伊朗业务序列 schema/人口非法：{relative}"
            )
        if relative == "data/compatible-episode-as.jsonl.gz" and any(
            row.get("country_code") != "IR"
            or row.get("cohort_view") != "compatible"
            or not isinstance(row.get("evidence_links"), list)
            for row in rows
        ):
            raise IranResearchAcceptanceError(
                "compatible episode-AS 不是伊朗 compatible 全人口"
            )
        verified_counts[relative] = len(rows)

    _report_ref, report_raw = _content_ref_from_manifest(
        package_root,
        manifest,
        _REQUIRED_BUSINESS_REPORT_PATH,
        maximum_bytes=64 * 1024 * 1024,
    )
    try:
        report = report_raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise IranResearchAcceptanceError("中文复算报告不是 UTF-8") from error
    if not report.strip() or "伊朗" not in report or "复算" not in report:
        raise IranResearchAcceptanceError("中文复算报告内容不闭合")
    if _json_content(package_root, manifest, "frozen/bindings.json") != bindings:
        raise IranResearchAcceptanceError("业务 frozen bindings 与 v2 包绑定漂移")

    business_refs = {
        relative: {
            key: index[relative].get(key)
            for key in ("kind", "sha256", "size_bytes", "record_count")
        }
        for relative in sorted(_REQUIRED_BUSINESS_PATHS)
    }
    return {
        "business_artifact_refs": business_refs,
        "business_artifact_set_sha256": _canonical_hash(
            {
                "schema": "rrc25_iran_v2_business_artifact_set_v1",
                "business_semantic_core_sha256": business_core,
                "refs": business_refs,
            }
        ),
        "verified_population_counts": verified_counts,
    }


def _update_acceptance_light(
    receipt_path: os.PathLike[str] | str,
) -> Mapping[str, Any]:
    """严格核验 v2 UPDATE 最终化门及完整研究包绑定。

    v1 ``pure_derivation_from_same_frozen_journal`` receipt 不再具备总验收
    准入资格。这里先调用 segmented-finalization 的规范 verifier，随后逐包
    强制绑定 ``TERMINAL``、``DEEP-VERIFICATION``、``segments/index.json``、
    resource receipt 与自包含业务制品。后续对账只读取 sealed segment
    payload/receipt，绝不回到 ``GENESIS.journal_root``，也不读取
    ``record_observations``。
    """

    path = Path(receipt_path).expanduser().absolute()
    receipt_raw = _read_stable_regular(path, maximum_bytes=8 * 1024 * 1024)
    try:
        receipt = json.loads(receipt_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IranResearchAcceptanceError("UPDATE acceptance receipt JSON 非法") from error
    if not isinstance(receipt, Mapping):
        raise IranResearchAcceptanceError("UPDATE acceptance receipt 顶层必须是对象")
    receipt = dict(receipt)
    semantic = dict(receipt)
    supplied = semantic.pop("receipt_sha256", None)
    if (
        receipt.get("schema_version") != UPDATE_ACCEPTANCE_SCHEMA_VERSION
        or receipt.get("acceptance_state") != "accepted"
        or receipt.get("reproduction_scope") != UPDATE_REPRODUCTION_SCOPE
        or receipt.get("raw_replay_reproduction")
        != "not_performed_by_user_choice"
        or supplied
        != _canonical_hash(
            {
                "schema": "rrc25_full_window_reproduction_acceptance_v2",
                "receipt": semantic,
            }
        )
    ):
        raise IranResearchAcceptanceError("UPDATE acceptance receipt 指纹/语义非法")
    business_core = _sha(
        receipt.get("business_semantic_core_sha256"),
        "UPDATE business semantic core",
    )
    segment_core = _sha(
        receipt.get("finalization_segment_core_sha256"),
        "UPDATE finalization segment core",
    )
    if receipt.get("semantic_core_sha256") != segment_core:
        raise IranResearchAcceptanceError(
            "UPDATE legacy semantic_core 必须仅兼容映射到 finalization segment core"
        )
    checks = receipt.get("checks")
    required_checks = {
        "two_distinct_empty_targets_used": True,
        "same_verified_segment_index": True,
        "semantic_core_equal": True,
        "business_semantic_core_equal": True,
        "finalization_segment_core_equal": True,
        "terminal_and_deep_receipts_verified": True,
        "record_observation_reads_during_both_assemblies": 0,
        "database_write_operations": 0,
    }
    if not isinstance(checks, Mapping) or any(
        checks.get(key) != value for key, value in required_checks.items()
    ):
        raise IranResearchAcceptanceError("UPDATE acceptance checks 未闭合")
    try:
        verified_receipt = (
            _finalization_workspace.verify_workspace_reproduction_acceptance_receipt(
                path
            )
        )
    except (
        OSError,
        ValueError,
        _finalization_workspace.FullWindowFinalizeWorkspaceError,
    ) as error:
        raise IranResearchAcceptanceError(
            "UPDATE v2 segmented-finalization verifier/双 core 未闭合"
        ) from error
    if canonical_json(verified_receipt) != canonical_json(receipt):
        raise IranResearchAcceptanceError("UPDATE v2 verifier 返回值与 receipt 漂移")
    packages = receipt.get("packages")
    if not isinstance(packages, list) or [row.get("role") for row in packages] != [
        "reference",
        "reproduction",
    ]:
        raise IranResearchAcceptanceError("UPDATE acceptance package 对非法")

    package_results = []
    roots = set()
    for row in packages:
        if not isinstance(row, Mapping):
            raise IranResearchAcceptanceError("UPDATE acceptance package 行非法")
        root = _assert_directory(Path(str(row.get("package_root"))), "package_root")
        roots.add(str(root.resolve()))
        resource_path = Path(str(row.get("resource_receipt_path"))).absolute()
        try:
            core_verified = (
                _finalization_workspace.verify_workspace_assembled_package(
                    root,
                    resource_receipt_path=resource_path,
                    require_resource_receipt=True,
                )
            )
        except (
            OSError,
            ValueError,
            _finalization_workspace.FullWindowFinalizeWorkspaceError,
        ) as error:
            raise IranResearchAcceptanceError(
                "UPDATE package v2 TERMINAL/DEEP/index/resource 核心 verifier 未闭合"
            ) from error
        if (
            core_verified.get("verified") is not True
            or core_verified.get("resource_receipt_verified") is not True
            or core_verified.get("semantic_core_sha256") != segment_core
            or core_verified.get("record_observation_reread_count") != 0
            or core_verified.get("full_segment_chain_reread_count") != 0
        ):
            raise IranResearchAcceptanceError(
                "UPDATE package 核心 verifier 未绑定 finalization segment core"
            )
        manifest_raw = _read_stable_regular(
            root / "package-manifest.json", maximum_bytes=16 * 1024 * 1024
        )
        if hashlib.sha256(manifest_raw).hexdigest() != row.get(
            "package_manifest_sha256"
        ):
            raise IranResearchAcceptanceError("package manifest SHA 与 acceptance 不一致")
        try:
            manifest = json.loads(manifest_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise IranResearchAcceptanceError("package manifest JSON 非法") from error
        try:
            normalized_manifest = _manifest_semantic(manifest)
        except (TypeError, ValueError) as error:
            raise IranResearchAcceptanceError("package manifest 语义指纹非法") from error
        if (
            normalized_manifest.get("semantic_fingerprint_sha256")
            != row.get("package_semantic_fingerprint_sha256")
            or normalized_manifest.get("release_id") != row.get("release_id")
            or canonical_json(core_verified.get("manifest"))
            != canonical_json(normalized_manifest)
        ):
            raise IranResearchAcceptanceError("package manifest identity 与 acceptance 不一致")
        workspace_genesis = _json_content(root, normalized_manifest, "GENESIS")
        terminal = _json_content(root, normalized_manifest, "TERMINAL")
        deep = _json_content(root, normalized_manifest, "DEEP-VERIFICATION")
        segment_index = _json_content(
            root, normalized_manifest, "segments/index.json"
        )
        finalization = _json_content(
            root, normalized_manifest, "metadata/finalization.json"
        )
        bindings = _json_content(root, normalized_manifest, "frozen/bindings.json")
        quality = _json_content(
            root, normalized_manifest, "quality-and-accounting.json"
        )
        try:
            workspace_genesis = _finalization_workspace._verify_fingerprinted(
                workspace_genesis,
                _finalization_workspace.WORKSPACE_GENESIS_SCHEMA,
                "packaged GENESIS",
            )
            terminal = _finalization_workspace._verify_fingerprinted(
                terminal,
                _finalization_workspace.WORKSPACE_TERMINAL_SCHEMA,
                "packaged TERMINAL",
            )
            deep = _finalization_workspace._verify_fingerprinted(
                deep,
                _finalization_workspace.WORKSPACE_DEEP_VERIFICATION_SCHEMA,
                "packaged DEEP-VERIFICATION",
            )
            segment_index = _finalization_workspace._verify_fingerprinted(
                segment_index,
                _finalization_workspace.WORKSPACE_ASSEMBLY_INDEX_SCHEMA,
                "packaged segment index",
            )
            finalization = _finalization_workspace._verify_fingerprinted(
                finalization,
                _finalization_workspace.WORKSPACE_ASSEMBLY_METADATA_SCHEMA,
                "packaged finalization",
            )
        except _finalization_workspace.FullWindowFinalizeWorkspaceError as error:
            raise IranResearchAcceptanceError(
                "UPDATE package TERMINAL/DEEP/index 指纹非法"
            ) from error
        package_terminal_ref = _file_ref(root / "TERMINAL")
        package_deep_ref = _file_ref(root / "DEEP-VERIFICATION")
        package_index_ref = _file_ref(root / "segments/index.json")
        terminal_relative_ref = {
            "path": "TERMINAL",
            "sha256": package_terminal_ref["sha256"],
            "size_bytes": package_terminal_ref["size_bytes"],
        }
        deep_relative_ref = {
            "path": "DEEP-VERIFICATION",
            "sha256": package_deep_ref["sha256"],
            "size_bytes": package_deep_ref["size_bytes"],
        }
        index_relative_ref = {
            "path": "segments/index.json",
            "sha256": package_index_ref["sha256"],
            "size_bytes": package_index_ref["size_bytes"],
        }
        if (
            finalization.get("business_semantic_core_sha256") != business_core
            or finalization.get("finalization_segment_core_sha256")
            != segment_core
            or row.get("business_semantic_core_sha256") != business_core
            or row.get("finalization_segment_core_sha256") != segment_core
            or row.get("segment_index_ref") != index_relative_ref
            or bindings != receipt.get("input_bindings")
            or finalization.get("acceptance_state") != "not_accepted"
            or finalization.get("reproduction_scope") != UPDATE_REPRODUCTION_SCOPE
            or finalization.get("record_observation_reads_during_assembly") != 0
            or finalization.get("database_write_operations") != 0
            or finalization.get("terminal_ref") != terminal_relative_ref
            or finalization.get("deep_verification_ref") != deep_relative_ref
            or finalization.get("segment_index_ref") != index_relative_ref
            or row.get("terminal_ref") != terminal_relative_ref
            or row.get("deep_verification_ref") != deep_relative_ref
            or segment_index.get("terminal_ref") != terminal_relative_ref
            or segment_index.get("deep_verification_ref") != deep_relative_ref
            or segment_index.get("semantic_core_sha256") != segment_core
            or segment_index.get("finalization_segment_core_sha256")
            != segment_core
            or segment_index.get("bindings") != bindings
            or segment_index.get("record_observation_reads_during_assembly") != 0
            or terminal.get("bindings") != bindings
            or terminal.get("sealed") is not True
            or terminal.get("completed_slots") != terminal.get("total_slots")
            or terminal.get("segment_receipt_refs")
            != segment_index.get("segment_receipt_refs")
            or deep.get("terminal_ref") != terminal_relative_ref
            or deep.get("terminal_segment_receipt_ref")
            != terminal.get("terminal_segment_receipt_ref")
            or deep.get("verified_segment_count") != terminal.get("total_slots")
            or deep.get("bindings") != bindings
            or deep.get("database_write_operations") != 0
            or workspace_genesis.get("bindings") != bindings
            or workspace_genesis.get("total_slots") != terminal.get("total_slots")
        ):
            raise IranResearchAcceptanceError(
                "package TERMINAL/DEEP/index/finalization/bindings 与 v2 receipt 不一致"
            )
        receipt_refs = segment_index.get("segment_receipt_refs")
        payload_refs = segment_index.get("segment_payload_refs")
        if (
            not isinstance(receipt_refs, list)
            or not isinstance(payload_refs, list)
            or len(receipt_refs) != terminal.get("total_slots")
            or len(payload_refs) != terminal.get("total_slots")
            or not receipt_refs
        ):
            raise IranResearchAcceptanceError("v2 segment index 人口不闭合")
        resource_raw = _read_stable_regular(resource_path, maximum_bytes=4 * 1024 * 1024)
        if hashlib.sha256(resource_raw).hexdigest() != row.get(
            "resource_receipt_file_sha256"
        ):
            raise IranResearchAcceptanceError("resource receipt 文件 SHA 漂移")
        try:
            resource = json.loads(resource_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise IranResearchAcceptanceError("resource receipt JSON 非法") from error
        try:
            resource = _finalization_workspace._verify_fingerprinted(
                resource,
                _finalization_workspace.WORKSPACE_PACKAGE_RESOURCE_SCHEMA,
                "package resource receipt",
            )
        except _finalization_workspace.FullWindowFinalizeWorkspaceError as error:
            raise IranResearchAcceptanceError(
                "v2 resource receipt 指纹非法"
            ) from error
        resource_accounting = resource.get("resource_accounting")
        if (
            resource.get("package_root") != str(root.resolve())
            or resource.get("package_manifest_sha256")
            != hashlib.sha256(manifest_raw).hexdigest()
            or resource.get("business_semantic_core_sha256") != business_core
            or resource.get("finalization_segment_core_sha256") != segment_core
            or resource.get("terminal_ref") != terminal_relative_ref
            or resource.get("deep_verification_ref") != deep_relative_ref
            or resource.get("segment_index_ref") != index_relative_ref
            or resource.get("bindings") != bindings
            or not isinstance(resource_accounting, Mapping)
            or resource_accounting.get("record_observation_reads_during_assembly")
            != 0
            or resource_accounting.get("database_write_operations") != 0
            or not isinstance(
                resource_accounting.get("maximum_temporary_bytes"), int
            )
            or resource_accounting["maximum_temporary_bytes"]
            >= DEFAULT_MAX_TEMPORARY_BYTES
        ):
            raise IranResearchAcceptanceError("resource receipt 指纹或资源门非法")
        frozen_head = {
            "run_id": workspace_genesis.get("run_id"),
            "bindings": dict(bindings),
            "terminal_receipt_ref": dict(
                terminal.get("journal_terminal_receipt_ref", {})
            ),
            "shard_chain_sha256": terminal.get(
                "journal_terminal_shard_chain_sha256"
            ),
            "completed_artifact_count": terminal.get("completed_slots"),
            "total_artifacts": terminal.get("total_slots"),
        }
        if (
            not isinstance(frozen_head["run_id"], str)
            or not isinstance(frozen_head["terminal_receipt_ref"], Mapping)
            or set(frozen_head["terminal_receipt_ref"]) != {"path", "sha256"}
            or _SHA256_RE.fullmatch(
                str(frozen_head["shard_chain_sha256"])
            )
            is None
        ):
            raise IranResearchAcceptanceError("v2 terminal journal identity 非法")
        business_gate = _verify_business_package_gate(
            package_root=root,
            manifest=normalized_manifest,
            finalization=finalization,
            quality=quality,
            bindings=bindings,
            business_core=business_core,
            segment_core=segment_core,
        )
        package_results.append(
            {
                "role": row["role"],
                "package_root": str(root.resolve()),
                "package_manifest_sha256": row["package_manifest_sha256"],
                "package_semantic_fingerprint_sha256": row[
                    "package_semantic_fingerprint_sha256"
                ],
                "resource_receipt_path": str(resource_path.resolve()),
                "resource_receipt_file_sha256": row[
                    "resource_receipt_file_sha256"
                ],
                "manifest": normalized_manifest,
                "workspace_genesis": workspace_genesis,
                "terminal": terminal,
                "deep_verification": deep,
                "segment_index": segment_index,
                "segment_index_ref": index_relative_ref,
                "finalization": finalization,
                "bindings": bindings,
                "quality": quality,
                "business_gate": business_gate,
                "frozen_journal_head": frozen_head,
                "business_semantic_core_sha256": business_core,
                "finalization_segment_core_sha256": segment_core,
            }
        )
    if len(roots) != 2:
        raise IranResearchAcceptanceError("UPDATE accepted receipt 未绑定两个独立目录")
    if (
        package_results[0]["business_gate"]["business_artifact_set_sha256"]
        != package_results[1]["business_gate"]["business_artifact_set_sha256"]
        or package_results[0]["business_gate"]["business_artifact_refs"]
        != package_results[1]["business_gate"]["business_artifact_refs"]
    ):
        raise IranResearchAcceptanceError("双目录完整业务制品人口或字节身份不一致")
    return {
        "receipt": receipt,
        "receipt_ref": {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(receipt_raw).hexdigest(),
            "size_bytes": len(receipt_raw),
        },
        "packages": package_results,
        "verification_scope": (
            "strict_v2_workspace_verifier_plus_bound_terminal_deep_segment_index_"
            "resource_and_self_contained_business_artifacts_without_record_observation_reread"
        ),
    }


def _manifest_index(manifest: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    contents = manifest.get("contents")
    if not isinstance(contents, list):
        raise IranResearchAcceptanceError("package manifest contents 非法")
    result = {}
    for row in contents:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise IranResearchAcceptanceError("package manifest content ref 非法")
        path = _safe_relative(row["path"], "manifest.content.path")
        if path in result:
            raise IranResearchAcceptanceError("package manifest content path 重复")
        result[path] = dict(row)
    return result


def _package_ref_in_manifest(
    manifest_index: Mapping[str, Mapping[str, Any]],
    ref: Mapping[str, Any],
    *,
    expected_kind: Optional[str] = None,
) -> Mapping[str, Any]:
    """核验 package-relative ref；允许声明 segment 内嵌集合但不放宽字节身份。"""

    if not isinstance(ref, Mapping):
        raise IranResearchAcceptanceError("package ref 必须是对象")
    allowed = {
        "path",
        "sha256",
        "size_bytes",
        "kind",
        "record_count",
        "embedded_collection",
    }
    if not {"path", "sha256", "size_bytes"} <= set(ref) or set(ref) - allowed:
        raise IranResearchAcceptanceError("package ref 字段不闭合")
    relative = _safe_relative(ref.get("path"), "package ref.path")
    packaged = manifest_index.get(relative)
    if packaged is None:
        raise IranResearchAcceptanceError("package ref 未登记在 package manifest")
    if (
        packaged.get("sha256") != ref.get("sha256")
        or packaged.get("size_bytes") != ref.get("size_bytes")
        or (
            expected_kind is not None
            and packaged.get("kind") != expected_kind
        )
        or (
            ref.get("kind") is not None
            and packaged.get("kind") != ref.get("kind")
        )
        or (
            ref.get("record_count") is not None
            and packaged.get("record_count") != ref.get("record_count")
        )
    ):
        raise IranResearchAcceptanceError("package ref 与 manifest 字节/类型身份冲突")
    embedded = ref.get("embedded_collection")
    if embedded is not None and embedded not in {
        "route_event_rows",
        "raw_record_ref_rows",
        "country_slots",
        "next_compact_state",
    }:
        raise IranResearchAcceptanceError("segment embedded_collection 非法")
    return dict(packaged)


def _sealed_segment_source_ref(
    ref: Mapping[str, Any], *, embedded_collection: str
) -> Mapping[str, Any]:
    return {
        "kind": "finalization-segment-payload",
        "path": ref["path"],
        "sha256": ref["sha256"],
        "size_bytes": ref["size_bytes"],
        "record_count": 1,
        "embedded_collection": embedded_collection,
    }


def _sealed_segment_chain(
    package: Mapping[str, Any],
) -> Tuple[Mapping[str, Any], ...]:
    """读取自包含 v2 segment receipt/payload；不访问 journal_root/observations。"""

    root = Path(str(package["package_root"])).absolute()
    manifest_index = _manifest_index(package["manifest"])
    terminal = package.get("terminal")
    index = package.get("segment_index")
    if not isinstance(terminal, Mapping) or not isinstance(index, Mapping):
        raise IranResearchAcceptanceError("v2 reference 缺少 TERMINAL/segment index")
    receipt_refs = index.get("segment_receipt_refs")
    payload_refs = index.get("segment_payload_refs")
    if (
        not isinstance(receipt_refs, list)
        or not isinstance(payload_refs, list)
        or receipt_refs != terminal.get("segment_receipt_refs")
        or len(receipt_refs) != terminal.get("total_slots")
        or len(payload_refs) != terminal.get("total_slots")
    ):
        raise IranResearchAcceptanceError("v2 segment index 与 TERMINAL 人口不闭合")
    rows = []
    previous_ref = None
    for sequence, (receipt_ref, payload_ref) in enumerate(
        zip(receipt_refs, payload_refs), start=1
    ):
        _package_ref_in_manifest(
            manifest_index,
            receipt_ref,
            expected_kind="finalization-segment-receipt",
        )
        _package_ref_in_manifest(
            manifest_index,
            payload_ref,
            expected_kind="finalization-segment-payload",
        )
        try:
            receipt = _finalization_workspace._load_segment_receipt(
                root, receipt_ref
            )
            payload = _finalization_workspace._load_segment_payload(
                root, payload_ref
            )
        except _finalization_workspace.FullWindowFinalizeWorkspaceError as error:
            raise IranResearchAcceptanceError(
                f"v2 sealed segment {sequence} 指纹/字节非法"
            ) from error
        forbidden_payload_fields = {
            "record_observations",
            "record_observation_rows",
            "observations",
        }
        route_rows = payload.get("route_event_rows")
        raw_rows = payload.get("raw_record_ref_rows")
        country_slots = payload.get("country_slots")
        compact = receipt.get("next_compact_state")
        if (
            receipt.get("sequence") != sequence
            or payload.get("sequence") != sequence
            or receipt.get("previous_segment_receipt_ref") != previous_ref
            or receipt.get("segment_payload_ref") != payload_ref
            or receipt.get("artifact") != payload.get("artifact")
            or receipt.get("journal_receipt_ref")
            != payload.get("journal_receipt_ref")
            or receipt.get("state_ref_sha256_verified") is not True
            or payload.get("state_ref_sha256_verified") is not True
            or forbidden_payload_fields & set(payload)
            or not isinstance(payload.get("record_observation_summary"), Mapping)
            or not isinstance(route_rows, list)
            or not isinstance(raw_rows, list)
            or not isinstance(country_slots, list)
            or not isinstance(compact, Mapping)
        ):
            raise IranResearchAcceptanceError(
                f"v2 sealed segment {sequence} receipt/payload/state 不闭合"
            )
        compact_state_from_payload(compact)
        by_view = {
            row.get("mapping_view"): row
            for row in country_slots
            if isinstance(row, Mapping)
        }
        if len(country_slots) != 2 or set(by_view) != {"compatible", "revised"}:
            raise IranResearchAcceptanceError(
                f"v2 sealed segment {sequence} country_slots 不闭合"
            )
        rows.append(
            {
                "sequence": sequence,
                "receipt_ref": dict(receipt_ref),
                "payload_ref": dict(payload_ref),
                "receipt": receipt,
                "payload": payload,
            }
        )
        previous_ref = dict(receipt_ref)
    if (
        previous_ref != terminal.get("terminal_segment_receipt_ref")
        or len(rows) != terminal.get("completed_slots")
    ):
        raise IranResearchAcceptanceError("v2 sealed segment terminal 链未闭合")
    return tuple(rows)


def _episode_as_rows(
    package_root: Path, manifest: Mapping[str, Any]
) -> Tuple[Mapping[str, Any], ...]:
    index = _manifest_index(manifest)
    packaged = index.get("data/compatible-episode-as.jsonl.gz")
    if packaged is None:
        raise IranResearchAcceptanceError("reference package 缺少 compatible episode-AS")
    ref = {
        field: packaged[field]
        for field in ("kind", "path", "sha256", "size_bytes", "record_count")
    }
    rows = tuple(_iter_shard_rows(package_root, ref))
    for row in rows:
        if (
            row.get("schema_version") != "country-outage-episode-as/v1"
            or row.get("country_code") != "IR"
            or row.get("cohort_view") != "compatible"
            or not isinstance(row.get("evidence_links"), list)
        ):
            raise IranResearchAcceptanceError("compatible episode-AS 行身份非法")
    return rows


def _collect_requested_route_ids(
    rows: Sequence[Mapping[str, Any]],
) -> Tuple[str, ...]:
    values = set()
    for row in rows:
        for link in row.get("evidence_links", ()):
            if not isinstance(link, Mapping):
                raise IranResearchAcceptanceError("episode-AS evidence link 必须是对象")
            required = {
                "route_event_id",
                "raw_record_ref_id",
                "artifact_id",
                "artifact_sha256",
                "record_ordinal",
                "element_ordinal",
            }
            if set(link) != required:
                raise IranResearchAcceptanceError("episode-AS evidence link 字段不闭合")
            route_id = link.get("route_event_id")
            if not isinstance(route_id, str) or not route_id:
                raise IranResearchAcceptanceError("episode-AS route_event_id 非法")
            _sha(link.get("artifact_sha256"), "episode-AS artifact_sha256")
            for field in ("record_ordinal", "element_ordinal"):
                _nonnegative(link.get(field), f"episode-AS {field}")
            values.add(route_id)
    return tuple(sorted(values))


def _selection_artifact_times(
    selection: Mapping[str, Any], profile: Mapping[str, Any]
) -> tuple[Tuple[Mapping[str, Any], ...], Tuple[Mapping[str, Any], ...], Mapping[str, Any]]:
    try:
        validate_complete_selection_against_profile(selection, profile)
    except (TypeError, ValueError) as error:
        raise IranResearchAcceptanceError("reference selection/Profile 未闭合") from error
    roles = selection.get("roles")
    if not isinstance(roles, Mapping):
        raise IranResearchAcceptanceError("selection.roles 缺失")
    updates_raw = roles.get("analysis_updates")
    ribs_raw = roles.get("analysis_ribs")
    baseline = roles.get("baseline_reference_rib")
    if (
        not isinstance(updates_raw, list)
        or not isinstance(ribs_raw, list)
        or not isinstance(baseline, Mapping)
    ):
        raise IranResearchAcceptanceError("selection UPDATE/RIB roles 非法")
    updates = tuple(sorted((dict(row) for row in updates_raw), key=lambda row: row["artifact_time_utc"]))
    ribs = tuple(sorted((dict(row) for row in ribs_raw), key=lambda row: row["artifact_time_utc"]))
    if len(ribs) != EXPECTED_BOUNDARY_COUNT:
        raise IranResearchAcceptanceError("窗口内 analysis RIB 必须恰有 21 张")
    boundaries = tuple(_utc(row.get("artifact_time_utc"), "analysis RIB time") for row in ribs)
    start = _utc(profile.get("window", {}).get("start_utc"), "profile.window.start")
    if boundaries[0] != start or any(
        _time(right) - _time(left) != timedelta(hours=8)
        for left, right in zip(boundaries, boundaries[1:])
    ):
        raise IranResearchAcceptanceError("21 张 analysis RIB 未从窗口起点按 8 小时连续")
    return updates, ribs, dict(baseline)


def _segment_plan(
    *,
    updates: Sequence[Mapping[str, Any]],
    ribs: Sequence[Mapping[str, Any]],
    receipts: Sequence[Tuple[Mapping[str, Any], Mapping[str, Any]]],
    window_end_exclusive_utc: str,
) -> Tuple[Mapping[str, Any], ...]:
    if len(receipts) != len(updates) + 1:
        raise IranResearchAcceptanceError("journal receipt 数与 UPDATE selection 不一致")
    for sequence, ((receipt_ref, receipt), selected) in enumerate(
        zip(receipts[1:], updates), start=1
    ):
        artifact = receipt.get("committed_artifact")
        if (
            receipt.get("sequence") != sequence
            or not isinstance(artifact, Mapping)
            or artifact.get("index") != sequence - 1
            or any(
                artifact.get(target) != selected.get(source)
                for target, source in (
                    ("artifact_id", "artifact_id"),
                    ("file_sha256", "file_sha256"),
                    ("size_bytes", "size_bytes"),
                    ("collector_id", "collector_id"),
                    ("slot_start_utc", "artifact_time_utc"),
                )
            )
        ):
            raise IranResearchAcceptanceError(
                f"journal receipt {sequence} 与 UPDATE selection 不一致"
            )
        del receipt_ref
    plans = []
    prior_end = 0
    for boundary_index, rib in enumerate(ribs):
        boundary = _utc(rib.get("artifact_time_utc"), "analysis boundary")
        end_sequence = sum(
            1
            for row in updates
            if _time(_utc(row.get("artifact_time_utc"), "UPDATE time"))
            + timedelta(minutes=5)
            <= _time(boundary)
        )
        if boundary_index == 0 and end_sequence != 0:
            raise IranResearchAcceptanceError("窗口起点 RIB 边界不得消费 UPDATE")
        plans.append(
            {
                "segment_index": boundary_index,
                "role": "analysis_rib_boundary",
                "start_receipt_sequence_inclusive": prior_end + 1,
                "end_receipt_sequence_inclusive": end_sequence,
                "boundary_at_utc": boundary,
                "analysis_rib_artifact_id": rib.get("artifact_id"),
            }
        )
        prior_end = end_sequence
    total = len(updates)
    end = _utc(window_end_exclusive_utc, "window_end_exclusive_utc")
    if prior_end >= total:
        raise IranResearchAcceptanceError("最后一个 RIB 边界必须严格早于窗口结束")
    final_artifact = receipts[-1][1].get("committed_artifact", {})
    if final_artifact.get("slot_end_exclusive_utc") != end:
        raise IranResearchAcceptanceError("terminal UPDATE receipt 未闭合到窗口半开终点")
    plans.append(
        {
            "segment_index": EXPECTED_SEGMENT_COUNT - 1,
            "role": "terminal_tail_without_rib_boundary",
            "start_receipt_sequence_inclusive": prior_end + 1,
            "end_receipt_sequence_inclusive": total,
            "boundary_at_utc": None,
            "analysis_rib_artifact_id": None,
        }
    )
    if len(plans) != EXPECTED_SEGMENT_COUNT:
        raise IranResearchAcceptanceError("有界 replay 必须恰好拆成 22 个 segment")
    for left, right in zip(plans, plans[1:]):
        if (
            left["end_receipt_sequence_inclusive"] + 1
            != right["start_receipt_sequence_inclusive"]
        ):
            raise IranResearchAcceptanceError("segment receipt 范围不连续")
    return tuple(plans)


def _workspace_genesis_path(root: Path) -> Path:
    return root / "GENESIS.json"


def _load_workspace_genesis(root: Path) -> Mapping[str, Any]:
    _assert_directory(root, "acceptance_workspace")
    value = _load_json(_workspace_genesis_path(root), maximum_bytes=64 * 1024 * 1024)
    return _verify_fingerprint(value, WORKSPACE_GENESIS_SCHEMA_VERSION, "workspace GENESIS")


def initialize_acceptance_workspace(
    workspace_root: os.PathLike[str] | str,
    *,
    update_acceptance_receipt_path: os.PathLike[str] | str,
    analysis_rib_anchor_root: os.PathLike[str] | str,
) -> Mapping[str, Any]:
    """create-only 初始化有界 replay workspace；不重放 UPDATE、不读 MRT。"""

    update = _update_acceptance_light(update_acceptance_receipt_path)
    reference = next(row for row in update["packages"] if row["role"] == "reference")
    package_root = Path(reference["package_root"])
    manifest = reference["manifest"]
    profile = _json_content(package_root, manifest, "frozen/profile.json")
    selection = _json_content(package_root, manifest, "frozen/input-selection.json")
    compatible_snapshot = _json_content(
        package_root, manifest, "frozen/compatible-mapping.json"
    )
    revised_snapshot = _json_content(
        package_root, manifest, "frozen/revised-mapping.json"
    )
    bindings = reference["bindings"]
    try:
        compatible_mapping = mapping_view_from_frozen_snapshot(compatible_snapshot)
        revised_mapping = mapping_view_from_revised_snapshot(
            revised_snapshot, compatible_snapshot
        )
    except (TypeError, ValueError) as error:
        raise IranResearchAcceptanceError("reference mapping snapshots 非法") from error
    if (
        profile_sha256(profile) != bindings.get("profile_sha256")
        or selection.get("semantic_fingerprint_sha256")
        != bindings.get("input_selection_sha256")
        or mapping_bundle_sha256(compatible_snapshot, revised_snapshot)
        != bindings.get("mapping_sha256")
    ):
        raise IranResearchAcceptanceError("reference profile/selection/mapping bindings 漂移")
    updates, ribs, baseline = _selection_artifact_times(selection, profile)

    frozen = reference.get("frozen_journal_head")
    if not isinstance(frozen, Mapping):
        raise IranResearchAcceptanceError("reference v2 TERMINAL 缺少 frozen journal identity")
    sealed_slots = _sealed_segment_chain(reference)
    receipt_views: list[Tuple[Mapping[str, Any], Mapping[str, Any]]] = [
        ({}, {"sequence": 0})
    ]
    for slot in sealed_slots:
        receipt_views.append(
            (
                dict(slot["receipt_ref"]),
                {
                    "sequence": slot["sequence"],
                    "committed_artifact": dict(slot["receipt"]["artifact"]),
                },
            )
        )
    plans = _segment_plan(
        updates=updates,
        ribs=ribs,
        receipts=receipt_views,
        window_end_exclusive_utc=profile["window"]["end_exclusive_utc"],
    )
    episode_rows = _episode_as_rows(package_root, manifest)
    requested_ids = _collect_requested_route_ids(episode_rows)
    raw_accounting = reference["quality"].get("raw_accounting")
    if not isinstance(raw_accounting, Mapping):
        raise IranResearchAcceptanceError("reference quality 缺少 raw accounting")
    update_cumulative = _nonnegative(
        raw_accounting.get("cumulative_reserved_raw_bytes_upper_bound"),
        "UPDATE cumulative raw",
    )
    update_peak = _nonnegative(
        raw_accounting.get("peak_temporary_bytes"), "UPDATE peak temporary"
    )
    update_db = _nonnegative(
        raw_accounting.get("database_write_operations"), "UPDATE database writes"
    )
    if (
        update_cumulative >= DEFAULT_MAX_RAW_READ_BYTES
        or update_peak >= DEFAULT_MAX_TEMPORARY_BYTES
        or update_db != 0
    ):
        raise IranResearchAcceptanceError("UPDATE 资源账越过 50GB/5GB/DB=0 门")

    anchor_root = _assert_directory(
        Path(analysis_rib_anchor_root), "analysis_rib_anchor_root"
    )
    target = Path(workspace_root).expanduser().absolute()
    _assert_safe_mutation_target(
        target,
        "acceptance workspace",
        source_roots=(
            package_root,
            anchor_root,
            Path(update_acceptance_receipt_path).expanduser().absolute(),
        ),
    )
    parent = _assert_directory(target.parent, "workspace parent")
    if target.exists() or target.is_symlink():
        raise FileExistsError("acceptance workspace 已存在，拒绝覆盖")
    genesis_semantic = {
        "acceptance_semantics": ACCEPTANCE_SEMANTICS,
        "database_access_policy": "no_database_connection_or_write",
        "raw_mrt_access_policy": "no_real_mrt_opened_by_acceptance_reconciliation",
        "update_curve_policy": "independent_rib_never_resets_or_rewrites_update_curve",
        "update_acceptance": {
            "receipt_ref": update["receipt_ref"],
            "schema_version": update["receipt"]["schema_version"],
            "reproduction_scope": update["receipt"]["reproduction_scope"],
            "business_semantic_core_sha256": update["receipt"][
                "business_semantic_core_sha256"
            ],
            "finalization_segment_core_sha256": update["receipt"][
                "finalization_segment_core_sha256"
            ],
            "verification_scope": update["verification_scope"],
            "reference_terminal_ref": dict(reference["terminal_ref"]),
            "reference_deep_verification_ref": dict(
                reference["deep_verification_ref"]
            ),
            "reference_segment_index_ref": dict(
                reference["segment_index_ref"]
            ),
        },
        "reference_package": {
            "root": str(package_root.resolve()),
            "manifest_sha256": reference["package_manifest_sha256"],
            "manifest_semantic_sha256": reference[
                "package_semantic_fingerprint_sha256"
            ],
            "release_id": manifest["release_id"],
            "frozen_journal_head": dict(frozen),
            "terminal_ref": dict(reference["terminal_ref"]),
            "deep_verification_ref": dict(reference["deep_verification_ref"]),
            "segment_index_ref": dict(reference["segment_index_ref"]),
            "segment_receipt_refs": [
                dict(row["receipt_ref"]) for row in sealed_slots
            ],
            "segment_payload_refs": [
                dict(row["payload_ref"]) for row in sealed_slots
            ],
            "business_semantic_core_sha256": reference[
                "business_semantic_core_sha256"
            ],
            "finalization_segment_core_sha256": reference[
                "finalization_segment_core_sha256"
            ],
        },
        "analysis_rib_anchor_root": str(anchor_root.resolve()),
        "bindings": dict(bindings),
        "window": dict(profile["window"]),
        "baseline_reference_rib": baseline,
        "analysis_rib_boundaries": [dict(row) for row in ribs],
        "segment_plan": [dict(row) for row in plans],
        "requested_evidence_route_event_ids": list(requested_ids),
        "compatible_episode_as": {
            "record_count": len(episode_rows),
            "semantic_sha256": hashlib.sha256(
                canonical_json(
                    {"semantics": SCAN_SEMANTICS, "rows": list(episode_rows)}
                ).encode("utf-8")
            ).hexdigest(),
            "source_path": "data/compatible-episode-as.jsonl.gz",
        },
        "update_resources": {
            "cumulative_reserved_raw_bytes": update_cumulative,
            "peak_temporary_bytes": update_peak,
            "database_writes": update_db,
        },
        "limits": {
            "cumulative_raw_bytes_exclusive": DEFAULT_MAX_RAW_READ_BYTES,
            "temporary_bytes_exclusive": DEFAULT_MAX_TEMPORARY_BYTES,
            "observation_seconds": DEFAULT_OBSERVATION_SECONDS,
            "term_seconds": DEFAULT_TERM_SECONDS,
            "kill_seconds": DEFAULT_KILL_SECONDS,
        },
    }
    genesis = _fingerprinted(WORKSPACE_GENESIS_SCHEMA_VERSION, genesis_semantic)
    staging = parent / f".{target.name}.iran-acceptance-staging-{os.getpid()}-{secrets.token_hex(8)}"
    os.mkdir(staging, 0o750)
    try:
        (staging / "segments").mkdir(mode=0o750)
        (staging / "supervisors").mkdir(mode=0o750)
        artifact = write_canonical_json(
            staging / "GENESIS.json", genesis, kind="iran-research-acceptance-genesis", mode=0o440
        )
        os.chmod(artifact.path, 0o440)
        for directory in (staging / "segments", staging / "supervisors", staging):
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        lock_path = parent / ".rrc25-iran-research-acceptance-workspace.lock"
        lock_fd = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            _rename_directory_no_replace(staging, target)
            parent_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
    finally:
        if staging.exists():
            _assert_safe_mutation_target(staging, "acceptance staging cleanup")
            shutil.rmtree(staging)
    return {
        "workspace_root": str(target.resolve()),
        "genesis_fingerprint_sha256": genesis["fingerprint_sha256"],
        "segment_count": EXPECTED_SEGMENT_COUNT,
        "analysis_rib_boundary_count": EXPECTED_BOUNDARY_COUNT,
        "requested_evidence_route_event_count": len(requested_ids),
        "next_action": "publish_analysis_rib_deep_verification_gate",
    }


def build_successful_supervision_evidence(
    *,
    command_kind: str,
    elapsed_seconds: float,
    observation_seconds: float = DEFAULT_OBSERVATION_SECONDS,
    term_seconds: float = DEFAULT_TERM_SECONDS,
    kill_seconds: float = DEFAULT_KILL_SECONDS,
) -> Mapping[str, Any]:
    """构造成功子进程的 supervisor 证据；生产验收只接受固定三段门。"""

    observed = _positive_seconds(observation_seconds, "observation_seconds")
    term = _positive_seconds(term_seconds, "term_seconds")
    kill = _positive_seconds(kill_seconds, "kill_seconds")
    elapsed = _positive_seconds(elapsed_seconds, "elapsed_seconds")
    if not isinstance(command_kind, str) or not command_kind:
        raise IranResearchAcceptanceError("command_kind 不能为空")
    if not observed < term < kill:
        raise IranResearchAcceptanceError("supervisor 必须满足 observe < TERM < KILL")
    if elapsed >= term:
        raise IranResearchAcceptanceError("成功子进程不得达到 TERM 边界")
    frozen = (
        observed == DEFAULT_OBSERVATION_SECONDS
        and term == DEFAULT_TERM_SECONDS
        and kill == DEFAULT_KILL_SECONDS
    )
    return {
        "semantics": SUPERVISOR_SEMANTICS,
        "command_kind": command_kind,
        "policy": {
            "observation_seconds": observed,
            "term_seconds": term,
            "kill_seconds": kill,
            "parent_exit_seconds_exclusive": DEFAULT_PARENT_EXIT_SECONDS,
            "is_frozen_acceptance_policy": frozen,
        },
        "actions": {
            "observation_boundary_crossed": elapsed >= observed,
            "term_sent": False,
            "kill_sent": False,
            "child_reaped_within_parent_deadline": True,
        },
        "child_exit_code": 0,
        "elapsed_seconds": round(elapsed, 6),
        "status": "child_completed_before_term_and_hard_timeout",
    }


def _verify_successful_supervision(
    value: Any, *, command_kind: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IranResearchAcceptanceError("supervision evidence 必须是对象")
    policy = value.get("policy")
    actions = value.get("actions")
    if (
        value.get("semantics") != SUPERVISOR_SEMANTICS
        or value.get("command_kind") != command_kind
        or policy
        != {
            "observation_seconds": DEFAULT_OBSERVATION_SECONDS,
            "term_seconds": DEFAULT_TERM_SECONDS,
            "kill_seconds": DEFAULT_KILL_SECONDS,
            "parent_exit_seconds_exclusive": DEFAULT_PARENT_EXIT_SECONDS,
            "is_frozen_acceptance_policy": True,
        }
        or not isinstance(actions, Mapping)
        or set(actions)
        != {
            "observation_boundary_crossed",
            "term_sent",
            "kill_sent",
            "child_reaped_within_parent_deadline",
        }
        or not isinstance(actions.get("observation_boundary_crossed"), bool)
        or actions.get("term_sent") is not False
        or actions.get("kill_sent") is not False
        or actions.get("child_reaped_within_parent_deadline") is not True
        or value.get("child_exit_code") != 0
        or value.get("status") != "child_completed_before_term_and_hard_timeout"
        or not isinstance(value.get("elapsed_seconds"), (int, float))
        or value["elapsed_seconds"] <= 0
        or value["elapsed_seconds"] >= DEFAULT_TERM_SECONDS
    ):
        raise IranResearchAcceptanceError("supervisor 420/540/590/596 成功证据不闭合")
    return dict(value)


def _workspace_source_context(
    workspace_root: os.PathLike[str] | str,
) -> Mapping[str, Any]:
    root = _assert_directory(Path(workspace_root), "acceptance_workspace")
    genesis = _load_workspace_genesis(root)
    update_ref = genesis.get("update_acceptance", {}).get("receipt_ref")
    if not isinstance(update_ref, Mapping):
        raise IranResearchAcceptanceError("workspace 缺少 UPDATE acceptance ref")
    update = _update_acceptance_light(update_ref.get("path"))
    if update["receipt_ref"] != update_ref:
        raise IranResearchAcceptanceError("UPDATE acceptance receipt 在 workspace 初始化后漂移")
    reference = next(row for row in update["packages"] if row["role"] == "reference")
    frozen_update = genesis.get("update_acceptance")
    if (
        not isinstance(frozen_update, Mapping)
        or frozen_update.get("schema_version") != UPDATE_ACCEPTANCE_SCHEMA_VERSION
        or frozen_update.get("reproduction_scope") != UPDATE_REPRODUCTION_SCOPE
        or frozen_update.get("business_semantic_core_sha256")
        != update["receipt"].get("business_semantic_core_sha256")
        or frozen_update.get("finalization_segment_core_sha256")
        != update["receipt"].get("finalization_segment_core_sha256")
        or frozen_update.get("verification_scope") != update["verification_scope"]
        or frozen_update.get("reference_terminal_ref")
        != reference.get("terminal_ref")
        or frozen_update.get("reference_deep_verification_ref")
        != reference.get("deep_verification_ref")
        or frozen_update.get("reference_segment_index_ref")
        != reference.get("segment_index_ref")
    ):
        raise IranResearchAcceptanceError(
            "workspace UPDATE v2 TERMINAL/DEEP/index gate 绑定漂移"
        )
    frozen_reference = genesis.get("reference_package")
    if (
        not isinstance(frozen_reference, Mapping)
        or frozen_reference.get("root") != reference["package_root"]
        or frozen_reference.get("manifest_sha256")
        != reference["package_manifest_sha256"]
        or frozen_reference.get("manifest_semantic_sha256")
        != reference["package_semantic_fingerprint_sha256"]
        or frozen_reference.get("release_id") != reference["manifest"]["release_id"]
        or genesis.get("bindings") != reference["bindings"]
        or frozen_reference.get("terminal_ref") != reference.get("terminal_ref")
        or frozen_reference.get("deep_verification_ref")
        != reference.get("deep_verification_ref")
        or frozen_reference.get("segment_index_ref")
        != reference.get("segment_index_ref")
        or frozen_reference.get("business_semantic_core_sha256")
        != reference.get("business_semantic_core_sha256")
        or frozen_reference.get("finalization_segment_core_sha256")
        != reference.get("finalization_segment_core_sha256")
        or frozen_reference.get("frozen_journal_head")
        != reference.get("frozen_journal_head")
    ):
        raise IranResearchAcceptanceError("workspace reference package 绑定漂移")
    package_root = Path(reference["package_root"])
    sealed_slots = _sealed_segment_chain(reference)
    if (
        [dict(row["receipt_ref"]) for row in sealed_slots]
        != frozen_reference.get("segment_receipt_refs")
        or [dict(row["payload_ref"]) for row in sealed_slots]
        != frozen_reference.get("segment_payload_refs")
    ):
        raise IranResearchAcceptanceError("workspace sealed segment index ref 漂移")
    return {
        "root": root,
        "genesis": genesis,
        "update": update,
        "reference": reference,
        "package_root": package_root,
        "manifest": reference["manifest"],
        "manifest_index": _manifest_index(reference["manifest"]),
        "sealed_slots": sealed_slots,
        "profile": _json_content(package_root, reference["manifest"], "frozen/profile.json"),
        "selection": _json_content(
            package_root, reference["manifest"], "frozen/input-selection.json"
        ),
        "compatible_snapshot": _json_content(
            package_root, reference["manifest"], "frozen/compatible-mapping.json"
        ),
        "revised_snapshot": _json_content(
            package_root, reference["manifest"], "frozen/revised-mapping.json"
        ),
    }


def compute_anchor_verification_candidate(
    workspace_root: os.PathLike[str] | str,
) -> Mapping[str, Any]:
    """在受 supervisor 约束的独立 child 中深验 22 张 anchor。"""

    context = _workspace_source_context(workspace_root)
    genesis = context["genesis"]
    anchor_root = _assert_directory(
        Path(genesis["analysis_rib_anchor_root"]), "analysis_rib_anchor_root"
    )
    try:
        verification = verify_analysis_rib_anchor_root(
            anchor_root,
            selection=context["selection"],
            profile=context["profile"],
            bindings=genesis["bindings"],
        )
    except (TypeError, ValueError) as error:
        raise IranResearchAcceptanceError("analysis RIB 22-anchor 深验失败") from error
    if (
        verification.get("verified") is not True
        or verification.get("anchor_count") != EXPECTED_ANCHOR_COUNT
        or verification.get("analysis_rib_count") != EXPECTED_ANALYSIS_RIB_COUNT
        or verification.get("baseline_reference_rib_count")
        != EXPECTED_BASELINE_RIB_COUNT
        or verification.get("execution_ready") is not True
        or verification.get("blocking_reasons") != []
        or verification.get("database_writes") != 0
        or verification.get("cumulative_reserved_raw_read_bytes", 0)
        >= DEFAULT_MAX_RAW_READ_BYTES
    ):
        raise IranResearchAcceptanceError("analysis RIB anchor 未达到总验收前置门")

    anchor_genesis_path = anchor_root / "ledger/GENESIS.json"
    anchor_genesis = _load_anchor_genesis(anchor_root)
    prior = anchor_genesis.get("prior_raw_accounting")
    frozen = genesis["reference_package"]["frozen_journal_head"]
    if (
        not isinstance(prior, Mapping)
        or prior.get("run_id") != frozen.get("run_id")
        or prior.get("bindings") != genesis.get("bindings")
        or prior.get("terminal_receipt_ref") != frozen.get("terminal_receipt_ref")
        or prior.get("shard_chain_sha256") != frozen.get("shard_chain_sha256")
        or prior.get("completed_artifact_count")
        != frozen.get("completed_artifact_count")
        or prior.get("total_artifacts") != frozen.get("total_artifacts")
        or prior.get("cumulative_reserved_raw_bytes")
        != genesis.get("update_resources", {}).get("cumulative_reserved_raw_bytes")
    ):
        raise IranResearchAcceptanceError("analysis anchor prior raw 未接在 reference UPDATE terminal 后")

    receipt_refs = []
    max_peak = 0
    database_writes = 0
    for path in sorted((anchor_root / "receipts").glob("anchor-*.json")):
        receipt = _load_anchor_json(
            path,
            schema_version=ANCHOR_RECEIPT_SCHEMA_VERSION,
            maximum_bytes=64 * 1024 * 1024,
        )
        ref = _file_ref(path)
        receipt_refs.append(
            {
                **ref,
                "anchor_id": receipt.get("anchor_id"),
                "artifact_id": receipt.get("artifact", {}).get("artifact_id"),
                "role": receipt.get("artifact", {}).get("role"),
                "boundary_at_utc": receipt.get("boundary_at_utc"),
                "projection_semantic_sha256": receipt.get("projection", {}).get(
                    "semantic_sha256"
                ),
            }
        )
        resources = receipt.get("resources")
        if not isinstance(resources, Mapping):
            raise IranResearchAcceptanceError("anchor receipt resources 缺失")
        max_peak = max(
            max_peak,
            _nonnegative(resources.get("peak_temporary_bytes"), "anchor peak temporary"),
        )
        database_writes += _nonnegative(
            resources.get("database_writes"), "anchor database writes"
        )
    if (
        len(receipt_refs) != EXPECTED_ANCHOR_COUNT
        or max_peak >= DEFAULT_MAX_TEMPORARY_BYTES
        or database_writes != 0
    ):
        raise IranResearchAcceptanceError("anchor receipt 集合或 5GB/DB 资源门非法")
    return {
        "workspace_genesis_fingerprint_sha256": genesis["fingerprint_sha256"],
        "analysis_rib_anchor_root": str(anchor_root.resolve()),
        "anchor_genesis_ref": _file_ref(anchor_genesis_path),
        "anchor_receipt_refs": receipt_refs,
        "verification": dict(verification),
        "resources": {
            "cumulative_reserved_raw_bytes": verification[
                "cumulative_reserved_raw_read_bytes"
            ],
            "peak_temporary_bytes": max_peak,
            "database_writes": database_writes,
        },
        "deep_verification_scope": (
            "verify_analysis_rib_anchor_root_replayed_22_anchor_route_events_raw_refs_"
            "states_projections_ledgers_retirements_and_execution_closure"
        ),
    }


def publish_anchor_verification_gate(
    workspace_root: os.PathLike[str] | str,
    *,
    candidate: Mapping[str, Any],
    supervision: Mapping[str, Any],
) -> Mapping[str, Any]:
    root = _assert_safe_workspace_mutation(workspace_root)
    genesis = _load_workspace_genesis(root)
    _verify_successful_supervision(supervision, command_kind="analysis-rib-deep-verify")
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("workspace_genesis_fingerprint_sha256")
        != genesis.get("fingerprint_sha256")
        or candidate.get("analysis_rib_anchor_root")
        != genesis.get("analysis_rib_anchor_root")
        or candidate.get("verification", {}).get("execution_ready") is not True
        or candidate.get("resources", {}).get("database_writes") != 0
        or candidate.get("resources", {}).get("cumulative_reserved_raw_bytes", 0)
        >= DEFAULT_MAX_RAW_READ_BYTES
        or candidate.get("resources", {}).get("peak_temporary_bytes", 0)
        >= DEFAULT_MAX_TEMPORARY_BYTES
    ):
        raise IranResearchAcceptanceError("anchor deep-verification candidate 非法")
    gate = _fingerprinted(
        ANCHOR_GATE_SCHEMA_VERSION,
        {**dict(candidate), "supervision": dict(supervision)},
    )
    target = root / "ANCHOR-VERIFICATION.json"
    artifact = write_canonical_json(
        target, gate, kind="analysis-rib-deep-verification-gate", mode=0o440
    )
    os.chmod(artifact.path, 0o440)
    return dict(gate)


def _load_anchor_gate(root: Path) -> Mapping[str, Any]:
    gate = _load_json(root / "ANCHOR-VERIFICATION.json", maximum_bytes=64 * 1024 * 1024)
    gate = _verify_fingerprint(gate, ANCHOR_GATE_SCHEMA_VERSION, "anchor gate")
    _verify_successful_supervision(
        gate.get("supervision"), command_kind="analysis-rib-deep-verify"
    )
    genesis = _load_workspace_genesis(root)
    if (
        gate.get("workspace_genesis_fingerprint_sha256")
        != genesis.get("fingerprint_sha256")
        or gate.get("analysis_rib_anchor_root")
        != genesis.get("analysis_rib_anchor_root")
        or gate.get("verification", {}).get("execution_ready") is not True
    ):
        raise IranResearchAcceptanceError("anchor gate 与 workspace 不一致")
    return gate


def _segment_relative(index: int) -> str:
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < EXPECTED_SEGMENT_COUNT:
        raise IranResearchAcceptanceError("segment_index 越出 0..21")
    return f"segments/segment-{index:04d}.jsonl.gz"


def _segment_file_ref(root: Path, index: int) -> Mapping[str, Any]:
    path = root / _segment_relative(index)
    raw = _read_stable_regular(path, maximum_bytes=2_000_000_000)
    return {
        "path": _segment_relative(index),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }


def _load_segment(root: Path, index: int) -> Mapping[str, Any]:
    path = root / _segment_relative(index)
    value = _load_one_record_gzip(path)
    value = _verify_fingerprint(value, SEGMENT_SCHEMA_VERSION, f"segment {index}")
    expected_plan = _load_workspace_genesis(root).get("segment_plan", [])[index]
    if value.get("segment_index") != index or value.get("plan") != expected_plan:
        raise IranResearchAcceptanceError(f"segment {index} 与冻结 plan 不一致")
    _verify_successful_supervision(
        value.get("supervision"), command_kind=f"reconciliation-segment-{index:02d}"
    )
    if index == 0:
        if value.get("predecessor_segment_ref") is not None:
            raise IranResearchAcceptanceError("segment 0 不得伪造 predecessor")
    else:
        expected_ref = _segment_file_ref(root, index - 1)
        if value.get("predecessor_segment_ref") != expected_ref:
            raise IranResearchAcceptanceError(f"segment {index} predecessor 链断裂")
    return value


def _anchor_receipts_by_artifact(
    anchor_root: Path, gate: Mapping[str, Any]
) -> Mapping[str, Mapping[str, Any]]:
    frozen_refs = gate.get("anchor_receipt_refs")
    if not isinstance(frozen_refs, list) or len(frozen_refs) != EXPECTED_ANCHOR_COUNT:
        raise IranResearchAcceptanceError("anchor gate receipt refs 非法")
    by_id = {}
    for ref in frozen_refs:
        if not isinstance(ref, Mapping):
            raise IranResearchAcceptanceError("anchor gate receipt ref 必须是对象")
        path = Path(str(ref.get("path"))).absolute()
        current = _file_ref(path)
        if any(current.get(field) != ref.get(field) for field in ("path", "sha256", "size_bytes")):
            raise IranResearchAcceptanceError("anchor receipt 在 deep gate 后漂移")
        receipt = _load_anchor_json(
            path,
            schema_version=ANCHOR_RECEIPT_SCHEMA_VERSION,
            maximum_bytes=64 * 1024 * 1024,
        )
        artifact_id = receipt.get("artifact", {}).get("artifact_id")
        if (
            not isinstance(artifact_id, str)
            or artifact_id in by_id
            or receipt.get("anchor_id") != ref.get("anchor_id")
            or receipt.get("boundary_at_utc") != ref.get("boundary_at_utc")
            or receipt.get("projection", {}).get("semantic_sha256")
            != ref.get("projection_semantic_sha256")
        ):
            raise IranResearchAcceptanceError("anchor receipt gate identity 不一致")
        by_id[artifact_id] = receipt
    return by_id


def _projection_key(row: Mapping[str, Any]) -> Tuple[str, str, str, str]:
    required = ("collector_id", "vp_id", "afi_safi", "prefix")
    if any(not isinstance(row.get(field), str) or not row.get(field) for field in required):
        raise IranResearchAcceptanceError("source-independent projection route key 非法")
    return tuple(str(row[field]) for field in required)  # type: ignore[return-value]


def _projection_index(
    rows: Sequence[Mapping[str, Any]], *, field: str
) -> Mapping[Tuple[str, str, str, str], Mapping[str, Any]]:
    result = {}
    previous = None
    for row in rows:
        if not isinstance(row, Mapping):
            raise IranResearchAcceptanceError(f"{field} projection 行非法")
        key = _projection_key(row)
        if key in result:
            raise IranResearchAcceptanceError(f"{field} projection route key 重复")
        if previous is not None and key <= previous:
            raise IranResearchAcceptanceError(f"{field} projection 未严格排序")
        result[key] = dict(row)
        previous = key
    return result


def _audit_projection_row(row: Optional[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    if row is None:
        return None
    return {
        "peer_ip": row.get("peer_ip"),
        "peer_asn": row.get("peer_asn"),
        "as_path": row.get("as_path"),
        "origin_state": row.get("origin_state"),
        "origin_asns": row.get("origin_asns"),
        "origin_reason": row.get("origin_reason"),
        "quality_flags": row.get("quality_flags"),
        "row_semantic_sha256": hashlib.sha256(
            canonical_json(dict(row)).encode("utf-8")
        ).hexdigest(),
    }


def _key_payload(key: Tuple[str, str, str, str]) -> Mapping[str, str]:
    return {
        "collector_id": key[0],
        "vp_id": key[1],
        "afi_safi": key[2],
        "prefix": key[3],
    }


def _compare_projections(
    *,
    boundary_at_utc: str,
    anchor_receipt: Mapping[str, Any],
    rib_rows: Sequence[Mapping[str, Any]],
    update_projection: Mapping[str, Any],
) -> Mapping[str, Any]:
    if anchor_receipt.get("artifact", {}).get("role") != "analysis_rib":
        raise IranResearchAcceptanceError("baseline_reference_rib 不得冒充 UPDATE 边界")
    if anchor_receipt.get("boundary_at_utc") != boundary_at_utc:
        raise IranResearchAcceptanceError("analysis RIB anchor 与 UPDATE boundary 时间不一致")
    if (
        anchor_receipt.get("projection", {}).get("semantics") != PROJECTION_SEMANTICS
        or update_projection.get("semantics") != PROJECTION_SEMANTICS
    ):
        raise IranResearchAcceptanceError("RIB/UPDATE projection semantics 不一致")
    update_rows = update_projection.get("rows")
    if not isinstance(update_rows, list):
        raise IranResearchAcceptanceError("UPDATE projection rows 非法")
    if (
        _projection_sha256(rib_rows)
        != anchor_receipt.get("projection", {}).get("semantic_sha256")
        or _projection_sha256(update_rows)
        != update_projection.get("semantic_sha256")
    ):
        raise IranResearchAcceptanceError("RIB/UPDATE projection 语义 SHA 与 rows 不一致")
    rib_index = _projection_index(rib_rows, field="RIB")
    update_index = _projection_index(update_rows, field="UPDATE")
    classes = {
        "matched": [],
        "missing_in_update": [],
        "missing_in_rib": [],
        "path_changed": [],
    }
    for key in sorted(set(rib_index) | set(update_index)):
        rib = rib_index.get(key)
        update = update_index.get(key)
        if rib is None:
            label = "missing_in_rib"
        elif update is None:
            label = "missing_in_update"
        elif canonical_json(rib) == canonical_json(update):
            label = "matched"
        else:
            label = "path_changed"
        classes[label].append(key)
    samples = {}
    for label, keys in classes.items():
        values = []
        for key in keys[:MAX_DIFFERENCE_SAMPLES_PER_CLASS]:
            values.append(
                {
                    "route_key": _key_payload(key),
                    "rib": _audit_projection_row(rib_index.get(key)),
                    "update": _audit_projection_row(update_index.get(key)),
                }
            )
        samples[label] = values
    rib_vps = sorted({key[1] for key in rib_index})
    update_vps = sorted({key[1] for key in update_index})
    semantic = {
        "semantics": RECONCILIATION_SEMANTICS,
        "boundary_at_utc": boundary_at_utc,
        "anchor_id": anchor_receipt.get("anchor_id"),
        "analysis_rib_artifact_id": anchor_receipt.get("artifact", {}).get("artifact_id"),
        "projection_semantics": PROJECTION_SEMANTICS,
        "rib_projection_semantic_sha256": anchor_receipt.get("projection", {}).get(
            "semantic_sha256"
        ),
        "update_projection_semantic_sha256": update_projection.get("semantic_sha256"),
        "counts": {label: len(keys) for label, keys in classes.items()},
        "vp_coverage": {
            "rib_observed_vp_ids": list(anchor_receipt.get("observed_vp_ids", ())),
            "rib_projection_vp_ids": rib_vps,
            "update_projection_vp_ids": update_vps,
            "common_vp_ids": sorted(set(rib_vps) & set(update_vps)),
            "missing_in_update_vp_ids": sorted(set(rib_vps) - set(update_vps)),
            "missing_in_rib_vp_ids": sorted(set(update_vps) - set(rib_vps)),
        },
        "difference_samples": samples,
        "update_curve_action": "none_independent_reconciliation_only",
        "real_differences_allowed_if_fully_classified": True,
        "causal_claim_allowed": False,
    }
    return {
        **semantic,
        "semantic_sha256": hashlib.sha256(
            canonical_json(
                {"schema": "rrc25_iran_rib_update_boundary_reconciliation_v1", "value": semantic}
            ).encode("utf-8")
        ).hexdigest(),
    }


def _baseline_reference_summary(
    anchor_root: Path, receipt: Mapping[str, Any]
) -> Mapping[str, Any]:
    if receipt.get("artifact", {}).get("role") != "baseline_reference_rib":
        raise IranResearchAcceptanceError("baseline receipt role 非法")
    projection = receipt.get("projection")
    if not isinstance(projection, Mapping):
        raise IranResearchAcceptanceError("baseline projection 缺失")
    rows = tuple(_verify_anchor_shard_ref(anchor_root, projection.get("shard")))
    index = _projection_index(rows, field="baseline RIB")
    semantic = {
        "role": "baseline_reference_rib",
        "anchor_id": receipt.get("anchor_id"),
        "artifact_id": receipt.get("artifact", {}).get("artifact_id"),
        "boundary_at_utc": receipt.get("boundary_at_utc"),
        "projection_semantics": projection.get("semantics"),
        "projection_semantic_sha256": projection.get("semantic_sha256"),
        "route_count": len(index),
        "observed_vp_ids": list(receipt.get("observed_vp_ids", ())),
        "usage": "independent_reference_only_never_update_boundary_or_curve_reset",
        "compared_to_update_boundary": False,
    }
    return {
        **semantic,
        "semantic_sha256": hashlib.sha256(
            canonical_json(
                {"schema": "rrc25_iran_baseline_reference_rib_v1", "value": semantic}
            ).encode("utf-8")
        ).hexdigest(),
    }


def _evidence_resolutions(
    route_rows: Sequence[Mapping[str, Any]],
    raw_rows: Sequence[Mapping[str, Any]],
    requested_ids: set[str],
    *,
    route_source_ref: Mapping[str, Any],
    raw_source_ref: Mapping[str, Any],
) -> Tuple[Mapping[str, Any], ...]:
    raw_by_route = {}
    for raw in raw_rows:
        route_id = raw.get("route_event_id")
        if route_id in requested_ids:
            if route_id in raw_by_route:
                raise IranResearchAcceptanceError("requested RouteEvent 对应重复 raw ref")
            raw_by_route[route_id] = dict(raw)
    values = []
    for route in route_rows:
        route_id = route.get("route_event_id")
        if route_id not in requested_ids:
            continue
        raw = raw_by_route.get(route_id)
        if (
            raw is None
            or route.get("raw_record_ref_id") != raw.get("raw_record_ref_id")
            or route.get("artifact_id") != raw.get("artifact_id")
            or route.get("file_sha256") != raw.get("file_sha256")
            or route.get("record_ordinal") != raw.get("record_ordinal")
            or route.get("element_ordinal") != raw.get("element_ordinal")
            or raw.get("verification_status") != "verified"
        ):
            raise IranResearchAcceptanceError("requested RouteEvent→raw ref 未闭合")
        values.append(
            {
                "route_event_id": route_id,
                "route_event": dict(route),
                "raw_record_ref": dict(raw),
                "route_event_source_ref": dict(route_source_ref),
                "raw_record_source_ref": dict(raw_source_ref),
            }
        )
    return tuple(sorted(values, key=lambda row: row["route_event_id"]))


def compute_reconciliation_segment_candidate(
    workspace_root: os.PathLike[str] | str,
    *,
    segment_index: int,
    self_stop_seconds: float = DEFAULT_SEGMENT_SELF_STOP_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    """计算一个可恢复 segment candidate；调用方成功监督后才能发布。"""

    if isinstance(segment_index, bool) or not isinstance(segment_index, int) or not 0 <= segment_index < EXPECTED_SEGMENT_COUNT:
        raise IranResearchAcceptanceError("segment_index 越出 0..21")
    limit = _positive_seconds(self_stop_seconds, "self_stop_seconds")
    if limit > DEFAULT_SEGMENT_SELF_STOP_SECONDS:
        raise IranResearchAcceptanceError("segment self-stop 不得放宽")
    started = monotonic()

    def check_runtime(phase: str) -> None:
        if monotonic() - started >= limit:
            raise IranResearchAcceptanceError(
                f"segment 在 {phase} 达到 {limit:.0f}s 自停止门，未发布"
            )

    context = _workspace_source_context(workspace_root)
    root = context["root"]
    genesis = context["genesis"]
    gate = _load_anchor_gate(root)
    plan = genesis.get("segment_plan", [])[segment_index]
    if plan.get("segment_index") != segment_index:
        raise IranResearchAcceptanceError("segment plan index 非法")
    predecessor_ref = None
    if segment_index == 0:
        prior_compact: Optional[Mapping[str, Any]] = None
    else:
        predecessor = _load_segment(root, segment_index - 1)
        prior_compact = predecessor.get("ending_compact_state")
        if not isinstance(prior_compact, Mapping):
            raise IranResearchAcceptanceError("predecessor 缺少 ending compact state")
        predecessor_ref = _segment_file_ref(root, segment_index - 1)

    requested_ids = set(genesis.get("requested_evidence_route_event_ids", ()))
    if any(not isinstance(value, str) for value in requested_ids):
        raise IranResearchAcceptanceError("requested evidence RouteEvent IDs 非法")
    source_refs = []
    resolutions = []
    sealed_slots = context["sealed_slots"]
    manifest_index = context["manifest_index"]

    if segment_index == 0:
        packaged_workspace_genesis = context["reference"].get("workspace_genesis")
        if not isinstance(packaged_workspace_genesis, Mapping) or not isinstance(
            packaged_workspace_genesis.get("initial_compact_state"), Mapping
        ):
            raise IranResearchAcceptanceError(
                "v2 packaged GENESIS 缺少 initial compact state"
            )
        prior_compact = dict(
            packaged_workspace_genesis["initial_compact_state"]
        )
        compact_state_from_payload(prior_compact)
        genesis_ref = _file_ref(context["package_root"] / "GENESIS")
        genesis_source_ref = {
            "kind": "workspace-genesis",
            "path": "GENESIS",
            "sha256": genesis_ref["sha256"],
            "size_bytes": genesis_ref["size_bytes"],
            "record_count": 1,
            "embedded_collection": "next_compact_state",
        }
        _package_ref_in_manifest(
            manifest_index,
            genesis_source_ref,
            expected_kind="workspace-genesis",
        )
        source_refs.append(genesis_source_ref)
    if prior_compact is None:  # pragma: no cover - 上述分支保证
        raise IranResearchAcceptanceError("segment 缺少 prior compact state")

    start_sequence = _nonnegative(
        plan.get("start_receipt_sequence_inclusive"), "segment start sequence"
    )
    end_sequence = _nonnegative(
        plan.get("end_receipt_sequence_inclusive"), "segment end sequence"
    )
    if end_sequence >= start_sequence:
        for sequence in range(start_sequence, end_sequence + 1):
            check_runtime(f"sealed segment {sequence} 前")
            try:
                slot = sealed_slots[sequence - 1]
            except IndexError as error:
                raise IranResearchAcceptanceError(
                    f"sealed segment {sequence} 缺失"
                ) from error
            receipt_ref = slot["receipt_ref"]
            payload_ref = slot["payload_ref"]
            receipt = slot["receipt"]
            payload = slot["payload"]
            receipt_source_ref = {
                "kind": "finalization-segment-receipt",
                **dict(receipt_ref),
                "record_count": 1,
            }
            payload_source_ref = {
                "kind": "finalization-segment-payload",
                **dict(payload_ref),
                "record_count": 1,
            }
            _package_ref_in_manifest(
                manifest_index,
                receipt_source_ref,
                expected_kind="finalization-segment-receipt",
            )
            _package_ref_in_manifest(
                manifest_index,
                payload_source_ref,
                expected_kind="finalization-segment-payload",
            )
            source_refs.extend((receipt_source_ref, payload_source_ref))
            artifact_payload = receipt.get("artifact")
            try:
                artifact = _journal_contract._artifact_from_dict(
                    artifact_payload, "sealed segment artifact"
                )
            except (TypeError, ValueError) as error:
                raise IranResearchAcceptanceError(
                    f"sealed segment {sequence} artifact 非法"
                ) from error
            if artifact.index != sequence - 1:
                raise IranResearchAcceptanceError(
                    f"sealed segment {sequence} artifact index 不连续"
                )
            route_rows = tuple(payload["route_event_rows"])
            raw_rows = tuple(payload["raw_record_ref_rows"])
            next_compact = receipt.get("next_compact_state")
            if not isinstance(next_compact, Mapping):
                raise IranResearchAcceptanceError(
                    f"sealed segment {sequence} 缺少 next compact state"
                )
            compact_state_from_payload(next_compact)
            prior_compact = dict(next_compact)
            route_source_ref = _sealed_segment_source_ref(
                payload_ref, embedded_collection="route_event_rows"
            )
            raw_source_ref = _sealed_segment_source_ref(
                payload_ref, embedded_collection="raw_record_ref_rows"
            )
            _package_ref_in_manifest(
                manifest_index,
                route_source_ref,
                expected_kind="finalization-segment-payload",
            )
            _package_ref_in_manifest(
                manifest_index,
                raw_source_ref,
                expected_kind="finalization-segment-payload",
            )
            resolutions.extend(
                _evidence_resolutions(
                    route_rows,
                    raw_rows,
                    requested_ids,
                    route_source_ref=route_source_ref,
                    raw_source_ref=raw_source_ref,
                )
            )
            check_runtime(f"sealed segment {sequence} 后")

    compact = compact_state_from_payload(prior_compact)
    anchor_root = Path(genesis["analysis_rib_anchor_root"])
    anchors = _anchor_receipts_by_artifact(anchor_root, gate)
    boundary = plan.get("boundary_at_utc")
    reconciliation = None
    baseline_summary = None
    if boundary is not None:
        boundary = _utc(boundary, "segment boundary")
        artifact_id = plan.get("analysis_rib_artifact_id")
        receipt = anchors.get(artifact_id)
        if receipt is None:
            raise IranResearchAcceptanceError("segment boundary 缺少 analysis RIB anchor")
        projection_info = receipt.get("projection")
        if not isinstance(projection_info, Mapping):
            raise IranResearchAcceptanceError("analysis RIB projection ref 缺失")
        rib_rows = tuple(
            _verify_anchor_shard_ref(anchor_root, projection_info.get("shard"))
        )
        update_projection = build_source_independent_route_projection(compact.route_state)
        reconciliation = _compare_projections(
            boundary_at_utc=boundary,
            anchor_receipt=receipt,
            rib_rows=rib_rows,
            update_projection=update_projection,
        )
        if segment_index > 0 and compact.last_slot_end_exclusive_utc != boundary:
            raise IranResearchAcceptanceError("segment compact 终点未精确落在 RIB 边界")
        if segment_index == 0:
            baseline_id = genesis.get("baseline_reference_rib", {}).get("artifact_id")
            baseline_receipt = anchors.get(baseline_id)
            if baseline_receipt is None:
                raise IranResearchAcceptanceError("缺少唯一 baseline reference anchor")
            baseline_summary = _baseline_reference_summary(anchor_root, baseline_receipt)
    elif segment_index != EXPECTED_SEGMENT_COUNT - 1:
        raise IranResearchAcceptanceError("只有 terminal tail 可以没有 RIB boundary")
    terminal_end = genesis.get("window", {}).get("end_exclusive_utc")
    if segment_index == EXPECTED_SEGMENT_COUNT - 1 and compact.last_slot_end_exclusive_utc != terminal_end:
        raise IranResearchAcceptanceError("terminal tail 未重放到完整窗口终点")

    unique_resolutions = {}
    for row in resolutions:
        route_id = row["route_event_id"]
        if route_id in unique_resolutions:
            raise IranResearchAcceptanceError("同一 requested RouteEvent 跨 source 重复")
        unique_resolutions[route_id] = row
    source_semantic = hashlib.sha256(
        canonical_json(
            {
                "schema": "rrc25_iran_reconciliation_segment_source_refs_v1",
                "refs": source_refs,
            }
        ).encode("utf-8")
    ).hexdigest()
    return {
        "segment_index": segment_index,
        "workspace_genesis_fingerprint_sha256": genesis["fingerprint_sha256"],
        "anchor_gate_fingerprint_sha256": gate["fingerprint_sha256"],
        "plan": dict(plan),
        "predecessor_segment_ref": predecessor_ref,
        "ending_compact_state": dict(prior_compact),
        "boundary_reconciliation": reconciliation,
        "baseline_reference": baseline_summary,
        "evidence_resolutions": [unique_resolutions[key] for key in sorted(unique_resolutions)],
        "source_refs": source_refs,
        "source_refs_semantic_sha256": source_semantic,
        "resources": {
            "real_mrt_raw_bytes_read": 0,
            "record_observation_shard_reads": 0,
            "database_writes": 0,
            "temporary_bytes_exclusive_limit": DEFAULT_MAX_TEMPORARY_BYTES,
        },
    }


def publish_reconciliation_segment(
    workspace_root: os.PathLike[str] | str,
    *,
    candidate: Mapping[str, Any],
    supervision: Mapping[str, Any],
) -> Mapping[str, Any]:
    root = _assert_safe_workspace_mutation(workspace_root)
    genesis = _load_workspace_genesis(root)
    if not isinstance(candidate, Mapping):
        raise IranResearchAcceptanceError("segment candidate 必须是对象")
    index = candidate.get("segment_index")
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < EXPECTED_SEGMENT_COUNT:
        raise IranResearchAcceptanceError("segment candidate index 非法")
    _verify_successful_supervision(
        supervision, command_kind=f"reconciliation-segment-{index:02d}"
    )
    if (
        candidate.get("workspace_genesis_fingerprint_sha256")
        != genesis.get("fingerprint_sha256")
        or candidate.get("plan") != genesis.get("segment_plan", [])[index]
        or candidate.get("resources")
        != {
            "real_mrt_raw_bytes_read": 0,
            "record_observation_shard_reads": 0,
            "database_writes": 0,
            "temporary_bytes_exclusive_limit": DEFAULT_MAX_TEMPORARY_BYTES,
        }
    ):
        raise IranResearchAcceptanceError("segment candidate 与 workspace/资源门不一致")
    segment = _fingerprinted(
        SEGMENT_SCHEMA_VERSION,
        {**dict(candidate), "supervision": dict(supervision)},
    )
    target = root / _segment_relative(index)
    artifact = write_canonical_jsonl_gzip(
        target, (segment,), kind="iran-rib-update-reconciliation-segment", mode=0o440
    )
    os.chmod(artifact.path, 0o440)
    if artifact.size_bytes >= DEFAULT_MAX_TEMPORARY_BYTES:
        raise IranResearchAcceptanceError("segment 输出达到或超过 5GB")
    return dict(segment)


def _validate_evidence_link(
    link: Mapping[str, Any],
    resolution: Mapping[str, Any],
    *,
    manifest_index: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    route = resolution.get("route_event")
    raw = resolution.get("raw_record_ref")
    if not isinstance(route, Mapping) or not isinstance(raw, Mapping):
        raise IranResearchAcceptanceError("evidence resolution 缺少 RouteEvent/raw ref")
    required = {
        "route_event_id",
        "raw_record_ref_id",
        "artifact_id",
        "artifact_sha256",
        "record_ordinal",
        "element_ordinal",
    }
    if not isinstance(link, Mapping) or set(link) != required:
        raise IranResearchAcceptanceError("episode-AS evidence link 字段不闭合")
    if (
        resolution.get("route_event_id") != link.get("route_event_id")
        or route.get("route_event_id") != link.get("route_event_id")
        or route.get("raw_record_ref_id") != link.get("raw_record_ref_id")
        or raw.get("raw_record_ref_id") != link.get("raw_record_ref_id")
        or route.get("artifact_id") != link.get("artifact_id")
        or raw.get("artifact_id") != link.get("artifact_id")
        or route.get("file_sha256") != link.get("artifact_sha256")
        or raw.get("file_sha256") != link.get("artifact_sha256")
        or route.get("record_ordinal") != link.get("record_ordinal")
        or raw.get("record_ordinal") != link.get("record_ordinal")
        or route.get("element_ordinal") != link.get("element_ordinal")
        or raw.get("element_ordinal") != link.get("element_ordinal")
        or raw.get("verification_status") != "verified"
        or raw.get("record_hash") != raw.get("raw_record_sha256")
    ):
        raise IranResearchAcceptanceError("episode-AS evidence link 未闭合到同一 RouteEvent/raw 坐标")
    expected_collections = {
        "route_event_source_ref": "route_event_rows",
        "raw_record_source_ref": "raw_record_ref_rows",
    }
    for field, expected_collection in expected_collections.items():
        ref = resolution.get(field)
        if (
            not isinstance(ref, Mapping)
            or ref.get("embedded_collection") != expected_collection
        ):
            raise IranResearchAcceptanceError(
                "evidence resolution 未绑定 sealed segment 内嵌集合"
            )
        _package_ref_in_manifest(
            manifest_index,
            ref,
            expected_kind="finalization-segment-payload",
        )
    return {
        "route_event_id": link["route_event_id"],
        "raw_record_ref_id": link["raw_record_ref_id"],
        "artifact_id": link["artifact_id"],
        "artifact_sha256": link["artifact_sha256"],
        "record_ordinal": link["record_ordinal"],
        "element_ordinal": link["element_ordinal"],
        "raw_record_sha256": raw["raw_record_sha256"],
        "record_offset": raw["record_offset"],
        "record_length": raw["record_length"],
        "closure_state": "closed_in_v2_sealed_segment_payload",
    }


def _category_predicates() -> Tuple[Tuple[str, str, Callable[[Mapping[str, Any]], bool]], ...]:
    def family(row: Mapping[str, Any], afi: str) -> Optional[Mapping[str, Any]]:
        families = row.get("address_families")
        value = families.get(afi) if isinstance(families, Mapping) else None
        return value if isinstance(value, Mapping) else None

    def visibility(row: Mapping[str, Any], afi: str) -> Optional[bool]:
        value = family(row, afi)
        observed = value.get("visibility") if isinstance(value, Mapping) else None
        if not isinstance(observed, Mapping) or observed.get("visibility_state") != "observed":
            return None
        result = observed.get("fully_invisible")
        return result if isinstance(result, bool) else None

    return (
        (
            "ipv4_fully_invisible",
            "IPv4 完全不可见",
            lambda row: visibility(row, "ipv4") is True,
        ),
        (
            "partially_visible",
            "仍有部分路由可见",
            lambda row: row.get("overall_classification") == "partially_visible",
        ),
        (
            "ipv6_still_visible",
            "IPv6 仍可见",
            lambda row: row.get("cumulative_member") is True
            and visibility(row, "ipv6") is False,
        ),
        (
            "observation_end_not_recovered",
            "观察终点仍未恢复",
            lambda row: row.get("observation_end_member") is True
            and row.get("recovered_at") is None,
        ),
    )


def _episode_as_sample(
    row: Mapping[str, Any], verified_links: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any]:
    return {
        "episode_as_id": row.get("episode_as_id"),
        "episode_id": row.get("episode_id"),
        "asn": row.get("asn"),
        "first_damaged_at": row.get("first_damaged_at"),
        "last_damaged_at": row.get("last_damaged_at"),
        "recovered_at": row.get("recovered_at"),
        "observation_end_member": row.get("observation_end_member"),
        "overall_classification": row.get("overall_classification"),
        "address_families": row.get("address_families"),
        "verified_evidence_link_count": len(verified_links),
        "verified_evidence_links": list(verified_links[:5]),
        "evidence_sample_truncated": len(verified_links) > 5,
    }


def _four_category_scan(
    rows: Sequence[Mapping[str, Any]],
    *,
    resolutions: Mapping[str, Mapping[str, Any]],
    manifest_index: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    verified_by_record = {}
    link_count = 0
    for row in rows:
        row_id = row.get("episode_as_id")
        if not isinstance(row_id, str) or row_id in verified_by_record:
            raise IranResearchAcceptanceError("episode-AS ID 非法或重复")
        verified = []
        for link in row.get("evidence_links", ()):
            route_id = link.get("route_event_id") if isinstance(link, Mapping) else None
            resolution = resolutions.get(str(route_id))
            if resolution is None:
                raise IranResearchAcceptanceError(
                    f"episode-AS evidence link 未在所有 segment 中解析：{route_id}"
                )
            verified.append(
                _validate_evidence_link(
                    link, resolution, manifest_index=manifest_index
                )
            )
            link_count += 1
        verified_by_record[row_id] = tuple(verified)

    categories = []
    reference_closure = True
    for category_id, label_zh, predicate in _category_predicates():
        population = tuple(row for row in rows if predicate(row))
        with_evidence = tuple(
            row
            for row in population
            if verified_by_record.get(str(row.get("episode_as_id")))
        )
        if not population:
            state = "not_observed_after_full_scan"
            blocking = False
            samples = []
        elif not with_evidence:
            state = "observed_population_without_closed_evidence_link"
            blocking = True
            reference_closure = False
            samples = []
        else:
            state = "observed_with_closed_evidence_samples"
            blocking = False
            samples = [
                _episode_as_sample(
                    row,
                    verified_by_record[str(row["episode_as_id"])],
                )
                for row in sorted(
                    with_evidence,
                    key=lambda item: (
                        str(item.get("episode_id")),
                        int(item.get("asn", 0)),
                    ),
                )[:MAX_CATEGORY_SAMPLES]
            ]
        categories.append(
            {
                "category_id": category_id,
                "label_zh": label_zh,
                "scan_state": state,
                "population_count": len(population),
                "population_with_evidence_count": len(with_evidence),
                "sample_count": len(samples),
                "samples": samples,
                "blocking": blocking,
                "empty_population_is_not_sample_success": True,
            }
        )
    semantic = {
        "semantics": SCAN_SEMANTICS,
        "population_scope": "all_compatible_episode_as_records",
        "population_record_count": len(rows),
        "published_evidence_link_count": link_count,
        "all_published_evidence_links_closed": True,
        "all_four_categories_scanned": len(categories) == 4,
        "reference_closure": reference_closure,
        "categories": categories,
    }
    return {
        **semantic,
        "semantic_sha256": hashlib.sha256(
            canonical_json(
                {"schema": "rrc25_iran_four_category_scan_v1", "value": semantic}
            ).encode("utf-8")
        ).hexdigest(),
    }


def _aggregate_reconciliation(
    boundaries: Sequence[Mapping[str, Any]], baseline: Mapping[str, Any]
) -> Mapping[str, Any]:
    if len(boundaries) != EXPECTED_BOUNDARY_COUNT:
        raise IranResearchAcceptanceError("总验收必须恰有 21 个 RIB/UPDATE 边界对账")
    ordered = tuple(sorted(boundaries, key=lambda row: row["boundary_at_utc"]))
    if [row["boundary_at_utc"] for row in ordered] != [
        row["boundary_at_utc"] for row in boundaries
    ]:
        raise IranResearchAcceptanceError("RIB/UPDATE 边界结果未按时间排序")
    aggregate = {
        label: sum(_nonnegative(row.get("counts", {}).get(label), label) for row in ordered)
        for label in ("matched", "missing_in_update", "missing_in_rib", "path_changed")
    }
    semantic = {
        "semantics": RECONCILIATION_SEMANTICS,
        "analysis_rib_boundary_count": len(ordered),
        "baseline_reference_rib_count": 1,
        "baseline_reference": dict(baseline),
        "aggregate_counts": aggregate,
        "boundaries": [dict(row) for row in ordered],
        "classification_complete": True,
        "real_differences_are_disclosed_not_overwritten": True,
        "update_curve_action": "none_independent_reconciliation_only",
    }
    return {
        **semantic,
        "semantic_sha256": hashlib.sha256(
            canonical_json(
                {"schema": "rrc25_iran_21_plus_1_reconciliation_v1", "value": semantic}
            ).encode("utf-8")
        ).hexdigest(),
    }


def acceptance_workspace_status(
    workspace_root: os.PathLike[str] | str,
) -> Mapping[str, Any]:
    root = _assert_directory(Path(workspace_root), "acceptance_workspace")
    genesis = _load_workspace_genesis(root)
    gate_ready = False
    try:
        _load_anchor_gate(root)
        gate_ready = True
    except (OSError, ValueError):
        gate_ready = False
    completed = []
    first_invalid = None
    for index in range(EXPECTED_SEGMENT_COUNT):
        path = root / _segment_relative(index)
        if not path.exists() and not path.is_symlink():
            break
        try:
            _load_segment(root, index)
        except (OSError, ValueError) as error:
            first_invalid = {"segment_index": index, "error": str(error)}
            break
        completed.append(index)
    next_segment = len(completed) if len(completed) < EXPECTED_SEGMENT_COUNT else None
    return {
        "workspace_root": str(root.resolve()),
        "genesis_fingerprint_sha256": genesis["fingerprint_sha256"],
        "anchor_deep_verification_gate_ready": gate_ready,
        "completed_segment_count": len(completed),
        "completed_segment_indices": completed,
        "next_segment_index": next_segment,
        "first_invalid_segment": first_invalid,
        "ready_to_finalize": gate_ready
        and len(completed) == EXPECTED_SEGMENT_COUNT
        and first_invalid is None,
    }


def compute_overall_acceptance_candidate(
    workspace_root: os.PathLike[str] | str,
) -> Mapping[str, Any]:
    """从深验 gate 与 22 个 segment 链总装 accepted candidate；不再 replay。"""

    context = _workspace_source_context(workspace_root)
    root = context["root"]
    genesis = context["genesis"]
    gate = _load_anchor_gate(root)
    segments = [_load_segment(root, index) for index in range(EXPECTED_SEGMENT_COUNT)]
    boundaries = []
    baseline = None
    resolutions = {}
    for index, segment in enumerate(segments):
        source_refs = segment.get("source_refs")
        if not isinstance(source_refs, list):
            raise IranResearchAcceptanceError("segment source_refs 必须是数组")
        for source_ref in source_refs:
            if not isinstance(source_ref, Mapping):
                raise IranResearchAcceptanceError("segment source ref 必须是对象")
            _package_ref_in_manifest(
                context["manifest_index"],
                source_ref,
                expected_kind=source_ref.get("kind"),
            )
        observed_source_sha = hashlib.sha256(
            canonical_json(
                {
                    "schema": "rrc25_iran_reconciliation_segment_source_refs_v1",
                    "refs": source_refs,
                }
            ).encode("utf-8")
        ).hexdigest()
        if observed_source_sha != segment.get("source_refs_semantic_sha256"):
            raise IranResearchAcceptanceError("segment source ref 语义 SHA 不闭合")
        if segment.get("resources") != {
            "real_mrt_raw_bytes_read": 0,
            "record_observation_shard_reads": 0,
            "database_writes": 0,
            "temporary_bytes_exclusive_limit": DEFAULT_MAX_TEMPORARY_BYTES,
        }:
            raise IranResearchAcceptanceError("segment 资源合同漂移")
        boundary = segment.get("boundary_reconciliation")
        if boundary is not None:
            boundary_semantic = dict(boundary)
            supplied_boundary_sha = boundary_semantic.pop("semantic_sha256", None)
            if supplied_boundary_sha != hashlib.sha256(
                canonical_json(
                    {
                        "schema": "rrc25_iran_rib_update_boundary_reconciliation_v1",
                        "value": boundary_semantic,
                    }
                ).encode("utf-8")
            ).hexdigest():
                raise IranResearchAcceptanceError("boundary reconciliation 语义 SHA 不闭合")
            boundaries.append(boundary)
        if segment.get("baseline_reference") is not None:
            if baseline is not None or index != 0:
                raise IranResearchAcceptanceError("baseline reference 必须只出现在 segment 0")
            baseline = segment["baseline_reference"]
            baseline_semantic = dict(baseline)
            supplied_baseline_sha = baseline_semantic.pop("semantic_sha256", None)
            if supplied_baseline_sha != hashlib.sha256(
                canonical_json(
                    {
                        "schema": "rrc25_iran_baseline_reference_rib_v1",
                        "value": baseline_semantic,
                    }
                ).encode("utf-8")
            ).hexdigest():
                raise IranResearchAcceptanceError("baseline reference 语义 SHA 不闭合")
        for row in segment.get("evidence_resolutions", ()):
            route_id = row.get("route_event_id") if isinstance(row, Mapping) else None
            if not isinstance(route_id, str) or route_id in resolutions:
                raise IranResearchAcceptanceError("segment evidence resolution ID 非法或重复")
            resolutions[route_id] = row
    if not isinstance(baseline, Mapping):
        raise IranResearchAcceptanceError("22 个 segment 缺少 baseline reference")
    requested = set(genesis.get("requested_evidence_route_event_ids", ()))
    if set(resolutions) != requested:
        missing = sorted(requested - set(resolutions))[:10]
        extra = sorted(set(resolutions) - requested)[:10]
        raise IranResearchAcceptanceError(
            f"segment evidence resolution 人口不闭合，missing={missing}, extra={extra}"
        )
    episode_rows = _episode_as_rows(context["package_root"], context["manifest"])
    expected_episode_semantic = genesis.get("compatible_episode_as", {}).get(
        "semantic_sha256"
    )
    observed_episode_semantic = hashlib.sha256(
        canonical_json(
            {"semantics": SCAN_SEMANTICS, "rows": list(episode_rows)}
        ).encode("utf-8")
    ).hexdigest()
    if observed_episode_semantic != expected_episode_semantic:
        raise IranResearchAcceptanceError("compatible episode-AS population 在 workspace 初始化后漂移")
    scan = _four_category_scan(
        episode_rows,
        resolutions=resolutions,
        manifest_index=context["manifest_index"],
    )
    reconciliation = _aggregate_reconciliation(boundaries, baseline)

    update_resources = genesis.get("update_resources")
    anchor_resources = gate.get("resources")
    if not isinstance(update_resources, Mapping) or not isinstance(anchor_resources, Mapping):
        raise IranResearchAcceptanceError("UPDATE/anchor resources 缺失")
    segment_refs = [_segment_file_ref(root, index) for index in range(EXPECTED_SEGMENT_COUNT)]
    segment_peak = max((row["size_bytes"] for row in segment_refs), default=0)
    cumulative = _nonnegative(
        anchor_resources.get("cumulative_reserved_raw_bytes"), "overall cumulative raw"
    )
    peak = max(
        _nonnegative(update_resources.get("peak_temporary_bytes"), "update peak"),
        _nonnegative(anchor_resources.get("peak_temporary_bytes"), "anchor peak"),
        segment_peak,
    )
    database_writes = _nonnegative(
        update_resources.get("database_writes"), "update database writes"
    ) + _nonnegative(anchor_resources.get("database_writes"), "anchor database writes")
    update_terminal = context["reference"].get("terminal")
    selected_updates = context["selection"].get("roles", {}).get(
        "analysis_updates"
    )
    strict_v2_gate = (
        context["update"]["receipt"].get("schema_version")
        == UPDATE_ACCEPTANCE_SCHEMA_VERSION
        and context["update"]["receipt"].get("reproduction_scope")
        == UPDATE_REPRODUCTION_SCOPE
        and _SHA256_RE.fullmatch(
            str(
                context["update"]["receipt"].get(
                    "business_semantic_core_sha256"
                )
            )
        )
        is not None
        and _SHA256_RE.fullmatch(
            str(
                context["update"]["receipt"].get(
                    "finalization_segment_core_sha256"
                )
            )
        )
        is not None
        and isinstance(update_terminal, Mapping)
        and update_terminal.get("sealed") is True
        and isinstance(selected_updates, list)
        and update_terminal.get("completed_slots") == len(selected_updates)
        and len(context["sealed_slots"]) == len(selected_updates)
        and genesis.get("update_acceptance", {}).get(
            "reference_terminal_ref"
        )
        == context["reference"].get("terminal_ref")
        and genesis.get("update_acceptance", {}).get(
            "reference_deep_verification_ref"
        )
        == context["reference"].get("deep_verification_ref")
        and genesis.get("update_acceptance", {}).get(
            "reference_segment_index_ref"
        )
        == context["reference"].get("segment_index_ref")
    )
    checks = {
        "update_reproduction_acceptance_receipt_bound": True,
        "strict_v2_terminal_deep_segment_index_bound": strict_v2_gate,
        "business_and_finalization_segment_cores_independently_bound": (
            genesis.get("update_acceptance", {}).get(
                "business_semantic_core_sha256"
            )
            == context["reference"].get("business_semantic_core_sha256")
            and genesis.get("update_acceptance", {}).get(
                "finalization_segment_core_sha256"
            )
            == context["reference"].get(
                "finalization_segment_core_sha256"
            )
        ),
        "update_raw_replay_ab_not_performed_by_user_choice": True,
        "sealed_segment_routeevent_raw_state_used": all(
            row.get("resources", {}).get("record_observation_shard_reads") == 0
            for row in segments
        ),
        "analysis_rib_22_anchor_deep_verified": gate.get("verification", {}).get("verified") is True,
        "analysis_rib_execution_ready": gate.get("verification", {}).get("execution_ready") is True,
        "twenty_one_update_rib_boundaries_reconciled": len(boundaries) == EXPECTED_BOUNDARY_COUNT,
        "one_baseline_reference_separate": baseline.get("compared_to_update_boundary") is False,
        "all_real_differences_classified": reconciliation.get("classification_complete") is True,
        "all_four_categories_scanned": scan.get("all_four_categories_scanned") is True,
        "evidence_reference_closure": scan.get("reference_closure") is True,
        "cumulative_raw_under_50gb_exclusive": cumulative < DEFAULT_MAX_RAW_READ_BYTES,
        "temporary_under_5gb_exclusive": peak < DEFAULT_MAX_TEMPORARY_BYTES,
        "database_writes_zero": database_writes == 0,
        "real_mrt_opened_by_acceptance_stage": False,
        "update_curve_reset_or_rewrite": False,
    }
    if not all(value is True for key, value in checks.items() if key not in {
        "real_mrt_opened_by_acceptance_stage", "update_curve_reset_or_rewrite"
    }) or checks["real_mrt_opened_by_acceptance_stage"] is not False or checks[
        "update_curve_reset_or_rewrite"
    ] is not False:
        raise IranResearchAcceptanceError("overall research acceptance 仍有阻断门")
    return {
        "acceptance_state": "accepted",
        "acceptance_semantics": ACCEPTANCE_SEMANTICS,
        "claim_boundaries": {
            "production_acceptance": False,
            "causal_truth": False,
            "raw_mrt_full_ab_reproduction": False,
            "incident_precursor_causality": "undetermined",
            "allowed_claim": "research_data_loop_completed_with_independent_rib_update_reconciliation",
        },
        "workspace_root": str(root.resolve()),
        "workspace_genesis_ref": _file_ref(root / "GENESIS.json"),
        "anchor_gate_ref": _file_ref(root / "ANCHOR-VERIFICATION.json"),
        "segment_refs": segment_refs,
        "input_bindings": dict(genesis["bindings"]),
        "update_acceptance": dict(genesis["update_acceptance"]),
        "analysis_rib_verification": {
            "anchor_set_semantic_sha256": gate.get("verification", {}).get(
                "anchor_set_semantic_sha256"
            ),
            "anchor_count": gate.get("verification", {}).get("anchor_count"),
            "execution_ready": gate.get("verification", {}).get("execution_ready"),
            "deep_verification_scope": gate.get("deep_verification_scope"),
        },
        "reconciliation": reconciliation,
        "four_category_scan": scan,
        "resources": {
            "cumulative_reserved_raw_bytes": cumulative,
            "cumulative_raw_bytes_exclusive_limit": DEFAULT_MAX_RAW_READ_BYTES,
            "peak_temporary_bytes": peak,
            "temporary_bytes_exclusive_limit": DEFAULT_MAX_TEMPORARY_BYTES,
            "database_writes": database_writes,
            "acceptance_stage_real_mrt_raw_bytes_read": 0,
            "acceptance_stage_record_observation_shard_reads": 0,
        },
        "checks": checks,
        "offline_verification_basis": (
            "strict_v2_terminal_deep_segment_index_plus_anchor_deep_gate_and_22_"
            "supervised_reconciliation_segments_from_sealed_routeevent_raw_state_"
            "payloads_without_record_observation_reread"
        ),
    }


def publish_overall_research_acceptance(
    workspace_root: os.PathLike[str] | str,
    *,
    output_receipt_path: os.PathLike[str] | str,
    candidate: Mapping[str, Any],
    supervision: Mapping[str, Any],
) -> Mapping[str, Any]:
    root = _assert_safe_workspace_mutation(workspace_root)
    _verify_successful_supervision(supervision, command_kind="overall-acceptance-finalize")
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("workspace_root") != str(root.resolve())
        or candidate.get("acceptance_state") != "accepted"
        or candidate.get("acceptance_semantics") != ACCEPTANCE_SEMANTICS
    ):
        raise IranResearchAcceptanceError("overall acceptance candidate 非法")
    semantic = {
        "schema_version": OVERALL_ACCEPTANCE_SCHEMA_VERSION,
        **dict(candidate),
        "supervision": dict(supervision),
    }
    receipt = {
        **semantic,
        "receipt_sha256": hashlib.sha256(
            canonical_json(
                {"schema": "rrc25_iran_overall_research_acceptance_v1", "receipt": semantic}
            ).encode("utf-8")
        ).hexdigest(),
    }
    target = Path(output_receipt_path).expanduser().absolute()
    _assert_safe_mutation_target(target, "overall acceptance receipt")
    artifact = write_canonical_json(
        target, receipt, kind="iran-overall-research-acceptance", mode=0o440
    )
    os.chmod(artifact.path, 0o440)
    return dict(receipt)


def verify_overall_research_acceptance(
    receipt_path: os.PathLike[str] | str,
) -> Mapping[str, Any]:
    """离线核验总 receipt；只重算 create-only 链和小型绑定，不重放 ancestry。"""

    path = Path(receipt_path).expanduser().absolute()
    receipt = _load_json(path, maximum_bytes=512 * 1024 * 1024)
    semantic = dict(receipt)
    supplied = semantic.pop("receipt_sha256", None)
    if (
        receipt.get("schema_version") != OVERALL_ACCEPTANCE_SCHEMA_VERSION
        or receipt.get("acceptance_state") != "accepted"
        or receipt.get("acceptance_semantics") != ACCEPTANCE_SEMANTICS
        or supplied
        != hashlib.sha256(
            canonical_json(
                {"schema": "rrc25_iran_overall_research_acceptance_v1", "receipt": semantic}
            ).encode("utf-8")
        ).hexdigest()
    ):
        raise IranResearchAcceptanceError("overall acceptance receipt 指纹/语义非法")
    _verify_successful_supervision(
        receipt.get("supervision"), command_kind="overall-acceptance-finalize"
    )
    workspace = Path(str(receipt.get("workspace_root"))).absolute()
    recomputed = compute_overall_acceptance_candidate(workspace)
    observed_candidate = {
        key: value
        for key, value in receipt.items()
        if key not in {"schema_version", "receipt_sha256", "supervision"}
    }
    if canonical_json(observed_candidate) != canonical_json(recomputed):
        raise IranResearchAcceptanceError("overall receipt 与 workspace create-only 链重算不一致")
    return {
        "verified": True,
        "receipt_path": str(path.resolve()),
        "receipt_sha256": supplied,
        "acceptance_state": "accepted",
        "acceptance_semantics": ACCEPTANCE_SEMANTICS,
        "research_only_not_production_or_causal_truth": True,
        "reconciliation_semantic_sha256": receipt.get("reconciliation", {}).get(
            "semantic_sha256"
        ),
        "four_category_scan_semantic_sha256": receipt.get("four_category_scan", {}).get(
            "semantic_sha256"
        ),
    }


__all__ = (
    "ACCEPTANCE_SEMANTICS",
    "ANCHOR_GATE_SCHEMA_VERSION",
    "DEFAULT_KILL_SECONDS",
    "DEFAULT_OBSERVATION_SECONDS",
    "DEFAULT_PARENT_EXIT_SECONDS",
    "DEFAULT_TERM_SECONDS",
    "EXPECTED_BOUNDARY_COUNT",
    "EXPECTED_SEGMENT_COUNT",
    "IranResearchAcceptanceError",
    "OVERALL_ACCEPTANCE_SCHEMA_VERSION",
    "SEGMENT_SCHEMA_VERSION",
    "WORKSPACE_GENESIS_SCHEMA_VERSION",
    "acceptance_workspace_status",
    "build_successful_supervision_evidence",
    "compute_anchor_verification_candidate",
    "compute_overall_acceptance_candidate",
    "compute_reconciliation_segment_candidate",
    "initialize_acceptance_workspace",
    "publish_anchor_verification_gate",
    "publish_overall_research_acceptance",
    "publish_reconciliation_segment",
    "verify_overall_research_acceptance",
)
