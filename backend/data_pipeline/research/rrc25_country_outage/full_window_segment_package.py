"""从 sealed segment 业务产物构造完整 v2 研究包的确定性内容计划。

本模块只产生值，不创建目录、不写文件、不读取 MRT、数据库或
``record_observations``。调用方先用 :mod:`full_window_segment_product`
完成 sealed workspace 适配，再把适配结果和纯业务产物交给本模块。计划中的
每一项只有四种物化方式：

* ``canonical_json``：已编码为末尾单换行的规范 JSON；
* ``canonical_jsonl_gzip``：``mtime=0``、空 filename 的规范 JSONL gzip；
* ``bytes``：调用方已经给出的确定性字节；
* ``verified_copy_source``：适配器已经核验过的 create-only 来源文件。

计划覆盖 workspace 根 receipt、全部 segment、seed/raw-ledger ancestry、完整
业务 frozen/data/evidence/quality/reconciliation/中文报告，以及
``package-manifest.json`` 和 ``SHA256SUMS``。业务语义 core 与 finalization
segment core 分开保存；旧 ``semantic_core_sha256`` 只兼容映射到 segment
core，避免把“业务结果相同”和“同一逐槽最终化链”混为一个结论。
"""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, Optional, Sequence, Tuple

from .file_artifacts import canonical_json
from .full_window_finalize import FullWindowBusinessOutputs
from .full_window_segment_product import SegmentProductInputs
from . import full_window_finalize as _finalizer
from . import full_window_finalize_workspace as _workspace
from .package_manifest import build_package_manifest


DEFAULT_MAX_PROJECTED_BYTES = 5_000_000_000
PACKAGE_PLAN_SCHEMA_VERSION = "rrc25-full-window-segment-package-plan/v1"
PACKAGE_PLAN_VERIFICATION_SCHEMA_VERSION = (
    "rrc25-full-window-segment-package-plan-verification/v1"
)

MATERIALIZATION_CANONICAL_JSON = "canonical_json"
MATERIALIZATION_CANONICAL_JSONL_GZIP = "canonical_jsonl_gzip"
MATERIALIZATION_BYTES = "bytes"
MATERIALIZATION_VERIFIED_COPY_SOURCE = "verified_copy_source"

