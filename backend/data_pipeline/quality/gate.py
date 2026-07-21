"""P0 数据质量门禁的纯函数核心。

本模块只接收已经生成的旁路 manifest/对账摘要，不打开数据库、不读取原始
MRT，也不写发布目录。调用方负责以只读方式取得输入并把返回值归档。门禁
采用失败关闭语义：质量字段缺失、类型非法或只有聚合失败数却没有可定位
明细时，不能被解释为通过。

冻结的输入扩展字段记录在 :data:`D2_REQUIRED_QUALITY_FIELDS`。D2 当前候选
manifest 尚未携带这些逐项计数时，本模块仍会生成合同有效的
``not_accepted`` 报告，并在中文失败明细中标记 ``missing_detail``；不会
根据 runner 的实现细节猜测为零。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


UTC = timezone.utc
SCHEMA_VERSION = "data-quality-report/v1"
GENERATOR_NAME = "domeye-p0-quality-report"
GENERATOR_VERSION = "1.0.0"
REPORT_FINGERPRINT_SCHEMA = "data_quality_report_fingerprint_v1"
INPUT_FINGERPRINT_SCHEMA = "p0_quality_gate_input_v1"
ARTIFACT_FINGERPRINT_SCHEMA = "mrt_artifact_manifest_fingerprint_v1"
METRIC_SOURCE_RECONCILIATION_SCOPE = (
    "independent_readonly_feature_rows_and_sqlite_interval_projection_v1"
)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

DIMENSION_ORDER = (
    "completeness",
    "uniqueness",
    "referential_integrity",
    "temporal_consistency",
    "raw_traceability",
    "sample_coverage",
    "reproducibility",
    "event_phase_coverage",
    "fixed_window_bounds",
    "unknown_missingness",
)

EVENT_TYPES = (
    "hijack",
    "sub_hijack",
    "leak",
    "prefix_outage",
    "as_outage",
    "country_outage",
)

# D2 runner 必须显式汇总这些字段，D5 才能据实放行。字段缺失不是零。
D2_REQUIRED_QUALITY_FIELDS = (
    "stable_id_conflict_count",
    "end_before_start_count",
    "local_utc_unverifiable_count",
    "invalid_asn_count",
    "invalid_prefix_count",
    "unknown_derived_null_count",
    "confirmed_missing_zero_fill_count",
    "visible_outside_window_count",
    "phase_state_missing_count",
    "phase_missing_reason_count",
)

ROUTE_EVENT_REQUIRED_QUALITY_FIELDS = (
    "raw_reference_unresolved_count",
    "processing_lineage_missing_count",
    "record_hash_verification_failed_count",
    "vp_identity_missing_count",
    "route_event_id_conflict_count",
    "invalid_asn_count",
    "invalid_prefix_count",
    "outside_window_record_count",
)

EVIDENCE_REQUIRED_QUALITY_FIELDS = (
    "schema_invalid_count",
    "classification_violation_count",
    "causal_conclusion_nonnull_count",
    "evidence_id_conflict_count",
    "unresolved_evidence_reference_count",
    "unresolved_route_event_reference_count",
    "outside_window_record_count",
    "unknown_missing_reason_count",
    "auto_zero_fill_count",
)

METRIC_REQUIRED_QUALITY_FIELDS = (
    "admitted_metric_count",
    "formula_contract_coverage_ratio",
    "strict_schema_status",
    "schema_invalid_count",
    "schema_validated_series_count",
    "schema_sha256",
    "source_reconciliation_scope",
    "source_reconciliation_expected_point_count",
    "source_reconciliation_difference_count",
    "source_reconciliation_difference_count_by_metric",
    "source_reconciliation_difference_count_by_type",
    "source_reconciliation_failure_samples",
    "internal_structural_difference_count",
    "reconciliation_difference_count_by_metric",
    "reconciliation_difference_count_by_type",
    "reconciliation_failure_samples",
    "reconciliation_difference_count",
    "unclassified_gap_count",
    "unknown_missing_reason_count",
    "confirmed_missing_zero_fill_count",
    "outside_window_point_count",
)

REPRODUCIBILITY_REQUIRED_FIELDS = (
    "execution_scope",
    "byte_identity",
    "semantic_validation",
    "full_semantic_validation",
    "conclusion",
)

_MISSING = object()


class QualityGateInputError(ValueError):
    """报告身份或执行安全证据不足，无法诚实生成合同对象。"""


@dataclass(frozen=True)
class QualityGateResult:
    """质量报告与不属于机器合同的中文定位明细。"""

    report: Mapping[str, Any]
    failure_details_zh: Tuple[Mapping[str, Any], ...]

    def report_bytes(self) -> bytes:
        """返回可直接归档的确定性 UTF-8 JSON。"""

        return (canonical_json(self.report) + "\n").encode("utf-8")

    def failure_details_bytes(self) -> bytes:
        """返回确定性中文失败清单 JSON。"""

        payload = {
            "schema_version": "p0-quality-failure-details-zh/v1",
            "report_id": self.report["report_id"],
            "failure_count": len(self.failure_details_zh),
            "failures": list(self.failure_details_zh),
        }
        return (canonical_json(payload) + "\n").encode("utf-8")


def canonical_json(value: Any) -> str:
    """使用稳定键序、无空白并拒绝 NaN/Infinity。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _source_sha256() -> str:
    digest = hashlib.sha256()
    with Path(__file__).open("rb") as stream:
        for block in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        raise QualityGateInputError(f"{field} 必须是带时区的秒级时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise QualityGateInputError(f"{field} 不是有效时间") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond:
        raise QualityGateInputError(f"{field} 必须带时区且精确到秒")
    return parsed


def _utc_text(value: Any, field: str) -> str:
    return _parse_time(value, field).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path_get(value: Any, path: str) -> Any:
    current = value
    for component in path.split(".") if path else ():
        if not isinstance(current, Mapping) or component not in current:
            return _MISSING
        current = current[component]
    return current


def _json_pointer(path: str) -> str:
    if not path:
        return "#"
    return "#/" + "/".join(
        component.replace("~", "~0").replace("/", "~1")
        for component in path.split(".")
    )


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_ratio(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 1
    )


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _artifact_id(file_sha256: str) -> str:
    identity = {"schema": "artifact_id_v1", "file_sha256": file_sha256}
    return "art_v1_" + _canonical_sha256(identity)[:32]


def _artifact_manifest_fingerprint(manifest: Mapping[str, Any]) -> Optional[str]:
    fingerprint = manifest.get("manifest_fingerprint_sha256")
    if not _is_sha256(fingerprint):
        return None
    payload = dict(manifest)
    payload.pop("manifest_fingerprint_sha256", None)
    expected = _canonical_sha256(
        {"schema": ARTIFACT_FINGERPRINT_SCHEMA, "manifest": payload}
    )
    return fingerprint if fingerprint == expected else None


def _input_sha(context: Mapping[str, Any], label: str) -> Optional[str]:
    values = context.get("input_sha256s")
    if not isinstance(values, Mapping):
        return None
    value = values.get(label)
    return value if _is_sha256(value) else None


def _evidence_ref(
    context: Mapping[str, Any], label: str, pointer: str, check_id: str
) -> Dict[str, Any]:
    names = {
        "d2": "d2-candidate-manifest.json",
        "d3": "d3-artifact-manifest.json",
        "route": "route-event-reconciliation-summary.json",
        "evidence": "evidence-reconciliation-summary.json",
        "metric": "metric-reconciliation-summary.json",
        "repro": "reproducibility-summary.json",
        "execution": "quality-gate-execution-context.json",
    }
    locator = names[label] + _json_pointer(pointer)
    return {
        "ref_id": f"{label}:{check_id}",
        "locator": locator,
        "sha256": _input_sha(context, label),
    }


def _failure(
    *,
    source_ref: str,
    table: str,
    primary_key: str,
    field: str,
    reason_codes: Sequence[str],
    evidence_locator: str,
    event_time: Optional[str] = None,
    missing_detail: bool = False,
) -> Dict[str, Any]:
    codes = sorted(set(reason_codes))
    if missing_detail and "missing_detail" not in codes:
        codes.append("missing_detail")
        codes.sort()
    return {
        "sample": {
            "source_ref": source_ref,
            "primary_key": primary_key,
            "field": field,
            "event_time": event_time,
            "reason_codes": codes,
        },
        "table": table,
        "evidence_locator": evidence_locator,
        "missing_detail": missing_detail,
    }


def _aggregate_failure(
    *,
    label: str,
    path: str,
    check_id: str,
    reason_code: str,
    missing_detail: bool,
) -> Dict[str, Any]:
    source = {
        "d2": "d2-candidate-manifest.json",
        "d3": "d3-artifact-manifest.json",
        "route": "route-event-reconciliation-summary.json",
        "evidence": "evidence-reconciliation-summary.json",
        "metric": "metric-reconciliation-summary.json",
        "repro": "reproducibility-summary.json",
        "execution": "quality-gate-execution-context.json",
    }[label]
    pointer = _json_pointer(path)
    field = path.rsplit(".", 1)[-1] if path else "input"
    return _failure(
        source_ref=source,
        table=f"{label}_aggregate",
        primary_key=f"aggregate:{check_id}",
        field=field,
        reason_codes=[reason_code],
        evidence_locator=source + pointer,
        missing_detail=missing_detail,
    )


def _normalize_supplied_failures(
    source: Optional[Mapping[str, Any]],
    *,
    label: str,
    check_id: str,
) -> List[Dict[str, Any]]:
    if not isinstance(source, Mapping):
        return []
    registry = source.get("quality_failure_samples")
    if not isinstance(registry, Mapping):
        return []
    rows = registry.get(check_id)
    if not isinstance(rows, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for index, row in enumerate(rows[:20]):
        if not isinstance(row, Mapping):
            continue
        source_ref = row.get("source_ref")
        primary_key = row.get("primary_key")
        field = row.get("field")
        reason_codes = row.get("reason_codes")
        table = row.get("table")
        if (
            not isinstance(source_ref, str)
            or not source_ref
            or not isinstance(primary_key, str)
            or not primary_key
            or not isinstance(field, str)
            or not field
            or not isinstance(table, str)
            or not table
            or not isinstance(reason_codes, list)
            or not reason_codes
            or any(
                not isinstance(code, str)
                or re.fullmatch(r"[a-z][a-z0-9_]*", code) is None
                for code in reason_codes
            )
        ):
            continue
        event_time = row.get("event_time")
        if event_time is not None:
            try:
                event_time = _utc_text(event_time, f"failure[{index}].event_time")
            except QualityGateInputError:
                continue
        normalized.append(
            _failure(
                source_ref=source_ref,
                table=table,
                primary_key=primary_key,
                field=field,
                event_time=event_time,
                reason_codes=reason_codes,
                evidence_locator=row.get("evidence_locator")
                if isinstance(row.get("evidence_locator"), str)
                and row.get("evidence_locator")
                else source_ref,
                missing_detail=False,
            )
        )
    return normalized


class _Checks:
    def __init__(self, context: Mapping[str, Any]) -> None:
        self.context = context
        self.checks: List[Dict[str, Any]] = []
        self.failure_details: List[Dict[str, Any]] = []

    def add(
        self,
        *,
        check_id: str,
        dimension: str,
        rule_id: str,
        title_zh: str,
        status: str,
        severity: str,
        scope_ref: str,
        observed_value: Any,
        observed_unit: str,
        expected_operator: str,
        expected_value: Any,
        expected_unit: str,
        unknown_count: int,
        message_zh: str,
        remediation_stage: str,
        evidence: Sequence[Tuple[str, str]],
        failures: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        if any(row["check_id"] == check_id for row in self.checks):
            raise AssertionError(f"检查 ID 重复：{check_id}")
        if dimension not in DIMENSION_ORDER:
            raise AssertionError(f"未知质量维度：{dimension}")
        failure_rows = list(failures)
        if status == "fail" and not failure_rows:
            raise AssertionError(f"失败检查缺少定位信息：{check_id}")
        refs = [
            _evidence_ref(self.context, label, pointer, check_id)
            for label, pointer in evidence
        ]
        if not refs:
            raise AssertionError(f"检查缺少证据引用：{check_id}")
        check = {
            "check_id": check_id,
            "dimension": dimension,
            "rule_id": rule_id,
            "title_zh": title_zh,
            "status": status,
            "severity": severity,
            "scope_ref": scope_ref,
            "observed": {"value": observed_value, "unit": observed_unit},
            "expected": {
                "operator": expected_operator,
                "value": expected_value,
                "unit": expected_unit,
            },
            "unknown_count": unknown_count,
            "failure_samples": [dict(item["sample"]) for item in failure_rows],
            "evidence_refs": refs,
            "message_zh": message_zh,
            "remediation_stage": remediation_stage,
        }
        self.checks.append(check)
        if status == "fail":
            for item in failure_rows:
                sample = item["sample"]
                self.failure_details.append(
                    {
                        "check_id": check_id,
                        "rule_id": rule_id,
                        "title_zh": title_zh,
                        "source": sample["source_ref"],
                        "table": item["table"],
                        "key": sample["primary_key"],
                        "field": sample["field"],
                        "event_time": sample["event_time"],
                        "reason_codes": sample["reason_codes"],
                        "evidence_locator": item["evidence_locator"],
                        "missing_detail": item["missing_detail"],
                        "message_zh": message_zh,
                    }
                )


def _count_values(
    specifications: Sequence[Tuple[Optional[Mapping[str, Any]], str, str]]
) -> Tuple[Optional[int], List[Tuple[str, str, str]]]:
    total = 0
    invalid: List[Tuple[str, str, str]] = []
    for source, label, path in specifications:
        value = _path_get(source, path) if isinstance(source, Mapping) else _MISSING
        if not _is_count(value):
            reason = "missing_quality_evidence" if value is _MISSING else "invalid_quality_evidence"
            invalid.append((label, path, reason))
        else:
            total += value
    return (None if invalid else total), invalid


def _add_zero_count_check(
    checks: _Checks,
    *,
    check_id: str,
    dimension: str,
    rule_id: str,
    title_zh: str,
    scope_ref: str,
    specifications: Sequence[Tuple[Optional[Mapping[str, Any]], str, str]],
    sources_for_samples: Sequence[Tuple[Optional[Mapping[str, Any]], str]],
    reason_code: str,
    remediation_stage: str,
    message_pass: str,
    message_fail: str,
    severity: str = "blocking",
) -> None:
    value, invalid = _count_values(specifications)
    status = "pass" if value == 0 and not invalid else "fail"
    failures: List[Dict[str, Any]] = []
    if status == "fail":
        for source, label in sources_for_samples:
            failures.extend(
                _normalize_supplied_failures(source, label=label, check_id=check_id)
            )
        if not failures:
            if invalid:
                failures.extend(
                    _aggregate_failure(
                        label=label,
                        path=path,
                        check_id=check_id,
                        reason_code=reason,
                        missing_detail=True,
                    )
                    for label, path, reason in invalid
                )
            else:
                label = specifications[0][1]
                path = specifications[0][2]
                failures.append(
                    _aggregate_failure(
                        label=label,
                        path=path,
                        check_id=check_id,
                        reason_code=reason_code,
                        missing_detail=True,
                    )
                )
    checks.add(
        check_id=check_id,
        dimension=dimension,
        rule_id=rule_id,
        title_zh=title_zh,
        status=status,
        severity=severity,
        scope_ref=scope_ref,
        observed_value=value,
        observed_unit="record_count",
        expected_operator="eq",
        expected_value=0,
        expected_unit="record_count",
        unknown_count=len(invalid) if invalid else (value if status == "fail" and not failures else 0),
        message_zh=message_pass if status == "pass" else message_fail,
        remediation_stage="none" if status == "pass" else remediation_stage,
        evidence=[(label, path) for _, label, path in specifications],
        failures=failures,
    )


def _profile_from_d2(
    d2_manifest: Mapping[str, Any], context: Mapping[str, Any]
) -> Dict[str, Any]:
    profile = d2_manifest.get("data_profile")
    if not isinstance(profile, Mapping):
        raise QualityGateInputError("D2 manifest 缺少 data_profile")
    required = ("id", "timezone", "window_start", "window_end_exclusive", "snapshot_time")
    if any(field not in profile for field in required):
        raise QualityGateInputError("D2 data_profile 缺少报告身份字段")
    if profile["timezone"] != "Asia/Shanghai":
        raise QualityGateInputError("冻结 DataQualityReport 仅接受 Asia/Shanghai")
    profile_id = profile["id"]
    profile_sha256 = context.get("profile_sha256")
    if (
        not isinstance(profile_id, str)
        or IDENTIFIER_RE.fullmatch(profile_id) is None
        or not _is_sha256(profile_sha256)
    ):
        raise QualityGateInputError("数据档 ID 或文件 SHA256 非法")
    start = _parse_time(profile["window_start"], "data_profile.window_start")
    end = _parse_time(profile["window_end_exclusive"], "data_profile.window_end_exclusive")
    snapshot = _parse_time(profile["snapshot_time"], "data_profile.snapshot_time")
    if not start < end or snapshot.timestamp() + 1 != end.timestamp():
        raise QualityGateInputError("数据档窗口或快照边界非法")
    return {
        "profile_id": profile_id,
        "profile_sha256": profile_sha256,
        "window": {
            "start": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "boundary": "[start,end)",
            "timezone": "Asia/Shanghai",
        },
        "snapshot_time": snapshot.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _source_release(
    d2_manifest: Mapping[str, Any], context: Mapping[str, Any]
) -> Dict[str, Any]:
    release_id = _path_get(d2_manifest, "source.release_id")
    git_sha = context.get("git_sha")
    manifest_git_sha = _path_get(d2_manifest, "source.provenance.git_sha")
    if git_sha is None:
        git_sha = manifest_git_sha
    probe = context.get("probe_fingerprint_sha256")
    artifact_sha = context.get("data_artifact_sha256")
    if artifact_sha is None:
        # D3 manifest 的实际输入文件 SHA 可直接作为本次数据制品身份；不能用
        # manifest 内部 fingerprint 冒充文件字节 SHA。
        artifact_sha = _input_sha(context, "d3")
    if not isinstance(release_id, str) or re.fullmatch(r"[A-Za-z0-9._-]+", release_id) is None:
        raise QualityGateInputError("D2 source.release_id 非法")
    if not isinstance(git_sha, str) or GIT_SHA_RE.fullmatch(git_sha) is None:
        raise QualityGateInputError("缺少有效 Git SHA")
    if (
        manifest_git_sha is not _MISSING
        and manifest_git_sha != git_sha
    ):
        raise QualityGateInputError("context.git_sha 与 D2 provenance 不一致")
    if not _is_sha256(probe):
        raise QualityGateInputError("缺少有效 probe_fingerprint_sha256")
    if artifact_sha is not None and not _is_sha256(artifact_sha):
        raise QualityGateInputError("data_artifact_sha256 非法")
    d3_input_sha = _input_sha(context, "d3")
    if artifact_sha is not None and d3_input_sha is not None and artifact_sha != d3_input_sha:
        raise QualityGateInputError("data_artifact_sha256 与 D3 输入文件 SHA256 不一致")
    return {
        "release_id": release_id,
        "git_sha": git_sha,
        "data_artifact_sha256": artifact_sha,
        "probe_fingerprint_sha256": probe,
    }


def _execution(context: Mapping[str, Any]) -> Tuple[Dict[str, Any], str]:
    # 合同不允许把发生过写操作的执行伪装为 0；此时直接拒绝生成报告。
    operations = context.get("database_write_operations")
    if operations != 0 or isinstance(operations, bool):
        raise QualityGateInputError("质量门禁执行发现数据库写操作，拒绝生成准入报告")
    started = _utc_text(context.get("started_at"), "context.started_at")
    finished = _utc_text(context.get("finished_at"), "context.finished_at")
    if _parse_time(started, "started_at") > _parse_time(finished, "finished_at"):
        raise QualityGateInputError("质量门禁 finished_at 早于 started_at")
    generated = _utc_text(
        context.get("generated_at", finished), "context.generated_at"
    )
    return (
        {
            "mode": "read_only_repeatable_read",
            "database_write_operations": 0,
            "started_at": started,
            "finished_at": finished,
        },
        generated,
    )


def _d2_integrity_failures(d2: Mapping[str, Any]) -> List[Dict[str, Any]]:
    failures: List[Dict[str, Any]] = []

    def fail(path: str, reason: str) -> None:
        failures.append(
            _aggregate_failure(
                label="d2",
                path=path,
                check_id="completeness-input-contracts",
                reason_code=reason,
                missing_detail=False,
            )
        )

    if d2.get("schema_version") != "p0_normalization_candidate_v1":
        fail("schema_version", "unsupported_schema")
    if d2.get("candidate_kind") != "readonly_legacy_fact_normalization":
        fail("candidate_kind", "invalid_candidate_kind")
    if not _is_sha256(d2.get("candidate_fingerprint_sha256")):
        fail("candidate_fingerprint_sha256", "invalid_fingerprint")
    if d2.get("classification") != "observation_only" or d2.get("causal_conclusion") is not None:
        fail("classification", "causal_boundary_violation")
    sample = d2.get("sample")
    if not isinstance(sample, Mapping) or sample.get("enabled") is not False or sample.get("admissible") is not True:
        fail("sample", "sample_not_admissible")
    admission = d2.get("admission")
    if (
        not isinstance(admission, Mapping)
        or admission.get("status") != "legacy_candidate_ready"
        or admission.get("eligible_for_release_gate") is not True
        or admission.get("raw_traceable") is not False
        or admission.get("blocking_reasons") != []
    ):
        fail("admission", "candidate_not_ready")
    database = _path_get(d2, "source.database")
    if (
        not isinstance(database, Mapping)
        or database.get("transaction_read_only") is not True
        or str(database.get("transaction_isolation", "")).lower() != "repeatable read"
    ):
        fail("source.database", "readonly_evidence_missing")
    policy = d2.get("materialization_policy")
    if not isinstance(policy, Mapping) or policy.get("missing_values_coerced_to_zero") is not False:
        fail("materialization_policy.missing_values_coerced_to_zero", "zero_fill_policy_invalid")

    summary = d2.get("summary")
    files = d2.get("files")
    if not isinstance(summary, Mapping):
        fail("summary", "missing_quality_evidence")
        return failures
    if not isinstance(files, Mapping):
        fail("files", "missing_file_inventory")
        return failures
    expected_files = {
        "incidents.jsonl.gz": "incident_count",
        "links.jsonl.gz": "link_count",
        "collision_groups.jsonl.gz": "collision_group_count",
        "quarantine.jsonl.gz": "quarantine_count",
    }
    for filename, counter in expected_files.items():
        inventory = files.get(filename)
        count = summary.get(counter)
        if (
            not isinstance(inventory, Mapping)
            or not _is_count(inventory.get("row_count"))
            or inventory.get("row_count") != count
            or not _is_sha256(inventory.get("sha256"))
            or not _is_sha256(inventory.get("content_sha256"))
        ):
            fail(f"files.{filename}", "file_inventory_mismatch")
    if summary.get("incident_count") != summary.get("link_count"):
        fail("summary.link_count", "incident_link_count_mismatch")
    reverse = summary.get("reverse_orphan_count")
    explained = summary.get("explained_reverse_orphan_count")
    unexplained = summary.get("unexplained_reverse_orphan_count")
    if not all(_is_count(value) for value in (reverse, explained, unexplained)) or reverse != explained + unexplained:
        fail("summary.unexplained_reverse_orphan_count", "reverse_reference_count_mismatch")
    event_counts = summary.get("event_type_counts")
    if (
        not isinstance(event_counts, Mapping)
        or set(event_counts) != set(EVENT_TYPES)
        or not all(_is_count(value) for value in event_counts.values())
        or sum(event_counts.values()) != summary.get("incident_count")
    ):
        fail("summary.event_type_counts", "six_event_count_mismatch")
    return failures


def _d3_integrity_failures(
    d2: Mapping[str, Any], d3: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    failures: List[Dict[str, Any]] = []

    def fail(path: str, reason: str) -> None:
        failures.append(
            _aggregate_failure(
                label="d3",
                path=path,
                check_id="completeness-input-contracts",
                reason_code=reason,
                missing_detail=False,
            )
        )

    if d3.get("schema_version") != 1 or d3.get("manifest_kind") != "mrt_artifact_manifest":
        fail("schema_version", "unsupported_schema")
    scan_policy = d3.get("scan_policy")
    if (
        not isinstance(scan_policy, Mapping)
        or scan_policy.get("compression_envelope_validation")
        != "full_stream_to_eof_crc_or_equivalent"
    ):
        fail("scan_policy.compression_envelope_validation", "missing_compression_integrity_policy")
    if _artifact_manifest_fingerprint(d3) is None:
        fail("manifest_fingerprint_sha256", "invalid_fingerprint")
    d2_profile = d2.get("data_profile")
    d3_profile = d3.get("data_profile")
    for field in ("id", "timezone", "window_start", "window_end_exclusive"):
        if not isinstance(d2_profile, Mapping) or not isinstance(d3_profile, Mapping) or d2_profile.get(field) != d3_profile.get(field):
            fail(f"data_profile.{field}", "data_profile_mismatch")
    artifacts = d3.get("artifacts")
    summary = d3.get("summary")
    coverage = d3.get("coverage")
    if not isinstance(artifacts, list):
        fail("artifacts", "missing_artifact_inventory")
        artifacts = []
    if not isinstance(summary, Mapping):
        fail("summary", "missing_quality_evidence")
        summary = {}
    if not isinstance(coverage, Mapping):
        fail("coverage", "missing_quality_evidence")
        coverage = {}
    slot_intervals = {
        "update": timedelta(minutes=5),
        "rib": timedelta(hours=8),
    }
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    collector_allowlist = d3.get("collector_allowlist")
    if (
        not isinstance(collector_allowlist, list)
        or not collector_allowlist
        or any(not isinstance(value, str) or not value for value in collector_allowlist)
        or len(set(collector_allowlist)) != len(collector_allowlist)
    ):
        fail("collector_allowlist", "invalid_collector_allowlist")
        collector_set = set()
    else:
        collector_set = set(collector_allowlist)

    identities = set()
    coordinates = set()
    total_size = 0
    try:
        profile_window_start = _parse_time(
            d3_profile.get("window_start"), "d3.window_start"
        ).astimezone(UTC)
        profile_window_end = _parse_time(
            d3_profile.get("window_end_exclusive"),
            "d3.window_end_exclusive",
        ).astimezone(UTC)
        declared_window_start = _parse_time(
            d3_profile.get("window_start_utc"), "d3.window_start_utc"
        ).astimezone(UTC)
        declared_window_end = _parse_time(
            d3_profile.get("window_end_exclusive_utc"),
            "d3.window_end_exclusive_utc",
        ).astimezone(UTC)
        window_start = profile_window_start
        window_end = profile_window_end
        if window_start >= window_end:
            raise QualityGateInputError("d3 固定窗口起点必须早于终点")
        if (
            declared_window_start != window_start
            or declared_window_end != window_end
        ):
            fail("data_profile", "derived_utc_window_mismatch")
    except (QualityGateInputError, AttributeError):
        window_start = window_end = None
        fail("data_profile", "invalid_window")

    def slot_coordinate(
        record: Mapping[str, Any], path: str
    ) -> Optional[Tuple[str, str, str]]:
        collector = record.get("collector_id")
        artifact_type = record.get("artifact_type")
        valid = True
        if not isinstance(collector, str) or collector not in collector_set:
            fail(path + ".collector_id", "coordinate_outside_allowlist")
            valid = False
        interval = (
            slot_intervals.get(artifact_type)
            if isinstance(artifact_type, str)
            else None
        )
        if interval is None:
            fail(path + ".artifact_type", "unsupported_artifact_type")
            valid = False
        try:
            observed_time = _parse_time(
                record.get("artifact_time_utc"), path + ".artifact_time_utc"
            )
        except QualityGateInputError:
            fail(path + ".artifact_time_utc", "invalid_event_time")
            return None
        if observed_time.utcoffset() != timedelta(0):
            fail(path + ".artifact_time_utc", "non_utc_slot_coordinate")
            valid = False
        observed_time = observed_time.astimezone(UTC)
        if window_start is not None and not window_start <= observed_time < window_end:
            fail(path + ".artifact_time_utc", "outside_fixed_window")
            valid = False
        if interval is not None and (observed_time - epoch) % interval:
            fail(path + ".artifact_time_utc", "slot_alignment_mismatch")
            valid = False
        if not valid or not isinstance(collector, str) or not isinstance(artifact_type, str):
            return None
        return collector, artifact_type, observed_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    for index, artifact in enumerate(artifacts):
        path = f"artifacts.{index}"
        if not isinstance(artifact, Mapping):
            fail(path, "invalid_artifact_record")
            continue
        file_hash = artifact.get("file_sha256")
        artifact_id = artifact.get("artifact_id")
        coordinate = slot_coordinate(artifact, path)
        if not _is_sha256(file_hash) or artifact_id != _artifact_id(file_hash):
            fail(path, "artifact_identity_mismatch")
        if (isinstance(artifact_id, str) and artifact_id in identities) or (
            coordinate is not None and coordinate in coordinates
        ):
            fail(path, "duplicate_artifact_identity")
        if isinstance(artifact_id, str):
            identities.add(artifact_id)
        if coordinate is not None:
            coordinates.add(coordinate)
        size = artifact.get("size_bytes")
        if not _is_count(size):
            fail(path + ".size_bytes", "invalid_quality_evidence")
        else:
            total_size += size
    if summary.get("artifact_count") != len(artifacts) or summary.get("size_bytes") != total_size:
        fail("summary.artifact_count", "artifact_summary_mismatch")
    invalid_records = d3.get("invalid_in_window")
    if not isinstance(invalid_records, list):
        fail("invalid_in_window", "missing_invalid_artifact_inventory")
        invalid_records = []
    invalid_coordinates = set()
    invalid_size = 0
    invalid_reason_counts: Counter[str] = Counter()
    allowed_invalid_reasons = {
        "compressed_stream_invalid",
        "compression_magic_mismatch",
        "empty_file",
    }
    for index, invalid in enumerate(invalid_records):
        path = "invalid_in_window.{}".format(index)
        if not isinstance(invalid, Mapping):
            fail(path, "invalid_artifact_record")
            continue
        coordinate = slot_coordinate(invalid, path)
        if coordinate is not None and (
            coordinate in coordinates or coordinate in invalid_coordinates
        ):
            fail(path, "duplicate_artifact_identity")
        if coordinate is not None:
            invalid_coordinates.add(coordinate)
        reason = invalid.get("missing_reason")
        size = invalid.get("size_bytes")
        if (
            invalid.get("value_state") != "parse_failed"
            or not isinstance(reason, str)
            or reason not in allowed_invalid_reasons
            or not _is_sha256(invalid.get("file_sha256"))
            or not _is_count(size)
            or (reason == "empty_file" and size != 0)
            or (reason != "empty_file" and size == 0)
        ):
            fail(path, "invalid_gap_classification")
        else:
            invalid_reason_counts[reason] += 1
            invalid_size += size
    invalid_summary = summary.get("invalid_in_window")
    expected_invalid_by_reason = {
        reason: {
            "file_count": invalid_reason_counts[reason],
            "size_bytes": sum(
                row.get("size_bytes", 0)
                for row in invalid_records
                if isinstance(row, Mapping) and row.get("missing_reason") == reason
            ),
        }
        for reason in sorted(allowed_invalid_reasons)
    }
    if (
        not isinstance(invalid_summary, Mapping)
        or invalid_summary.get("file_count") != len(invalid_records)
        or invalid_summary.get("size_bytes") != invalid_size
        or invalid_summary.get("by_missing_reason") != expected_invalid_by_reason
    ):
        fail("summary.invalid_in_window", "invalid_artifact_summary_mismatch")
    expected = coverage.get("expected_slots")
    available = coverage.get("available_slots")
    missing = coverage.get("missing_slots")
    ratio = coverage.get("coverage_ratio")
    ranges = coverage.get("missing_ranges")
    if not all(_is_count(value) for value in (expected, available, missing)) or expected != available + missing:
        fail("coverage", "coverage_count_mismatch")
    elif not _is_ratio(ratio) or abs(ratio - (available / expected if expected else 1.0)) > 1e-8:
        fail("coverage.coverage_ratio", "coverage_ratio_mismatch")
    if not isinstance(ranges, list):
        fail("coverage.missing_ranges", "missing_gap_classification")
    else:
        classified = 0
        classified_by_state: Counter[str] = Counter()
        range_coordinates = set()
        parse_failed_coordinates = set()
        source_unavailable_coordinates = set()
        for index, item in enumerate(ranges):
            path = f"coverage.missing_ranges.{index}"
            if (
                not isinstance(item, Mapping)
            ):
                fail(path, "invalid_gap_classification")
                continue
            slot_count = item.get("slot_count")
            value_state = item.get("value_state")
            collector = item.get("collector_id")
            artifact_type = item.get("artifact_type")
            interval = (
                slot_intervals.get(artifact_type)
                if isinstance(artifact_type, str)
                else None
            )
            valid_range = True
            if not _is_count(slot_count) or slot_count == 0:
                fail(path + ".slot_count", "invalid_gap_classification")
                valid_range = False
            if not isinstance(value_state, str) or value_state not in {
                "source_unavailable",
                "parse_failed",
            }:
                fail(path + ".value_state", "invalid_gap_classification")
                valid_range = False
            if not isinstance(collector, str) or collector not in collector_set:
                fail(path + ".collector_id", "coordinate_outside_allowlist")
                valid_range = False
            if interval is None:
                fail(path + ".artifact_type", "unsupported_artifact_type")
                valid_range = False
            try:
                range_start = _parse_time(
                    item.get("start_time_utc"), path + ".start_time_utc"
                )
                range_end = _parse_time(
                    item.get("end_time_exclusive_utc"),
                    path + ".end_time_exclusive_utc",
                )
            except QualityGateInputError:
                fail(path, "invalid_gap_range_coordinate")
                valid_range = False
                range_start = range_end = None
            if range_start is not None and range_end is not None:
                if (
                    range_start.utcoffset() != timedelta(0)
                    or range_end.utcoffset() != timedelta(0)
                ):
                    fail(path, "non_utc_slot_coordinate")
                    valid_range = False
                range_start = range_start.astimezone(UTC)
                range_end = range_end.astimezone(UTC)
                if range_start >= range_end:
                    fail(path, "invalid_gap_range_coordinate")
                    valid_range = False
                # 槽按“起点落入半开窗口”计入；最后一个 RIB 槽的排他终点
                # 可以自然越过窗口尾，但其槽起点本身不得越界。
                if (
                    window_start is not None
                    and interval is not None
                    and (
                        range_start < window_start
                        or range_start >= window_end
                        or range_end - interval >= window_end
                    )
                ):
                    fail(path, "outside_fixed_window")
                    valid_range = False
                if interval is not None and (
                    (range_start - epoch) % interval
                    or (range_end - epoch) % interval
                ):
                    fail(path, "slot_alignment_mismatch")
                    valid_range = False
                if (
                    interval is not None
                    and _is_count(slot_count)
                    and slot_count > 0
                    and range_end - range_start != interval * slot_count
                ):
                    fail(path + ".slot_count", "gap_range_length_mismatch")
                    valid_range = False
            if _is_count(slot_count):
                classified += slot_count
                if isinstance(value_state, str) and value_state in {
                    "source_unavailable",
                    "parse_failed",
                }:
                    classified_by_state[value_state] += slot_count
            if not valid_range:
                continue
            assert interval is not None
            assert range_start is not None and range_end is not None
            assert isinstance(collector, str) and isinstance(artifact_type, str)
            assert isinstance(slot_count, int)
            current = range_start
            for _ in range(slot_count):
                coordinate = (
                    collector,
                    artifact_type,
                    current.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
                if coordinate in range_coordinates:
                    fail(path, "gap_coordinate_overlap")
                if coordinate in coordinates:
                    fail(path, "available_gap_coordinate_overlap")
                range_coordinates.add(coordinate)
                if value_state == "parse_failed":
                    parse_failed_coordinates.add(coordinate)
                else:
                    source_unavailable_coordinates.add(coordinate)
                current += interval
        if _is_count(missing) and classified != missing:
            fail("coverage.missing_ranges", "gap_count_mismatch")
        if classified_by_state["parse_failed"] != len(invalid_records):
            fail("coverage.missing_ranges", "parse_failed_count_mismatch")
        if parse_failed_coordinates != invalid_coordinates:
            fail("coverage.missing_ranges", "parse_failed_coordinate_mismatch")
        if (
            window_start is not None
            and window_end is not None
            and collector_set
        ):
            expected_coordinates = set()
            for collector in collector_set:
                for artifact_type, interval in slot_intervals.items():
                    remainder = (window_start - epoch) % interval
                    current = (
                        window_start
                        if not remainder
                        else window_start + (interval - remainder)
                    )
                    while current < window_end:
                        expected_coordinates.add(
                            (
                                collector,
                                artifact_type,
                                current.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            )
                        )
                        current += interval
            if _is_count(expected) and expected != len(expected_coordinates):
                fail("coverage.expected_slots", "expected_slot_count_mismatch")
            if coordinates | range_coordinates != expected_coordinates:
                fail("coverage.missing_ranges", "slot_coordinate_closure_mismatch")
            expected_source_unavailable = (
                expected_coordinates - coordinates - invalid_coordinates
            )
            if source_unavailable_coordinates != expected_source_unavailable:
                fail(
                    "coverage.missing_ranges",
                    "source_unavailable_coordinate_mismatch",
                )
    expected_status = "complete" if missing == 0 else "partial"
    if missing == 0:
        expected_state = None
    elif not invalid_records:
        expected_state = "source_unavailable"
    elif len(invalid_records) == missing:
        expected_state = "parse_failed"
    else:
        expected_state = "mixed"
    if coverage.get("coverage_status") != expected_status or coverage.get("missing_value_state") != expected_state:
        fail("coverage.coverage_status", "false_coverage_claim")
    return failures


def _missing_ranges_failures(d3: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = _path_get(d3, "coverage.missing_ranges")
    if not isinstance(rows, list):
        return []
    failures = []
    for index, row in enumerate(rows[:20]):
        if not isinstance(row, Mapping):
            continue
        collector = row.get("collector_id", "unknown")
        artifact_type = row.get("artifact_type", "unknown")
        start = row.get("start_time_utc")
        try:
            event_time = _utc_text(start, "coverage.missing_ranges.start_time_utc")
        except QualityGateInputError:
            event_time = None
        failures.append(
            _failure(
                source_ref="d3-artifact-manifest.json",
                table="artifact_manifest.coverage",
                primary_key=f"{collector}:{artifact_type}:{start}",
                field="missing_ranges",
                event_time=event_time,
                reason_codes=[
                    row.get("value_state")
                    if row.get("value_state") in {"source_unavailable", "parse_failed"}
                    else "unclassified_missingness"
                ],
                evidence_locator="d3-artifact-manifest.json#/coverage/missing_ranges/{}".format(index),
                missing_detail=False,
            )
        )
    return failures


def _summary_present(summary: Optional[Mapping[str, Any]], version: str) -> bool:
    return isinstance(summary, Mapping) and summary.get("schema_version") == version


def _raw_claimed(route_summary: Optional[Mapping[str, Any]]) -> bool:
    return (
        isinstance(route_summary, Mapping)
        and route_summary.get("lineage_status") == "raw_traceable"
        and isinstance(route_summary.get("route_event_count"), int)
        and route_summary.get("route_event_count", 0) > 0
    )


def _build_dimensions(checks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    summaries = {
        "completeness": "必需输入、实体、指标合同与只读证据",
        "uniqueness": "稳定 ID 与历史碰撞解释",
        "referential_integrity": "正反向、Evidence 与原始引用闭合",
        "temporal_consistency": "时间顺序与本地/UTC 转换",
        "raw_traceability": "原始覆盖、引用、哈希、VP 与处理血缘",
        "sample_coverage": "来源和派生缺口分类",
        "reproducibility": "稳定 ID、记录数和聚合摘要复现",
        "event_phase_coverage": "六类事件阶段状态与缺失原因",
        "fixed_window_bounds": "固定半开窗口外可见记录",
        "unknown_missingness": "未知空值与缺测补零",
    }
    result: Dict[str, Any] = {}
    for dimension in DIMENSION_ORDER:
        rows = [row for row in checks if row["dimension"] == dimension]
        if not rows:
            raise AssertionError(f"质量维度没有检查：{dimension}")
        if any(row["status"] == "fail" for row in rows):
            status = "fail"
        elif any(row["status"] == "pending" for row in rows):
            status = "pending"
        else:
            status = "pass"
        if status == "pass":
            conclusion = "通过"
        elif status == "pending":
            conclusion = "待定，证据尚未闭合"
        else:
            conclusion = "失败，不能用总分掩盖"
        result[dimension] = {
            "dimension": dimension,
            "status": status,
            "blocking": any(row["severity"] == "blocking" for row in rows),
            "check_ids": [row["check_id"] for row in rows],
            "summary_zh": f"{summaries[dimension]}：{conclusion}。",
        }
    return result


def _build_gate(checks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    failed = [
        row["check_id"]
        for row in checks
        if row["severity"] == "blocking" and row["status"] == "fail"
    ]
    pending = [
        row["check_id"]
        for row in checks
        if row["severity"] == "blocking" and row["status"] == "pending"
    ]
    warnings = [
        row["check_id"]
        for row in checks
        if row["severity"] == "warning" and row["status"] != "pass"
    ]
    raw_rows = [row for row in checks if row["dimension"] == "raw_traceability"]
    full_semantic_rows = [
        row
        for row in checks
        if row["check_id"] == "reproducibility-full-semantic-validation"
    ]
    full_semantic_ok = not full_semantic_rows or all(
        row["status"] == "pass" for row in full_semantic_rows
    )
    if failed:
        status = "failed"
        admission = "not_accepted"
        reasons = ["存在阻断性数据质量失败，当前数据档不得准入。"]
    elif pending:
        status = "pending"
        admission = "not_accepted"
        reasons = ["存在阻断性待定项，证据闭合前不得准入。"]
    else:
        status = "passed"
        if (
            raw_rows
            and all(row["status"] == "pass" for row in raw_rows)
            and full_semantic_ok
        ):
            admission = "raw_traceable"
            reasons = ["业务事实与全窗口原始引用均通过逐维度门禁。"]
        else:
            admission = "legacy_compatible"
            reasons = [
                "业务事实满足历史兼容准入；原始证据或全量语义复现未达到全窗口通过。"
            ]
    if warnings:
        reasons.append("原始覆盖、追溯或复现范围警告仍需保留，不能提升数据身份。")
    return {
        "status": status,
        "admission_level": admission,
        "blocking_failed_check_ids": failed,
        "blocking_pending_check_ids": pending,
        "warning_check_ids": warnings,
        "decision_reasons_zh": reasons,
    }


def _check_summary(checks: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    return {
        "total_check_count": len(checks),
        "passed_check_count": sum(row["status"] == "pass" for row in checks),
        "failed_check_count": sum(row["status"] == "fail" for row in checks),
        "pending_check_count": sum(row["status"] == "pending" for row in checks),
        "blocking_failed_check_count": sum(
            row["status"] == "fail" and row["severity"] == "blocking"
            for row in checks
        ),
        "blocking_pending_check_count": sum(
            row["status"] == "pending" and row["severity"] == "blocking"
            for row in checks
        ),
    }


def validate_report_semantics(report: Mapping[str, Any]) -> None:
    """复算维度引用、检查计数和门禁决定；发现漂移立即失败。"""

    checks = report.get("checks")
    dimensions = report.get("dimensions")
    if not isinstance(checks, list) or not isinstance(dimensions, Mapping):
        raise QualityGateInputError("质量报告缺少 checks/dimensions")
    ids = [row.get("check_id") for row in checks if isinstance(row, Mapping)]
    if len(ids) != len(checks) or len(set(ids)) != len(ids):
        raise QualityGateInputError("质量报告 check_id 不唯一")
    for name in DIMENSION_ORDER:
        dimension = dimensions.get(name)
        expected = [row["check_id"] for row in checks if row["dimension"] == name]
        if not isinstance(dimension, Mapping) or dimension.get("check_ids") != expected:
            raise QualityGateInputError(f"质量维度引用未闭合：{name}")
    if report.get("check_summary") != _check_summary(checks):
        raise QualityGateInputError("质量报告检查计数不一致")
    if report.get("gate") != _build_gate(checks):
        raise QualityGateInputError("质量报告门禁决定不一致")
    fingerprint = report.get("report_fingerprint_sha256")
    payload = dict(report)
    payload.pop("report_fingerprint_sha256", None)
    expected_fingerprint = _canonical_sha256(
        {"schema": REPORT_FINGERPRINT_SCHEMA, "report": payload}
    )
    if fingerprint != expected_fingerprint:
        raise QualityGateInputError("质量报告 fingerprint 不一致")


def _single_run_assurance_views(
    checks: _Checks,
    summary: Mapping[str, Any],
    context: Mapping[str, Any],
) -> Dict[str, Any]:
    """验证 single-run assurance，并投影到通用抽样复现检查。

    最终候选身份和闭包由 D5 CLI 独立复算后写入 ``context``；这里要求
    assurance 与实际输入逐字段相等。D2 有界重放仍保留为抽样，不能据此
    提升为全量流水线复现。
    """

    top_keys = {
        "schema_version",
        "assurance_mode",
        "execution_scope",
        "final_candidate_integrity",
        "final_candidate_identity",
        "cross_artifact_binding",
        "bounded_replay",
        "cross_run_coverage",
        "full_semantic_validation",
        "conclusion",
        "classification",
        "causal_conclusion",
    }
    top_ok = (
        set(summary) == top_keys
        and summary.get("schema_version") == "p0_single_run_assurance_v1"
        and summary.get("assurance_mode")
        == "final_single_candidate_plus_d2_bounded_replay_v1"
        and summary.get("classification") == "observation_only"
        and summary.get("causal_conclusion") is None
    )

    integrity = summary.get("final_candidate_integrity")
    components = integrity.get("components") if isinstance(integrity, Mapping) else None
    component_names = {"d2", "d3", "d4", "metric", "route_event"}
    components_ok = isinstance(components, Mapping) and set(components) == component_names
    if components_ok:
        for value in components.values():
            if (
                not isinstance(value, Mapping)
                or set(value)
                != {
                    "sha256sums_sha256",
                    "signed_file_count",
                    "signed_size_bytes",
                    "verified",
                }
                or not _is_sha256(value.get("sha256sums_sha256"))
                or not _is_count(value.get("signed_file_count"))
                or not _is_count(value.get("signed_size_bytes"))
                or value.get("verified") is not True
            ):
                components_ok = False
                break
    actual_integrity = context.get("final_candidate_integrity")
    integrity_ok = (
        top_ok
        and isinstance(integrity, Mapping)
        and integrity.get("status") == "passed"
        and integrity.get("all_sha256_closures_verified") is True
        and components_ok
        and isinstance(actual_integrity, Mapping)
        and dict(components) == dict(actual_integrity)
    )
    checks.add(
        check_id="reproducibility-final-artifact-integrity",
        dimension="reproducibility",
        rule_id="P0-REPRO-005",
        title_zh="最终五类候选 SHA256 闭包与送检输入一致",
        status="pass" if integrity_ok else "fail",
        severity="blocking",
        scope_ref="candidate:final-five-component-closures",
        observed_value=integrity_ok,
        observed_unit="boolean",
        expected_operator="eq",
        expected_value=True,
        expected_unit="boolean",
        unknown_count=0 if integrity_ok else 1,
        message_zh="D2/D3/D4/Metric/RouteEvent 闭包均由 D5 复算并与 assurance 一致。"
        if integrity_ok
        else "最终候选闭包缺失、非法或与 D5 实际送检输入不一致。",
        remediation_stage="none" if integrity_ok else "D5",
        evidence=[("repro", "final_candidate_integrity")],
        failures=[]
        if integrity_ok
        else [
            _aggregate_failure(
                label="repro",
                path="final_candidate_integrity",
                check_id="reproducibility-final-artifact-integrity",
                reason_code="deterministic_summary_mismatch",
                missing_detail=True,
            )
        ],
    )

    identity = summary.get("final_candidate_identity")
    identity_fields = {
        "d2": {
            "candidate_fingerprint_sha256",
            "manifest_sha256",
            "sha256sums_sha256",
            "incidents_sha256",
        },
        "d3": {
            "manifest_fingerprint_sha256",
            "manifest_sha256",
            "summary_sha256",
            "sha256sums_sha256",
        },
        "d4": {
            "candidate_fingerprint_sha256",
            "manifest_sha256",
            "reconciliation_fingerprint_sha256",
            "sha256sums_sha256",
        },
        "metric": {
            "candidate_fingerprint_sha256",
            "manifest_sha256",
            "reconciliation_fingerprint_sha256",
            "sha256sums_sha256",
        },
        "route_event": {
            "index_fingerprint_sha256",
            "parent_d3_manifest_fingerprint_sha256",
            "reconciliation_summary_sha256",
            "sha256sums_sha256",
        },
    }
    identity_ok = isinstance(identity, Mapping) and set(identity) == set(identity_fields)
    if identity_ok:
        for name, fields in identity_fields.items():
            value = identity.get(name)
            if (
                not isinstance(value, Mapping)
                or set(value) != fields
                or any(not _is_sha256(value.get(field)) for field in fields)
            ):
                identity_ok = False
                break
    actual_identity = context.get("final_candidate_identity")
    identity_ok = (
        top_ok
        and identity_ok
        and isinstance(actual_identity, Mapping)
        and dict(identity) == dict(actual_identity)
    )
    checks.add(
        check_id="reproducibility-final-identity-binding",
        dimension="reproducibility",
        rule_id="P0-REPRO-006",
        title_zh="Assurance 精确绑定最终五类候选身份",
        status="pass" if identity_ok else "fail",
        severity="blocking",
        scope_ref="candidate:final-five-component-identities",
        observed_value=identity_ok,
        observed_unit="boolean",
        expected_operator="eq",
        expected_value=True,
        expected_unit="boolean",
        unknown_count=0 if identity_ok else 1,
        message_zh="候选指纹、manifest、对账摘要及 SHA256SUMS 与实际送检输入一致。"
        if identity_ok
        else "Assurance 引用的最终候选不是本次 D5 实际送检候选。",
        remediation_stage="none" if identity_ok else "D5",
        evidence=[("repro", "final_candidate_identity")],
        failures=[]
        if identity_ok
        else [
            _aggregate_failure(
                label="repro",
                path="final_candidate_identity",
                check_id="reproducibility-final-identity-binding",
                reason_code="deterministic_summary_mismatch",
                missing_detail=True,
            )
        ],
    )

    binding = summary.get("cross_artifact_binding")
    binding_checks = binding.get("checks") if isinstance(binding, Mapping) else None
    expected_binding_keys = {
        "d4_to_final_d2",
        "d4_to_final_d3",
        "metric_to_final_d2",
        "metric_to_final_d3",
        "route_event_to_final_d3",
        "shared_data_profile",
    }
    actual_binding = context.get("cross_artifact_binding")
    binding_ok = (
        top_ok
        and isinstance(binding, Mapping)
        and set(binding) == {"status", "checks"}
        and binding.get("status") == "passed"
        and isinstance(binding_checks, Mapping)
        and set(binding_checks) == expected_binding_keys
        and all(value is True for value in binding_checks.values())
        and isinstance(actual_binding, Mapping)
        and dict(binding_checks) == dict(actual_binding)
    )
    checks.add(
        check_id="reproducibility-cross-artifact-binding",
        dimension="reproducibility",
        rule_id="P0-REPRO-007",
        title_zh="D4、Metric、RouteEvent 绑定最终上游候选",
        status="pass" if binding_ok else "fail",
        severity="blocking",
        scope_ref="candidate:cross-artifact-bindings",
        observed_value=binding_ok,
        observed_unit="boolean",
        expected_operator="eq",
        expected_value=True,
        expected_unit="boolean",
        unknown_count=0 if binding_ok else 1,
        message_zh="六项跨制品绑定均由 D5 从实际 manifest 复核通过。"
        if binding_ok
        else "跨制品绑定缺项、非法或与 D5 独立复核结果不一致。",
        remediation_stage="none" if binding_ok else "D5",
        evidence=[("repro", "cross_artifact_binding")],
        failures=[]
        if binding_ok
        else [
            _aggregate_failure(
                label="repro",
                path="cross_artifact_binding",
                check_id="reproducibility-cross-artifact-binding",
                reason_code="deterministic_summary_mismatch",
                missing_detail=True,
            )
        ],
    )

    bounded = summary.get("bounded_replay")
    side_values = {
        side: bounded.get(side) if isinstance(bounded, Mapping) else None
        for side in ("a", "b")
    }
    sides_ok = True
    for value in side_values.values():
        closure = value.get("closure") if isinstance(value, Mapping) else None
        sample = value.get("sample") if isinstance(value, Mapping) else None
        evidence = value.get("execution_evidence") if isinstance(value, Mapping) else None
        if (
            not isinstance(value, Mapping)
            or not _is_sha256(value.get("candidate_fingerprint_sha256"))
            or not _is_sha256(value.get("manifest_sha256"))
            or not _is_sha256(value.get("sha256sums_sha256"))
            or not _is_sha256(value.get("incidents_sha256"))
            or not isinstance(value.get("record_counts"), Mapping)
            or dict(sample or {})
            != {"enabled": True, "max_events": 64, "admissible": False}
            or not isinstance(closure, Mapping)
            or closure.get("verified") is not True
            or not _is_sha256(closure.get("sha256sums_sha256"))
            or closure.get("sha256sums_sha256") != value.get("sha256sums_sha256")
            or not _is_count(closure.get("signed_file_count"))
            or not _is_count(closure.get("signed_size_bytes"))
            or not isinstance(evidence, Mapping)
            or not _is_sha256(evidence.get("evidence_sha256"))
            or evidence.get("candidate_sha256sums_sha256")
            != value.get("sha256sums_sha256")
            or any(
                not _is_sha256(evidence.get(field))
                for field in ("command_argv_sha256", "stdout_sha256", "stderr_sha256")
            )
        ):
            sides_ok = False
            break
    byte = bounded.get("byte_identity") if isinstance(bounded, Mapping) else None
    semantic = bounded.get("semantic_identity") if isinstance(bounded, Mapping) else None
    independence = (
        bounded.get("generation_independence") if isinstance(bounded, Mapping) else None
    )
    stable = semantic.get("stable_id_scope") if isinstance(semantic, Mapping) else None
    bounded_ok = (
        top_ok
        and isinstance(bounded, Mapping)
        and bounded.get("component") == "d2"
        and bounded.get("requested_max_events") == 64
        and bounded.get("final_input_identity_match") is True
        and bounded.get("status") == "passed"
        and sides_ok
        and isinstance(byte, Mapping)
        and byte.get("scope") == "full_sample_candidate_closure"
        and byte.get("all_files_rehashed") is True
        and byte.get("all_corresponding_files_match") is True
        and byte.get("sha256sums_bytes_match") is True
        and byte.get("mismatch_count") == 0
        and byte.get("mismatched_files") == []
        and isinstance(semantic, Mapping)
        and semantic.get("scope") == "full_sample_candidate_population"
        and semantic.get("all_records_streamed") is True
        and semantic.get("record_count_metadata_match") is True
        and semantic.get("aggregate_summary_match") is True
        and semantic.get("file_inventory_match") is True
        and semantic.get("fingerprint_match") is True
        and semantic.get("all_results_match") is True
        and isinstance(stable, Mapping)
        and stable.get("match_ratio") == 1
        and isinstance(independence, Mapping)
        and independence.get("status") == "externally_attested"
        and independence.get("path_distinct") is True
        and independence.get("directory_inode_distinct") is True
        and independence.get("all_corresponding_file_inodes_distinct") is True
        and independence.get("external_execution_evidence_provided") is True
        and independence.get("cryptographic_independence_proven") is False
        and side_values["a"].get("execution_evidence", {}).get("execution_id")
        != side_values["b"].get("execution_evidence", {}).get("execution_id")
        and side_values["a"].get("execution_evidence", {}).get("output_dir")
        != side_values["b"].get("execution_evidence", {}).get("output_dir")
    )

    coverage = summary.get("cross_run_coverage")
    coverage_ok = (
        isinstance(coverage, Mapping)
        and set(coverage)
        == {
            "status",
            "replayed_components",
            "single_candidate_components",
            "population_coverage_claimed",
            "full_pipeline_reproducibility_claimed",
        }
        and coverage.get("status") == "partial"
        and coverage.get("replayed_components") == ["d2_bounded_sample"]
        and coverage.get("single_candidate_components")
        == ["d2_full", "d3", "d4", "metric", "route_event"]
        and coverage.get("population_coverage_claimed") is False
        and coverage.get("full_pipeline_reproducibility_claimed") is False
    )
    checks.add(
        check_id="reproducibility-cross-run-coverage",
        dimension="reproducibility",
        rule_id="P0-REPRO-008",
        title_zh="跨运行复现覆盖范围",
        status="pending" if coverage_ok else "fail",
        severity="warning" if coverage_ok else "blocking",
        scope_ref="candidate:cross-run-component-coverage",
        observed_value="partial" if coverage_ok else None,
        observed_unit="identity_level",
        expected_operator="eq",
        expected_value="full",
        expected_unit="identity_level",
        unknown_count=1,
        message_zh="仅 D2 的 64 条样本执行了双跑；其余最终候选为单份生成。"
        if coverage_ok
        else "跨运行覆盖声明非法，存在把抽样冒充全量的风险。",
        remediation_stage="D5",
        evidence=[("repro", "cross_run_coverage")],
        failures=[]
        if coverage_ok
        else [
            _aggregate_failure(
                label="repro",
                path="cross_run_coverage",
                check_id="reproducibility-cross-run-coverage",
                reason_code="missing_quality_evidence",
                missing_detail=True,
            )
        ],
    )

    execution_scope = summary.get("execution_scope")
    full_semantic = summary.get("full_semantic_validation")
    conclusion = summary.get("conclusion")
    scope_ok = (
        top_ok
        and bounded_ok
        and coverage_ok
        and isinstance(execution_scope, Mapping)
        and dict(execution_scope)
        == {
            "candidates_regenerated_in_this_execution": False,
            "source_database_access": "none",
            "source_database_connection_attempts": 0,
            "source_database_write_operations": 0,
            "raw_mrt_access": "none",
        }
        and isinstance(full_semantic, Mapping)
        and dict(full_semantic)
        == {
            "status": "not_run",
            "reason": "user_requested_bounded_sample",
            "population_coverage_claimed": False,
        }
        and isinstance(conclusion, Mapping)
        and conclusion.get("final_artifact_integrity_status") == "passed"
        and conclusion.get("bounded_d2_replay_status") == "passed"
        and conclusion.get("cross_artifact_binding_status") == "passed"
        and conclusion.get("cross_run_coverage_status") == "partial"
        and conclusion.get("full_semantic_reproducibility_status") == "not_run"
    )

    a = side_values["a"] if isinstance(side_values["a"], Mapping) else {}
    b = side_values["b"] if isinstance(side_values["b"], Mapping) else {}
    a_closure = a.get("closure") if isinstance(a.get("closure"), Mapping) else {}
    b_closure = b.get("closure") if isinstance(b.get("closure"), Mapping) else {}
    a_counts = a.get("record_counts") if isinstance(a.get("record_counts"), Mapping) else {}
    b_counts = b.get("record_counts") if isinstance(b.get("record_counts"), Mapping) else {}
    return {
        "execution_scope": {
            "candidates_regenerated": False,
            "source_database_access": "none",
            "source_database_connection_attempts": 0,
            "source_database_write_operations": 0,
            "raw_mrt_access": "none",
        },
        "byte_identity": {
            "scope": "full_artifact_closure",
            "all_files_rehashed": byte.get("all_files_rehashed") is True
            if isinstance(byte, Mapping)
            else False,
            "all_corresponding_files_match": bounded_ok,
            "components": {
                "d2_bounded_sample": {
                    "a_sha256sums_sha256": a.get("sha256sums_sha256"),
                    "b_sha256sums_sha256": b.get("sha256sums_sha256"),
                    "a_signed_file_count": a_closure.get("signed_file_count"),
                    "b_signed_file_count": b_closure.get("signed_file_count"),
                    "a_signed_size_bytes": a_closure.get("signed_size_bytes"),
                    "b_signed_size_bytes": b_closure.get("signed_size_bytes"),
                    "sha256sums_bytes_match": byte.get("sha256sums_bytes_match") is True
                    if isinstance(byte, Mapping)
                    else False,
                    "mismatch_count": byte.get("mismatch_count")
                    if isinstance(byte, Mapping)
                    else None,
                    "mismatched_files": byte.get("mismatched_files")
                    if isinstance(byte, Mapping)
                    else None,
                }
            },
        },
        "semantic_validation": {
            "mode": "deterministic_bounded_sample_v1",
            "sample_only": True,
            "population_coverage_claimed": False,
            "d2_sample_comparison": {
                "incidents.jsonl.gz": {
                    "a_selected_count": a_counts.get("incidents.jsonl.gz"),
                    "b_selected_count": b_counts.get("incidents.jsonl.gz"),
                    "a_content_sha256": a.get("incidents_sha256"),
                    "b_content_sha256": b.get("incidents_sha256"),
                    "match": bounded_ok,
                }
            },
            "stable_id_match_ratio": stable.get("match_ratio")
            if isinstance(stable, Mapping)
            else None,
            "record_count_metadata_match": bounded_ok,
            "aggregate_summary_match": bounded_ok,
            "fingerprint_matches": {"d2_bounded_sample": bounded_ok},
            "failure_count": 0 if bounded_ok else 1,
            "all_results_match": bounded_ok,
        },
        "full_semantic_validation": full_semantic,
        "conclusion": {
            "byte_reproducibility_status": "passed" if bounded_ok else "failed",
            "sampled_semantic_status": "passed" if bounded_ok else "failed",
            "full_semantic_reproducibility_status": "not_run",
        },
        "scope_ok": scope_ok,
        "bounded_ok": bounded_ok,
    }


def build_quality_report(
    d2_manifest: Mapping[str, Any],
    d3_artifact_manifest: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
    route_event_summary: Optional[Mapping[str, Any]] = None,
    artifact_verification_summary: Optional[Mapping[str, Any]] = None,
    evidence_summary: Optional[Mapping[str, Any]] = None,
    metric_summary: Optional[Mapping[str, Any]] = None,
    reproducibility_summary: Optional[Mapping[str, Any]] = None,
) -> QualityGateResult:
    """生成 schema-valid、逐维度且失败关闭的 P0 质量报告。

    ``context`` 必须提供 ``profile_sha256``、``git_sha``、
    ``probe_fingerprint_sha256``、``started_at``、``finished_at`` 和
    ``database_write_operations=0``。时间由调用方冻结，不读取系统当前时钟，
    因而相同输入字节会产生相同报告字节。
    """

    if not isinstance(d2_manifest, Mapping) or not isinstance(d3_artifact_manifest, Mapping):
        raise QualityGateInputError("D2/D3 manifest 必须是 JSON 对象")
    if not isinstance(context, Mapping):
        raise QualityGateInputError("context 必须是 JSON 对象")

    data_profile = _profile_from_d2(d2_manifest, context)
    source_release = _source_release(d2_manifest, context)
    execution, generated_at = _execution(context)
    checks = _Checks(context)

    input_failures = _d2_integrity_failures(d2_manifest) + _d3_integrity_failures(
        d2_manifest, d3_artifact_manifest
    )
    checks.add(
        check_id="completeness-input-contracts",
        dimension="completeness",
        rule_id="P0-COMPLETE-001",
        title_zh="D2/D3 输入身份与清单完整",
        status="fail" if input_failures else "pass",
        severity="blocking",
        scope_ref="candidate:d2+d3-manifests",
        observed_value=len(input_failures),
        observed_unit="record_count",
        expected_operator="eq",
        expected_value=0,
        expected_unit="record_count",
        unknown_count=0,
        message_zh="D2/D3 manifest 身份、指纹和内部计数闭合。"
        if not input_failures
        else "D2/D3 manifest 身份、指纹或内部计数不闭合。",
        remediation_stage="none" if not input_failures else "D5",
        evidence=[("d2", ""), ("d3", "")],
        failures=input_failures,
    )

    evidence_present = _summary_present(evidence_summary, "evidence_reconciliation_v1")
    evidence_failures = []
    if not evidence_present:
        evidence_failures.append(
            _aggregate_failure(
                label="evidence",
                path="schema_version",
                check_id="completeness-evidence-contract",
                reason_code="missing_quality_evidence",
                missing_detail=True,
            )
        )
    evidence_contract_value, evidence_contract_invalid = _count_values(
        [
            (evidence_summary, "evidence", "schema_invalid_count"),
            (evidence_summary, "evidence", "classification_violation_count"),
            (evidence_summary, "evidence", "causal_conclusion_nonnull_count"),
        ]
    )
    if evidence_contract_invalid and evidence_present:
        evidence_failures.extend(
            _aggregate_failure(
                label=label,
                path=path,
                check_id="completeness-evidence-contract",
                reason_code=reason,
                missing_detail=True,
            )
            for label, path, reason in evidence_contract_invalid
        )
    if evidence_contract_value not in (None, 0):
        supplied = _normalize_supplied_failures(
            evidence_summary, label="evidence", check_id="completeness-evidence-contract"
        )
        evidence_failures.extend(
            supplied
            or [
                _aggregate_failure(
                    label="evidence",
                    path="schema_invalid_count",
                    check_id="completeness-evidence-contract",
                    reason_code="evidence_contract_violation",
                    missing_detail=True,
                )
            ]
        )
    evidence_types = (
        evidence_summary.get("event_types")
        if evidence_present and isinstance(evidence_summary.get("event_types"), list)
        else None
    )
    evidence_scope_ok = (
        evidence_present
        and evidence_summary.get("scope")
        == "six_event_contract_investigation_sample"
        and evidence_summary.get("sample_only") is True
        and evidence_summary.get("population_coverage_claimed") is False
        and evidence_summary.get("strict_schema_status") == "passed"
        and evidence_summary.get("bundle_count") == 6
        and evidence_summary.get("event_type_count") == 6
        and evidence_types is not None
        and len(evidence_types) == len(EVENT_TYPES)
        and all(isinstance(event_type, str) for event_type in evidence_types)
        and set(evidence_types) == set(EVENT_TYPES)
    )
    if evidence_present and not evidence_scope_ok:
        evidence_failures.append(
            _aggregate_failure(
                label="evidence",
                path="scope",
                check_id="completeness-evidence-contract",
                reason_code="evidence_sample_scope_misrepresented",
                missing_detail=True,
            )
        )
    evidence_contract_status = (
        "pass"
        if evidence_scope_ok
        and evidence_contract_value == 0
        and not evidence_contract_invalid
        else "fail"
    )
    checks.add(
        check_id="completeness-evidence-contract",
        dimension="completeness",
        rule_id="P0-COMPLETE-002",
        title_zh="Evidence v2 合同与观测边界完整",
        status=evidence_contract_status,
        severity="blocking",
        scope_ref="evidence:v2:six-event-contract-sample",
        observed_value=evidence_contract_value,
        observed_unit="record_count",
        expected_operator="eq",
        expected_value=0,
        expected_unit="record_count",
        unknown_count=len(evidence_contract_invalid)
        + (0 if evidence_present else 1)
        + (0 if evidence_scope_ok else 1),
        message_zh="六类 Evidence 调查样本全部通过 schema，并明确不代表全量事件覆盖；保持 observation_only 且 causal_conclusion=null。"
        if evidence_contract_status == "pass"
        else "Evidence 对账摘要缺失或违反合同/因果边界。",
        remediation_stage="none" if evidence_contract_status == "pass" else "D4",
        evidence=[("evidence", "")],
        failures=evidence_failures,
    )

    metric_present = _summary_present(metric_summary, "metric_reconciliation_v1")
    metric_ratio = _path_get(metric_summary, "formula_contract_coverage_ratio") if metric_present else _MISSING
    metric_admitted = _path_get(metric_summary, "admitted_metric_count") if metric_present else _MISSING
    metric_schema_status = _path_get(metric_summary, "strict_schema_status") if metric_present else _MISSING
    metric_schema_invalid = _path_get(metric_summary, "schema_invalid_count") if metric_present else _MISSING
    metric_schema_validated = _path_get(metric_summary, "schema_validated_series_count") if metric_present else _MISSING
    metric_schema_sha = _path_get(metric_summary, "schema_sha256") if metric_present else _MISSING
    metric_source_scope = _path_get(metric_summary, "source_reconciliation_scope") if metric_present else _MISSING
    metric_source_expected_points = _path_get(metric_summary, "source_reconciliation_expected_point_count") if metric_present else _MISSING
    metric_source_differences = _path_get(metric_summary, "source_reconciliation_difference_count") if metric_present else _MISSING
    metric_source_by_metric = _path_get(metric_summary, "source_reconciliation_difference_count_by_metric") if metric_present else _MISSING
    metric_source_by_type = _path_get(metric_summary, "source_reconciliation_difference_count_by_type") if metric_present else _MISSING
    metric_source_samples = _path_get(metric_summary, "source_reconciliation_failure_samples") if metric_present else _MISSING
    metric_internal_structural_differences = _path_get(metric_summary, "internal_structural_difference_count") if metric_present else _MISSING
    metric_reconciliation_differences = _path_get(metric_summary, "reconciliation_difference_count") if metric_present else _MISSING
    metric_reconciliation_by_metric = _path_get(metric_summary, "reconciliation_difference_count_by_metric") if metric_present else _MISSING
    metric_reconciliation_by_type = _path_get(metric_summary, "reconciliation_difference_count_by_type") if metric_present else _MISSING
    metric_reconciliation_samples = _path_get(metric_summary, "reconciliation_failure_samples") if metric_present else _MISSING
    metric_contract_ok = (
        metric_ratio == 1
        and metric_admitted == 10
        and metric_schema_status == "passed"
        and metric_schema_invalid == 0
        and metric_schema_validated == 10
        and _is_sha256(metric_schema_sha)
        and metric_source_scope == METRIC_SOURCE_RECONCILIATION_SCOPE
        and _is_count(metric_source_expected_points)
        and metric_source_expected_points > 0
        and metric_source_differences == 0
        and isinstance(metric_source_by_metric, Mapping)
        and isinstance(metric_source_by_type, Mapping)
        and isinstance(metric_source_samples, list)
        and not metric_source_samples
        and metric_internal_structural_differences == 0
        and metric_reconciliation_differences == metric_source_differences
        and metric_reconciliation_by_metric == metric_source_by_metric
        and metric_reconciliation_by_type == metric_source_by_type
        and metric_reconciliation_samples == metric_source_samples
    )
    metric_failures = []
    if not metric_contract_ok:
        metric_failures.append(
            _aggregate_failure(
                label="metric",
                path=(
                    "strict_schema_status"
                    if metric_present and metric_schema_status != "passed"
                    else "source_reconciliation_scope"
                    if metric_present
                    and metric_source_scope != METRIC_SOURCE_RECONCILIATION_SCOPE
                    else "source_reconciliation_difference_count"
                    if metric_present and metric_source_differences != 0
                    else "internal_structural_difference_count"
                    if metric_present and metric_internal_structural_differences != 0
                    else "formula_contract_coverage_ratio"
                ),
                check_id="completeness-metric-contract",
                reason_code="missing_quality_evidence"
                if not metric_present
                else "metric_contract_incomplete",
                missing_detail=True,
            )
        )
    checks.add(
        check_id="completeness-metric-contract",
        dimension="completeness",
        rule_id="P0-METRIC-001",
        title_zh="十项准入指标公式、单位、范围与 Schema 完整",
        status="pass" if metric_contract_ok else "fail",
        severity="blocking",
        scope_ref="metric-series:admitted-ten-metrics",
        observed_value=metric_ratio if _is_ratio(metric_ratio) else None,
        observed_unit="ratio_0_1",
        expected_operator="eq",
        expected_value=1,
        expected_unit="ratio_0_1",
        unknown_count=0 if metric_present else 1,
        message_zh="十项准入指标均通过冻结 Schema、公式合同和独立源数据逐点对账。"
        if metric_contract_ok
        else "Metric 对账摘要缺失、准入合同覆盖不足，或独立源数据逐点对账未通过。",
        remediation_stage="none" if metric_contract_ok else "D2",
        evidence=[("metric", "formula_contract_coverage_ratio")],
        failures=metric_failures,
    )

    _add_zero_count_check(
        checks,
        check_id="completeness-entity-identities",
        dimension="completeness",
        rule_id="P0-IDENTITY-001",
        title_zh="可见 ASN 与前缀身份合法",
        scope_ref="normalized+route-event:asn-prefix",
        specifications=[
            (d2_manifest, "d2", "summary.invalid_asn_count"),
            (d2_manifest, "d2", "summary.invalid_prefix_count"),
        ]
        + (
            [
                (route_event_summary, "route", "invalid_asn_count"),
                (route_event_summary, "route", "invalid_prefix_count"),
            ]
            if route_event_summary is not None
            else []
        ),
        sources_for_samples=[(d2_manifest, "d2"), (route_event_summary, "route")],
        reason_code="invalid_identity",
        remediation_stage="D2",
        message_pass="非法身份只保留在 quarantine，不进入可见规范层。",
        message_fail="存在非法 ASN/前缀，或 manifest 没有给出逐项质量证据。",
    )

    database = _path_get(d2_manifest, "source.database")
    readonly_ok = (
        isinstance(database, Mapping)
        and database.get("transaction_read_only") is True
        and str(database.get("transaction_isolation", "")).lower() == "repeatable read"
    )
    readonly_failures = [] if readonly_ok else [
        _aggregate_failure(
            label="d2",
            path="source.database",
            check_id="completeness-readonly-safety",
            reason_code="readonly_evidence_missing",
            missing_detail=False,
        )
    ]
    checks.add(
        check_id="completeness-readonly-safety",
        dimension="completeness",
        rule_id="P0-SAFETY-001",
        title_zh="候选读取边界无数据库写副作用",
        status="pass" if readonly_ok else "fail",
        severity="blocking",
        scope_ref="execution:database-access",
        observed_value=0 if readonly_ok else None,
        observed_unit="record_count",
        expected_operator="eq",
        expected_value=0,
        expected_unit="record_count",
        unknown_count=0 if readonly_ok else 1,
        message_zh="D2 记录只读可重复读事务，本门禁核心不打开数据库。"
        if readonly_ok
        else "D2 没有提供可复核的只读可重复读证据。",
        remediation_stage="none" if readonly_ok else "D2",
        evidence=[("d2", "source.database"), ("execution", "database_write_operations")],
        failures=readonly_failures,
    )

    _add_zero_count_check(
        checks,
        check_id="uniqueness-stable-ids",
        dimension="uniqueness",
        rule_id="P0-UNIQUE-001",
        title_zh="稳定 Incident、RouteEvent 与 Evidence ID 无冲突",
        scope_ref="candidate:stable-identifiers",
        specifications=[
            (d2_manifest, "d2", "summary.stable_id_conflict_count"),
            (evidence_summary, "evidence", "evidence_id_conflict_count"),
        ]
        + (
            [(route_event_summary, "route", "route_event_id_conflict_count")]
            if route_event_summary is not None
            else []
        ),
        sources_for_samples=[
            (d2_manifest, "d2"),
            (route_event_summary, "route"),
            (evidence_summary, "evidence"),
        ],
        reason_code="stable_id_conflict",
        remediation_stage="D2",
        message_pass="稳定 ID 冲突数为零。",
        message_fail="稳定 ID 冲突或逐项冲突计数缺失。",
    )

    summary = d2_manifest.get("summary") if isinstance(d2_manifest.get("summary"), Mapping) else {}
    files = d2_manifest.get("files") if isinstance(d2_manifest.get("files"), Mapping) else {}
    duplicate_count = summary.get("duplicate_event_reference_count")
    quarantined_duplicates = summary.get("quarantined_duplicate_event_count")
    collision_count = summary.get("collision_group_count")
    collision_incidents = summary.get("collision_incident_count")
    collision_inventory = files.get("collision_groups.jsonl.gz")
    collision_rows = (
        collision_inventory.get("row_count")
        if isinstance(collision_inventory, Mapping)
        else _MISSING
    )
    collision_ok = (
        all(
            _is_count(value)
            for value in (
                duplicate_count,
                quarantined_duplicates,
                collision_count,
                collision_incidents,
                collision_rows,
            )
        )
        and collision_count == collision_rows
        and collision_incidents >= collision_count * 2
        and duplicate_count == quarantined_duplicates
    )
    collision_failures = [] if collision_ok else [
        _aggregate_failure(
            label="d2",
            path="summary.collision_group_count",
            check_id="uniqueness-source-collisions-explained",
            reason_code="source_fact_collision",
            missing_detail=True,
        )
    ]
    checks.add(
        check_id="uniqueness-source-collisions-explained",
        dimension="uniqueness",
        rule_id="P0-UNIQUE-002",
        title_zh="历史事实主键复用均显式建组",
        status="pass" if collision_ok else "fail",
        severity="blocking",
        scope_ref="normalized:legacy-collision-groups",
        observed_value=collision_count if _is_count(collision_count) else None,
        observed_unit="group_count",
        expected_operator="eq",
        expected_value=collision_rows if _is_count(collision_rows) else 0,
        expected_unit="group_count",
        unknown_count=0 if collision_ok else 1,
        message_zh="每组事实主键复用均进入 collision group。"
        if collision_ok
        else "事实主键复用数与 collision group 明细不闭合。",
        remediation_stage="none" if collision_ok else "D2",
        evidence=[("d2", "summary.collision_group_count"), ("d2", "files.collision_groups.jsonl.gz")],
        failures=collision_failures,
    )

    _add_zero_count_check(
        checks,
        check_id="references-forward-unresolved",
        dimension="referential_integrity",
        rule_id="P0-REF-001",
        title_zh="总表到事实表未解释引用为零",
        scope_ref="normalized:event-to-fact-links",
        specifications=[(d2_manifest, "d2", "summary.unexplained_forward_reference_count")],
        sources_for_samples=[(d2_manifest, "d2")],
        reason_code="dangling_reference",
        remediation_stage="D2",
        message_pass="总表引用全部匹配或被显式解释。",
        message_fail="存在悬空/歧义/时间不一致引用，或缺少可定位明细。",
    )
    _add_zero_count_check(
        checks,
        check_id="references-reverse-unexplained",
        dimension="referential_integrity",
        rule_id="P0-REF-002",
        title_zh="事实表未解释反向孤儿为零",
        scope_ref="normalized:fact-to-event-links",
        specifications=[(d2_manifest, "d2", "summary.unexplained_reverse_orphan_count")],
        sources_for_samples=[(d2_manifest, "d2")],
        reason_code="orphan_reference",
        remediation_stage="D2",
        message_pass="反向孤儿均已进入可复核 quarantine。",
        message_fail="存在未解释事实孤儿，或缺少可定位明细。",
    )
    _add_zero_count_check(
        checks,
        check_id="references-evidence-closure",
        dimension="referential_integrity",
        rule_id="P0-REF-003",
        title_zh="Evidence、阶段与 RouteEvent 引用闭合",
        scope_ref="evidence:v2:registry-closure",
        specifications=[
            (evidence_summary, "evidence", "unresolved_evidence_reference_count"),
            (evidence_summary, "evidence", "unresolved_route_event_reference_count"),
        ],
        sources_for_samples=[(evidence_summary, "evidence")],
        reason_code="unresolved_evidence_reference",
        remediation_stage="D4",
        message_pass="Evidence 注册表、阶段和 RouteEvent 引用完全闭合。",
        message_fail="Evidence 引用未闭合或缺少逐项对账证据。",
    )

    raw_claimed = _raw_claimed(route_event_summary)
    raw_severity = "blocking" if raw_claimed else "warning"
    raw_ref_specs = []
    if route_event_summary is not None:
        raw_ref_specs = [
            (route_event_summary, "route", "raw_reference_unresolved_count"),
            (route_event_summary, "route", "processing_lineage_missing_count"),
        ]
    if raw_ref_specs:
        _add_zero_count_check(
            checks,
            check_id="references-raw-closure",
            dimension="raw_traceability",
            rule_id="P0-RAW-REF-001",
            title_zh="RouteEvent 原始引用与处理血缘闭合",
            scope_ref="route-event:raw-reference-closure",
            specifications=raw_ref_specs,
            sources_for_samples=[(route_event_summary, "route")],
            reason_code="unresolved_raw_reference",
            remediation_stage="D3",
            message_pass="RouteEvent 原始引用与处理血缘完整。",
            message_fail="RouteEvent 原始引用/处理血缘不完整或缺少明细。",
            severity=raw_severity,
        )
    else:
        missing_raw_ref = _aggregate_failure(
            label="route",
            path="raw_reference_unresolved_count",
            check_id="references-raw-closure",
            reason_code="route_event_index_unavailable",
            missing_detail=False,
        )
        checks.add(
            check_id="references-raw-closure",
            dimension="raw_traceability",
            rule_id="P0-RAW-REF-001",
            title_zh="RouteEvent 原始引用与处理血缘闭合",
            status="fail",
            severity="warning",
            scope_ref="route-event:raw-reference-closure",
            observed_value=None,
            observed_unit="record_count",
            expected_operator="eq",
            expected_value=0,
            expected_unit="record_count",
            unknown_count=1,
            message_zh="未提供 RouteEvent 对账摘要，不能声明 raw_traceable。",
            remediation_stage="D3",
            evidence=[("route", "")],
            failures=[missing_raw_ref],
        )

    _add_zero_count_check(
        checks,
        check_id="time-end-before-start",
        dimension="temporal_consistency",
        rule_id="P0-TIME-001",
        title_zh="结束时间早于开始时间的记录为零",
        scope_ref="normalized:incident-time-order",
        specifications=[(d2_manifest, "d2", "summary.end_before_start_count")],
        sources_for_samples=[(d2_manifest, "d2")],
        reason_code="end_before_start",
        remediation_stage="D2",
        message_pass="所有可比较事件满足 end_time >= start_time。",
        message_fail="存在时间逆序或缺少逐项时间质量证据。",
    )
    _add_zero_count_check(
        checks,
        check_id="time-local-utc-verifiable",
        dimension="temporal_consistency",
        rule_id="P0-TIME-002",
        title_zh="历史本地时间到 UTC 转换全部可验证",
        scope_ref="normalized:business-time-to-utc",
        specifications=[(d2_manifest, "d2", "summary.local_utc_unverifiable_count")],
        sources_for_samples=[(d2_manifest, "d2")],
        reason_code="utc_conversion_unverifiable",
        remediation_stage="D2",
        message_pass="本地时间按 Asia/Shanghai 转换并可复核。",
        message_fail="存在无法验证的本地/UTC 转换或缺少计数。",
    )

    coverage = d3_artifact_manifest.get("coverage") if isinstance(d3_artifact_manifest.get("coverage"), Mapping) else {}
    coverage_ratio = coverage.get("coverage_ratio")
    coverage_complete = (
        coverage.get("coverage_status") == "complete"
        and coverage.get("missing_slots") == 0
        and coverage_ratio == 1
    )
    raw_source_failures = [] if coverage_complete else _missing_ranges_failures(d3_artifact_manifest)
    if not coverage_complete and not raw_source_failures:
        raw_source_failures = [
            _aggregate_failure(
                label="d3",
                path="coverage",
                check_id="raw-full-window-source",
                reason_code="source_unavailable",
                missing_detail=True,
            )
        ]
    checks.add(
        check_id="raw-full-window-source",
        dimension="raw_traceability",
        rule_id="P0-RAW-001",
        title_zh="原始制品覆盖完整固定窗口",
        status="pass" if coverage_complete else "fail",
        severity="warning",
        scope_ref="raw-artifacts:profile-window",
        observed_value=coverage_ratio if _is_ratio(coverage_ratio) else None,
        observed_unit="ratio_0_1",
        expected_operator="eq",
        expected_value=1,
        expected_unit="ratio_0_1",
        unknown_count=0 if _is_ratio(coverage_ratio) else 1,
        message_zh="原始制品覆盖固定窗口。"
        if coverage_complete
        else (
            "原始制品仅部分覆盖；未发现文件保持 source_unavailable，"
            "已发现但内容完整性失败的文件保持 parse_failed，最高只能 legacy_compatible。"
        ),
        remediation_stage="none" if coverage_complete else "external",
        evidence=[("d3", "coverage")],
        failures=raw_source_failures,
    )

    route_present = _summary_present(route_event_summary, "route_event_index_summary_v1")
    route_count = _path_get(route_event_summary, "route_event_count") if route_present else _MISSING
    route_index_ok = route_present and _is_count(route_count) and route_count > 0 and raw_claimed
    route_index_failures = [] if route_index_ok else [
        _aggregate_failure(
            label="route",
            path="route_event_count",
            check_id="raw-route-event-index",
            reason_code="route_event_index_unavailable",
            missing_detail=False,
        )
    ]
    checks.add(
        check_id="raw-route-event-index",
        dimension="raw_traceability",
        rule_id="P0-RAW-002",
        title_zh="RouteEvent 原始观测索引存在",
        status="pass" if route_index_ok else "fail",
        severity="warning" if not raw_claimed else "blocking",
        scope_ref="route-event:index-summary",
        observed_value=route_count if _is_count(route_count) else None,
        observed_unit="record_count",
        expected_operator="gte",
        expected_value=1,
        expected_unit="record_count",
        unknown_count=0 if route_present else 1,
        message_zh="RouteEvent 索引包含可复核原始观测。"
        if route_index_ok
        else "RouteEvent 索引未提供或没有可声明 raw_traceable 的记录。",
        remediation_stage="none" if route_index_ok else "D3",
        evidence=[("route", "")],
        failures=route_index_failures,
    )

    raw_reference_value, raw_reference_invalid = _count_values(
        [
            (route_event_summary, "route", "raw_reference_unresolved_count"),
            (route_event_summary, "route", "processing_lineage_missing_count"),
        ]
    ) if route_event_summary is not None else (None, [("route", "raw_reference_unresolved_count", "missing_quality_evidence")])
    raw_reference_ok = route_index_ok and raw_reference_value == 0 and not raw_reference_invalid
    raw_reference_failures = []
    if not raw_reference_ok:
        raw_reference_failures = _normalize_supplied_failures(
            route_event_summary, label="route", check_id="raw-reference-resolvable"
        )
        if not raw_reference_failures:
            raw_reference_failures = [
                _aggregate_failure(
                    label="route",
                    path=raw_reference_invalid[0][1]
                    if raw_reference_invalid
                    else "raw_reference_unresolved_count",
                    check_id="raw-reference-resolvable",
                    reason_code="unresolved_raw_reference"
                    if route_present
                    else "route_event_index_unavailable",
                    missing_detail=raw_claimed,
                )
            ]
    checks.add(
        check_id="raw-reference-resolvable",
        dimension="raw_traceability",
        rule_id="P0-RAW-003",
        title_zh="raw_traceable 原始引用可解析",
        status="pass" if raw_reference_ok else "fail",
        severity="blocking" if raw_claimed and not raw_reference_ok else "warning",
        scope_ref="route-event:raw-refs",
        observed_value=raw_reference_value,
        observed_unit="record_count",
        expected_operator="eq",
        expected_value=0,
        expected_unit="record_count",
        unknown_count=len(raw_reference_invalid),
        message_zh="全部 raw_traceable 原始引用与处理版本可解析。"
        if raw_reference_ok
        else "原始引用不可解析、处理血缘不完整或缺少对账证据。",
        remediation_stage="none" if raw_reference_ok else "D3",
        evidence=[("route", "raw_reference_unresolved_count")],
        failures=raw_reference_failures,
    )

    verification_ok = (
        isinstance(artifact_verification_summary, Mapping)
        and artifact_verification_summary.get("verified") is True
        and artifact_verification_summary.get("artifact_count")
        == _path_get(d3_artifact_manifest, "summary.artifact_count")
        and artifact_verification_summary.get("manifest_fingerprint_sha256")
        == d3_artifact_manifest.get("manifest_fingerprint_sha256")
        and _input_sha(context, "d3") is not None
    )
    record_hash_value = _path_get(route_event_summary, "record_hash_verification_failed_count")
    record_hash_ok = route_index_ok and record_hash_value == 0
    raw_hash_ok = verification_ok and record_hash_ok
    raw_hash_failures = [] if raw_hash_ok else [
        _aggregate_failure(
            label="route" if verification_ok else "d3",
            path="record_hash_verification_failed_count"
            if verification_ok
            else "verification",
            check_id="raw-hashes-verified",
            reason_code="raw_hash_verification_missing"
            if record_hash_value is _MISSING or not verification_ok
            else "raw_hash_mismatch",
            missing_detail=raw_claimed,
        )
    ]
    checks.add(
        check_id="raw-hashes-verified",
        dimension="raw_traceability",
        rule_id="P0-RAW-004",
        title_zh="原始文件与记录哈希校验全部成功",
        status="pass" if raw_hash_ok else "fail",
        severity="blocking" if raw_claimed and not raw_hash_ok else "warning",
        scope_ref="raw-artifact+route-event:hash-verification",
        observed_value=True if raw_hash_ok else False,
        observed_unit="boolean",
        expected_operator="eq",
        expected_value=True,
        expected_unit="boolean",
        unknown_count=0 if raw_hash_ok else 1,
        message_zh="文件级复扫和记录级哈希校验均成功。"
        if raw_hash_ok
        else "缺少完整复扫/记录哈希证据或发现哈希失败。",
        remediation_stage="none" if raw_hash_ok else "D3",
        evidence=[("d3", "manifest_fingerprint_sha256"), ("route", "record_hash_verification_failed_count")],
        failures=raw_hash_failures,
    )

    vp_value, vp_invalid = _count_values(
        [
            (route_event_summary, "route", "vp_identity_missing_count"),
            (route_event_summary, "route", "processing_lineage_missing_count"),
        ]
    ) if route_event_summary is not None else (None, [("route", "vp_identity_missing_count", "missing_quality_evidence")])
    vp_ok = route_index_ok and vp_value == 0 and not vp_invalid
    vp_failures = [] if vp_ok else [
        _aggregate_failure(
            label="route",
            path=vp_invalid[0][1] if vp_invalid else "vp_identity_missing_count",
            check_id="raw-vp-lineage-complete",
            reason_code="vp_or_lineage_missing",
            missing_detail=raw_claimed,
        )
    ]
    checks.add(
        check_id="raw-vp-lineage-complete",
        dimension="raw_traceability",
        rule_id="P0-RAW-005",
        title_zh="raw_traceable VP 身份与处理版本完整",
        status="pass" if vp_ok else "fail",
        severity="blocking" if raw_claimed and not vp_ok else "warning",
        scope_ref="route-event:vp-processing-lineage",
        observed_value=vp_value,
        observed_unit="record_count",
        expected_operator="eq",
        expected_value=0,
        expected_unit="record_count",
        unknown_count=len(vp_invalid),
        message_zh="每条 raw_traceable 记录均有稳定 VP 和处理版本。"
        if vp_ok
        else "VP 身份/处理版本不完整或没有逐项证据。",
        remediation_stage="none" if vp_ok else "D3",
        evidence=[("route", "vp_identity_missing_count")],
        failures=vp_failures,
    )

    missing_slots = coverage.get("missing_slots")
    ranges = coverage.get("missing_ranges")
    classified_slots = (
        sum(row.get("slot_count", 0) for row in ranges if isinstance(row, Mapping))
        if isinstance(ranges, list)
        else None
    )
    metric_unclassified = _path_get(metric_summary, "unclassified_gap_count")
    gaps_ok = (
        _is_count(missing_slots)
        and classified_slots == missing_slots
        and metric_unclassified == 0
    )
    gaps_failures = [] if gaps_ok else [
        _aggregate_failure(
            label="metric" if metric_unclassified is _MISSING or metric_unclassified != 0 else "d3",
            path="unclassified_gap_count"
            if metric_unclassified is _MISSING or metric_unclassified != 0
            else "coverage.missing_ranges",
            check_id="coverage-gaps-classified",
            reason_code="unclassified_missingness",
            missing_detail=True,
        )
    ]
    checks.add(
        check_id="coverage-gaps-classified",
        dimension="sample_coverage",
        rule_id="P0-COVERAGE-001",
        title_zh="来源与派生缺口完成分类",
        status="pass" if gaps_ok else "fail",
        severity="blocking",
        scope_ref="raw+metric:expected-time-slots",
        observed_value=True if gaps_ok else False,
        observed_unit="boolean",
        expected_operator="classified_only",
        expected_value=True,
        expected_unit="boolean",
        unknown_count=0 if gaps_ok else 1,
        message_zh="低覆盖保留真实比例，所有源缺口和处理缺口均已分类。"
        if gaps_ok
        else "缺口数量、范围或原因未完全闭合。",
        remediation_stage="none" if gaps_ok else "D2",
        evidence=[("d3", "coverage.missing_ranges"), ("metric", "unclassified_gap_count")],
        failures=gaps_failures,
    )

    assurance_present = _summary_present(
        reproducibility_summary, "p0_single_run_assurance_v1"
    )
    assurance_views: Optional[Mapping[str, Any]] = None
    if assurance_present:
        assurance_views = _single_run_assurance_views(
            checks, reproducibility_summary, context
        )
        repro_present = True
        execution_scope = assurance_views["execution_scope"]
        byte_identity = assurance_views["byte_identity"]
        semantic = assurance_views["semantic_validation"]
        full_semantic = assurance_views["full_semantic_validation"]
        conclusion = assurance_views["conclusion"]
    else:
        repro_present = _summary_present(
            reproducibility_summary, "p0_reproducibility_summary_v2"
        )
        execution_scope = _path_get(reproducibility_summary, "execution_scope") if repro_present else _MISSING
        byte_identity = _path_get(reproducibility_summary, "byte_identity") if repro_present else _MISSING
        semantic = _path_get(reproducibility_summary, "semantic_validation") if repro_present else _MISSING
        full_semantic = _path_get(reproducibility_summary, "full_semantic_validation") if repro_present else _MISSING
        conclusion = _path_get(reproducibility_summary, "conclusion") if repro_present else _MISSING

    byte_components = byte_identity.get("components") if isinstance(byte_identity, Mapping) else None
    byte_components_ok = isinstance(byte_components, Mapping) and bool(byte_components)
    if byte_components_ok:
        for component in byte_components.values():
            if not isinstance(component, Mapping) or any(
                (
                    not _is_sha256(component.get(field))
                    for field in ("a_sha256sums_sha256", "b_sha256sums_sha256")
                )
            ) or any(
                isinstance(component.get(field), bool)
                or not isinstance(component.get(field), int)
                or component.get(field) < 0
                for field in (
                    "a_signed_file_count",
                    "b_signed_file_count",
                    "a_signed_size_bytes",
                    "b_signed_size_bytes",
                    "mismatch_count",
                )
            ) or component.get("a_signed_file_count") != component.get("b_signed_file_count") \
                    or component.get("a_signed_size_bytes") != component.get("b_signed_size_bytes") \
                    or component.get("sha256sums_bytes_match") is not True \
                    or component.get("mismatch_count") != 0 \
                    or component.get("mismatched_files") != []:
                byte_components_ok = False
                break
    byte_ok = (
        repro_present
        and isinstance(byte_identity, Mapping)
        and byte_identity.get("scope") == "full_artifact_closure"
        and byte_identity.get("all_files_rehashed") is True
        and byte_identity.get("all_corresponding_files_match") is True
        and byte_components_ok
    )
    byte_failures = [] if byte_ok else [
        _aggregate_failure(
            label="repro",
            path="byte_identity",
            check_id="reproducibility-byte-identity",
            reason_code="missing_quality_evidence" if not repro_present else "deterministic_summary_mismatch",
            missing_detail=True,
        )
    ]
    checks.add(
        check_id="reproducibility-byte-identity",
        dimension="reproducibility",
        rule_id="P0-REPRO-001",
        title_zh="D2 64 条 A/B 样本逐文件字节身份一致"
        if assurance_present
        else "A/B 全制品逐文件字节身份一致",
        status="pass" if byte_ok else "fail",
        severity="blocking",
        scope_ref="candidate:d2-bounded-sample-closure"
        if assurance_present
        else "candidate:full-signed-artifact-closure",
        observed_value=True if byte_ok else False,
        observed_unit="boolean",
        expected_operator="eq",
        expected_value=True,
        expected_unit="boolean",
        unknown_count=0 if repro_present else 1,
        message_zh="D2 两份 64 条样本的所有已签名文件均重新计算 SHA256 且零差异。"
        if assurance_present and byte_ok
        else ("A/B 所有已签名文件均重新计算 SHA256 且对应文件零差异。"
        if byte_ok
        else "缺少全目录闭包重哈希证据或 A/B 文件字节身份存在差异。"),
        remediation_stage="none" if byte_ok else "D5",
        evidence=[("repro", "byte_identity")],
        failures=byte_failures,
    )

    metric_deterministic = _path_get(metric_summary, "deterministic_summary_match")
    metric_deterministic_scope = _path_get(
        metric_summary, "deterministic_summary_scope"
    )
    metric_cross_run_claimed = _path_get(
        metric_summary, "cross_run_reproducibility_claimed"
    )
    metric_roundtrip_ok = (
        metric_deterministic is True
        and metric_deterministic_scope
        == "internal_memory_vs_emitted_roundtrip_only"
        and metric_cross_run_claimed is False
    )
    semantic_mode = semantic.get("mode") if isinstance(semantic, Mapping) else _MISSING
    stable_ratio = semantic.get("stable_id_match_ratio") if isinstance(semantic, Mapping) else _MISSING
    fingerprint_matches = semantic.get("fingerprint_matches") if isinstance(semantic, Mapping) else None
    d2_samples = semantic.get("d2_sample_comparison") if isinstance(semantic, Mapping) else None
    sample_rows_ok = isinstance(d2_samples, Mapping) and all(
        isinstance(row, Mapping)
        and row.get("match") is True
        and _is_sha256(row.get("a_content_sha256"))
        and _is_sha256(row.get("b_content_sha256"))
        and row.get("a_selected_count") == row.get("b_selected_count")
        for row in d2_samples.values()
    )
    semantic_ok = (
        repro_present
        and isinstance(semantic, Mapping)
        and semantic_mode in ("deterministic_bounded_sample_v1", "full_population_v1")
        and _is_ratio(stable_ratio)
        and stable_ratio == 1
        and semantic.get("record_count_metadata_match") is True
        and semantic.get("aggregate_summary_match") is True
        and isinstance(fingerprint_matches, Mapping)
        and bool(fingerprint_matches)
        and all(value is True for value in fingerprint_matches.values())
        and semantic.get("failure_count") == 0
        and semantic.get("all_results_match") is True
        and (sample_rows_ok if semantic_mode == "deterministic_bounded_sample_v1" else d2_samples == {})
        and metric_roundtrip_ok
        and (
            not assurance_present
            or (
                isinstance(assurance_views, Mapping)
                and assurance_views.get("bounded_ok") is True
            )
        )
    )
    semantic_failures = [] if semantic_ok else [
        _aggregate_failure(
            label="metric" if not metric_roundtrip_ok else "repro",
            path="deterministic_summary_match"
            if not metric_roundtrip_ok
            else "semantic_validation",
            check_id="reproducibility-sampled-semantics",
            reason_code="missing_quality_evidence"
            if not repro_present
            else "deterministic_summary_mismatch",
            missing_detail=True,
        )
    ]
    checks.add(
        check_id="reproducibility-sampled-semantics",
        dimension="reproducibility",
        rule_id="P0-REPRO-002",
        title_zh="A/B 有界语义抽样与小型候选复核一致",
        status="pass" if semantic_ok else "fail",
        severity="blocking",
        scope_ref="candidate:bounded-semantic-validation",
        observed_value=True if semantic_ok else False,
        observed_unit="boolean",
        expected_operator="eq",
        expected_value=True,
        expected_unit="boolean",
        unknown_count=(0 if repro_present else 1)
        + (0 if metric_roundtrip_ok else 1),
        message_zh="D2 64 条真实双跑样本的稳定 ID、计数、摘要与候选字节一致。"
        if assurance_present and semantic_ok
        else ("冻结的 D2 前缀样本、D3 元数据、六类 Evidence 样本、Metric 候选与 RouteEvent pilot 复核一致。"
        if semantic_ok
        else "有界语义计划、稳定 ID、摘要、指纹或 Metric 内部往返证据不一致。"),
        remediation_stage="none" if semantic_ok else "D5",
        evidence=[("repro", "semantic_validation"), ("metric", "deterministic_summary_scope")],
        failures=semantic_failures,
    )

    scope_is_sample = (
        isinstance(execution_scope, Mapping)
        and execution_scope.get("candidates_regenerated") is False
        and execution_scope.get("source_database_access") == "none"
        and execution_scope.get("source_database_connection_attempts") == 0
        and execution_scope.get("source_database_write_operations") == 0
        and execution_scope.get("raw_mrt_access") == "none"
        and isinstance(semantic, Mapping)
        and semantic_mode == "deterministic_bounded_sample_v1"
        and semantic.get("sample_only") is True
        and semantic.get("population_coverage_claimed") is False
        and isinstance(full_semantic, Mapping)
        and full_semantic.get("status") == "not_run"
        and full_semantic.get("reason") == "user_requested_bounded_sample"
        and isinstance(conclusion, Mapping)
        and conclusion.get("byte_reproducibility_status") == "passed"
        and conclusion.get("sampled_semantic_status") == "passed"
        and conclusion.get("full_semantic_reproducibility_status") == "not_run"
        and (
            not assurance_present
            or (
                isinstance(assurance_views, Mapping)
                and assurance_views.get("scope_ok") is True
            )
        )
    )
    scope_is_full = (
        isinstance(execution_scope, Mapping)
        and execution_scope.get("candidates_regenerated") is False
        and execution_scope.get("source_database_access") == "none"
        and execution_scope.get("source_database_connection_attempts") == 0
        and execution_scope.get("source_database_write_operations") == 0
        and execution_scope.get("raw_mrt_access") == "none"
        and isinstance(semantic, Mapping)
        and semantic_mode == "full_population_v1"
        and semantic.get("sample_only") is False
        and semantic.get("population_coverage_claimed") is True
        and isinstance(full_semantic, Mapping)
        and full_semantic.get("status") == "passed"
        and isinstance(conclusion, Mapping)
        and conclusion.get("full_semantic_reproducibility_status") == "passed"
    )
    scope_ok = scope_is_sample or scope_is_full
    scope_failures = [] if scope_ok else [
        _aggregate_failure(
            label="repro",
            path="execution_scope",
            check_id="reproducibility-scope-integrity",
            reason_code="missing_quality_evidence",
            missing_detail=True,
        )
    ]
    checks.add(
        check_id="reproducibility-scope-integrity",
        dimension="reproducibility",
        rule_id="P0-REPRO-003",
        title_zh="复现执行与总体覆盖边界内部一致",
        status="pass" if scope_ok else "fail",
        severity="blocking",
        scope_ref="candidate:reproducibility-scope",
        observed_value=True if scope_ok else False,
        observed_unit="boolean",
        expected_operator="eq",
        expected_value=True,
        expected_unit="boolean",
        unknown_count=0 if scope_ok else 1,
        message_zh="本轮未重建候选、未访问数据库或原始 MRT，抽样不声明总体覆盖。"
        if scope_is_sample
        else ("全量语义扫描范围声明与结论一致。" if scope_is_full else "复现范围或结论字段互相矛盾。"),
        remediation_stage="none" if scope_ok else "D5",
        evidence=[("repro", "execution_scope"), ("repro", "semantic_validation")],
        failures=scope_failures,
    )

    full_semantic_ok = scope_is_full
    full_semantic_pending = scope_is_sample
    full_semantic_failures = [] if full_semantic_ok or full_semantic_pending else [
        _aggregate_failure(
            label="repro",
            path="full_semantic_validation",
            check_id="reproducibility-full-semantic-validation",
            reason_code="missing_quality_evidence",
            missing_detail=True,
        )
    ]
    checks.add(
        check_id="reproducibility-full-semantic-validation",
        dimension="reproducibility",
        rule_id="P0-REPRO-004",
        title_zh="A/B 全量语义复现",
        status="pass" if full_semantic_ok else ("pending" if full_semantic_pending else "fail"),
        severity="warning" if full_semantic_pending else "blocking",
        scope_ref="candidate:full-semantic-population",
        observed_value="passed" if full_semantic_ok else ("not_run" if full_semantic_pending else None),
        observed_unit="identity_level",
        expected_operator="eq",
        expected_value="passed",
        expected_unit="identity_level",
        unknown_count=0 if full_semantic_ok else 1,
        message_zh="已执行 A/B 全量语义复现。"
        if full_semantic_ok
        else (
            "按用户约束未再次执行真实 A/B 全量语义复现；该项保持待定，不得由抽样替代。"
            if full_semantic_pending
            else "全量语义复现状态缺失或范围声明非法。"
        ),
        remediation_stage="none" if full_semantic_ok else "D5",
        evidence=[("repro", "full_semantic_validation")],
        failures=full_semantic_failures,
    )

    _add_zero_count_check(
        checks,
        check_id="reproducibility-metric-reconciliation",
        dimension="reproducibility",
        rule_id="P0-METRIC-002",
        title_zh="指标精确计数对账差异为零",
        scope_ref="metric-series:source-reconciliation",
        specifications=[(metric_summary, "metric", "reconciliation_difference_count")],
        sources_for_samples=[(metric_summary, "metric")],
        reason_code="metric_reconciliation_mismatch",
        remediation_stage="D2",
        message_pass="指标记录数、公式输入和聚合输出对账一致。",
        message_fail="指标对账存在差异或缺少可定位证据。",
    )

    phase_value, phase_invalid = _count_values(
        [
            (d2_manifest, "d2", "summary.phase_state_missing_count"),
            (d2_manifest, "d2", "summary.phase_missing_reason_count"),
        ]
    )
    phase_counts = _path_get(d2_manifest, "summary.event_type_counts")
    phase_types_ok = isinstance(phase_counts, Mapping) and set(phase_counts) == set(EVENT_TYPES)
    phase_ok = phase_value == 0 and not phase_invalid and phase_types_ok
    phase_failures = [] if phase_ok else [
        _aggregate_failure(
            label="d2",
            path=phase_invalid[0][1] if phase_invalid else "summary.event_type_counts",
            check_id="phase-six-event-coverage",
            reason_code="phase_missing_reason_absent",
            missing_detail=True,
        )
    ]
    checks.add(
        check_id="phase-six-event-coverage",
        dimension="event_phase_coverage",
        rule_id="P0-PHASE-001",
        title_zh="六类事件阶段状态与缺失原因覆盖完整",
        status="pass" if phase_ok else "fail",
        severity="blocking",
        scope_ref="normalized:six-event-types:phase-state",
        observed_value=1 if phase_ok else None,
        observed_unit="ratio_0_1",
        expected_operator="eq",
        expected_value=1,
        expected_unit="ratio_0_1",
        unknown_count=len(phase_invalid) + (0 if phase_types_ok else 1),
        message_zh="六类事件全部给出适用阶段状态和缺失原因。"
        if phase_ok
        else "阶段状态/缺失原因计数缺失或六类事件范围不完整。",
        remediation_stage="none" if phase_ok else "D2",
        evidence=[("d2", "summary.phase_state_missing_count")],
        failures=phase_failures,
    )

    outside_specs = [(d2_manifest, "d2", "summary.visible_outside_window_count")]
    if route_event_summary is not None:
        outside_specs.append((route_event_summary, "route", "outside_window_record_count"))
    outside_specs.extend(
        [
            (evidence_summary, "evidence", "outside_window_record_count"),
            (metric_summary, "metric", "outside_window_point_count"),
        ]
    )
    _add_zero_count_check(
        checks,
        check_id="window-outside-visible-records",
        dimension="fixed_window_bounds",
        rule_id="P0-WINDOW-001",
        title_zh="固定窗口外可见记录为零",
        scope_ref="normalized+route+evidence+metric:profile-window",
        specifications=outside_specs,
        sources_for_samples=[
            (d2_manifest, "d2"),
            (route_event_summary, "route"),
            (evidence_summary, "evidence"),
            (metric_summary, "metric"),
        ],
        reason_code="outside_fixed_window",
        remediation_stage="D2",
        message_pass="窗口污染只保留在 quarantine，不进入可见数据层。",
        message_fail="存在窗口外可见记录或缺少逐层窗口对账。",
    )

    _add_zero_count_check(
        checks,
        check_id="missing-reason-complete",
        dimension="unknown_missingness",
        rule_id="P0-MISSING-001",
        title_zh="派生空值均有明确缺失原因",
        scope_ref="normalized+evidence+metric:nullable-fields",
        specifications=[
            (d2_manifest, "d2", "summary.unknown_derived_null_count"),
            (evidence_summary, "evidence", "unknown_missing_reason_count"),
            (metric_summary, "metric", "unknown_missing_reason_count"),
        ],
        sources_for_samples=[
            (d2_manifest, "d2"),
            (evidence_summary, "evidence"),
            (metric_summary, "metric"),
        ],
        reason_code="unknown_missing_reason",
        remediation_stage="D2",
        message_pass="所有派生空值均带冻结 value_state 和 missing_reason。",
        message_fail="存在未说明空值或缺少逐层缺失语义对账。",
    )

    policy_no_zero = _path_get(
        d2_manifest, "materialization_policy.missing_values_coerced_to_zero"
    ) is False
    zero_value, zero_invalid = _count_values(
        [
            (d2_manifest, "d2", "summary.confirmed_missing_zero_fill_count"),
            (evidence_summary, "evidence", "auto_zero_fill_count"),
            (metric_summary, "metric", "confirmed_missing_zero_fill_count"),
        ]
    )
    zero_ok = policy_no_zero and zero_value == 0 and not zero_invalid
    zero_failures = [] if zero_ok else [
        _aggregate_failure(
            label=zero_invalid[0][0] if zero_invalid else "d2",
            path=zero_invalid[0][1]
            if zero_invalid
            else "materialization_policy.missing_values_coerced_to_zero",
            check_id="missing-no-zero-fill",
            reason_code="confirmed_missing_zero_fill",
            missing_detail=True,
        )
    ]
    checks.add(
        check_id="missing-no-zero-fill",
        dimension="unknown_missingness",
        rule_id="P0-MISSING-002",
        title_zh="已确认缺测补零记录为零",
        status="pass" if zero_ok else "fail",
        severity="blocking",
        scope_ref="normalized+evidence+metric:zero-semantics",
        observed_value=zero_value,
        observed_unit="record_count",
        expected_operator="eq",
        expected_value=0,
        expected_unit="record_count",
        unknown_count=len(zero_invalid),
        message_zh="缺测、不可计算和查询失败均未被补成 observed_zero。"
        if zero_ok
        else "发现缺测补零或缺少可复核零值语义证据。",
        remediation_stage="none" if zero_ok else "D2",
        evidence=[
            ("d2", "materialization_policy.missing_values_coerced_to_zero"),
            ("evidence", "auto_zero_fill_count"),
            ("metric", "confirmed_missing_zero_fill_count"),
        ],
        failures=zero_failures,
    )

    dimensions = _build_dimensions(checks.checks)
    gate = _build_gate(checks.checks)
    check_summary = _check_summary(checks.checks)
    input_identity = {
        "schema": INPUT_FINGERPRINT_SCHEMA,
        "d2": d2_manifest,
        "d3": d3_artifact_manifest,
        "route": route_event_summary,
        "artifact_verification": artifact_verification_summary,
        "evidence": evidence_summary,
        "metric": metric_summary,
        "repro": reproducibility_summary,
        "context": context,
    }
    input_fingerprint = _canonical_sha256(input_identity)
    report = {
        "schema_version": SCHEMA_VERSION,
        "report_id": f"dqr-{data_profile['profile_id']}-{input_fingerprint[:16]}",
        "data_profile": data_profile,
        "source_release": source_release,
        "generator": {
            "name": GENERATOR_NAME,
            "version": GENERATOR_VERSION,
            "source_sha256": _source_sha256(),
        },
        "execution": execution,
        "dimensions": dimensions,
        "checks": checks.checks,
        "check_summary": check_summary,
        "gate": gate,
        "generated_at": generated_at,
    }
    report["report_fingerprint_sha256"] = _canonical_sha256(
        {"schema": REPORT_FINGERPRINT_SCHEMA, "report": report}
    )
    validate_report_semantics(report)
    ordered_failures = tuple(
        sorted(
            checks.failure_details,
            key=lambda row: (
                row["check_id"],
                row["source"],
                row["key"],
                row["field"],
            ),
        )
    )
    return QualityGateResult(report=report, failure_details_zh=ordered_failures)


__all__ = [
    "D2_REQUIRED_QUALITY_FIELDS",
    "EVIDENCE_REQUIRED_QUALITY_FIELDS",
    "METRIC_REQUIRED_QUALITY_FIELDS",
    "QualityGateInputError",
    "QualityGateResult",
    "REPRODUCIBILITY_REQUIRED_FIELDS",
    "ROUTE_EVENT_REQUIRED_QUALITY_FIELDS",
    "build_quality_report",
    "canonical_json",
    "validate_report_semantics",
]
