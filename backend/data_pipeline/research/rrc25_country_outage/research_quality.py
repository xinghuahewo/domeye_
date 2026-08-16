"""RRC25 伊朗国家中断研究的十项质量门纯函数。

质量评估只消费调用方显式传入的诊断事实、违规和内存中的研究记录；不读
文件、MRT、数据库，也不产生任何副作用。每个门都保留逐条中文诊断，禁止
用总分掩盖输入缺口。跨结构检查覆盖同快照计量、unknown 缺失语义、映射
未决、Episode/Wave 样本引用、RouteEvent/raw/artifact 闭环、稳定身份与原始
坐标、十进制资源硬边界和双运行语义指纹。

研究报告允许 ``warn``，而 ``research-run/v1`` 的质量门合同只允许
``pass/fail/pending``。因此 :meth:`QualityEvaluation.to_research_run_fields`
将非阻断 ``warn`` 无损映射为 ``blocking=false,status=pending``；阻断失败
始终映射为 ``blocking=true,status=fail``，绝不会被包装成 warning。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence, Tuple

from ...route_event import artifact_id_v1, route_event_id_v1


SCHEMA_VERSION = "rrc25-country-outage-research-quality/v1"

GATE_ORDER: Tuple[str, ...] = (
    "input_completeness",
    "parse_completeness",
    "state_continuity",
    "vp_coverage",
    "mapping_coverage",
    "stable_identity",
    "reference_closure",
    "missing_semantics",
    "resource_usage",
    "reproducibility",
)

CONTRACT_GATE_IDS = {
    "input_completeness": "input_integrity",
    "parse_completeness": "parse_integrity",
    "state_continuity": "state_continuity",
    "vp_coverage": "vp_coverage",
    "mapping_coverage": "mapping_coverage",
    "stable_identity": "stable_identity",
    "reference_closure": "reference_closure",
    "missing_semantics": "unknown_missingness",
    "resource_usage": "resource_usage",
    "reproducibility": "reproducibility",
}

MAX_NEW_RAW_BYTES = 50_000_000_000
MAX_TEMPORARY_BYTES = 5_000_000_000
MAX_WORKER_SECONDS = 600

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID_RE = re.compile(r"^art_v1_[0-9a-f]{32}$")
_ROUTE_EVENT_ID_RE = re.compile(r"^rte_v1_[0-9a-f]{32}$")
_RAW_REF_ID_RE = re.compile(r"^raw_v1_[0-9a-f]{32}$")
_SAMPLE_ID_RE = re.compile(r"^sample_v1_[0-9a-f]{24}$")
_SNAPSHOT_ID_RE = re.compile(r"^snapshot_v1_[0-9a-f]{24}$")
_EPISODE_ID_RE = re.compile(r"^episode_v1_[0-9a-f]{24}$")
_WAVE_ID_RE = re.compile(r"^wave_v1_[0-9a-f]{24}$")
_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_HAN_RE = re.compile(r"[\u3400-\u9fff]")
_UNKNOWN_STATES = frozenset(
    {
        "unknown_source_gap",
        "unknown_parse_failure",
        "unknown_mapping",
        "unknown_state_gap",
        "unknown",
    }
)
_REQUIRED_METRICS = frozenset(
    {
        "visible_asn_count",
        "damaged_asn_count",
        "baseline_asn_count",
        "visible_ipv4_prefix_count",
        "visible_ipv6_prefix_count",
        "visible_ipv4_address_union",
        "visible_ipv4_24_equivalent",
        "visible_ipv6_48_equivalent",
        "announce_count",
        "withdraw_count",
        "vp_expected_count",
        "vp_observed_count",
        "damaged_asn_ratio",
    }
)
_REQUIRED_ASN_SETS = frozenset({"visible", "damaged", "baseline"})
_STATE_DERIVED_METRICS = frozenset(
    {
        "visible_asn_count",
        "damaged_asn_count",
        "baseline_asn_count",
        "visible_ipv4_prefix_count",
        "visible_ipv6_prefix_count",
        "visible_ipv4_address_union",
        "visible_ipv4_24_equivalent",
        "visible_ipv6_48_equivalent",
        "damaged_asn_ratio",
    }
)


class ResearchQualityInputError(ValueError):
    """质量门调用格式非法，必须由调用方修正后再评估。"""


@dataclass(frozen=True)
class DiagnosticFact:
    """调用方明确声明的一条已执行检查及其结果。"""

    gate_id: str
    code: str
    passed: bool
    details_zh: str
    blocking: bool = True

    def __post_init__(self) -> None:
        _validate_diagnostic_identity(
            self.gate_id, self.code, self.details_zh, self.blocking
        )
        if not isinstance(self.passed, bool):
            raise ResearchQualityInputError("DiagnosticFact.passed 必须是布尔值")


@dataclass(frozen=True)
class DiagnosticViolation:
    """调用方明确发现的一条失败或警告；warning 永不阻断。"""

    gate_id: str
    code: str
    details_zh: str
    severity: str = "fail"
    blocking: bool = True

    def __post_init__(self) -> None:
        _validate_diagnostic_identity(
            self.gate_id, self.code, self.details_zh, self.blocking
        )
        if self.severity not in {"fail", "warn"}:
            raise ResearchQualityInputError("violation.severity 只能为 fail/warn")
        if self.severity == "warn" and self.blocking:
            raise ResearchQualityInputError("warn 必须明确为 blocking=false")


@dataclass(frozen=True)
class ResearchQualityInput:
    """一次质量评估的显式内存输入。"""

    facts: Tuple[DiagnosticFact, ...]
    violations: Tuple[DiagnosticViolation, ...] = ()
    samples: Tuple[Mapping[str, Any], ...] = ()
    episodes: Tuple[Mapping[str, Any], ...] = ()
    waves: Tuple[Mapping[str, Any], ...] = ()
    episode_as_records: Tuple[Mapping[str, Any], ...] = ()
    route_events: Tuple[Mapping[str, Any], ...] = ()
    raw_refs: Tuple[Mapping[str, Any], ...] = ()
    artifacts: Tuple[Mapping[str, Any], ...] = ()
    execution: Mapping[str, Any] | None = None
    semantic_fingerprints: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.facts, tuple) or not all(
            isinstance(item, DiagnosticFact) for item in self.facts
        ):
            raise ResearchQualityInputError("facts 必须是 DiagnosticFact 元组")
        if not isinstance(self.violations, tuple) or not all(
            isinstance(item, DiagnosticViolation) for item in self.violations
        ):
            raise ResearchQualityInputError(
                "violations 必须是 DiagnosticViolation 元组"
            )
        for name in (
            "samples",
            "episodes",
            "waves",
            "episode_as_records",
            "route_events",
            "raw_refs",
            "artifacts",
        ):
            value = getattr(self, name)
            if not isinstance(value, tuple) or not all(
                isinstance(item, Mapping) for item in value
            ):
                raise ResearchQualityInputError(f"{name} 必须是 mapping 元组")
        if self.execution is not None and not isinstance(self.execution, Mapping):
            raise ResearchQualityInputError("execution 必须是 mapping 或 None")
        if not isinstance(self.semantic_fingerprints, tuple):
            raise ResearchQualityInputError("semantic_fingerprints 必须是元组")


@dataclass(frozen=True)
class QualityDiagnostic:
    """输出报告中的一条确定性事实或违规。"""

    source: str
    code: str
    status: str
    blocking: bool
    details_zh: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "code": self.code,
            "status": self.status,
            "blocking": self.blocking,
            "details_zh": self.details_zh,
        }


@dataclass(frozen=True)
class QualityGateResult:
    """一个研究门的明细结果；blocking 表示该结果是否阻断验收。"""

    gate_id: str
    contract_gate_id: str
    status: str
    blocking: bool
    details_zh: Tuple[str, ...]
    diagnostics: Tuple[QualityDiagnostic, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "gate_id": self.gate_id,
            "contract_gate_id": self.contract_gate_id,
            "status": self.status,
            "blocking": self.blocking,
            "details_zh": list(self.details_zh),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class QualityEvaluation:
    """十门闭合后的研究验收结论。"""

    gates: Tuple[QualityGateResult, ...]
    run_state: str
    acceptance_state: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_state": self.run_state,
            "acceptance_state": self.acceptance_state,
            "gate_count": len(self.gates),
            "gates": [gate.to_dict() for gate in self.gates],
        }

    def to_research_run_fields(
        self, evidence_prefix: str = "quality"
    ) -> dict[str, object]:
        """返回严格符合 ``research-run/v1`` 枚举的三个字段。"""

        if not isinstance(evidence_prefix, str) or not evidence_prefix.strip():
            raise ResearchQualityInputError("evidence_prefix 不能为空")
        prefix = evidence_prefix.rstrip("/")
        return {
            "run_state": self.run_state,
            "acceptance_state": self.acceptance_state,
            "quality_gates": [
                {
                    "gate_id": gate.contract_gate_id,
                    "blocking": gate.blocking,
                    "status": (
                        "pending" if gate.status == "warn" else gate.status
                    ),
                    "evidence_ref": f"{prefix}/{gate.contract_gate_id}.json",
                }
                for gate in self.gates
            ],
        }


def _validate_diagnostic_identity(
    gate_id: object, code: object, details_zh: object, blocking: object
) -> None:
    if gate_id not in GATE_ORDER:
        raise ResearchQualityInputError(f"未知质量门: {gate_id}")
    if not isinstance(code, str) or _CODE_RE.fullmatch(code) is None:
        raise ResearchQualityInputError("诊断 code 必须是小写稳定标识")
    if (
        not isinstance(details_zh, str)
        or not details_zh.strip()
        or _HAN_RE.search(details_zh) is None
    ):
        raise ResearchQualityInputError("details_zh 必须是非空中文说明")
    if not isinstance(blocking, bool):
        raise ResearchQualityInputError("blocking 必须是布尔值")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _raw_record_ref_id_v1(
    file_sha256: str, record_ordinal: int, element_ordinal: int
) -> str:
    identity = {
        "schema": "raw_record_ref_id_v1",
        "file_sha256": file_sha256,
        "record_ordinal": record_ordinal,
        "element_ordinal": element_ordinal,
    }
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    return "raw_v1_" + digest[:32]


def _derived(
    gate_id: str, code: str, passed: bool, details_zh: str, blocking: bool = True
) -> QualityDiagnostic:
    return QualityDiagnostic(
        source="derived",
        code=code,
        status="pass" if passed else ("fail" if blocking else "warn"),
        blocking=blocking if not passed else False,
        details_zh=details_zh,
    )


def _violation(
    gate_id: str, code: str, details_zh: str, blocking: bool = True
) -> tuple[str, QualityDiagnostic]:
    return gate_id, _derived(gate_id, code, False, details_zh, blocking)


def _id_index(
    rows: Sequence[Mapping[str, Any]], field: str
) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    index: dict[str, Mapping[str, Any]] = {}
    duplicates: list[str] = []
    for row in rows:
        value = row.get(field)
        if isinstance(value, str):
            if value in index:
                duplicates.append(value)
            else:
                index[value] = row
    return index, sorted(set(duplicates))


def _measure_nodes(sample: Mapping[str, Any]) -> Iterable[tuple[str, Mapping[str, Any]]]:
    for group_name in ("metrics", "asn_sets"):
        group = sample.get(group_name)
        if not isinstance(group, Mapping):
            continue
        for name in sorted(group):
            value = group[name]
            if not isinstance(value, Mapping):
                continue
            yield f"{group_name}.{name}", value
            for component in ("numerator", "denominator"):
                nested = value.get(component)
                if isinstance(nested, Mapping):
                    yield f"{group_name}.{name}.{component}", nested


def _unknown_nodes(value: object, path: str = "") -> Iterable[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        if "value_state" in value or "visibility_state" in value:
            yield path or "$", value
        for key in sorted(value, key=str):
            yield from _unknown_nodes(value[key], f"{path}.{key}" if path else str(key))
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            yield from _unknown_nodes(item, f"{path}[{index}]")


def _validate_samples(data: ResearchQualityInput) -> list[tuple[str, QualityDiagnostic]]:
    findings: list[tuple[str, QualityDiagnostic]] = []
    seen: set[str] = set()
    for index, sample in enumerate(data.samples):
        sample_id = sample.get("sample_id")
        snapshot_id = sample.get("snapshot_id")
        label = sample_id if isinstance(sample_id, str) else f"samples[{index}]"
        if not isinstance(sample_id, str) or _SAMPLE_ID_RE.fullmatch(sample_id) is None:
            findings.append(_violation("stable_identity", "sample_id_invalid", f"样本 {label} 的 sample_id 非法。"))
        elif sample_id in seen:
            findings.append(_violation("stable_identity", "sample_id_duplicate", f"样本 ID {sample_id} 重复。"))
        else:
            seen.add(sample_id)
        if not isinstance(snapshot_id, str) or _SNAPSHOT_ID_RE.fullmatch(snapshot_id) is None:
            findings.append(_violation("stable_identity", "snapshot_id_invalid", f"样本 {label} 的 snapshot_id 非法。"))

        metrics = sample.get("metrics")
        asn_sets = sample.get("asn_sets")
        if not isinstance(metrics, Mapping) or set(metrics) != _REQUIRED_METRICS:
            findings.append(_violation("missing_semantics", "sample_metrics_incomplete", f"样本 {label} 未提供合同要求的十三项 metrics。"))
        if not isinstance(asn_sets, Mapping) or set(asn_sets) != _REQUIRED_ASN_SETS:
            findings.append(_violation("missing_semantics", "sample_asn_sets_incomplete", f"样本 {label} 未提供 visible、damaged、baseline 三个 ASN 集合。"))

        for path, measure in _measure_nodes(sample):
            if measure.get("sample_id") != sample_id or measure.get("snapshot_id") != snapshot_id:
                findings.append(_violation("stable_identity", "measure_parent_identity_mismatch", f"样本 {label} 的 {path} 未绑定父 sample_id 与同一 snapshot_id。"))

        continuity = sample.get("continuity_state")
        if continuity == "unknown_after_gap":
            findings.append(
                _violation(
                    "state_continuity",
                    "state_continuity_unknown",
                    f"样本 {label} 位于关键输入缺口之后，不能证明路由状态连续。",
                )
            )
            for path, measure in _measure_nodes(sample):
                root_name = path.split(".", 1)[1].split(".", 1)[0]
                if path.startswith("metrics.") and root_name not in _STATE_DERIVED_METRICS:
                    # 当前槽的 UPDATE 计数与 VP 会话覆盖可以在先前状态缺口后
                    # 仍被完整观测；不能把这些独立 raw observations 强制降为
                    # unknown。只有依赖连续路由状态的指标和 ASN 集合必须未知。
                    continue
                state = measure.get("value_state")
                if state not in _UNKNOWN_STATES:
                    findings.append(_violation("state_continuity", "gap_sample_has_observed_measure", f"样本 {label} 在连续性未知后仍把 {path} 标为已观测。"))
        elif continuity != "continuous":
            findings.append(_violation("state_continuity", "continuity_state_invalid", f"样本 {label} 的 continuity_state 非法或缺失。"))
        else:
            for path, measure in _measure_nodes(sample):
                state = measure.get("value_state")
                if state in {
                    "unknown_source_gap",
                    "unknown_parse_failure",
                    "unknown_state_gap",
                }:
                    findings.append(_violation("state_continuity", "continuous_sample_has_gap_measure", f"样本 {label} 标记连续，但 {path} 仍是输入或状态缺口。"))

        for path, node in _unknown_nodes(sample):
            state = node.get("value_state", node.get("visibility_state"))
            if state == "unknown_mapping":
                findings.append(_violation("mapping_coverage", "sample_mapping_unresolved", f"样本 {label} 的 {path} 因映射未决而未知。"))

        if isinstance(metrics, Mapping):
            expected = metrics.get("vp_expected_count")
            observed = metrics.get("vp_observed_count")
            if isinstance(expected, Mapping) and isinstance(observed, Mapping):
                left, right = expected.get("value"), observed.get("value")
                left_state = expected.get("value_state")
                right_state = observed.get("value_state")
                if left_state in _UNKNOWN_STATES or right_state in _UNKNOWN_STATES:
                    findings.append(_violation("vp_coverage", "vp_coverage_unknown", f"样本 {label} 的预期或观测 VP 数为 unknown，无法证明覆盖门。"))
                if (
                    isinstance(left, int)
                    and not isinstance(left, bool)
                    and isinstance(right, int)
                    and not isinstance(right, bool)
                    and right > left
                ):
                    findings.append(_violation("vp_coverage", "vp_observed_exceeds_expected", f"样本 {label} 的观测 VP 数大于预期 VP 数。"))

        for path, node in _unknown_nodes(sample):
            state = node.get("value_state", node.get("visibility_state"))
            if state not in _UNKNOWN_STATES:
                continue
            value = node.get("value", node.get("fully_invisible"))
            reason = node.get("missing_reason")
            if value is not None:
                findings.append(_violation("missing_semantics", "unknown_has_non_null_value", f"样本 {label} 的 {path} 为 unknown，却写入了非 null 值。"))
            if not isinstance(reason, str) or not reason.strip():
                findings.append(_violation("missing_semantics", "unknown_missing_reason", f"样本 {label} 的 {path} 为 unknown，却没有缺失原因。"))
    return findings


def _collect_sample_refs(row: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for field in ("supporting_sample_ids", "evidence_sample_ids"):
        value = row.get(field)
        if isinstance(value, (list, tuple)):
            refs.extend(item for item in value if isinstance(item, str))
    split = row.get("split_evidence")
    split_rows: Sequence[object]
    if isinstance(split, Mapping):
        split_rows = (split,)
    elif isinstance(split, (list, tuple)):
        split_rows = split
    else:
        split_rows = ()
    for item in split_rows:
        if not isinstance(item, Mapping):
            continue
        for field, value in item.items():
            if (field.endswith("sample_id") or field == "sample_id") and isinstance(value, str):
                refs.append(value)
            elif isinstance(value, Mapping):
                nested = value.get("sample_id")
                if isinstance(nested, str):
                    refs.append(nested)
    mappings = row.get("incident_mappings")
    if isinstance(mappings, (list, tuple)):
        for item in mappings:
            if isinstance(item, Mapping):
                refs.extend(
                    value
                    for value in item.get("evidence_sample_ids", ())
                    if isinstance(value, str)
                )
    return refs


def _validate_episode_links(data: ResearchQualityInput) -> list[tuple[str, QualityDiagnostic]]:
    findings: list[tuple[str, QualityDiagnostic]] = []
    samples, sample_dupes = _id_index(data.samples, "sample_id")
    episodes, episode_dupes = _id_index(data.episodes, "episode_id")
    waves, wave_dupes = _id_index(data.waves, "wave_id")
    for value in sample_dupes + episode_dupes + wave_dupes:
        findings.append(_violation("stable_identity", "research_id_duplicate", f"研究稳定 ID {value} 重复。"))

    for index, episode in enumerate(data.episodes):
        episode_id = episode.get("episode_id")
        label = episode_id if isinstance(episode_id, str) else f"episodes[{index}]"
        if not isinstance(episode_id, str) or _EPISODE_ID_RE.fullmatch(episode_id) is None:
            findings.append(_violation("stable_identity", "episode_id_invalid", f"Episode {label} 的稳定 ID 非法。"))
        for sample_id in _collect_sample_refs(episode):
            if sample_id not in samples:
                findings.append(_violation("reference_closure", "episode_sample_unresolved", f"Episode {label} 引用不存在的样本 {sample_id}。"))
        for wave_id in episode.get("wave_ids", ()) if isinstance(episode.get("wave_ids"), (list, tuple)) else ():
            if wave_id not in waves:
                findings.append(_violation("reference_closure", "episode_wave_unresolved", f"Episode {label} 引用不存在的 Wave {wave_id}。"))

    for index, wave in enumerate(data.waves):
        wave_id = wave.get("wave_id")
        label = wave_id if isinstance(wave_id, str) else f"waves[{index}]"
        if not isinstance(wave_id, str) or _WAVE_ID_RE.fullmatch(wave_id) is None:
            findings.append(_violation("stable_identity", "wave_id_invalid", f"Wave {label} 的稳定 ID 非法。"))
        episode_id = wave.get("episode_id")
        if episode_id not in episodes:
            findings.append(_violation("reference_closure", "wave_episode_unresolved", f"Wave {label} 引用不存在的 Episode {episode_id}。"))
        for sample_id in _collect_sample_refs(wave):
            if sample_id not in samples:
                findings.append(_violation("reference_closure", "wave_sample_unresolved", f"Wave {label} 引用不存在的样本 {sample_id}。"))
    return findings


def _normalize_coordinate(row: Mapping[str, Any]) -> tuple[str, int, int] | None:
    file_hash = row.get("file_sha256", row.get("artifact_sha256"))
    record = row.get("record_ordinal")
    element = row.get("element_ordinal")
    if (
        not isinstance(file_hash, str)
        or _SHA256_RE.fullmatch(file_hash) is None
        or isinstance(record, bool)
        or not isinstance(record, int)
        or record < 0
        or isinstance(element, bool)
        or not isinstance(element, int)
        or element < 0
    ):
        return None
    return file_hash, record, element


def _validate_evidence_links(data: ResearchQualityInput) -> list[tuple[str, QualityDiagnostic]]:
    findings: list[tuple[str, QualityDiagnostic]] = []
    artifacts, artifact_dupes = _id_index(data.artifacts, "artifact_id")
    raw_refs, raw_dupes = _id_index(data.raw_refs, "raw_record_ref_id")
    routes, route_dupes = _id_index(data.route_events, "route_event_id")
    for value in artifact_dupes + raw_dupes + route_dupes:
        findings.append(_violation("stable_identity", "evidence_id_duplicate", f"证据稳定 ID {value} 重复。"))

    for index, artifact in enumerate(data.artifacts):
        artifact_id = artifact.get("artifact_id")
        file_hash = artifact.get("file_sha256", artifact.get("artifact_sha256"))
        label = artifact_id if isinstance(artifact_id, str) else f"artifacts[{index}]"
        if not isinstance(artifact_id, str) or _ARTIFACT_ID_RE.fullmatch(artifact_id) is None or not isinstance(file_hash, str) or _SHA256_RE.fullmatch(file_hash) is None:
            findings.append(_violation("stable_identity", "artifact_identity_invalid", f"原始制品 {label} 的 ID 或 SHA-256 非法。"))
        else:
            try:
                expected = artifact_id_v1(file_hash)
            except ValueError:
                expected = None
            if artifact_id != expected:
                findings.append(_violation("stable_identity", "artifact_id_coordinate_mismatch", f"原始制品 {label} 的 ID 与文件哈希不一致。"))

    for index, raw in enumerate(data.raw_refs):
        raw_id = raw.get("raw_record_ref_id")
        label = raw_id if isinstance(raw_id, str) else f"raw_refs[{index}]"
        coordinate = _normalize_coordinate(raw)
        if not isinstance(raw_id, str) or _RAW_REF_ID_RE.fullmatch(raw_id) is None or coordinate is None:
            findings.append(_violation("stable_identity", "raw_identity_invalid", f"原始记录 {label} 的 ID 或坐标非法。"))
            continue
        file_hash, record, element = coordinate
        if raw_id != _raw_record_ref_id_v1(file_hash, record, element):
            findings.append(_violation("stable_identity", "raw_id_coordinate_mismatch", f"原始记录 {label} 的稳定 ID 与文件/record/element 坐标不一致。"))
        verification_status = raw.get("verification_status")
        closure_state = raw.get("raw_closure_state")
        if verification_status == "verified":
            record_offset = raw.get("record_offset")
            record_length = raw.get("record_length")
            record_hash = raw.get("record_hash")
            if (
                isinstance(record_offset, bool)
                or not isinstance(record_offset, int)
                or record_offset < 0
                or isinstance(record_length, bool)
                or not isinstance(record_length, int)
                or record_length < 12
                or not isinstance(record_hash, str)
                or _SHA256_RE.fullmatch(record_hash) is None
                or closure_state != "verified_raw_audit"
                or raw.get("missing_reason_zh") is not None
            ):
                findings.append(
                    _violation(
                        "reference_closure",
                        "raw_audit_metadata_invalid",
                        f"原始记录 {label} 标记 verified，但缺少正式 raw audit 的字节范围、record hash 或闭合状态。",
                    )
                )
        elif verification_status == "derived_coordinate_only":
            if (
                closure_state != "unverified"
                or any(
                    raw.get(field) is not None
                    for field in ("record_offset", "record_length", "record_hash")
                )
                or not isinstance(raw.get("missing_reason_zh"), str)
                or not raw.get("missing_reason_zh", "").strip()
            ):
                findings.append(
                    _violation(
                        "missing_semantics",
                        "coordinate_only_state_invalid",
                        f"原始记录 {label} 的 coordinate-only 缺失语义不完整。",
                    )
                )
            findings.append(
                _violation(
                    "reference_closure",
                    "raw_audit_unverified",
                    f"原始记录 {label} 仅由坐标推导，正式 raw audit 前不得宣称 raw_traceable 或引用闭合。",
                )
            )
        else:
            findings.append(
                _violation(
                    "reference_closure",
                    "raw_verification_status_invalid",
                    f"原始记录 {label} 缺少 verified/derived_coordinate_only 验证状态。",
                )
            )
        artifact_id = raw.get("artifact_id")
        if artifact_id not in artifacts:
            findings.append(_violation("reference_closure", "raw_artifact_unresolved", f"原始记录 {label} 引用不存在的制品 {artifact_id}。"))
        elif artifacts[artifact_id].get("file_sha256", artifacts[artifact_id].get("artifact_sha256")) != file_hash:
            findings.append(_violation("reference_closure", "raw_artifact_hash_mismatch", f"原始记录 {label} 与父制品 SHA-256 不一致。"))

    for index, route in enumerate(data.route_events):
        route_id = route.get("route_event_id")
        label = route_id if isinstance(route_id, str) else f"route_events[{index}]"
        coordinate = _normalize_coordinate(route)
        if not isinstance(route_id, str) or _ROUTE_EVENT_ID_RE.fullmatch(route_id) is None or coordinate is None:
            findings.append(_violation("stable_identity", "route_identity_invalid", f"RouteEvent {label} 的 ID 或坐标非法。"))
            continue
        file_hash, record, element = coordinate
        try:
            expected = route_event_id_v1(file_hash, record, element)
        except ValueError:
            expected = None
        if route_id != expected:
            findings.append(_violation("stable_identity", "route_id_coordinate_mismatch", f"RouteEvent {label} 的稳定 ID 与文件/record/element 坐标不一致。"))
        raw_ids: list[object] = []
        artifact_id = route.get("artifact_id")
        artifact = artifacts.get(artifact_id) if isinstance(artifact_id, str) else None
        if artifact is None:
            findings.append(_violation("reference_closure", "route_artifact_unresolved", f"RouteEvent {label} 引用不存在的制品 {artifact_id}。"))
        elif artifact.get("file_sha256", artifact.get("artifact_sha256")) != file_hash:
            findings.append(_violation("reference_closure", "route_artifact_hash_mismatch", f"RouteEvent {label} 与父制品 SHA-256 不一致。"))
        if "raw_record_ref_id" in route:
            raw_ids.append(route.get("raw_record_ref_id"))
        if isinstance(route.get("raw_record_ref_ids"), (list, tuple)):
            raw_ids.extend(route["raw_record_ref_ids"])
        if not raw_ids:
            findings.append(_violation("reference_closure", "route_raw_ref_missing", f"RouteEvent {label} 没有 raw record 引用。"))
        for raw_id in raw_ids:
            raw = raw_refs.get(raw_id) if isinstance(raw_id, str) else None
            if raw is None:
                findings.append(_violation("reference_closure", "route_raw_ref_unresolved", f"RouteEvent {label} 引用不存在的 raw record {raw_id}。"))
            elif _normalize_coordinate(raw) != coordinate:
                findings.append(_violation("reference_closure", "route_raw_coordinate_mismatch", f"RouteEvent {label} 与 raw record 坐标不一致。"))
            elif raw.get("artifact_id") != artifact_id:
                findings.append(_violation("reference_closure", "route_raw_artifact_mismatch", f"RouteEvent {label} 与 raw record 指向不同原始制品。"))
            elif (
                raw.get("verification_status") == "verified"
                and route.get("raw_closure_state") != "verified_raw_audit"
            ):
                findings.append(
                    _violation(
                        "reference_closure",
                        "route_raw_closure_state_mismatch",
                        f"RouteEvent {label} 未声明 verified_raw_audit 闭合状态。",
                    )
                )
            elif (
                raw.get("verification_status") != "verified"
                and route.get("raw_closure_state") != "derived_coordinate_only"
            ):
                findings.append(
                    _violation(
                        "reference_closure",
                        "route_raw_closure_state_mismatch",
                        f"RouteEvent {label} 对未验证 raw record 的闭合状态不一致。",
                    )
                )

    for index, episode_as in enumerate(data.episode_as_records):
        mapping = episode_as.get("mapping_evidence")
        mapping_state = mapping.get("mapping_state") if isinstance(mapping, Mapping) else None
        label = str(episode_as.get("episode_as_id", f"episode_as_records[{index}]"))
        if mapping_state in {"unknown", "conflict"}:
            findings.append(_violation("mapping_coverage", "episode_as_mapping_unresolved", f"逐 ASN 记录 {label} 的国家映射仍为 {mapping_state}。"))
        elif mapping_state != "mapped":
            findings.append(_violation("mapping_coverage", "episode_as_mapping_missing", f"逐 ASN 记录 {label} 没有可核验的映射状态。"))
        for path, node in _unknown_nodes(episode_as):
            state = node.get("value_state", node.get("visibility_state"))
            if state not in _UNKNOWN_STATES:
                continue
            value = node.get("value", node.get("fully_invisible"))
            reason = node.get("missing_reason")
            if value is not None:
                findings.append(_violation("missing_semantics", "episode_as_unknown_has_non_null_value", f"逐 ASN 记录 {label} 的 {path} 为 unknown，却写入了非 null 值。"))
            if not isinstance(reason, str) or not reason.strip():
                findings.append(_violation("missing_semantics", "episode_as_unknown_missing_reason", f"逐 ASN 记录 {label} 的 {path} 为 unknown，却没有缺失原因。"))
        links = episode_as.get("evidence_links", ())
        if not isinstance(links, (list, tuple)):
            findings.append(_violation("reference_closure", "episode_as_evidence_links_invalid", f"逐 ASN 记录 {label} 的 evidence_links 不是数组。"))
            continue
        for link in links:
            if not isinstance(link, Mapping):
                findings.append(_violation("reference_closure", "episode_as_evidence_link_invalid", f"逐 ASN 记录 {label} 含非法 evidence link。"))
                continue
            route_id = link.get("route_event_id")
            raw_id = link.get("raw_record_ref_id")
            artifact_id = link.get("artifact_id")
            if route_id not in routes or raw_id not in raw_refs or artifact_id not in artifacts:
                findings.append(_violation("reference_closure", "episode_as_evidence_unresolved", f"逐 ASN 记录 {label} 的 RouteEvent/raw/artifact 引用未闭合。"))
                continue
            coordinate = _normalize_coordinate(link)
            if coordinate is None or coordinate != _normalize_coordinate(routes[route_id]) or coordinate != _normalize_coordinate(raw_refs[raw_id]):
                findings.append(_violation("reference_closure", "episode_as_evidence_coordinate_mismatch", f"逐 ASN 记录 {label} 的证据链坐标不一致。"))
            artifact_hash = artifacts[artifact_id].get(
                "file_sha256", artifacts[artifact_id].get("artifact_sha256")
            )
            if (
                artifact_hash != link.get("artifact_sha256")
                or routes[route_id].get("artifact_id") != artifact_id
                or raw_refs[raw_id].get("artifact_id") != artifact_id
            ):
                findings.append(_violation("reference_closure", "episode_as_evidence_artifact_mismatch", f"逐 ASN 记录 {label} 的证据链没有绑定同一原始制品与 SHA-256。"))
    return findings


def _validate_resources(data: ResearchQualityInput) -> list[tuple[str, QualityDiagnostic]]:
    execution = data.execution
    if execution is None:
        return [_violation("resource_usage", "execution_missing", "缺少 research-run execution 实际资源证据。")]
    findings: list[tuple[str, QualityDiagnostic]] = []
    required = {
        "database_write_operations": (0, "数据库写操作必须为零"),
        "new_raw_bytes_read": (MAX_NEW_RAW_BYTES, "新增原始读取达到或超过十进制 50 GB"),
        "peak_temporary_bytes": (MAX_TEMPORARY_BYTES, "临时空间达到或超过十进制 5 GB"),
        "max_worker_seconds": (MAX_WORKER_SECONDS, "单 worker 用时达到或超过 600 秒"),
    }
    for field, (limit, explanation) in required.items():
        value = execution.get(field)
        integer_required = field != "max_worker_seconds"
        wrong_type = (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or (integer_required and not isinstance(value, int))
        )
        if wrong_type or not math.isfinite(float(value)) or value < 0:
            findings.append(_violation("resource_usage", f"{field}_invalid", f"资源字段 {field} 缺失或不是非负有限数。"))
            continue
        if field == "database_write_operations":
            if value != 0:
                findings.append(_violation("resource_usage", "database_write_detected", f"{explanation}，实际为 {value}。"))
        elif value >= limit:
            findings.append(_violation("resource_usage", f"{field}_limit_reached", f"{explanation}，实际为 {value}。"))
    return findings


def _validate_reproducibility(data: ResearchQualityInput) -> list[tuple[str, QualityDiagnostic]]:
    values = data.semantic_fingerprints
    if len(values) != 2:
        return [_violation("reproducibility", "two_run_fingerprints_required", "可复现性必须提供恰好两次独立运行的语义指纹。")]
    if any(not isinstance(item, str) or _SHA256_RE.fullmatch(item) is None for item in values):
        return [_violation("reproducibility", "semantic_fingerprint_invalid", "双运行语义指纹必须都是 64 位小写 SHA-256。")]
    if values[0] != values[1]:
        return [_violation("reproducibility", "semantic_fingerprint_mismatch", "相同冻结输入的两次运行产生了不同语义指纹。")]
    return []


def _explicit_diagnostics(data: ResearchQualityInput) -> dict[str, list[QualityDiagnostic]]:
    grouped = {gate_id: [] for gate_id in GATE_ORDER}
    seen_codes: set[tuple[str, str]] = set()
    fact_gates: set[str] = set()
    for fact in data.facts:
        key = (fact.gate_id, fact.code)
        if key in seen_codes:
            raise ResearchQualityInputError(f"显式诊断重复: {fact.gate_id}/{fact.code}")
        seen_codes.add(key)
        fact_gates.add(fact.gate_id)
        if fact.passed:
            status, blocks = "pass", False
        elif fact.blocking:
            status, blocks = "fail", True
        else:
            status, blocks = "warn", False
        grouped[fact.gate_id].append(QualityDiagnostic("explicit_fact", fact.code, status, blocks, fact.details_zh))
    for item in data.violations:
        key = (item.gate_id, item.code)
        if key in seen_codes:
            raise ResearchQualityInputError(f"显式诊断重复: {item.gate_id}/{item.code}")
        seen_codes.add(key)
        status = "fail" if item.severity == "fail" and item.blocking else "warn"
        grouped[item.gate_id].append(QualityDiagnostic("explicit_violation", item.code, status, status == "fail", item.details_zh))
    for gate_id in GATE_ORDER:
        if gate_id not in fact_gates:
            grouped[gate_id].append(QualityDiagnostic("engine", "explicit_fact_missing", "fail", True, f"质量门 {gate_id} 缺少明确的已执行诊断事实。"))
    return grouped


def evaluate_research_quality(data: ResearchQualityInput) -> QualityEvaluation:
    """评估十项质量门，并返回确定性、逐事实可审计的验收结果。"""

    if not isinstance(data, ResearchQualityInput):
        raise ResearchQualityInputError("data 必须是 ResearchQualityInput")
    grouped = _explicit_diagnostics(data)
    derived_findings: list[tuple[str, QualityDiagnostic]] = []
    derived_findings.extend(_validate_samples(data))
    derived_findings.extend(_validate_episode_links(data))
    derived_findings.extend(_validate_evidence_links(data))
    derived_findings.extend(_validate_resources(data))
    derived_findings.extend(_validate_reproducibility(data))
    for gate_id, finding in derived_findings:
        grouped[gate_id].append(finding)

    gates: list[QualityGateResult] = []
    for gate_id in GATE_ORDER:
        diagnostics = tuple(
            sorted(
                grouped[gate_id],
                key=lambda item: (
                    item.code,
                    item.status,
                    item.source,
                    item.details_zh,
                ),
            )
        )
        if any(item.status == "fail" and item.blocking for item in diagnostics):
            status, blocking = "fail", True
        elif any(item.status == "warn" for item in diagnostics):
            status, blocking = "warn", False
        else:
            status = "pass"
            blocking = any(
                fact.gate_id == gate_id and fact.blocking for fact in data.facts
            )
        gates.append(
            QualityGateResult(
                gate_id=gate_id,
                contract_gate_id=CONTRACT_GATE_IDS[gate_id],
                status=status,
                blocking=blocking,
                details_zh=tuple(item.details_zh for item in diagnostics),
                diagnostics=diagnostics,
            )
        )

    blocking_failure = any(
        gate.status == "fail" and gate.blocking for gate in gates
    )
    if blocking_failure:
        run_state, acceptance_state = "incomplete", "not_accepted"
    else:
        run_state, acceptance_state = "completed", "accepted"
    return QualityEvaluation(tuple(gates), run_state, acceptance_state)


__all__ = (
    "CONTRACT_GATE_IDS",
    "DiagnosticFact",
    "DiagnosticViolation",
    "GATE_ORDER",
    "MAX_NEW_RAW_BYTES",
    "MAX_TEMPORARY_BYTES",
    "MAX_WORKER_SECONDS",
    "QualityDiagnostic",
    "QualityEvaluation",
    "QualityGateResult",
    "ResearchQualityInput",
    "ResearchQualityInputError",
    "SCHEMA_VERSION",
    "evaluate_research_quality",
)
