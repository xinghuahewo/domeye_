#!/usr/bin/env python3
"""P2-S1 实现工程阶段防跑偏 Hook。

S1I-P0/W0 保留既有验收；W1-W4 只有在原子单元人口、真实实现制品、
冻结合同、W0 Source 谱系、Registry 整波 binding 及三类测试均闭合时才放行。
W5 另需本地隔离组合运行时、独立执行准入、真实执行轨迹、API/UI/导出与
fixture 模型链证据；W6 继续 fail-closed，直到同候选认证补齐同等级证据。
"""

from __future__ import annotations

import argparse
import ast
import copy
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
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
W3_W4_OP33_EVIDENCE_TASK_ID = "country-outage-agent-p2-s1-w3-w4-op33-population-evidence-closure-r2-20260813"
W3_W4_OP33_EVIDENCE_TARGET_VERSION = "country-outage-agent-p2-s1-w3-w4-op33-population-evidence-closure-r2-v1"
W5_TASK_ID = "country-outage-agent-p2-s1-w5-composition-runtime-v6-20260813"
W5_TARGET_VERSION = "country-outage-agent-p2-s1-w5-composition-runtime-v6"
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
    "W1": "1034972e81cc190543a3a8d0b4ccb87e34a00865957e19161e0e0043fab3279c",
    "W2": "d8c040b3d63077752b05b38cb0deaf92b7419011d3f32debc8b8e9fd23afa82a",
    "W3": "ef8037710321a5ef27b414647896d63dfccbaadde379606a5f53bb8b810e9c16",
    "W4": "0584cec0a620d9ddb05674be57461c1c36fbb017818a5a692e69b96db5cd5541",
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
    "w0-python": ("W0", "source_and_store_positive_boundary_attack", "117cd2a14b539b02925316ae7f1d49534630e02a507d55b80b4db650bd05a40a"),
    "w0-typescript": ("W0", "registry_and_receipt_positive_boundary_attack", "4a32ac25dc6d7c2711ce05d9b7b9be785e66405742d72627c1df1d7fefbe3683"),
    "w1-positive": ("W1", "positive", "2a8292dd32188ae9434e402fdfd57ad1827e0ab8d8b9458c4ab4fd8babd4d0a6"),
    "w1-boundary": ("W1", "boundary", "7359b0942da162d0cd40de298cee1e01fe77c85300e2a96a666fb9647cd0f053"),
    "w1-attack": ("W1", "attack", "fddf83b436286a0265063cb4265db326fc2a3d3d9f7fc007d4821c4746e74b58"),
    "w2-positive": ("W2", "positive", "b072f923957fcb439ab05ad9d6f861cce4c3ad604d03bd9369e98096ec35ad2a"),
    "w2-boundary": ("W2", "boundary", "bb7c554a8d53606b986cd6e6498bda410525521ad23ab8cc4a142c4f6b41ba31"),
    "w2-attack": ("W2", "attack", "242a7f19caad40abad866274d1820b10c6243b565636a5d111ade8e47e6ce35f"),
    "w3-positive": ("W3", "positive", "cc433cc588e512bfead252eca61898c54c3d20ad755e91873938245fa653b0bc"),
    "w3-boundary": ("W3", "boundary", "f43b8880810f68e75f56a3b9f7cf0d230db88eac0e22a42d5e627811265dab26"),
    "w3-attack": ("W3", "attack", "961651bb3c275017dd84a27b8e2c2b325b669df6ffd5c5df6b59e139b69633a5"),
    "w4-positive": ("W4", "positive", "c6d768e839321f78f1f8d2df9e94af02bba7463c1a94cfc16787b706259da2ef"),
    "w4-boundary": ("W4", "boundary", "697d52b647925e85546bccb4719cffa2555e326b5d57a947d35c2c73138cff71"),
    "w4-attack": ("W4", "attack", "a4b63ae394dccae9422ca242e7b59a7451e81aabb2e087f30d906297bb5607b3"),
    # W5 的摘要在四个真实 suite 全部通过后冻结；占位值使未签发回执必然
    # fail-closed，不能靠外层 evidence 重签绕过。
    "w5-python": ("W5", "runtime_api_result_graph_export_and_cas", "b2bb01f2d1558e14365d8a23549a6ea2ef13e9ff515419ad20c84b16f4834572"),
    "w5-openapi": ("W5", "openapi_contract_and_generated_types", "6ff574616affb931ee76a68339c9f74ab9528f6f67ea15ac595aa3de685e8278"),
    "w5-sidecar": ("W5", "local_fixture_sol_host_ds_chain", "305af56e55140857f794cec943779ff48c85166ebfa451be2562019dd7f460da"),
    "w5-frontend": ("W5", "ui_journey_typecheck_and_build", "87085d1870dee9820e288ea71a259e13e366016c0d453f53172f0b8ff25501a0"),
}

