"""RRC25 国家中断研究的纯函数资源与只读门禁。

本模块只评估由调用方提供的计划或观测证据；它不读取文件系统、
不打开数据库，也不执行任何写操作。门禁对无法分类的写入目标失败关闭。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import posixpath
import re
from pathlib import PurePosixPath
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlsplit


SCHEMA_VERSION = "rrc25_country_outage_resource_gate_v1"

# 审批边界使用十进制 GB，不隐式换算为 GiB。
DEFAULT_MAX_NEW_RAW_READ_BYTES = 50_000_000_000
DEFAULT_MAX_TEMPORARY_BYTES = 5_000_000_000
DEFAULT_HARD_RUNTIME_SECONDS = 600.0
DEFAULT_SOFT_RUNTIME_SECONDS = 540.0

_ALLOWED_PHASES = frozenset({"estimated", "observed"})
_ALLOWED_TARGET_KINDS = frozenset(
    {
        "artifact",
        "checkpoint",
        "database",
        "directory",
        "file",
        "production",
        "temporary",
        "unknown",
    }
)
_DATABASE_SCHEMES = frozenset(
    {
        "cassandra",
        "clickhouse",
        "cockroachdb",
        "duckdb",
        "mariadb",
        "mongodb",
        "mssql",
        "mysql",
        "oracle",
        "postgres",
        "postgresql",
        "redis",
        "sqlite",
    }
)
_DATABASE_SUFFIXES = (".db", ".duckdb", ".sqlite", ".sqlite3")
_DATABASE_DSN_PATTERN = re.compile(
    r"(?:^|\s)(?:database|dbname|driver)\s*=", re.IGNORECASE
)


class ResourceGateInputError(ValueError):
    """门禁证据结构无效。

    这与“证据有效但超出边界”不同：输入结构错误时调用方必须
    修正证据，不能将其当作已获批准。
    """


def _non_negative_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResourceGateInputError(f"{name} 必须是非负整数")
    return value


def _positive_integer(name: str, value: object) -> int:
    result = _non_negative_integer(name, value)
    if result == 0:
        raise ResourceGateInputError(f"{name} 必须大于 0")
    return result


def _non_negative_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResourceGateInputError(f"{name} 必须是非负有限数")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ResourceGateInputError(f"{name} 必须是非负有限数")
    return result


def _positive_number(name: str, value: object) -> float:
    result = _non_negative_number(name, value)
    if result == 0:
        raise ResourceGateInputError(f"{name} 必须大于 0")
    return result


@dataclass(frozen=True)
class ResourceLimits:
    """可由已验证研究 Profile 显式覆盖的门禁边界。"""

    max_new_raw_read_bytes: int = DEFAULT_MAX_NEW_RAW_READ_BYTES
    max_temporary_bytes: int = DEFAULT_MAX_TEMPORARY_BYTES
    max_worker_runtime_seconds: float = DEFAULT_HARD_RUNTIME_SECONDS
    worker_soft_stop_seconds: float = DEFAULT_SOFT_RUNTIME_SECONDS
    database_writes: str = "forbidden"
    output_storage: str = "filesystem_only"

    def __post_init__(self) -> None:
        _positive_integer("max_new_raw_read_bytes", self.max_new_raw_read_bytes)
        _positive_integer("max_temporary_bytes", self.max_temporary_bytes)
        hard = _positive_number(
            "max_worker_runtime_seconds", self.max_worker_runtime_seconds
        )
        soft = _positive_number(
            "worker_soft_stop_seconds", self.worker_soft_stop_seconds
        )
        if soft >= hard:
            raise ResourceGateInputError(
                "worker_soft_stop_seconds 必须严格小于 "
                "max_worker_runtime_seconds"
            )
        if self.database_writes != "forbidden":
            raise ResourceGateInputError("database_writes 必须为 forbidden")
        if self.output_storage != "filesystem_only":
            raise ResourceGateInputError("output_storage 必须为 filesystem_only")

    @property
    def hard_runtime_seconds(self) -> float:
        """门禁内部与早期调用方的只读兼容名。"""

        return self.max_worker_runtime_seconds

    @property
    def soft_runtime_seconds(self) -> float:
        """门禁内部与早期调用方的只读兼容名。"""

        return self.worker_soft_stop_seconds

    @classmethod
    def from_profile(cls, profile: Mapping[str, object]) -> "ResourceLimits":
        """从已验证 Profile 的 ``resource_limits`` 构造边界。

        本函数不替代 JSON Schema 验证；为避免配置漂移，它拒绝未知
        字段，也不对 GB/GiB 或分钟/秒做隐式换算。
        """

        if not isinstance(profile, Mapping):
            raise ResourceGateInputError("profile 必须是映射")
        raw_limits = profile.get("resource_limits", profile)
        if not isinstance(raw_limits, Mapping):
            raise ResourceGateInputError("resource_limits 必须是映射")
        allowed = {
            "max_new_raw_read_bytes",
            "max_temporary_bytes",
            "max_worker_runtime_seconds",
            "worker_soft_stop_seconds",
            "database_writes",
            "output_storage",
        }
        unknown = sorted(set(raw_limits) - allowed)
        if unknown:
            raise ResourceGateInputError(
                "resource_limits 包含未知字段: " + ", ".join(map(str, unknown))
            )
        return cls(**dict(raw_limits))

    def to_dict(self) -> dict[str, int | float | str]:
        return {
            "max_new_raw_read_bytes": self.max_new_raw_read_bytes,
            "max_temporary_bytes": self.max_temporary_bytes,
            "max_worker_runtime_seconds": self.max_worker_runtime_seconds,
            "worker_soft_stop_seconds": self.worker_soft_stop_seconds,
            "database_writes": self.database_writes,
            "output_storage": self.output_storage,
        }


@dataclass(frozen=True)
class WriteTarget:
    """对一个计划写入目标的纯数据声明。"""

    label: str
    location: str
    kind: str = "artifact"
    production: bool = False
    protected: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ResourceGateInputError("write target label 不能为空")
        if not isinstance(self.location, str) or not self.location.strip():
            raise ResourceGateInputError("write target location 不能为空")
        if self.kind not in _ALLOWED_TARGET_KINDS:
            raise ResourceGateInputError(f"未知 write target kind: {self.kind}")
        if not isinstance(self.production, bool) or not isinstance(self.protected, bool):
            raise ResourceGateInputError("production/protected 必须是布尔值")

    def safe_identity(self) -> dict[str, object]:
        """不输出可能含密钥的 location。"""

        return {
            "label": self.label,
            "kind": self.kind,
            "production": self.production,
            "protected": self.protected,
        }


@dataclass(frozen=True)
class ResourceUsage:
    """单个分块的预计或已观测资源证据。"""

    new_raw_read_bytes: int
    process_runtime_seconds: float
    temporary_bytes: int
    output_bytes: int
    write_targets: tuple[WriteTarget, ...] = ()
    phase: str = "estimated"

    def __post_init__(self) -> None:
        _non_negative_integer("new_raw_read_bytes", self.new_raw_read_bytes)
        _non_negative_number("process_runtime_seconds", self.process_runtime_seconds)
        _non_negative_integer("temporary_bytes", self.temporary_bytes)
        _non_negative_integer("output_bytes", self.output_bytes)
        if self.phase not in _ALLOWED_PHASES:
            raise ResourceGateInputError(
                "phase 必须是 estimated 或 observed"
            )
        if not isinstance(self.write_targets, tuple) or not all(
            isinstance(target, WriteTarget) for target in self.write_targets
        ):
            raise ResourceGateInputError("write_targets 必须是 WriteTarget 元组")

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "new_raw_read_bytes": self.new_raw_read_bytes,
            "process_runtime_seconds": self.process_runtime_seconds,
            "temporary_bytes": self.temporary_bytes,
            "output_bytes": self.output_bytes,
            "write_targets": [target.safe_identity() for target in self.write_targets],
        }


@dataclass(frozen=True)
class GateFinding:
    code: str
    message_zh: str
    category: str
    target_label: str | None = None

    def to_dict(self) -> dict[str, str]:
        result = {
            "code": self.code,
            "message_zh": self.message_zh,
            "category": self.category,
        }
        if self.target_label is not None:
            result["target_label"] = self.target_label
        return result


@dataclass(frozen=True)
class ResourceGateResult:
    """门禁决策。``execution_allowed`` 是调用方的唯一放行信号。"""

    decision: str
    execution_allowed: bool
    checkpoint_required: bool
    approval_required: bool
    findings: tuple[GateFinding, ...]
    usage: ResourceUsage
    limits: ResourceLimits

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "decision": self.decision,
            "execution_allowed": self.execution_allowed,
            "checkpoint_required": self.checkpoint_required,
            "approval_required": self.approval_required,
            "findings": [finding.to_dict() for finding in self.findings],
            "usage": self.usage.to_dict(),
            "limits": self.limits.to_dict(),
        }


def estimate_resource_usage(
    *,
    raw_input_sizes_bytes: Iterable[int],
    raw_read_passes: int = 1,
    process_runtime_seconds: float = 0,
    temporary_bytes: int = 0,
    output_bytes: int = 0,
    write_targets: Sequence[WriteTarget] = (),
    phase: str = "estimated",
) -> ResourceUsage:
    """对 dry-run 已盘点的输入大小做保守、确定性汇总。"""

    passes = _positive_integer("raw_read_passes", raw_read_passes)
    sizes: list[int] = []
    for index, size in enumerate(raw_input_sizes_bytes):
        sizes.append(_non_negative_integer(f"raw_input_sizes_bytes[{index}]", size))
    return ResourceUsage(
        new_raw_read_bytes=sum(sizes) * passes,
        process_runtime_seconds=_non_negative_number(
            "process_runtime_seconds", process_runtime_seconds
        ),
        temporary_bytes=_non_negative_integer("temporary_bytes", temporary_bytes),
        output_bytes=_non_negative_integer("output_bytes", output_bytes),
        write_targets=tuple(write_targets),
        phase=phase,
    )


def _database_like(target: WriteTarget) -> bool:
    if target.kind == "database":
        return True
    location = target.location.strip()
    lowered = location.lower()
    if lowered == ":memory:" or lowered.endswith(_DATABASE_SUFFIXES):
        return True
    try:
        scheme = urlsplit(location).scheme.lower()
    except ValueError:
        # 无法安全解析的位置由 unclassified 分支失败关闭。
        return False
    return scheme in _DATABASE_SCHEMES or bool(_DATABASE_DSN_PATTERN.search(location))


def _as_absolute_path(location: str) -> PurePosixPath | None:
    """只做词法归一化，不访问文件系统。"""

    try:
        split = urlsplit(location)
    except ValueError:
        return None
    if split.scheme:
        if split.scheme.lower() != "file" or split.netloc not in {"", "localhost"}:
            return None
        location = split.path
    normalized = posixpath.normpath(location)
    path = PurePosixPath(normalized)
    if not path.is_absolute():
        return None
    return path


def _normalized_roots(name: str, roots: Iterable[str]) -> tuple[PurePosixPath, ...]:
    result: list[PurePosixPath] = []
    for index, root in enumerate(roots):
        if not isinstance(root, str) or not root.strip():
            raise ResourceGateInputError(f"{name}[{index}] 必须是非空绝对路径")
        path = _as_absolute_path(root)
        if path is None:
            raise ResourceGateInputError(f"{name}[{index}] 必须是绝对文件路径")
        result.append(path)
    return tuple(result)


def _inside(path: PurePosixPath, root: PurePosixPath) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def evaluate_resource_gate(
    usage: ResourceUsage,
    *,
    limits: ResourceLimits | None = None,
    protected_roots: Iterable[str] = (),
    production_roots: Iterable[str] = (),
) -> ResourceGateResult:
    """评估资源与写入目标，不产生任何副作用。

    比较统一使用 ``>=``：预计值恰好达到硬上限时已无安全裕量，
    必须停止并请求批准。达到软限时要求在 record 边界检查点退出。
    """

    if not isinstance(usage, ResourceUsage):
        raise ResourceGateInputError("usage 必须是 ResourceUsage")
    active_limits = limits or ResourceLimits()
    if not isinstance(active_limits, ResourceLimits):
        raise ResourceGateInputError("limits 必须是 ResourceLimits")

    protected = _normalized_roots("protected_roots", protected_roots)
    production = _normalized_roots("production_roots", production_roots)
    findings: list[GateFinding] = []
    forbidden = False
    approval_required = False

    if usage.new_raw_read_bytes >= active_limits.max_new_raw_read_bytes:
        approval_required = True
        findings.append(
            GateFinding(
                code="new_raw_read_hard_limit_reached",
                category="hard_resource_limit",
                message_zh="新增原始读取量已达到或超过审批边界",
            )
        )
    if usage.process_runtime_seconds >= active_limits.hard_runtime_seconds:
        approval_required = True
        findings.append(
            GateFinding(
                code="runtime_hard_limit_reached",
                category="hard_resource_limit",
                message_zh="单进程运行时间已达到或超过十分钟审批边界",
            )
        )
    if usage.temporary_bytes >= active_limits.max_temporary_bytes:
        approval_required = True
        findings.append(
            GateFinding(
                code="temporary_space_hard_limit_reached",
                category="hard_resource_limit",
                message_zh="临时空间已达到或超过审批边界",
            )
        )

    for target in usage.write_targets:
        if _database_like(target):
            approval_required = True
            findings.append(
                GateFinding(
                    code="database_write_target",
                    category="write_boundary",
                    message_zh="检测到数据库写入目标，必须在写入前停止",
                    target_label=target.label,
                )
            )
            continue

        path = _as_absolute_path(target.location)
        if target.kind == "unknown" or path is None:
            forbidden = True
            findings.append(
                GateFinding(
                    code="unclassified_write_target",
                    category="write_boundary",
                    message_zh="写入目标无法安全分类为绝对文件路径",
                    target_label=target.label,
                )
            )
            continue

        is_production = target.production or target.kind == "production" or any(
            _inside(path, root) for root in production
        )
        is_protected = target.protected or any(
            _inside(path, root) for root in protected
        )
        if is_production:
            forbidden = True
            findings.append(
                GateFinding(
                    code="production_write_target",
                    category="write_boundary",
                    message_zh="研究流程禁止写入生产目标",
                    target_label=target.label,
                )
            )
        if is_protected:
            forbidden = True
            findings.append(
                GateFinding(
                    code="protected_write_target",
                    category="write_boundary",
                    message_zh="研究流程禁止写入受保护路径",
                    target_label=target.label,
                )
            )

    if forbidden:
        decision = "forbidden"
        execution_allowed = False
        checkpoint_required = True
    elif approval_required:
        decision = "approval_required"
        execution_allowed = False
        checkpoint_required = True
    elif usage.process_runtime_seconds >= active_limits.soft_runtime_seconds:
        findings.append(
            GateFinding(
                code="runtime_soft_limit_reached",
                category="soft_resource_limit",
                message_zh="已达到九分钟软限，应在 record 边界保存检查点并退出",
            )
        )
        decision = "soft_stop"
        execution_allowed = False
        checkpoint_required = True
    else:
        decision = "allowed"
        execution_allowed = True
        checkpoint_required = False

    return ResourceGateResult(
        decision=decision,
        execution_allowed=execution_allowed,
        checkpoint_required=checkpoint_required,
        approval_required=approval_required,
        findings=tuple(findings),
        usage=usage,
        limits=active_limits,
    )


__all__ = (
    "DEFAULT_HARD_RUNTIME_SECONDS",
    "DEFAULT_MAX_NEW_RAW_READ_BYTES",
    "DEFAULT_MAX_TEMPORARY_BYTES",
    "DEFAULT_SOFT_RUNTIME_SECONDS",
    "GateFinding",
    "ResourceGateInputError",
    "ResourceGateResult",
    "ResourceLimits",
    "ResourceUsage",
    "SCHEMA_VERSION",
    "WriteTarget",
    "estimate_resource_usage",
    "evaluate_resource_gate",
)
