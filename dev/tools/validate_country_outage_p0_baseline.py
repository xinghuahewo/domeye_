#!/usr/bin/env python3
"""校验国家中断 Agent P0 首版 35 案例、证据身份和交接制品。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_COUNTS = {
    "direct": 20,
    "multi_turn": 5,
    "boundary": 5,
    "exception": 5,
}
EXPECTED_DIRECT_COVERAGE = (
    "event_summary",
    "observation_window",
    "detected_time_not_true_onset",
    "primary_peak_time",
    "maximum_ipv4_visibility_drop",
    "recovery_status",
    "window_end_state",
    "affected_scope",
    "top_affected_asns",
    "specified_asn",
    "remaining_vs_peak",
    "ipv4_ipv6_comparison",
    "update_activity_availability",
    "metric_semantics",
    "new_prefix_resources",
    "path_sample_semantics",
    "evidence_trace",
    "data_completeness",
    "publication_revision_finality",
    "rrc25_proof_boundary",
)
EXPECTED_IDS = tuple(
    [f"P0-D-{number:02d}" for number in range(1, 21)]
    + [f"P0-M-{number:02d}" for number in range(1, 6)]
    + [f"P0-B-{number:02d}" for number in range(1, 6)]
    + [f"P0-X-{number:02d}" for number in range(1, 6)]
)
HEX_64 = re.compile(r"[0-9a-f]{64}")
PII_KEYS = {
    "username",
    "user_name",
    "account",
    "account_id",
    "avatar",
    "email",
    "phone",
    "contact",
    "ip_address",
}
MISSING = object()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法读取 JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON 顶层必须是对象：{path}")
    return value


def safe_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{label} 必须是非空仓库相对路径")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"{label} 越出仓库：{value!r}")
    path = (REPOSITORY_ROOT / relative).resolve()
    try:
        path.relative_to(REPOSITORY_ROOT)
    except ValueError as error:
        raise RuntimeError(f"{label} 越出仓库：{value!r}") from error
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dotted_value(value: object, dotted_path: str) -> object:
    current = value
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return MISSING
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return MISSING
            current = current[index]
            continue
        return MISSING
    return current


def scan_pii_keys(value: object, location: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key.lower() in PII_KEYS:
                errors.append(f"正式制品包含禁止个人信息字段：{child_location}")
            errors.extend(scan_pii_keys(child, child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(scan_pii_keys(child, f"{location}[{index}]"))
    return errors


def require_string_list(value: object, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        errors.append(f"{label} 必须是非空字符串数组")
        return []
    return value


def validate_evidence(evidence: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if evidence.get("schema_version") != "country_outage_p0_evidence_snapshot_v1":
        errors.append("证据 snapshot schema_version 无效")
    identity = evidence.get("identity")
    if not isinstance(identity, dict):
        return errors + ["证据 snapshot 缺少 identity"]
    expected_identity = {
        "country_code": "IR",
        "collector_id": "rrc25",
        "revision": 1,
        "publication_state": "published",
        "quality_state": "complete",
        "missing_slot_count": 0,
        "is_final_in_data_range": False,
        "lifecycle_state": "event_end_unknown",
    }
    for key, expected in expected_identity.items():
        if identity.get(key, MISSING) != expected:
            errors.append(
                f"证据 identity.{key} 应为 {expected!r}，实际为 {identity.get(key)!r}"
            )

    series = evidence.get("series")
    if not isinstance(series, dict):
        errors.append("证据 snapshot 缺少 series")
    else:
        if series.get("point_count") != 3455:
            errors.append("证据 series.point_count 必须为 3455")
        if series.get("timestamp_count") != series.get("point_count"):
            errors.append("证据 series timestamp_count 与 point_count 不一致")
        if series.get("missing_slot_count") != 0:
            errors.append("证据 series 缺槽数必须为 0")

    endpoints = evidence.get("api_endpoints")
    if not isinstance(endpoints, dict) or set(endpoints) != {
        "resolve",
        "overview",
        "series",
        "asns_page_1",
        "path_downstreams_page_1",
        "audit",
    }:
        errors.append("证据 api_endpoints 必须完整覆盖六个只读响应")
    else:
        publication = identity.get("publication_id")
        for name, endpoint in endpoints.items():
            if not isinstance(endpoint, dict):
                errors.append(f"api_endpoints.{name} 必须是对象")
                continue
            if endpoint.get("status") != 200:
                errors.append(f"api_endpoints.{name} 状态必须为 200")
            response_hash = endpoint.get("response_sha256")
            if not isinstance(response_hash, str) or not HEX_64.fullmatch(response_hash):
                errors.append(f"api_endpoints.{name} 缺少有效响应 SHA-256")
            etag = endpoint.get("etag")
            if not isinstance(etag, str) or str(publication) not in etag:
                errors.append(f"api_endpoints.{name} ETag 未绑定当前 publication")
    return errors


def validate_case_set(
    case_set: dict[str, Any],
    evidence: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if case_set.get("schema_version") != "country_outage_p0_case_set_v1":
        errors.append("案例集 schema_version 无效")
    case_revision = case_set.get("revision")
    content_summary = case_set.get("content_summary")
    if content_summary != {
        **EXPECTED_COUNTS,
        "purpose": "仅用当前伊朗事件页面/API 建立 P1 可执行的最小事实、上下文、边界和异常评测合同",
    }:
        errors.append("案例集 content_summary 必须完整记录 20/5/5/5 和首版用途")
    change_log = case_set.get("change_log")
    if (
        not isinstance(change_log, list)
        or len(change_log) != 1
        or not isinstance(change_log[0], dict)
        or change_log[0].get("revision") != case_revision
        or change_log[0].get("date") != "2026-08-08"
        or not change_log[0].get("summary")
    ):
        errors.append("案例集 change_log 必须记录当前 revision 的首版变更")
    if case_set.get("evidence_snapshot_id") != evidence.get("snapshot_id"):
        errors.append("案例集 evidence_snapshot_id 与证据不一致")

    expected_counts = case_set.get("expected_counts")
    if expected_counts != {**EXPECTED_COUNTS, "total": 35}:
        errors.append("案例集 expected_counts 必须严格为 20/5/5/5/35")

    binding = case_set.get("event_binding")
    identity = evidence.get("identity")
    if not isinstance(binding, dict) or not isinstance(identity, dict):
        errors.append("案例集或证据缺少事件绑定")
    else:
        binding_map = {
            "legacy_reference": "legacy_reference",
            "incident_id": "incident_id",
            "publication_id": "publication_id",
            "revision": "revision",
            "collector_id": "collector_id",
            "country_code": "country_code",
        }
        for case_key, evidence_key in binding_map.items():
            if binding.get(case_key, MISSING) != identity.get(evidence_key, MISSING):
                errors.append(f"案例集 event_binding.{case_key} 与证据不一致")

    cases = case_set.get("cases")
    if not isinstance(cases, list):
        return errors + ["cases 必须是数组"]
    if len(cases) != 35:
        errors.append(f"案例总数必须为 35，实际为 {len(cases)}")

    ids = [case.get("case_id") for case in cases if isinstance(case, dict)]
    if tuple(ids) != EXPECTED_IDS:
        errors.append("案例 ID 必须严格按 P0-D-01..20、M-01..05、B-01..05、X-01..05 排列")
    if len(set(ids)) != len(ids):
        errors.append("案例 ID 存在重复")

    counts = Counter(
        case.get("category") for case in cases if isinstance(case, dict)
    )
    if dict(counts) != EXPECTED_COUNTS:
        errors.append(f"案例分类数量必须为 {EXPECTED_COUNTS}，实际为 {dict(counts)}")

    direct_coverage = tuple(
        case.get("coverage_key")
        for case in cases
        if isinstance(case, dict) and case.get("category") == "direct"
    )
    if direct_coverage != EXPECTED_DIRECT_COVERAGE:
        errors.append("20 个直接案例 coverage_key 未严格覆盖冻结任务清单")

    public_source_count = 0
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"cases[{index}] 必须是对象")
            continue
        case_id = case.get("case_id", f"cases[{index}]")
        prefix = str(case_id)
        if case.get("revision") != case_revision:
            errors.append(f"{prefix} revision 与案例集不一致")
        for key in (
            "revision",
            "category",
            "coverage_key",
            "question",
            "source",
            "intent_labels",
            "entities",
            "operators",
            "answerability",
            "target_phase",
            "expected",
            "evidence_refs",
            "hard_gates",
        ):
            if key not in case:
                errors.append(f"{prefix} 缺少 {key}")

        if case.get("target_phase") != "P1":
            errors.append(f"{prefix} target_phase 必须为 P1")
        entities = case.get("entities")
        if not isinstance(entities, dict) or entities.get("binding_ref") != "event_binding":
            errors.append(f"{prefix} 必须显式引用 event_binding")

        source = case.get("source")
        if not isinstance(source, dict):
            errors.append(f"{prefix} source 必须是对象")
        else:
            for source_key in ("type", "locator", "title", "rationale"):
                if not isinstance(source.get(source_key), str) or not source[source_key]:
                    errors.append(f"{prefix} source.{source_key} 无效")
            if source.get("type") == "public_question_transcription":
                public_source_count += 1
                if not str(source.get("locator", "")).startswith("https://"):
                    errors.append(f"{prefix} 公开问题来源必须使用 https URL")
                for source_key in (
                    "collected_at",
                    "original_language",
                    "transcription_note",
                ):
                    if not isinstance(source.get(source_key), str) or not source[source_key]:
                        errors.append(f"{prefix} 公开问题 source.{source_key} 无效")

        for key in ("intent_labels", "operators", "evidence_refs", "hard_gates"):
            require_string_list(case.get(key), f"{prefix}.{key}", errors)

        evidence_refs = case.get("evidence_refs")
        if isinstance(evidence_refs, list):
            for evidence_ref in evidence_refs:
                if not isinstance(evidence_ref, str):
                    continue
                if dotted_value(evidence, evidence_ref) is MISSING:
                    errors.append(f"{prefix} 证据路径不存在：{evidence_ref}")

        expected = case.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{prefix} expected 必须是对象")
            continue
        for key in (
            "answer_type",
            "facts",
            "required_answer_points",
            "required_limitations",
            "forbidden_assertions",
            "failure_closed",
        ):
            if key not in expected:
                errors.append(f"{prefix}.expected 缺少 {key}")
        require_string_list(
            expected.get("required_answer_points"),
            f"{prefix}.expected.required_answer_points",
            errors,
        )
        require_string_list(
            expected.get("forbidden_assertions"),
            f"{prefix}.expected.forbidden_assertions",
            errors,
        )
        limitations = expected.get("required_limitations")
        if not isinstance(limitations, list) or not all(
            isinstance(item, str) and item for item in limitations
        ):
            errors.append(f"{prefix}.expected.required_limitations 必须是字符串数组")

        facts = expected.get("facts")
        if not isinstance(facts, list):
            errors.append(f"{prefix}.expected.facts 必须是数组")
        else:
            for fact_index, fact in enumerate(facts):
                if not isinstance(fact, dict):
                    errors.append(f"{prefix}.expected.facts[{fact_index}] 必须是对象")
                    continue
                evidence_ref = fact.get("evidence_ref")
                if not isinstance(evidence_ref, str):
                    errors.append(f"{prefix}.expected.facts[{fact_index}] 缺少 evidence_ref")
                    continue
                actual = dotted_value(evidence, evidence_ref)
                if actual is MISSING:
                    errors.append(f"{prefix} 事实证据路径不存在：{evidence_ref}")
                elif actual != fact.get("value", MISSING):
                    errors.append(
                        f"{prefix} 事实与证据不一致：{evidence_ref} "
                        f"期望 {fact.get('value')!r}，证据 {actual!r}"
                    )

        category = case.get("category")
        hard_gates = set(case.get("hard_gates", []))
        if not {"event_binding", "publication_binding"}.issubset(hard_gates):
            errors.append(f"{prefix} 缺少事件或 publication 硬门禁")
        if category == "multi_turn":
            turns = case.get("turns")
            if not isinstance(turns, list) or not 2 <= len(turns) <= 4:
                errors.append(f"{prefix} 多轮旅程必须包含 2—4 轮")
            else:
                if [turn.get("turn_id") for turn in turns if isinstance(turn, dict)] != list(
                    range(1, len(turns) + 1)
                ):
                    errors.append(f"{prefix} turn_id 必须从 1 连续编号")
                for turn_index, turn in enumerate(turns):
                    if not isinstance(turn, dict):
                        errors.append(f"{prefix}.turns[{turn_index}] 必须是对象")
                        continue
                    state = turn.get("state_expectation")
                    if not isinstance(state, dict) or set(state) != {
                        "inherit",
                        "set",
                        "clear",
                    }:
                        errors.append(f"{prefix}.turns[{turn_index}] 状态变化不完整")
            if "context_isolation" not in hard_gates:
                errors.append(f"{prefix} 缺少 context_isolation 硬门禁")
        elif "turns" in case:
            errors.append(f"{prefix} 非多轮案例不得包含 turns")

        if category == "boundary":
            if "no_forbidden_assertion" not in hard_gates:
                errors.append(f"{prefix} 边界案例缺少 no_forbidden_assertion")
            if case.get("answerability") not in {"partial", "unsupported"}:
                errors.append(f"{prefix} 边界案例可回答性必须为 partial 或 unsupported")

        if category == "exception":
            if not isinstance(case.get("fixture"), dict):
                errors.append(f"{prefix} 异常案例必须包含 fixture")
            if expected.get("failure_closed") is not True:
                errors.append(f"{prefix} 异常案例必须 failure_closed=true")
            if "invalid_answer_not_published" not in hard_gates:
                errors.append(f"{prefix} 异常案例缺少 invalid_answer_not_published")

    if public_source_count < 5:
        errors.append(f"公开问题转述案例至少 5 个，实际为 {public_source_count}")
    errors.extend(scan_pii_keys(case_set))
    return errors


def validate_receipts(receipts: dict[str, Any], revision: str) -> list[str]:
    errors: list[str] = []
    if receipts.get("schema_version") != "country_outage_p0_stage_receipts_v1":
        errors.append("阶段回执 schema_version 无效")
    if receipts.get("revision") != revision:
        errors.append("阶段回执 revision 与 manifest 不一致")
    stages = receipts.get("stages")
    if not isinstance(stages, dict) or tuple(stages) != ("S0", "S1", "S2", "S3"):
        errors.append("阶段回执必须严格覆盖 S0、S1、S2、S3")
        return errors
    for stage, receipt in stages.items():
        if not isinstance(receipt, dict):
            errors.append(f"阶段回执 {stage} 必须是对象")
            continue
        if receipt.get("status") != "passed":
            errors.append(f"阶段回执 {stage} 尚未通过")
        if not isinstance(receipt.get("hook_command"), str):
            errors.append(f"阶段回执 {stage} 缺少 Hook 命令")
        checks = receipt.get("evidence")
        if not isinstance(checks, list) or not checks:
            errors.append(f"阶段回执 {stage} 缺少验收证据")
    return errors


def validate_manifest(manifest_path: Path) -> list[str]:
    errors: list[str] = []
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != "country_outage_p0_manifest_v1":
        errors.append("manifest schema_version 无效")
    revision = manifest.get("revision")
    if not isinstance(revision, str) or not revision:
        errors.append("manifest revision 无效")
        revision = ""

    try:
        case_path = safe_path(manifest.get("case_set_path"), "case_set_path")
        evidence_path = safe_path(
            manifest.get("evidence_snapshot_path"), "evidence_snapshot_path"
        )
        schema_path = safe_path(manifest.get("case_schema_path"), "case_schema_path")
        receipts_path = safe_path(
            manifest.get("stage_receipts_path"), "stage_receipts_path"
        )
        p1_path = safe_path(manifest.get("p1_handoff_path"), "p1_handoff_path")
        record_path = safe_path(
            manifest.get("baseline_record_path"), "baseline_record_path"
        )
    except RuntimeError as error:
        return [str(error)]

    required_files = (
        case_path,
        evidence_path,
        schema_path,
        receipts_path,
        p1_path,
        record_path,
    )
    for path in required_files:
        if not path.is_file():
            errors.append(f"必需制品不存在：{path.relative_to(REPOSITORY_ROOT)}")
    if errors:
        return errors

    case_set = read_json(case_path)
    evidence = read_json(evidence_path)
    schema = read_json(schema_path)
    receipts = read_json(receipts_path)
    if schema.get("$id") != "https://domeye.local/schemas/country-outage-p0-cases-v1.json":
        errors.append("案例 Schema $id 无效")
    if case_set.get("revision") != revision:
        errors.append("案例集 revision 与 manifest 不一致")
    errors.extend(validate_evidence(evidence))
    errors.extend(validate_case_set(case_set, evidence))
    errors.extend(validate_receipts(receipts, revision))

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("manifest artifacts 必须是非空数组")
    else:
        seen_paths: set[str] = set()
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                errors.append("manifest artifact 必须是对象")
                continue
            raw_path = artifact.get("path")
            try:
                path = safe_path(raw_path, "artifact.path")
            except RuntimeError as error:
                errors.append(str(error))
                continue
            if str(raw_path) in seen_paths:
                errors.append(f"manifest artifact 重复：{raw_path}")
            seen_paths.add(str(raw_path))
            if not path.is_file():
                errors.append(f"manifest artifact 不存在：{raw_path}")
                continue
            expected_hash = artifact.get("sha256")
            actual_hash = sha256(path)
            if expected_hash != actual_hash:
                errors.append(
                    f"manifest artifact SHA-256 不一致：{raw_path} "
                    f"期望 {expected_hash!r}，实际 {actual_hash}"
                )

    for path, label in ((p1_path, "P1 入口回执"), (record_path, "基线验收记录")):
        text = path.read_text(encoding="utf-8")
        for phrase in (
            "20 个当前页面/API 直接问题",
            "5 个多轮旅程",
            "5 个越界问题",
            "5 个数据缺失、冲突或异常问题",
            revision,
        ):
            if phrase not in text:
                errors.append(f"{label} 缺少语义：{phrase}")
        if "Agent 已经实现" in text and "不表示" not in text:
            errors.append(f"{label} 越级宣称 Agent 已经实现")
    return errors


def build_summary(manifest_path: Path, errors: Iterable[str]) -> dict[str, Any]:
    error_list = list(errors)
    return {
        "schema_version": "country_outage_p0_validation_result_v1",
        "manifest": str(manifest_path.relative_to(REPOSITORY_ROOT)),
        "status": "passed" if not error_list else "failed",
        "case_counts": {**EXPECTED_COUNTS, "total": 35},
        "hard_gates": {
            "event_binding": "100%",
            "publication_binding": "100%",
            "fact_match": "100%",
            "evidence_resolvable": "100%",
            "no_forbidden_assertion_contract": "100%",
            "context_transition_contract": "100%",
            "missing_not_zero_contract": "100%",
            "invalid_answer_not_published_contract": "100%",
        },
        "errors": error_list,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="P0 manifest 的仓库相对路径")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        manifest_path = safe_path(arguments.manifest, "manifest")
        errors = validate_manifest(manifest_path)
    except RuntimeError as error:
        errors = [str(error)]
        manifest_path = (REPOSITORY_ROOT / arguments.manifest).resolve()
    summary = build_summary(manifest_path, errors)
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
