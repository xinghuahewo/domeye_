"""伊朗 RRC25 国家中断研究的主张级对账纯函数。

本模块不读取文件、数据库或网络。调用方必须把已经冻结的主张清单、可复算
assessment 和内容寻址证据登记表显式传入。生成器只负责规范化、闭合引用、
执行 RRC25 单观测点的因果边界，并生成符合
``reconciliation-result/v1`` 合同的字典。

合同当前没有独立的 ``comparison_outcome`` 输出字段，因此该字段在 assessment
中是强制输入，并以“比较结果：一致/不同/不可算”写入中文 ``rationale_zh``，
避免通过 rating 反推时丢失比较语义。
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "reconciliation-result/v1"
CLAIM_INVENTORY_SCHEMA_VERSION = "iran-rrc25-report-claim-inventory/v1"

_RUN_ID_RE = re.compile(r"^research_run_v1_[0-9a-f]{24}$")
_EVIDENCE_ID_RE = re.compile(r"^evidence_v1_[0-9a-f]{24}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

_CLAIM_TYPES = (
    "report_event_time",
    "ipv4_decline",
    "recovery_state",
    "report_affected_asn_ratio",
    "report_visibility_class_counts",
    "database_affected_asn_ratio",
    "active_withdrawal_intent",
    "physical_cut",
    "bgp_session_closed",
    "traffic_impact",
    "government_intent",
)
_CLAIM_TYPE_SET = frozenset(_CLAIM_TYPES)
_COMPARISON_LABELS = {
    "consistent": "一致",
    "different": "不同",
    "not_computable": "不可算",
}
_CAUSAL_LEVELS = {
    "report_event_time": "observation",
    "ipv4_decline": "observation",
    "recovery_state": "observation",
    "report_affected_asn_ratio": "observation",
    "report_visibility_class_counts": "observation",
    "database_affected_asn_ratio": "observation",
    "active_withdrawal_intent": "intent_hypothesis",
    "physical_cut": "causal_hypothesis",
    "bgp_session_closed": "mechanism_hypothesis",
    "traffic_impact": "causal_hypothesis",
    "government_intent": "intent_hypothesis",
}
_RRC25_CAUSAL_TYPES = frozenset(
    (
        "active_withdrawal_intent",
        "physical_cut",
        "bgp_session_closed",
        "traffic_impact",
        "government_intent",
    )
)
_MANDATORY_HYPOTHESIS_TYPES = frozenset(
    ("active_withdrawal_intent", "government_intent")
)
_DEFAULT_UNKNOWN_RATING = {
    "active_withdrawal_intent": "hypothesis_only",
    "physical_cut": "hypothesis_only",
    "bgp_session_closed": "unverifiable",
    "traffic_impact": "unverifiable",
    "government_intent": "hypothesis_only",
}
_CAUSAL_LIMITATIONS = {
    "active_withdrawal_intent": "RRC25 的 WITHDRAW 仅是路由观测，不能证明运营方的主动撤回意图。",
    "physical_cut": "RRC25 路由观测不包含物理链路遥测，不能证明发生物理断路。",
    "bgp_session_closed": "结构化会话状态只描述 RRC25 的单条对等会话观测，不能证明全局会话关闭机制。",
    "traffic_impact": "路由可见性不等于业务流量，缺少流量遥测时不能复算流量影响。",
    "government_intent": "RRC25 路由观测不包含决策主体与意图证据，政府意图只能保留为假设。",
}
_DEFAULT_MISSING_REASONS = {
    "active_withdrawal_intent": "RRC25单源不能观测主动撤回意图",
    "physical_cut": "缺少物理链路遥测",
    "bgp_session_closed": "单观测点会话状态不能证明全局机制",
    "traffic_impact": "缺少流量遥测",
    "government_intent": "RRC25单源不能观测政府意图",
}
_EVIDENCE_KINDS = frozenset(
    (
        "sample",
        "episode",
        "wave",
        "episode_as",
        "route_event",
        "raw_record",
        "source_fact",
        "report_page",
        "limitation",
    )
)


class ReconciliationInputError(ValueError):
    """对账输入不完整、引用不闭合或越过证据边界。"""


def canonical_json(value: Any) -> str:
    """返回稳定 JSON；非有限数值会被拒绝。"""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ReconciliationInputError("值不能规范化为稳定 JSON") from error


def _stable_id(prefix: str, identity: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return prefix + digest[:24]


def evidence_id_v1(*, kind: str, ref: str, sha256: str) -> str:
    """由证据种类、稳定引用和内容哈希生成内容寻址 ID。"""

    normalized = _normalize_evidence_identity(kind=kind, ref=ref, sha256=sha256)
    return _stable_id("evidence_v1_", normalized)


def _nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationInputError(f"{field} 必须是非空字符串")
    if value != value.strip():
        raise ReconciliationInputError(f"{field} 首尾不得有空白")
    return value


def _chinese_text(value: object, field: str) -> str:
    text = _nonempty_text(value, field)
    if _HAN_RE.search(text) is None:
        raise ReconciliationInputError(f"{field} 必须包含中文说明")
    return text


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ReconciliationInputError(f"{field} 必须是 64 位小写十六进制 SHA-256")
    return value


def _normalize_evidence_identity(*, kind: object, ref: object, sha256: object) -> Dict[str, str]:
    if not isinstance(kind, str) or kind not in _EVIDENCE_KINDS:
        raise ReconciliationInputError("evidence.kind 不在合同枚举中")
    return {
        "kind": kind,
        "ref": _nonempty_text(ref, "evidence.ref"),
        "sha256": _sha256(sha256, "evidence.sha256"),
    }


def _normalize_evidence_registry(
    values: Iterable[Mapping[str, Any]],
) -> Tuple[Tuple[Dict[str, str], ...], Dict[str, str]]:
    if isinstance(values, (str, bytes, Mapping)):
        raise ReconciliationInputError("evidence_registry 必须是证据对象序列")
    try:
        rows = tuple(values)
    except TypeError as error:
        raise ReconciliationInputError("evidence_registry 必须可迭代") from error
    if not rows:
        raise ReconciliationInputError("evidence_registry 至少需要一条证据")

    output = []
    aliases: Dict[str, str] = {}
    seen_ids = set()
    seen_refs = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ReconciliationInputError(f"evidence_registry[{index}] 必须是对象")
        allowed = {"evidence_id", "kind", "ref", "sha256"}
        unknown = set(row) - allowed
        if unknown:
            raise ReconciliationInputError(
                f"evidence_registry[{index}] 包含未知字段: {sorted(unknown)}"
            )
        identity = _normalize_evidence_identity(
            kind=row.get("kind"), ref=row.get("ref"), sha256=row.get("sha256")
        )
        evidence_id = evidence_id_v1(**identity)
        supplied_id = row.get("evidence_id")
        if supplied_id is not None:
            if not isinstance(supplied_id, str) or _EVIDENCE_ID_RE.fullmatch(supplied_id) is None:
                raise ReconciliationInputError("evidence_id 格式非法")
            if supplied_id != evidence_id:
                raise ReconciliationInputError("evidence_id 与证据内容寻址结果不一致")
        if evidence_id in seen_ids or identity["ref"] in seen_refs:
            raise ReconciliationInputError("evidence_registry 存在重复证据 ID 或 ref")
        if identity["ref"] in aliases or (
            evidence_id in aliases and aliases[evidence_id] != evidence_id
        ):
            raise ReconciliationInputError("evidence_registry 的 ref 与内容寻址 ID 发生别名冲突")
        seen_ids.add(evidence_id)
        seen_refs.add(identity["ref"])
        record = {"evidence_id": evidence_id, **identity}
        output.append(record)
        aliases[evidence_id] = evidence_id
        aliases[identity["ref"]] = evidence_id

    output.sort(key=lambda row: row["evidence_id"])
    return tuple(output), aliases


def _normalize_reported_value(claim_type: str, value: object) -> object:
    if value is None:
        raise ReconciliationInputError(f"{claim_type}.reported_value 不得为空")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ReconciliationInputError(f"{claim_type}.reported_value 必须是有限数")
        return value
    if isinstance(value, str):
        normalized = _nonempty_text(value, f"{claim_type}.reported_value")
        if claim_type == "report_event_time":
            try:
                parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            except ValueError as error:
                raise ReconciliationInputError("报告事件时间不是合法 ISO 8601 时间") from error
            if parsed.utcoffset() is None:
                raise ReconciliationInputError("报告事件时间必须带时区")
            parsed = parsed.astimezone(timezone.utc)
            if parsed.microsecond:
                raise ReconciliationInputError("报告事件时间必须精确到秒")
            return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
        return normalized
    if isinstance(value, (Mapping, list, tuple)):
        # 合同的 claimValue 不接收对象；保留全部分量为排序稳定的 JSON 字符串。
        return canonical_json(value)
    raise ReconciliationInputError(f"{claim_type}.reported_value 类型不受支持")


def _claim_value(
    *,
    value: object,
    value_state: str,
    unit: Optional[str],
    snapshot_id: Optional[str],
    missing_reason: Optional[str],
) -> Dict[str, Any]:
    if value_state in {"unknown", "not_applicable"}:
        if value is not None:
            raise ReconciliationInputError("unknown/not_applicable 值必须为 null，不能补零")
        if unit is not None or snapshot_id is not None:
            raise ReconciliationInputError("unknown/not_applicable 不得携带 unit 或 snapshot_id")
        return {
            "value": None,
            "value_state": value_state,
            "unit": None,
            "snapshot_id": None,
            "missing_reason": _chinese_text(missing_reason, "missing_reason"),
        }

    if value_state not in {"reported", "recomputed"}:
        raise ReconciliationInputError("value_state 非法")
    if value is None or isinstance(value, (Mapping, list, tuple)):
        raise ReconciliationInputError("reported/recomputed 值必须是非空标量")
    if isinstance(value, float) and not math.isfinite(value):
        raise ReconciliationInputError("reported/recomputed 数值必须有限")
    if not isinstance(value, (str, int, float, bool)):
        raise ReconciliationInputError("reported/recomputed 值类型不受支持")
    if isinstance(value, str):
        _nonempty_text(value, "claim value")
    if unit is not None:
        unit = _nonempty_text(unit, "unit")
    if snapshot_id is not None:
        snapshot_id = _nonempty_text(snapshot_id, "snapshot_id")
    if missing_reason is not None:
        raise ReconciliationInputError("已知值的 missing_reason 必须为 null")
    return {
        "value": value,
        "value_state": value_state,
        "unit": unit,
        "snapshot_id": snapshot_id,
        "missing_reason": None,
    }


def _normalize_claim_inventory(inventory: Mapping[str, Any]) -> Tuple[Tuple[Mapping[str, Any], ...], Mapping[str, Any], Mapping[str, Any]]:
    if not isinstance(inventory, Mapping):
        raise ReconciliationInputError("claim_inventory 必须是对象")
    if inventory.get("schema_version") != CLAIM_INVENTORY_SCHEMA_VERSION:
        raise ReconciliationInputError("claim_inventory.schema_version 非法")
    study_id = _nonempty_text(inventory.get("study_id"), "study_id")
    incident_ref = _nonempty_text(inventory.get("incident_ref"), "incident_ref")
    scope = inventory.get("scope")
    if not isinstance(scope, Mapping) or scope.get("evidence_scope") not in {
        "rrc25_only",
        "rrc25_with_external_corroboration",
    }:
        raise ReconciliationInputError("scope.evidence_scope 非法")

    sources = inventory.get("sources")
    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
        raise ReconciliationInputError("sources 必须是序列")
    source_by_id = {}
    report_sources = []
    for source in sources:
        if not isinstance(source, Mapping):
            raise ReconciliationInputError("sources 只能包含对象")
        source_id = _nonempty_text(source.get("source_id"), "source_id")
        if source_id in source_by_id:
            raise ReconciliationInputError("sources 存在重复 source_id")
        _sha256(source.get("sha256"), f"{source_id}.sha256")
        if source.get("preserved_unmodified") is not True:
            raise ReconciliationInputError(f"{source_id} 必须 preserved_unmodified=true")
        source_by_id[source_id] = source
        if source.get("source_kind") == "user_supplied_docx":
            report_sources.append(source)
    if len(report_sources) != 1:
        raise ReconciliationInputError("必须且只能有一个 user_supplied_docx 报告源")
    report_source = report_sources[0]
    if report_source.get("preserved_unmodified") is not True:
        raise ReconciliationInputError("报告源必须 preserved_unmodified=true")
    _nonempty_text(report_source.get("title"), "report_source.title")
    _sha256(report_source.get("sha256"), "report_source.sha256")

    claims = inventory.get("claims")
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
        raise ReconciliationInputError("claims 必须是序列")
    claim_by_type = {}
    claim_keys = set()
    normalized_claims = []
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise ReconciliationInputError("claims 只能包含对象")
        claim_type = claim.get("claim_type")
        claim_key = _nonempty_text(claim.get("claim_key"), "claim_key")
        if claim_type not in _CLAIM_TYPE_SET:
            raise ReconciliationInputError(f"未知 claim_type: {claim_type}")
        if claim_type in claim_by_type or claim_key in claim_keys:
            raise ReconciliationInputError("主张清单存在重复 claim_type 或 claim_key")
        if claim.get("source_id") not in source_by_id:
            raise ReconciliationInputError(f"{claim_key} 引用了未知 source_id")
        _nonempty_text(claim.get("source_locator"), f"{claim_key}.source_locator")
        _chinese_text(claim.get("source_claim_zh"), f"{claim_key}.source_claim_zh")
        _nonempty_text(claim.get("reported_unit"), f"{claim_key}.reported_unit")
        _normalize_reported_value(claim_type, claim.get("reported_value"))
        claim_by_type[claim_type] = claim
        claim_keys.add(claim_key)
        normalized_claims.append(claim)
    if set(claim_by_type) != _CLAIM_TYPE_SET or len(normalized_claims) != 11:
        missing = sorted(_CLAIM_TYPE_SET - set(claim_by_type))
        extra = sorted(set(claim_by_type) - _CLAIM_TYPE_SET)
        raise ReconciliationInputError(
            f"主张清单必须精确包含 11 类；missing={missing}, extra={extra}"
        )
    ordered = tuple(claim_by_type[claim_type] for claim_type in _CLAIM_TYPES)
    return ordered, scope, {"incident_ref": incident_ref, "report_source": report_source, "study_id": study_id}


def _normalize_assessments(
    assessments: object, claim_keys: frozenset[str]
) -> Dict[str, Mapping[str, Any]]:
    rows = []
    if isinstance(assessments, Mapping):
        for key, raw in assessments.items():
            if not isinstance(raw, Mapping):
                raise ReconciliationInputError("assessment 映射值必须是对象")
            row = dict(raw)
            if "claim_key" in row and row["claim_key"] != key:
                raise ReconciliationInputError("assessment 映射键与 claim_key 不一致")
            row["claim_key"] = key
            rows.append(row)
    elif isinstance(assessments, Sequence) and not isinstance(assessments, (str, bytes)):
        rows = list(assessments)
    else:
        raise ReconciliationInputError("assessments 必须是映射或对象序列")

    result = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ReconciliationInputError(f"assessments[{index}] 必须是对象")
        key = _nonempty_text(row.get("claim_key"), "assessment.claim_key")
        if key in result:
            raise ReconciliationInputError(f"assessment 重复 claim_key: {key}")
        if key not in claim_keys:
            raise ReconciliationInputError(f"assessment 引用未知 claim_key: {key}")
        result[key] = row
    if set(result) != claim_keys:
        missing = sorted(claim_keys - set(result))
        extra = sorted(set(result) - claim_keys)
        raise ReconciliationInputError(
            f"assessments 必须精确覆盖冻结主张；missing={missing}, extra={extra}"
        )
    return result


def _resolve_refs(
    values: object,
    *,
    aliases: Mapping[str, str],
    field: str,
) -> Tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ReconciliationInputError(f"{field} 必须是引用序列")
    resolved = []
    for value in values:
        ref = _nonempty_text(value, field)
        evidence_id = aliases.get(ref)
        if evidence_id is None:
            raise ReconciliationInputError(f"{field} 包含未知证据引用: {ref}")
        resolved.append(evidence_id)
    if len(resolved) != len(set(resolved)):
        raise ReconciliationInputError(f"{field} 不得包含重复引用")
    return tuple(sorted(resolved))


def _normalize_assessment_value(
    assessment: Mapping[str, Any],
    *,
    claim_type: str,
    comparison_outcome: str,
) -> Dict[str, Any]:
    raw = assessment.get("recomputed_value")
    if not isinstance(raw, Mapping):
        raise ReconciliationInputError(
            f"{claim_type}.recomputed_value 必须是对象；不可算也必须显式给出 null 与原因"
        )
    allowed = {"value", "value_state", "unit", "snapshot_id", "missing_reason"}
    unknown = set(raw) - allowed
    if unknown:
        raise ReconciliationInputError(
            f"{claim_type}.recomputed_value 包含未知字段: {sorted(unknown)}"
        )

    if comparison_outcome == "not_computable":
        supplied_state = raw.get("value_state", "unknown")
        if supplied_state not in {"unknown", "not_applicable"}:
            raise ReconciliationInputError("不可算 assessment 的 value_state 必须是 unknown/not_applicable")
        value = raw.get("value")
        reason = raw.get("missing_reason") or _DEFAULT_MISSING_REASONS.get(claim_type)
        return _claim_value(
            value=value,
            value_state=supplied_state,
            unit=raw.get("unit"),
            snapshot_id=raw.get("snapshot_id"),
            missing_reason=reason,
        )

    if raw.get("value_state", "recomputed") != "recomputed":
        raise ReconciliationInputError("可复算 assessment 的 value_state 必须是 recomputed")
    value = raw.get("value")
    if isinstance(value, (Mapping, list, tuple)):
        value = canonical_json(value)
    return _claim_value(
        value=value,
        value_state="recomputed",
        unit=raw.get("unit"),
        snapshot_id=raw.get("snapshot_id"),
        missing_reason=raw.get("missing_reason"),
    )


def _claim_record(
    *,
    claim: Mapping[str, Any],
    assessment: Mapping[str, Any],
    evidence_scope: str,
    aliases: Mapping[str, str],
    study_id: object,
) -> Dict[str, Any]:
    claim_key = str(claim["claim_key"])
    claim_type = str(claim["claim_type"])
    outcome = assessment.get("comparison_outcome")
    if outcome not in _COMPARISON_LABELS:
        raise ReconciliationInputError(
            f"{claim_key}.comparison_outcome 必须是 consistent/different/not_computable"
        )

    if evidence_scope == "rrc25_only" and claim_type in _RRC25_CAUSAL_TYPES:
        if outcome != "not_computable":
            raise ReconciliationInputError(
                f"RRC25 单源不能把 {claim_type} 标为一致或不同的可复算结论"
            )

    recomputed = _normalize_assessment_value(
        assessment, claim_type=claim_type, comparison_outcome=outcome
    )
    evidence_refs = _resolve_refs(
        assessment.get("evidence_refs", ()),
        aliases=aliases,
        field=f"{claim_key}.evidence_refs",
    )
    counterevidence_refs = _resolve_refs(
        assessment.get("counterevidence_refs", ()),
        aliases=aliases,
        field=f"{claim_key}.counterevidence_refs",
    )
    overlap = set(evidence_refs) & set(counterevidence_refs)
    if overlap:
        raise ReconciliationInputError(
            f"{claim_key} 同一证据不能同时作为 evidence 与 counterevidence"
        )

    if outcome == "consistent":
        rating = "confirmed"
    elif outcome == "different":
        rating = "revised"
    else:
        requested = assessment.get("unknown_rating")
        if requested is not None and requested not in {"unverifiable", "hypothesis_only"}:
            raise ReconciliationInputError("unknown_rating 只能是 unverifiable/hypothesis_only")
        rating = requested or _DEFAULT_UNKNOWN_RATING.get(claim_type, "unverifiable")
        if claim_type in _MANDATORY_HYPOTHESIS_TYPES and rating != "hypothesis_only":
            raise ReconciliationInputError(
                f"{claim_type} 在 RRC25 单源下至少必须保留为 hypothesis_only"
            )

    if rating in {"confirmed", "revised"}:
        if recomputed["value_state"] != "recomputed" or not evidence_refs:
            raise ReconciliationInputError(
                f"{rating} 主张必须有可复算值和至少一条证据"
            )

    limitations_raw = assessment.get("limitations_zh")
    if not isinstance(limitations_raw, Sequence) or isinstance(limitations_raw, (str, bytes)):
        raise ReconciliationInputError(f"{claim_key}.limitations_zh 必须是中文字符串序列")
    limitations = [
        _chinese_text(value, f"{claim_key}.limitations_zh") for value in limitations_raw
    ]
    causal_limitation = _CAUSAL_LIMITATIONS.get(claim_type)
    if evidence_scope == "rrc25_only" and causal_limitation and causal_limitation not in limitations:
        limitations.append(causal_limitation)
    if not limitations:
        raise ReconciliationInputError(f"{claim_key}.limitations_zh 至少需要一项")

    rationale = _chinese_text(assessment.get("rationale_zh"), f"{claim_key}.rationale_zh")
    rationale = f"比较结果：{_COMPARISON_LABELS[outcome]}。{rationale}"
    causal_level = _CAUSAL_LEVELS[claim_type]
    supplied_causal_level = assessment.get("causal_level")
    if supplied_causal_level is not None and supplied_causal_level != causal_level:
        raise ReconciliationInputError(
            f"{claim_key}.causal_level 必须是冻结值 {causal_level}"
        )

    reported_value = _normalize_reported_value(claim_type, claim.get("reported_value"))
    original = _claim_value(
        value=reported_value,
        value_state="reported",
        unit=_nonempty_text(claim.get("reported_unit"), f"{claim_key}.reported_unit"),
        snapshot_id=None,
        missing_reason=None,
    )
    source_identity = {
        "study_id": study_id,
        "claim_key": claim_key,
        "claim_type": claim_type,
        "source_id": claim.get("source_id"),
        "source_locator": claim.get("source_locator"),
        "source_claim_zh": claim.get("source_claim_zh"),
        "original_value": original,
    }
    return {
        "claim_id": _stable_id("claim_v1_", source_identity),
        "claim_type": claim_type,
        "source_claim_zh": claim["source_claim_zh"],
        "original_value": original,
        "recomputed_value": recomputed,
        "rating": rating,
        "evidence_scope": evidence_scope,
        "causal_level": causal_level,
        "evidence_refs": list(evidence_refs),
        "counterevidence_refs": list(counterevidence_refs),
        "limitations_zh": limitations,
        "rationale_zh": rationale,
    }


def build_reconciliation_result(
    *,
    run_id: str,
    claim_inventory: Mapping[str, Any],
    assessments: object,
    evidence_registry: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """生成严格的、可重复的 ``reconciliation-result/v1`` 记录。

    ``assessment`` 必须精确覆盖冻结清单中的 11 个 ``claim_key``。每项必须
    显式给出 ``comparison_outcome``、``recomputed_value``、两组证据引用、
    中文限制与中文依据。引用既可使用登记表的 ``ref``，也可使用经校验的
    ``evidence_id``；输出统一为内容寻址 ID。
    """

    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise ReconciliationInputError("run_id 格式非法")
    claims, scope, inventory_context = _normalize_claim_inventory(claim_inventory)
    normalized_evidence, aliases = _normalize_evidence_registry(evidence_registry)
    claim_keys = frozenset(str(claim["claim_key"]) for claim in claims)
    normalized_assessments = _normalize_assessments(assessments, claim_keys)
    evidence_scope = str(scope["evidence_scope"])

    claim_records = tuple(
        _claim_record(
            claim=claim,
            assessment=normalized_assessments[str(claim["claim_key"])],
            evidence_scope=evidence_scope,
            aliases=aliases,
            study_id=inventory_context["study_id"],
        )
        for claim in claims
    )
    summary = {
        rating: sum(record["rating"] == rating for record in claim_records)
        for rating in ("confirmed", "revised", "unverifiable", "hypothesis_only")
    }
    report = inventory_context["report_source"]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "incident_ref": inventory_context["incident_ref"],
        "report_source": {
            "title": report["title"],
            "sha256": report["sha256"],
            "preserved_unmodified": True,
        },
        "evidence_registry": [dict(record) for record in normalized_evidence],
        "claims": [dict(record) for record in claim_records],
        "summary": summary,
    }
    reconciliation_id = _stable_id("reconciliation_v1_", payload)
    return {
        "schema_version": payload["schema_version"],
        "reconciliation_id": reconciliation_id,
        "run_id": payload["run_id"],
        "incident_ref": payload["incident_ref"],
        "report_source": payload["report_source"],
        "evidence_registry": payload["evidence_registry"],
        "claims": payload["claims"],
        "summary": payload["summary"],
    }


__all__ = (
    "CLAIM_INVENTORY_SCHEMA_VERSION",
    "ReconciliationInputError",
    "SCHEMA_VERSION",
    "build_reconciliation_result",
    "canonical_json",
    "evidence_id_v1",
)
