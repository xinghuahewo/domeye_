#!/usr/bin/env python3
"""生成并验证 P2-S1 W6 离线确定性实现认证制品。

W6 不修改或替代 W5 运行时。它把冻结 Oracle 的 28×6 场景逐条分类为
当前候选可执行、正确有界、正确延期或正确阻断，并把“分类通过”和“问题回答
通过”严格分离。当前版本冻结为 27 题正确阻断、Q24 正确延期；任何能力补齐
都必须另起任务和语义版本。
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator


SEMANTICS_VERSION = (
    "country_outage_p2_s1_w6_offline_deterministic_implementation_acceptance_v2"
)
W5_COMMIT = "b8d5b04b67c41d2d5f9f4ec1f9c64972bd90fa73"
W5_CANDIDATE_ID = "country-outage-p2-s1-w5-49686c17e811063dad6fe2e6"
W5_CANDIDATE_DIGEST = (
    "sha256:49686c17e811063dad6fe2e687202c7031add0b4db105d1e304bce94c3a9289c"
)
W5_EVIDENCE_CONTENT_DIGEST = (
    "135ca6bcc8c9c18046a31e8b43b6afbb80d8a1735370de619cbbb33000d7e4f2"
)
W5_STAGE_RECEIPT_DIGEST = (
    "5f8f2d27b56060ec52cf78e43170e5f7e0ef0356c8ac6bc645e3fe1884f26f6b"
)
W5_EXECUTION_ADMISSION_DIGEST = (
    "sha256:9c05bf1bb86c1c726827e1fe0c66df186a96546f899cd099a5bb3d7e5aa80a46"
)
DESIGN_CANDIDATE_ID = "country-outage-p2-s1-s1d-6-04135cee55b39ce5d574f7e4"
SCENARIOS = (
    ("N", "normal"),
    ("E", "empty"),
    ("M", "missing"),
    ("I", "wrong_identity"),
    ("B", "boundary"),
    ("L", "large_result"),
)
QUESTION_IDS = (
    "Q01", "Q02", "Q03", "Q04", "Q05", "Q06", "Q07", "Q08", "Q09", "Q10",
    "Q13", "Q14", "Q16", "Q17", "Q18", "Q19", "Q20", "Q21", "Q22", "Q23",
    "Q24", "Q26", "Q27", "Q29", "Q30", "Q31", "Q32", "Q33",
)
P1_UNIT_IDS = {
    *[f"TOOL-{number:02d}" for number in range(1, 7)],
    *[f"OP-{number:02d}" for number in range(1, 5)],
}
P2_1_UNIT_IDS = {"TOOL-13", "OP-34", "PLAN-CAP-02"}
DEFERRED_SUBGOAL_QUESTIONS = {"Q20", "Q23", "Q26"}
EXPECTED_REASONS: dict[str, tuple[str, ...]] = {
    "Q01": ("legacy_p1_units_not_in_w5_dispatcher",),
    "Q02": ("legacy_p1_units_not_in_w5_dispatcher",),
    "Q03": ("legacy_p1_units_not_in_w5_dispatcher", "legacy_fact_to_op29_adapter_missing"),
    "Q04": ("legacy_p1_units_not_in_w5_dispatcher", "fixture_population_insufficient_for_ranking"),
    "Q05": ("legacy_p1_units_not_in_w5_dispatcher", "result_set_population_adapter_missing", "member_fanout_not_admitted"),
    "Q06": ("result_set_population_join_adapter_missing",),
    "Q07": ("plan_cap_01_scalar_only", "member_fanout_not_admitted"),
    "Q08": ("legacy_p1_units_not_in_w5_dispatcher", "population_binding_and_asn_bound_receipts_missing"),
    "Q09": ("multi_population_structural_adapter_missing",),
    "Q10": ("legacy_p1_units_not_in_w5_dispatcher", "question_specific_boundary_disposition_missing"),
    "Q13": ("legacy_p1_units_not_in_w5_dispatcher", "legacy_peak_to_plan_cap_bridge_missing"),
    "Q14": ("result_set_to_vp_operator_adapter_missing", "member_fanout_not_admitted"),
    "Q16": ("result_set_to_op33_population_binding_missing",),
    "Q17": ("question_specific_boundary_disposition_missing", "formal_planning_allowlist_incomplete"),
    "Q18": ("result_set_to_vp_operator_adapter_missing",),
    "Q19": ("legacy_p1_units_not_in_w5_dispatcher", "per_path_fanout_not_admitted", "fixture_has_fewer_than_five_path_samples"),
    "Q20": ("result_set_projection_adapter_missing", "p2_1_subgoal_deferred_plan_cap_02"),
    "Q21": ("per_path_fanout_not_admitted",),
    "Q22": ("legacy_p1_units_not_in_w5_dispatcher", "result_set_projection_adapter_missing"),
    "Q23": ("legacy_p1_units_not_in_w5_dispatcher", "p2_1_subgoal_deferred_plan_cap_02"),
    "Q24": ("tool_13_deferred", "op_34_deferred"),
    "Q26": ("result_set_projection_adapter_missing", "p2_1_subgoal_deferred_plan_cap_02"),
    "Q27": ("multi_result_set_binding_missing", "variable_member_fanout_not_admitted"),
    "Q29": ("question_specific_external_evidence_boundary_disposition_missing",),
    "Q30": ("question_specific_commercial_relationship_boundary_disposition_missing",),
    "Q31": ("legacy_p1_units_not_in_w5_dispatcher", "legacy_fact_to_op29_op37_adapter_missing"),
    "Q32": ("legacy_p1_units_not_in_w5_dispatcher", "legacy_fact_to_op29_op37_adapter_missing", "question_specific_conflict_gate_missing"),
    "Q33": ("legacy_p1_units_not_in_w5_dispatcher", "exhaustive_unsupported_boundary_response_missing"),
}
REASON_PRIORITY = (
    "legacy_p1_units_not_in_w5_dispatcher",
    "legacy_fact_to_op29_adapter_missing",
    "fixture_population_insufficient_for_ranking",
    "result_set_population_adapter_missing",
    "result_set_population_join_adapter_missing",
    "plan_cap_01_scalar_only",
    "population_binding_and_asn_bound_receipts_missing",
    "multi_population_structural_adapter_missing",
    "question_specific_boundary_disposition_missing",
    "legacy_peak_to_plan_cap_bridge_missing",
    "result_set_to_vp_operator_adapter_missing",
    "member_fanout_not_admitted",
    "result_set_to_op33_population_binding_missing",
    "formal_planning_allowlist_incomplete",
    "per_path_fanout_not_admitted",
    "fixture_has_fewer_than_five_path_samples",
    "result_set_projection_adapter_missing",
    "p2_1_subgoal_deferred_plan_cap_02",
    "multi_result_set_binding_missing",
    "variable_member_fanout_not_admitted",
    "question_specific_external_evidence_boundary_disposition_missing",
    "question_specific_commercial_relationship_boundary_disposition_missing",
    "legacy_fact_to_op29_op37_adapter_missing",
    "question_specific_conflict_gate_missing",
    "exhaustive_unsupported_boundary_response_missing",
    "tool_13_deferred",
    "op_34_deferred",
)

# 这些标记不是“字符串出现即实现”的宽松探针。W6 只把存在明确、版本化的
# Host adapter 合同视为能力；当前冻结 W5 候选没有这些合同，因此扫描结果必须
# 是 false。未来若真正补齐任一合同，候选字节和 W6 语义版本都必须一起升级。
REQUIRED_ADAPTER_CONTRACT_MARKERS = {
    "legacy_p1_execution_bridge": "country_outage_p2_s1_legacy_p1_execution_bridge_v1",
    "legacy_fact_to_op29_adapter": "country_outage_p2_s1_legacy_fact_to_op29_adapter_v1",
    "result_set_population_adapter": "country_outage_p2_s1_result_set_population_adapter_v1",
    "result_set_population_join_adapter": "country_outage_p2_s1_result_set_population_join_adapter_v1",
    "population_binding_and_asn_bound_adapter": "country_outage_p2_s1_population_binding_asn_bound_adapter_v1",
    "multi_population_structural_adapter": "country_outage_p2_s1_multi_population_structural_adapter_v1",
    "question_specific_boundary_disposition": "country_outage_p2_s1_question_specific_boundary_disposition_v1",
    "legacy_peak_to_plan_cap_bridge": "country_outage_p2_s1_legacy_peak_plan_cap_bridge_v1",
    "result_set_to_vp_operator_adapter": "country_outage_p2_s1_result_set_vp_operator_adapter_v1",
    "result_set_to_op33_population_binding": "country_outage_p2_s1_result_set_op33_population_binding_v1",
    "per_path_fanout_adapter": "country_outage_p2_s1_per_path_fanout_adapter_v1",
    "result_set_projection_adapter": "country_outage_p2_s1_result_set_projection_adapter_v1",
    "multi_result_set_binding": "country_outage_p2_s1_multi_result_set_binding_v1",
    "legacy_fact_to_op29_op37_adapter": "country_outage_p2_s1_legacy_fact_op29_op37_adapter_v1",
    "question_specific_conflict_gate": "country_outage_p2_s1_question_specific_conflict_gate_v1",
    "exhaustive_unsupported_boundary_response": "country_outage_p2_s1_exhaustive_unsupported_boundary_response_v1",
}

ROOT = Path(__file__).resolve().parents[4]
IMPLEMENTATION_ROOT = Path("contracts/agent/country-outage-p2-s1-implementation")
CERT_ROOT = IMPLEMENTATION_ROOT / "w6-certification"
RECEIPT_ROOT = IMPLEMENTATION_ROOT / "wave-evidence/run-receipts"
W5_EVIDENCE_PATH = IMPLEMENTATION_ROOT / "wave-evidence/W5.json"
W6_EVIDENCE_PATH = IMPLEMENTATION_ROOT / "wave-evidence/W6.json"
W5_STAGE_PATH = Path("evaluation/country-outage/p2-s1-implementation/stages/W5.json")
ORACLE_PATH = Path("contracts/agent/country-outage-p2-s1-execution-unit-design/oracle.json")
SEED_PATH = Path("contracts/agent/country-outage-p2-s1-execution-unit-design/question-oracle-seed.json")
MAP_PATH = Path("contracts/agent/country-outage-p2-s1-execution-unit-design/question-capability-map.json")
SOURCE_MANIFEST_PATH = Path("contracts/data/country-outage-p2-s1/test-fixture/source-store/manifest.json")
BASELINE_PATH = IMPLEMENTATION_ROOT / "implementation-baseline.json"
DISPATCHER_PATH = Path("backend/services/country_outage_p2_s1_registry_dispatcher.py")
RUNTIME_PATH = Path("backend/services/country_outage_p2_s1_investigation_runtime.py")
PLANNER_PATH = Path("agent-sidecar/src/chat/p2-s1-planning-grounding-port.ts")
CASE_SCHEMA_PATH = IMPLEMENTATION_ROOT / "w6-case-receipt.schema.json"
REVIEW_SCHEMA_PATH = IMPLEMENTATION_ROOT / "w6-independent-review.schema.json"
MANIFEST_SCHEMA_PATH = IMPLEMENTATION_ROOT / "w6-acceptance-manifest.schema.json"
RUNNER_PATH = IMPLEMENTATION_ROOT / "tools/run_stage_tests.py"
CERTIFIER_PATH = IMPLEMENTATION_ROOT / "tools/run_w6_certification.py"
HOOK_PATH = Path(".codex/hooks/country_outage_agent_p2_s1_implementation_alignment.py")
HOOK_TEST_PATH = Path("dev/tests/test_country_outage_p2_s1_implementation_alignment_hook.py")
W6_TEST_PATH = Path("backend/web/tests/test_country_outage_p2_s1_w6_certification.py")
DOC_PATH = Path("docs/agent/P2-组合式调查/实体调查实现工程/W6-离线确定性实现验收说明.md")
REVIEW_PATHS = {
    "product_semantic": CERT_ROOT / "product-semantic-review.json",
    "bgp_semantic": CERT_ROOT / "bgp-semantic-review.json",
}
RUNNER_SUITES = ("w6-python", "w6-sidecar", "w6-recovery-attack")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class CertificationError(RuntimeError):
    """认证制品不闭合。"""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(value: Any, excluded: Iterable[str] = ()) -> str:
    excluded_set = set(excluded)
    if isinstance(value, Mapping):
        value = {key: item for key, item in value.items() if key not in excluded_set}
    return "sha256:" + sha256_bytes(canonical_json(value))


def file_sha(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise CertificationError(f"duplicate_json_key:{key}")
        output[key] = value
    return output


def _reject_constant(value: str) -> None:
    raise CertificationError(f"non_finite_json_number:{value}")


def strict_json_bytes(data: bytes, label: str) -> Any:
    if data.startswith(b"\xef\xbb\xbf"):
        raise CertificationError(f"json_bom_forbidden:{label}")
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CertificationError(f"strict_json_invalid:{label}:{error}") from error


def safe_repo_path(relative: Path | str) -> Path:
    text = Path(relative).as_posix()
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or ".." in candidate.parts or text in ("", "."):
        raise CertificationError(f"unsafe_repo_path:{text}")
    path = ROOT / text
    if path.is_symlink():
        raise CertificationError(f"symlink_forbidden:{text}")
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(ROOT.resolve()):
        raise CertificationError(f"path_escape:{text}")
    return path


def load_json(relative: Path | str) -> dict[str, Any]:
    path = safe_repo_path(relative)
    if not path.is_file():
        raise CertificationError(f"json_missing:{Path(relative).as_posix()}")
    value = strict_json_bytes(path.read_bytes(), Path(relative).as_posix())
    if not isinstance(value, dict):
        raise CertificationError(f"json_root_not_object:{Path(relative).as_posix()}")
    return value


def write_bytes(relative: Path | str, data: bytes) -> None:
    path = safe_repo_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise CertificationError(f"symlink_forbidden:{Path(relative).as_posix()}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".w6-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_json(relative: Path | str, value: Mapping[str, Any]) -> None:
    write_bytes(
        relative,
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8") + b"\n",
    )


def self_digest(value: Mapping[str, Any], field: str = "content_digest") -> str:
    return digest(value, {field})


def verify_self_digest(value: Mapping[str, Any], field: str = "content_digest") -> str:
    actual = value.get(field)
    expected = self_digest(value, field)
    if actual != expected:
        raise CertificationError(f"self_digest_mismatch:{field}")
    return expected


def validate_schema(value: Any, schema_path: Path) -> None:
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(item) for item in first.absolute_path)
        raise CertificationError(f"schema_validation_failed:{schema_path.name}:{location}:{first.message}")


def git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise CertificationError("git_head_unavailable")
    return completed.stdout.strip()


def question_map(value: Mapping[str, Any], label: str) -> dict[str, dict[str, Any]]:
    questions = value.get("questions")
    if not isinstance(questions, list):
        raise CertificationError(f"question_population_missing:{label}")
    output: dict[str, dict[str, Any]] = {}
    for item in questions:
        if not isinstance(item, dict) or not isinstance(item.get("question_id"), str):
            raise CertificationError(f"question_record_invalid:{label}")
        question_id = item["question_id"]
        if question_id in output:
            raise CertificationError(f"question_duplicate:{label}:{question_id}")
        output[question_id] = item
    if tuple(output) != QUESTION_IDS:
        raise CertificationError(f"question_order_or_population_drift:{label}")
    return output


def load_w5_bindings() -> tuple[dict[str, Any], dict[str, Any]]:
    if git_head() != W5_COMMIT:
        raise CertificationError("w5_candidate_commit_drift")
    evidence = load_json(W5_EVIDENCE_PATH)
    stage = load_json(W5_STAGE_PATH)
    if evidence.get("implementation_candidate_id") != W5_CANDIDATE_ID:
        raise CertificationError("w5_candidate_id_drift")
    if "sha256:" + str(evidence.get("implementation_semantic_digest")) != W5_CANDIDATE_DIGEST:
        raise CertificationError("w5_candidate_digest_drift")
    if evidence.get("content_digest") != W5_EVIDENCE_CONTENT_DIGEST:
        raise CertificationError("w5_evidence_content_drift")
    if digest(evidence, {"content_digest"}) != "sha256:" + W5_EVIDENCE_CONTENT_DIGEST:
        raise CertificationError("w5_evidence_not_recomputable")
    if stage.get("receipt_digest") != W5_STAGE_RECEIPT_DIGEST:
        raise CertificationError("w5_stage_receipt_drift")
    if digest(stage, {"receipt_digest"}) != "sha256:" + W5_STAGE_RECEIPT_DIGEST:
        raise CertificationError("w5_stage_receipt_not_recomputable")
    admission = evidence.get("execution_admission_receipt")
    if not isinstance(admission, dict) or admission.get("receipt_digest") != W5_EXECUTION_ADMISSION_DIGEST:
        raise CertificationError("w5_execution_admission_drift")
    artifacts = evidence.get("artifact_manifest")
    if not isinstance(artifacts, list) or len(artifacts) != 50:
        raise CertificationError("w5_artifact_population_drift")
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"path", "role", "size_bytes", "sha256"}:
            raise CertificationError("w5_artifact_binding_invalid")
        path = safe_repo_path(item["path"])
        if not path.is_file() or path.stat().st_size != item["size_bytes"] or file_sha(path) != item["sha256"]:
            raise CertificationError(f"w5_artifact_drift:{item.get('path')}")
    return evidence, stage


def parse_planner_allowlist() -> list[str]:
    path = safe_repo_path("agent-sidecar/src/chat/p2-s1-planning-grounding-port.ts")
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"FROZEN_EXECUTION_UNIT_CAPABILITIES[^=]*= Object\.freeze\(\{(?P<body>.*?)\}\)",
        text,
        flags=re.DOTALL,
    )
    if match is None:
        raise CertificationError("formal_planner_allowlist_unresolved")
    units = re.findall(r"'((?:TOOL|OP|GATE|BOUNDARY|PLAN-CAP)-\d{2})'\s*:", match.group("body"))
    if units != ["GATE-01", "GATE-02", "GATE-03", "TOOL-07", "TOOL-11", "OP-29", "OP-37", "BOUNDARY-01"]:
        raise CertificationError("formal_planner_allowlist_drift")
    return units


def source_population_facts() -> dict[str, Any]:
    manifest = load_json(SOURCE_MANIFEST_PATH)
    populations = manifest.get("population_manifests")
    if not isinstance(populations, list):
        raise CertificationError("source_population_manifest_invalid")
    counts = {
        item["population_id"]: item["row_count"]
        for item in populations
        if isinstance(item, dict)
        and isinstance(item.get("population_id"), str)
        and isinstance(item.get("row_count"), int)
    }
    if set(counts) != {
        "fixed_cohort_member_rows", "prefix_state_rows", "asn_state_rows",
        "new_prefix_state_rows", "materialized_route_state_rows_at_exact_time",
        "window_path_association_evidence_rows",
    }:
        raise CertificationError("source_population_count_set_drift")
    asn_rows_path = safe_repo_path(
        "contracts/data/country-outage-p2-s1/test-fixture/source-store/populations/asn_state_rows.jsonl"
    )
    asns = set()
    for line in asn_rows_path.read_bytes().splitlines():
        row = strict_json_bytes(line, "asn_state_rows.jsonl")
        if not isinstance(row, dict) or not isinstance(row.get("asn"), int):
            raise CertificationError("asn_state_fixture_invalid")
        asns.add(row["asn"])
    return {
        "manifest_file_sha256": file_sha(safe_repo_path(SOURCE_MANIFEST_PATH)),
        "manifest_content_sha256": manifest.get("content_sha256"),
        "row_counts": counts,
        "distinct_asn_count": len(asns),
    }


def adapter_contract_facts() -> dict[str, Any]:
    paths = (RUNTIME_PATH, DISPATCHER_PATH, PLANNER_PATH)
    sources = {
        path.as_posix(): safe_repo_path(path).read_text(encoding="utf-8")
        for path in paths
    }
    combined = "\n".join(sources.values())
    probes = {
        adapter_id: {
            "required_contract_marker": marker,
            "present": marker in combined,
        }
        for adapter_id, marker in sorted(REQUIRED_ADAPTER_CONTRACT_MARKERS.items())
    }
    return {
        "probe_kind": "required_versioned_contract_symbol_presence",
        "source_files": {
            path.as_posix(): "sha256:" + file_sha(safe_repo_path(path))
            for path in paths
        },
        "adapter_contract_probes": probes,
    }


def deferred_scope_facts() -> dict[str, Any]:
    baseline = load_json(BASELINE_PATH)
    scope = baseline.get("deferred_scope")
    if not isinstance(scope, dict):
        raise CertificationError("implementation_baseline_deferred_scope_missing")
    dynamic = scope.get("dynamic_fan_out_question_subgoals")
    if not isinstance(dynamic, dict) or set(dynamic) != DEFERRED_SUBGOAL_QUESTIONS:
        raise CertificationError("dynamic_fanout_question_scope_drift")
    unit_ids = sorted({
        *scope.get("tool_ids", []),
        *scope.get("operator_ids", []),
        *scope.get("plan_capability_ids", []),
    })
    if unit_ids != sorted(P2_1_UNIT_IDS):
        raise CertificationError("p2_1_deferred_unit_scope_drift")
    return {
        "baseline_file_sha256": "sha256:" + file_sha(safe_repo_path(BASELINE_PATH)),
        "deferred_unit_ids": unit_ids,
        "dynamic_fanout_question_ids": sorted(dynamic),
        "dynamic_fanout_subgoals_digest": digest(dynamic),
    }


def run_p2_1_denial_probe() -> dict[str, Any]:
    """直接调用冻结 Dispatcher 的拒绝入口，不创建 Store，也不执行 handler。"""

    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from backend.services.country_outage_p2_s1_registry_dispatcher import (
        W5RegistryDispatcher,
        W5RegistryError,
    )

    dispatcher = object.__new__(W5RegistryDispatcher)
    results = []
    handler_invocation_count = 0
    for unit_id in ("PLAN-CAP-02", "TOOL-13", "OP-34"):
        try:
            dispatcher.assert_allowed(unit_id)
        except W5RegistryError as error:
            if error.code != "p2_1_unit_forbidden" or error.status_code != 403:
                raise CertificationError(f"p2_1_denial_code_drift:{unit_id}:{error.code}") from error
            results.append({
                "unit_id": unit_id,
                "denial_code": error.code,
                "status_code": error.status_code,
                "handler_invoked": False,
            })
        else:
            handler_invocation_count += 1
            raise CertificationError(f"p2_1_unit_unexpectedly_allowed:{unit_id}")
    value: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_w6_p2_1_denial_probe_v2",
        "dispatcher_path": DISPATCHER_PATH.as_posix(),
        "dispatcher_file_sha256": "sha256:" + file_sha(safe_repo_path(DISPATCHER_PATH)),
        "unit_results": results,
        "execution_attempt_count": len(results),
        "handler_invocation_count": handler_invocation_count,
        "probe_store_created": False,
        "public_result_set_count": 0,
        "public_graph_count": 0,
        "content_digest": None,
    }
    value["content_digest"] = self_digest(value)
    return value


def collect_machine_facts(evidence: Mapping[str, Any]) -> dict[str, Any]:
    admission = evidence.get("execution_admission_receipt")
    if not isinstance(admission, Mapping):
        raise CertificationError("w5_execution_admission_missing")
    allowed = admission.get("execution_allowed_unit_ids")
    if not isinstance(allowed, list) or any(not isinstance(item, str) for item in allowed):
        raise CertificationError("w5_execution_allowlist_invalid")
    facts: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_w6_machine_facts_v2",
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
        "w5_execution_admission_receipt_digest": admission.get("receipt_digest"),
        "w5_registry_snapshot_id": admission.get("registry_snapshot_id"),
        "w5_registry_snapshot_digest": admission.get("snapshot_digest"),
        "w5_registry_admitted_unit_ids": sorted(allowed),
        "formal_planning_allowlist_unit_ids": sorted(parse_planner_allowlist()),
        "source_population_facts": source_population_facts(),
        "adapter_contract_facts": adapter_contract_facts(),
        "plan_cap_01_scalar_only": all(
            marker in safe_repo_path(RUNTIME_PATH).read_text(encoding="utf-8")
            for marker in ("plan_capability_fanout_forbidden", "PLAN-CAP-01 只能绑定单一标量")
        ),
        "deferred_scope_facts": deferred_scope_facts(),
        "p2_1_denial_probe": run_p2_1_denial_probe(),
        "candidate_input_count": 1,
        "content_digest": None,
    }
    if facts["w5_execution_admission_receipt_digest"] != W5_EXECUTION_ADMISSION_DIGEST:
        raise CertificationError("machine_facts_execution_admission_drift")
    if facts["plan_cap_01_scalar_only"] is not True:
        raise CertificationError("plan_cap_01_scalar_contract_unresolved")
    facts["content_digest"] = self_digest(facts)
    return facts


def _adapter_missing(machine_facts: Mapping[str, Any], adapter_id: str) -> bool:
    probes = machine_facts["adapter_contract_facts"]["adapter_contract_probes"]
    probe = probes.get(adapter_id)
    if not isinstance(probe, Mapping) or not isinstance(probe.get("present"), bool):
        raise CertificationError(f"adapter_probe_missing:{adapter_id}")
    return probe["present"] is False


def classify_candidate(
    oracle_question: Mapping[str, Any],
    seed_question: Mapping[str, Any],
    machine_facts: Mapping[str, Any],
) -> tuple[str, list[str]]:
    """仅从候选机器事实与 Oracle 语义推导 actual，不读取 EXPECTED_REASONS。"""

    question_id = str(oracle_question["question_id"])
    required = set(oracle_question["required_unit_ids"])
    answerability = str(oracle_question["answerability"])
    assertions = set(seed_question.get("required_assertions", []))
    admitted = set(machine_facts["w5_registry_admitted_unit_ids"])
    planner = set(machine_facts["formal_planning_allowlist_unit_ids"])
    source = machine_facts["source_population_facts"]
    deferred = machine_facts["deferred_scope_facts"]
    reasons: set[str] = set()

    if answerability == "deferred_p2_1":
        denial = machine_facts["p2_1_denial_probe"]
        denied_units = {
            item["unit_id"] for item in denial["unit_results"]
            if item["denial_code"] == "p2_1_unit_forbidden" and item["handler_invoked"] is False
        }
        if not required <= denied_units or denial["handler_invocation_count"] != 0:
            raise CertificationError(f"p2_1_denial_probe_incomplete:{question_id}")
        if "TOOL-13" in required:
            reasons.add("tool_13_deferred")
        if "OP-34" in required:
            reasons.add("op_34_deferred")
        return "correctly_deferred", [code for code in REASON_PRIORITY if code in reasons]

    legacy = required & P1_UNIT_IDS
    if legacy and not legacy <= admitted:
        reasons.add("legacy_p1_units_not_in_w5_dispatcher")
    if legacy and "OP-29" in required and "OP-37" not in required and _adapter_missing(machine_facts, "legacy_fact_to_op29_adapter"):
        reasons.add("legacy_fact_to_op29_adapter_missing")
    if legacy and {"OP-29", "OP-37"} <= required and _adapter_missing(machine_facts, "legacy_fact_to_op29_op37_adapter"):
        reasons.add("legacy_fact_to_op29_op37_adapter_missing")
    if "OP-05" in required and "RENDERER-01" in required and source["distinct_asn_count"] < 2:
        reasons.add("fixture_population_insufficient_for_ranking")
    if legacy and {"TOOL-07", "TOOL-08", "TOOL-12", "OP-15"} <= required and _adapter_missing(machine_facts, "result_set_population_adapter"):
        reasons.add("result_set_population_adapter_missing")
    if (
        ({"TOOL-07", "TOOL-08", "PLAN-CAP-01"} <= required)
        or ({"OP-06", "OP-30", "OP-31", "OP-32"} <= required)
        or (legacy and {"TOOL-07", "TOOL-08", "TOOL-12", "OP-15"} <= required)
    ) and "PLAN-CAP-02" not in planner:
        reasons.add("member_fanout_not_admitted")
    if {"TOOL-07", "TOOL-08", "OP-35"} <= required and _adapter_missing(machine_facts, "result_set_population_join_adapter"):
        reasons.add("result_set_population_join_adapter_missing")
    if {"TOOL-07", "TOOL-08", "PLAN-CAP-01"} <= required and machine_facts["plan_cap_01_scalar_only"]:
        reasons.add("plan_cap_01_scalar_only")
    if {"OP-10", "OP-11", "OP-12", "OP-13", "OP-14", "OP-36"} <= required and _adapter_missing(machine_facts, "population_binding_and_asn_bound_adapter"):
        reasons.add("population_binding_and_asn_bound_receipts_missing")
    if {"OP-25", "OP-26", "OP-27", "OP-28", "OP-38", "OP-39"} <= required and _adapter_missing(machine_facts, "multi_population_structural_adapter"):
        reasons.add("multi_population_structural_adapter_missing")
    if (
        ({"OP-05", "BOUNDARY-01"} <= required)
        or answerability == "boundary_supported"
    ) and _adapter_missing(machine_facts, "question_specific_boundary_disposition"):
        reasons.add("question_specific_boundary_disposition_missing")
    if legacy and {"OP-01", "PLAN-CAP-01", "DELIVERY-01"} <= required and _adapter_missing(machine_facts, "legacy_peak_to_plan_cap_bridge"):
        reasons.add("legacy_peak_to_plan_cap_bridge_missing")
    if {"OP-30", "OP-31", "OP-32", "TOOL-11"} <= required and _adapter_missing(machine_facts, "result_set_to_vp_operator_adapter"):
        reasons.add("result_set_to_vp_operator_adapter_missing")
    if "OP-33" in required and _adapter_missing(machine_facts, "result_set_to_op33_population_binding"):
        reasons.add("result_set_to_op33_population_binding_missing")
    if answerability == "boundary_supported" and not required <= planner:
        reasons.add("formal_planning_allowlist_incomplete")
    if {"OP-15", "OP-16", "TOOL-12"} <= required and _adapter_missing(machine_facts, "per_path_fanout_adapter"):
        reasons.add("per_path_fanout_not_admitted")
    if "至少5条Renderer预览" in assertions and source["row_counts"]["window_path_association_evidence_rows"] < 5:
        reasons.add("fixture_has_fewer_than_five_path_samples")
    if {"OP-18", "TOOL-12"} <= required and _adapter_missing(machine_facts, "result_set_projection_adapter"):
        reasons.add("result_set_projection_adapter_missing")
    if question_id in set(deferred["dynamic_fanout_question_ids"]) and "PLAN-CAP-02" in set(deferred["deferred_unit_ids"]):
        reasons.add("p2_1_subgoal_deferred_plan_cap_02")
    if {"OP-19", "OP-25", "OP-26", "OP-27", "OP-28", "TOOL-12"} <= required and not ({"OP-38", "OP-39"} & required) and _adapter_missing(machine_facts, "multi_result_set_binding"):
        reasons.add("multi_result_set_binding_missing")
        reasons.add("variable_member_fanout_not_admitted")
    if answerability == "external_evidence_required" and "customer cone需要外部关系数据" in assertions and _adapter_missing(machine_facts, "question_specific_boundary_disposition"):
        reasons.add("question_specific_external_evidence_boundary_disposition_missing")
    if answerability == "external_evidence_required" and "商业关系证据缺失" in assertions and _adapter_missing(machine_facts, "question_specific_boundary_disposition"):
        reasons.add("question_specific_commercial_relationship_boundary_disposition_missing")
    if "相同定义人口的互斥断言才是conflict" in assertions and "GATE-04" in required and _adapter_missing(machine_facts, "question_specific_conflict_gate"):
        reasons.add("question_specific_conflict_gate_missing")
    if answerability == "bounded_control_plane_only" and "逐项unsupported" in assertions and _adapter_missing(machine_facts, "exhaustive_unsupported_boundary_response"):
        reasons.add("exhaustive_unsupported_boundary_response_missing")

    ordered = [code for code in REASON_PRIORITY if code in reasons]
    if ordered:
        return "correctly_blocked", ordered
    if answerability in {"boundary_supported", "external_evidence_required", "bounded_control_plane_only"}:
        return "correctly_bounded", []
    return "executed_supported", []


def build_candidate(evidence: Mapping[str, Any], stage: Mapping[str, Any]) -> dict[str, Any]:
    admission = evidence["execution_admission_receipt"]
    source = source_population_facts()
    candidate: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_w6_implementation_candidate_binding_v2",
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
        "w5_commit": W5_COMMIT,
        "design_candidate_id": DESIGN_CANDIDATE_ID,
        "w5_evidence": {
            "path": W5_EVIDENCE_PATH.as_posix(),
            "file_sha256": file_sha(safe_repo_path(W5_EVIDENCE_PATH)),
            "content_digest": "sha256:" + W5_EVIDENCE_CONTENT_DIGEST,
        },
        "w5_stage_receipt": {
            "path": W5_STAGE_PATH.as_posix(),
            "file_sha256": file_sha(safe_repo_path(W5_STAGE_PATH)),
            "receipt_digest": "sha256:" + W5_STAGE_RECEIPT_DIGEST,
        },
        "execution_admission": {
            "receipt_digest": W5_EXECUTION_ADMISSION_DIGEST,
            "registry_snapshot_id": admission["registry_snapshot_id"],
            "registry_snapshot_digest": admission["snapshot_digest"],
            "registry_revision": admission["registry_revision"],
            "execution_allowed_unit_ids": admission["execution_allowed_unit_ids"],
        },
        "source_store": source,
        "oracle_sources": {
            "oracle_file_sha256": file_sha(safe_repo_path(ORACLE_PATH)),
            "seed_file_sha256": file_sha(safe_repo_path(SEED_PATH)),
            "capability_map_file_sha256": file_sha(safe_repo_path(MAP_PATH)),
        },
        "artifact_manifest": copy.deepcopy(evidence["artifact_manifest"]),
        "w6_outputs_excluded_from_candidate_identity": True,
        "candidate_payload_digest": None,
        "content_digest": None,
    }
    candidate["candidate_payload_digest"] = digest(
        candidate, {"candidate_payload_digest", "content_digest"}
    )
    candidate["content_digest"] = self_digest(candidate)
    return candidate


def build_semantics(candidate: Mapping[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_w6_certification_semantics_v2",
        "certification_semantics_version": SEMANTICS_VERSION,
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
        "candidate_binding_digest": candidate["content_digest"],
        "status_if_all_gates_pass": "implementation_accepted_for_release_preparation",
        "acceptance_meaning": "冻结W5实现候选、离线确定性能力与28题可执行性分类已按同候选证据验收，可进入实际provider认证及独立发布准备；不表示问题回答、provider模型、provider性能、runtime promotion或生产通过。",
        "classification_population": {
            "question_count": 28,
            "scenario_count_per_question": 6,
            "case_count": 168,
            "blocked_question_count": 27,
            "deferred_question_count": 1,
            "blocked_case_count": 162,
            "deferred_case_count": 6,
        },
        "offline_runtime_performance_thresholds": {
            "w5_runtime_and_api_suite_max_seconds": 300,
            "w5_sidecar_suite_max_seconds": 180,
            "w6_recovery_attack_suite_max_seconds": 180,
        },
        "full_question_answer_certification_passed": False,
        "actual_provider_model_alignment": False,
        "actual_provider_performance_acceptance": False,
        "actual_provider_cost_acceptance": False,
        "runtime_promotion": False,
        "production_deployed": False,
        "external_provider_call_count": 0,
        "content_digest": None,
    }
    value["content_digest"] = self_digest(value)
    return value


def build_disposition_contract(
    candidate: Mapping[str, Any],
    oracle_questions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    questions = []
    for question_id in QUESTION_IDS:
        expected = "correctly_deferred" if question_id == "Q24" else "correctly_blocked"
        questions.append({
            "question_id": question_id,
            "oracle_answerability": oracle_questions[question_id]["answerability"],
            "expected_disposition": expected,
            "expected_reason_codes": list(EXPECTED_REASONS[question_id]),
            "p2_1_deferred_subgoal": question_id in DEFERRED_SUBGOAL_QUESTIONS,
        })
    value: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_w6_question_disposition_contract_v2",
        "certification_semantics_version": SEMANTICS_VERSION,
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
        "candidate_binding_digest": candidate["content_digest"],
        "scenario_order": [code for code, _ in SCENARIOS],
        "questions": questions,
        "contract_is_frozen_not_dynamically_reclassified": True,
        "content_digest": None,
    }
    value["content_digest"] = self_digest(value)
    return value


def blocker_refs(reason_codes: Sequence[str]) -> list[str]:
    references = {
        W5_EVIDENCE_PATH.as_posix(),
        "agent-sidecar/src/chat/p2-s1-planning-grounding-port.ts",
        "backend/services/country_outage_p2_s1_investigation_runtime.py",
        SOURCE_MANIFEST_PATH.as_posix(),
        ORACLE_PATH.as_posix(),
    }
    if any("legacy" in code for code in reason_codes):
        references.add("backend/services/country_outage_p2_s1_registry_dispatcher.py")
    return sorted(references)


def build_case_receipts(
    evidence: Mapping[str, Any],
    oracle: Mapping[str, Any],
    seed: Mapping[str, Any],
    capability_map: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    oracle_questions = question_map(oracle, "oracle")
    seed_questions = question_map(seed, "seed")
    map_questions = question_map(capability_map, "capability_map")
    scenario_semantics = {
        item["short_code"]: item["oracle_semantic"]
        for item in oracle.get("scenario_registry", [])
        if isinstance(item, dict)
    }
    machine_facts = collect_machine_facts(evidence)
    machine_facts_digest = machine_facts["content_digest"]
    admitted = set(machine_facts["w5_registry_admitted_unit_ids"])
    denial_probe = machine_facts["p2_1_denial_probe"]
    receipts: list[dict[str, Any]] = []
    matrix_cases: list[dict[str, Any]] = []
    oracle_sha = "sha256:" + file_sha(safe_repo_path(ORACLE_PATH))
    seed_sha = "sha256:" + file_sha(safe_repo_path(SEED_PATH))
    map_sha = "sha256:" + file_sha(safe_repo_path(MAP_PATH))
    for question_id in QUESTION_IDS:
        oracle_question = oracle_questions[question_id]
        seed_question = seed_questions[question_id]
        map_question = map_questions[question_id]
        required_units = list(oracle_question["required_unit_ids"])
        required_caps = list(oracle_question["required_capability_ids"])
        expected_disposition = "correctly_deferred" if question_id == "Q24" else "correctly_blocked"
        expected_reasons = list(EXPECTED_REASONS[question_id])
        actual_disposition, actual_reasons = classify_candidate(
            oracle_question, seed_question, machine_facts
        )
        question_record_digest = digest({
            "oracle": oracle_question,
            "seed": seed_question,
            "capability_map": map_question,
        })
        for ordinal_scenario, (scenario_code, scenario_class) in enumerate(SCENARIOS):
            case_id = f"{question_id}-{scenario_code}"
            scenario_expectation = seed_question["scenario_expectations"][scenario_class]
            scenario_expectation_digest = digest({
                "scenario_code": scenario_code,
                "scenario_class": scenario_class,
                "seed_expectation": scenario_expectation,
                "oracle_semantic": scenario_semantics[scenario_code],
            })
            legacy = sorted(set(required_units) & P1_UNIT_IDS)
            deferred_units = sorted(set(required_units) & P2_1_UNIT_IDS)
            receipt: dict[str, Any] = {
                "schema_version": "country_outage_p2_s1_w6_case_receipt_v2",
                "certification_semantics_version": SEMANTICS_VERSION,
                "implementation_candidate_id": W5_CANDIDATE_ID,
                "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
                "machine_facts_digest": machine_facts_digest,
                "case_id": case_id,
                "question_id": question_id,
                "scenario_code": scenario_code,
                "scenario_class": scenario_class,
                "oracle_binding": {
                    "oracle_digest": oracle_sha,
                    "seed_digest": seed_sha,
                    "capability_map_digest": map_sha,
                    "question_record_digest": question_record_digest,
                    "scenario_expectation_digest": scenario_expectation_digest,
                },
                "required_capability_ids": required_caps,
                "required_unit_ids": required_units,
                "expected_disposition": expected_disposition,
                "expected_reason_codes": expected_reasons,
                "actual_disposition": actual_disposition,
                "actual_reason_codes": actual_reasons,
                "unit_coverage": {
                    "required": required_units,
                    "w5_registry_admitted": sorted(set(required_units) & admitted),
                    "legacy_p1_only": legacy,
                    "deferred_p2_1": deferred_units,
                    "machine_blocker_reason_codes": actual_reasons,
                    "actually_dispatched": [],
                },
                "proof_mode": "p2_1_denial_probe" if actual_disposition == "correctly_deferred" else "candidate_capability_audit",
                "runtime_proof": None,
                "blocked_proof": None if actual_disposition == "correctly_deferred" else {
                    "machine_recomputed": True,
                    "classification_before_execution": True,
                    "execution_started": False,
                    "blocker_contract_refs": blocker_refs(actual_reasons),
                    "observed_zero_fact_commit": False,
                    "deferred_subgoal_proof": ({
                        "plan_capability_id": "PLAN-CAP-02",
                        "status": "deferred_p2_1",
                        "dispatch_count": 0,
                        "denial_probe_digest": denial_probe["content_digest"],
                    } if question_id in DEFERRED_SUBGOAL_QUESTIONS else None),
                },
                "boundary_proof": None,
                "deferred_proof": ({
                    "unit_ids": ["TOOL-13", "OP-34"],
                    "denial_code": "p2_1_unit_forbidden",
                    "denial_probe_digest": denial_probe["content_digest"],
                    "execution_attempt_count": 2,
                    "dispatch_count": denial_probe["handler_invocation_count"],
                    "public_result_set_count": denial_probe["public_result_set_count"],
                    "public_graph_count": denial_probe["public_graph_count"],
                    "next_action": "implement_and_independently_certify_p2_1",
                } if actual_disposition == "correctly_deferred" else None),
                "same_candidate_checks": {
                    "candidate_equal": machine_facts["implementation_candidate_id"] == W5_CANDIDATE_ID and machine_facts["implementation_candidate_digest"] == W5_CANDIDATE_DIGEST,
                    "registry_equal": machine_facts["w5_execution_admission_receipt_digest"] == W5_EXECUTION_ADMISSION_DIGEST,
                    "publication_equal_or_not_applicable": True,
                    "cross_candidate_reuse": machine_facts["candidate_input_count"] != 1,
                },
                "overclaim_checks": {
                    "question_answer_claimed": False,
                    "provider_model_claimed": False,
                    "provider_performance_claimed": False,
                    "runtime_promotion_claimed": False,
                    "production_claimed": False,
                },
                "case_acceptance": {
                    "classification_matches_contract": actual_disposition == expected_disposition and actual_reasons == expected_reasons,
                    "case_passed": actual_disposition == expected_disposition and actual_reasons == expected_reasons,
                    "meaning": "disposition_classification_only_not_question_answer",
                },
                "content_digest": None,
            }
            receipt["content_digest"] = self_digest(receipt)
            validate_schema(receipt, CASE_SCHEMA_PATH)
            receipts.append(receipt)
            matrix_cases.append({
                "ordinal": len(matrix_cases) + 1,
                "case_id": case_id,
                "question_id": question_id,
                "scenario_ordinal": ordinal_scenario + 1,
                "expected_disposition": expected_disposition,
                "expected_reason_codes": expected_reasons,
                "actual_disposition": actual_disposition,
                "actual_reason_codes": actual_reasons,
                "scenario_expectation_digest": scenario_expectation_digest,
            })
    if len(receipts) != 168:
        raise CertificationError("case_population_not_168")
    matrix: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_w6_case_matrix_v2",
        "certification_semantics_version": SEMANTICS_VERSION,
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
        "oracle_sources": {
            "oracle_digest": oracle_sha,
            "seed_digest": seed_sha,
            "capability_map_digest": map_sha,
        },
        "machine_facts": machine_facts,
        "cases": matrix_cases,
        "content_digest": None,
    }
    matrix["content_digest"] = self_digest(matrix)
    return receipts, matrix


def build_case_index(receipts: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], bytes]:
    lines: list[bytes] = []
    entries: list[dict[str, Any]] = []
    for ordinal, receipt in enumerate(receipts, start=1):
        line = canonical_json(receipt)
        lines.append(line)
        entries.append({
            "ordinal": ordinal,
            "case_id": receipt["case_id"],
            "case_receipt_digest": receipt["content_digest"],
            "line_sha256": "sha256:" + sha256_bytes(line),
        })
    value: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_w6_case_index_v2",
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
        "case_count": 168,
        "entries": entries,
        "ordered_case_digest": digest(entries),
        "content_digest": None,
    }
    value["content_digest"] = self_digest(value)
    return value, b"\n".join(lines) + b"\n"


def parse_w5_trace(receipt: Mapping[str, Any]) -> dict[str, Any]:
    output = receipt.get("normalized_output")
    if not isinstance(output, str):
        raise CertificationError("w5_trace_output_missing")
    prefix = "P2_S1_W5_EXECUTION_TRACE="
    matches = [line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)]
    if len(matches) != 1:
        raise CertificationError("w5_trace_population_invalid")
    value = strict_json_bytes(matches[0].encode("utf-8"), "w5_execution_trace")
    if not isinstance(value, dict) or value.get("trace_source") != "runtime_execution_spy_and_content_addressed_store":
        raise CertificationError("w5_trace_invalid")
    return value


def verify_runner_receipt(suite_id: str) -> dict[str, Any]:
    path = RECEIPT_ROOT / f"{suite_id}.json"
    value = load_json(path)
    if value.get("suite_id") != suite_id or value.get("stage") != "W6":
        raise CertificationError(f"w6_runner_identity_mismatch:{suite_id}")
    if value.get("runner_version") != "1.3.0":
        raise CertificationError(f"w6_runner_version_mismatch:{suite_id}")
    if value.get("passed") is not True or any(value.get(key) != 0 for key in ("exit_code", "failure_count", "error_count", "skipped_count")):
        raise CertificationError(f"w6_runner_failed:{suite_id}")
    expected = digest(value, {"receipt_digest"}).removeprefix("sha256:")
    if value.get("receipt_digest") != expected:
        raise CertificationError(f"w6_runner_digest_mismatch:{suite_id}")
    bindings = value.get("artifact_bindings")
    if not isinstance(bindings, list):
        raise CertificationError(f"w6_runner_artifacts_missing:{suite_id}")
    for item in bindings:
        path_value = safe_repo_path(item["path"])
        if file_sha(path_value) != item["sha256"] or path_value.stat().st_size != item["size_bytes"]:
            raise CertificationError(f"w6_runner_artifact_drift:{suite_id}:{item.get('path')}")
    return value


def elapsed_seconds(receipt: Mapping[str, Any]) -> int:
    started = datetime.fromisoformat(str(receipt["started_at_utc"]).replace("Z", "+00:00"))
    completed = datetime.fromisoformat(str(receipt["completed_at_utc"]).replace("Z", "+00:00"))
    seconds = int((completed - started).total_seconds())
    if seconds < 0:
        raise CertificationError("runner_time_order_invalid")
    return seconds


def build_runtime_proof(
    candidate: Mapping[str, Any], case_index: Mapping[str, Any]
) -> dict[str, Any]:
    w5_python = load_json(RECEIPT_ROOT / "w5-python.json")
    w5_sidecar = load_json(RECEIPT_ROOT / "w5-sidecar.json")
    trace = parse_w5_trace(w5_python)
    chain = trace.get("admission_event_chain")
    if not isinstance(chain, dict) or len(chain.get("events", [])) != 14:
        raise CertificationError("w5_14_event_chain_invalid")
    expected_actions = [
        "plan_design_validated", "plan_runtime_admitted", "running_committed", "first_dispatch",
        "result_set_built", "result_set_design_validated", "result_set_runtime_admitted",
        "result_set_published", "evidence_graph_built", "evidence_graph_design_validated",
        "evidence_graph_runtime_admitted", "evidence_graph_receipts_published",
        "evidence_graph_published", "final_investigation_cas_committed",
    ]
    if [event.get("event_kind") for event in chain["events"]] != expected_actions:
        raise CertificationError("w5_14_event_order_invalid")
    proof: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_w6_same_candidate_runtime_proof_v2",
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
        "candidate_binding_digest": candidate["content_digest"],
        "case_index_digest": case_index["content_digest"],
        "w5_python_runner_receipt_digest": "sha256:" + w5_python["receipt_digest"],
        "w5_sidecar_runner_receipt_digest": "sha256:" + w5_sidecar["receipt_digest"],
        "actual_execution_trace": {
            "trace_digest": "sha256:" + trace["trace_digest"],
            "chain_digest": chain["chain_digest"],
            "event_count": 14,
            "event_order": expected_actions,
            "execution_id": chain["execution_id"],
            "investigation_id": chain["investigation_id"],
            "registry_snapshot_digest": chain["registry_snapshot_digest"],
            "plan_result_graph_design_validator_replays": len(trace["design_semantic_validator_replays"]),
            "result_set_committed": trace["result_set_committed"],
            "evidence_graph_committed": trace["evidence_graph_committed"],
            "export_committed": trace["export_committed"],
            "running_cancel_verified": trace["running_cancel_verified"],
            "cas_crash_recovery_replayed_same_outcome": trace["cas_crash_recovery_replayed_same_outcome"],
            "business_unit_ids": trace["business_unit_invocation_ids"],
            "control_unit_ids": trace["invoked_control_unit_ids"],
            "dynamic_fanout_count": trace["dynamic_fanout_count"],
            "arbitrary_callback_count": trace["arbitrary_callback_count"],
        },
        "fixture_protocol": {
            "sidecar_tests_run": w5_sidecar["tests_run"],
            "transport": "local_fixture_injection_only",
            "external_provider_called": False,
            "teacher_reference_is_ground_truth": False,
        },
        "question_answer_certification": False,
        "external_provider_called": False,
        "production_deployed": False,
        "content_digest": None,
    }
    proof["content_digest"] = self_digest(proof)
    return proof


def build_performance_recovery_cost(
    candidate: Mapping[str, Any],
    case_index: Mapping[str, Any],
    runners: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    w5_python = load_json(RECEIPT_ROOT / "w5-python.json")
    w5_sidecar = load_json(RECEIPT_ROOT / "w5-sidecar.json")
    measurements = {
        "w5_runtime_and_api_suite_seconds": elapsed_seconds(w5_python),
        "w5_sidecar_suite_seconds": elapsed_seconds(w5_sidecar),
        "w6_python_certification_suite_seconds": elapsed_seconds(runners["w6-python"]),
        "w6_sidecar_replay_suite_seconds": elapsed_seconds(runners["w6-sidecar"]),
        "w6_recovery_attack_suite_seconds": elapsed_seconds(runners["w6-recovery-attack"]),
        "w5_runtime_tests_run": w5_python["tests_run"],
        "w5_sidecar_tests_run": w5_sidecar["tests_run"],
        "w6_recovery_attack_tests_run": runners["w6-recovery-attack"]["tests_run"],
    }
    thresholds = {
        "w5_runtime_and_api_suite_max_seconds": 300,
        "w5_sidecar_suite_max_seconds": 180,
        "w6_recovery_attack_suite_max_seconds": 180,
    }
    local_performance_passed = (
        measurements["w5_runtime_and_api_suite_seconds"] <= thresholds["w5_runtime_and_api_suite_max_seconds"]
        and measurements["w5_sidecar_suite_seconds"] <= thresholds["w5_sidecar_suite_max_seconds"]
        and measurements["w6_recovery_attack_suite_seconds"] <= thresholds["w6_recovery_attack_suite_max_seconds"]
    )
    value: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_w6_performance_recovery_cost_v2",
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
        "candidate_binding_digest": candidate["content_digest"],
        "case_index_digest": case_index["content_digest"],
        "offline_deterministic_measurement": {
            "measurement_source": "content_addressed_subprocess_run_receipts",
            "measurements": measurements,
            "thresholds": thresholds,
            "offline_deterministic_runtime_performance_acceptance_passed": local_performance_passed,
            "characterization_completed": True,
            "not_a_provider_measurement": True,
        },
        "recovery_evidence": {
            "runner_receipt_digest": "sha256:" + runners["w6-recovery-attack"]["receipt_digest"],
            "cas_journal_crash_recovery_verified": True,
            "pointer_before_idempotency_recovery_verified": True,
            "cancel_wins_worker_cas_verified": True,
            "rerun_revision_and_idempotency_verified": True,
            "unadmitted_result_set_graph_public_residue_count": 0,
            "offline_recovery_acceptance_passed": True,
        },
        "cost_evidence": {
            "external_provider_call_count": 0,
            "fixture_cost_accounting_mode": "synthetic_fixture_receipt_only",
            "provider_billed_amount": None,
            "provider_price_attestation": None,
            "actual_provider_cost_acceptance": False,
        },
        "actual_provider_performance_acceptance": False,
        "actual_provider_cost_acceptance": False,
        "runtime_promotion": False,
        "production_deployed": False,
        "content_digest": None,
    }
    if not local_performance_passed:
        raise CertificationError("offline_runtime_performance_threshold_failed")
    value["content_digest"] = self_digest(value)
    return value


def verify_case_population(
    receipts: Sequence[Mapping[str, Any]],
    matrix: Mapping[str, Any] | None = None,
    *,
    reference_receipts: Sequence[Mapping[str, Any]] | None = None,
    reference_matrix: Mapping[str, Any] | None = None,
) -> None:
    if len(receipts) != 168:
        raise CertificationError("case_count_mismatch")
    expected_ids = [f"{question_id}-{code}" for question_id in QUESTION_IDS for code, _ in SCENARIOS]
    actual_ids = [item.get("case_id") for item in receipts]
    if actual_ids != expected_ids or len(set(actual_ids)) != 168:
        raise CertificationError("case_order_population_or_duplicate_invalid")
    if reference_receipts is None or reference_matrix is None:
        evidence, _ = load_w5_bindings()
        reference_receipts, reference_matrix = build_case_receipts(
            evidence,
            load_json(ORACLE_PATH),
            load_json(SEED_PATH),
            load_json(MAP_PATH),
        )
    if len(reference_receipts) != 168:
        raise CertificationError("reference_case_population_invalid")
    blocked = 0
    deferred = 0
    for ordinal, (item, reference) in enumerate(
        zip(receipts, reference_receipts, strict=True), start=1
    ):
        validate_schema(item, CASE_SCHEMA_PATH)
        verify_self_digest(item)
        if (
            item.get("implementation_candidate_id") != W5_CANDIDATE_ID
            or item.get("implementation_candidate_digest") != W5_CANDIDATE_DIGEST
        ):
            raise CertificationError(f"case_candidate_binding_mismatch:{item.get('case_id')}")
        question_id = item["question_id"]
        if item.get("oracle_binding") != reference.get("oracle_binding"):
            raise CertificationError(f"case_oracle_binding_mismatch:{item['case_id']}")
        if item.get("machine_facts_digest") != reference.get("machine_facts_digest"):
            raise CertificationError(f"case_machine_facts_binding_mismatch:{item['case_id']}")
        if item.get("expected_disposition") != reference.get("expected_disposition") or item.get("expected_reason_codes") != reference.get("expected_reason_codes"):
            raise CertificationError(f"case_frozen_contract_mismatch:{item['case_id']}")
        if item.get("actual_disposition") != reference.get("actual_disposition") or item.get("actual_reason_codes") != reference.get("actual_reason_codes"):
            raise CertificationError(f"case_actual_classification_not_recomputed:{item['case_id']}")
        expected = reference["expected_disposition"]
        if item["actual_disposition"] != expected:
            raise CertificationError(f"case_disposition_overclaim:{item['case_id']}")
        if any(item["overclaim_checks"].values()):
            raise CertificationError(f"case_overclaim:{item['case_id']}")
        if item["unit_coverage"]["actually_dispatched"]:
            raise CertificationError(f"blocked_case_dispatch_overclaim:{item['case_id']}")
        if item.get("case_acceptance") != reference.get("case_acceptance") or item["case_acceptance"].get("case_passed") is not True:
            raise CertificationError(f"case_acceptance_not_derived:{item['case_id']}")
        if item != reference:
            raise CertificationError(f"case_receipt_not_recomputed:{ordinal}:{item['case_id']}")
        blocked += expected == "correctly_blocked"
        deferred += expected == "correctly_deferred"
    if (blocked, deferred) != (162, 6):
        raise CertificationError("case_disposition_counts_invalid")
    if matrix is not None:
        verify_self_digest(matrix)
        if [item["case_id"] for item in matrix.get("cases", [])] != expected_ids:
            raise CertificationError("case_matrix_order_invalid")
        if matrix != reference_matrix:
            raise CertificationError("case_matrix_machine_facts_not_recomputed")


def verify_jsonl_bytes(data: bytes) -> list[dict[str, Any]]:
    if not data.endswith(b"\n") or data.startswith(b"\xef\xbb\xbf"):
        raise CertificationError("jsonl_framing_invalid")
    values = []
    for ordinal, line in enumerate(data.splitlines(), start=1):
        value = strict_json_bytes(line, f"case-receipts.jsonl:{ordinal}")
        if not isinstance(value, dict) or canonical_json(value) != line:
            raise CertificationError(f"jsonl_noncanonical:{ordinal}")
        values.append(value)
    return values


def verify_candidate_binding(
    candidate: Mapping[str, Any],
    expected_candidate: Mapping[str, Any] | None = None,
) -> None:
    verify_self_digest(candidate)
    if expected_candidate is None:
        evidence, stage = load_w5_bindings()
        expected_candidate = build_candidate(evidence, stage)
    if candidate != expected_candidate:
        raise CertificationError("candidate_binding_not_recomputed")


def verify_runtime_proof(
    runtime: Mapping[str, Any],
    expected_runtime: Mapping[str, Any],
) -> None:
    verify_self_digest(runtime)
    trace = runtime.get("actual_execution_trace")
    if not isinstance(trace, Mapping) or trace.get("event_count") != 14:
        raise CertificationError("runtime_proof_14_event_chain_missing")
    if runtime != expected_runtime:
        raise CertificationError("runtime_proof_not_recomputed")


def assert_no_review_cycles(value: Mapping[str, Any]) -> None:
    text = canonical_json(value).decode("utf-8")
    forbidden = ("acceptance-manifest", "product-semantic-review", "bgp-semantic-review")
    if any(item in text for item in forbidden):
        raise CertificationError("review_cycle_or_mutual_reference")


def assert_no_acceptance_cycles(value: Mapping[str, Any]) -> None:
    text = canonical_json(value).decode("utf-8")
    forbidden = ("wave-evidence/W6.json", "stages/W6.json", "acceptance-manifest.json#self")
    if any(item in text for item in forbidden):
        raise CertificationError("acceptance_digest_cycle")


def _attack_input_digest(value: Any) -> str:
    if isinstance(value, bytes):
        return "sha256:" + sha256_bytes(value)
    return digest(value)


def _expect_attack_rejected(
    attack_id: str,
    verifier_entrypoint: str,
    attack_input: Any,
    probe: Any,
) -> dict[str, Any]:
    try:
        probe()
    except CertificationError as error:
        return {
            "attack_id": attack_id,
            "rejected": True,
            "verifier_entrypoint": verifier_entrypoint,
            "rejection_code": str(error).split(":", 1)[0],
            "attack_input_digest": _attack_input_digest(attack_input),
        }
    raise CertificationError(f"attack_not_rejected:{attack_id}")


def run_attack_probes(
    receipts: Sequence[Mapping[str, Any]],
    *,
    matrix: Mapping[str, Any] | None = None,
    candidate: Mapping[str, Any] | None = None,
    case_index: Mapping[str, Any] | None = None,
    runtime: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    evidence, stage = load_w5_bindings()
    oracle = load_json(ORACLE_PATH)
    seed = load_json(SEED_PATH)
    capability_map = load_json(MAP_PATH)
    reference_receipts, reference_matrix = build_case_receipts(
        evidence, oracle, seed, capability_map
    )
    if matrix is None:
        matrix = reference_matrix
    if candidate is None:
        candidate = build_candidate(evidence, stage)
    expected_candidate = build_candidate(evidence, stage)
    if case_index is None:
        case_index, _ = build_case_index(reference_receipts)
    if runtime is None:
        runtime = build_runtime_proof(candidate, case_index)
    expected_runtime = build_runtime_proof(candidate, case_index)

    def verify_population(population: Sequence[Mapping[str, Any]]) -> None:
        verify_case_population(
            population,
            matrix,
            reference_receipts=reference_receipts,
            reference_matrix=reference_matrix,
        )

    attacks: list[tuple[str, Any]] = []
    attacks.append(("167_cases", list(receipts[:-1])))
    attacks.append(("169_cases", [*receipts, copy.deepcopy(receipts[-1])]))
    duplicate = list(receipts)
    duplicate[-1] = copy.deepcopy(receipts[0])
    attacks.append(("duplicate_case", duplicate))
    reordered = list(receipts)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    attacks.append(("reordered_case", reordered))
    executed = copy.deepcopy(receipts)
    executed[0]["actual_disposition"] = "executed_supported"
    executed[0]["content_digest"] = self_digest(executed[0])
    attacks.append(("blocked_forged_as_executed", executed))
    provider = copy.deepcopy(receipts)
    provider[0]["overclaim_checks"]["provider_model_claimed"] = True
    provider[0]["content_digest"] = self_digest(provider[0])
    attacks.append(("provider_overclaim", provider))
    cross_candidate = copy.deepcopy(receipts)
    cross_candidate[0]["implementation_candidate_digest"] = "sha256:" + "0" * 64
    cross_candidate[0]["content_digest"] = self_digest(cross_candidate[0])
    attacks.append(("cross_candidate_splice", cross_candidate))
    subgoal = copy.deepcopy(receipts)
    target = next(item for item in subgoal if item["question_id"] == "Q20")
    target["actual_disposition"] = "correctly_deferred"
    target["content_digest"] = self_digest(target)
    attacks.append(("deferred_subgoal_forged_as_whole_question", subgoal))
    results: list[dict[str, Any]] = []
    for attack_id, population in attacks:
        results.append(_expect_attack_rejected(
            attack_id,
            "verify_case_population",
            population,
            lambda population=population: verify_population(population),
        ))
    strict_attacks = {
        "duplicate_json_key": b'{"a":1,"a":2}',
        "nan": b'{"a":NaN}',
        "infinity": b'{"a":Infinity}',
        "bom": b'\xef\xbb\xbf{"a":1}',
    }
    for attack_id, payload in strict_attacks.items():
        results.append(_expect_attack_rejected(
            attack_id,
            "strict_json_bytes",
            payload,
            lambda payload=payload, attack_id=attack_id: strict_json_bytes(payload, attack_id),
        ))

    noncanonical = canonical_json(receipts[0]) + b" \n"
    results.append(_expect_attack_rejected(
        "noncanonical_jsonl", "verify_jsonl_bytes", noncanonical,
        lambda: verify_jsonl_bytes(noncanonical),
    ))

    oracle_splice = copy.deepcopy(list(receipts))
    oracle_splice[0]["oracle_binding"]["oracle_digest"] = "sha256:" + "0" * 64
    oracle_splice[0]["oracle_binding"]["question_record_digest"] = "sha256:" + "1" * 64
    oracle_splice[0]["oracle_binding"]["scenario_expectation_digest"] = "sha256:" + "2" * 64
    oracle_splice[0]["content_digest"] = self_digest(oracle_splice[0])
    results.append(_expect_attack_rejected(
        "oracle_question_splice", "verify_case_population", oracle_splice,
        lambda: verify_population(oracle_splice),
    ))

    stale_candidate = copy.deepcopy(candidate)
    stale_candidate["artifact_manifest"][0]["sha256"] = "0" * 64
    stale_candidate["candidate_payload_digest"] = digest(
        stale_candidate, {"candidate_payload_digest", "content_digest"}
    )
    stale_candidate["content_digest"] = self_digest(stale_candidate)
    results.append(_expect_attack_rejected(
        "stale_w5_artifact_digest", "verify_candidate_binding", stale_candidate,
        lambda: verify_candidate_binding(stale_candidate, expected_candidate),
    ))

    generic_boundary = copy.deepcopy(list(receipts))
    boundary_target = next(item for item in generic_boundary if item["question_id"] == "Q29")
    boundary_target["actual_disposition"] = "correctly_bounded"
    boundary_target["proof_mode"] = "host_boundary_execution"
    boundary_target["blocked_proof"] = None
    boundary_target["boundary_proof"] = {"generic_boundary_only": True}
    boundary_target["content_digest"] = self_digest(boundary_target)
    results.append(_expect_attack_rejected(
        "generic_boundary_forged_as_question_specific", "verify_case_population", generic_boundary,
        lambda: verify_population(generic_boundary),
    ))

    missing_admission = copy.deepcopy(runtime)
    missing_admission["actual_execution_trace"].pop("result_set_committed", None)
    missing_admission["content_digest"] = self_digest(missing_admission)
    results.append(_expect_attack_rejected(
        "missing_plan_result_graph_admission", "verify_runtime_proof", missing_admission,
        lambda: verify_runtime_proof(missing_admission, expected_runtime),
    ))

    legacy_copy = copy.deepcopy(list(receipts))
    legacy_target = legacy_copy[0]
    legacy_target["actual_disposition"] = "executed_supported"
    legacy_target["proof_mode"] = "actual_w5_runtime_execution"
    legacy_target["runtime_proof"] = {"copied_value_without_bridge": True}
    legacy_target["blocked_proof"] = None
    legacy_target["unit_coverage"]["actually_dispatched"] = ["TOOL-01"]
    legacy_target["content_digest"] = self_digest(legacy_target)
    results.append(_expect_attack_rejected(
        "legacy_value_copy_without_bridge_receipt", "verify_case_population", legacy_copy,
        lambda: verify_population(legacy_copy),
    ))

    hidden_fanout = copy.deepcopy(list(receipts))
    fanout_target = next(item for item in hidden_fanout if item["question_id"] == "Q20")
    fanout_target["unit_coverage"]["actually_dispatched"] = ["PLAN-CAP-02"]
    fanout_target["content_digest"] = self_digest(fanout_target)
    results.append(_expect_attack_rejected(
        "hidden_fanout_or_p2_1_smuggling", "verify_case_population", hidden_fanout,
        lambda: verify_population(hidden_fanout),
    ))

    cross_binding = copy.deepcopy(list(receipts))
    cross_binding[0]["same_candidate_checks"]["publication_equal_or_not_applicable"] = False
    cross_binding[0]["same_candidate_checks"]["registry_equal"] = False
    cross_binding[0]["content_digest"] = self_digest(cross_binding[0])
    results.append(_expect_attack_rejected(
        "cross_publication_registry_binding", "verify_case_population", cross_binding,
        lambda: verify_population(cross_binding),
    ))

    monotonic_candidate = copy.deepcopy(candidate)
    monotonic_candidate["started_monotonic_ns"] = 123456789
    monotonic_candidate["candidate_payload_digest"] = digest(
        monotonic_candidate, {"candidate_payload_digest", "content_digest"}
    )
    monotonic_candidate["content_digest"] = self_digest(monotonic_candidate)
    results.append(_expect_attack_rejected(
        "monotonic_measurement_in_semantic_identity", "verify_candidate_binding", monotonic_candidate,
        lambda: verify_candidate_binding(monotonic_candidate, expected_candidate),
    ))

    path_escape = "../outside-w6.json"
    results.append(_expect_attack_rejected(
        "symlink_or_path_escape", "safe_repo_path", path_escape,
        lambda: safe_repo_path(path_escape),
    ))

    cyclic_review = {"finding": "引用 product-semantic-review.json 形成互引"}
    results.append(_expect_attack_rejected(
        "review_self_or_mutual_reference", "assert_no_review_cycles", cyclic_review,
        lambda: assert_no_review_cycles(cyclic_review),
    ))

    cyclic_acceptance = {"artifact_ref": "wave-evidence/W6.json"}
    results.append(_expect_attack_rejected(
        "acceptance_digest_cycle", "assert_no_acceptance_cycles", cyclic_acceptance,
        lambda: assert_no_acceptance_cycles(cyclic_acceptance),
    ))
    if len(results) != 24 or any(item["rejected"] is not True for item in results):
        raise CertificationError("attack_population_or_result_invalid")
    return results


def build_attack_evidence(
    candidate: Mapping[str, Any],
    case_index: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
    matrix: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    results = run_attack_probes(
        receipts,
        matrix=matrix,
        candidate=candidate,
        case_index=case_index,
        runtime=runtime,
    )
    value: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_w6_attack_evidence_v2",
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
        "candidate_binding_digest": candidate["content_digest"],
        "case_index_digest": case_index["content_digest"],
        "attack_count": len(results),
        "attacks": results,
        "all_attacks_rejected": all(item["rejected"] is True for item in results),
        "accepted": len(results) == 24 and all(item["rejected"] is True for item in results),
        "acceptance_meaning": "listed_certification_overclaim_and_integrity_attacks_rejected",
        "content_digest": None,
    }
    value["content_digest"] = self_digest(value)
    return value


def build_certifier_manifest() -> dict[str, Any]:
    roles = {
        CERTIFIER_PATH.as_posix(): "w6_certifier",
        RUNNER_PATH.as_posix(): "stage_test_runner",
        W6_TEST_PATH.as_posix(): "w6_certification_test",
        CASE_SCHEMA_PATH.as_posix(): "case_receipt_schema",
        REVIEW_SCHEMA_PATH.as_posix(): "independent_review_schema",
        MANIFEST_SCHEMA_PATH.as_posix(): "acceptance_manifest_schema",
        HOOK_PATH.as_posix(): "implementation_alignment_hook",
        HOOK_TEST_PATH.as_posix(): "implementation_alignment_hook_test",
        DOC_PATH.as_posix(): "w6_chinese_acceptance_explanation",
    }
    artifacts = []
    for path_text, role in roles.items():
        path = safe_repo_path(path_text)
        if not path.is_file():
            raise CertificationError(f"certifier_artifact_missing:{path_text}")
        artifacts.append({
            "path": path_text,
            "role": role,
            "size_bytes": path.stat().st_size,
            "sha256": file_sha(path),
        })
    value: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_w6_certifier_manifest_v2",
        "certification_semantics_version": SEMANTICS_VERSION,
        "certifier_id": "country_outage_p2_s1_w6_offline_certifier",
        "certifier_version": "2.0.0",
        "generated_result_paths_excluded": True,
        "artifacts": artifacts,
        "content_digest": None,
    }
    value["content_digest"] = self_digest(value)
    return value


def review_input_digest(
    candidate: Mapping[str, Any],
    semantics: Mapping[str, Any],
    disposition_contract: Mapping[str, Any],
    matrix: Mapping[str, Any],
    certifier: Mapping[str, Any],
    case_index: Mapping[str, Any],
    runtime: Mapping[str, Any],
    performance: Mapping[str, Any],
    attack: Mapping[str, Any],
) -> str:
    return digest({
        "candidate_digest": candidate["content_digest"],
        "question_disposition_contract_digest": disposition_contract["content_digest"],
        "case_matrix_digest": matrix["content_digest"],
        "certifier_manifest_digest": certifier["content_digest"],
        "case_index_digest": case_index["content_digest"],
        "runtime_proof_digest": runtime["content_digest"],
        "performance_digest": performance["content_digest"],
        "attack_digest": attack["content_digest"],
        "semantics_digest": semantics["content_digest"],
    })


def artifact_ref(path: Path, content_digest: str) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "sha256": file_sha(safe_repo_path(path)),
        "content_digest": content_digest,
    }


def validate_review(
    role: str,
    value: Mapping[str, Any],
    expected_review_input: str,
    candidate: Mapping[str, Any],
    disposition_contract: Mapping[str, Any],
    matrix: Mapping[str, Any],
    certifier: Mapping[str, Any],
    case_index: Mapping[str, Any],
    runtime: Mapping[str, Any],
    performance: Mapping[str, Any],
    attack: Mapping[str, Any],
) -> None:
    validate_schema(value, REVIEW_SCHEMA_PATH)
    verify_self_digest(value)
    expected = {
        "reviewer_role": role,
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
        "review_input_digest": expected_review_input,
        "question_disposition_contract_digest": disposition_contract["content_digest"],
        "case_matrix_digest": matrix["content_digest"],
        "certifier_manifest_digest": certifier["content_digest"],
        "case_index_digest": case_index["content_digest"],
        "runtime_proof_digest": runtime["content_digest"],
        "performance_digest": performance["content_digest"],
        "attack_digest": attack["content_digest"],
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise CertificationError(f"review_binding_mismatch:{role}:{key}")
    assert_no_review_cycles(value)


def build_acceptance_manifest(
    candidate: Mapping[str, Any],
    semantics: Mapping[str, Any],
    contract: Mapping[str, Any],
    matrix: Mapping[str, Any],
    certifier: Mapping[str, Any],
    case_index: Mapping[str, Any],
    runtime: Mapping[str, Any],
    performance: Mapping[str, Any],
    attack: Mapping[str, Any],
    reviews: Mapping[str, Mapping[str, Any]],
    runner_receipts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if set(reviews) != {"product_semantic", "bgp_semantic"}:
        raise CertificationError("independent_review_population_incomplete")
    if any(
        review.get("review_disposition")
        != "accepted_for_pre_provider_release_preparation_with_explicit_question_blocks"
        for review in reviews.values()
    ):
        raise CertificationError("independent_review_revision_required")
    refs = {
        "certification_semantics": artifact_ref(CERT_ROOT / "certification-semantics.json", semantics["content_digest"]),
        "implementation_candidate": artifact_ref(CERT_ROOT / "implementation-candidate.json", candidate["content_digest"]),
        "question_disposition_contract": artifact_ref(CERT_ROOT / "question-disposition-contract.json", contract["content_digest"]),
        "case_matrix": artifact_ref(CERT_ROOT / "case-matrix.json", matrix["content_digest"]),
        "certifier_manifest": artifact_ref(CERT_ROOT / "certifier-manifest.json", certifier["content_digest"]),
        "case_index": artifact_ref(CERT_ROOT / "case-index.json", case_index["content_digest"]),
        "same_candidate_runtime_proof": artifact_ref(CERT_ROOT / "same-candidate-runtime-proof.json", runtime["content_digest"]),
        "performance_recovery_cost": artifact_ref(CERT_ROOT / "performance-recovery-cost.json", performance["content_digest"]),
        "attack_evidence": artifact_ref(CERT_ROOT / "attack-evidence.json", attack["content_digest"]),
    }
    for suite_id, receipt in runner_receipts.items():
        refs[f"runner_{suite_id}"] = artifact_ref(
            RECEIPT_ROOT / f"{suite_id}.json", "sha256:" + receipt["receipt_digest"]
        )
    review_refs = {
        role: artifact_ref(REVIEW_PATHS[role], reviews[role]["content_digest"])
        for role in ("product_semantic", "bgp_semantic")
    }
    value: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_w6_acceptance_manifest_v2",
        "certification_semantics_version": SEMANTICS_VERSION,
        "status": "implementation_accepted_for_release_preparation",
        "implementation_accepted_for_release_preparation": True,
        "acceptance_meaning": "冻结W5实现候选、离线确定性能力与28题可执行性分类已按同候选证据验收，可进入实际provider认证及独立发布准备；不表示问题回答、provider模型、provider性能、runtime promotion或生产通过。",
        "implementation_candidate": {
            "implementation_candidate_id": W5_CANDIDATE_ID,
            "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
            "w5_commit": W5_COMMIT,
        },
        "artifact_refs": refs,
        "question_summary": {
            "question_count": 28,
            "case_count": 168,
            "blocked_question_count": 27,
            "deferred_question_count": 1,
            "executed_supported_question_count": 0,
            "correctly_bounded_question_count": 0,
            "blocked_case_count": 162,
            "deferred_case_count": 6,
            "p2_1_deferred_subgoal_question_ids": ["Q20", "Q23", "Q26"],
        },
        "review_refs": review_refs,
        "same_candidate_28_question_classification_completed": True,
        "fixture_protocol_certified": True,
        "deterministic_runtime_performance_characterized": True,
        "offline_recovery_acceptance_passed": True,
        "full_question_answer_certification_passed": False,
        "actual_provider_model_alignment": False,
        "actual_provider_performance_acceptance": False,
        "actual_provider_cost_acceptance": False,
        "runtime_promotion": False,
        "production_deployed": False,
        "external_provider_call_count": 0,
        "content_digest": None,
    }
    value["content_digest"] = self_digest(value)
    assert_no_acceptance_cycles(value)
    validate_schema(value, MANIFEST_SCHEMA_PATH)
    return value


def build_w6_evidence(
    candidate: Mapping[str, Any],
    manifest: Mapping[str, Any],
    runner_receipts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    artifact_paths = [
        CERTIFIER_PATH, RUNNER_PATH, W6_TEST_PATH, CASE_SCHEMA_PATH, REVIEW_SCHEMA_PATH,
        MANIFEST_SCHEMA_PATH, HOOK_PATH, HOOK_TEST_PATH, DOC_PATH,
        *[CERT_ROOT / name for name in (
            "certification-semantics.json", "implementation-candidate.json",
            "certifier-manifest.json", "question-disposition-contract.json",
            "case-matrix.json", "case-receipts.jsonl", "case-index.json",
            "same-candidate-runtime-proof.json", "performance-recovery-cost.json",
            "attack-evidence.json", "review-input.json",
            "product-semantic-review.json", "bgp-semantic-review.json",
            "acceptance-manifest.json",
        )],
    ]
    artifacts = []
    for path in artifact_paths:
        actual = safe_repo_path(path)
        artifacts.append({
            "path": path.as_posix(),
            "role": "w6_certification_artifact",
            "size_bytes": actual.stat().st_size,
            "sha256": file_sha(actual),
        })
    test_receipts = []
    categories = {
        "w6-python": "same_candidate_168_case_certification",
        "w6-sidecar": "unchanged_local_fixture_protocol_replay",
        "w6-recovery-attack": "cas_recovery_cancel_idempotency_and_residue",
    }
    for suite_id in RUNNER_SUITES:
        path = RECEIPT_ROOT / f"{suite_id}.json"
        receipt = runner_receipts[suite_id]
        test_receipts.append({
            "suite_id": suite_id,
            "category": categories[suite_id],
            "path": path.as_posix(),
            "sha256": file_sha(safe_repo_path(path)),
            "receipt_digest": receipt["receipt_digest"],
        })
    p0 = load_json("evaluation/country-outage/p2-s1-implementation-planning/stages/S1I-P0.json")
    evidence: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_implementation_wave_evidence_v1",
        "stage": "W6",
        "status": "implementation_accepted_for_release_preparation",
        "design_candidate_id": DESIGN_CANDIDATE_ID,
        "baseline_content_digest": load_json(IMPLEMENTATION_ROOT / "implementation-baseline.json")["content_digest"],
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "implementation_semantic_digest": W5_CANDIDATE_DIGEST.removeprefix("sha256:"),
        "content_digest": None,
        "effect": "same_candidate_28_question_offline_certification_accepted",
        "effect_verified": True,
        "implemented_unit_ids": [],
        "atomic_split_tests_passed": True,
        "p2_1_units_included": [],
        "production_deployed": False,
        "artifact_manifest": artifacts,
        "test_receipts": test_receipts,
        "acceptance_manifest": {
            "path": (CERT_ROOT / "acceptance-manifest.json").as_posix(),
            "sha256": file_sha(safe_repo_path(CERT_ROOT / "acceptance-manifest.json")),
            "content_digest": manifest["content_digest"],
        },
        "certification_scope": {
            "same_candidate_28_question_classification_completed": True,
            "blocked_question_count": 27,
            "deferred_question_count": 1,
            "executed_supported_question_count": 0,
            "case_count": 168,
            "blocked_case_count": 162,
            "deferred_case_count": 6,
            "full_question_answer_certification_passed": False,
            "fixture_protocol_certified": True,
            "offline_recovery_acceptance_passed": True,
            "actual_provider_model_alignment": False,
            "actual_provider_performance_acceptance": False,
            "actual_provider_cost_acceptance": False,
            "runtime_promotion": False,
            "production_deployed": False,
            "external_provider_call_count": 0,
        },
        "acceptance_scope": {
            "status": "implementation_accepted_for_release_preparation",
            "implementation_accepted_for_release_preparation": True,
            "acceptance_meaning": manifest["acceptance_meaning"],
            "release_preparation_authorized": True,
            "provider_certification_authorized": False,
            "runtime_promotion_passed": False,
            "production_deployed": False,
        },
        "prior_stage_receipt_digests": {
            "S1I-P0": p0["receipt_digest"],
            "W5": W5_STAGE_RECEIPT_DIGEST,
        },
    }
    evidence["content_digest"] = self_digest(evidence).removeprefix("sha256:")
    return evidence


def generate() -> dict[str, Any]:
    evidence, stage = load_w5_bindings()
    oracle = load_json(ORACLE_PATH)
    seed = load_json(SEED_PATH)
    capability_map = load_json(MAP_PATH)
    oracle_questions = question_map(oracle, "oracle")
    candidate = build_candidate(evidence, stage)
    semantics = build_semantics(candidate)
    disposition_contract = build_disposition_contract(candidate, oracle_questions)
    receipts, matrix = build_case_receipts(evidence, oracle, seed, capability_map)
    verify_case_population(receipts, matrix)
    case_index, jsonl = build_case_index(receipts)

    write_json(CERT_ROOT / "certification-semantics.json", semantics)
    write_json(CERT_ROOT / "implementation-candidate.json", candidate)
    write_json(CERT_ROOT / "question-disposition-contract.json", disposition_contract)
    write_json(CERT_ROOT / "case-matrix.json", matrix)
    write_bytes(CERT_ROOT / "case-receipts.jsonl", jsonl)
    write_json(CERT_ROOT / "case-index.json", case_index)

    runner_receipts = {suite_id: verify_runner_receipt(suite_id) for suite_id in RUNNER_SUITES}
    runtime = build_runtime_proof(candidate, case_index)
    performance = build_performance_recovery_cost(candidate, case_index, runner_receipts)
    attack = build_attack_evidence(candidate, case_index, receipts, matrix, runtime)
    certifier = build_certifier_manifest()
    write_json(CERT_ROOT / "same-candidate-runtime-proof.json", runtime)
    write_json(CERT_ROOT / "performance-recovery-cost.json", performance)
    write_json(CERT_ROOT / "attack-evidence.json", attack)
    write_json(CERT_ROOT / "certifier-manifest.json", certifier)
    review_input = {
        "schema_version": "country_outage_p2_s1_w6_review_input_v2",
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "implementation_candidate_digest": W5_CANDIDATE_DIGEST,
        "review_input_digest": review_input_digest(
            candidate, semantics, disposition_contract, matrix, certifier,
            case_index, runtime, performance, attack
        ),
        "question_disposition_contract_digest": disposition_contract["content_digest"],
        "case_matrix_digest": matrix["content_digest"],
        "certifier_manifest_digest": certifier["content_digest"],
        "case_index_digest": case_index["content_digest"],
        "runtime_proof_digest": runtime["content_digest"],
        "performance_digest": performance["content_digest"],
        "attack_digest": attack["content_digest"],
        "semantics_digest": semantics["content_digest"],
        "content_digest": None,
    }
    review_input["content_digest"] = self_digest(review_input)
    write_json(CERT_ROOT / "review-input.json", review_input)

    reviews: dict[str, dict[str, Any]] = {}
    revision_required_roles: list[str] = []
    for role, path in REVIEW_PATHS.items():
        if safe_repo_path(path).is_file():
            review = load_json(path)
            validate_review(
                role, review, review_input["review_input_digest"], candidate,
                disposition_contract, matrix, certifier, case_index, runtime,
                performance, attack,
            )
            if review["review_disposition"] == "accepted_for_pre_provider_release_preparation_with_explicit_question_blocks":
                reviews[role] = review
            else:
                revision_required_roles.append(role)
    manifest = None
    if set(reviews) == set(REVIEW_PATHS):
        manifest = build_acceptance_manifest(
            candidate, semantics, disposition_contract, matrix, certifier,
            case_index, runtime, performance, attack, reviews, runner_receipts,
        )
        write_json(CERT_ROOT / "acceptance-manifest.json", manifest)
        w6_evidence = build_w6_evidence(candidate, manifest, runner_receipts)
        write_json(W6_EVIDENCE_PATH, w6_evidence)
    return {
        "status": (
            "implementation_accepted_for_release_preparation"
            if manifest else (
                "independent_reviews_revision_required"
                if revision_required_roles else "independent_reviews_pending"
            )
        ),
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "case_count": len(receipts),
        "blocked_case_count": 162,
        "deferred_case_count": 6,
        "review_input_digest": review_input["review_input_digest"],
        "acceptance_manifest_digest": manifest["content_digest"] if manifest else None,
    }


def read_jsonl() -> list[dict[str, Any]]:
    path = safe_repo_path(CERT_ROOT / "case-receipts.jsonl")
    return verify_jsonl_bytes(path.read_bytes())


def verify() -> dict[str, Any]:
    evidence, stage = load_w5_bindings()
    candidate = load_json(CERT_ROOT / "implementation-candidate.json")
    semantics = load_json(CERT_ROOT / "certification-semantics.json")
    disposition_contract = load_json(CERT_ROOT / "question-disposition-contract.json")
    matrix = load_json(CERT_ROOT / "case-matrix.json")
    case_index = load_json(CERT_ROOT / "case-index.json")
    runtime = load_json(CERT_ROOT / "same-candidate-runtime-proof.json")
    performance = load_json(CERT_ROOT / "performance-recovery-cost.json")
    attack = load_json(CERT_ROOT / "attack-evidence.json")
    certifier = load_json(CERT_ROOT / "certifier-manifest.json")
    review_input = load_json(CERT_ROOT / "review-input.json")
    for value in (
        candidate, semantics, disposition_contract, matrix, case_index, runtime,
        performance, attack, certifier, review_input,
    ):
        verify_self_digest(value)
    expected_candidate = build_candidate(evidence, stage)
    if candidate != expected_candidate:
        raise CertificationError("candidate_binding_not_recomputed")
    receipts = read_jsonl()
    verify_case_population(receipts, matrix)
    expected_index, expected_jsonl = build_case_index(receipts)
    if case_index != expected_index or safe_repo_path(CERT_ROOT / "case-receipts.jsonl").read_bytes() != expected_jsonl:
        raise CertificationError("case_index_or_jsonl_binding_mismatch")
    runner_receipts = {suite_id: verify_runner_receipt(suite_id) for suite_id in RUNNER_SUITES}
    expected_review_input = review_input_digest(
        candidate, semantics, disposition_contract, matrix, certifier,
        case_index, runtime, performance, attack
    )
    if review_input.get("review_input_digest") != expected_review_input:
        raise CertificationError("review_input_digest_mismatch")
    expected_review_fields = {
        "question_disposition_contract_digest": disposition_contract["content_digest"],
        "case_matrix_digest": matrix["content_digest"],
        "certifier_manifest_digest": certifier["content_digest"],
    }
    for field, expected_value in expected_review_fields.items():
        if review_input.get(field) != expected_value:
            raise CertificationError(f"review_input_binding_mismatch:{field}")
    reviews = {}
    identities = set()
    for role, path in REVIEW_PATHS.items():
        value = load_json(path)
        validate_review(
            role, value, expected_review_input, candidate,
            disposition_contract, matrix, certifier, case_index,
            runtime, performance, attack,
        )
        if value.get("review_disposition") != "accepted_for_pre_provider_release_preparation_with_explicit_question_blocks":
            raise CertificationError(f"independent_review_revision_required:{role}")
        identities.add(value["reviewer_identity"])
        reviews[role] = value
    if len(identities) != 2:
        raise CertificationError("independent_reviewer_identity_not_distinct")
    manifest = load_json(CERT_ROOT / "acceptance-manifest.json")
    validate_schema(manifest, MANIFEST_SCHEMA_PATH)
    verify_self_digest(manifest)
    expected_manifest = build_acceptance_manifest(
        candidate, semantics, disposition_contract, matrix, certifier,
        case_index, runtime, performance, attack, reviews, runner_receipts,
    )
    if manifest != expected_manifest:
        raise CertificationError("acceptance_manifest_not_recomputed")
    w6 = load_json(W6_EVIDENCE_PATH)
    if w6.get("implementation_candidate_id") != W5_CANDIDATE_ID:
        raise CertificationError("w6_evidence_candidate_drift")
    if w6.get("acceptance_manifest", {}).get("content_digest") != manifest["content_digest"]:
        raise CertificationError("w6_manifest_binding_mismatch")
    if digest(w6, {"content_digest"}).removeprefix("sha256:") != w6.get("content_digest"):
        raise CertificationError("w6_evidence_content_digest_mismatch")
    return {
        "status": manifest["status"],
        "implementation_candidate_id": W5_CANDIDATE_ID,
        "case_count": 168,
        "blocked_question_count": 27,
        "deferred_question_count": 1,
        "full_question_answer_certification_passed": False,
        "actual_provider_model_alignment": False,
        "production_deployed": False,
        "acceptance_manifest_digest": manifest["content_digest"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P2-S1 W6 离线确定性实现认证")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--mode", choices=("generate", "verify"), required=True)
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve()
    if root != ROOT.resolve():
        raise SystemExit("W6认证必须在冻结仓库根执行")
    try:
        result = generate() if args.mode == "generate" else verify()
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
        return 0
    except CertificationError as error:
        print(f"P2-S1 W6认证失败：{error}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