W5_TEST_SUITE_IDS = (
    "w5-python", "w5-openapi", "w5-sidecar", "w5-frontend",
)
W5_EXECUTION_TRACE_PREFIX = "P2_S1_W5_EXECUTION_TRACE="
W5_ADMISSION_EVENT_ORDER = (
    "plan_design_validated",
    "plan_runtime_admitted",
    "running_committed",
    "first_dispatch",
    "result_set_built",
    "result_set_design_validated",
    "result_set_runtime_admitted",
    "result_set_published",
    "evidence_graph_built",
    "evidence_graph_design_validated",
    "evidence_graph_runtime_admitted",
    "evidence_graph_receipts_published",
    "evidence_graph_published",
    "final_investigation_cas_committed",
)
W5_W4_ACTUAL_SNAPSHOT_ID = (
    "p2-s1-registry-wave-sha256:"
    "027cab0d6d63efb0d10f50ffcefdd280f84a3cc47ccd86aae2ede0e8fbeb7ac6"
)
W5_W4_ACTUAL_SNAPSHOT_DIGEST = (
    "sha256:027cab0d6d63efb0d10f50ffcefdd280f84a3cc47ccd86aae2ede0e8fbeb7ac6"
)
W5_CONTROL_RUNTIME_SCHEMA_PATH = Path(
    "contracts/agent/country-outage-p2-s1-implementation/w5-control-runtime.schema.json"
)
W5_CONTROL_COMMON_SCHEMA_DEFS = {
    "sha256", "nonEmpty", "gateInput", "gateOutput", "rendererInput", "rendererOutput",
}
W5_PERSISTED_ARTIFACT_SCHEMAS = {
    "InvestigationPlan": "contracts/agent/country-outage-p2-s1-execution-unit-design/investigation-plan.schema.json",
    "ResultSet": "contracts/agent/country-outage-p2-s1-execution-unit-design/result-set.schema.json",
    "EvidenceGraph": "contracts/agent/country-outage-p2-s1-execution-unit-design/evidence-graph.schema.json",
}
W5_DESIGN_SEMANTIC_VALIDATORS = {
    "InvestigationPlan": (
        "country_outage_p2_s1_w5_investigation_plan_semantic_validator",
        ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py",
        (
            ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py::validate_investigation_plan_instance",
        ),
    ),
    "ResultSet": (
        "country_outage_p2_s1_w5_result_set_semantic_validator",
        ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py",
        (
            ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py::validate_result_set_instance",
        ),
    ),
    "EvidenceGraph": (
        "country_outage_p2_s1_w5_evidence_graph_semantic_validator",
        ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py",
        (
            ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py::validate_evidence_graph_instance",
        ),
    ),
}
W5_RUNTIME_ARTIFACT_VALIDATORS = {
    "InvestigationPlan": (
        "plan_admission",
        "country_outage_p2_s1_w5_host_plan_admission_validator",
        "backend/services/country_outage_p2_s1_investigation_runtime.py",
        True,
    ),
    "ResultSet": (
        "result_set_freeze",
        "country_outage_p2_s1_w5_result_set_freeze_validator",
        "backend/services/country_outage_p2_s1_result_set.py",
        False,
    ),
    "EvidenceGraph": (
        "evidence_graph_commit",
        "country_outage_p2_s1_w5_evidence_graph_commit_validator",
        "backend/services/country_outage_p2_s1_evidence_graph.py",
        False,
    ),
}
W5_PYTHON_REQUIRED_TEST_NAMES = {
    "test_frozen_design_semantic_validators_replay_actual_runtime_artifacts",
    "test_frozen_design_semantic_validators_reject_named_attacks",
    "test_tool11_exact_time_route_state_journey_commits_result_graph_and_export",
    "test_op29_to_op37_route_path_consistency_journey_commits_result_graph_and_export",
    "test_z_runtime_execution_trace_is_derived_from_actual_spy_and_store",
}
W5_SIDECAR_REQUIRED_OUTPUT_MARKERS = (
    "缺冻结 execution template 的 fixture 在 Sol planning 前 typed fail-closed",
    "projection A + recipe B 无法通过 projection/receipt 闭包",
    "Plan、Registry、capability、node、parameter 与 binding source 漂移均失败关闭",
    "ghost unit 即使重算 recipe/projection 摘要也被冻结 unit 映射拒绝",
)
# Plan 10.7 的三项W5核心旅程：事件全景（TOOL-07）、时间点下钻与
# 证据一致性（W4全人口）。这里只接受runtime/store实际调用聚合，不从
# Plan节点定义推断执行。
W5_REQUIRED_CORE_BUSINESS_UNIT_IDS = {"TOOL-07", *WAVE_CONTRACT["W4"]["unit_ids"]}
W5_EXECUTION_ALLOWED_UNIT_IDS = [
    *WAVE_CONTRACT["W1"]["unit_ids"],
    *WAVE_CONTRACT["W2"]["unit_ids"],
    *WAVE_CONTRACT["W3"]["unit_ids"],
    *WAVE_CONTRACT["W4"]["unit_ids"],
    *sorted(WAVE_CONTRACT["W5"]["unit_ids"]),
]
W5_ARTIFACT_ROLES = {
    ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py": "frozen_design_semantic_validator",
    "backend/services/country_outage_p2_s1_contract_runtime.py": "host_contract_runtime",
    "backend/services/country_outage_p2_s1_trusted_store.py": "content_addressed_store",
    "backend/services/country_outage_p2_s1_registry_dispatcher.py": "trusted_static_dispatcher",
    "backend/services/country_outage_p2_s1_result_set.py": "result_set_runtime",
    "backend/services/country_outage_p2_s1_evidence_graph.py": "evidence_graph_runtime",
    "backend/services/country_outage_p2_s1_delivery.py": "delivery_runtime",
    "backend/services/country_outage_p2_s1_investigation_runtime.py": "investigation_runtime",
    W5_CONTROL_RUNTIME_SCHEMA_PATH.as_posix(): "control_unit_runtime_schema",
    "backend/web/api/v2/country_outage_investigations.py": "investigation_api",
    "backend/web/api/v2/route.py": "api_route_registration",
    "backend/web/tests/test_country_outage_p2_s1_runtime.py": "runtime_test",
    "backend/web/tests/test_country_outage_p2_s1_investigation_api.py": "api_test",
    "backend/pyproject.toml": "python_dependency_contract",
    "backend/uv.lock": "python_locked_dependencies",
    "contracts/openapi.json": "openapi_contract",
    "backend/web/tests/test_openapi_contract.py": "openapi_test",
    "dev/verify_openapi_types.py": "openapi_generated_type_verifier",
    "frontend/package.json": "frontend_runtime_manifest",
    "frontend/package-lock.json": "frontend_locked_dependencies",
    "frontend/src/types/openapi.generated.d.ts": "generated_openapi_types",
    "frontend/src/types/api.ts": "frontend_api_types",
    "frontend/src/api/countryOutageInvestigation.ts": "frontend_api_client",
    "frontend/src/api/countryOutageInvestigation.test.ts": "frontend_api_test",
    "frontend/src/pages/CountryOutageInvestigationPage.vue": "investigation_page",
    "frontend/src/pages/CountryOutageInvestigationPage.test.ts": "investigation_page_test",
    "frontend/src/pages/EventDetailPage.vue": "event_page_entry",
    "frontend/src/pages/EventDetailPage.test.ts": "event_page_entry_test",
    "frontend/src/components/CountryOutageInvestigationPlan.vue": "investigation_plan_component",
    "frontend/src/router/index.ts": "frontend_route_registration",
    "agent-sidecar/package.json": "sidecar_runtime_manifest",
    "agent-sidecar/package-lock.json": "sidecar_locked_dependencies",
    "agent-sidecar/tsconfig.json": "sidecar_typescript_contract",
    "agent-sidecar/src/chat/index.ts": "sidecar_chat_export_surface",
    "agent-sidecar/src/chat/p2-s1-composition-contracts.ts": "model_chain_contracts",
    "agent-sidecar/src/chat/p2-s1-model-runner.ts": "fixture_model_port",
    "agent-sidecar/src/chat/p2-s1-dual-artifact-store.ts": "dual_artifact_store",
    "agent-sidecar/src/chat/p2-s1-teacher-plan-grounder.ts": "teacher_grounding",
    "agent-sidecar/src/chat/p2-s1-gate-validator.ts": "model_gate_validator",
    "agent-sidecar/src/chat/p2-s1-oracle-materializer.ts": "independent_oracle",
    "agent-sidecar/src/chat/p2-s1-alignment-evaluator.ts": "student_alignment",
    "agent-sidecar/src/chat/p2-s1-composition-runtime.ts": "sol_host_ds_runtime",
    "agent-sidecar/src/chat/p2-s1-integrated-answer-runtime.ts": "integrated_answer_runtime",
    "agent-sidecar/src/chat/p2-s1-planning-grounding-port.ts": "trusted_planning_grounding_port",
    "agent-sidecar/src/server/p2-s1-w5-http-handler.ts": "loopback_fixture_handler",
    "agent-sidecar/src/cli/formal-p2-s1-w5-sidecar.ts": "sidecar_fixture_cli",
    "agent-sidecar/src/cli/serve-formal-p2-s1-w5.ts": "sidecar_fixture_server_cli",
    "agent-sidecar/tests/p2-s1-w5-composition-runtime.test.ts": "model_chain_test",
    "agent-sidecar/tests/p2-s1-w5-integrated-answer-test-server.test.ts": "python_sidecar_e2e_server",
    "agent-sidecar/tests/p2-s1-w5-planning-grounding-port.test.ts": "planning_grounding_port_test",
}
W5_SUITE_ARTIFACT_PATHS = {
    "w5-python": (
        ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py",
        "backend/services/country_outage_p2_s1_contract_runtime.py",
        "backend/services/country_outage_p2_s1_trusted_store.py",
        "backend/services/country_outage_p2_s1_registry_dispatcher.py",
        "backend/services/country_outage_p2_s1_result_set.py",
        "backend/services/country_outage_p2_s1_evidence_graph.py",
        "backend/services/country_outage_p2_s1_delivery.py",
        "backend/services/country_outage_p2_s1_investigation_runtime.py",
        W5_CONTROL_RUNTIME_SCHEMA_PATH.as_posix(),
        "backend/web/api/v2/country_outage_investigations.py",
        "backend/web/api/v2/route.py",
        "backend/web/tests/test_country_outage_p2_s1_runtime.py",
        "backend/web/tests/test_country_outage_p2_s1_investigation_api.py",
        "backend/pyproject.toml",
        "backend/uv.lock",
        "agent-sidecar/package.json",
        "agent-sidecar/package-lock.json",
        "agent-sidecar/tsconfig.json",
        "agent-sidecar/src/chat/p2-s1-composition-contracts.ts",
        "agent-sidecar/src/chat/p2-s1-model-runner.ts",
        "agent-sidecar/src/chat/p2-s1-dual-artifact-store.ts",
        "agent-sidecar/src/chat/p2-s1-teacher-plan-grounder.ts",
        "agent-sidecar/src/chat/p2-s1-gate-validator.ts",
        "agent-sidecar/src/chat/p2-s1-oracle-materializer.ts",
        "agent-sidecar/src/chat/p2-s1-alignment-evaluator.ts",
        "agent-sidecar/src/chat/p2-s1-composition-runtime.ts",
        "agent-sidecar/src/chat/p2-s1-integrated-answer-runtime.ts",
        "agent-sidecar/src/server/p2-s1-w5-http-handler.ts",
        "agent-sidecar/tests/p2-s1-w5-integrated-answer-test-server.test.ts",
    ),
    "w5-openapi": (
        "contracts/openapi.json",
        "backend/web/tests/test_openapi_contract.py",
        "dev/verify_openapi_types.py",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/src/types/openapi.generated.d.ts",
    ),
    "w5-sidecar": (
        "agent-sidecar/package.json",
        "agent-sidecar/package-lock.json",
        "agent-sidecar/tsconfig.json",
        "agent-sidecar/src/chat/index.ts",
        "agent-sidecar/src/chat/p2-s1-composition-contracts.ts",
        "agent-sidecar/src/chat/p2-s1-model-runner.ts",
        "agent-sidecar/src/chat/p2-s1-dual-artifact-store.ts",
        "agent-sidecar/src/chat/p2-s1-teacher-plan-grounder.ts",
        "agent-sidecar/src/chat/p2-s1-gate-validator.ts",
        "agent-sidecar/src/chat/p2-s1-oracle-materializer.ts",
        "agent-sidecar/src/chat/p2-s1-alignment-evaluator.ts",
        "agent-sidecar/src/chat/p2-s1-composition-runtime.ts",
        "agent-sidecar/src/chat/p2-s1-integrated-answer-runtime.ts",
        "agent-sidecar/src/chat/p2-s1-planning-grounding-port.ts",
        "agent-sidecar/src/server/p2-s1-w5-http-handler.ts",
        "agent-sidecar/src/cli/formal-p2-s1-w5-sidecar.ts",
        "agent-sidecar/src/cli/serve-formal-p2-s1-w5.ts",
        "agent-sidecar/tests/p2-s1-w5-composition-runtime.test.ts",
        "agent-sidecar/tests/p2-s1-w5-integrated-answer-test-server.test.ts",
        "agent-sidecar/tests/p2-s1-w5-planning-grounding-port.test.ts",
    ),
    "w5-frontend": (
        "frontend/src/api/countryOutageInvestigation.ts",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/src/api/countryOutageInvestigation.test.ts",
        "frontend/src/pages/CountryOutageInvestigationPage.vue",
        "frontend/src/pages/CountryOutageInvestigationPage.test.ts",
        "frontend/src/pages/EventDetailPage.vue",
        "frontend/src/pages/EventDetailPage.test.ts",
        "frontend/src/components/CountryOutageInvestigationPlan.vue",
        "frontend/src/router/index.ts",
        "frontend/src/types/api.ts",
        "frontend/src/types/openapi.generated.d.ts",
    ),
}
W5_SUITE_COMMANDS = {
    "w5-python": [
        "bash", "-lc",
        "cd agent-sidecar && npm run build && cd .. && "
        "uv run --project backend python -m unittest -v "
        "backend.web.tests.test_country_outage_p2_s1_runtime "
        "backend.web.tests.test_country_outage_p2_s1_investigation_api",
    ],
    "w5-openapi": [
        "bash", "-lc",
        "uv run --project backend pytest -q backend/web/tests/test_openapi_contract.py && python3 dev/verify_openapi_types.py",
    ],
    "w5-sidecar": [
        "bash", "-lc",
        "npm run test:p2-s1-w5",
    ],
    "w5-frontend": [
        "bash", "-lc",
        "npm test -- --run src/api/countryOutageInvestigation.test.ts src/pages/CountryOutageInvestigationPage.test.ts src/pages/EventDetailPage.test.ts && npm run typecheck && npm run build",
    ],
}
W5_SUITE_WORKING_DIRECTORIES = {
    "w5-python": ".", "w5-openapi": ".",
    "w5-sidecar": "agent-sidecar", "w5-frontend": "frontend",
}
W5_SUITE_SELECTED_TEST_IDS = {
    "w5-python": [
        "backend.web.tests.test_country_outage_p2_s1_runtime",
        "backend.web.tests.test_country_outage_p2_s1_investigation_api",
    ],
    "w5-openapi": ["backend.web.tests.test_openapi_contract"],
    "w5-sidecar": [
        "agent-sidecar/tests/p2-s1-w5-composition-runtime.test.ts",
        "agent-sidecar/tests/p2-s1-w5-planning-grounding-port.test.ts",
    ],
    "w5-frontend": [
        "frontend/src/api/countryOutageInvestigation.test.ts",
        "frontend/src/pages/CountryOutageInvestigationPage.test.ts",
        "frontend/src/pages/EventDetailPage.test.ts",
    ],
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
    w3_w4_op33_evidence_task = task_id == W3_W4_OP33_EVIDENCE_TASK_ID
    w3_w4_task = task_id == W3_W4_TASK_ID or w3_w4_op33_evidence_task
    w5_task = task_id == W5_TASK_ID
    expect(w5_task, "task_identity_mismatch", "最终W0-W5同候选重签只接受冻结的W5-v6 Task")
    if w0_task:
        exact(task.get("targetVersion"), "country-outage-agent-p2-s1-w0-source-governance-v1", "task_version_mismatch", "W0 目标版本不匹配")
    elif w1_w2_task:
        exact(task.get("targetVersion"), W1_W2_TARGET_VERSION, "task_version_mismatch", "W1/W2 目标版本不匹配")
    elif w3_w4_task:
        exact(
            task.get("targetVersion"),
            W3_W4_OP33_EVIDENCE_TARGET_VERSION if w3_w4_op33_evidence_task else W3_W4_TARGET_VERSION,
            "task_version_mismatch",
            "W3/W4 目标版本不匹配",
        )
    else:
        exact(task.get("targetVersion"), W5_TARGET_VERSION, "task_version_mismatch", "W5 目标版本不匹配")
    transition = task.get("taskTransition")
    expect(isinstance(transition, dict), "task_transition_missing", "缺少任务迁移记录")
    exact(transition.get("frozenDesignCandidateId"), DESIGN_CANDIDATE_ID, "task_design_binding_mismatch", "Task 未绑定冻结设计候选")
    exact(transition.get("frozenDesignCandidateSha256"), DESIGN_CANDIDATE_SHA256, "task_design_sha_mismatch", "Task 设计摘要不匹配")
    expected_p0_receipt = (
        "0d661430471008cd84115bcdd6e1fcc7e404619b9503f8cf41a7148f2ce63b59"
        if w3_w4_task or w5_task
        else "2e2d72f18f030bb1f91e7037e6f88786f64d9b4b7865a36b82f605e7e701d838"
    )
    exact(transition.get("s1ip0ReceiptDigest"), expected_p0_receipt, "task_p0_binding_mismatch", "Task 未绑定创建任务时冻结的 S1I-P0 回执")
    forbidden = task.get("forbiddenPaths")
    expect(isinstance(forbidden, list), "task_forbidden_paths_missing", "缺少 forbiddenPaths")
    required_forbidden = ["backend/core/**", "backend/data_pipeline/**", "backend/database/**", "deploy/**", "tools/rrc25-iran-replay-go/**"]
    if not w5_task:
        required_forbidden.extend(["backend/web/api/**", "frontend/**"])
    for pattern in required_forbidden:
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
    elif w3_w4_task:
        exact(
            transition.get("supersedesTaskId"),
            W3_W4_TASK_ID if w3_w4_op33_evidence_task else W1_W2_TASK_ID,
            "task_transition_invalid",
            "W3/W4 未显式继承已通过的前序 Task",
        )
        exact(transition.get("implementationBaselineSha256"), "9dc80bec20db0c68ee044c4da9e4148a2a2ab7bd1c70c8863ae737cc6231422f", "task_baseline_transition_mismatch", "W3/W4 Task 创建时的实现基线摘要漂移")
        exact(transition.get("w0ReceiptDigest"), "cbab6787eeec1071c1c982063085f6fadb16e85e584c76289a43b31aecd4108c", "task_w0_binding_mismatch", "W3/W4 Task 未绑定创建时冻结的 W0 回执")
        exact(transition.get("w1ReceiptDigest"), "2ac94ea56923bd8dff140af56aa6e8a876860931f6bd8fd479a4908eeaa34c73", "task_w1_binding_mismatch", "W3/W4 Task 未绑定创建时冻结的 W1 回执")
        exact(transition.get("w2ReceiptDigest"), "b4672f844e559d3bdf44d713fd02f674ec431f4caacb77c3afa85886c27298a1", "task_w2_binding_mismatch", "W3/W4 Task 未绑定创建时冻结的 W2 回执")
        if w3_w4_op33_evidence_task:
            exact(transition.get("w3ReceiptDigest"), "f5c4dd1ea7208e023a9432b4ab4c273ca0f8cf4a5e15fbfeb181210ef542c6a2", "task_w3_binding_mismatch", "OP-33 Evidence Task 未绑定前序 W3 回执")
            exact(transition.get("w4ReceiptDigest"), "2453e5d884e6ae821cd00573b467f430bfc838a5a6902b4ebc8d20402d246b6a", "task_w4_binding_mismatch", "OP-33 Evidence Task 未绑定前序 W4 回执")
    else:
        exact(transition.get("supersedesTaskId"), "country-outage-agent-p2-s1-w5-composition-runtime-v5-20260813", "task_transition_invalid", "W5-v6 未显式取代已封存但 Sidecar 人口不完整的 W5-v5 Task")
        exact(transition.get("implementationBaselineSha256"), "9dc80bec20db0c68ee044c4da9e4148a2a2ab7bd1c70c8863ae737cc6231422f", "task_baseline_transition_mismatch", "W5 Task 创建时的实现基线摘要漂移")
        for stage, expected_digest in {
            "w0ReceiptDigest": "eb6fb53994a344c2bb3a6085f17269df851356b42ccbcf53301ec408ab0cd013",
            "w1ReceiptDigest": "8fbf092fecffe241dad9e03fff076e4805143509c1a665ea755816171350b7c2",
            "w2ReceiptDigest": "7feb0c930fc836525577dde3f49df52311cb237489dab30d7ca1491384feaf45",
            "w3ReceiptDigest": "aa17bd95687265deb97a85327e3aaf2c4fcd8cada7a4aeee6a044b97739e8239",
            "w4ReceiptDigest": "68c1c3546357d2ac5fe98026832ad0cee77cabb66479d788854061709c644918",
        }.items():
            exact(transition.get(stage), expected_digest, "task_prior_wave_binding_mismatch", f"W5 Task 未绑定当前 {stage}")
        for required_path in (
            "contracts/openapi.json",
            "backend/web/api/v2/country_outage_investigations.py",
            "frontend/src/pages/EventDetailPage.vue",
            "agent-sidecar/src/cli/formal-p2-s1-w5-sidecar.ts",
            "backend/pyproject.toml",
        ):
            expect(required_path in allowed, "w5_runtime_path_not_authorized", f"W5 未授权必要用户旅程路径：{required_path}")
        required_checks = task.get("requiredChecks")
        expect(isinstance(required_checks, list), "w5_required_checks_missing", "W5-v6 缺少 requiredChecks")
        commands = [item.get("command") for item in required_checks if isinstance(item, dict)]
        expect(
            [
                "uv", "run", "--project", "backend", "pytest", "-q",
                "backend/web/tests/test_openapi_contract.py",
            ] in commands,
            "w5_openapi_zero_test_command_forbidden",
            "W5-v6 必须用pytest实际执行7个OpenAPI测试，禁止unittest 0测假绿",
        )
        expect(
            any(
                isinstance(command, list)
                and command[:2] == ["bash", "-lc"]
                and len(command) == 3
                and (
                    "npm run test:p2-s1-w5" in command[2]
                    or (
                        "dist/tests/p2-s1-w5-composition-runtime.test.js" in command[2]
                        and "dist/tests/p2-s1-w5-planning-grounding-port.test.js" in command[2]
                    )
                )
                for command in commands
            ),
            "w5_sidecar_suite_incomplete",
            "W5-v6 Sidecar requiredCheck 必须实际执行 composition 与 planning/grounding 两套测试，数量由真实runner下界验收",
        )
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
            isinstance(pattern, str) and fnmatch.fnmatchcase(path_text, pattern)
            for pattern in allowed
        )
        expect(authorized, "wave_runtime_path_not_authorized", f"当前原子波次未授权实现路径：{path}")
    non_goals = task.get("explicitNonGoals")
    expect(isinstance(non_goals, list), "wave_non_goals_missing", "原子实现波次缺少显式非目标")
    non_goal_text = "\n".join(item for item in non_goals if isinstance(item, str))
    expected_non_goal_phrases = (
        ("每个Tool只能过滤和分页一种W0已验证事实人口", "每个Operator只能执行一种登记的确定性业务变换", "PLAN-CAP-02", "本阶段不修改生产")
        if w1_w2_task
        else (("W4不可调用Registry binding保持不变", "PLAN-CAP-02", "不调用外部模型", "不表示性能、模型、运行时晋级或生产部署通过") if w5_task else ("TOOL-11只能读取W0预物化", "只能执行一种登记的确定性业务变换", "execution_allowed_unit_ids必须为空", "PLAN-CAP-02", "本阶段不修改生产"))
    )
    for phrase in expected_non_goal_phrases:
        expect(phrase in non_goal_text, "wave_non_goals_missing", f"原子实现波次非目标未闭合：{phrase}")
    return ["w1_w2_task_boundary_verified" if w1_w2_task else ("w5_task_boundary_verified" if w5_task else "w3_w4_task_boundary_verified")]


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
    exact(
        receipt.get("runner_version"),
        "1.2.0" if expected_stage == "W5" else "1.0.0",
        "wave_test_receipt_invalid",
        "测试 runner version 漂移",
    )
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
    if expected_suite_id in W5_SUITE_COMMANDS:
        exact(receipt.get("command"), W5_SUITE_COMMANDS[expected_suite_id], "w5_test_command_mismatch", f"{expected_suite_id} 未执行冻结真实命令")
        exact(receipt.get("working_directory"), W5_SUITE_WORKING_DIRECTORIES[expected_suite_id], "w5_test_command_mismatch", f"{expected_suite_id} 工作目录漂移")
        exact(selected, W5_SUITE_SELECTED_TEST_IDS[expected_suite_id], "w5_test_selector_mismatch", f"{expected_suite_id} 测试选择器漂移")
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
    if expected_stage == "W5":
        count_patterns = {
            "w5-python": r"(?m)^Ran (\d+) tests? in <ELAPSED>$",
            "w5-openapi": r"(?m)^(?:\.+\s+)?(\d+) passed in <ELAPSED>$",
            "w5-sidecar": r"(?m)^[#ℹ] tests (\d+)$",
            "w5-frontend": r"(?m)^\s*Tests\s+(\d+) passed\b",
        }
        reported_counts = re.findall(count_patterns[expected_suite_id], output)
        exact(len(reported_counts), 1, "w5_test_count_output_invalid", f"{expected_suite_id} normalized output 缺少唯一真实测试总数")
        exact(tests_run, int(reported_counts[0]), "w5_test_count_output_mismatch", f"{expected_suite_id} tests_run 与子进程输出不一致")
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
    if expected_suite_id in W5_SUITE_ARTIFACT_PATHS:
        exact(
            artifact_paths,
            {STAGE_TEST_RUNNER_PATH.as_posix(), *W5_SUITE_ARTIFACT_PATHS[expected_suite_id]},
            "w5_test_artifact_population_mismatch",
            f"{expected_suite_id} runner artifact bindings 不是冻结精确人口",
        )
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


