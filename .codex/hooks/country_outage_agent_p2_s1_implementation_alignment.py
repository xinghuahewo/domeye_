#!/usr/bin/env python3
"""P2-S1 实现工程阶段防跑偏 Hook。

S1I-P0/W0 保留既有验收；W1/W2 只有在原子单元人口、真实实现制品、
冻结合同、W0 Source 谱系、Registry 整波激活及三类测试均闭合时才放行。
W3-W6 继续 fail-closed，直到各自实现任务补齐同等级证据。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Sequence


DESIGN_CANDIDATE_ID = "country-outage-p2-s1-s1d-6-04135cee55b39ce5d574f7e4"
DESIGN_CANDIDATE_SHA256 = "dfc764ff34ca2d79f4580f3eb4f9792c4a10ed907c485182f877a9079b31f957"
DESIGN_CANDIDATE_CONTENT_DIGEST = "d0256d9f1246191df2d48432655ea384acb2e5a6844b15a78f80e4c9f5e55e74"
DESIGN_MANIFEST_SHA256 = "d5e5a6e31d600f7437c612792396a27d3418d82576dcbd6f871dd87b7c9abdbe"
S1D6_RECEIPT_SHA256 = "bd219692b3c899ec699a813c875f9ec0d36e394ffc99e8fc910c105c4c8eb883"
S1D6_RECEIPT_DIGEST = "e9bebac7d23ca78f4a942e4b7d21c9203d3de8420dcfc1db4a87627e91fea827"
FINAL_RECEIPT_SHA256 = "eeb5df8ec8433b9e70f73c9e94a31639c2d7fe012f39435d97f9f0b7047e55fb"
FINAL_RECEIPT_DIGEST = "3ef7e71ddd9bcaf2ed0fb762cc0b3d217c6f4b8047f2eacbbaaba7712bfa0619"
QUESTION_IDS = [
    "Q01", "Q02", "Q03", "Q04", "Q05", "Q06", "Q07", "Q08", "Q09", "Q10",
    "Q13", "Q14", "Q16", "Q17", "Q18", "Q19", "Q20", "Q21", "Q22", "Q23",
    "Q24", "Q26", "Q27", "Q29", "Q30", "Q31", "Q32", "Q33",
]
TOOL_IDS = [f"TOOL-{number:02d}" for number in range(7, 13)]
OPERATOR_IDS = [
    *[f"OP-{number:02d}" for number in range(5, 34)],
    *[f"OP-{number:02d}" for number in range(35, 40)],
]
CONTROL_IDS = [
    "GATE-01", "GATE-02", "GATE-03", "GATE-04", "GATE-05", "BOUNDARY-01",
    "RENDERER-01", "RENDERER-02", "RENDERER-03", "DELIVERY-01",
]
STAGE_SEQUENCE = ["S1I-P0", "W0", "W1", "W2", "W3", "W4", "W5", "W6"]
WAVE_CONTRACT = {
    "W0": {
        "depends_on": [],
        "effect": "source_schema_view_registry_admission_and_trusted_receipt_store_implemented",
        "unit_ids": [],
    },
    "W1": {
        "depends_on": ["W0"],
        "effect": "offline_as_prefix_state_time_atomic_harness_and_non_callable_binding_verified",
        "unit_ids": [
            "TOOL-07", "TOOL-08", "TOOL-09", "TOOL-10",
            *[f"OP-{number:02d}" for number in range(5, 15)], "OP-35", "OP-36",
        ],
    },
    "W2": {
        "depends_on": ["W0"],
        "effect": "offline_window_path_set_count_atomic_harness_and_non_callable_binding_verified",
        "unit_ids": ["TOOL-12", *[f"OP-{number:02d}" for number in range(15, 29)]],
    },
    "W3": {
        "depends_on": ["W1", "W2"],
        "effect": "offline_state_interval_overlap_and_fixed_cohort_prefix_set_atomic_harness_and_non_callable_binding_verified",
        "unit_ids": ["OP-38", "OP-39"],
    },
    "W4": {
        "depends_on": ["W0", "W2"],
        "effect": "offline_exact_time_route_state_path_vp_consistency_atomic_harness_and_non_callable_binding_verified",
        "unit_ids": ["TOOL-11", "OP-29", "OP-30", "OP-31", "OP-32", "OP-33", "OP-37"],
    },
    "W5": {
        "depends_on": ["W1", "W2", "W3", "W4"],
        "effect": "investigation_plan_result_graph_api_ui_delivery_and_sol_host_ds_implemented",
        "unit_ids": ["PLAN-CAP-01", *CONTROL_IDS],
    },
    "W6": {
        "depends_on": ["W5"],
        "effect": "same_candidate_28_question_offline_certification_accepted",
        "unit_ids": [],
    },
}

TASK_PATH = Path(".codex/TASK.json")
TARGET_PATH = Path(
    "docs/agent/P2-组合式调查/实体调查实现工程/"
    "Task-Spec-P2-S1实现工程目标与最终验收.md"
)
PLAN_PATH = Path(
    "docs/agent/P2-组合式调查/实体调查实现工程/"
    "Plan-P2-S1实现工程分阶段计划.md"
)
BASELINE_PATH = Path(
    "contracts/agent/country-outage-p2-s1-implementation/implementation-baseline.json"
)
DESIGN_CANDIDATE_PATH = Path(
    "contracts/agent/country-outage-p2-s1-execution-unit-design/candidate.json"
)
DESIGN_MANIFEST_PATH = Path(
    "contracts/agent/country-outage-p2-s1-execution-unit-design/acceptance-manifest.json"
)
S1D6_PATH = Path("evaluation/country-outage/p2-s1-execution-unit-design/stages/S1D-6.json")
FINAL_PATH = Path("evaluation/country-outage/p2-s1-execution-unit-design/stages/final.json")
P0_RECEIPT_PATH = Path(
    "evaluation/country-outage/p2-s1-implementation-planning/stages/S1I-P0.json"
)
WAVE_EVIDENCE_ROOT = Path(
    "contracts/agent/country-outage-p2-s1-implementation/wave-evidence"
)
WAVE_RECEIPT_ROOT = Path("evaluation/country-outage/p2-s1-implementation/stages")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
W0_SOURCE_POPULATIONS = [
    "fixed_cohort_member_rows",
    "prefix_state_rows",
    "asn_state_rows",
    "new_prefix_state_rows",
    "materialized_route_state_rows_at_exact_time",
    "window_path_association_evidence_rows",
]
W0_SOURCE_SCHEMA_BY_POPULATION = {
    "fixed_cohort_member_rows": "fixed-cohort-member-row.schema.json",
    "prefix_state_rows": "prefix-state-row.schema.json",
    "asn_state_rows": "asn-state-row.schema.json",
    "new_prefix_state_rows": "new-prefix-state-row.schema.json",
    "materialized_route_state_rows_at_exact_time": "materialized-route-state-row.schema.json",
    "window_path_association_evidence_rows": "window-path-association-row.schema.json",
}
W0_SOURCE_FIXTURE_MANIFEST = Path(
    "contracts/data/country-outage-p2-s1/test-fixture/source-store/manifest.json"
)
W0_REQUIRED_RUNTIME_PATHS = {
    "tools/build_country_outage_p2_s1_source_views.py",
    "backend/services/country_outage_p2_s1_source_store.py",
    "agent-sidecar/src/chat/p2-s1-registry-runtime.ts",
    "agent-sidecar/src/chat/p2-s1-trusted-receipt-store.ts",
}
W0_ALLOWED_ARTIFACT_PREFIXES = (
    "contracts/data/country-outage-p2-s1/",
    "contracts/agent/country-outage-p2-s1-implementation/",
    "tools/build_country_outage_p2_s1_source_views.py",
    "backend/services/country_outage_p2_s1_source_store.py",
    "backend/web/tests/test_country_outage_p2_s1_source_store.py",
    "dev/tests/test_country_outage_p2_s1_w0_source_governance.py",
    "agent-sidecar/src/chat/p2-s1-registry-runtime.ts",
    "agent-sidecar/src/chat/p2-s1-trusted-receipt-store.ts",
    "agent-sidecar/tests/p2-s1-w0-source-governance.test.ts",
)
# 该摘要只标识 W1/W2 Task 创建时已经通过的前序 W0 回执。W0 在本 Task
# 内因 Source/Hook 收紧而重新签发后，W1/W2 必须绑定当前回执文件，不能把
# 这个历史 transition 摘要当作当前运行依赖，否则会形成 Task→Hook→W0
# receipt→Task 的摘要环。
W0_TRANSITION_RECEIPT_DIGEST = "3e875e80422e3c33528a39d24d08add6ea68f6e16e02f2ce22f7084119189351"
W1_W2_TASK_ID = "country-outage-agent-p2-s1-w1-w2-atomic-runtime-20260813"
W1_W2_TARGET_VERSION = "country-outage-agent-p2-s1-w1-w2-atomic-runtime-v1"
W3_W4_TASK_ID = "country-outage-agent-p2-s1-w3-w4-atomic-runtime-20260813"
W3_W4_TARGET_VERSION = "country-outage-agent-p2-s1-w3-w4-atomic-runtime-v1"
W3_W4_ERRATA_TASK_ID = "country-outage-agent-p2-s1-w3-w4-op33-empty-population-errata-20260813"
W3_W4_ERRATA_TARGET_VERSION = "country-outage-agent-p2-s1-w3-w4-op33-empty-population-errata-v1"
REGISTRY_WAVE_SEQUENCE = ["W1", "W2", "W3", "W4"]
STRUCTURAL_BINDING_PATH = Path(
    "contracts/agent/country-outage-p2-s1-implementation/w1-w2-structural-binding.schema.json"
)
TOOL_CATALOG_PATH = Path(
    "contracts/agent/country-outage-p2-s1-execution-unit-design/tool-catalog.json"
)
TOOL_CONTRACT_SCHEMA_PATH = Path(
    "contracts/agent/country-outage-p2-s1-execution-unit-design/tool-contract.schema.json"
)
OPERATOR_CATALOG_PATH = Path(
    "contracts/agent/country-outage-p2-s1-execution-unit-design/operator-catalog.json"
)
OPERATOR_CONTRACT_SCHEMA_PATH = Path(
    "contracts/agent/country-outage-p2-s1-execution-unit-design/operator-contract.schema.json"
)
W1_W2_TOOL_IMPLEMENTATION_PATH = Path("backend/services/country_outage_p2_s1_tools.py")
W1_W2_OPERATOR_IMPLEMENTATION_PATH = Path("backend/services/country_outage_p2_s1_operators.py")
W1_W2_TOOL_TEST_PATH = Path("backend/web/tests/test_country_outage_p2_s1_tools.py")
W1_W2_OPERATOR_TEST_PATH = Path("backend/web/tests/test_country_outage_p2_s1_operators.py")
W1_W2_TOOL_RUNTIME_SCHEMA_PATH = Path(
    "contracts/agent/country-outage-p2-s1-implementation/w1-w2-tool-runtime.schema.json"
)
STAGE_TEST_RUNNER_PATH = Path(
    "contracts/agent/country-outage-p2-s1-implementation/tools/run_stage_tests.py"
)
STAGE_TEST_RUN_RECEIPT_ROOT = Path(
    "contracts/agent/country-outage-p2-s1-implementation/wave-evidence/run-receipts"
)
W1_W2_REGISTRY_RUNTIME_PATH = Path("agent-sidecar/src/chat/p2-s1-registry-runtime.ts")
W1_W2_REGISTRY_TEST_PATH = Path("agent-sidecar/tests/p2-s1-w0-source-governance.test.ts")
W1_W2_REGISTRY_EVIDENCE_GENERATOR_PATH = Path(
    "contracts/agent/country-outage-p2-s1-implementation/tools/generate_registry_evidence.ts"
)
W1_W2_REGISTRY_EVIDENCE_ROOT = Path(
    "contracts/agent/country-outage-p2-s1-implementation/wave-evidence/registry-runtime"
)
W1_W2_REGISTRY_EVIDENCE_SHA256 = {
    "W1": "c8d0dad59e2f22be22580acc93166da3723543b2de1546ed1ab90568b55c0a06",
    "W2": "9cc53c0d84f43f86dc078420fd76e2b51328b83e0ce5b101b947a3d9878fdb35",
    "W3": "b2ae3d95e42439104b9073cf0f83aca54bc2fdb1adf007206bb64b6c6d94369b",
    "W4": "911c5c9595a4dd741aff4665eaf6cb375a95f47a444c7edd9c1c9c336f496510",
}
W1_W2_SOURCE_STORE_PATH = Path("backend/services/country_outage_p2_s1_source_store.py")
W1_W2_SOURCE_SCHEMA_PATHS = {
    population: Path("contracts/data/country-outage-p2-s1") / schema_name
    for population, schema_name in W0_SOURCE_SCHEMA_BY_POPULATION.items()
}
W1_W2_SHARED_ARTIFACT_ROLES = {
    W1_W2_TOOL_IMPLEMENTATION_PATH.as_posix(): "tool_implementation",
    W1_W2_OPERATOR_IMPLEMENTATION_PATH.as_posix(): "operator_implementation",
    W1_W2_TOOL_TEST_PATH.as_posix(): "tool_test",
    W1_W2_OPERATOR_TEST_PATH.as_posix(): "operator_test",
    W1_W2_TOOL_RUNTIME_SCHEMA_PATH.as_posix(): "tool_runtime_contract",
    W1_W2_REGISTRY_RUNTIME_PATH.as_posix(): "registry_runtime",
    W1_W2_REGISTRY_TEST_PATH.as_posix(): "registry_test",
    W1_W2_REGISTRY_EVIDENCE_GENERATOR_PATH.as_posix(): "registry_evidence_generator",
    STRUCTURAL_BINDING_PATH.as_posix(): "structural_binding_contract",
    TOOL_CATALOG_PATH.as_posix(): "frozen_tool_catalog",
    TOOL_CONTRACT_SCHEMA_PATH.as_posix(): "frozen_tool_contract_schema",
    OPERATOR_CATALOG_PATH.as_posix(): "frozen_operator_catalog",
    OPERATOR_CONTRACT_SCHEMA_PATH.as_posix(): "frozen_operator_contract_schema",
    W1_W2_SOURCE_STORE_PATH.as_posix(): "w0_source_store",
    W0_SOURCE_FIXTURE_MANIFEST.as_posix(): "w0_source_manifest",
    **{path.as_posix(): "w0_source_schema" for path in W1_W2_SOURCE_SCHEMA_PATHS.values()},
}
W1_W2_ALL_UNIT_IDS = set(WAVE_CONTRACT["W1"]["unit_ids"] + WAVE_CONTRACT["W2"]["unit_ids"])
W1_W4_ALL_UNIT_IDS = set(
    unit_id
    for stage in REGISTRY_WAVE_SEQUENCE
    for unit_id in WAVE_CONTRACT[stage]["unit_ids"]
)
P2_1_UNIT_IDS = {"PLAN-CAP-02", "TOOL-13", "OP-34"}
REGISTRY_SNAPSHOT_ID = re.compile(r"^p2-s1-registry-wave-sha256:[0-9a-f]{64}$")
STAGE_TEST_RUN_RECEIPTS = {
    "w0-python": ("W0", "source_and_store_positive_boundary_attack", "96d2714e5960a9e9803b63511c3f0370d6e54e6fb4a900bbee1d87a65580acd7"),
    "w0-typescript": ("W0", "registry_and_receipt_positive_boundary_attack", "ac32c3800e4c41bc39625103446116c32249b5a9240a8be8ca2cdc9d954b18c1"),
    "w1-positive": ("W1", "positive", "181a5187c8283d4b9cac1e5e72361d6275eed589b6bd23e0b352cf578c70c5fd"),
    "w1-boundary": ("W1", "boundary", "3d4fdc41d365310329f8d7e34021830c3c4fb4ff747ca66a26a712d871cab744"),
    "w1-attack": ("W1", "attack", "c28c779915ebd1409bb9db2a7570e19a285c258808691bb22b3c3cccedcccc3b"),
    "w2-positive": ("W2", "positive", "3d95af9ba2f7096d522857cf4ba37dcc35bc59010e9b455bfa541f9f71b48cce"),
    "w2-boundary": ("W2", "boundary", "5fc1aa54c6de15cbf011cb4befaa094feed4e30197c81f60cb65223e25de1dbd"),
    "w2-attack": ("W2", "attack", "65f4021f96130b914dd2efd020347bb8a98d5a5584577d775286be793f963618"),
    "w3-positive": ("W3", "positive", "c8a2392ce3c86f301bdb0688876c28eed4568897770dafd2fe2a8953266050ad"),
    "w3-boundary": ("W3", "boundary", "f21727356fb5d01dd43b4c53b8f081ac74c562d4b8bbef83137ee13c08581633"),
    "w3-attack": ("W3", "attack", "224e2533e68821e3d514467aff67be23ad96326aaebdcb37e71a99c112af0b84"),
    "w4-positive": ("W4", "positive", "7a0a0d4bd89fc7307fb3668bb0f5c1dbe2aa5c356fd93a6194adb3fcde5a1bb7"),
    "w4-boundary": ("W4", "boundary", "ae96a3eee481d3b56fc9961f6f5a0437ce47b0010fba37b78a7ceb8e207c6c35"),
    "w4-attack": ("W4", "attack", "dc0048407911451b6848377fad3a14ee1841b9805b732d5084404db51eb8f628"),
}


def atomic_wave_artifact_roles(stage: str) -> dict[str, str]:
    """返回当前原子波次精确制品人口；W4 继续复用同一 Tool 运行时合同。"""

    expect(stage in REGISTRY_WAVE_SEQUENCE, "wave_stage_not_implemented", f"{stage} 不是已登记原子波次")
    return dict(W1_W2_SHARED_ARTIFACT_ROLES)


def cumulative_registry_units(stage: str) -> list[str]:
    wave_index = REGISTRY_WAVE_SEQUENCE.index(stage)
    return [
        unit_id
        for prior_wave in REGISTRY_WAVE_SEQUENCE[: wave_index + 1]
        for unit_id in WAVE_CONTRACT[prior_wave]["unit_ids"]
    ]


def stage_prior_dependencies(stage: str) -> list[str]:
    """功能依赖之外，W4 签发还必须承接已经签发的 W3 Registry 前序。"""

    dependencies = list(WAVE_CONTRACT[stage]["depends_on"])
    if stage == "W4" and "W3" not in dependencies:
        dependencies.append("W3")
    return dependencies


class AlignmentError(RuntimeError):
    """实现规划或实施候选偏离冻结目标。"""


def _reject_constant(value: str) -> None:
    raise ValueError(f"禁止非有限 JSON 数值：{value}")


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"重复 JSON key：{key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise AlignmentError(f"无法严格读取 JSON：{path}：{error}") from error
    if not isinstance(value, dict):
        raise AlignmentError(f"JSON 顶层必须是对象：{path}")
    return value


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError as error:
        raise AlignmentError(f"无法读取文件：{path}：{error}") from error


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise AlignmentError(f"无法规范化 JSON：{error}") from error


def object_digest(value: dict[str, Any], excluded: set[str] | None = None) -> str:
    projected = {key: copy.deepcopy(item) for key, item in value.items() if key not in (excluded or set())}
    return sha256_bytes(canonical_json(projected))


def expect(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise AlignmentError(f"{code}：{detail}")


def exact(value: Any, expected: Any, code: str, detail: str) -> None:
    expect(value == expected, code, detail)


def require_hex64(value: Any, code: str, detail: str) -> str:
    expect(isinstance(value, str) and HEX64.fullmatch(value) is not None, code, detail)
    return value


def require_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise AlignmentError(f"无法读取文档：{path}：{error}") from error


def repository_artifact_path(root: Path, value: Any, code: str) -> tuple[str, Path]:
    expect(isinstance(value, str) and value, code, "制品路径必须是非空仓库相对路径")
    relative = Path(value)
    expect(not relative.is_absolute() and ".." not in relative.parts, code, f"非法制品路径：{value}")
    normalized = relative.as_posix()
    expect(normalized == value and not value.startswith("./"), code, f"制品路径必须是规范 POSIX 路径：{value}")
    path = root / relative
    try:
        expect(path.is_file() and not path.is_symlink(), code, f"制品不是普通非符号链接文件：{value}")
        expect(path.resolve().is_relative_to(root.resolve()), code, f"制品逃逸仓库：{value}")
    except OSError as error:
        raise AlignmentError(f"{code}：无法解析制品路径 {value}：{error}") from error
    return normalized, path


def validate_recomputable_receipt(item: Any, code: str, label: str) -> str:
    expect(isinstance(item, dict), code, f"{label} 必须是对象")
    digest = require_hex64(item.get("receipt_digest"), code, f"{label} receipt digest 无效")
    exact(object_digest(item, {"receipt_digest"}), digest, code, f"{label} receipt digest 不可重算")
    return digest


def validate_frozen_design(root: Path) -> list[str]:
    candidate_path = root / DESIGN_CANDIDATE_PATH
    manifest_path = root / DESIGN_MANIFEST_PATH
    s1d6_path = root / S1D6_PATH
    final_path = root / FINAL_PATH
    exact(file_sha256(candidate_path), DESIGN_CANDIDATE_SHA256, "design_candidate_stale", "设计候选字节摘要漂移")
    exact(file_sha256(manifest_path), DESIGN_MANIFEST_SHA256, "design_manifest_stale", "设计验收清单字节摘要漂移")
    exact(file_sha256(s1d6_path), S1D6_RECEIPT_SHA256, "s1d6_receipt_stale", "S1D-6 回执字节摘要漂移")
    exact(file_sha256(final_path), FINAL_RECEIPT_SHA256, "final_receipt_stale", "final 回执字节摘要漂移")
    candidate = load_json(candidate_path)
    manifest = load_json(manifest_path)
    s1d6 = load_json(s1d6_path)
    final = load_json(final_path)
    exact(candidate.get("design_candidate_id"), DESIGN_CANDIDATE_ID, "design_candidate_identity_mismatch", "设计候选 ID 不匹配")
    exact(candidate.get("content_digest"), DESIGN_CANDIDATE_CONTENT_DIGEST, "design_candidate_content_mismatch", "设计候选 content digest 不匹配")
    exact(candidate.get("runtime_implemented"), False, "design_runtime_overclaim", "冻结设计候选不得声称运行时已实现")
    exact(candidate.get("production_deployed"), False, "design_deployment_overclaim", "冻结设计候选不得声称已部署")
    exact(manifest.get("design_candidate_id"), DESIGN_CANDIDATE_ID, "design_manifest_identity_mismatch", "Manifest 候选 ID 不匹配")
    reviews = manifest.get("final_independent_reviews")
    expect(isinstance(reviews, list) and len(reviews) == 2, "design_review_open", "必须有产品语义和 BGP 两份独立终审")
    exact(
        {item.get("review_kind") for item in reviews if isinstance(item, dict)},
        {"product_semantic_final_candidate_review", "bgp_final_candidate_review"},
        "design_review_population_mismatch",
        "独立终审人口不匹配",
    )
    for review in reviews:
        expect(isinstance(review, dict), "design_review_invalid", "终审记录必须是对象")
        exact(review.get("execution_status"), "completed", "design_review_incomplete", "终审必须完成")
        exact(review.get("hard_gate_passed"), True, "design_review_failed", "终审硬门必须通过")
        exact(review.get("candidate_content_digest"), DESIGN_CANDIDATE_CONTENT_DIGEST, "design_review_binding_mismatch", "终审未绑定同一候选")
    for stage, receipt, receipt_digest in (
        ("S1D-6", s1d6, S1D6_RECEIPT_DIGEST),
        ("final", final, FINAL_RECEIPT_DIGEST),
    ):
        exact(receipt.get("stage"), stage, "design_receipt_stage_mismatch", f"{stage} 回执 stage 不匹配")
        exact(receipt.get("status"), "alignment_passed", "design_receipt_failed", f"{stage} 回执未通过")
        exact(receipt.get("design_candidate_id"), DESIGN_CANDIDATE_ID, "design_receipt_identity_mismatch", f"{stage} 未绑定同一候选")
        exact(receipt.get("receipt_digest"), receipt_digest, "design_receipt_digest_mismatch", f"{stage} receipt digest 不匹配")
        exact(object_digest(receipt, {"receipt_digest"}), receipt_digest, "design_receipt_not_recomputable", f"{stage} receipt digest 不可重算")
    return [
        "frozen_design_candidate_verified",
        "design_manifest_and_independent_reviews_verified",
        "s1d6_and_final_receipts_verified",
    ]


def validate_documents(root: Path) -> list[str]:
    target = require_text(root / TARGET_PATH)
    plan = require_text(root / PLAN_PATH)
    target_sections = [
        "## 四、必须达成的最终效果",
        "### 4.1 用户调查效果",
        "### 4.2 BGP 事实效果",
        "### 4.3 组合运行效果",
        "### 4.4 开发者与原子性效果",
        "### 4.5 运行治理效果",
        "## 九、最终验收旅程",
        "## 十一、实现完成定义",
    ]
    plan_sections = [
        "## 四、S1I-P0：实现规划与防跑偏基线",
        "## 五、W0：Source、Schema、Registry 与可信回执基础",
        "## 六、W1：ASN、前缀与状态时间",
        "## 七、W2：窗口路径、投影、集合与计数",
        "## 八、W3：异常区间交集与固定 cohort 前缀集合",
        "## 九、W4：Path-at-time、观察方向与 VP 一致性",
        "## 十、W5：组合调查运行时、接口和页面",
        "## 十一、W6：同候选离线认证与实现验收",
    ]
    for marker in target_sections:
        expect(marker in target, "target_effect_section_missing", f"目标文档缺少：{marker}")
    for marker in plan_sections:
        expect(marker in plan, "implementation_wave_section_missing", f"实施计划缺少：{marker}")
    for phrase in (
        "用户提出一个复杂国家中断调查目标",
        "一个 Tool 只读取一种冻结事实人口",
        "一个 Operator 只执行一种登记的确定性变换",
        "Sol→Host Grounding/Validation→DS",
        "implementation acceptance、runtime promotion 与 production deployment 是三个独立状态",
        "生产部署：禁止" if "生产部署：禁止" in target else "生产部署",
    ):
        expect(phrase in target, "target_effect_statement_missing", f"目标文档缺少效果声明：{phrase}")
    for stage in STAGE_SEQUENCE:
        expect(stage in plan, "implementation_stage_missing", f"实施计划缺少阶段：{stage}")
    expect(
        "写隐式循环" in plan
        and all(owner in plan for owner in ("Host", "Tool", "Operator", "Renderer", "API handler", "模型")),
        "hidden_fanout_boundary_missing",
        "实施计划未禁止各执行边界隐藏 fan-out",
    )
    expect("一个 Tool 只读取一种冻结事实人口" in plan, "tool_atomicity_goal_missing", "实施计划未冻结 Tool 功能原子性")
    expect("一个 Operator 只执行一种登记的确定性业务变换" in plan, "operator_atomicity_goal_missing", "实施计划未冻结 Operator 功能原子性")
    return ["effect_first_target_document_verified", "w0_w6_effect_and_exit_plan_verified"]


def validate_task(root: Path) -> list[str]:
    task = load_json(root / TASK_PATH)
    task_id = task.get("taskId")
    w0_task = task_id == "country-outage-agent-p2-s1-w0-source-governance-20260813"
    w1_w2_task = task_id == W1_W2_TASK_ID
    w3_w4_errata_task = task_id == W3_W4_ERRATA_TASK_ID
    w3_w4_task = task_id == W3_W4_TASK_ID or w3_w4_errata_task
    expect(w0_task or w1_w2_task or w3_w4_task, "task_identity_mismatch", "Task ID 不属于冻结的 W0、W1/W2 或 W3/W4 实现任务")
    if w0_task:
        exact(task.get("targetVersion"), "country-outage-agent-p2-s1-w0-source-governance-v1", "task_version_mismatch", "W0 目标版本不匹配")
    elif w1_w2_task:
        exact(task.get("targetVersion"), W1_W2_TARGET_VERSION, "task_version_mismatch", "W1/W2 目标版本不匹配")
    else:
        exact(
            task.get("targetVersion"),
            W3_W4_ERRATA_TARGET_VERSION if w3_w4_errata_task else W3_W4_TARGET_VERSION,
            "task_version_mismatch",
            "W3/W4 目标版本不匹配",
        )
    transition = task.get("taskTransition")
    expect(isinstance(transition, dict), "task_transition_missing", "缺少任务迁移记录")
    exact(transition.get("frozenDesignCandidateId"), DESIGN_CANDIDATE_ID, "task_design_binding_mismatch", "Task 未绑定冻结设计候选")
    exact(transition.get("frozenDesignCandidateSha256"), DESIGN_CANDIDATE_SHA256, "task_design_sha_mismatch", "Task 设计摘要不匹配")
    expected_p0_receipt = (
        "0d661430471008cd84115bcdd6e1fcc7e404619b9503f8cf41a7148f2ce63b59"
        if w3_w4_task
        else "2e2d72f18f030bb1f91e7037e6f88786f64d9b4b7865a36b82f605e7e701d838"
    )
    exact(transition.get("s1ip0ReceiptDigest"), expected_p0_receipt, "task_p0_binding_mismatch", "Task 未绑定创建任务时冻结的 S1I-P0 回执")
    forbidden = task.get("forbiddenPaths")
    expect(isinstance(forbidden, list), "task_forbidden_paths_missing", "缺少 forbiddenPaths")
    for pattern in ("backend/core/**", "backend/data_pipeline/**", "backend/database/**", "backend/web/api/**", "frontend/**", "deploy/**", "tools/rrc25-iran-replay-go/**"):
        expect(pattern in forbidden, "task_scope_expanded", f"任务未禁止修改 {pattern}")
    allowed = task.get("allowedPaths")
    expect(isinstance(allowed, list), "task_allowed_paths_missing", "缺少 allowedPaths")
    if w0_task:
        for path in W0_REQUIRED_RUNTIME_PATHS:
            expect(path in allowed, "w0_runtime_path_not_authorized", f"W0 未授权实现路径：{path}")
        return ["w0_task_boundary_verified"]
    if w1_w2_task:
        exact(transition.get("supersedesTaskId"), "country-outage-agent-p2-s1-w0-source-governance-20260813", "task_transition_invalid", "W1/W2 未显式继承 W0 Task")
        exact(transition.get("w0ReceiptDigest"), W0_TRANSITION_RECEIPT_DIGEST, "task_w0_binding_mismatch", "W1/W2 Task 未绑定创建时冻结的 W0 回执")
    else:
        exact(
            transition.get("supersedesTaskId"),
            W3_W4_TASK_ID if w3_w4_errata_task else W1_W2_TASK_ID,
            "task_transition_invalid",
            "W3/W4 未显式继承已通过的前序 Task",
        )
        exact(transition.get("implementationBaselineSha256"), "9dc80bec20db0c68ee044c4da9e4148a2a2ab7bd1c70c8863ae737cc6231422f", "task_baseline_transition_mismatch", "W3/W4 Task 创建时的实现基线摘要漂移")
        exact(transition.get("w0ReceiptDigest"), "cbab6787eeec1071c1c982063085f6fadb16e85e584c76289a43b31aecd4108c", "task_w0_binding_mismatch", "W3/W4 Task 未绑定创建时冻结的 W0 回执")
        exact(transition.get("w1ReceiptDigest"), "2ac94ea56923bd8dff140af56aa6e8a876860931f6bd8fd479a4908eeaa34c73", "task_w1_binding_mismatch", "W3/W4 Task 未绑定创建时冻结的 W1 回执")
        exact(transition.get("w2ReceiptDigest"), "b4672f844e559d3bdf44d713fd02f674ec431f4caacb77c3afa85886c27298a1", "task_w2_binding_mismatch", "W3/W4 Task 未绑定创建时冻结的 W2 回执")
        if w3_w4_errata_task:
            exact(transition.get("w3ReceiptDigest"), "f5c4dd1ea7208e023a9432b4ab4c273ca0f8cf4a5e15fbfeb181210ef542c6a2", "task_w3_binding_mismatch", "勘误 Task 未绑定前序 W3 回执")
            exact(transition.get("w4ReceiptDigest"), "2453e5d884e6ae821cd00573b467f430bfc838a5a6902b4ebc8d20402d246b6a", "task_w4_binding_mismatch", "勘误 Task 未绑定前序 W4 回执")
    for path in (
        W1_W2_TOOL_IMPLEMENTATION_PATH,
        W1_W2_OPERATOR_IMPLEMENTATION_PATH,
        W1_W2_TOOL_TEST_PATH,
        W1_W2_OPERATOR_TEST_PATH,
        W1_W2_REGISTRY_RUNTIME_PATH,
        STRUCTURAL_BINDING_PATH,
    ):
        path_text = path.as_posix()
        authorized = path_text in allowed or any(
            isinstance(pattern, str)
            and pattern.endswith("/**")
            and path_text.startswith(pattern[:-3] + "/")
            for pattern in allowed
        )
        expect(authorized, "wave_runtime_path_not_authorized", f"当前原子波次未授权实现路径：{path}")
    non_goals = task.get("explicitNonGoals")
    expect(isinstance(non_goals, list), "wave_non_goals_missing", "原子实现波次缺少显式非目标")
    non_goal_text = "\n".join(item for item in non_goals if isinstance(item, str))
    expected_non_goal_phrases = (
        ("每个Tool只能过滤和分页一种W0已验证事实人口", "每个Operator只能执行一种登记的确定性业务变换", "PLAN-CAP-02", "本阶段不修改生产")
        if w1_w2_task
        else ("TOOL-11只能读取W0预物化", "只能执行一种登记的确定性业务变换", "execution_allowed_unit_ids必须为空", "PLAN-CAP-02", "本阶段不修改生产")
    )
    for phrase in expected_non_goal_phrases:
        expect(phrase in non_goal_text, "wave_non_goals_missing", f"原子实现波次非目标未闭合：{phrase}")
    return ["w1_w2_task_boundary_verified" if w1_w2_task else "w3_w4_task_boundary_verified"]


def validate_baseline(root: Path) -> tuple[dict[str, Any], list[str]]:
    baseline = load_json(root / BASELINE_PATH)
    exact(baseline.get("schema_version"), "country_outage_p2_s1_implementation_baseline_v1", "baseline_schema_mismatch", "实现基线 Schema 不匹配")
    content_digest = require_hex64(baseline.get("content_digest"), "baseline_digest_invalid", "实现基线 content digest 必须是 lower hex64")
    exact(object_digest(baseline, {"content_digest"}), content_digest, "baseline_digest_mismatch", "实现基线 content digest 不可重算")
    frozen = baseline.get("frozen_design")
    expect(isinstance(frozen, dict), "baseline_design_binding_missing", "实现基线缺少 frozen_design")
    exact(frozen.get("candidate_id"), DESIGN_CANDIDATE_ID, "baseline_design_id_mismatch", "基线设计候选 ID 漂移")
    exact(frozen.get("candidate_sha256"), DESIGN_CANDIDATE_SHA256, "baseline_design_sha_mismatch", "基线设计候选 SHA 漂移")
    exact(frozen.get("acceptance_manifest_sha256"), DESIGN_MANIFEST_SHA256, "baseline_manifest_sha_mismatch", "基线 manifest SHA 漂移")
    exact(frozen.get("s1d6_receipt_digest"), S1D6_RECEIPT_DIGEST, "baseline_s1d6_digest_mismatch", "基线 S1D-6 receipt 漂移")
    exact(frozen.get("final_receipt_digest"), FINAL_RECEIPT_DIGEST, "baseline_final_digest_mismatch", "基线 final receipt 漂移")
    questions = baseline.get("question_contract")
    expect(isinstance(questions, dict), "question_contract_missing", "基线缺少问题人口")
    exact(questions.get("question_count"), 28, "question_count_drift", "问题数量必须为 28")
    exact(questions.get("question_ids"), QUESTION_IDS, "question_population_drift", "问题人口或顺序漂移")
    exact(questions.get("p2_v1_not_executable_question_ids"), ["Q24"], "non_executable_scope_drift", "Q24 边界漂移")
    exact(questions.get("external_evidence_required_question_ids"), ["Q29", "Q30"], "external_evidence_scope_drift", "外部证据问题边界漂移")
    population = baseline.get("implementation_population")
    expect(isinstance(population, dict), "implementation_population_missing", "基线缺少实现人口")
    exact(population.get("tool_ids"), TOOL_IDS, "tool_population_drift", "P2 v1 Tool 人口漂移")
    exact(population.get("operator_ids"), OPERATOR_IDS, "operator_population_drift", "P2 v1 Operator 人口漂移")
    exact(population.get("plan_capability_ids"), ["PLAN-CAP-01"], "plan_population_drift", "P2 v1 Plan capability 人口漂移")
    exact(population.get("control_unit_ids"), CONTROL_IDS, "control_population_drift", "控制与交付人口漂移")
    deferred = baseline.get("deferred_scope")
    expect(isinstance(deferred, dict), "deferred_scope_missing", "基线缺少 P2.1 延期边界")
    exact(deferred.get("tool_ids"), ["TOOL-13"], "deferred_tool_drift", "P2.1 Tool 延期人口漂移")
    exact(deferred.get("operator_ids"), ["OP-34"], "deferred_operator_drift", "P2.1 Operator 延期人口漂移")
    exact(deferred.get("plan_capability_ids"), ["PLAN-CAP-02"], "deferred_plan_drift", "P2.1 Plan capability 延期人口漂移")
    atomicity = baseline.get("atomicity_contract")
    expect(isinstance(atomicity, dict) and all(atomicity.get(key) is True for key in (
        "one_tool_one_fact_population", "one_operator_one_deterministic_transform",
        "hidden_fan_out_forbidden", "operator_external_read_forbidden",
        "operator_model_call_forbidden", "renderer_business_transform_forbidden",
        "tool_business_transform_forbidden", "tool_internal_unit_call_forbidden",
    )), "atomicity_contract_open", "Tool/Operator 功能原子性合同未闭合")
    exact(baseline.get("stage_sequence"), STAGE_SEQUENCE, "stage_sequence_drift", "实施阶段顺序漂移")
    waves = baseline.get("wave_contract")
    expect(isinstance(waves, list) and len(waves) == 7, "wave_contract_missing", "W0-W6 合同必须正好七项")
    actual_waves = {item.get("stage"): item for item in waves if isinstance(item, dict)}
    exact(set(actual_waves), set(WAVE_CONTRACT), "wave_population_drift", "W0-W6 阶段人口漂移")
    for stage, expected in WAVE_CONTRACT.items():
        exact(actual_waves[stage].get("depends_on"), expected["depends_on"], "wave_dependency_drift", f"{stage} 依赖漂移")
        exact(actual_waves[stage].get("effect"), expected["effect"], "wave_effect_drift", f"{stage} 最终效果漂移")
        exact(actual_waves[stage].get("unit_ids"), expected["unit_ids"], "wave_unit_population_drift", f"{stage} 执行单元人口漂移")
    layers = baseline.get("acceptance_layers")
    expect(isinstance(layers, dict), "acceptance_layers_missing", "缺少分层验收状态")
    exact(layers.get("design_contract_accepted"), True, "design_acceptance_lost", "设计合同必须保持已通过")
    for key in (
        "implementation_planning_accepted", "implementation_accepted", "model_alignment_passed",
        "performance_acceptance", "production_deployed", "registry_admission_performed",
        "runtime_implemented", "runtime_model_promotion",
    ):
        exact(layers.get(key), False, "implementation_status_overclaim", f"规划阶段不得把 {key} 标为 true")
    boundary = baseline.get("boundary_contract")
    expect(isinstance(boundary, dict), "boundary_contract_missing", "缺少边界合同")
    exact(boundary.get("collector_id"), "rrc25", "collector_scope_drift", "collector 必须为 rrc25")
    exact(boundary.get("publication_cardinality"), 1, "publication_scope_drift", "必须为单一 publication")
    for key in (
        "external_data", "network_rca", "customer_cone_inference", "recovery_judgment",
        "user_impact_judgment", "cross_country_comparison", "cross_event_comparison",
        "production_change_authorized",
    ):
        exact(boundary.get(key), False, "boundary_overclaim", f"边界字段 {key} 必须为 false")
    exact(boundary.get("read_only"), True, "read_only_boundary_lost", "P2 v1 必须只读")
    model = baseline.get("model_flow_contract")
    expect(isinstance(model, dict), "model_flow_contract_missing", "缺少 Sol→Host→DS 合同")
    exact(model.get("execution_order"), ["gpt-5.6-sol", "host_grounding_and_validation", "ds_student"], "model_order_drift", "模型执行顺序漂移")
    exact(model.get("teacher_reference_is_ground_truth"), False, "teacher_truth_conflation", "Teacher 不得成为事实真值")
    exact(model.get("successful_student_revision_max"), 1, "student_revision_scope_drift", "DS 成功修订上限必须为 1")
    effects = baseline.get("effect_contract")
    expect(isinstance(effects, dict), "effect_contract_missing", "机器基线缺少最终效果合同")
    for key, minimum in (("user_effects", 10), ("bgp_fact_effects", 6), ("runtime_effects", 8), ("developer_effects", 6), ("governance_effects", 6)):
        value = effects.get(key)
        expect(isinstance(value, list) and len(value) >= minimum and len(value) == len(set(value)), "effect_population_open", f"{key} 最终效果人口不完整或重复")
    return baseline, [
        "implementation_effect_contract_verified",
        "question_and_unit_populations_verified",
        "tool_and_operator_atomicity_verified",
        "w0_w6_dependencies_and_effects_verified",
        "p2_1_and_product_boundaries_verified",
        "sol_host_ds_boundary_verified",
    ]


def validate_w0_evidence(root: Path, evidence: dict[str, Any]) -> list[str]:
    """验证 W0 的真实代码、原子人口、治理和测试证据，而不是相信布尔自报。"""

    semantic_digest = require_hex64(
        evidence.get("implementation_semantic_digest"),
        "w0_candidate_digest_invalid",
        "W0 implementation semantic digest 无效",
    )
    expected_semantic = object_digest(
        evidence,
        {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
    )
    exact(semantic_digest, expected_semantic, "w0_candidate_digest_mismatch", "W0 implementation semantic digest 不可重算")
    exact(
        evidence.get("implementation_candidate_id"),
        f"country-outage-p2-s1-w0-{semantic_digest[:24]}",
        "w0_candidate_identity_mismatch",
        "W0 implementation candidate ID 不可由语义摘要重算",
    )
    content_digest = require_hex64(
        evidence.get("content_digest"), "w0_content_digest_invalid", "W0 content digest 无效"
    )
    exact(
        content_digest,
        object_digest(evidence, {"content_digest"}),
        "w0_content_digest_mismatch",
        "W0 evidence content digest 不可重算",
    )

    artifacts = evidence.get("artifact_manifest")
    expect(isinstance(artifacts, list) and artifacts, "w0_artifact_manifest_missing", "W0 缺少实现制品清单")
    seen_paths: set[str] = set()
    actual_roles: set[str] = set()
    for index, item in enumerate(artifacts):
        expect(isinstance(item, dict), "w0_artifact_invalid", f"artifact_manifest[{index}] 必须是对象")
        path_text, path = repository_artifact_path(root, item.get("path"), "w0_artifact_path_invalid")
        expect(path_text not in seen_paths, "w0_artifact_duplicate", f"W0 制品路径重复：{path_text}")
        seen_paths.add(path_text)
        expect(
            any(path_text == prefix or path_text.startswith(prefix) for prefix in W0_ALLOWED_ARTIFACT_PREFIXES),
            "w0_artifact_scope_invalid",
            f"W0 制品超出允许范围：{path_text}",
        )
        exact(item.get("size_bytes"), path.stat().st_size, "w0_artifact_size_mismatch", f"制品大小漂移：{path_text}")
        exact(item.get("sha256"), file_sha256(path), "w0_artifact_digest_mismatch", f"制品摘要漂移：{path_text}")
        role = item.get("role")
        expect(isinstance(role, str) and role, "w0_artifact_role_invalid", f"制品角色无效：{path_text}")
        actual_roles.add(role)
    expect(W0_REQUIRED_RUNTIME_PATHS <= seen_paths, "w0_runtime_artifact_missing", "W0 四个核心运行时制品不完整")
    expect(
        {"source_schema", "source_materializer", "source_store", "registry_runtime", "trusted_receipt_store", "test"} <= actual_roles,
        "w0_artifact_role_population_open",
        "W0 制品角色人口不完整",
    )

    fixture_manifest_path_text, fixture_manifest_path = repository_artifact_path(
        root, W0_SOURCE_FIXTURE_MANIFEST.as_posix(), "w0_fixture_manifest_path_invalid"
    )
    fixture_manifest = load_json(fixture_manifest_path)
    exact(
        fixture_manifest.get("schema_version"),
        "country_outage_p2_s1_source_store_manifest_v1",
        "w0_fixture_manifest_schema_mismatch",
        "W0 source fixture manifest Schema 不匹配",
    )
    exact(
        fixture_manifest.get("content_sha256"),
        object_digest(fixture_manifest, {"content_sha256"}),
        "w0_fixture_manifest_digest_mismatch",
        "W0 source fixture manifest content digest 不可重算",
    )
    fixture_store_id = fixture_manifest.get("store_id")
    expect(
        isinstance(fixture_store_id, str)
        and fixture_store_id.startswith("country_outage_p2_s1_source_store_v1_"),
        "w0_fixture_store_identity_invalid",
        "W0 source fixture store_id 无效",
    )
    fixture_populations = fixture_manifest.get("population_manifests")
    expect(
        isinstance(fixture_populations, list),
        "w0_fixture_population_manifest_missing",
        "W0 source fixture 缺少人口 manifests",
    )
    exact(
        [item.get("population_id") if isinstance(item, dict) else None for item in fixture_populations],
        W0_SOURCE_POPULATIONS,
        "w0_fixture_population_order_mismatch",
        "W0 source fixture 必须恰好包含六个冻结人口",
    )
    fixture_by_population = {item["population_id"]: item for item in fixture_populations}
    required_fixture_paths = {fixture_manifest_path_text}
    profile_ref = fixture_manifest.get("source_profiles_ref")
    expect(isinstance(profile_ref, dict), "w0_fixture_profile_ref_missing", "W0 source fixture 缺少 Profile 引用")
    profile_relative = profile_ref.get("path")
    expect(isinstance(profile_relative, str) and profile_relative, "w0_fixture_profile_ref_missing", "W0 source fixture Profile path 无效")
    fixture_root = W0_SOURCE_FIXTURE_MANIFEST.parent
    required_fixture_paths.add((fixture_root / profile_relative).as_posix())
    for population in W0_SOURCE_POPULATIONS:
        fixture_entry = fixture_by_population[population]
        for field in ("row_file", "index_file"):
            ref = fixture_entry.get(field)
            expect(isinstance(ref, dict) and isinstance(ref.get("path"), str), "w0_fixture_file_ref_missing", f"{population} 缺少 {field}")
            required_fixture_paths.add((fixture_root / ref["path"]).as_posix())
        receipt_ref = fixture_entry.get("materialization_receipt_ref")
        expect(isinstance(receipt_ref, str) and receipt_ref, "w0_fixture_receipt_ref_missing", f"{population} 缺少物化回执引用")
        required_fixture_paths.add((fixture_root / receipt_ref).as_posix())
    expect(
        required_fixture_paths <= seen_paths,
        "w0_fixture_artifact_population_open",
        "W0 artifact manifest 未完整绑定 source fixture 的 manifest/Profile/行/索引/回执",
    )

    receipts = evidence.get("source_and_governance_receipts")
    expect(isinstance(receipts, list), "w0_source_receipts_missing", "W0 缺少 source/governance 回执")
    source_receipts = [item for item in receipts if isinstance(item, dict) and item.get("receipt_kind") == "source_population_contract"]
    registry_receipts = [item for item in receipts if isinstance(item, dict) and item.get("receipt_kind") == "registry_governance_contract"]
    store_receipts = [item for item in receipts if isinstance(item, dict) and item.get("receipt_kind") == "trusted_receipt_store_contract"]
    exact(len(source_receipts), 6, "w0_source_population_receipt_count_mismatch", "W0 必须恰有六个人口合同回执")
    exact(len(registry_receipts), 1, "w0_registry_receipt_count_mismatch", "W0 必须恰有一份 Registry 治理回执")
    exact(len(store_receipts), 1, "w0_store_receipt_count_mismatch", "W0 必须恰有一份可信回执存储合同回执")
    exact(len(receipts), 8, "w0_governance_receipt_population_mismatch", "W0 source/governance 回执人口必须精确为八项")
    actual_populations = [item.get("population_id") for item in source_receipts]
    exact(actual_populations, W0_SOURCE_POPULATIONS, "w0_source_population_order_mismatch", "W0 六个人口必须按冻结顺序且无缺失")
    for item in source_receipts:
        population = item["population_id"]
        fixture_entry = fixture_by_population[population]
        validate_recomputable_receipt(item, "w0_source_receipt_digest_mismatch", population)
        exact(item.get("atomic_fact_population_count"), 1, "w0_source_atomicity_open", f"{population} 不是单一事实人口")
        exact(item.get("query_time_replay"), False, "w0_query_time_replay_enabled", f"{population} 禁止查询时回放")
        exact(item.get("query_time_path_parsing"), False, "w0_query_time_path_parse_enabled", f"{population} 禁止查询时解析路径")
        exact(item.get("query_time_business_transform"), False, "w0_query_business_transform_enabled", f"{population} 禁止查询时业务变换")
        exact(item.get("readiness"), "fixture_materialization_verified_authoritative_run_pending", "w0_source_contract_not_ready", f"{population} source adapter 合同未就绪或冒充权威全量运行")
        exact(item.get("fixture_manifest_path"), fixture_manifest_path_text, "w0_fixture_receipt_binding_mismatch", f"{population} 未绑定冻结 fixture manifest path")
        exact(item.get("fixture_manifest_sha256"), file_sha256(fixture_manifest_path), "w0_fixture_receipt_binding_mismatch", f"{population} 未绑定冻结 fixture manifest SHA")
        exact(item.get("fixture_store_id"), fixture_store_id, "w0_fixture_receipt_binding_mismatch", f"{population} 未绑定冻结 fixture store")
        exact(item.get("fixture_population_readiness"), fixture_entry.get("readiness"), "w0_fixture_receipt_binding_mismatch", f"{population} fixture readiness 漂移")
        exact(item.get("fixture_row_count"), fixture_entry.get("row_count"), "w0_fixture_receipt_binding_mismatch", f"{population} fixture row count 漂移")
        exact(item.get("fixture_member_keys_digest"), fixture_entry.get("member_keys_digest"), "w0_fixture_receipt_binding_mismatch", f"{population} fixture member keys 摘要漂移")
        exact(item.get("fixture_materialization_receipt_digest"), fixture_entry.get("materialization_receipt_digest"), "w0_fixture_receipt_binding_mismatch", f"{population} fixture 物化回执摘要漂移")
        schema_text, schema_path = repository_artifact_path(root, item.get("schema_path"), "w0_source_schema_path_invalid")
        expect(schema_text.startswith("contracts/data/country-outage-p2-s1/"), "w0_source_schema_scope_invalid", f"{population} Schema 不在 W0 目录")
        exact(schema_text, f"contracts/data/country-outage-p2-s1/{W0_SOURCE_SCHEMA_BY_POPULATION[population]}", "w0_source_schema_identity_mismatch", f"{population} Schema 身份漂移")
        exact(item.get("schema_sha256"), file_sha256(schema_path), "w0_source_schema_digest_mismatch", f"{population} Schema 摘要漂移")
        schema = load_json(schema_path)
        exact(schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema", "w0_source_schema_draft_mismatch", f"{population} 必须使用 Draft 2020-12")
        exact(schema.get("additionalProperties"), False, "w0_source_schema_open", f"{population} 顶层 Schema 必须闭合")
        source_refs = item.get("authoritative_source_refs")
        expect(isinstance(source_refs, list) and source_refs and len(source_refs) == len(set(source_refs)), "w0_source_lineage_open", f"{population} 权威源引用不完整或重复")
        for source_ref in source_refs:
            expect(isinstance(source_ref, str) and source_ref, "w0_source_lineage_invalid", f"{population} 源引用无效")
        if population == "new_prefix_state_rows":
            exact(item.get("projection_profile_id"), "PROFILE-NEW-PREFIX-FIXED-FIRST-OBSERVED-DIRECTIONS-1.0.0", "w0_new_prefix_profile_missing", "新前缀四态投影 Profile 未冻结")
        if population == "materialized_route_state_rows_at_exact_time":
            exact(item.get("state_semantics"), "all_events_with_event_time_strictly_before_state_point", "w0_exact_time_semantics_drift", "exact-time no-future 语义漂移")
            exact(item.get("left_checkpoint_only"), True, "w0_future_checkpoint_allowed", "exact-time 只能使用左 checkpoint")
        if population == "window_path_association_evidence_rows":
            exact(item.get("known_origin_must_equal_collapsed_path_tail"), True, "w0_path_origin_tail_open", "TOOL-12 known origin 尾端语义未闭合")
            exact(item.get("eligible_anchor_population_complete"), True, "w0_anchor_population_open", "TOOL-12 eligible anchor 人口未闭合")

    registry = registry_receipts[0]
    validate_recomputable_receipt(registry, "w0_registry_receipt_digest_mismatch", "Registry governance")
    exact(registry.get("new_unit_lifecycle_state"), "proposed", "w0_unit_activation_overclaim", "W0 新单元只能登记为 proposed")
    exact(registry.get("active_new_unit_ids"), [], "w0_unit_activation_overclaim", "W0 不得激活新执行单元")
    exact(registry.get("inactive_execution_call_count"), 0, "w0_inactive_execution_observed", "未 active 单元执行次数必须为 0")
    exact(registry.get("p2_1_unit_ids"), ["PLAN-CAP-02", "TOOL-13", "OP-34"], "w0_p2_1_denial_population_drift", "W0 P2.1 拒绝人口漂移")
    exact(registry.get("p2_1_admission"), "denied", "w0_p2_1_admitted", "W0 必须拒绝 P2.1 单元")
    exact(registry.get("publication_cardinality"), 1, "w0_registry_publication_scope_drift", "Registry 准入必须绑定单 publication")
    exact(registry.get("collector_id"), "rrc25", "w0_registry_collector_drift", "Registry 准入必须绑定 rrc25")
    for field in ("contract_sha256", "implementation_sha256", "attack_test_receipt_digest"):
        require_hex64(registry.get(field), "w0_registry_binding_invalid", f"Registry {field} 无效")
    for path_field, digest_field, role in (
        ("contract_path", "contract_sha256", "registry_contract"),
        ("implementation_path", "implementation_sha256", "registry_runtime"),
        ("attack_test_artifact_path", "attack_test_artifact_sha256", "test"),
    ):
        path_text, path = repository_artifact_path(root, registry.get(path_field), "w0_registry_binding_invalid")
        expect(path_text in seen_paths, "w0_registry_binding_invalid", f"Registry {path_field} 未进入 artifact manifest")
        exact(file_sha256(path), registry.get(digest_field), "w0_registry_binding_invalid", f"Registry {digest_field} 未绑定真实制品")
        expect(role in actual_roles, "w0_registry_binding_invalid", f"Registry 缺少 {role} 制品角色")
    exact(registry.get("attack_test_receipt_digest"), registry.get("attack_test_artifact_sha256"), "w0_registry_binding_invalid", "Registry 攻击测试回执未绑定测试制品")

    store = store_receipts[0]
    validate_recomputable_receipt(store, "w0_store_receipt_digest_mismatch", "trusted receipt store")
    exact(store.get("content_addressed"), True, "w0_store_not_content_addressed", "可信回执必须内容寻址")
    exact(store.get("atomic_write_and_recovery"), True, "w0_store_durability_open", "可信回执缺少原子写入/恢复")
    exact(store.get("caller_forged_receipt_rejected"), True, "w0_store_forgery_open", "可信回执存储未拒绝 caller forge")
    exact(store.get("cross_binding_replay_rejected"), True, "w0_store_replay_open", "可信回执存储未拒绝跨身份重放")
    for field in ("contract_sha256", "implementation_sha256", "attack_test_receipt_digest"):
        require_hex64(store.get(field), "w0_store_binding_invalid", f"可信回执存储 {field} 无效")
    for path_field, digest_field, role in (
        ("contract_path", "contract_sha256", "trusted_receipt_contract"),
        ("implementation_path", "implementation_sha256", "trusted_receipt_store"),
        ("attack_test_artifact_path", "attack_test_artifact_sha256", "test"),
    ):
        path_text, path = repository_artifact_path(root, store.get(path_field), "w0_store_binding_invalid")
        expect(path_text in seen_paths, "w0_store_binding_invalid", f"可信回执存储 {path_field} 未进入 artifact manifest")
        exact(file_sha256(path), store.get(digest_field), "w0_store_binding_invalid", f"可信回执存储 {digest_field} 未绑定真实制品")
        expect(role in actual_roles, "w0_store_binding_invalid", f"可信回执存储缺少 {role} 制品角色")
    exact(store.get("attack_test_receipt_digest"), store.get("attack_test_artifact_sha256"), "w0_store_binding_invalid", "可信回执攻击测试回执未绑定测试制品")

    performance = evidence.get("performance_baseline")
    expect(isinstance(performance, dict), "w0_performance_baseline_missing", "W0 缺少性能基线")
    exact(performance.get("measurement_status"), "fixture_baseline_not_w6_acceptance", "w0_performance_overclaim", "W0 性能只能是 fixture baseline")
    exact(performance.get("performance_acceptance_passed"), False, "w0_performance_overclaim", "W0 不得冒充 W6 性能验收")
    measurements = performance.get("measurements")
    expect(isinstance(measurements, list) and measurements, "w0_performance_measurements_missing", "W0 缺少实测数据")
    for item in measurements:
        expect(isinstance(item, dict), "w0_performance_measurement_invalid", "W0 性能测量必须是对象")
        for field in ("duration_ms", "row_count", "bytes", "peak_rss_bytes"):
            value = item.get(field)
            expect(isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0, "w0_performance_measurement_invalid", f"性能字段 {field} 无效")
        validate_recomputable_receipt(item, "w0_performance_receipt_digest_mismatch", "performance measurement")

    return [
        "w0_content_addressed_artifact_manifest_verified",
        "w0_six_atomic_source_population_contracts_verified",
        "w0_registry_proposed_only_zero_execution_verified",
        "w0_trusted_receipt_store_verified",
        "w0_fixture_performance_baseline_verified",
    ]


def _catalog_units(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    tool_catalog = load_json(root / TOOL_CATALOG_PATH)
    operator_catalog = load_json(root / OPERATOR_CATALOG_PATH)
    tools = tool_catalog.get("tools")
    operators = operator_catalog.get("operators")
    expect(isinstance(tools, list), "w1_w2_tool_catalog_invalid", "冻结 Tool catalog 缺少 tools")
    expect(isinstance(operators, list), "w1_w2_operator_catalog_invalid", "冻结 Operator catalog 缺少 operators")
    tool_map = {item.get("unit_id"): item for item in tools if isinstance(item, dict)}
    operator_map = {item.get("unit_id"): item for item in operators if isinstance(item, dict)}
    expect(W1_W4_ALL_UNIT_IDS <= set(tool_map) | set(operator_map), "w1_w2_catalog_population_open", "冻结 catalog 未覆盖 W1-W4 原子单元")
    return tool_map, operator_map


def _w1_w2_schema_refs(
    unit_id: str,
    tool_map: dict[str, dict[str, Any]],
    operator_map: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    if unit_id in tool_map:
        suffix = unit_id.removeprefix("TOOL-")
        return (
            f"w1-w2-tool-runtime.schema.json#/$defs/tool{suffix}Request",
            f"w1-w2-tool-runtime.schema.json#/$defs/tool{suffix}ResultPage",
        )
    unit = operator_map[unit_id]
    return str(unit.get("input_schema_ref")), str(unit.get("output_schema_ref"))


def _validate_bound_artifact(
    root: Path,
    item: Any,
    expected_path: str,
    expected_role: str,
    code: str,
) -> None:
    expect(isinstance(item, dict), code, f"{expected_path} 的引用必须是对象")
    exact(set(item), {"path", "role", "size_bytes", "sha256"}, code, f"{expected_path} 制品引用字段必须精确")
    exact(item.get("path"), expected_path, code, f"制品路径漂移：{expected_path}")
    exact(item.get("role"), expected_role, code, f"制品角色漂移：{expected_path}")
    _, path = repository_artifact_path(root, expected_path, code)
    exact(item.get("size_bytes"), path.stat().st_size, code, f"制品大小漂移：{expected_path}")
    exact(item.get("sha256"), file_sha256(path), code, f"制品摘要漂移：{expected_path}")


def _validate_stage_test_run_receipt(root: Path, reference: Any, expected_suite_id: str) -> dict[str, Any]:
    """解析真实 runner 制品；外层 evidence 不能通过重签布尔字段伪造测试成功。"""

    expect(isinstance(reference, dict), "wave_test_receipt_invalid", f"{expected_suite_id} 引用必须是对象")
    exact(
        set(reference),
        {"suite_id", "category", "path", "sha256", "receipt_digest"},
        "wave_test_receipt_invalid",
        f"{expected_suite_id} 引用字段不精确",
    )
    expected_stage, expected_category, pinned_sha = STAGE_TEST_RUN_RECEIPTS[expected_suite_id]
    expected_path = (STAGE_TEST_RUN_RECEIPT_ROOT / f"{expected_suite_id}.json").as_posix()
    exact(reference.get("suite_id"), expected_suite_id, "wave_test_receipt_invalid", "测试 suite ID 漂移")
    exact(reference.get("category"), expected_category, "wave_test_receipt_invalid", "测试分类漂移")
    exact(reference.get("path"), expected_path, "wave_test_receipt_invalid", "测试回执路径漂移")
    _, receipt_path = repository_artifact_path(root, expected_path, "wave_test_receipt_invalid")
    actual_sha = file_sha256(receipt_path)
    exact(actual_sha, pinned_sha, "wave_test_receipt_untrusted_resign", f"{expected_suite_id} 运行制品不是 Hook 冻结字节")
    exact(reference.get("sha256"), actual_sha, "wave_test_receipt_invalid", "测试回执文件摘要漂移")
    receipt = load_json(receipt_path)
    expected_fields = {
        "schema_version", "runner_id", "runner_version", "suite_id", "stage", "category",
        "started_at_utc", "completed_at_utc", "command", "working_directory",
        "selected_test_ids", "test_case_coverage", "tested_unit_ids", "tested_execution_unit_ids", "artifact_bindings", "exit_code", "tests_run",
        "failure_count", "error_count", "skipped_count", "passed", "normalized_output",
        "normalized_output_sha256", "receipt_digest",
    }
    exact(set(receipt), expected_fields, "wave_test_receipt_invalid", f"{expected_suite_id} 回执字段不精确")
    exact(receipt.get("schema_version"), "country_outage_p2_s1_stage_test_run_receipt_v1", "wave_test_receipt_invalid", "测试回执 Schema 漂移")
    exact(receipt.get("runner_id"), "country_outage_p2_s1_stage_test_runner", "wave_test_receipt_invalid", "测试 runner ID 漂移")
    exact(receipt.get("runner_version"), "1.0.0", "wave_test_receipt_invalid", "测试 runner version 漂移")
    exact(receipt.get("suite_id"), expected_suite_id, "wave_test_receipt_invalid", "测试 suite 漂移")
    exact(receipt.get("stage"), expected_stage, "wave_test_receipt_invalid", "测试 stage 漂移")
    exact(receipt.get("category"), expected_category, "wave_test_receipt_invalid", "测试 category 漂移")
    receipt_digest = require_hex64(receipt.get("receipt_digest"), "wave_test_digest_invalid", "测试 receipt digest 无效")
    exact(receipt_digest, object_digest(receipt, {"receipt_digest"}), "wave_test_digest_invalid", "测试 receipt digest 不可重算")
    exact(reference.get("receipt_digest"), receipt_digest, "wave_test_digest_invalid", "测试引用未绑定 receipt digest")
    output = receipt.get("normalized_output")
    expect(isinstance(output, str) and output, "wave_test_receipt_invalid", "测试输出为空")
    exact(receipt.get("normalized_output_sha256"), sha256_bytes(output.encode("utf-8")), "wave_test_output_digest_mismatch", "测试输出摘要不可重算")
    exact(receipt.get("exit_code"), 0, "wave_test_failed", "测试 exit code 非零")
    exact(receipt.get("passed"), True, "wave_test_failed", "测试未通过")
    for field in ("failure_count", "error_count", "skipped_count"):
        exact(receipt.get(field), 0, "wave_test_failed", f"测试 {field} 非零")
    selected = receipt.get("selected_test_ids")
    expect(isinstance(selected, list) and selected and len(selected) == len(set(selected)), "wave_test_receipt_invalid", "测试选择人口无效")
    coverage = receipt.get("test_case_coverage")
    expect(isinstance(coverage, list) and len(coverage) == len(selected), "wave_test_coverage_invalid", "测试逐选择器 coverage 人口无效")
    exact([item.get("test_id") for item in coverage if isinstance(item, dict)], selected, "wave_test_coverage_invalid", "测试 coverage 未逐项绑定选择器")
    covered_units: set[str] = set()
    executed_units: set[str] = set()
    for item in coverage:
        expect(isinstance(item, dict) and set(item) == {"test_id", "coverage_kind", "unit_ids", "executed_unit_ids"}, "wave_test_coverage_invalid", "测试 coverage 字段不精确")
        coverage_kind = item.get("coverage_kind")
        expect(coverage_kind in {"direct_execution", "direct_execution_and_schema_validation", "static_atomicity_analysis"}, "wave_test_coverage_invalid", "测试 coverage kind 无效")
        unit_ids = item.get("unit_ids")
        expect(isinstance(unit_ids, list) and len(unit_ids) == len(set(unit_ids)), "wave_test_coverage_invalid", "测试 coverage unit 人口无效")
        execution_ids = item.get("executed_unit_ids")
        expect(isinstance(execution_ids, list) and len(execution_ids) == len(set(execution_ids)), "wave_test_coverage_invalid", "测试 execution unit 人口无效")
        expect(set(execution_ids) <= set(unit_ids), "wave_test_coverage_invalid", "实际执行人口不是 coverage 人口子集")
        if coverage_kind == "static_atomicity_analysis":
            exact(execution_ids, [], "wave_test_coverage_invalid", "静态检查不得冒充执行覆盖")
        else:
            exact(execution_ids, unit_ids, "wave_test_coverage_invalid", "直接执行测试必须精确登记实际单元")
        covered_units.update(unit_ids)
        executed_units.update(execution_ids)
    exact(set(receipt.get("tested_unit_ids", [])), covered_units, "wave_test_coverage_invalid", "tested_unit_ids 不是逐测试 coverage 的精确并集")
    exact(set(receipt.get("tested_execution_unit_ids", [])), executed_units, "wave_test_coverage_invalid", "tested_execution_unit_ids 不是实际执行人口的精确并集")
    tests_run = receipt.get("tests_run")
    expect(isinstance(tests_run, int) and not isinstance(tests_run, bool) and tests_run > 0, "wave_test_receipt_invalid", "测试数量无效")
    if expected_stage in {"W1", "W2"}:
        exact(tests_run, len(selected), "wave_test_receipt_invalid", "定向测试数量与选择器不一致")
    artifacts = receipt.get("artifact_bindings")
    expect(isinstance(artifacts, list) and artifacts, "wave_test_artifact_mismatch", "测试回执缺少制品绑定")
    artifact_paths: set[str] = set()
    for item in artifacts:
        expect(isinstance(item, dict) and set(item) == {"path", "size_bytes", "sha256"}, "wave_test_artifact_mismatch", "测试制品绑定字段不精确")
        path_text = item.get("path")
        expect(isinstance(path_text, str) and path_text not in artifact_paths, "wave_test_artifact_mismatch", "测试制品路径重复或无效")
        artifact_paths.add(path_text)
        _, artifact_path = repository_artifact_path(root, path_text, "wave_test_artifact_mismatch")
        exact(item.get("size_bytes"), artifact_path.stat().st_size, "wave_test_artifact_mismatch", f"测试制品大小漂移：{path_text}")
        exact(item.get("sha256"), file_sha256(artifact_path), "wave_test_artifact_mismatch", f"测试制品摘要漂移：{path_text}")
    expect(STAGE_TEST_RUNNER_PATH.as_posix() in artifact_paths, "wave_test_runner_binding_missing", "测试回执未绑定 runner 字节")
    return receipt


def _p2s1_governance_digest(value: Any) -> str:
    def number(current: int) -> str:
        if current == 0:
            return "0"
        sign = "-" if current < 0 else ""
        digits = str(abs(current))
        trimmed = digits.rstrip("0")
        exponent = len(digits) - 1
        coefficient = trimmed if len(trimmed) == 1 else f"{trimmed[0]}.{trimmed[1:]}"
        return f"{sign}{coefficient}e{exponent}"

    def encode(current: Any) -> str:
        if current is None:
            return "null"
        if current is True:
            return "true"
        if current is False:
            return "false"
        if isinstance(current, int):
            return number(current)
        if isinstance(current, float):
            if current.is_integer():
                return number(int(current))
            raise AlignmentError("w1_w2_registry_runtime_evidence_invalid：Registry bundle 不允许非整数浮点数")
        if isinstance(current, str):
            return json.dumps(current, ensure_ascii=False, separators=(",", ":"))
        if isinstance(current, list):
            return f"[{','.join(encode(item) for item in current)}]"
        if isinstance(current, dict):
            return "{" + ",".join(
                f"{json.dumps(key, ensure_ascii=False)}:{encode(current[key])}"
                for key in sorted(current)
            ) + "}"
        raise AlignmentError("w1_w2_registry_runtime_evidence_invalid：Registry bundle 包含非 JSON 类型")

    return f"sha256:{sha256_bytes(encode(value).encode('utf-8'))}"


def _validate_registry_runtime_evidence(root: Path, stage: str, reference: Any) -> None:
    """验证 TypeScript Registry 运行时代码实际生成的不可调用 binding bundle。"""

    expect(isinstance(reference, dict), "w1_w2_registry_runtime_evidence_missing", f"{stage} Registry evidence 引用无效")
    exact(
        set(reference),
        {"path", "sha256", "content_digest", "generator_path", "generator_sha256"},
        "w1_w2_registry_runtime_evidence_invalid",
        f"{stage} Registry evidence 引用字段不精确",
    )
    expected_path = (W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json").as_posix()
    exact(reference.get("path"), expected_path, "w1_w2_registry_runtime_evidence_invalid", f"{stage} Registry evidence path 漂移")
    exact(reference.get("generator_path"), W1_W2_REGISTRY_EVIDENCE_GENERATOR_PATH.as_posix(), "w1_w2_registry_runtime_evidence_invalid", f"{stage} Registry generator path 漂移")
    generator_sha = file_sha256(root / W1_W2_REGISTRY_EVIDENCE_GENERATOR_PATH)
    exact(reference.get("generator_sha256"), generator_sha, "w1_w2_registry_runtime_evidence_invalid", f"{stage} Registry generator SHA 漂移")
    _, path = repository_artifact_path(root, expected_path, "w1_w2_registry_runtime_evidence_invalid")
    actual_sha = file_sha256(path)
    exact(actual_sha, W1_W2_REGISTRY_EVIDENCE_SHA256[stage], "w1_w2_registry_runtime_evidence_untrusted_resign", f"{stage} Registry bundle 不是冻结 TypeScript 输出")
    exact(reference.get("sha256"), actual_sha, "w1_w2_registry_runtime_evidence_invalid", f"{stage} Registry bundle SHA 漂移")
    bundle = load_json(path)
    exact(
        set(bundle),
        {
            "schema_version", "generator_id", "generator_source_sha256", "wave_id",
            "proposal_snapshot", "proposal_admission_receipt", "handler_manifest",
            "wave_snapshot", "wave_admission_receipt", "non_execution_probe",
            "execution_scope", "sequence_ordinal", "content_digest",
        },
        "w1_w2_registry_runtime_evidence_invalid",
        f"{stage} Registry bundle 字段不精确",
    )
    exact(bundle.get("schema_version"), "country_outage_p2_s1_registry_runtime_evidence_bundle_v1", "w1_w2_registry_runtime_evidence_invalid", "Registry bundle Schema 漂移")
    exact(bundle.get("generator_id"), "generate-p2-s1-w1-w4-registry-evidence", "w1_w2_registry_runtime_evidence_invalid", "Registry generator ID 漂移")
    exact(bundle.get("generator_source_sha256"), generator_sha, "w1_w2_registry_runtime_evidence_invalid", "Registry bundle 未绑定 generator 字节")
    exact(bundle.get("wave_id"), stage, "w1_w2_registry_population_drift", "Registry bundle wave 漂移")
    wave_index = REGISTRY_WAVE_SEQUENCE.index(stage)
    exact(bundle.get("sequence_ordinal"), wave_index + 1, "w1_w2_registry_sequence_invalid", "Registry bundle 顺序漂移")
    content_digest = _p2s1_governance_digest({key: value for key, value in bundle.items() if key != "content_digest"})
    exact(bundle.get("content_digest"), content_digest, "w1_w2_registry_runtime_evidence_invalid", "Registry bundle content digest 不可重算")
    exact(reference.get("content_digest"), content_digest, "w1_w2_registry_runtime_evidence_invalid", "Registry 引用未绑定 content digest")

    proposal = bundle.get("proposal_snapshot")
    expect(isinstance(proposal, dict), "w1_w2_registry_runtime_evidence_invalid", "Registry proposal 缺失")
    proposal_payload = proposal.get("snapshot_payload")
    expect(isinstance(proposal_payload, dict), "w1_w2_registry_runtime_evidence_invalid", "Registry proposal payload 缺失")
    proposal_digest = _p2s1_governance_digest(proposal_payload)
    exact(proposal.get("snapshot_digest"), proposal_digest, "w1_w2_registry_snapshot_digest_mismatch", "Registry proposal digest 不可重算")
    exact(proposal.get("registry_snapshot_id"), f"p2-s1-registry-proposal-sha256:{proposal_digest.removeprefix('sha256:')}", "w1_w2_registry_snapshot_invalid", "Registry proposal ID 不可重算")
    exact(proposal_payload.get("candidate_id"), DESIGN_CANDIDATE_ID, "w1_w2_registry_candidate_mismatch", "Registry proposal candidate 漂移")
    exact(proposal_payload.get("registry_revision"), 3, "w1_w2_registry_snapshot_invalid", "Registry proposal revision 漂移")
    exact(proposal_payload.get("activation_scope"), "w0_proposal_only", "w1_w2_registry_handler_activation_overclaim", "Registry proposal scope 漂移")
    exact(proposal_payload.get("production_deployed"), False, "wave_deployment_overclaim", "Registry proposal 不得声明生产部署")
    exact(proposal_payload.get("external_data_allowed"), False, "w1_w2_registry_runtime_evidence_invalid", "Registry proposal 不得允许外部数据")

    manifest = bundle.get("handler_manifest")
    expect(isinstance(manifest, dict) and isinstance(manifest.get("manifest_payload"), dict), "w1_w2_registry_manifest_invalid", "Registry handler manifest 缺失")
    manifest_payload = manifest["manifest_payload"]
    manifest_digest = _p2s1_governance_digest(manifest_payload)
    exact(manifest.get("handler_manifest_digest"), manifest_digest, "w1_w2_registry_manifest_digest_mismatch", "Registry handler manifest digest 不可重算")
    exact(manifest.get("handler_manifest_id"), f"p2-s1-handler-manifest-sha256:{manifest_digest.removeprefix('sha256:')}", "w1_w2_registry_manifest_invalid", "Registry handler manifest ID 不可重算")
    exact(manifest_payload.get("wave_id"), stage, "w1_w2_registry_population_drift", "Registry handler manifest wave 漂移")
    exact(manifest_payload.get("candidate_id"), DESIGN_CANDIDATE_ID, "w1_w2_registry_candidate_mismatch", "Registry handler manifest candidate 漂移")
    expected_structural = f"sha256:{file_sha256(root / STRUCTURAL_BINDING_PATH)}"
    exact(manifest_payload.get("structural_binding_contract_digest"), expected_structural, "w1_w2_registry_manifest_invalid", "Registry handler manifest 未绑定结构合同")
    handlers = manifest_payload.get("handlers")
    expect(isinstance(handlers, list), "w1_w2_registry_manifest_invalid", "Registry handlers 缺失")
    wave_units = WAVE_CONTRACT[stage]["unit_ids"]
    exact([item.get("unit_id") for item in handlers if isinstance(item, dict)], wave_units, "w1_w2_registry_population_drift", "Registry handler 人口漂移")
    for handler in handlers:
        expect(isinstance(handler, dict), "w1_w2_registry_manifest_invalid", "Registry handler 无效")
        unit_id = handler.get("unit_id")
        implementation_path = W1_W2_TOOL_IMPLEMENTATION_PATH if str(unit_id).startswith("TOOL-") else W1_W2_OPERATOR_IMPLEMENTATION_PATH
        exact(handler.get("implementation_digest"), f"sha256:{file_sha256(root / implementation_path)}", "w1_w2_registry_artifact_mismatch", f"{unit_id} Registry implementation digest 漂移")
        exact(handler.get("structural_binding_contract_digest"), expected_structural, "w1_w2_registry_manifest_invalid", f"{unit_id} Registry structural binding 漂移")
        test_evidence = handler.get("test_evidence")
        expect(isinstance(test_evidence, dict), "w1_w2_registry_manifest_invalid", f"{unit_id} Registry test evidence 缺失")
        exact(
            set(test_evidence),
            {
                "schema_version", "receipt_digest", "candidate_id", "design_candidate_digest",
                "wave_id", "unit_id", "handler_id", "implementation_digest", "contract_digest",
                "semantic_digest", "structural_binding_contract_digest", "runner_receipt_digest",
                "runner_receipt_file_digest", "runner_receipt_path", "test_case_ids", "test_result",
                "tested_execution_count",
            },
            "w1_w2_registry_manifest_invalid",
            f"{unit_id} Registry test evidence 字段不精确",
        )
        test_digest = test_evidence.get("receipt_digest")
        exact(test_digest, _p2s1_governance_digest({key: value for key, value in test_evidence.items() if key != "receipt_digest"}), "w1_w2_registry_receipt_digest_mismatch", f"{unit_id} Registry test receipt 不可重算")
        allowed_suites = {f"{stage.lower()}-{category}" for category in ("positive", "boundary", "attack")}
        runner_path_value = test_evidence.get("runner_receipt_path")
        expect(isinstance(runner_path_value, str), "w1_w2_registry_probe_invalid", f"{unit_id} Registry runner receipt path 无效")
        runner_name = Path(runner_path_value).stem
        expect(runner_name in allowed_suites, "w1_w2_registry_probe_invalid", f"{unit_id} Registry runner receipt 不属于当前波次")
        run_path = STAGE_TEST_RUN_RECEIPT_ROOT / f"{runner_name}.json"
        exact(runner_path_value, run_path.as_posix(), "w1_w2_registry_probe_invalid", f"{unit_id} Registry runner receipt path 漂移")
        run_receipt = load_json(root / run_path)
        exact(file_sha256(root / run_path), STAGE_TEST_RUN_RECEIPTS[runner_name][2], "w1_w2_registry_probe_invalid", f"{unit_id} Registry runner receipt 不是冻结真实运行制品")
        exact(run_receipt.get("receipt_digest"), object_digest(run_receipt, {"receipt_digest"}), "w1_w2_registry_probe_invalid", f"{unit_id} Registry runner receipt 不可重算")
        exact(run_receipt.get("passed"), True, "w1_w2_registry_probe_invalid", f"{unit_id} Registry runner receipt 未通过")
        exact(test_evidence.get("runner_receipt_file_digest"), f"sha256:{file_sha256(root / run_path)}", "w1_w2_registry_probe_invalid", f"{unit_id} Registry 未绑定实际 runner receipt bytes")
        exact(test_evidence.get("runner_receipt_digest"), f"sha256:{run_receipt['receipt_digest']}", "w1_w2_registry_probe_invalid", f"{unit_id} Registry 未绑定实际 runner receipt digest")
        case_ids = test_evidence.get("test_case_ids")
        expect(isinstance(case_ids, list) and case_ids and len(case_ids) == len(set(case_ids)), "w1_w2_registry_probe_invalid", f"{unit_id} Registry test case 人口无效")
        selected = run_receipt.get("selected_test_ids")
        expect(isinstance(selected, list), "w1_w2_registry_probe_invalid", f"{unit_id} runner test IDs 缺失")
        expect(set(case_ids) <= set(selected), "w1_w2_registry_probe_invalid", f"{unit_id} Registry test case 不属于实际 runner")
        coverage_by_test = {
            item.get("test_id"): item.get("executed_unit_ids")
            for item in run_receipt.get("test_case_coverage", [])
            if isinstance(item, dict)
        }
        for case_id in case_ids:
            expect(unit_id in coverage_by_test.get(case_id, []), "w1_w2_registry_probe_invalid", f"{unit_id} Registry test case 未实际执行该原子单元")
        exact(test_evidence.get("tested_execution_count"), len(case_ids), "w1_w2_registry_probe_invalid", f"{unit_id} Registry tested execution count 漂移")
        exact(test_evidence.get("test_result"), "passed", "w1_w2_registry_probe_invalid", f"{unit_id} Registry test result 未通过")

    snapshot = bundle.get("wave_snapshot")
    expect(isinstance(snapshot, dict) and isinstance(snapshot.get("snapshot_payload"), dict), "wave_registry_snapshot_missing", "Registry wave snapshot 缺失")
    snapshot_payload = snapshot["snapshot_payload"]
    snapshot_digest = _p2s1_governance_digest(snapshot_payload)
    exact(snapshot.get("snapshot_digest"), snapshot_digest, "w1_w2_registry_snapshot_digest_mismatch", "Registry wave snapshot digest 不可重算")
    exact(snapshot.get("registry_snapshot_id"), f"p2-s1-registry-wave-sha256:{snapshot_digest.removeprefix('sha256:')}", "w1_w2_registry_snapshot_invalid", "Registry wave snapshot ID 不可重算")
    exact(snapshot_payload.get("wave_id"), stage, "w1_w2_registry_population_drift", "Registry wave snapshot wave 漂移")
    exact(snapshot_payload.get("registry_revision"), 4 + wave_index, "w1_w2_registry_snapshot_invalid", "Registry wave revision 漂移")
    exact(snapshot_payload.get("handler_manifest"), manifest, "w1_w2_registry_snapshot_invalid", "Registry snapshot 未逐字绑定实际 handler manifest")
    exact(snapshot_payload.get("admitted_wave_binding_unit_ids"), wave_units, "w1_w2_registry_population_drift", "Registry 本波 binding 人口漂移")
    expected_all_units = cumulative_registry_units(stage)
    exact(snapshot_payload.get("admitted_binding_unit_ids"), expected_all_units, "w1_w2_registry_population_drift", "Registry 继承 binding 人口漂移")
    if stage == "W1":
        exact(snapshot_payload.get("previous_snapshot_ref"), snapshot_payload.get("proposal_snapshot_ref"), "w1_w2_registry_sequence_invalid", "W1 未从同一 proposal 开始")
    else:
        previous_stage = REGISTRY_WAVE_SEQUENCE[wave_index - 1]
        previous_bundle = load_json(root / W1_W2_REGISTRY_EVIDENCE_ROOT / f"{previous_stage}.json")
        previous_snapshot = previous_bundle["wave_snapshot"]
        exact(snapshot_payload.get("previous_snapshot_ref"), {
            "registry_snapshot_id": previous_snapshot["registry_snapshot_id"],
            "snapshot_digest": previous_snapshot["snapshot_digest"],
            "registry_revision": previous_snapshot["snapshot_payload"]["registry_revision"],
        }, "w1_w2_registry_sequence_invalid", f"{stage} 未承接冻结 {previous_stage} snapshot")

    admission = bundle.get("wave_admission_receipt")
    expect(isinstance(admission, dict), "w1_w2_registry_receipt_invalid", "Registry admission receipt 缺失")
    exact(admission.get("receipt_digest"), _p2s1_governance_digest({key: value for key, value in admission.items() if key != "receipt_digest"}), "w1_w2_registry_receipt_digest_mismatch", "Registry admission receipt 不可重算")
    exact(admission.get("status"), "admitted_complete_atomic_wave_bindings", "w1_w2_registry_handler_activation_overclaim", "Registry 只允许 binding admission")
    exact(admission.get("registry_snapshot_id"), snapshot.get("registry_snapshot_id"), "w1_w2_registry_receipt_invalid", "Registry admission snapshot ID 漂移")
    exact(admission.get("snapshot_digest"), snapshot_digest, "w1_w2_registry_receipt_invalid", "Registry admission snapshot digest 漂移")
    exact(admission.get("handler_manifest_id"), manifest.get("handler_manifest_id"), "w1_w2_registry_receipt_invalid", "Registry admission manifest ID 漂移")
    exact(admission.get("execution_allowed_unit_ids"), [], "w1_w2_registry_execution_authorization_overclaim", "Registry execution allowlist 必须为空")
    exact(admission.get("partial_binding_admission"), False, "w1_w2_registry_receipt_invalid", "Registry 不允许部分 binding")
    exact(admission.get("execution_started"), False, "w1_w2_registry_execution_authorization_overclaim", "Registry binding 不得启动执行")
    exact(admission.get("production_deployed"), False, "wave_deployment_overclaim", "Registry admission 不得声明部署")

    probe = bundle.get("non_execution_probe")
    exact(probe, {
        "tested_unit_id": wave_units[0],
        "assert_execution_authorized_error": "registry_dispatch_not_bound",
        "caller_callback_spy_count": 0,
        "execution_allowed_unit_ids": [],
        "execution_started": False,
    }, "w1_w2_registry_probe_invalid", "Registry 实际 non-execution probe 漂移")
    exact(bundle.get("execution_scope"), {
        "offline_harness_verified": True,
        "immutable_non_callable_binding_admitted": True,
        "trusted_dispatcher_implemented": False,
        "registry_execution_authorized": False,
        "production_deployed": False,
    }, "w1_w2_registry_handler_activation_overclaim", "Registry bundle 只能证明不可调用 binding")


def validate_w1_w2_evidence(root: Path, stage: str, evidence: dict[str, Any]) -> list[str]:
    """验证 W1/W2 原子运行时证据；布尔自报和旧 W0 回执均不能替代它。"""

    semantic_digest = require_hex64(
        evidence.get("implementation_semantic_digest"),
        "w1_w2_candidate_digest_invalid",
        f"{stage} implementation semantic digest 无效",
    )
    exact(
        semantic_digest,
        object_digest(evidence, {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"}),
        "w1_w2_candidate_digest_mismatch",
        f"{stage} implementation semantic digest 不可重算",
    )
    exact(
        evidence.get("implementation_candidate_id"),
        f"country-outage-p2-s1-{stage.lower()}-{semantic_digest[:24]}",
        "w1_w2_candidate_identity_mismatch",
        f"{stage} candidate ID 不可由语义摘要重算",
    )
    content_digest = require_hex64(
        evidence.get("content_digest"), "w1_w2_content_digest_invalid", f"{stage} content digest 无效"
    )
    exact(
        content_digest,
        object_digest(evidence, {"content_digest"}),
        "w1_w2_content_digest_mismatch",
        f"{stage} evidence content digest 不可重算",
    )

    expected_artifact_roles = atomic_wave_artifact_roles(stage)
    artifacts = evidence.get("artifact_manifest")
    expect(isinstance(artifacts, list), "w1_w2_artifact_manifest_missing", f"{stage} 缺少 artifact manifest")
    exact(len(artifacts), len(expected_artifact_roles), "w1_w2_artifact_population_mismatch", f"{stage} artifact manifest 不得缺失或夹带额外制品")
    artifact_by_path: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(artifacts):
        expect(isinstance(item, dict), "w1_w2_artifact_invalid", f"artifact_manifest[{index}] 必须是对象")
        path_text = item.get("path")
        expect(isinstance(path_text, str), "w1_w2_artifact_path_invalid", f"artifact_manifest[{index}] path 无效")
        expect(path_text not in artifact_by_path, "w1_w2_artifact_duplicate", f"重复制品：{path_text}")
        artifact_by_path[path_text] = item
    exact(set(artifact_by_path), set(expected_artifact_roles), "w1_w2_artifact_population_mismatch", f"{stage} artifact manifest 路径人口漂移")
    for path_text, role in expected_artifact_roles.items():
        _validate_bound_artifact(root, artifact_by_path[path_text], path_text, role, "w1_w2_artifact_binding_mismatch")

    structural = evidence.get("structural_binding_contract")
    _validate_bound_artifact(
        root,
        structural,
        STRUCTURAL_BINDING_PATH.as_posix(),
        "structural_binding_contract",
        "w1_w2_structural_contract_missing",
    )

    frozen = evidence.get("frozen_contracts")
    expect(isinstance(frozen, list), "w1_w2_frozen_contracts_missing", f"{stage} 缺少冻结合同绑定")
    expected_frozen = {
        TOOL_CATALOG_PATH.as_posix(): "frozen_tool_catalog",
        TOOL_CONTRACT_SCHEMA_PATH.as_posix(): "frozen_tool_contract_schema",
        OPERATOR_CATALOG_PATH.as_posix(): "frozen_operator_catalog",
        OPERATOR_CONTRACT_SCHEMA_PATH.as_posix(): "frozen_operator_contract_schema",
    }
    exact(len(frozen), len(expected_frozen), "w1_w2_frozen_contract_population_mismatch", f"{stage} 冻结合同人口漂移")
    frozen_by_path = {item.get("path"): item for item in frozen if isinstance(item, dict)}
    exact(set(frozen_by_path), set(expected_frozen), "w1_w2_frozen_contract_population_mismatch", f"{stage} 冻结合同路径漂移")
    for path_text, role in expected_frozen.items():
        _validate_bound_artifact(root, frozen_by_path[path_text], path_text, role, "w1_w2_frozen_contract_binding_mismatch")

    source = evidence.get("w0_source_binding")
    expect(isinstance(source, dict), "w1_w2_source_binding_missing", f"{stage} 缺少 W0 source 绑定")
    exact(
        set(source),
        {"w0_receipt_digest", "store_id", "manifest", "source_store", "population_schemas"},
        "w1_w2_source_binding_invalid",
        f"{stage} W0 source 绑定字段不精确",
    )
    current_w0 = load_json(root / WAVE_RECEIPT_ROOT / "W0.json")
    current_w0_digest = require_hex64(
        current_w0.get("receipt_digest"),
        "w1_w2_current_w0_receipt_invalid",
        f"{stage} 当前 W0 回执摘要无效",
    )
    exact(
        current_w0_digest,
        object_digest(current_w0, {"receipt_digest"}),
        "w1_w2_current_w0_receipt_invalid",
        f"{stage} 当前 W0 回执不可重算",
    )
    exact(source.get("w0_receipt_digest"), current_w0_digest, "w1_w2_old_w0_receipt_replay", f"{stage} 未绑定当前 W0 回执")
    _validate_bound_artifact(root, source.get("manifest"), W0_SOURCE_FIXTURE_MANIFEST.as_posix(), "w0_source_manifest", "w1_w2_source_manifest_binding_mismatch")
    _validate_bound_artifact(root, source.get("source_store"), W1_W2_SOURCE_STORE_PATH.as_posix(), "w0_source_store", "w1_w2_source_store_binding_mismatch")
    manifest = load_json(root / W0_SOURCE_FIXTURE_MANIFEST)
    exact(source.get("store_id"), manifest.get("store_id"), "w1_w2_source_store_identity_mismatch", f"{stage} Source Store ID 漂移")
    tool_map, operator_map = _catalog_units(root)
    wave_units = WAVE_CONTRACT[stage]["unit_ids"]
    required_populations = [tool_map[unit_id]["source_population"] for unit_id in wave_units if unit_id in tool_map]
    schemas = source.get("population_schemas")
    expect(isinstance(schemas, list), "w1_w2_source_schema_binding_missing", f"{stage} 缺少 source schema 摘要")
    exact(len(schemas), len(required_populations), "w1_w2_source_schema_population_mismatch", f"{stage} source schema 人口漂移")
    schema_by_population = {item.get("population_id"): item for item in schemas if isinstance(item, dict)}
    exact(set(schema_by_population), set(required_populations), "w1_w2_source_schema_population_mismatch", f"{stage} source population 漂移")
    for population in required_populations:
        item = schema_by_population[population]
        expect(isinstance(item, dict), "w1_w2_source_schema_binding_invalid", f"{population} schema 引用无效")
        exact(set(item), {"population_id", "path", "role", "size_bytes", "sha256"}, "w1_w2_source_schema_binding_invalid", f"{population} schema 字段不精确")
        expected_path = W1_W2_SOURCE_SCHEMA_PATHS[population].as_posix()
        exact(item.get("population_id"), population, "w1_w2_source_schema_binding_invalid", f"{population} identity 漂移")
        projected = {key: value for key, value in item.items() if key != "population_id"}
        _validate_bound_artifact(root, projected, expected_path, "w0_source_schema", "w1_w2_source_schema_binding_invalid")

    atomic_receipts = evidence.get("atomic_unit_receipts")
    expect(isinstance(atomic_receipts, list), "w1_w2_atomic_receipts_missing", f"{stage} 缺少 atomic unit receipts")
    exact(len(atomic_receipts), len(wave_units), "w1_w2_atomic_receipt_population_mismatch", f"{stage} atomic receipt 数量漂移")
    receipt_by_unit = {item.get("unit_id"): item for item in atomic_receipts if isinstance(item, dict)}
    exact(set(receipt_by_unit), set(wave_units), "w1_w2_atomic_receipt_population_mismatch", f"{stage} atomic unit 人口漂移或混入其他 wave")
    for unit_id in wave_units:
        receipt = receipt_by_unit[unit_id]
        validate_recomputable_receipt(receipt, "w1_w2_atomic_receipt_digest_mismatch", unit_id)
        unit_kind = "tool" if unit_id in tool_map else "operator"
        catalog_entry = tool_map.get(unit_id) or operator_map[unit_id]
        implementation_path = W1_W2_TOOL_IMPLEMENTATION_PATH if unit_kind == "tool" else W1_W2_OPERATOR_IMPLEMENTATION_PATH
        input_ref, output_ref = _w1_w2_schema_refs(unit_id, tool_map, operator_map)
        expected_fields = {
            "schema_version", "receipt_kind", "stage", "design_candidate_id", "unit_id", "unit_kind",
            "catalog_entry_digest", "implementation_path", "implementation_sha256", "input_schema_ref",
            "output_schema_ref", "registered_atomic_operation_count", "business_transform_count",
            "fact_population_read_count", "internal_unit_calls", "model_call_count", "external_read_count",
            "p2_1_unit_ids", "receipt_digest",
        }
        if unit_kind == "tool":
            expected_fields.add("source_population_id")
        exact(set(receipt), expected_fields, "w1_w2_atomic_receipt_fields_invalid", f"{unit_id} atomic receipt 字段不精确")
        exact(receipt.get("schema_version"), "country_outage_p2_s1_atomic_unit_receipt_v1", "w1_w2_atomic_receipt_schema_mismatch", f"{unit_id} receipt schema 漂移")
        exact(receipt.get("receipt_kind"), "atomic_unit_implementation", "w1_w2_atomic_receipt_kind_mismatch", f"{unit_id} receipt kind 漂移")
        exact(receipt.get("stage"), stage, "w1_w2_atomic_receipt_stage_mismatch", f"{unit_id} 混入其他 wave")
        exact(receipt.get("design_candidate_id"), DESIGN_CANDIDATE_ID, "w1_w2_atomic_receipt_design_mismatch", f"{unit_id} 未绑定冻结设计")
        exact(receipt.get("unit_kind"), unit_kind, "w1_w2_atomic_receipt_kind_mismatch", f"{unit_id} unit kind 漂移")
        exact(receipt.get("catalog_entry_digest"), sha256_bytes(canonical_json(catalog_entry)), "w1_w2_atomic_receipt_catalog_mismatch", f"{unit_id} 未绑定 catalog entry")
        exact(receipt.get("implementation_path"), implementation_path.as_posix(), "w1_w2_atomic_receipt_implementation_mismatch", f"{unit_id} implementation path 漂移")
        exact(receipt.get("implementation_sha256"), file_sha256(root / implementation_path), "w1_w2_atomic_receipt_implementation_mismatch", f"{unit_id} implementation SHA 漂移")
        exact(receipt.get("input_schema_ref"), input_ref, "w1_w2_atomic_receipt_schema_ref_mismatch", f"{unit_id} input schema ref 漂移")
        exact(receipt.get("output_schema_ref"), output_ref, "w1_w2_atomic_receipt_schema_ref_mismatch", f"{unit_id} output schema ref 漂移")
        exact(receipt.get("registered_atomic_operation_count"), 1, "w1_w2_hidden_second_transform", f"{unit_id} 必须且只能有一个登记原子操作")
        exact(receipt.get("business_transform_count"), 0 if unit_kind == "tool" else 1, "w1_w2_hidden_second_transform", f"{unit_id} business transform 计数违反原子性")
        exact(receipt.get("fact_population_read_count"), 1 if unit_kind == "tool" else 0, "w1_w2_hidden_second_population_read", f"{unit_id} fact population read 计数违反原子性")
        for field in ("internal_unit_calls", "model_call_count", "external_read_count"):
            exact(receipt.get(field), 0, "w1_w2_hidden_execution", f"{unit_id} {field} 必须为 0")
        exact(receipt.get("p2_1_unit_ids"), [], "p2_1_unit_smuggled", f"{unit_id} 混入 P2.1")
        if unit_kind == "tool":
            exact(receipt.get("source_population_id"), catalog_entry.get("source_population"), "w1_w2_atomic_receipt_source_mismatch", f"{unit_id} 读取人口漂移")

    tests = evidence.get("test_receipts")
    expect(isinstance(tests, list), "wave_test_receipts_missing", f"{stage} 缺少测试回执")
    exact(len(tests), 3, "w1_w2_test_category_population_mismatch", f"{stage} 必须恰有正例、边界、攻击三类回执")
    categories: set[str] = set()
    tested_union: set[str] = set()
    executed_union: set[str] = set()
    for category in ("positive", "boundary", "attack"):
        reference = next((item for item in tests if isinstance(item, dict) and item.get("category") == category), None)
        expect(reference is not None and category not in categories, "w1_w2_test_category_population_mismatch", f"{stage} 缺少 {category} 测试回执")
        receipt = _validate_stage_test_run_receipt(root, reference, f"{stage.lower()}-{category}")
        categories.add(category)
        unit_ids = receipt.get("tested_unit_ids")
        expect(isinstance(unit_ids, list) and len(unit_ids) == len(set(unit_ids)), "w1_w2_test_unit_population_mismatch", f"{stage} {category} unit 人口无效")
        expect(set(unit_ids) <= set(wave_units), "w1_w2_test_unit_population_mismatch", f"{stage} {category} 混入其他波次 unit")
        tested_union.update(unit_ids)
        execution_ids = receipt.get("tested_execution_unit_ids")
        expect(isinstance(execution_ids, list) and len(execution_ids) == len(set(execution_ids)), "w1_w2_test_unit_population_mismatch", f"{stage} {category} execution 人口无效")
        expect(set(execution_ids) <= set(wave_units), "w1_w2_test_unit_population_mismatch", f"{stage} {category} execution 混入其他波次 unit")
        executed_union.update(execution_ids)
    exact(categories, {"positive", "boundary", "attack"}, "w1_w2_test_category_population_mismatch", f"{stage} 测试分类未闭合")
    exact(tested_union, set(wave_units), "w1_w2_test_unit_population_mismatch", f"{stage} 测试未覆盖完整 unit 人口")
    exact(executed_union, set(wave_units), "w1_w2_test_unit_population_mismatch", f"{stage} 实际执行测试未覆盖完整 unit 人口")

    _validate_registry_runtime_evidence(root, stage, evidence.get("registry_runtime_evidence"))
    # 下列对象只是对已验证 TypeScript bundle 的兼容投影视图，供既有静态字段攻击
    # 测试使用；信任根始终是上面的内容寻址实际输出，而不是 Python 调用方自报。
    registry = evidence.get("registry_binding_projection")
    expect(isinstance(registry, dict), "wave_registry_snapshot_missing", f"{stage} 缺少 Registry binding 投影视图")
    exact(
        set(registry),
        {"source_runtime_bundle", "artifact", "binding_manifest", "snapshot", "admission_receipt", "execution_probe", "execution_scope"},
        "w1_w2_registry_binding_invalid",
        f"{stage} Registry binding admission 字段不精确",
    )
    source_bundle = registry.get("source_runtime_bundle")
    expect(isinstance(source_bundle, dict), "w1_w2_registry_projection_unbound", f"{stage} Registry 投影缺少 TypeScript source bundle")
    exact(set(source_bundle), {
        "path", "sha256", "content_digest", "handler_manifest_id", "handler_manifest_digest",
        "snapshot_id", "snapshot_digest", "admission_receipt_digest",
    }, "w1_w2_registry_projection_unbound", f"{stage} Registry source bundle 字段不精确")
    actual_bundle_path = W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json"
    actual_bundle = load_json(root / actual_bundle_path)
    exact(source_bundle, {
        "path": actual_bundle_path.as_posix(),
        "sha256": file_sha256(root / actual_bundle_path),
        "content_digest": actual_bundle["content_digest"],
        "handler_manifest_id": actual_bundle["handler_manifest"]["handler_manifest_id"],
        "handler_manifest_digest": actual_bundle["handler_manifest"]["handler_manifest_digest"],
        "snapshot_id": actual_bundle["wave_snapshot"]["registry_snapshot_id"],
        "snapshot_digest": actual_bundle["wave_snapshot"]["snapshot_digest"],
        "admission_receipt_digest": actual_bundle["wave_admission_receipt"]["receipt_digest"],
    }, "w1_w2_registry_projection_unbound", f"{stage} Registry 兼容投影未绑定实际 TypeScript bundle")
    _validate_bound_artifact(root, registry.get("artifact"), W1_W2_REGISTRY_RUNTIME_PATH.as_posix(), "registry_runtime", "w1_w2_registry_artifact_mismatch")
    binding = registry.get("binding_manifest")
    expect(isinstance(binding, dict), "w1_w2_registry_manifest_invalid", f"{stage} binding manifest 无效")
    exact(
        set(binding),
        {
            "binding_manifest_id", "binding_manifest_digest", "wave_binding_unit_ids", "binding_kind",
            "artifact_path", "artifact_sha256", "structural_binding_contract_sha256",
            "callable_handler_refs", "caller_callback_refs", "trusted_dispatcher_id",
        },
        "w1_w2_registry_manifest_invalid",
        f"{stage} binding manifest 字段不精确",
    )
    binding_digest = require_hex64(binding.get("binding_manifest_digest"), "w1_w2_registry_manifest_invalid", f"{stage} binding manifest digest 无效")
    exact(binding_digest, object_digest(binding, {"binding_manifest_id", "binding_manifest_digest"}), "w1_w2_registry_manifest_digest_mismatch", f"{stage} binding manifest digest 不可重算")
    exact(binding.get("binding_manifest_id"), f"p2-s1-dispatch-binding-manifest-sha256:{binding_digest}", "w1_w2_registry_manifest_invalid", f"{stage} binding manifest ID 不可重算")
    exact(binding.get("wave_binding_unit_ids"), wave_units, "w1_w2_registry_population_drift", f"{stage} Registry binding unit 人口漂移")
    exact(binding.get("binding_kind"), "immutable_non_callable_dispatch_binding", "w1_w2_registry_handler_activation_overclaim", f"{stage} binding 不得冒充 callable handler activation")
    exact(binding.get("artifact_path"), W1_W2_REGISTRY_RUNTIME_PATH.as_posix(), "w1_w2_registry_artifact_mismatch", f"{stage} Registry binding path 漂移")
    exact(binding.get("artifact_sha256"), file_sha256(root / W1_W2_REGISTRY_RUNTIME_PATH), "w1_w2_registry_artifact_mismatch", f"{stage} Registry binding SHA 漂移")
    exact(binding.get("structural_binding_contract_sha256"), file_sha256(root / STRUCTURAL_BINDING_PATH), "w1_w2_registry_manifest_invalid", f"{stage} Registry binding 未绑定结构合同")
    exact(binding.get("callable_handler_refs"), [], "w1_w2_registry_callback_seam_open", f"{stage} 不得准入 callable handler ref")
    exact(binding.get("caller_callback_refs"), [], "w1_w2_registry_callback_seam_open", f"{stage} 不得暴露 caller callback seam")
    exact(binding.get("trusted_dispatcher_id"), None, "w1_w2_registry_handler_activation_overclaim", f"{stage} W5 前不得声称 trusted dispatcher 已绑定")

    wave_index = REGISTRY_WAVE_SEQUENCE.index(stage)
    admitted_binding_units = cumulative_registry_units(stage)
    snapshot = registry.get("snapshot")
    expect(isinstance(snapshot, dict), "wave_registry_snapshot_missing", f"{stage} Registry snapshot 无效")
    exact(set(snapshot), {
        "schema_version", "snapshot_id", "snapshot_digest", "registry_revision", "wave_id",
        "proposal_snapshot_id", "proposal_snapshot_digest", "proposal_registry_revision",
        "previous_snapshot_id", "previous_snapshot_digest", "previous_registry_revision",
        "binding_manifest_id", "binding_manifest_digest", "structural_binding_contract_sha256",
        "admitted_wave_binding_unit_ids", "admitted_binding_unit_ids", "binding_admission_only",
        "artifact_path", "artifact_sha256", "production_deployed",
    }, "w1_w2_registry_snapshot_invalid", f"{stage} Registry snapshot 字段不精确")
    exact(snapshot.get("schema_version"), "country_outage_p2_s1_registry_wave_snapshot_v1", "w1_w2_registry_snapshot_invalid", f"{stage} Registry snapshot schema 漂移")
    snapshot_digest = require_hex64(snapshot.get("snapshot_digest"), "wave_registry_digest_invalid", f"{stage} Registry digest 无效")
    exact(snapshot_digest, object_digest(snapshot, {"snapshot_id", "snapshot_digest"}), "w1_w2_registry_snapshot_digest_mismatch", f"{stage} Registry snapshot digest 不可重算")
    expect(isinstance(snapshot.get("snapshot_id"), str) and REGISTRY_SNAPSHOT_ID.fullmatch(snapshot["snapshot_id"]) is not None, "w1_w2_registry_snapshot_invalid", f"{stage} Registry snapshot ID 无效")
    exact(snapshot.get("snapshot_id"), f"p2-s1-registry-wave-sha256:{snapshot_digest}", "w1_w2_registry_snapshot_invalid", f"{stage} Registry snapshot ID 不可重算")
    expect(isinstance(snapshot.get("registry_revision"), int) and snapshot["registry_revision"] >= 1, "w1_w2_registry_snapshot_invalid", f"{stage} Registry revision 无效")
    exact(snapshot.get("wave_id"), stage, "w1_w2_registry_population_drift", f"{stage} Registry wave 漂移")
    for prefix in ("proposal", "previous"):
        expect(isinstance(snapshot.get(f"{prefix}_snapshot_id"), str) and snapshot[f"{prefix}_snapshot_id"], "w1_w2_registry_snapshot_invalid", f"{stage} {prefix} snapshot ID 无效")
        require_hex64(snapshot.get(f"{prefix}_snapshot_digest"), "w1_w2_registry_snapshot_invalid", f"{stage} {prefix} snapshot digest 无效")
        expect(isinstance(snapshot.get(f"{prefix}_registry_revision"), int) and snapshot[f"{prefix}_registry_revision"] >= 1, "w1_w2_registry_snapshot_invalid", f"{stage} {prefix} Registry revision 无效")
    exact(snapshot.get("proposal_registry_revision"), 3, "w1_w2_registry_snapshot_invalid", f"{stage} proposal revision 必须承接冻结 W0 proposal")
    exact(snapshot.get("registry_revision"), snapshot.get("previous_registry_revision") + 1, "w1_w2_registry_snapshot_invalid", f"{stage} Registry revision 必须严格 +1")
    if stage == "W1":
        for suffix in ("snapshot_id", "snapshot_digest", "registry_revision"):
            exact(snapshot.get(f"previous_{suffix}"), snapshot.get(f"proposal_{suffix}"), "w1_w2_registry_snapshot_invalid", f"W1 previous {suffix} 必须是 W0 proposal")
    else:
        previous_stage = REGISTRY_WAVE_SEQUENCE[wave_index - 1]
        previous_evidence = load_json(root / WAVE_EVIDENCE_ROOT / f"{previous_stage}.json")
        previous_registry = previous_evidence.get("registry_binding_projection")
        expect(isinstance(previous_registry, dict) and isinstance(previous_registry.get("snapshot"), dict), "w1_w2_registry_snapshot_invalid", f"{stage} 必须绑定当前 {previous_stage} binding snapshot")
        previous_snapshot = previous_registry["snapshot"]
        exact(snapshot.get("previous_snapshot_id"), previous_snapshot.get("snapshot_id"), "w1_w2_registry_snapshot_invalid", f"{stage} previous snapshot ID 未绑定当前 {previous_stage}")
        exact(snapshot.get("previous_snapshot_digest"), previous_snapshot.get("snapshot_digest"), "w1_w2_registry_snapshot_invalid", f"{stage} previous snapshot digest 未绑定当前 {previous_stage}")
        exact(snapshot.get("previous_registry_revision"), previous_snapshot.get("registry_revision"), "w1_w2_registry_snapshot_invalid", f"{stage} previous revision 未绑定当前 {previous_stage}")
    exact(snapshot.get("binding_manifest_id"), binding.get("binding_manifest_id"), "w1_w2_registry_snapshot_invalid", f"{stage} snapshot 未绑定 binding manifest ID")
    exact(snapshot.get("binding_manifest_digest"), binding_digest, "w1_w2_registry_snapshot_invalid", f"{stage} snapshot 未绑定 binding manifest digest")
    exact(snapshot.get("structural_binding_contract_sha256"), file_sha256(root / STRUCTURAL_BINDING_PATH), "w1_w2_registry_snapshot_invalid", f"{stage} snapshot 未绑定结构合同")
    exact(snapshot.get("admitted_wave_binding_unit_ids"), wave_units, "w1_w2_registry_population_drift", f"{stage} Registry 本波 binding 人口漂移")
    exact(snapshot.get("admitted_binding_unit_ids"), admitted_binding_units, "w1_w2_registry_population_drift", f"{stage} Registry 继承 binding 人口漂移")
    exact(snapshot.get("binding_admission_only"), True, "w1_w2_registry_handler_activation_overclaim", f"{stage} snapshot 不得冒充 handler activation")
    exact(snapshot.get("artifact_path"), W1_W2_REGISTRY_RUNTIME_PATH.as_posix(), "w1_w2_registry_artifact_mismatch", f"{stage} Registry snapshot artifact 漂移")
    exact(snapshot.get("artifact_sha256"), file_sha256(root / W1_W2_REGISTRY_RUNTIME_PATH), "w1_w2_registry_artifact_mismatch", f"{stage} Registry snapshot SHA 漂移")
    exact(snapshot.get("production_deployed"), False, "wave_deployment_overclaim", f"{stage} Registry 不得声称生产部署")

    admission = registry.get("admission_receipt")
    validate_recomputable_receipt(admission, "w1_w2_registry_receipt_digest_mismatch", f"{stage} Registry admission")
    exact(set(admission), {
        "schema_version", "status", "wave_id", "snapshot_id", "snapshot_digest", "registry_revision",
        "previous_snapshot_id", "previous_snapshot_digest", "previous_registry_revision",
        "binding_manifest_id", "binding_manifest_digest", "structural_binding_contract_sha256",
        "registry_artifact_path", "registry_artifact_sha256", "admitted_wave_binding_unit_ids",
        "admitted_binding_unit_ids", "execution_allowed_unit_ids", "partial_binding_admission",
        "trusted_dispatcher_bound", "execution_started", "production_deployed", "receipt_digest",
    }, "w1_w2_registry_receipt_invalid", f"{stage} Registry admission 字段不精确")
    exact(admission.get("schema_version"), "country_outage_p2_s1_registry_wave_admission_v1", "w1_w2_registry_receipt_invalid", f"{stage} Registry admission schema 漂移")
    exact(admission.get("status"), "admitted_complete_atomic_wave_bindings", "w1_w2_registry_handler_activation_overclaim", f"{stage} 只允许完整 binding admission，不允许 handler activation")
    exact(admission.get("wave_id"), stage, "w1_w2_registry_population_drift", f"{stage} Registry admission wave 漂移")
    exact(admission.get("snapshot_id"), snapshot.get("snapshot_id"), "w1_w2_registry_receipt_invalid", f"{stage} admission snapshot ID 漂移")
    exact(admission.get("snapshot_digest"), snapshot_digest, "w1_w2_registry_receipt_invalid", f"{stage} admission snapshot digest 漂移")
    exact(admission.get("registry_revision"), snapshot.get("registry_revision"), "w1_w2_registry_receipt_invalid", f"{stage} admission revision 漂移")
    exact(admission.get("previous_snapshot_id"), snapshot.get("previous_snapshot_id"), "w1_w2_registry_receipt_invalid", f"{stage} admission previous snapshot ID 漂移")
    exact(admission.get("previous_snapshot_digest"), snapshot.get("previous_snapshot_digest"), "w1_w2_registry_receipt_invalid", f"{stage} admission previous snapshot digest 漂移")
    exact(admission.get("previous_registry_revision"), snapshot.get("previous_registry_revision"), "w1_w2_registry_receipt_invalid", f"{stage} admission previous revision 漂移")
    exact(admission.get("binding_manifest_id"), binding.get("binding_manifest_id"), "w1_w2_registry_receipt_invalid", f"{stage} admission binding manifest ID 漂移")
    exact(admission.get("binding_manifest_digest"), binding_digest, "w1_w2_registry_receipt_invalid", f"{stage} admission binding manifest digest 漂移")
    exact(admission.get("structural_binding_contract_sha256"), file_sha256(root / STRUCTURAL_BINDING_PATH), "w1_w2_registry_receipt_invalid", f"{stage} admission 未绑定结构合同")
    exact(admission.get("registry_artifact_path"), W1_W2_REGISTRY_RUNTIME_PATH.as_posix(), "w1_w2_registry_artifact_mismatch", f"{stage} admission registry path 漂移")
    exact(admission.get("registry_artifact_sha256"), file_sha256(root / W1_W2_REGISTRY_RUNTIME_PATH), "w1_w2_registry_artifact_mismatch", f"{stage} admission registry SHA 漂移")
    exact(admission.get("admitted_wave_binding_unit_ids"), wave_units, "w1_w2_registry_population_drift", f"{stage} admission 本波 binding 人口漂移")
    exact(admission.get("admitted_binding_unit_ids"), admitted_binding_units, "w1_w2_registry_population_drift", f"{stage} admission 继承 binding 人口漂移")
    exact(admission.get("execution_allowed_unit_ids"), [], "w1_w2_registry_execution_authorization_overclaim", f"{stage} W5 前 execution allowlist 必须为空")
    exact(admission.get("partial_binding_admission"), False, "w1_w2_registry_receipt_invalid", f"{stage} 不允许部分 binding admission")
    exact(admission.get("trusted_dispatcher_bound"), False, "w1_w2_registry_handler_activation_overclaim", f"{stage} W5 前 trusted dispatcher 必须未绑定")
    exact(admission.get("execution_started"), False, "w1_w2_registry_execution_authorization_overclaim", f"{stage} binding admission 不得启动执行")
    exact(admission.get("production_deployed"), False, "wave_deployment_overclaim", f"{stage} Registry admission 不得声称生产部署")

    probe = registry.get("execution_probe")
    validate_recomputable_receipt(probe, "w1_w2_registry_probe_digest_mismatch", f"{stage} Registry execution probe")
    exact(set(probe), {
        "schema_version", "wave_id", "tested_unit_ids", "test_artifact_path", "test_artifact_sha256",
        "caller_callback_injection_supported", "caller_callback_spy_count", "execution_allowed_unit_ids",
        "assert_execution_authorized_error", "trusted_dispatcher_bound", "receipt_digest",
    }, "w1_w2_registry_probe_invalid", f"{stage} Registry execution probe 字段不精确")
    exact(probe.get("schema_version"), "country_outage_p2_s1_registry_non_execution_probe_v1", "w1_w2_registry_probe_invalid", f"{stage} execution probe schema 漂移")
    exact(probe.get("wave_id"), stage, "w1_w2_registry_probe_invalid", f"{stage} execution probe wave 漂移")
    exact(probe.get("tested_unit_ids"), wave_units, "w1_w2_registry_population_drift", f"{stage} execution probe 人口漂移")
    exact(probe.get("test_artifact_path"), W1_W2_REGISTRY_TEST_PATH.as_posix(), "w1_w2_registry_probe_invalid", f"{stage} execution probe test path 漂移")
    exact(probe.get("test_artifact_sha256"), file_sha256(root / W1_W2_REGISTRY_TEST_PATH), "w1_w2_registry_probe_invalid", f"{stage} execution probe test SHA 漂移")
    exact(probe.get("caller_callback_injection_supported"), False, "w1_w2_registry_callback_seam_open", f"{stage} caller callback seam 必须封死")
    exact(probe.get("caller_callback_spy_count"), 0, "w1_w2_registry_callback_seam_open", f"{stage} caller callback spy 必须为 0")
    exact(probe.get("execution_allowed_unit_ids"), [], "w1_w2_registry_execution_authorization_overclaim", f"{stage} probe execution allowlist 必须为空")
    exact(probe.get("assert_execution_authorized_error"), "registry_dispatch_not_bound", "w1_w2_registry_handler_activation_overclaim", f"{stage} 未绑定 dispatcher 时必须 fail-closed")
    exact(probe.get("trusted_dispatcher_bound"), False, "w1_w2_registry_handler_activation_overclaim", f"{stage} probe 不得声称 dispatcher 已绑定")

    scope = registry.get("execution_scope")
    exact(scope, {
        "offline_harness_verified": True,
        "trusted_dispatcher_implemented": False,
        "registry_execution_authorized": False,
        "production_deployed": False,
    }, "w1_w2_registry_handler_activation_overclaim", f"{stage} 只能证明离线 harness，受信 dispatcher 留待 W5")

    exact(
        evidence.get("capability_scope"),
        {
            "offline_fixture_harness_verified": True,
            "non_callable_registry_binding_verified": True,
            "user_answer_available": False,
            "api_available": False,
            "export_available": False,
            "complete_result_set_freeze_available": False,
            "real_publication_replay_verified": False,
            "runtime_activation": False,
            "trusted_dispatcher_implemented": False,
            "production_deployed": False,
        },
        "w1_w2_capability_overclaim",
        f"{stage} 必须机器化声明离线 harness 与尚未实现的交付边界",
    )

    performance = evidence.get("performance_baseline")
    expect(isinstance(performance, dict), "w1_w2_performance_status_missing", f"{stage} 缺少性能诚实状态")
    exact(performance, {"measurement_status": "not_w6_acceptance", "performance_acceptance_passed": False}, "w1_w2_performance_overclaim", f"{stage} 不得冒充 W6 性能验收")
    return [
        f"{stage.lower()}_content_addressed_artifacts_verified",
        f"{stage.lower()}_atomic_unit_receipts_verified",
        f"{stage.lower()}_structural_and_frozen_contracts_verified",
        f"{stage.lower()}_w0_source_lineage_verified",
        f"{stage.lower()}_registry_complete_wave_binding_admission_verified",
        f"{stage.lower()}_trusted_dispatcher_deferred_to_w5_verified",
        f"{stage.lower()}_positive_boundary_attack_tests_verified",
        f"{stage.lower()}_not_w6_or_production_verified",
    ]


def _validate_current_wave_stage_receipt(
    root: Path,
    stage: str,
    receipt: dict[str, Any],
    baseline: dict[str, Any],
) -> None:
    """证明 prior wave 回执仍绑定当前候选字节，而不只是历史上自洽。"""

    exact(
        receipt.get("design_candidate_id"),
        DESIGN_CANDIDATE_ID,
        "wave_prior_artifact_binding_mismatch",
        f"{stage} prior receipt 未绑定冻结设计候选",
    )
    current_bindings = {
        "task_sha256": file_sha256(root / TASK_PATH),
        "target_document_sha256": file_sha256(root / TARGET_PATH),
        "phase_plan_sha256": file_sha256(root / PLAN_PATH),
        "baseline_sha256": file_sha256(root / BASELINE_PATH),
        "hook_sha256": file_sha256(Path(__file__).resolve()),
        "hook_tests_sha256": file_sha256(
            root / "dev/tests/test_country_outage_p2_s1_implementation_alignment_hook.py"
        ),
        "wave_evidence_sha256": file_sha256(root / WAVE_EVIDENCE_ROOT / f"{stage}.json"),
    }
    for field, expected_value in current_bindings.items():
        exact(
            receipt.get(field),
            expected_value,
            "wave_prior_artifact_binding_mismatch",
            f"{stage} prior receipt 的 {field} 已过期",
        )
    exact(
        receipt.get("baseline_content_digest"),
        baseline.get("content_digest"),
        "wave_prior_artifact_binding_mismatch",
        f"{stage} prior receipt 未绑定当前 baseline content digest",
    )
    evidence = load_json(root / WAVE_EVIDENCE_ROOT / f"{stage}.json")
    exact(
        receipt.get("implementation_candidate_id"),
        evidence.get("implementation_candidate_id"),
        "wave_prior_artifact_binding_mismatch",
        f"{stage} prior receipt 未绑定当前 implementation candidate",
    )
    exact(
        receipt.get("production_deployed"),
        False,
        "wave_prior_artifact_binding_mismatch",
        f"{stage} prior receipt 不得声称已部署",
    )
    expected_prior_stages = ["S1I-P0", *stage_prior_dependencies(stage)]
    expected_prior_digests: list[str] = []
    for prior_stage in expected_prior_stages:
        path = (
            P0_RECEIPT_PATH
            if prior_stage == "S1I-P0"
            else WAVE_RECEIPT_ROOT / f"{prior_stage}.json"
        )
        expected_prior_digests.append(load_json(root / path)["receipt_digest"])
    exact(
        receipt.get("prior_stage_receipt_digests"),
        expected_prior_digests,
        "wave_prior_chain_binding_mismatch",
        f"{stage} prior receipt 的依赖摘要链已过期",
    )


def validate_wave(root: Path, stage: str, baseline: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    expect(stage in {"W0", "W1", "W2", "W3", "W4"}, "wave_stage_not_implemented", f"{stage} 尚未进入本实现任务，必须 fail-closed")
    contract = WAVE_CONTRACT[stage]
    evidence_path = root / WAVE_EVIDENCE_ROOT / f"{stage}.json"
    evidence = load_json(evidence_path)
    exact(evidence.get("schema_version"), "country_outage_p2_s1_implementation_wave_evidence_v1", "wave_evidence_schema_mismatch", f"{stage} evidence Schema 不匹配")
    exact(evidence.get("stage"), stage, "wave_evidence_stage_mismatch", f"{stage} evidence stage 不匹配")
    expected_status = (
        "implementation_wave_accepted"
        if stage == "W0"
        else "offline_atomic_harness_accepted_for_w5_integration"
    )
    exact(evidence.get("status"), expected_status, "wave_not_accepted", f"{stage} 尚未形成当前层级验收证据")
    exact(evidence.get("design_candidate_id"), DESIGN_CANDIDATE_ID, "wave_design_binding_mismatch", f"{stage} 未绑定冻结设计候选")
    exact(evidence.get("baseline_content_digest"), baseline.get("content_digest"), "wave_baseline_binding_mismatch", f"{stage} 未绑定当前实现基线")
    implementation_candidate_id = evidence.get("implementation_candidate_id")
    expect(isinstance(implementation_candidate_id, str) and implementation_candidate_id, "implementation_candidate_missing", f"{stage} 缺少实现候选 ID")
    exact(evidence.get("effect"), contract["effect"], "wave_effect_not_verified", f"{stage} 未证明冻结最终效果")
    exact(evidence.get("effect_verified"), True, "wave_effect_not_verified", f"{stage} 最终效果未通过")
    exact(evidence.get("implemented_unit_ids"), contract["unit_ids"], "wave_unit_population_mismatch", f"{stage} 实现单元人口漂移")
    exact(evidence.get("atomic_split_tests_passed"), True, "wave_atomicity_failed", f"{stage} atomic split tests 未通过")
    exact(evidence.get("p2_1_units_included"), [], "p2_1_unit_smuggled", f"{stage} 混入 P2.1 单元")
    exact(evidence.get("production_deployed"), False, "wave_deployment_overclaim", f"{stage} 不得声称已部署")
    if stage == "W0":
        tests = evidence.get("test_receipts")
        expect(isinstance(tests, list) and len(tests) == 2, "wave_test_receipts_missing", f"{stage} 必须恰有 Python 与 TypeScript 真实 runner 回执")
        for suite_id in ("w0-python", "w0-typescript"):
            reference = next((item for item in tests if isinstance(item, dict) and item.get("suite_id") == suite_id), None)
            expect(reference is not None, "wave_test_receipts_missing", f"{stage} 缺少 {suite_id} 回执")
            _validate_stage_test_run_receipt(root, reference, suite_id)
        wave_checks = validate_w0_evidence(root, evidence)
    else:
        wave_checks = validate_w1_w2_evidence(root, stage, evidence)
    prior_receipt_digests: list[str] = []
    required_prior_stages = ["S1I-P0", *stage_prior_dependencies(stage)]
    supplied_prior = evidence.get("prior_stage_receipt_digests")
    expect(isinstance(supplied_prior, dict), "wave_prior_receipts_missing", f"{stage} 缺少 prior stage receipts")
    for prior_stage in required_prior_stages:
        prior_path = root / (P0_RECEIPT_PATH if prior_stage == "S1I-P0" else WAVE_RECEIPT_ROOT / f"{prior_stage}.json")
        prior = load_json(prior_path)
        exact(prior.get("stage"), prior_stage, "wave_prior_stage_mismatch", f"{stage} 的 {prior_stage} 回执 stage 不匹配")
        exact(prior.get("status"), "alignment_passed", "wave_prior_stage_failed", f"{stage} 的 {prior_stage} 回执未通过")
        digest = require_hex64(prior.get("receipt_digest"), "wave_prior_digest_invalid", f"{prior_stage} receipt digest 无效")
        exact(object_digest(prior, {"receipt_digest"}), digest, "wave_prior_digest_mismatch", f"{prior_stage} receipt digest 不可重算")
        if prior_stage != "S1I-P0":
            _validate_current_wave_stage_receipt(root, prior_stage, prior, baseline)
        exact(supplied_prior.get(prior_stage), digest, "wave_prior_binding_mismatch", f"{stage} 未绑定当前 {prior_stage} 回执")
        prior_receipt_digests.append(digest)
    exact(set(supplied_prior), set(required_prior_stages), "wave_prior_population_mismatch", f"{stage} prior receipt 人口不是精确依赖闭包")
    effect_check = (
        f"{stage.lower()}_implementation_effect_verified"
        if stage == "W0"
        else f"{stage.lower()}_offline_atomic_harness_scope_verified"
    )
    return evidence, [effect_check, f"{stage.lower()}_atomicity_and_evidence_verified", *wave_checks], prior_receipt_digests


def run_alignment(root: Path, stage: str) -> dict[str, Any]:
    expect(stage in STAGE_SEQUENCE, "unsupported_stage", f"不支持阶段：{stage}")
    checks: list[str] = []
    checks.extend(validate_task(root))
    checks.extend(validate_frozen_design(root))
    checks.extend(validate_documents(root))
    baseline, baseline_checks = validate_baseline(root)
    checks.extend(baseline_checks)
    prior_receipt_digests: list[str] = []
    implementation_candidate_id: str | None = None
    evidence_sha256: str | None = None
    if stage != "S1I-P0":
        evidence, wave_checks, prior_receipt_digests = validate_wave(root, stage, baseline)
        checks.extend(wave_checks)
        implementation_candidate_id = evidence["implementation_candidate_id"]
        evidence_sha256 = file_sha256(root / WAVE_EVIDENCE_ROOT / f"{stage}.json")
    receipt: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_implementation_alignment_receipt_v1",
        "stage": stage,
        "status": "alignment_passed",
        "design_candidate_id": DESIGN_CANDIDATE_ID,
        "implementation_candidate_id": implementation_candidate_id,
        "task_sha256": file_sha256(root / TASK_PATH),
        "target_document_sha256": file_sha256(root / TARGET_PATH),
        "phase_plan_sha256": file_sha256(root / PLAN_PATH),
        "baseline_sha256": file_sha256(root / BASELINE_PATH),
        "baseline_content_digest": baseline["content_digest"],
        "hook_sha256": file_sha256(Path(__file__).resolve()),
        "hook_tests_sha256": file_sha256(
            root / "dev/tests/test_country_outage_p2_s1_implementation_alignment_hook.py"
        ),
        "wave_evidence_sha256": evidence_sha256,
        "prior_stage_receipt_digests": prior_receipt_digests,
        "checks": checks,
        "design_contract_accepted": True,
        "implementation_planning": stage == "S1I-P0",
        "implementation_wave": stage != "S1I-P0",
        "runtime_implemented": stage == "W6",
        "production_deployed": False,
    }
    receipt["receipt_digest"] = object_digest(receipt)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P2-S1 实现工程阶段防跑偏 Hook")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--stage", required=True, choices=STAGE_SEQUENCE)
    parser.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.repo_root).resolve()
    try:
        receipt = run_alignment(root, args.stage)
        if args.output:
            output = (root / args.output).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({
            "stage": args.stage,
            "status": receipt["status"],
            "design_candidate_id": receipt["design_candidate_id"],
            "receipt_digest": receipt["receipt_digest"],
            "output": args.output,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except AlignmentError as error:
        print(f"P2-S1 实现对齐检查失败：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