_MATERIALIZATIONS = frozenset(
    {
        MATERIALIZATION_CANONICAL_JSON,
        MATERIALIZATION_CANONICAL_JSONL_GZIP,
        MATERIALIZATION_BYTES,
        MATERIALIZATION_VERIFIED_COPY_SOURCE,
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# 这里复制的是最终包的稳定公开人口，不从 acceptance 模块反向导入私有常量，
# 以免 assembly 与总验收产生循环依赖。
REQUIRED_BUSINESS_OBJECT_PATHS = frozenset(
    {
        "metadata/finalization.json",
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
        "reconciliation.json",
        "quality-and-accounting.json",
    }
)
REQUIRED_BUSINESS_SEQUENCE_PATHS = frozenset(
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
REQUIRED_BUSINESS_BYTE_PATHS = frozenset(
    {"报告/RRC25伊朗国家路由中断事件复算与对账报告.md"}
)
REQUIRED_BUSINESS_PATHS = frozenset(
    {
        *REQUIRED_BUSINESS_OBJECT_PATHS,
        *REQUIRED_BUSINESS_SEQUENCE_PATHS,
        *REQUIRED_BUSINESS_BYTE_PATHS,
    }
)
REQUIRED_FIXED_PACKAGE_PATHS = frozenset(
    {
        "GENESIS",
        "TERMINAL",
        "DEEP-VERIFICATION",
        "segments/index.json",
        "package-manifest.json",
        "SHA256SUMS",
    }
)


class FullWindowSegmentPackageError(ValueError):
    """完整 v2 package 计划不能按来源、双 core 或资源边界闭合。"""


@dataclass(frozen=True)
class PackageContentPlanItem:
    """一个确定性包文件；generated 与 copy source 必须二选一。"""

    relative_path: str
    kind: str
    materialization: str
    sha256: str
    size_bytes: int
    record_count: int
    included_in_manifest: bool
    generated_bytes: Optional[bytes]
    source_path: Optional[str]

    def content_ref(self) -> Mapping[str, Any]:
        """返回 package manifest 使用的严格五字段 ref。"""

        return {
            "kind": self.kind,
            "path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "record_count": self.record_count,
        }


@dataclass(frozen=True)
class FullWindowSegmentPackagePlan:
    """完整 v2 包的纯值计划及排他资源上界。"""

    schema_version: str
    business_semantic_core_sha256: str
    finalization_segment_core_sha256: str
    projected_regular_bytes: int
    maximum_projected_bytes: int
    database_write_operations: int
    record_observation_reads: int
    real_mrt_raw_bytes_read: int
    manifest: Mapping[str, Any]
    items: Tuple[PackageContentPlanItem, ...]


def _fail(message: str) -> None:
    raise FullWindowSegmentPackageError(message)


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(f"{field} 必须是 64 位小写 SHA256")
    return value


def _nonnegative(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"{field} 必须是非负整数")
    return value


def _safe_relative(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{field} 必须是非空相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        _fail(f"{field} 不是安全相对路径")
    return path.as_posix()


def _validated_limit(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > DEFAULT_MAX_PROJECTED_BYTES
    ):
        _fail("maximum_projected_bytes 必须位于 (0, 十进制 5GB]")
    return value


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    if not isinstance(value, Mapping):
        _fail("canonical JSON 顶层必须是对象")
    return (canonical_json(dict(value)) + "\n").encode("utf-8")


def _canonical_jsonl_gzip_bytes(
    rows: Sequence[Mapping[str, Any]],
) -> Tuple[bytes, int]:
    if isinstance(rows, (str, bytes, Mapping)) or not isinstance(
        rows, Sequence
    ):
        _fail("canonical JSONL gzip 输入必须是有序对象序列")
    buffer = io.BytesIO()
    count = 0
    with gzip.GzipFile(
        filename="",
        mode="wb",
        compresslevel=9,
        fileobj=buffer,
        mtime=0,
    ) as compressed:
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                _fail(f"canonical JSONL gzip rows[{index}] 必须是对象")
            compressed.write(
                (canonical_json(dict(row)) + "\n").encode("utf-8")
            )
            count += 1
    return buffer.getvalue(), count


def _generated_item(
    *,
    relative_path: str,
    kind: str,
    materialization: str,
    payload: bytes,
    record_count: int,
    included_in_manifest: bool = True,
) -> PackageContentPlanItem:
    relative = _safe_relative(relative_path, "generated item path")
    if materialization not in {
        MATERIALIZATION_CANONICAL_JSON,
        MATERIALIZATION_CANONICAL_JSONL_GZIP,
        MATERIALIZATION_BYTES,
    }:
        _fail("generated item materialization 非法")
    if not isinstance(kind, str) or not kind:
        _fail("generated item kind 不能为空")
    if not isinstance(payload, bytes):
        _fail("generated item payload 必须是 bytes")
    count = _nonnegative(record_count, "generated item record_count")
    return PackageContentPlanItem(
        relative_path=relative,
        kind=kind,
        materialization=materialization,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        record_count=count,
        included_in_manifest=included_in_manifest,
        generated_bytes=payload,
        source_path=None,
    )


def _copy_item(raw: Mapping[str, Any]) -> PackageContentPlanItem:
    required = {
        "source_path",
        "output_relative_path",
        "sha256",
        "size_bytes",
        "record_count",
        "kind",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        _fail("verified copy source 字段必须精确闭合")
    source = raw.get("source_path")
    if not isinstance(source, str) or not Path(source).is_absolute():
        _fail("verified copy source_path 必须是绝对路径")
    relative = _safe_relative(
        raw.get("output_relative_path"), "verified copy output path"
    )
    # adapter 已在构造 SegmentProductInputs 时核验这些来源；计划阶段不再次
    # 打开大 segment，更不能触碰 record_observations。
    if (
        "record_observations" in relative
        or "record_observations" in source
    ):
        _fail("package copy source 不得包含 record_observations")
    kind = raw.get("kind")
    if not isinstance(kind, str) or not kind:
        _fail("verified copy kind 不能为空")
    return PackageContentPlanItem(
        relative_path=relative,
        kind=kind,
        materialization=MATERIALIZATION_VERIFIED_COPY_SOURCE,
        sha256=_sha(raw.get("sha256"), "verified copy sha256"),
        size_bytes=_nonnegative(
            raw.get("size_bytes"), "verified copy size_bytes"
        ),
        record_count=_nonnegative(
            raw.get("record_count"), "verified copy record_count"
        ),
        included_in_manifest=True,
        generated_bytes=None,
        source_path=source,
    )


def _read_verified_receipt_copy(
    source: PackageContentPlanItem,
    *,
    maximum_bytes: int = 64 * 1024 * 1024,
) -> Mapping[str, Any]:
    """只读已验证的小 receipt；拒绝符号链接、漂移及非规范 JSON。

    这是构造 segment core/deep-chain 所需的唯一文件读取。调用者只会传入
    ``TERMINAL`` 和 ``DEEP-VERIFICATION``，不会传入 segment payload、MRT
    或 observation。
    """

    if (
        source.materialization != MATERIALIZATION_VERIFIED_COPY_SOURCE
        or source.source_path is None
        or source.size_bytes > maximum_bytes
    ):
        _fail("workspace receipt copy source 非法或超过 64MiB")
    path = Path(source.source_path)
    try:
        initial = path.lstat()
    except OSError as error:
        raise FullWindowSegmentPackageError(
            f"workspace receipt 不可读：{source.relative_path}"
        ) from error
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        _fail("workspace receipt 必须是非符号链接普通文件")
    if initial.st_size != source.size_bytes:
        _fail("workspace receipt size 与已验证 copy ref 漂移")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    chunks = []
    total = 0
    try:
        before = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(
            getattr(initial, field) != getattr(before, field)
            for field in identity
        ):
            _fail("workspace receipt 在打开前发生变化")
        while True:
            block = os.read(descriptor, 64 * 1024)
            if not block:
                break
            total += len(block)
            if total > maximum_bytes:
                _fail("workspace receipt 超过 64MiB")
            chunks.append(block)
        after = os.fstat(descriptor)
        if any(
            getattr(before, field) != getattr(after, field)
            for field in identity
        ):
            _fail("workspace receipt 在读取期间发生变化")
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if (
        len(raw) != source.size_bytes
        or hashlib.sha256(raw).hexdigest() != source.sha256
    ):
        _fail("workspace receipt SHA/size 与已验证 copy ref 漂移")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FullWindowSegmentPackageError(
            "workspace receipt 不是严格 UTF-8 JSON"
        ) from error
    if (
        not isinstance(value, Mapping)
        or _canonical_json_bytes(value) != raw
    ):
        _fail("workspace receipt 不是末尾单换行的规范 JSON")
    return dict(value)


def _add_without_collision(
    items: dict[str, PackageContentPlanItem],
    item: PackageContentPlanItem,
) -> None:
    path = item.relative_path
    if path in items:
        _fail(f"package content path 冲突：{path}")
    # 不仅拒绝同名，也拒绝一个文件占用另一个文件的父目录。
    for existing in items:
        if existing.startswith(path + "/") or path.startswith(existing + "/"):
            _fail(
                f"package content 文件/目录前缀冲突：{existing} 与 {path}"
            )
    items[path] = item


def _relative_ref(item: PackageContentPlanItem) -> Mapping[str, Any]:
    return {
        "path": item.relative_path,
        "sha256": item.sha256,
        "size_bytes": item.size_bytes,
    }


def _copy_ref(item: PackageContentPlanItem) -> Mapping[str, Any]:
    return _relative_ref(item)


def _workspace_copy_index(
    copy_items: Sequence[PackageContentPlanItem],
) -> Mapping[str, PackageContentPlanItem]:
    result: dict[str, PackageContentPlanItem] = {}
    for item in copy_items:
        if item.relative_path in result:
            _fail(f"verified copy output path 重复：{item.relative_path}")
        result[item.relative_path] = item
    return result


def _require_copy_refs(
    copy_by_path: Mapping[str, PackageContentPlanItem],
    refs: Any,
    *,
    field: str,
    expected_kind: str,
    expected_count: int,
) -> Tuple[Mapping[str, Any], ...]:
    if not isinstance(refs, list) or len(refs) != expected_count:
        _fail(f"{field} 未逐槽闭合")
    normalized = []
    seen = set()
    for index, raw in enumerate(refs):
        if not isinstance(raw, Mapping) or set(raw) != {
            "path",
            "sha256",
            "size_bytes",
        }:
            _fail(f"{field}[{index}] 字段不闭合")
        relative = _safe_relative(raw.get("path"), f"{field}[{index}].path")
        ref = {
            "path": relative,
            "sha256": _sha(raw.get("sha256"), f"{field}[{index}].sha256"),
            "size_bytes": _nonnegative(
                raw.get("size_bytes"), f"{field}[{index}].size_bytes"
            ),
        }
        item = copy_by_path.get(relative)
        if (
            item is None
            or item.kind != expected_kind
            or _copy_ref(item) != ref
            or relative in seen
        ):
            _fail(f"{field}[{index}] 未闭合到唯一 verified copy source")
        seen.add(relative)
        normalized.append(ref)
    return tuple(normalized)


def _business_mapping(
    files: Mapping[str, Any],
    path: str,
    *,
    field: str,
) -> Tuple[str, Mapping[str, Any]]:
    value = files.get(path)
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or not isinstance(value[0], str)
        or not value[0]
        or not isinstance(value[1], Mapping)
    ):
        _fail(f"{field} {path} 不是 (kind, Mapping)")
    return value[0], dict(value[1])


def _merged_finalization(
    *,
    business_value: Mapping[str, Any],
    business_core: str,
    segment_core: str,
    terminal: Mapping[str, Any],
    terminal_ref: Mapping[str, Any],
    deep_ref: Mapping[str, Any],
    index_ref: Mapping[str, Any],
) -> Mapping[str, Any]:
    base = dict(business_value)
    business_schema = base.pop("schema_version", None)
    if (
        business_schema != _finalizer.FINALIZATION_SCHEMA_VERSION
        or base.get("semantic_core_sha256") != business_core
    ):
        _fail("业务 metadata/finalization 未绑定业务 semantic core")
    base.update(
        {
            "business_finalization_schema_version": business_schema,
            # v2 receipt-only verifier 的 legacy 字段固定指向 segment core。
            "semantic_core_sha256": segment_core,
            "business_semantic_core_sha256": business_core,
            "finalization_segment_core_sha256": segment_core,
            "reproduction_state": (
                "pending_second_independent_package_assembly"
            ),
            "acceptance_state": "not_accepted",
            "reproduction_scope": (
                "independent_package_assembly_from_same_verified_"
                "finalization_segments"
            ),
            "raw_replay_reproduction": "not_performed_by_user_choice",
            "segment_index_ref": dict(index_ref),
            "terminal_ref": dict(terminal_ref),
            "deep_verification_ref": dict(deep_ref),
            "bindings": dict(terminal["bindings"]),
            "code_identity_sha256": terminal["code_identity_sha256"],
            "record_observation_reads_during_assembly": 0,
            "database_write_operations": 0,
        }
    )
    return _workspace._fingerprinted(
        _workspace.WORKSPACE_ASSEMBLY_METADATA_SCHEMA,
        base,
    )


def _merged_quality(
    *,
    business_value: Mapping[str, Any],
    business_core: str,
    segment_core: str,
) -> Mapping[str, Any]:
    value = dict(business_value)
    if (
        value.get("schema_version")
        != "rrc25-full-window-quality-and-accounting/v1"
        or value.get("acceptance_state") != "not_accepted"
    ):
        _fail("业务 quality-and-accounting 身份或验收状态非法")
    value.update(
        {
            "business_semantic_core_sha256": business_core,
            "finalization_segment_core_sha256": segment_core,
        }
    )
    return value


def _segment_index(
    *,
    business_core: str,
    segment_core: str,
    terminal: Mapping[str, Any],
    terminal_ref: Mapping[str, Any],
    deep_ref: Mapping[str, Any],
    segment_receipt_refs: Sequence[Mapping[str, Any]],
    segment_payload_refs: Sequence[Mapping[str, Any]],
    deep_segment_receipt_refs: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    return _workspace._fingerprinted(
        _workspace.WORKSPACE_ASSEMBLY_INDEX_SCHEMA,
        {
            "assembly_semantics": (
                "assemble_only_from_sealed_finalization_segments_"
                "no_record_observation_read"
            ),
            "terminal_ref": dict(terminal_ref),
            "deep_verification_ref": dict(deep_ref),
            "segment_receipt_refs": [
                dict(ref) for ref in segment_receipt_refs
            ],
            "segment_payload_refs": [
                dict(ref) for ref in segment_payload_refs
            ],
            "deep_segment_receipt_refs": [
                dict(ref) for ref in deep_segment_receipt_refs
            ],
            "deep_chain_sha256": terminal["deep_chain_sha256"],
            "semantic_core_sha256": segment_core,
            "business_semantic_core_sha256": business_core,
            "finalization_segment_core_sha256": segment_core,
            "bindings": dict(terminal["bindings"]),
            "code_identity_sha256": terminal["code_identity_sha256"],
            "record_observation_reads_during_assembly": 0,
            "database_write_operations": 0,
        },
    )


def _validate_product_and_business(
    product: SegmentProductInputs,
    business: FullWindowBusinessOutputs,
) -> None:
    if not isinstance(product, SegmentProductInputs):
        _fail("product 必须是 SegmentProductInputs")
    if not isinstance(business, FullWindowBusinessOutputs):
        _fail("business 必须是 FullWindowBusinessOutputs")
    verification = product.verification
    if (
        not isinstance(verification, Mapping)
        or verification.get("verified") is not True
        or verification.get("record_observation_reread_count") != 0
        or verification.get("real_mrt_raw_bytes_read") != 0
        or verification.get("database_write_operations") != 0
    ):
        _fail("SegmentProductInputs 未闭合 0 observation/MRT/DB 门")
    workspace_verification = verification.get(
        "workspace_receipt_only_verification"
    )
    resource = (
        workspace_verification.get("resource_accounting")
        if isinstance(workspace_verification, Mapping)
        else None
    )
    if (
        not isinstance(workspace_verification, Mapping)
        or workspace_verification.get("verified") is not True
        or workspace_verification.get("record_observation_reread_count") != 0
        or not isinstance(resource, Mapping)
        or resource.get("database_write_operations") != 0
    ):
        _fail("workspace receipt-only verification 未闭合")
    business_core = _sha(
        business.semantic_core_sha256, "business semantic core"
    )
    if _finalizer._canonical_hash(business.semantic_core) != business_core:
        _fail("FullWindowBusinessOutputs semantic core 指纹不闭合")
    if not isinstance(business.object_files, Mapping):
        _fail("business.object_files 必须是 Mapping")
    if not isinstance(business.sequence_files, Mapping):
        _fail("business.sequence_files 必须是 Mapping")
    if not isinstance(business.byte_files, Mapping):
        _fail("business.byte_files 必须是 Mapping")
    missing = (
        REQUIRED_BUSINESS_OBJECT_PATHS - set(business.object_files),
        REQUIRED_BUSINESS_SEQUENCE_PATHS - set(business.sequence_files),
        REQUIRED_BUSINESS_BYTE_PATHS - set(business.byte_files),
    )
    if any(missing):
        _fail(
            "FullWindowBusinessOutputs 缺少完整 frozen/data/quality/"
            "reconciliation/中文报告人口"
        )


def build_full_window_segment_package_plan(
    product: SegmentProductInputs,
    business: FullWindowBusinessOutputs,
    *,
    maximum_projected_bytes: int = DEFAULT_MAX_PROJECTED_BYTES,
) -> FullWindowSegmentPackagePlan:
    """构造完整 v2 包的确定性纯值计划。

    函数不发布任何文件。除两个小型 workspace receipt 外，不打开 copy
    source；尤其不会读取 segment payload、MRT、数据库或 observation。
    """

    limit = _validated_limit(maximum_projected_bytes)
    _validate_product_and_business(product, business)
    business_core = business.semantic_core_sha256

    copy_items = tuple(_copy_item(row) for row in product.copy_sources)
    copy_by_path = _workspace_copy_index(copy_items)
    required_root = {"GENESIS", "TERMINAL", "DEEP-VERIFICATION"}
    if not required_root <= set(copy_by_path):
        _fail("verified copy source 缺少 GENESIS/TERMINAL/DEEP-VERIFICATION")
    if not any(path.startswith("seed/") for path in copy_by_path):
        _fail("verified copy source 缺少 seed ancestry")
    if not any(path.startswith("raw-ledger/") for path in copy_by_path):
        _fail("verified copy source 缺少 raw-ledger ancestry")

    terminal_item = copy_by_path["TERMINAL"]
    deep_item = copy_by_path["DEEP-VERIFICATION"]
    terminal = _workspace._verify_fingerprinted(
        _read_verified_receipt_copy(terminal_item),
        _workspace.WORKSPACE_TERMINAL_SCHEMA,
        "TERMINAL",
    )
    deep = _workspace._verify_fingerprinted(
        _read_verified_receipt_copy(deep_item),
        _workspace.WORKSPACE_DEEP_VERIFICATION_SCHEMA,
        "DEEP-VERIFICATION",
    )
    inputs = product.inputs
    total = product.verification.get("verified_segment_count")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total <= 0
        or terminal.get("sealed") is not True
        or terminal.get("completed_slots") != total
        or terminal.get("total_slots") != total
        or deep.get("verified_segment_count") != total
        or terminal.get("bindings") != inputs.bindings
        or deep.get("bindings") != inputs.bindings
        or terminal.get("code_identity_sha256")
        != inputs.code_identity.get("identity_sha256")
        or deep.get("code_identity_sha256")
        != inputs.code_identity.get("identity_sha256")
        or deep.get("terminal_ref") != _relative_ref(terminal_item)
        or deep.get("database_write_operations") != 0
    ):
        _fail("TERMINAL/DEEP 与 SegmentProductInputs 身份或资源门不闭合")

    segment_receipt_refs = _require_copy_refs(
        copy_by_path,
        terminal.get("segment_receipt_refs"),
        field="TERMINAL.segment_receipt_refs",
        expected_kind="finalization-segment-receipt",
        expected_count=total,
    )
    segment_payload_refs = _require_copy_refs(
        copy_by_path,
        terminal.get("segment_payload_refs"),
        field="TERMINAL.segment_payload_refs",
        expected_kind="finalization-segment-payload",
        expected_count=total,
    )
    deep_segment_receipt_refs = _require_copy_refs(
        copy_by_path,
        terminal.get("deep_segment_receipt_refs"),
        field="TERMINAL.deep_segment_receipt_refs",
        expected_kind="finalization-deep-segment-receipt",
        expected_count=total,
    )
    segment_core = _workspace._workspace_semantic_core(terminal, deep)
    _sha(segment_core, "finalization segment core")

    items: dict[str, PackageContentPlanItem] = {}
    for item in copy_items:
        _add_without_collision(items, item)

    # 先生成 index，metadata/finalization 才能内容寻址地引用它。
    index_value = _segment_index(
        business_core=business_core,
        segment_core=segment_core,
        terminal=terminal,
        terminal_ref=_relative_ref(terminal_item),
        deep_ref=_relative_ref(deep_item),
        segment_receipt_refs=segment_receipt_refs,
        segment_payload_refs=segment_payload_refs,
        deep_segment_receipt_refs=deep_segment_receipt_refs,
    )
    index_item = _generated_item(
        relative_path="segments/index.json",
        kind="segment-index",
        materialization=MATERIALIZATION_CANONICAL_JSON,
        payload=_canonical_json_bytes(index_value),
        record_count=1,
    )
    _add_without_collision(items, index_item)

    finalization_kind, business_finalization = _business_mapping(
        business.object_files,
        "metadata/finalization.json",
        field="business.object_files",
    )
    _quality_kind, business_quality = _business_mapping(
        business.object_files,
        "quality-and-accounting.json",
        field="business.object_files",
    )
    finalization_value = _merged_finalization(
        business_value=business_finalization,
        business_core=business_core,
        segment_core=segment_core,
        terminal=terminal,
        terminal_ref=_relative_ref(terminal_item),
        deep_ref=_relative_ref(deep_item),
        index_ref=_relative_ref(index_item),
    )
    quality_value = _merged_quality(
        business_value=business_quality,
        business_core=business_core,
        segment_core=segment_core,
    )

    for raw_path, raw_value in sorted(business.object_files.items()):
        path = _safe_relative(raw_path, "business object path")
        if path in {
            "metadata/finalization.json",
            "quality-and-accounting.json",
        }:
            continue
        kind, value = _business_mapping(
            business.object_files, path, field="business.object_files"
        )
        _add_without_collision(
            items,
            _generated_item(
                relative_path=path,
                kind=kind,
                materialization=MATERIALIZATION_CANONICAL_JSON,
                payload=_canonical_json_bytes(value),
                record_count=1,
            ),
        )
    _add_without_collision(
        items,
        _generated_item(
            relative_path="metadata/finalization.json",
            kind=finalization_kind,
            materialization=MATERIALIZATION_CANONICAL_JSON,
            payload=_canonical_json_bytes(finalization_value),
            record_count=1,
        ),
    )
    _add_without_collision(
        items,
        _generated_item(
            relative_path="quality-and-accounting.json",
            kind="quality",
            materialization=MATERIALIZATION_CANONICAL_JSON,
            payload=_canonical_json_bytes(quality_value),
            record_count=1,
        ),
    )

    for raw_path, raw_value in sorted(business.sequence_files.items()):
        path = _safe_relative(raw_path, "business sequence path")
        if (
            not isinstance(raw_value, tuple)
            or len(raw_value) != 2
            or not isinstance(raw_value[0], str)
            or not raw_value[0]
            or isinstance(raw_value[1], (str, bytes, Mapping))
            or not isinstance(raw_value[1], Sequence)
        ):
            _fail(f"business.sequence_files {path} 结构非法")
        payload, count = _canonical_jsonl_gzip_bytes(raw_value[1])
        _add_without_collision(
            items,
            _generated_item(
                relative_path=path,
                kind=raw_value[0],
                materialization=MATERIALIZATION_CANONICAL_JSONL_GZIP,
                payload=payload,
                record_count=count,
            ),
        )

    for raw_path, raw_value in sorted(business.byte_files.items()):
        path = _safe_relative(raw_path, "business byte path")
        if (
            not isinstance(raw_value, tuple)
            or len(raw_value) != 2
            or not isinstance(raw_value[0], str)
            or not raw_value[0]
            or not isinstance(raw_value[1], bytes)
        ):
            _fail(f"business.byte_files {path} 结构非法")
        _add_without_collision(
            items,
            _generated_item(
                relative_path=path,
                kind=raw_value[0],
                materialization=MATERIALIZATION_BYTES,
                payload=raw_value[1],
                record_count=1,
            ),
        )

    business_paths = set(items) & REQUIRED_BUSINESS_PATHS
    if business_paths != REQUIRED_BUSINESS_PATHS:
        _fail("完整业务 package 路径人口未闭合")
    manifest_contents = [
        item.content_ref()
        for item in items.values()
        if item.included_in_manifest
    ]
    manifest = build_package_manifest(
        run_id=str(inputs.journal.frozen_head["run_id"]),
        study_id=str(inputs.profile["study_id"]),
        incident_ref=str(inputs.source_fact.incident["detail_reference"]),
        execution_mode="full_profile",
        acceptance_state="not_accepted",
        bindings=inputs.bindings,
        contents=manifest_contents,
    )
    manifest_item = _generated_item(
        relative_path="package-manifest.json",
        kind="package-manifest",
        materialization=MATERIALIZATION_CANONICAL_JSON,
        payload=_canonical_json_bytes(manifest),
        record_count=1,
        included_in_manifest=False,
    )
    _add_without_collision(items, manifest_item)
    sums_rows = [
        (item.sha256, item.relative_path)
        for item in items.values()
        if item.included_in_manifest
    ]
    sums_rows.append((manifest_item.sha256, manifest_item.relative_path))
    sums_bytes = "".join(
        f"{digest}  {path}\n"
        for digest, path in sorted(sums_rows, key=lambda row: row[1])
    ).encode("utf-8")
    _add_without_collision(
        items,
        _generated_item(
            relative_path="SHA256SUMS",
            kind="sha256sums",
            materialization=MATERIALIZATION_BYTES,
            payload=sums_bytes,
            record_count=len(sums_rows),
            included_in_manifest=False,
        ),
    )

    ordered = tuple(items[path] for path in sorted(items))
    projected = sum(item.size_bytes for item in ordered)
    if projected >= limit or projected >= DEFAULT_MAX_PROJECTED_BYTES:
        _fail(
            "完整 v2 package 预计 regular bytes 达到十进制 5GB 排他边界"
        )
    plan = FullWindowSegmentPackagePlan(
        schema_version=PACKAGE_PLAN_SCHEMA_VERSION,
        business_semantic_core_sha256=business_core,
        finalization_segment_core_sha256=segment_core,
        projected_regular_bytes=projected,
        maximum_projected_bytes=limit,
        database_write_operations=0,
        record_observation_reads=0,
        real_mrt_raw_bytes_read=0,
        manifest=manifest,
        items=ordered,
    )
    verify_full_window_segment_package_plan(plan)
    return plan


def _json_item(
    by_path: Mapping[str, PackageContentPlanItem],
    path: str,
) -> Mapping[str, Any]:
    item = by_path.get(path)
    if (
        item is None
        or item.generated_bytes is None
        or item.materialization != MATERIALIZATION_CANONICAL_JSON
    ):
        _fail(f"plan 缺少 generated canonical JSON：{path}")
    try:
        value = json.loads(item.generated_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FullWindowSegmentPackageError(
            f"plan generated JSON 非法：{path}"
        ) from error
    if (
        not isinstance(value, Mapping)
        or _canonical_json_bytes(value) != item.generated_bytes
    ):
        _fail(f"plan generated JSON 不是规范字节：{path}")
    return dict(value)


def verify_full_window_segment_package_plan(
    plan: FullWindowSegmentPackagePlan,
) -> Mapping[str, Any]:
    """纯值核验完整计划；不打开任何 copy source。"""

    if not isinstance(plan, FullWindowSegmentPackagePlan):
        _fail("plan 必须是 FullWindowSegmentPackagePlan")
    if plan.schema_version != PACKAGE_PLAN_SCHEMA_VERSION:
        _fail("plan schema_version 非法")
    business_core = _sha(
        plan.business_semantic_core_sha256, "plan business core"
    )
    segment_core = _sha(
        plan.finalization_segment_core_sha256, "plan segment core"
    )
    limit = _validated_limit(plan.maximum_projected_bytes)
    if (
        plan.database_write_operations != 0
        or plan.record_observation_reads != 0
        or plan.real_mrt_raw_bytes_read != 0
    ):
        _fail("plan 必须闭合 DB=0、observation reads=0、MRT raw reads=0")
    by_path: dict[str, PackageContentPlanItem] = {}
    for item in plan.items:
        if not isinstance(item, PackageContentPlanItem):
            _fail("plan.items 必须全部是 PackageContentPlanItem")
        if item.materialization not in _MATERIALIZATIONS:
            _fail(f"plan item materialization 非法：{item.relative_path}")
        _safe_relative(item.relative_path, "plan item path")
        _sha(item.sha256, "plan item sha256")
        _nonnegative(item.size_bytes, "plan item size_bytes")
        _nonnegative(item.record_count, "plan item record_count")
        if not isinstance(item.kind, str) or not item.kind:
            _fail("plan item kind 不能为空")
        generated = item.materialization != MATERIALIZATION_VERIFIED_COPY_SOURCE
        if generated:
            if item.generated_bytes is None or item.source_path is not None:
                _fail("generated plan item 必须只有 generated_bytes")
            if (
                len(item.generated_bytes) != item.size_bytes
                or hashlib.sha256(item.generated_bytes).hexdigest()
                != item.sha256
            ):
                _fail(f"generated plan item SHA/size 漂移：{item.relative_path}")
        else:
            if (
                item.generated_bytes is not None
                or not isinstance(item.source_path, str)
                or not Path(item.source_path).is_absolute()
                or "record_observations" in item.source_path
                or "record_observations" in item.relative_path
            ):
                _fail("verified copy plan item 来源非法")
        _add_without_collision(by_path, item)
    if tuple(by_path) != tuple(sorted(by_path)):
        _fail("plan.items 必须按 package-relative path 稳定排序")
    required = REQUIRED_FIXED_PACKAGE_PATHS | REQUIRED_BUSINESS_PATHS
    missing = sorted(required - set(by_path))
    if missing:
        _fail(f"plan 缺少完整固定/业务路径：{missing}")
    if not any(path.startswith("seed/") for path in by_path):
        _fail("plan 缺少 seed ancestry")
    if not any(path.startswith("raw-ledger/") for path in by_path):
        _fail("plan 缺少 raw-ledger ancestry")
    for prefix in (
        "segments/receipts/",
        "segments/payloads/",
        "segments/deep-receipts/",
    ):
        if not any(path.startswith(prefix) for path in by_path):
            _fail(f"plan 缺少逐槽 segment 人口：{prefix}")

    projected = sum(item.size_bytes for item in plan.items)
    if (
        projected != plan.projected_regular_bytes
        or projected >= limit
        or projected >= DEFAULT_MAX_PROJECTED_BYTES
    ):
        _fail("plan projected_regular_bytes 或 5GB 排他门不闭合")
    manifest_item = by_path["package-manifest.json"]
    manifest = _json_item(by_path, "package-manifest.json")
    rebuilt = build_package_manifest(
        run_id=manifest.get("run_id"),
        study_id=manifest.get("study_id"),
        incident_ref=manifest.get("incident_ref"),
        execution_mode=manifest.get("execution_mode"),
        acceptance_state=manifest.get("acceptance_state"),
        bindings=manifest.get("bindings"),
        contents=manifest.get("contents"),
    )
    expected_refs = tuple(
        sorted(
            (
                dict(item.content_ref())
                for item in plan.items
                if item.included_in_manifest
            ),
            key=lambda row: (str(row["kind"]), str(row["path"])),
        )
    )
    if (
        manifest != rebuilt
        or dict(plan.manifest) != rebuilt
        or tuple(rebuilt["contents"]) != expected_refs
        or manifest_item.included_in_manifest
    ):
        _fail("plan manifest 与内容人口不闭合")
    sums_item = by_path["SHA256SUMS"]
    expected_sums_rows = [
        (item.sha256, item.relative_path)
        for item in plan.items
        if item.included_in_manifest
    ]
    expected_sums_rows.append(
        (manifest_item.sha256, manifest_item.relative_path)
    )
    expected_sums = "".join(
        f"{digest}  {path}\n"
        for digest, path in sorted(
            expected_sums_rows, key=lambda row: row[1]
        )
    ).encode("utf-8")
    if (
        sums_item.materialization != MATERIALIZATION_BYTES
        or sums_item.generated_bytes != expected_sums
        or sums_item.included_in_manifest
    ):
        _fail("plan SHA256SUMS 与内容人口不闭合")

    finalization = _workspace._verify_fingerprinted(
        _json_item(by_path, "metadata/finalization.json"),
        _workspace.WORKSPACE_ASSEMBLY_METADATA_SCHEMA,
        "plan finalization",
    )
    index = _workspace._verify_fingerprinted(
        _json_item(by_path, "segments/index.json"),
        _workspace.WORKSPACE_ASSEMBLY_INDEX_SCHEMA,
        "plan segment index",
    )
    quality = _json_item(by_path, "quality-and-accounting.json")
    if (
        finalization.get("semantic_core_sha256") != segment_core
        or finalization.get("business_semantic_core_sha256") != business_core
        or finalization.get("finalization_segment_core_sha256")
        != segment_core
        or finalization.get("record_observation_reads_during_assembly") != 0
        or finalization.get("database_write_operations") != 0
        or index.get("semantic_core_sha256") != segment_core
        or index.get("business_semantic_core_sha256") != business_core
        or index.get("finalization_segment_core_sha256") != segment_core
        or index.get("record_observation_reads_during_assembly") != 0
        or index.get("database_write_operations") != 0
        or quality.get("business_semantic_core_sha256") != business_core
        or quality.get("finalization_segment_core_sha256") != segment_core
    ):
        _fail("metadata/index/quality 未全链绑定双 core 或 0 资源门")
    if (
        finalization.get("segment_index_ref")
        != _relative_ref(by_path["segments/index.json"])
        or finalization.get("terminal_ref")
        != _relative_ref(by_path["TERMINAL"])
        or finalization.get("deep_verification_ref")
        != _relative_ref(by_path["DEEP-VERIFICATION"])
        or index.get("terminal_ref") != _relative_ref(by_path["TERMINAL"])
        or index.get("deep_verification_ref")
        != _relative_ref(by_path["DEEP-VERIFICATION"])
    ):
        _fail("metadata/index 未内容寻址绑定 package receipt")
    return {
        "schema_version": PACKAGE_PLAN_VERIFICATION_SCHEMA_VERSION,
        "verified": True,
        "business_semantic_core_sha256": business_core,
        "finalization_segment_core_sha256": segment_core,
        "projected_regular_bytes": projected,
        "content_item_count": len(plan.items),
        "manifest_content_count": len(expected_refs),
        "database_write_operations": 0,
        "record_observation_reads": 0,
        "real_mrt_raw_bytes_read": 0,
    }


__all__ = (
    "DEFAULT_MAX_PROJECTED_BYTES",
    "FullWindowSegmentPackageError",
    "FullWindowSegmentPackagePlan",
    "MATERIALIZATION_BYTES",
    "MATERIALIZATION_CANONICAL_JSON",
    "MATERIALIZATION_CANONICAL_JSONL_GZIP",
    "MATERIALIZATION_VERIFIED_COPY_SOURCE",
    "PACKAGE_PLAN_SCHEMA_VERSION",
    "PACKAGE_PLAN_VERIFICATION_SCHEMA_VERSION",
    "PackageContentPlanItem",
    "REQUIRED_BUSINESS_BYTE_PATHS",
    "REQUIRED_BUSINESS_OBJECT_PATHS",
    "REQUIRED_BUSINESS_PATHS",
    "REQUIRED_BUSINESS_SEQUENCE_PATHS",
    "REQUIRED_FIXED_PACKAGE_PATHS",
    "build_full_window_segment_package_plan",
    "verify_full_window_segment_package_plan",
)