def _validate_op33_population_evidence_contract(root: Path) -> None:
    operator_schema = load_json(root / OPERATOR_CONTRACT_SCHEMA_PATH)
    op33_inputs = operator_schema.get("$defs", {}).get("op33InputPayload", {}).get("properties", {})
    expected_items = {
        "new_prefix_state_rows": "#/$defs/newPrefixState",
        "route_state_rows": "#/$defs/routeStateAtTime",
    }
    for field, item_ref in expected_items.items():
        field_schema = op33_inputs.get(field)
        expect(isinstance(field_schema, dict), "op33_empty_population_contract_open", f"OP-33 缺少 {field} 输入合同")
        exact(field_schema.get("type"), "array", "op33_empty_population_contract_open", f"OP-33 {field} 必须保持数组人口")
        exact(field_schema.get("items"), {"$ref": item_ref}, "op33_empty_population_contract_open", f"OP-33 {field} 成员 Schema 漂移")
        expect("minItems" not in field_schema, "op33_empty_population_contract_open", f"OP-33 {field} 不得拒绝合法空人口")

    binding_schema = load_json(root / STRUCTURAL_BINDING_PATH)
    binding = binding_schema.get("$defs", {}).get("populationEvidenceBindingReceipt", {})
    properties = binding.get("properties", {})
    operator_ids = properties.get("operator_id", {}).get("enum", [])
    input_names = properties.get("operator_input_name", {}).get("enum", [])
    expect("OP-33" in operator_ids, "op33_population_evidence_contract_open", "人口 Evidence 合同未登记 OP-33")
    for input_name in expected_items:
        expect(input_name in input_names, "op33_population_evidence_contract_open", f"人口 Evidence 合同未登记 {input_name}")
    expect("identity_digest" in properties, "op33_population_evidence_contract_open", "OP-33 人口回执未声明 identity_digest")
    conditionals = binding.get("allOf", [])
    expect(
        any(
            item.get("if", {}).get("properties", {}).get("operator_id", {}).get("const") == "OP-33"
            and "identity_digest" in item.get("then", {}).get("required", [])
            for item in conditionals
            if isinstance(item, dict)
        ),
        "op33_population_evidence_contract_open",
        "OP-33 人口回执未机器强制 identity_digest",
    )

    implementation_path = root / W1_W2_OPERATOR_IMPLEMENTATION_PATH
    tree = ast.parse(implementation_path.read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    op33 = functions.get("op33_join_new_prefix_route_state")
    expect(op33 is not None, "op33_population_evidence_contract_open", "缺少 OP-33 实现")
    keyword_names = {item.arg for item in op33.args.kwonlyargs}
    expect(
        {"population_evidence_bindings", "offline_structural_context"}.issubset(keyword_names),
        "op33_population_evidence_contract_open",
        "OP-33 未接收双人口绑定与离线结构上下文",
    )
    binding_calls = [
        node for node in ast.walk(op33)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_population_evidence"
    ]
    exact(len(binding_calls), 2, "op33_population_evidence_contract_open", "OP-33 必须恰好验证左右两个人口回执")
    op33_source = ast.get_source_segment(implementation_path.read_text(encoding="utf-8"), op33) or ""
    for input_name in expected_items:
        expect(input_name in op33_source, "op33_population_evidence_contract_open", f"OP-33 未绑定 {input_name}")


def _validate_w5_artifact_manifest(root: Path, value: Any) -> None:
    expect(isinstance(value, list), "w5_artifact_manifest_missing", "W5 缺少精确制品人口")
    exact(len(value), len(W5_ARTIFACT_ROLES), "w5_artifact_population_mismatch", "W5 制品数量漂移")
    by_path: dict[str, Any] = {}
    for item in value:
        expect(isinstance(item, dict), "w5_artifact_invalid", "W5 制品引用必须是对象")
        path = item.get("path")
        expect(isinstance(path, str) and path not in by_path, "w5_artifact_population_mismatch", "W5 制品路径无效或重复")
        by_path[path] = item
    exact(set(by_path), set(W5_ARTIFACT_ROLES), "w5_artifact_population_mismatch", "W5 制品人口不是冻结精确集合")
    for path, role in W5_ARTIFACT_ROLES.items():
        _validate_bound_artifact(root, by_path[path], path, role, "w5_artifact_digest_mismatch")


def _validate_w5_dispatcher_source(root: Path) -> None:
    """独立检查静态 Dispatcher 边界，禁止 evidence 自报覆盖 callback seam。"""

    path = root / "backend/services/country_outage_p2_s1_registry_dispatcher.py"
    source = require_text(path)
    tree = ast.parse(source)
    dispatcher = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "W5RegistryDispatcher"),
        None,
    )
    expect(dispatcher is not None, "w5_dispatcher_missing", "缺少 W5RegistryDispatcher")
    execute = next(
        (node for node in dispatcher.body if isinstance(node, ast.FunctionDef) and node.name == "execute"),
        None,
    )
    expect(execute is not None, "w5_dispatcher_missing", "缺少 Dispatcher.execute")
    positional = [argument.arg for argument in execute.args.args]
    kwonly = [argument.arg for argument in execute.args.kwonlyargs]
    exact(positional, ["self", "unit_id", "request"], "w5_dispatcher_callback_seam_open", "Dispatcher.execute 位置参数漂移")
    exact(kwonly, ["trusted_context_digest"], "w5_dispatcher_callback_seam_open", "Dispatcher.execute 关键字参数漂移")
    exact(execute.args.vararg, None, "w5_dispatcher_callback_seam_open", "Dispatcher 不得接受 *args")
    exact(execute.args.kwarg, None, "w5_dispatcher_callback_seam_open", "Dispatcher 不得接受 **kwargs")
    for forbidden_name in ("eval", "exec", "__import__"):
        expect(
            not any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == forbidden_name
                for node in ast.walk(execute)
            ),
            "w5_dispatcher_callback_seam_open",
            f"Dispatcher.execute 不得调用 {forbidden_name}",
        )
    expect(W5_W4_ACTUAL_SNAPSHOT_ID in source, "w5_registry_predecessor_mismatch", "Dispatcher 未冻结 actual W4 snapshot")
    expect('"arbitrary_callback_supported": False' in source, "w5_dispatcher_callback_seam_open", "execution admission 未关闭 callback")


def _validate_w5_python_validator_test_source(root: Path, normalized_output: str) -> None:
    """把具名正/负回放与核心旅程绑定到 pinned Python test 字节。"""

    test_path = root / "backend/web/tests/test_country_outage_p2_s1_runtime.py"
    source = test_path.read_text(encoding="utf-8")
    try:
        module = ast.parse(source)
    except SyntaxError as error:
        raise AlignmentError(f"w5_python_test_source_invalid：runtime test 不是合法 Python：{error}") from error
    functions = {
        node.name: node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    expect(W5_PYTHON_REQUIRED_TEST_NAMES <= set(functions), "w5_python_required_test_missing", "Python runner 源码缺少完整validator或核心journey具名测试")
    for test_name in W5_PYTHON_REQUIRED_TEST_NAMES:
        expect(test_name in normalized_output, "w5_python_required_test_not_run", f"pinned runner 未实际运行 {test_name}")
    called_attributes = {
        node.func.attr
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    exact_design_functions = {
        "validate_investigation_plan_instance",
        "validate_result_set_instance",
        "validate_evidence_graph_instance",
    }
    expect(exact_design_functions <= called_attributes, "w5_design_semantic_validator_entrypoint_invalid", "runtime test 未实际调用冻结设计Hook三项完整validator")
    string_literals = {
        node.value for node in ast.walk(module)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    required_attack_codes = {
        "plan_admission_receipt_unresolved",
        "result_set_sort_digest_mismatch",
        "evidence_graph_plan_digest_mismatch",
    }
    expect(required_attack_codes <= string_literals, "w5_design_semantic_attack_test_missing", "runtime test 未断言三类完整validator具名失败码")
    trace_test = functions["test_z_runtime_execution_trace_is_derived_from_actual_spy_and_store"]
    trace_called_attributes = {
        node.func.attr
        for node in ast.walk(trace_test)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    expect(
        not ({"_runtime_artifact_admission", "_frozen_design_semantic_admission", "_admission_event"} & trace_called_attributes),
        "w5_trace_posthoc_admission_mint_forbidden",
        "正式trace测试不得在执行后新铸A/B admission或event，只能读取真实runtime路径已提交回执",
    )
    trace_store_reads = {
        node.args[0].value
        for node in ast.walk(trace_test)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "list_json"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        )
    }
    trace_literals = {
        node.value for node in ast.walk(trace_test)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    expect(
        {
            "design-semantic-validator-receipt",
            "runtime-artifact-admission",
            "receipt",
            "control-execution-call",
            "admission-event-context",
            "admission-event",
        } <= trace_store_reads,
        "w5_trace_actual_store_population_missing",
        "正式trace测试未从store读取A/B、事件链、业务Schema与控制调用真实人口",
    )
    expect(
        "business_runtime_schema_validation" in trace_literals,
        "w5_trace_actual_store_population_missing",
        "正式trace测试未按业务Schema回执类型筛选真实receipt人口",
    )


def _validate_w5_planning_grounding_source(root: Path) -> None:
    """请求与runtime构造器均不得接收调用方裸 plan_nodes。"""

    path = root / "backend/services/country_outage_p2_s1_investigation_runtime.py"
    module = ast.parse(path.read_text(encoding="utf-8"))
    for class_name in ("TrustedFixturePlanningGroundingPort", "LocalFixtureSidecarPlanningGroundingPort"):
        class_node = next(
            (node for node in module.body if isinstance(node, ast.ClassDef) and node.name == class_name),
            None,
        )
        expect(class_node is not None, "w5_trusted_planning_grounding_missing", f"缺少 {class_name}")
        init = next(
            (node for node in class_node.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"),
            None,
        )
        expect(init is not None, "w5_trusted_planning_grounding_missing", f"{class_name} 缺少静态构造合同")
        arguments = [item.arg for item in (*init.args.args, *init.args.kwonlyargs)]
        expect("plan_nodes" not in arguments, "w5_constructor_plan_nodes_forbidden", f"{class_name} 构造器不得接受裸 plan_nodes")
    build = next(
        (node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "build_local_fixture_runtime"),
        None,
    )
    expect(build is not None, "w5_trusted_planning_grounding_missing", "缺少 build_local_fixture_runtime")
    build_arguments = [item.arg for item in (*build.args.args, *build.args.kwonlyargs)]
    expect("plan_nodes" not in build_arguments, "w5_constructor_plan_nodes_forbidden", "runtime builder不得接受裸 plan_nodes")
    runtime_class = next(
        (node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "CountryOutageP2S1InvestigationRuntime"),
        None,
    )
    create = next(
        (node for node in runtime_class.body if isinstance(node, ast.FunctionDef) and node.name == "create_investigation"),
        None,
    ) if runtime_class is not None else None
    expect(create is not None, "w5_trusted_planning_grounding_missing", "缺少 create_investigation")
    expect(
        not any(isinstance(node, ast.Constant) and node.value == "plan_nodes" for node in ast.walk(create)),
        "w5_request_plan_nodes_forbidden",
        "create request不得读取或接受裸 plan_nodes",
    )


def _w5_control_schema_refs(unit_id: str) -> tuple[str, str]:
    stem = unit_id.lower().replace("-", "")
    return (
        f"w5-control-runtime.schema.json#/$defs/{stem}Input",
        f"w5-control-runtime.schema.json#/$defs/{stem}Output",
    )


def _validate_w5_control_runtime_schema(root: Path) -> tuple[dict[str, Any], str]:
    schema = load_json(root / W5_CONTROL_RUNTIME_SCHEMA_PATH)
    validator_code = """
import json
import sys
from jsonschema import Draft202012Validator
with open(sys.argv[1], encoding='utf-8') as source:
    Draft202012Validator.check_schema(json.load(source))
""".strip()
    dependency_project = Path(__file__).resolve().parents[2] / "backend"
    completed = subprocess.run(
        ["uv", "run", "--project", str(dependency_project), "python", "-c", validator_code, str(root / W5_CONTROL_RUNTIME_SCHEMA_PATH)],
        text=True,
        capture_output=True,
        check=False,
    )
    expect(
        completed.returncode == 0,
        "w5_control_schema_invalid",
        f"控制单元 Schema 不是合法 Draft 2020-12：{completed.stderr.strip()}",
    )
    exact(schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema", "w5_control_schema_invalid", "控制单元 Schema draft 漂移")
    definitions = schema.get("$defs")
    expect(isinstance(definitions, dict), "w5_control_schema_invalid", "控制单元 Schema 缺少 $defs")
    unit_definitions = {
        reference.rsplit("/", 1)[-1]
        for unit_id in WAVE_CONTRACT["W5"]["unit_ids"]
        for reference in _w5_control_schema_refs(unit_id)
    }
    exact(
        set(definitions),
        unit_definitions | W5_CONTROL_COMMON_SCHEMA_DEFS,
        "w5_control_schema_population_mismatch",
        "6个公共defs与11个控制单元input/output defs人口不精确",
    )

    def resolve(name: str, seen: set[str] | None = None) -> dict[str, Any]:
        seen = set(seen or set())
        expect(name not in seen, "w5_control_schema_ref_cycle", f"控制单元 Schema ref 成环：{name}")
        seen.add(name)
        definition = definitions.get(name)
        expect(isinstance(definition, dict), "w5_control_schema_invalid", f"{name} 不是 Schema 对象")
        if set(definition) == {"$ref"}:
            reference = definition["$ref"]
            expect(
                isinstance(reference, str) and reference.startswith("#/$defs/"),
                "w5_control_schema_ref_invalid",
                f"{name} 只允许本文件 $defs ref",
            )
            target = reference.removeprefix("#/$defs/")
            expect(target in W5_CONTROL_COMMON_SCHEMA_DEFS, "w5_control_schema_ref_invalid", f"{name} 只能引用冻结公共defs")
            return resolve(target, seen)
        if set(definition) == {"allOf"}:
            parts = definition["allOf"]
            expect(isinstance(parts, list) and len(parts) == 2, "w5_control_schema_ref_invalid", f"{name} allOf 必须是公共ref加单元const")
            reference_part, overlay = parts
            expect(isinstance(reference_part, dict) and set(reference_part) == {"$ref"}, "w5_control_schema_ref_invalid", f"{name} allOf 第一项必须是公共ref")
            reference = reference_part["$ref"]
            expect(isinstance(reference, str) and reference.startswith("#/$defs/"), "w5_control_schema_ref_invalid", f"{name} allOf ref 无效")
            target = reference.removeprefix("#/$defs/")
            expect(target in W5_CONTROL_COMMON_SCHEMA_DEFS, "w5_control_schema_ref_invalid", f"{name} allOf 只能引用冻结公共defs")
            expect(isinstance(overlay, dict) and set(overlay) == {"properties"}, "w5_control_schema_ref_invalid", f"{name} allOf 第二项只能收紧 properties")
            overlay_properties = overlay.get("properties")
            expect(isinstance(overlay_properties, dict) and overlay_properties, "w5_control_schema_ref_invalid", f"{name} allOf properties 无效")
            base = copy.deepcopy(resolve(target, seen))
            base_properties = base.get("properties")
            expect(isinstance(base_properties, dict) and set(overlay_properties) <= set(base_properties), "w5_control_schema_ref_invalid", f"{name} allOf 只能收紧公共字段")
            for field, field_schema in overlay_properties.items():
                expect(isinstance(field_schema, dict) and set(field_schema) == {"const"}, "w5_control_schema_ref_invalid", f"{name}.{field} 必须用 const 收紧")
                base_properties[field] = copy.deepcopy(field_schema)
            return base
        expect("$ref" not in definition, "w5_control_schema_ref_invalid", f"{name} 不得混合未解析顶层 $ref")
        return definition

    for name in unit_definitions:
        effective = resolve(name)
        exact(effective.get("type"), "object", "w5_control_schema_invalid", f"{name} 必须解析为对象")
        exact(effective.get("additionalProperties"), False, "w5_control_schema_open", f"{name} 必须关闭额外字段")
        properties = effective.get("properties", {})
        required = effective.get("required", [])
        expect(isinstance(required, list) and isinstance(properties, dict), "w5_control_schema_invalid", f"{name} required/properties 无效")
        exact(set(required), set(properties), "w5_control_schema_open", f"{name} required 必须覆盖全部已声明字段")
    for unit_id in WAVE_CONTRACT["W5"]["unit_ids"]:
        if not (unit_id.startswith("GATE-") or unit_id.startswith("RENDERER-")):
            continue
        output_name = _w5_control_schema_refs(unit_id)[1].rsplit("/", 1)[-1]
        definition = definitions[output_name]
        expect(isinstance(definition, dict) and set(definition) == {"allOf"}, "w5_control_schema_unit_identity_open", f"{unit_id} output 未用 allOf 冻结单元身份")
        identity_field = "gate_id" if unit_id.startswith("GATE-") else "renderer_unit_id"
        try:
            unit_const = definition["allOf"][1]["properties"][identity_field]["const"]
        except (KeyError, IndexError, TypeError) as error:
            raise AlignmentError(f"w5_control_schema_unit_identity_open：{unit_id} output 缺少 {identity_field} const") from error
        exact(unit_const, unit_id, "w5_control_schema_unit_identity_open", f"{unit_id} output 会接受其他控制单元身份")
    return schema, file_sha256(root / W5_CONTROL_RUNTIME_SCHEMA_PATH)


def _validate_w5_admission_control_entries(root: Path, admission: dict[str, Any]) -> None:
    _, schema_sha256 = _validate_w5_control_runtime_schema(root)
    entries = admission.get("control_unit_entries")
    expect(isinstance(entries, list), "w5_execution_admission_invalid", "W5 admission 缺少控制单元 entries")
    entry_by_unit = {
        item.get("unit_id"): item for item in entries if isinstance(item, dict)
    }
    exact(set(entry_by_unit), set(WAVE_CONTRACT["W5"]["unit_ids"]), "w5_execution_admission_invalid", "W5 admission 控制单元 entry 人口漂移")
    for unit_id in WAVE_CONTRACT["W5"]["unit_ids"]:
        entry = entry_by_unit[unit_id]
        exact(set(entry), {
            "unit_id", "input_schema_ref", "output_schema_ref", "schema_path",
            "schema_sha256", "handler_id", "implementation_digest",
        }, "w5_execution_admission_invalid", f"{unit_id} admission entry 字段不精确")
        input_ref, output_ref = _w5_control_schema_refs(unit_id)
        exact(entry.get("input_schema_ref"), input_ref, "w5_control_schema_binding_mismatch", f"{unit_id} input schema ref 漂移")
        exact(entry.get("output_schema_ref"), output_ref, "w5_control_schema_binding_mismatch", f"{unit_id} output schema ref 漂移")
        exact(entry.get("schema_path"), W5_CONTROL_RUNTIME_SCHEMA_PATH.as_posix(), "w5_control_schema_binding_mismatch", f"{unit_id} schema path 漂移")
        exact(entry.get("schema_sha256"), schema_sha256, "w5_control_schema_binding_mismatch", f"{unit_id} 未绑定当前 schema bytes")
        expect(isinstance(entry.get("handler_id"), str) and entry["handler_id"].startswith("python:backend.services."), "w5_execution_admission_invalid", f"{unit_id} handler 非静态 Python")
        _validate_prefixed_digest(entry.get("implementation_digest"), "w5_execution_admission_invalid", f"{unit_id} implementation digest 无效")


def _validate_prefixed_digest(value: Any, code: str, detail: str) -> str:
    expect(
        isinstance(value, str)
        and value.startswith("sha256:")
        and HEX64.fullmatch(value.removeprefix("sha256:")) is not None,
        code,
        detail,
    )
    return value


def _validate_w5_admission_event_chain(
    trace: Mapping[str, Any],
    *,
    replay_by_kind: Mapping[str, Mapping[str, Any]],
    artifact_by_kind: Mapping[str, Mapping[str, Any]],
    result_set_closure: Mapping[str, Any],
    business_call_receipt_digests: set[str],
) -> dict[str, Any]:
    """验证 A→B→动作的内容寻址顺序；最终存在性不能替代时序。"""

    chain = trace.get("admission_event_chain")
    expect(isinstance(chain, dict), "w5_admission_event_chain_missing", "缺少runtime动作现场写入的A/B admission事件链")
    exact(set(chain), {
        "schema_version", "execution_id", "investigation_id",
        "base_investigation_revision", "idempotency_key_digest",
        "registry_snapshot_digest", "events", "chain_digest",
    }, "w5_admission_event_chain_invalid", "admission事件链字段不精确")
    exact(chain.get("schema_version"), "country_outage_p2_s1_w5_admission_event_chain_v1", "w5_admission_event_chain_invalid", "admission事件链schema漂移")
    investigation_id = chain.get("investigation_id")
    expect(isinstance(investigation_id, str) and investigation_id.startswith("inv_"), "w5_admission_event_chain_invalid", "事件链investigation_id无效")
    base_revision = chain.get("base_investigation_revision")
    expect(isinstance(base_revision, int) and not isinstance(base_revision, bool) and base_revision >= 1, "w5_admission_event_chain_invalid", "事件链base investigation revision无效")
    idempotency_digest = _validate_prefixed_digest(chain.get("idempotency_key_digest"), "w5_admission_event_chain_invalid", "事件链idempotency key digest无效")
    registry_digest = _validate_prefixed_digest(chain.get("registry_snapshot_digest"), "w5_admission_event_chain_invalid", "事件链Registry摘要无效")
    plan_digest = artifact_by_kind["InvestigationPlan"]["design_artifact_object_digest"]
    expected_execution_hex = object_digest({
        "investigation_id": investigation_id,
        "base_investigation_revision": base_revision,
        "idempotency_key_digest": idempotency_digest,
        "plan_artifact_digest": plan_digest,
        "registry_snapshot_digest": registry_digest,
    })
    exact(chain.get("execution_id"), f"w5-execution-sha256:{expected_execution_hex}", "w5_admission_event_chain_invalid", "execution_id不能由调查、Plan和Registry重算")
    events = chain.get("events")
    expect(isinstance(events, list), "w5_admission_event_chain_invalid", "admission events必须为数组")
    exact([item.get("event_kind") for item in events if isinstance(item, dict)], list(W5_ADMISSION_EVENT_ORDER), "w5_admission_event_order_invalid", "A/B、dispatch/publish与final CAS顺序漂移")
    exact(len(events), len(W5_ADMISSION_EVENT_ORDER), "w5_admission_event_chain_invalid", "admission事件人口重复或缺失")
    previous_digest: str | None = None
    by_event: dict[str, Mapping[str, Any]] = {}
    for sequence, event in enumerate(events, start=1):
        expect(isinstance(event, dict), "w5_admission_event_chain_invalid", "admission event必须为对象")
        exact(set(event), {
            "schema_version", "event_kind", "execution_id", "investigation_id", "sequence",
            "previous_event_digest", "artifact_kind", "artifact_digest",
            "design_validator_receipt_digest", "runtime_admission_receipt_digest",
            "registry_snapshot_digest", "parameter_bindings_digest",
            "action", "action_subject_digest", "event_digest",
        }, "w5_admission_event_chain_invalid", f"第{sequence}个admission event字段不精确")
        kind = event["event_kind"]
        exact(event.get("schema_version"), "country_outage_p2_s1_w5_admission_event_v1", "w5_admission_event_chain_invalid", f"{kind} event schema漂移")
        exact(event.get("execution_id"), chain["execution_id"], "w5_admission_event_cross_execution", f"{kind}混入其他execution")
        exact(event.get("investigation_id"), investigation_id, "w5_admission_event_cross_investigation", f"{kind}混入其他investigation")
        exact(event.get("sequence"), sequence, "w5_admission_event_order_invalid", f"{kind} sequence不连续")
        exact(event.get("previous_event_digest"), previous_digest, "w5_admission_event_chain_broken", f"{kind} previous digest断链")
        exact(event.get("registry_snapshot_digest"), registry_digest, "w5_admission_event_registry_drift", f"{kind} Registry漂移")
        exact(event.get("action"), kind, "w5_admission_event_action_invalid", f"{kind} action漂移")
        _validate_prefixed_digest(event.get("artifact_digest"), "w5_admission_event_chain_invalid", f"{kind} artifact digest无效")
        _validate_prefixed_digest(event.get("parameter_bindings_digest"), "w5_admission_event_chain_invalid", f"{kind} parameter digest无效")
        _validate_prefixed_digest(event.get("action_subject_digest"), "w5_admission_event_action_invalid", f"{kind} action subject无效")
        event_digest = _validate_prefixed_digest(event.get("event_digest"), "w5_admission_event_chain_invalid", f"{kind} event digest无效")
        exact(event_digest, "sha256:" + object_digest(event, {"event_digest"}), "w5_admission_event_chain_broken", f"{kind} event digest不可重算")
        previous_digest = event_digest
        by_event[kind] = event
    chain_digest = _validate_prefixed_digest(chain.get("chain_digest"), "w5_admission_event_chain_invalid", "事件链摘要无效")
    exact(chain_digest, "sha256:" + object_digest(chain, {"chain_digest"}), "w5_admission_event_chain_broken", "事件链摘要不可重算")

    artifact_events = {
        "InvestigationPlan": W5_ADMISSION_EVENT_ORDER[:4],
        "ResultSet": W5_ADMISSION_EVENT_ORDER[4:8],
        "EvidenceGraph": W5_ADMISSION_EVENT_ORDER[8:13],
    }
    for artifact_kind, event_kinds in artifact_events.items():
        replay = replay_by_kind[artifact_kind]
        artifact_digest = artifact_by_kind[artifact_kind]["design_artifact_object_digest"]
        design_receipt_digest = replay["validator_receipt"]["receipt_digest"]
        runtime_receipt = replay["runtime_admission_receipt"]
        runtime_receipt_digest = runtime_receipt["receipt_digest"]
        parameter_digest = runtime_receipt["parameter_bindings_digest"]
        for event_index, event_kind in enumerate(event_kinds):
            event = by_event[event_kind]
            exact(event.get("artifact_kind"), artifact_kind, "w5_admission_event_artifact_drift", f"{event_kind} artifact kind漂移")
            exact(event.get("artifact_digest"), artifact_digest, "w5_admission_event_artifact_drift", f"{event_kind} artifact digest漂移")
            exact(event.get("parameter_bindings_digest"), parameter_digest, "w5_admission_event_parameter_drift", f"{event_kind} 参数绑定漂移")
            a_exists = event_index >= (0 if artifact_kind == "InvestigationPlan" else 1)
            b_exists = event_index >= (1 if artifact_kind == "InvestigationPlan" else 2)
            exact(event.get("design_validator_receipt_digest"), design_receipt_digest if a_exists else None, "w5_admission_event_receipt_drift", f"{event_kind} A receipt时序或绑定漂移")
            exact(event.get("runtime_admission_receipt_digest"), runtime_receipt_digest if b_exists else None, "w5_admission_event_receipt_drift", f"{event_kind} B receipt时序或绑定漂移")
        if artifact_kind == "InvestigationPlan":
            exact(by_event[event_kinds[0]]["action_subject_digest"], "sha256:" + design_receipt_digest, "w5_admission_event_action_invalid", "Plan A动作未绑定A receipt")
            exact(by_event[event_kinds[1]]["action_subject_digest"], runtime_receipt_digest, "w5_admission_event_action_invalid", "Plan B动作未绑定B receipt")
        else:
            exact(by_event[event_kinds[0]]["action_subject_digest"], artifact_digest, "w5_admission_event_action_invalid", f"{artifact_kind} build起点未绑定制品")
            exact(by_event[event_kinds[1]]["action_subject_digest"], "sha256:" + design_receipt_digest, "w5_admission_event_action_invalid", f"{artifact_kind} A动作未绑定A receipt")
            exact(by_event[event_kinds[2]]["action_subject_digest"], runtime_receipt_digest, "w5_admission_event_action_invalid", f"{artifact_kind} B动作未绑定B receipt")
    expect(by_event["first_dispatch"]["action_subject_digest"] in business_call_receipt_digests, "w5_admission_event_dispatch_unresolved", "first dispatch未绑定真实business schema call receipt")
    exact(by_event["result_set_published"]["action_subject_digest"], artifact_by_kind["ResultSet"]["runtime_object_digest"], "w5_admission_event_publish_invalid", "ResultSet publish未绑定runtime对象")
    exact(by_event["evidence_graph_published"]["action_subject_digest"], artifact_by_kind["EvidenceGraph"]["runtime_object_digest"], "w5_admission_event_publish_invalid", "EvidenceGraph publish未绑定runtime对象")
    exact(result_set_closure["result_set_id"], trace["result_set_receipt_closure"]["result_set_id"], "w5_admission_event_publish_invalid", "ResultSet事件链未绑定同一闭包")
    final = by_event["final_investigation_cas_committed"]
    exact(final.get("artifact_kind"), "InvestigationCommit", "w5_admission_event_final_cas_invalid", "final CAS artifact kind漂移")
    final_commit_digest = _validate_prefixed_digest(final.get("artifact_digest"), "w5_admission_event_final_cas_invalid", "final CAS制品摘要无效")
    exact(final.get("action_subject_digest"), final_commit_digest, "w5_admission_event_final_cas_invalid", "final CAS动作未绑定同一InvestigationCommit对象")
    exact(final.get("design_validator_receipt_digest"), None, "w5_admission_event_final_cas_invalid", "final CAS不得冒充A receipt")
    exact(final.get("runtime_admission_receipt_digest"), None, "w5_admission_event_final_cas_invalid", "final CAS不得冒充B receipt")
    return chain


def _parse_w5_execution_trace(root: Path, output: str) -> dict[str, Any]:
    lines = [
        line.removeprefix(W5_EXECUTION_TRACE_PREFIX)
        for line in output.splitlines()
        if line.startswith(W5_EXECUTION_TRACE_PREFIX)
    ]
    exact(len(lines), 1, "w5_execution_trace_missing", "w5-python 必须恰好输出一条正式 execution trace")
    try:
        trace = json.loads(
            lines[0],
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise AlignmentError(f"w5_execution_trace_invalid：execution trace 不是严格 JSON：{error}") from error
    expect(isinstance(trace, dict), "w5_execution_trace_invalid", "execution trace 必须是对象")
    exact(set(trace), {
        "schema_version", "trace_source", "execution_admission_receipt_digest",
        "invoked_control_unit_ids", "control_unit_call_counts", "plan_node_unit_ids",
        "control_unit_execution_records",
        "schema_validated_control_unit_ids", "schema_validation_failure_count",
        "persisted_artifact_schema_bindings", "runtime_artifact_schema_validation_failure_count",
        "design_semantic_validator_replays", "result_set_receipt_closure",
        "plan_admission_validator", "admission_event_chain",
        "business_unit_invocation_ids", "schema_validated_business_unit_ids",
        "business_unit_execution_records",
        "business_unit_schema_validation_failure_count",
        "monetary_limit_mode", "max_cost_amount_zero_present",
        "cas_crash_recovery_replayed_same_outcome", "planning_grounding_port",
        "dynamic_fanout_count", "arbitrary_callback_count", "p2_1_unit_ids",
        "external_model_call_count",
        "result_set_committed", "evidence_graph_committed", "export_committed",
        "cas_conflict_rejected", "running_cancel_verified", "local_fixture_only", "production_deployed",
        "trace_digest",
    }, "w5_execution_trace_invalid", "execution trace 字段不精确")
    exact(trace.get("schema_version"), "country_outage_p2_s1_w5_execution_trace_v1", "w5_execution_trace_invalid", "execution trace schema 漂移")
    exact(trace.get("trace_source"), "runtime_execution_spy_and_content_addressed_store", "w5_execution_trace_invalid", "execution trace 不是 runtime spy/store 实际记录")
    digest = require_hex64(trace.get("trace_digest"), "w5_execution_trace_invalid", "execution trace digest 无效")
    exact(digest, object_digest(trace, {"trace_digest"}), "w5_execution_trace_invalid", "execution trace digest 不可重算")
    invoked = trace.get("invoked_control_unit_ids")
    expect(isinstance(invoked, list) and len(invoked) == len(set(invoked)), "w5_execution_trace_invalid", "control unit 调用人口无效")
    exact(set(invoked), set(WAVE_CONTRACT["W5"]["unit_ids"]), "w5_control_execution_population_incomplete", "PLAN/GATE/BOUNDARY/Renderer/Delivery 未全部由真实 runtime 调用")
    counts = trace.get("control_unit_call_counts")
    expect(isinstance(counts, dict), "w5_execution_trace_invalid", "control unit call counts 缺失")
    exact(set(counts), set(WAVE_CONTRACT["W5"]["unit_ids"]), "w5_control_execution_population_incomplete", "control unit count 人口不精确")
    for unit_id, count in counts.items():
        expect(isinstance(count, int) and not isinstance(count, bool) and count > 0, "w5_control_execution_population_incomplete", f"{unit_id} 没有真实调用")
    control_records = trace.get("control_unit_execution_records")
    expect(isinstance(control_records, list), "w5_control_execution_records_missing", "缺少控制单元逐次执行记录")
    record_population: dict[str, int] = {unit_id: 0 for unit_id in WAVE_CONTRACT["W5"]["unit_ids"]}
    control_receipt_digests: set[str] = set()
    for record in control_records:
        expect(isinstance(record, dict) and set(record) == {
            "unit_id", "input_digest", "output_digest", "handler_id",
            "implementation_digest", "input_schema_ref", "output_schema_ref",
            "input_schema_valid", "output_schema_valid", "execution_disposition",
            "call_receipt_digest",
        }, "w5_control_execution_record_invalid", "控制单元执行记录字段不精确")
        unit_id = record.get("unit_id")
        expect(unit_id in record_population, "w5_control_execution_record_invalid", "控制单元执行记录混入未登记单元")
        for field in ("input_digest", "output_digest"):
            _validate_prefixed_digest(record.get(field), "w5_control_execution_record_invalid", f"{unit_id} {field}无效")
        expect(isinstance(record.get("handler_id"), str) and record["handler_id"].startswith("python:backend.services."), "w5_control_execution_record_invalid", f"{unit_id} handler id无效")
        _validate_prefixed_digest(record.get("implementation_digest"), "w5_control_execution_record_invalid", f"{unit_id} implementation digest无效")
        exact(record.get("input_schema_ref"), _w5_control_schema_refs(unit_id)[0], "w5_control_execution_record_invalid", f"{unit_id} input Schema ref漂移")
        exact(record.get("output_schema_ref"), _w5_control_schema_refs(unit_id)[1], "w5_control_execution_record_invalid", f"{unit_id} output Schema ref漂移")
        exact(record.get("input_schema_valid"), True, "w5_control_schema_runtime_validation_failed", f"{unit_id} input未通过runtime Schema")
        exact(record.get("output_schema_valid"), True, "w5_control_schema_runtime_validation_failed", f"{unit_id} output未通过runtime Schema")
        exact(record.get("execution_disposition"), "completed", "w5_control_execution_record_invalid", f"{unit_id} 未完成")
        receipt_digest = _validate_prefixed_digest(record.get("call_receipt_digest"), "w5_control_execution_record_invalid", f"{unit_id} call receipt digest无效")
        exact(receipt_digest, "sha256:" + object_digest(record, {"call_receipt_digest"}), "w5_control_execution_record_invalid", f"{unit_id} call receipt digest无法重算")
        expect(receipt_digest not in control_receipt_digests, "w5_control_execution_record_invalid", "控制单元call receipt重复")
        control_receipt_digests.add(receipt_digest)
        record_population[unit_id] += 1
    exact(record_population, counts, "w5_control_execution_population_incomplete", "逐次控制记录人口与call counts不一致")
    validated_units = trace.get("schema_validated_control_unit_ids")
    expect(isinstance(validated_units, list) and len(validated_units) == len(set(validated_units)), "w5_control_schema_runtime_validation_missing", "schema validated control unit 人口无效")
    exact(set(validated_units), set(WAVE_CONTRACT["W5"]["unit_ids"]), "w5_control_schema_runtime_validation_missing", "Host owner 未按 Schema 验证全部11个控制单元输入输出")
    exact(trace.get("schema_validation_failure_count"), 0, "w5_control_schema_runtime_validation_failed", "控制单元 runtime Schema 验证存在失败")

    artifact_bindings = trace.get("persisted_artifact_schema_bindings")
    expect(isinstance(artifact_bindings, list), "w5_runtime_artifact_schema_evidence_missing", "缺少持久化 Plan/ResultSet/EvidenceGraph Schema 证据")
    by_kind = {
        item.get("artifact_kind"): item for item in artifact_bindings if isinstance(item, dict)
    }
    exact(set(by_kind), set(W5_PERSISTED_ARTIFACT_SCHEMAS), "w5_runtime_artifact_schema_evidence_missing", "持久化制品 Schema 证据人口不精确")
    exact(len(artifact_bindings), 3, "w5_runtime_artifact_schema_evidence_missing", "持久化制品 Schema 证据重复")
    for kind, schema_path in W5_PERSISTED_ARTIFACT_SCHEMAS.items():
        binding = by_kind[kind]
        exact(set(binding), {
            "artifact_kind", "validation_mode", "frozen_schema_path",
            "frozen_schema_sha256", "design_artifact_object_digest",
            "runtime_object_digest", "runtime_envelope_object_digest",
        }, "w5_runtime_artifact_schema_evidence_invalid", f"{kind} Schema binding 字段不精确")
        expect(binding.get("validation_mode") in {"frozen_schema_valid", "design_envelope_bound"}, "w5_runtime_artifact_schema_evidence_invalid", f"{kind} validation mode 无效")
        exact(binding.get("frozen_schema_path"), schema_path, "w5_runtime_artifact_schema_evidence_invalid", f"{kind} 未绑定冻结 Schema 路径")
        exact(binding.get("frozen_schema_sha256"), file_sha256(root / schema_path), "w5_runtime_artifact_schema_evidence_invalid", f"{kind} 未绑定冻结 Schema 字节")
        _validate_prefixed_digest(binding.get("design_artifact_object_digest"), "w5_runtime_artifact_schema_evidence_invalid", f"{kind} design artifact digest 无效")
        _validate_prefixed_digest(binding.get("runtime_object_digest"), "w5_runtime_artifact_schema_evidence_invalid", f"{kind} runtime object digest 无效")
        envelope = binding.get("runtime_envelope_object_digest")
        if binding.get("validation_mode") == "design_envelope_bound":
            _validate_prefixed_digest(envelope, "w5_runtime_artifact_schema_evidence_invalid", f"{kind} runtime envelope digest 无效")
        else:
            exact(envelope, None, "w5_runtime_artifact_schema_evidence_invalid", f"{kind} 直接合法时不得伪报 envelope")
    exact(trace.get("runtime_artifact_schema_validation_failure_count"), 0, "w5_runtime_artifact_schema_validation_failed", "持久化制品 Schema 验证存在失败")

    semantic_replays = trace.get("design_semantic_validator_replays")
    expect(isinstance(semantic_replays, list), "w5_design_semantic_replay_missing", "缺少 Plan/ResultSet/EvidenceGraph 完整设计语义回放")
    replay_by_kind = {
        item.get("artifact_kind"): item for item in semantic_replays if isinstance(item, dict)
    }
    exact(len(semantic_replays), 3, "w5_design_semantic_replay_invalid", "设计语义回放人口重复或缺失")
    exact(set(replay_by_kind), set(W5_DESIGN_SEMANTIC_VALIDATORS), "w5_design_semantic_replay_missing", "设计语义回放人口不精确")
    for kind, (validator_id, implementation_path, validator_entrypoints) in W5_DESIGN_SEMANTIC_VALIDATORS.items():
        replay = replay_by_kind[kind]
        exact(set(replay), {
            "artifact_kind", "artifact_digest", "schema_path", "schema_sha256",
            "validator_id", "validator_version", "validator_contract_digest",
            "validator_implementation_digest", "trusted_store_resolved",
            "draft_schema_error_count", "semantic_error_count", "replay_disposition",
            "validator_receipt", "runtime_admission_receipt",
        }, "w5_design_semantic_replay_invalid", f"{kind} 语义回放字段不精确")
        schema_path = W5_PERSISTED_ARTIFACT_SCHEMAS[kind]
        exact(replay.get("artifact_digest"), by_kind[kind]["design_artifact_object_digest"], "w5_design_semantic_replay_invalid", f"{kind} 回放未绑定同一设计制品")
        exact(replay.get("schema_path"), schema_path, "w5_design_semantic_replay_invalid", f"{kind} 回放 Schema 路径漂移")
        schema_sha = file_sha256(root / schema_path)
        exact(replay.get("schema_sha256"), schema_sha, "w5_design_semantic_replay_invalid", f"{kind} 回放未绑定 Schema 字节")
        exact(replay.get("validator_id"), validator_id, "w5_design_semantic_replay_invalid", f"{kind} validator id 漂移")
        exact(replay.get("validator_version"), "1.0.0", "w5_design_semantic_replay_invalid", f"{kind} validator version 漂移")
        exact(replay.get("validator_contract_digest"), "sha256:" + schema_sha, "w5_design_semantic_replay_invalid", f"{kind} validator 合同摘要漂移")
        exact(replay.get("validator_implementation_digest"), "sha256:" + file_sha256(root / implementation_path), "w5_design_semantic_replay_invalid", f"{kind} validator 实现摘要漂移")
        exact(replay.get("trusted_store_resolved"), True, "w5_design_semantic_replay_invalid", f"{kind} 回放未从 trusted store 解析制品")
        exact(replay.get("draft_schema_error_count"), 0, "w5_design_semantic_replay_failed", f"{kind} Draft Schema 回放失败")
        exact(replay.get("semantic_error_count"), 0, "w5_design_semantic_replay_failed", f"{kind} 完整设计语义回放失败")
        exact(replay.get("replay_disposition"), "passed", "w5_design_semantic_replay_failed", f"{kind} 回放未通过")
        validator_receipt = replay.get("validator_receipt")
        expect(isinstance(validator_receipt, dict), "w5_design_semantic_validator_receipt_missing", f"{kind} 缺少实际 validator receipt")
        exact(set(validator_receipt), {
            "schema_version", "artifact_kind", "artifact_digest", "validator_id",
            "validator_version", "validator_entrypoints", "validator_contract_digest",
            "validator_implementation_digests", "trusted_store_snapshot_digest",
            "draft_schema_error_codes", "semantic_error_codes", "disposition",
            "receipt_digest",
        }, "w5_design_semantic_validator_receipt_invalid", f"{kind} validator receipt 字段不精确")
        exact(validator_receipt.get("schema_version"), "country_outage_p2_s1_w5_design_semantic_validator_receipt_v1", "w5_design_semantic_validator_receipt_invalid", f"{kind} validator receipt schema 漂移")
        for field in ("artifact_kind", "artifact_digest", "validator_id", "validator_version", "validator_contract_digest"):
            exact(validator_receipt.get(field), replay[field], "w5_design_semantic_validator_receipt_invalid", f"{kind} validator receipt 未绑定 {field}")
        exact(validator_receipt.get("validator_entrypoints"), list(validator_entrypoints), "w5_design_semantic_validator_entrypoint_invalid", f"{kind} 未调用冻结设计完整 validator 入口")
        exact(validator_receipt.get("validator_implementation_digests"), {
            entrypoint: "sha256:" + file_sha256(root / implementation_path)
            for entrypoint in validator_entrypoints
        }, "w5_design_semantic_validator_receipt_invalid", f"{kind} validator receipt 未绑定实现字节")
        _validate_prefixed_digest(validator_receipt.get("trusted_store_snapshot_digest"), "w5_design_semantic_validator_receipt_invalid", f"{kind} trusted store snapshot digest 无效")
        exact(validator_receipt.get("draft_schema_error_codes"), [], "w5_design_semantic_replay_failed", f"{kind} Draft Schema validator 返回错误码")
        exact(validator_receipt.get("semantic_error_codes"), [], "w5_design_semantic_replay_failed", f"{kind} 完整设计 validator 返回错误码")
        exact(validator_receipt.get("disposition"), "passed", "w5_design_semantic_replay_failed", f"{kind} validator receipt 未通过")
        validator_receipt_digest = require_hex64(validator_receipt.get("receipt_digest"), "w5_design_semantic_validator_receipt_invalid", f"{kind} validator receipt digest 无效")
        exact(validator_receipt_digest, object_digest(validator_receipt, {"receipt_digest"}), "w5_design_semantic_validator_receipt_invalid", f"{kind} validator receipt digest 无法重算")
        runtime_receipt = replay.get("runtime_admission_receipt")
        expect(isinstance(runtime_receipt, dict), "w5_runtime_artifact_admission_missing", f"{kind} 缺少独立runtime准入回执")
        exact(set(runtime_receipt), {
            "schema_version", "artifact_kind", "design_artifact_digest",
            "runtime_subject_digest", "frozen_design_validator_receipt_digest",
            "runtime_receipt_kind", "validator_id", "validator_version",
            "validator_contract_digest", "validator_implementation_digest",
            "registry_snapshot_digest", "parameter_bindings_digest",
            "trusted_store_snapshot_digest", "trusted_store_resolved",
            "authorizes_dispatcher_execution", "disposition", "receipt_digest",
        }, "w5_runtime_artifact_admission_invalid", f"{kind} runtime准入字段不精确")
        exact(runtime_receipt.get("schema_version"), "country_outage_p2_s1_w5_runtime_artifact_admission_receipt_v1", "w5_runtime_artifact_admission_invalid", f"{kind} runtime准入schema漂移")
        exact(runtime_receipt.get("artifact_kind"), kind, "w5_runtime_artifact_admission_invalid", f"{kind} runtime准入artifact kind漂移")
        exact(runtime_receipt.get("design_artifact_digest"), replay["artifact_digest"], "w5_runtime_artifact_admission_invalid", f"{kind} runtime准入未绑定design artifact")
        exact(runtime_receipt.get("runtime_subject_digest"), by_kind[kind]["runtime_object_digest"], "w5_runtime_artifact_admission_invalid", f"{kind} runtime准入未绑定实际runtime对象")
        exact(runtime_receipt.get("frozen_design_validator_receipt_digest"), validator_receipt_digest, "w5_runtime_artifact_admission_invalid", f"{kind} runtime准入未绑定A层冻结设计validator回执")
        receipt_kind, runtime_validator_id, runtime_implementation_path, authorizes_dispatcher = W5_RUNTIME_ARTIFACT_VALIDATORS[kind]
        exact(runtime_receipt.get("runtime_receipt_kind"), receipt_kind, "w5_runtime_artifact_admission_invalid", f"{kind} runtime receipt kind漂移")
        exact(runtime_receipt.get("validator_id"), runtime_validator_id, "w5_runtime_artifact_admission_invalid", f"{kind} runtime validator id漂移")
        exact(runtime_receipt.get("validator_version"), "1.0.0", "w5_runtime_artifact_admission_invalid", f"{kind} runtime validator version漂移")
        exact(runtime_receipt.get("validator_contract_digest"), "sha256:" + schema_sha, "w5_runtime_artifact_admission_placeholder_masquerade", f"{kind} runtime validator不得使用冻结design占位contract digest")
        exact(runtime_receipt.get("validator_implementation_digest"), "sha256:" + file_sha256(root / runtime_implementation_path), "w5_runtime_artifact_admission_placeholder_masquerade", f"{kind} runtime validator不得使用冻结design占位implementation digest")
        for digest_field in ("registry_snapshot_digest", "parameter_bindings_digest", "trusted_store_snapshot_digest"):
            _validate_prefixed_digest(runtime_receipt.get(digest_field), "w5_runtime_artifact_admission_invalid", f"{kind} {digest_field}无效")
        exact(runtime_receipt.get("trusted_store_resolved"), True, "w5_runtime_artifact_admission_invalid", f"{kind} runtime对象未从trusted store解析")
        exact(runtime_receipt.get("authorizes_dispatcher_execution"), authorizes_dispatcher, "w5_runtime_artifact_admission_invalid", f"{kind} Dispatcher授权边界漂移")
        exact(runtime_receipt.get("disposition"), "passed", "w5_runtime_artifact_admission_invalid", f"{kind} runtime准入未通过")
        runtime_receipt_digest = _validate_prefixed_digest(runtime_receipt.get("receipt_digest"), "w5_runtime_artifact_admission_invalid", f"{kind} runtime准入digest无效")
        exact(runtime_receipt_digest, "sha256:" + object_digest(runtime_receipt, {"receipt_digest"}), "w5_runtime_artifact_admission_invalid", f"{kind} runtime准入digest无法重算")

    closure = trace.get("result_set_receipt_closure")
    expect(isinstance(closure, dict), "w5_result_set_receipt_closure_missing", "缺少 ResultSet query/page/freeze 回执闭包")
    closure_identity_fields = {
        "result_set_id", "result_set_revision", "manifest_digest", "content_digest",
        "returned_count", "total_count", "set_completeness", "source_population_id",
        "source_population_schema_digest", "source_dataset_digest",
    }
    exact(set(closure), closure_identity_fields | {"query_receipt", "page_receipts", "freeze_receipt"}, "w5_result_set_receipt_closure_invalid", "ResultSet 回执闭包字段不精确")
    expect(isinstance(closure.get("result_set_id"), str) and closure["result_set_id"].startswith("result-set-sha256:"), "w5_result_set_receipt_closure_invalid", "ResultSet id 无效")
    expect(isinstance(closure.get("result_set_revision"), int) and not isinstance(closure["result_set_revision"], bool) and closure["result_set_revision"] >= 1, "w5_result_set_receipt_closure_invalid", "ResultSet revision 无效")
    for field in ("manifest_digest", "content_digest", "source_population_schema_digest", "source_dataset_digest"):
        require_hex64(closure.get(field), "w5_result_set_receipt_closure_invalid", f"ResultSet {field} 无效")
    expect(isinstance(closure.get("source_population_id"), str) and bool(closure["source_population_id"]), "w5_result_set_receipt_closure_invalid", "ResultSet population id 无效")
    expect(isinstance(closure.get("returned_count"), int) and not isinstance(closure["returned_count"], bool) and closure["returned_count"] >= 0, "w5_result_set_receipt_closure_invalid", "ResultSet returned_count 无效")
    exact(closure.get("total_count"), closure["returned_count"], "w5_result_set_receipt_closure_invalid", "complete ResultSet returned/total 未闭合")
    exact(closure.get("set_completeness"), "complete", "w5_result_set_receipt_closure_invalid", "W5 导出必须消费 complete ResultSet")

    def validate_embedded_receipt(receipt: Any, expected_kind: str, expected_fields: set[str]) -> dict[str, Any]:
        expect(isinstance(receipt, dict), "w5_result_set_receipt_closure_invalid", f"{expected_kind} receipt 缺失")
        exact(set(receipt), expected_fields, "w5_result_set_receipt_closure_invalid", f"{expected_kind} receipt 字段不精确")
        exact(receipt.get("receipt_kind"), expected_kind, "w5_result_set_receipt_kind_invalid", f"{expected_kind} receipt kind 漂移")
        receipt_digest = require_hex64(receipt.get("receipt_digest"), "w5_result_set_receipt_closure_invalid", f"{expected_kind} receipt digest 无效")
        exact(receipt_digest, object_digest(receipt, {"receipt_digest"}), "w5_result_set_receipt_closure_invalid", f"{expected_kind} receipt digest 无法重算")
        exact(receipt.get("disposition"), "passed", "w5_result_set_receipt_closure_invalid", f"{expected_kind} receipt 未通过")
        return receipt

    common_source_fields = {
        "identity_digest", "query_digest", "source_population_id",
        "source_population_schema_digest", "source_dataset_digest",
    }
    raw_query = closure.get("query_receipt")
    if isinstance(raw_query, dict):
        expect(raw_query.get("receipt_digest") != raw_query.get("atomic_tool_query_receipt_digest"), "w5_result_set_receipt_reused", "Host query receipt 不得复用 Tool query receipt")
    query = validate_embedded_receipt(
        raw_query, "query",
        {"receipt_kind", "receipt_digest", "tool_run_id", "total_count", "disposition", "atomic_tool_query_receipt_digest"} | common_source_fields,
    )
    expect(isinstance(query.get("tool_run_id"), str) and bool(query["tool_run_id"]), "w5_result_set_receipt_closure_invalid", "query receipt tool_run_id无效")
    for field in ("source_population_id", "source_population_schema_digest", "source_dataset_digest", "total_count"):
        exact(query.get(field), closure[field], "w5_result_set_receipt_binding_invalid", f"query receipt 未绑定 {field}")
    atomic_query_digest = require_hex64(query.get("atomic_tool_query_receipt_digest"), "w5_result_set_receipt_closure_invalid", "Tool query receipt digest 无效")
    expect(query["receipt_digest"] != atomic_query_digest, "w5_result_set_receipt_reused", "Host query receipt 不得复用 Tool query receipt")
    for field in ("identity_digest", "query_digest"):
        require_hex64(query.get(field), "w5_result_set_receipt_closure_invalid", f"query {field} 无效")

    pages = closure.get("page_receipts")
    expect(isinstance(pages, list) and pages, "w5_result_set_receipt_closure_invalid", "非空 complete ResultSet 必须有 page receipts")
    receipt_digests = {query["receipt_digest"]}
    page_member_total = 0
    for index, raw_page in enumerate(pages):
        if isinstance(raw_page, dict):
            expect(raw_page.get("receipt_digest") != raw_page.get("atomic_tool_query_receipt_digest"), "w5_result_set_receipt_reused", "Host page receipt 不得复用 Tool query receipt")
        page = validate_embedded_receipt(
            raw_page, "page",
            {"receipt_kind", "receipt_digest", "page_index", "page_content_digest", "member_count", "disposition", "atomic_tool_query_receipt_digest"} | common_source_fields,
        )
        exact(page.get("page_index"), index, "w5_result_set_receipt_binding_invalid", "page receipt 必须从0连续")
        expect(isinstance(page.get("member_count"), int) and not isinstance(page["member_count"], bool) and page["member_count"] >= 0, "w5_result_set_receipt_closure_invalid", "page member_count 无效")
        page_member_total += page["member_count"]
        for field in ("source_population_id", "source_population_schema_digest", "source_dataset_digest"):
            exact(page.get(field), closure[field], "w5_result_set_receipt_binding_invalid", f"page receipt 未绑定 {field}")
        for field in ("identity_digest", "query_digest"):
            exact(page.get(field), query[field], "w5_result_set_receipt_binding_invalid", f"page receipt {field} 漂移")
        require_hex64(page.get("page_content_digest"), "w5_result_set_receipt_closure_invalid", "page content digest 无效")
        atomic_page_digest = require_hex64(page.get("atomic_tool_query_receipt_digest"), "w5_result_set_receipt_closure_invalid", "page Tool receipt digest 无效")
        expect(page["receipt_digest"] != atomic_page_digest, "w5_result_set_receipt_reused", "Host page receipt 不得复用 Tool query receipt")
        expect(page["receipt_digest"] not in receipt_digests, "w5_result_set_receipt_reused", "query/page/freeze receipt 摘要必须唯一")
        receipt_digests.add(page["receipt_digest"])
    exact(page_member_total, closure["returned_count"], "w5_result_set_receipt_binding_invalid", "page member_count 未闭合 returned_count")

    freeze = validate_embedded_receipt(
        closure.get("freeze_receipt"), "freeze",
        {"receipt_kind", "receipt_digest", "disposition"} | closure_identity_fields,
    )
    for field in closure_identity_fields:
        exact(freeze.get(field), closure[field], "w5_result_set_receipt_binding_invalid", f"freeze receipt 未绑定 {field}")
    expect(freeze["receipt_digest"] not in receipt_digests, "w5_result_set_receipt_reused", "freeze receipt 不得复用 query/page receipt")

    plan_admission = trace.get("plan_admission_validator")
    expect(isinstance(plan_admission, dict), "w5_plan_admission_evidence_missing", "缺少 PlanAdmission validator 证据")
    exact(set(plan_admission), {
        "receipt_digest", "validator_id", "validator_version",
        "validator_contract_digest", "validator_implementation_digest",
        "trusted_store_resolved",
    }, "w5_plan_admission_evidence_invalid", "PlanAdmission validator 字段不精确")
    _validate_prefixed_digest(plan_admission.get("receipt_digest"), "w5_plan_admission_evidence_invalid", "PlanAdmission receipt digest 无效")
    exact(plan_admission.get("validator_id"), "country_outage_p2_s1_w5_host_plan_admission_validator", "w5_plan_admission_evidence_invalid", "PlanAdmission validator id 漂移")
    exact(plan_admission.get("validator_version"), "1.0.0", "w5_plan_admission_evidence_invalid", "PlanAdmission validator version 漂移")
    exact(plan_admission.get("validator_contract_digest"), "sha256:" + file_sha256(root / W5_PERSISTED_ARTIFACT_SCHEMAS["InvestigationPlan"]), "w5_plan_admission_evidence_invalid", "PlanAdmission 未绑定冻结合同")
    exact(plan_admission.get("validator_implementation_digest"), "sha256:" + file_sha256(root / "backend/services/country_outage_p2_s1_investigation_runtime.py"), "w5_plan_admission_evidence_invalid", "PlanAdmission 未绑定当前 Host validator 实现")
    exact(plan_admission.get("trusted_store_resolved"), True, "w5_plan_admission_evidence_invalid", "PlanAdmission receipt 未从 trusted store 解析")
    runtime_plan_admission = replay_by_kind["InvestigationPlan"]["runtime_admission_receipt"]
    for trace_field, runtime_field in (
        ("receipt_digest", "receipt_digest"),
        ("validator_id", "validator_id"),
        ("validator_version", "validator_version"),
        ("validator_contract_digest", "validator_contract_digest"),
        ("validator_implementation_digest", "validator_implementation_digest"),
        ("trusted_store_resolved", "trusted_store_resolved"),
    ):
        exact(plan_admission.get(trace_field), runtime_plan_admission[runtime_field], "w5_plan_admission_evidence_invalid", f"PlanAdmission 与B层runtime准入 {trace_field} 漂移")

    business_invoked = trace.get("business_unit_invocation_ids")
    business_validated = trace.get("schema_validated_business_unit_ids")
    expect(isinstance(business_invoked, list) and business_invoked and len(business_invoked) == len(set(business_invoked)), "w5_business_schema_runtime_validation_missing", "Tool/Operator 实际调用人口无效")
    exact(business_validated, business_invoked, "w5_business_schema_runtime_validation_missing", "Dispatcher 未对全部实际Tool/Operator输入输出按Registry Schema验证")
    business_population = set().union(*(set(WAVE_CONTRACT[stage]["unit_ids"]) for stage in ("W1", "W2", "W3", "W4")))
    expect(set(business_invoked) <= business_population, "w5_business_schema_runtime_validation_invalid", "business trace 混入控制单元或未登记单元")
    expect(W5_REQUIRED_CORE_BUSINESS_UNIT_IDS <= set(business_invoked), "w5_core_business_journey_missing", "runtime trace 未覆盖 TOOL-07 事件全景、TOOL-11 时间点下钻与 OP-29..33/37 证据一致性三项核心旅程")
    exact(trace.get("business_unit_schema_validation_failure_count"), 0, "w5_business_schema_runtime_validation_failed", "Tool/Operator runtime Schema 验证存在失败")
    business_records = trace.get("business_unit_execution_records")
    expect(isinstance(business_records, list), "w5_business_execution_records_missing", "缺少Tool/Operator逐单元执行记录")
    business_by_unit = {
        item.get("unit_id"): item for item in business_records if isinstance(item, dict)
    }
    exact(len(business_records), len(business_by_unit), "w5_business_execution_record_invalid", "Tool/Operator执行记录重复")
    exact(set(business_by_unit), set(business_invoked), "w5_business_execution_records_missing", "Tool/Operator执行记录人口与调用人口不一致")
    all_business_call_receipts: set[str] = set()
    for unit_id, record in business_by_unit.items():
        exact(set(record), {
            "unit_id", "invocation_count", "schema_validation_count",
            "schema_validation_failure_count", "registry_snapshot_digest",
            "input_schema_ref", "output_schema_ref", "input_schema_digest",
            "output_schema_digest", "handler_id", "implementation_digest",
            "call_receipt_digests",
        }, "w5_business_execution_record_invalid", f"{unit_id} execution record字段不精确")
        invocation_count = record.get("invocation_count")
        expect(isinstance(invocation_count, int) and not isinstance(invocation_count, bool) and invocation_count > 0, "w5_business_execution_record_invalid", f"{unit_id} invocation count无效")
        exact(record.get("schema_validation_count"), invocation_count * 2, "w5_business_schema_runtime_validation_missing", f"{unit_id} 未逐次验证输入输出Schema")
        exact(record.get("schema_validation_failure_count"), 0, "w5_business_schema_runtime_validation_failed", f"{unit_id} runtime Schema验证失败")
        _validate_prefixed_digest(record.get("registry_snapshot_digest"), "w5_business_execution_record_invalid", f"{unit_id} Registry snapshot digest无效")
        for field in ("input_schema_ref", "output_schema_ref", "handler_id"):
            expect(isinstance(record.get(field), str) and bool(record[field]), "w5_business_execution_record_invalid", f"{unit_id} {field}无效")
        for field in ("input_schema_digest", "output_schema_digest", "implementation_digest"):
            _validate_prefixed_digest(record.get(field), "w5_business_execution_record_invalid", f"{unit_id} {field}无效")
        call_receipts = record.get("call_receipt_digests")
        expect(isinstance(call_receipts, list) and len(call_receipts) == invocation_count and len(call_receipts) == len(set(call_receipts)), "w5_business_execution_record_invalid", f"{unit_id} call receipt人口不闭合")
        for receipt_digest in call_receipts:
            _validate_prefixed_digest(receipt_digest, "w5_business_execution_record_invalid", f"{unit_id} call receipt digest无效")
            expect(receipt_digest not in all_business_call_receipts, "w5_business_execution_record_invalid", "跨Tool/Operator call receipt重复")
            all_business_call_receipts.add(receipt_digest)

    _validate_w5_admission_event_chain(
        trace,
        replay_by_kind=replay_by_kind,
        artifact_by_kind=by_kind,
        result_set_closure=closure,
        business_call_receipt_digests=all_business_call_receipts,
    )

    exact(trace.get("monetary_limit_mode"), "unlimited", "w5_monetary_limit_mode_invalid", "W5 必须保持 monetary_limit_mode=unlimited")
    exact(trace.get("max_cost_amount_zero_present"), False, "w5_monetary_limit_zero_masquerade", "Plan 不得用 max_cost_amount=0 冒充 unlimited")
    exact(trace.get("cas_crash_recovery_replayed_same_outcome"), True, "w5_cas_crash_recovery_missing", "未证明 CAS crash 后重建 Store 复放同一 outcome")
    exact(trace.get("planning_grounding_port"), {
        "port_kind": "trusted_fixture_sol_planning_host_grounding",
        "request_plan_nodes_rejected": True,
        "constructor_plan_nodes_supported": False,
        "grounded_plan_committed": True,
        "grounded_execution_recipe_schema_version": "country_outage_p2_s1_w5_grounded_execution_recipe_v1",
        "recipe_digest_verified": True,
        "projection_recipe_digest_verified": True,
        "host_grounding_recipe_digest_verified": True,
    }, "w5_trusted_planning_grounding_missing", "create 未通过受信 planning/grounding port 或仍接受裸 plan_nodes")
    plan_units = trace.get("plan_node_unit_ids")
    expect(isinstance(plan_units, list) and len(plan_units) == len(set(plan_units)), "w5_execution_trace_invalid", "plan node unit 人口无效")
    expect(set(plan_units) <= set(W5_EXECUTION_ALLOWED_UNIT_IDS), "w5_dynamic_fanout_detected", "plan 混入未准入单元")
    expect(set(business_invoked) <= set(plan_units), "w5_core_business_journey_missing", "business unit trace 未绑定实际Plan node人口")
    exact(trace.get("dynamic_fanout_count"), 0, "w5_dynamic_fanout_detected", "runtime 检测到动态 fan-out")
    exact(trace.get("arbitrary_callback_count"), 0, "w5_dispatcher_callback_seam_open", "runtime 检测到任意 callback")
    exact(trace.get("p2_1_unit_ids"), [], "p2_1_unit_smuggled", "runtime trace 混入 P2.1")
    exact(trace.get("external_model_call_count"), 0, "w5_external_model_overclaim", "runtime trace 检测到外部模型调用")
    for field in (
        "result_set_committed", "evidence_graph_committed", "export_committed",
        "cas_conflict_rejected", "running_cancel_verified", "local_fixture_only",
    ):
        exact(trace.get(field), True, "w5_execution_trace_incomplete", f"execution trace 未证明 {field}")
    exact(trace.get("production_deployed"), False, "wave_deployment_overclaim", "execution trace 不得声称生产部署")
    return trace


def validate_w5_evidence(root: Path, evidence: dict[str, Any]) -> list[str]:
    """验收本地隔离组合 runtime；不把 W5 升级为模型、性能或生产结论。"""

    expected_fields = {
        "schema_version", "stage", "status", "design_candidate_id",
        "baseline_content_digest", "implementation_candidate_id",
        "implementation_semantic_digest", "content_digest", "effect",
        "effect_verified", "implemented_unit_ids", "atomic_split_tests_passed",
        "p2_1_units_included", "production_deployed", "artifact_manifest",
        "test_receipts", "execution_admission_receipt", "dispatcher_probe",
        "runtime_capability_evidence", "model_chain_evidence",
        "atomicity_evidence", "acceptance_scope", "prior_stage_receipt_digests",
    }
    exact(set(evidence), expected_fields, "w5_evidence_fields_invalid", "W5 evidence 字段人口不精确")
    semantic = require_hex64(
        evidence.get("implementation_semantic_digest"),
        "w5_candidate_digest_invalid",
        "W5 implementation semantic digest 无效",
    )
    exact(
        semantic,
        object_digest(
            evidence,
            {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
        ),
        "w5_candidate_digest_mismatch",
        "W5 implementation semantic digest 不可重算",
    )
    exact(
        evidence.get("implementation_candidate_id"),
        f"country-outage-p2-s1-w5-{semantic[:24]}",
        "w5_candidate_identity_mismatch",
        "W5 candidate ID 不可由语义摘要重算",
    )
    content_digest = require_hex64(evidence.get("content_digest"), "w5_content_digest_invalid", "W5 content digest 无效")
    exact(content_digest, object_digest(evidence, {"content_digest"}), "w5_content_digest_mismatch", "W5 content digest 不可重算")

    _validate_w5_artifact_manifest(root, evidence.get("artifact_manifest"))
    _validate_w5_planning_grounding_source(root)

    references = evidence.get("test_receipts")
    expect(isinstance(references, list), "wave_test_receipts_missing", "W5 缺少真实 runner 回执")
    exact(len(references), len(W5_TEST_SUITE_IDS), "wave_test_receipts_missing", "W5 runner 回执数量漂移")
    reference_by_suite = {
        item.get("suite_id"): item for item in references if isinstance(item, dict)
    }
    exact(set(reference_by_suite), set(W5_TEST_SUITE_IDS), "wave_test_receipts_missing", "W5 runner suite 人口漂移")
    receipts = {
        suite_id: _validate_stage_test_run_receipt(root, reference_by_suite[suite_id], suite_id)
        for suite_id in W5_TEST_SUITE_IDS
    }
    for suite_id in W5_TEST_SUITE_IDS:
        exact(receipts[suite_id]["tested_execution_unit_ids"], [], "w5_test_coverage_overclaim", f"{suite_id} 不得冒充 Registry 单元执行覆盖")
    output_markers = {
        "w5-python": ("test_country_outage_p2_s1_runtime", "test_country_outage_p2_s1_investigation_api", "OK"),
        "w5-openapi": ("8 passed", "OpenAPI 生成类型与契约一致"),
        "w5-sidecar": ("Sol planning", "pass "),
        "w5-frontend": ("countryOutageInvestigation.test.ts", "CountryOutageInvestigationPage.test.ts", "EventDetailPage.test.ts", "passed"),
    }
    for suite_id, markers in output_markers.items():
        output = receipts[suite_id]["normalized_output"]
        for marker in markers:
            expect(marker in output, "w5_test_output_marker_missing", f"{suite_id} 缺少真实输出标记：{marker}")
    for marker in W5_SIDECAR_REQUIRED_OUTPUT_MARKERS:
        expect(marker in receipts["w5-sidecar"]["normalized_output"], "w5_sidecar_required_test_not_run", f"Sidecar pinned runner未运行recipe攻击：{marker}")
    exact(receipts["w5-openapi"]["tests_run"], 8, "w5_test_count_mismatch", "OpenAPI 必须实际运行 8 测")
    expect(receipts["w5-sidecar"]["tests_run"] >= 23, "w5_test_count_mismatch", "Sidecar 必须至少实际运行 composition、planning/grounding 与recipe攻击共23测；允许继续增加")
    exact(receipts["w5-frontend"]["tests_run"], 15, "w5_test_count_mismatch", "Frontend 必须实际运行 15 测")
    expect(receipts["w5-python"]["tests_run"] >= 18, "w5_test_count_mismatch", "Python suite 必须至少运行runtime、7个API、完整validator正负、核心journey与正式trace测试；允许继续增加")
    _validate_w5_python_validator_test_source(root, receipts["w5-python"]["normalized_output"])
    execution_trace = _parse_w5_execution_trace(root, receipts["w5-python"]["normalized_output"])

    admission = evidence.get("execution_admission_receipt")
    expect(isinstance(admission, dict), "w5_execution_admission_missing", "W5 缺少独立 execution admission receipt")
    exact(set(admission), {
        "schema_version", "status", "registry_snapshot_id", "snapshot_digest",
        "registry_revision", "previous_snapshot_id", "snapshot_object_digest",
        "execution_allowed_unit_ids", "deferred_denied_unit_ids",
        "control_unit_entries",
        "arbitrary_callback_supported", "external_data_allowed",
        "production_deployed", "receipt_digest",
    }, "w5_execution_admission_invalid", "W5 execution admission 字段不精确")
    exact(admission.get("schema_version"), "country_outage_p2_s1_w5_execution_admission_v1", "w5_execution_admission_invalid", "W5 admission schema 漂移")
    exact(admission.get("status"), "admitted_local_isolated_execution", "w5_execution_admission_invalid", "W5 admission 状态漂移")
    snapshot_digest = _validate_prefixed_digest(admission.get("snapshot_digest"), "w5_execution_admission_invalid", "W5 snapshot digest 无效")
    exact(admission.get("registry_snapshot_id"), f"registry-snapshot-sha256:{snapshot_digest.removeprefix('sha256:')}", "w5_execution_admission_invalid", "W5 snapshot ID 与 digest 不一致")
    exact(admission.get("registry_revision"), 8, "w5_execution_admission_invalid", "W5 Registry revision 必须为 8")
    exact(admission.get("previous_snapshot_id"), W5_W4_ACTUAL_SNAPSHOT_ID, "w5_registry_predecessor_mismatch", "W5 未承接 actual W4 snapshot")
    _validate_prefixed_digest(admission.get("snapshot_object_digest"), "w5_execution_admission_invalid", "W5 snapshot object digest 无效")
    exact(admission.get("execution_allowed_unit_ids"), W5_EXECUTION_ALLOWED_UNIT_IDS, "w5_execution_population_invalid", "W5 execution allowlist 人口或顺序漂移")
    exact(admission.get("deferred_denied_unit_ids"), sorted(P2_1_UNIT_IDS), "p2_1_unit_smuggled", "W5 未精确拒绝 P2.1 人口")
    _validate_w5_admission_control_entries(root, admission)
    admission_control_by_unit = {
        item["unit_id"]: item for item in admission["control_unit_entries"]
    }
    for record in execution_trace["control_unit_execution_records"]:
        entry = admission_control_by_unit[record["unit_id"]]
        for field in (
            "handler_id", "implementation_digest", "input_schema_ref", "output_schema_ref",
        ):
            exact(record[field], entry[field], "w5_control_execution_record_invalid", f"{record['unit_id']} 执行记录未绑定admission {field}")
    for replay in execution_trace["design_semantic_validator_replays"]:
        exact(
            replay["runtime_admission_receipt"]["registry_snapshot_digest"],
            admission["snapshot_digest"],
            "w5_runtime_artifact_admission_invalid",
            f"{replay['artifact_kind']} B层runtime准入未绑定同一W5 Registry snapshot",
        )
    for record in execution_trace["business_unit_execution_records"]:
        exact(record["registry_snapshot_digest"], admission["snapshot_digest"], "w5_business_execution_record_invalid", f"{record['unit_id']} 未绑定同一W5 Registry snapshot")
    exact(admission.get("arbitrary_callback_supported"), False, "w5_dispatcher_callback_seam_open", "W5 不得开放任意 callback")
    exact(admission.get("external_data_allowed"), False, "w5_external_data_overclaim", "W5 不得接入外部数据")
    exact(admission.get("production_deployed"), False, "wave_deployment_overclaim", "W5 admission 不得声称生产部署")
    receipt_digest = _validate_prefixed_digest(admission.get("receipt_digest"), "w5_execution_admission_invalid", "W5 admission receipt digest 无效")
    exact(receipt_digest, "sha256:" + object_digest(admission, {"receipt_digest"}), "w5_execution_admission_invalid", "W5 admission receipt digest 不可重算")
    exact(execution_trace.get("execution_admission_receipt_digest"), receipt_digest, "w5_execution_trace_invalid", "execution trace 未绑定同一 admission receipt")
    exact(execution_trace["admission_event_chain"]["registry_snapshot_digest"], snapshot_digest, "w5_admission_event_registry_drift", "admission事件链未绑定同一W5 execution Registry")

    probe = evidence.get("dispatcher_probe")
    validate_recomputable_receipt(probe, "w5_dispatcher_probe_invalid", "W5 Dispatcher probe")
    exact(set(probe), {
        "schema_version", "dispatcher_path", "dispatcher_sha256", "test_suite_id",
        "execution_admission_receipt_digest", "caller_callback_injection_supported",
        "arbitrary_handler_name_supported", "dynamic_import_path_supported",
        "dynamic_member_fanout_supported", "p2_1_execution_supported",
        "production_deployed", "receipt_digest",
    }, "w5_dispatcher_probe_invalid", "W5 Dispatcher probe 字段不精确")
    exact(probe.get("schema_version"), "country_outage_p2_s1_w5_dispatcher_probe_v1", "w5_dispatcher_probe_invalid", "W5 Dispatcher probe schema 漂移")
    dispatcher_path = "backend/services/country_outage_p2_s1_registry_dispatcher.py"
    exact(probe.get("dispatcher_path"), dispatcher_path, "w5_dispatcher_probe_invalid", "W5 Dispatcher path 漂移")
    exact(probe.get("dispatcher_sha256"), file_sha256(root / dispatcher_path), "w5_dispatcher_probe_invalid", "W5 Dispatcher bytes 漂移")
    exact(probe.get("test_suite_id"), "w5-python", "w5_dispatcher_probe_invalid", "W5 Dispatcher probe 未绑定真实 Python suite")
    exact(probe.get("execution_admission_receipt_digest"), receipt_digest, "w5_dispatcher_probe_invalid", "W5 Dispatcher probe 未绑定 execution admission")
    for field in (
        "caller_callback_injection_supported", "arbitrary_handler_name_supported",
        "dynamic_import_path_supported", "dynamic_member_fanout_supported",
        "p2_1_execution_supported", "production_deployed",
    ):
        exact(probe.get(field), False, "w5_dispatcher_callback_seam_open", f"W5 Dispatcher probe {field} 必须为 false")
    _validate_w5_dispatcher_source(root)

    capability = evidence.get("runtime_capability_evidence")
    exact(capability, {
        "execution_scope": "local_isolated_fixture_only",
        "local_runtime_implemented": True,
        "investigation_api_available": True,
        "investigation_ui_available": True,
        "complete_result_set_freeze_available": True,
        "stable_result_set_pagination_available": True,
        "evidence_graph_commit_available": True,
        "authorized_export_available": True,
        "revision_and_digest_cas_available": True,
        "explicit_followup_anchor_required": True,
        "cancel_and_rerun_available": True,
        "real_publication_execution_verified": False,
        "proof_suite_ids": ["w5-python", "w5-openapi", "w5-frontend"],
    }, "w5_runtime_capability_invalid", "W5 本地产品能力机器声明漂移")

    model = evidence.get("model_chain_evidence")
    exact(model, {
        "execution_order": ["gpt-5.6-sol-planning", "host-grounding-validation-execution", "gpt-5.6-sol-reference", "ds-first-answer", "ds-revision-at-most-once"],
        "teacher_model_id": "gpt-5.6-sol",
        "student_model_id": "ds",
        "transport": "local_fixture_injection_only",
        "external_model_call_count": 0,
        "hard_gate_ids": ["GATE-01", "GATE-02", "GATE-03", "GATE-04", "GATE-05"],
        "independent_oracle_required": True,
        "successful_student_revision_max": 1,
        "teacher_reference_is_ground_truth": False,
        "model_alignment_passed": False,
        "runtime_model_promotion": False,
        "production_deployed": False,
        "proof_suite_id": "w5-sidecar",
    }, "w5_model_chain_invalid", "W5 Sol→Host→DS 本地 fixture 合同漂移或越级")

    atomicity = evidence.get("atomicity_evidence")
    exact(atomicity, {
        "one_tool_one_fact_population_preserved": True,
        "one_operator_one_deterministic_transform_preserved": True,
        "plan_cap_01_static_dag_only": True,
        "hidden_dynamic_fanout": False,
        "host_hidden_business_transform": False,
        "api_hidden_business_transform": False,
        "renderer_hidden_business_transform": False,
        "model_hidden_business_transform": False,
        "arbitrary_callback_seam": False,
        "proof_suite_ids": ["w5-python", "w5-sidecar"],
    }, "w5_atomicity_invalid", "W5 组合层破坏 Tool/Operator 功能原子性或隐藏 fan-out")

    acceptance = evidence.get("acceptance_scope")
    exact(acceptance, {
        "status": "local_isolated_composition_runtime_accepted_for_w6_certification",
        "implementation_wave_accepted": True,
        "w6_same_candidate_certification_passed": False,
        "external_model_execution_verified": False,
        "model_alignment_passed": False,
        "performance_acceptance_passed": False,
        "runtime_promotion_passed": False,
        "release_preparation_accepted": False,
        "production_deployed": False,
    }, "w5_acceptance_overclaim", "W5 验收层级越级或状态漂移")
    return [
        "w5_exact_content_addressed_artifact_population_verified",
        "w5_real_subprocess_receipts_and_outputs_verified",
        "w5_actual_w4_to_independent_execution_admission_verified",
        "w5_static_dispatcher_without_callback_or_dynamic_fanout_verified",
        "w5_api_ui_result_graph_export_and_cas_verified",
        "w5_sol_host_ds_local_fixture_chain_verified",
        "w5_p2_1_external_model_performance_and_production_boundaries_verified",
    ]


def _generate_w5_execution_admission(root: Path) -> dict[str, Any]:
    """从真实 Python runtime 生成 admission，再由 Hook 独立重算和校验。"""

    code = """
import json
from pathlib import Path
import tempfile
from backend.services.country_outage_p2_s1_registry_dispatcher import create_w5_execution_admission
from backend.services.country_outage_p2_s1_trusted_store import ContentAddressedStore
with tempfile.TemporaryDirectory(prefix='p2-s1-w5-admission-') as directory:
    store = ContentAddressedStore(Path(directory))
    create_w5_execution_admission(store)
    receipts = store.list_json('registry-admission')
    if len(receipts) != 1:
        raise RuntimeError('execution admission receipt population mismatch')
    print(json.dumps(receipts[0], ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True))
""".strip()
    completed = subprocess.run(
        ["uv", "run", "--project", "backend", "python", "-c", code],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    expect(completed.returncode == 0, "w5_evidence_generation_failed", f"无法从真实 runtime 生成 execution admission：{completed.stderr.strip()}")
    try:
        value = json.loads(
            completed.stdout,
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise AlignmentError(f"w5_evidence_generation_failed：execution admission 输出不是严格 JSON：{error}") from error
    expect(isinstance(value, dict), "w5_evidence_generation_failed", "execution admission 输出必须是对象")
    return value


def build_w5_evidence(root: Path) -> dict[str, Any]:
    """只从当前仓库字节、真实 runner 回执和 runtime admission 构造 W5 evidence。"""

    baseline = load_json(root / BASELINE_PATH)
    artifacts = []
    for path, role in W5_ARTIFACT_ROLES.items():
        artifact_path = root / path
        expect(artifact_path.is_file() and not artifact_path.is_symlink(), "w5_evidence_generation_failed", f"W5 制品不存在：{path}")
        artifacts.append({
            "path": path,
            "role": role,
            "size_bytes": artifact_path.stat().st_size,
            "sha256": file_sha256(artifact_path),
        })
    test_receipts = []
    for suite_id in W5_TEST_SUITE_IDS:
        stage, category, pinned_sha = STAGE_TEST_RUN_RECEIPTS[suite_id]
        expect(stage == "W5" and HEX64.fullmatch(pinned_sha) is not None, "w5_test_receipt_pin_missing", f"{suite_id} 尚未冻结真实 runner 回执")
        path = STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
        receipt = load_json(root / path)
        test_receipts.append({
            "suite_id": suite_id,
            "category": category,
            "path": path.as_posix(),
            "sha256": pinned_sha,
            "receipt_digest": receipt["receipt_digest"],
        })
    admission = _generate_w5_execution_admission(root)
    dispatcher_path = "backend/services/country_outage_p2_s1_registry_dispatcher.py"
    probe = {
        "schema_version": "country_outage_p2_s1_w5_dispatcher_probe_v1",
        "dispatcher_path": dispatcher_path,
        "dispatcher_sha256": file_sha256(root / dispatcher_path),
        "test_suite_id": "w5-python",
        "execution_admission_receipt_digest": admission["receipt_digest"],
        "caller_callback_injection_supported": False,
        "arbitrary_handler_name_supported": False,
        "dynamic_import_path_supported": False,
        "dynamic_member_fanout_supported": False,
        "p2_1_execution_supported": False,
        "production_deployed": False,
    }
    probe["receipt_digest"] = object_digest(probe)
    p0 = load_json(root / P0_RECEIPT_PATH)
    prior = {"S1I-P0": p0["receipt_digest"]}
    for stage in ("W1", "W2", "W3", "W4"):
        prior[stage] = load_json(root / WAVE_RECEIPT_ROOT / f"{stage}.json")["receipt_digest"]
    evidence: dict[str, Any] = {
        "schema_version": "country_outage_p2_s1_implementation_wave_evidence_v1",
        "stage": "W5",
        "status": "local_isolated_composition_runtime_accepted_for_w6_certification",
        "design_candidate_id": DESIGN_CANDIDATE_ID,
        "baseline_content_digest": baseline["content_digest"],
        "implementation_candidate_id": None,
        "implementation_semantic_digest": None,
        "content_digest": None,
        "effect": WAVE_CONTRACT["W5"]["effect"],
        "effect_verified": True,
        "implemented_unit_ids": list(WAVE_CONTRACT["W5"]["unit_ids"]),
        "atomic_split_tests_passed": True,
        "p2_1_units_included": [],
        "production_deployed": False,
        "artifact_manifest": artifacts,
        "test_receipts": test_receipts,
        "execution_admission_receipt": admission,
        "dispatcher_probe": probe,
        "runtime_capability_evidence": {
            "execution_scope": "local_isolated_fixture_only",
            "local_runtime_implemented": True,
            "investigation_api_available": True,
            "investigation_ui_available": True,
            "complete_result_set_freeze_available": True,
            "stable_result_set_pagination_available": True,
            "evidence_graph_commit_available": True,
            "authorized_export_available": True,
            "revision_and_digest_cas_available": True,
            "explicit_followup_anchor_required": True,
            "cancel_and_rerun_available": True,
            "real_publication_execution_verified": False,
            "proof_suite_ids": ["w5-python", "w5-openapi", "w5-frontend"],
        },
        "model_chain_evidence": {
            "execution_order": ["gpt-5.6-sol-planning", "host-grounding-validation-execution", "gpt-5.6-sol-reference", "ds-first-answer", "ds-revision-at-most-once"],
            "teacher_model_id": "gpt-5.6-sol",
            "student_model_id": "ds",
            "transport": "local_fixture_injection_only",
            "external_model_call_count": 0,
            "hard_gate_ids": ["GATE-01", "GATE-02", "GATE-03", "GATE-04", "GATE-05"],
            "independent_oracle_required": True,
            "successful_student_revision_max": 1,
            "teacher_reference_is_ground_truth": False,
            "model_alignment_passed": False,
            "runtime_model_promotion": False,
            "production_deployed": False,
            "proof_suite_id": "w5-sidecar",
        },
        "atomicity_evidence": {
            "one_tool_one_fact_population_preserved": True,
            "one_operator_one_deterministic_transform_preserved": True,
            "plan_cap_01_static_dag_only": True,
            "hidden_dynamic_fanout": False,
            "host_hidden_business_transform": False,
            "api_hidden_business_transform": False,
            "renderer_hidden_business_transform": False,
            "model_hidden_business_transform": False,
            "arbitrary_callback_seam": False,
            "proof_suite_ids": ["w5-python", "w5-sidecar"],
        },
        "acceptance_scope": {
            "status": "local_isolated_composition_runtime_accepted_for_w6_certification",
            "implementation_wave_accepted": True,
            "w6_same_candidate_certification_passed": False,
            "external_model_execution_verified": False,
            "model_alignment_passed": False,
            "performance_acceptance_passed": False,
            "runtime_promotion_passed": False,
            "release_preparation_accepted": False,
            "production_deployed": False,
        },
        "prior_stage_receipt_digests": prior,
    }
    evidence["implementation_semantic_digest"] = object_digest(
        evidence,
        {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
    )
    evidence["implementation_candidate_id"] = (
        f"country-outage-p2-s1-w5-{evidence['implementation_semantic_digest'][:24]}"
    )
    evidence["content_digest"] = object_digest(evidence, {"content_digest"})
    return evidence


def validate_wave(root: Path, stage: str, baseline: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    expect(stage in {"W0", "W1", "W2", "W3", "W4", "W5"}, "wave_stage_not_implemented", f"{stage} 尚未进入本实现任务，必须 fail-closed")
    contract = WAVE_CONTRACT[stage]
    evidence_path = root / WAVE_EVIDENCE_ROOT / f"{stage}.json"
    evidence = load_json(evidence_path)
    exact(evidence.get("schema_version"), "country_outage_p2_s1_implementation_wave_evidence_v1", "wave_evidence_schema_mismatch", f"{stage} evidence Schema 不匹配")
    exact(evidence.get("stage"), stage, "wave_evidence_stage_mismatch", f"{stage} evidence stage 不匹配")
    expected_status = {
        "W0": "implementation_wave_accepted",
        "W5": "local_isolated_composition_runtime_accepted_for_w6_certification",
    }.get(stage, "offline_atomic_harness_accepted_for_w5_integration")
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
    elif stage == "W5":
        wave_checks = validate_w5_evidence(root, evidence)
    else:
        wave_checks = validate_w1_w2_evidence(root, stage, evidence)
        if stage == "W4":
            _validate_op33_population_evidence_contract(root)
            wave_checks.append("op33_dual_population_evidence_contract_verified")
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
        if stage in {"W0", "W5"}
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
        "runtime_implemented": stage in {"W5", "W6"},
        "production_deployed": False,
    }
    receipt["receipt_digest"] = object_digest(receipt)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P2-S1 实现工程阶段防跑偏 Hook")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--stage", choices=STAGE_SEQUENCE)
    parser.add_argument("--output")
    parser.add_argument(
        "--generate-w5-evidence",
        action="store_true",
        help="从当前制品、冻结真实 runner 回执与真实 runtime admission 构造 W5 evidence",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.repo_root).resolve()
    try:
        if args.generate_w5_evidence:
            expect(args.stage is None, "cli_mode_conflict", "生成 W5 evidence 时不得同时指定 --stage")
            expect(isinstance(args.output, str) and args.output, "cli_output_missing", "生成 W5 evidence 必须指定 --output")
            evidence = build_w5_evidence(root)
            output = (root / args.output).resolve()
            expect(output.is_relative_to(root), "cli_output_invalid", "W5 evidence 输出不得逃逸仓库")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(evidence, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(json.dumps({
                "stage": "W5",
                "status": evidence["status"],
                "implementation_candidate_id": evidence["implementation_candidate_id"],
                "content_digest": evidence["content_digest"],
                "output": args.output,
            }, ensure_ascii=False, sort_keys=True))
            return 0
        expect(args.stage is not None, "cli_stage_missing", "对齐检查必须指定 --stage")
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
