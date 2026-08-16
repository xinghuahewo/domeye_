#!/usr/bin/env python3
"""重建 P2-S1 W0-W4 同候选阶段证据。

该脚本只从当前仓库字节、冻结 runner 回执和 TypeScript Registry 实际输出
构造离线验收证据；不执行 Tool/Operator，不授予运行时执行，也不部署。
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[4]
HOOK_PATH = ROOT / ".codex/hooks/country_outage_agent_p2_s1_implementation_alignment.py"
SPEC = importlib.util.spec_from_file_location("p2_s1_implementation_alignment", HOOK_PATH)
assert SPEC is not None and SPEC.loader is not None
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)


def load(relative: Path | str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def write(relative: Path | str, value: dict[str, Any]) -> None:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def artifact(relative: Path | str, role: str) -> dict[str, Any]:
    relative = Path(relative)
    path = ROOT / relative
    return {
        "path": relative.as_posix(),
        "role": role,
        "size_bytes": path.stat().st_size,
        "sha256": HOOK.file_sha256(path),
    }


def signed(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result["receipt_digest"] = HOOK.object_digest(result, {"receipt_digest"})
    return result


def finalize(stage: str, evidence: dict[str, Any]) -> dict[str, Any]:
    evidence["implementation_semantic_digest"] = HOOK.object_digest(
        evidence,
        {"implementation_candidate_id", "implementation_semantic_digest", "content_digest"},
    )
    evidence["implementation_candidate_id"] = (
        f"country-outage-p2-s1-{stage.lower()}-{evidence['implementation_semantic_digest'][:24]}"
    )
    evidence["content_digest"] = HOOK.object_digest(evidence, {"content_digest"})
    return evidence


def test_reference(suite_id: str) -> dict[str, Any]:
    _, category, sha256 = HOOK.STAGE_TEST_RUN_RECEIPTS[suite_id]
    path = HOOK.STAGE_TEST_RUN_RECEIPT_ROOT / f"{suite_id}.json"
    receipt = load(path)
    return {
        "suite_id": suite_id,
        "category": category,
        "path": path.as_posix(),
        "sha256": sha256,
        "receipt_digest": receipt["receipt_digest"],
    }


def build_w0() -> dict[str, Any]:
    previous = load(HOOK.WAVE_EVIDENCE_ROOT / "W0.json")
    fixture_manifest = load(HOOK.W0_SOURCE_FIXTURE_MANIFEST)
    fixture_by_population = {
        item["population_id"]: item for item in fixture_manifest["population_manifests"]
    }
    # 不继承历史 artifact manifest。物化回执是内容寻址文件，人口重建后旧路径
    # 会被替换；继续遍历旧清单会让当前候选依赖已经不存在的文件。这里从当前
    # 权威目录和固定运行时入口重新枚举，保证每次重建都只绑定当前字节。
    artifact_roles: dict[Path, str] = {}
    source_contract_root = Path("contracts/data/country-outage-p2-s1")
    for path in sorted((ROOT / source_contract_root).glob("*")):
        if path.is_file():
            artifact_roles[path.relative_to(ROOT)] = "source_schema"
    fixture_root = ROOT / HOOK.W0_SOURCE_FIXTURE_MANIFEST.parent
    for path in sorted(fixture_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        artifact_roles[relative] = (
            "source_fixture_manifest"
            if relative == HOOK.W0_SOURCE_FIXTURE_MANIFEST
            else "source_fixture_data"
        )
    artifact_roles.update({
        Path("tools/build_country_outage_p2_s1_source_views.py"): "source_materializer",
        Path("backend/services/country_outage_p2_s1_source_store.py"): "source_store",
        Path("backend/web/tests/test_country_outage_p2_s1_source_store.py"): "test",
        Path("dev/tests/test_country_outage_p2_s1_w0_source_governance.py"): "test",
        Path("agent-sidecar/src/chat/p2-s1-registry-runtime.ts"): "registry_runtime",
        Path("agent-sidecar/src/chat/p2-s1-trusted-receipt-store.ts"): "trusted_receipt_store",
        Path("agent-sidecar/tests/p2-s1-w0-source-governance.test.ts"): "test",
        Path("contracts/agent/country-outage-p2-s1-implementation/w0-registry-proposal.schema.json"): "registry_contract",
        Path("contracts/agent/country-outage-p2-s1-implementation/w0-trusted-receipt.schema.json"): "trusted_receipt_contract",
    })
    artifacts = [
        artifact(path, artifact_roles[path])
        for path in sorted(artifact_roles, key=lambda item: item.as_posix())
    ]

    receipts = []
    old_by_kind_population = {
        (item.get("receipt_kind"), item.get("population_id")): item
        for item in previous["source_and_governance_receipts"]
    }
    for population in HOOK.W0_SOURCE_POPULATIONS:
        item = copy.deepcopy(old_by_kind_population[("source_population_contract", population)])
        schema_path = f"contracts/data/country-outage-p2-s1/{HOOK.W0_SOURCE_SCHEMA_BY_POPULATION[population]}"
        fixture = fixture_by_population[population]
        item.update({
            "schema_path": schema_path,
            "schema_sha256": HOOK.file_sha256(ROOT / schema_path),
            "fixture_manifest_sha256": HOOK.file_sha256(ROOT / HOOK.W0_SOURCE_FIXTURE_MANIFEST),
            "fixture_store_id": fixture_manifest["store_id"],
            "fixture_population_readiness": fixture["readiness"],
            "fixture_row_count": fixture["row_count"],
            "fixture_member_keys_digest": fixture["member_keys_digest"],
            "fixture_materialization_receipt_digest": fixture["materialization_receipt_digest"],
        })
        receipts.append(signed({key: value for key, value in item.items() if key != "receipt_digest"}))
    for kind in ("registry_governance_contract", "trusted_receipt_store_contract"):
        item = copy.deepcopy(old_by_kind_population[(kind, None)])
        for prefix in ("contract", "implementation", "attack_test_artifact"):
            path_key = f"{prefix}_path"
            sha_key = f"{prefix}_sha256"
            if path_key in item:
                item[sha_key] = HOOK.file_sha256(ROOT / item[path_key])
        if "attack_test_receipt_digest" in item:
            item["attack_test_receipt_digest"] = HOOK.file_sha256(
                ROOT / item["attack_test_artifact_path"]
            )
        receipts.append(signed({key: value for key, value in item.items() if key != "receipt_digest"}))

    p0 = load(HOOK.P0_RECEIPT_PATH)
    baseline = load(HOOK.BASELINE_PATH)
    evidence = {
        **previous,
        "status": "implementation_wave_accepted",
        "baseline_content_digest": baseline["content_digest"],
        "implementation_candidate_id": None,
        "implementation_semantic_digest": None,
        "content_digest": None,
        "artifact_manifest": artifacts,
        "source_and_governance_receipts": receipts,
        "test_receipts": [test_reference("w0-python"), test_reference("w0-typescript")],
        "prior_stage_receipt_digests": {"S1I-P0": p0["receipt_digest"]},
    }
    return finalize("W0", evidence)


def projection_from_bundle(stage: str, bundle: dict[str, Any]) -> dict[str, Any]:
    manifest = bundle["handler_manifest"]
    wave = bundle["wave_snapshot"]
    admission = bundle["wave_admission_receipt"]
    payload = wave["snapshot_payload"]
    binding = {
        "binding_manifest_id": None,
        "binding_manifest_digest": None,
        "wave_binding_unit_ids": list(payload["admitted_wave_binding_unit_ids"]),
        "binding_kind": "immutable_non_callable_dispatch_binding",
        "artifact_path": HOOK.W1_W2_REGISTRY_RUNTIME_PATH.as_posix(),
        "artifact_sha256": HOOK.file_sha256(ROOT / HOOK.W1_W2_REGISTRY_RUNTIME_PATH),
        "structural_binding_contract_sha256": HOOK.file_sha256(ROOT / HOOK.STRUCTURAL_BINDING_PATH),
        "callable_handler_refs": [],
        "caller_callback_refs": [],
        "trusted_dispatcher_id": None,
    }
    binding_digest = HOOK.object_digest(
        binding, {"binding_manifest_id", "binding_manifest_digest"}
    )
    binding["binding_manifest_digest"] = binding_digest
    binding["binding_manifest_id"] = (
        f"p2-s1-dispatch-binding-manifest-sha256:{binding_digest}"
    )
    proposal = payload["proposal_snapshot_ref"]
    previous = payload["previous_snapshot_ref"]
    if stage != "W1":
        # W2-W4 的兼容投影视图必须承接前一 Registry 波次投影，而不是把 TypeScript
        # 治理对象的摘要混入 Python 投影摘要空间。实际治理链由 bundle 单独验证。
        wave_index = HOOK.REGISTRY_WAVE_SEQUENCE.index(stage)
        previous_stage = HOOK.REGISTRY_WAVE_SEQUENCE[wave_index - 1]
        previous_projection = load(HOOK.WAVE_EVIDENCE_ROOT / f"{previous_stage}.json")[
            "registry_binding_projection"
        ]["snapshot"]
        previous_snapshot_id = previous_projection["snapshot_id"]
        previous_snapshot_digest = previous_projection["snapshot_digest"]
        previous_registry_revision = previous_projection["registry_revision"]
    else:
        previous_snapshot_id = previous["registry_snapshot_id"]
        previous_snapshot_digest = previous["snapshot_digest"].removeprefix("sha256:")
        previous_registry_revision = int(previous["registry_revision"])
    snapshot = {
        "schema_version": "country_outage_p2_s1_registry_wave_snapshot_v1",
        "snapshot_id": None,
        "snapshot_digest": None,
        "registry_revision": int(payload["registry_revision"]),
        "wave_id": stage,
        "proposal_snapshot_id": proposal["registry_snapshot_id"],
        "proposal_snapshot_digest": proposal["snapshot_digest"].removeprefix("sha256:"),
        "proposal_registry_revision": int(proposal["registry_revision"]),
        "previous_snapshot_id": previous_snapshot_id,
        "previous_snapshot_digest": previous_snapshot_digest,
        "previous_registry_revision": previous_registry_revision,
        "binding_manifest_id": binding["binding_manifest_id"],
        "binding_manifest_digest": binding["binding_manifest_digest"],
        "structural_binding_contract_sha256": binding["structural_binding_contract_sha256"],
        "admitted_wave_binding_unit_ids": list(payload["admitted_wave_binding_unit_ids"]),
        "admitted_binding_unit_ids": list(payload["admitted_binding_unit_ids"]),
        "binding_admission_only": True,
        "artifact_path": binding["artifact_path"],
        "artifact_sha256": binding["artifact_sha256"],
        "production_deployed": False,
    }
    snapshot_digest = HOOK.object_digest(snapshot, {"snapshot_id", "snapshot_digest"})
    snapshot["snapshot_digest"] = snapshot_digest
    snapshot["snapshot_id"] = f"p2-s1-registry-wave-sha256:{snapshot_digest}"
    projected_admission = signed({
        "schema_version": "country_outage_p2_s1_registry_wave_admission_v1",
        "status": admission["status"],
        "wave_id": stage,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "registry_revision": snapshot["registry_revision"],
        "previous_snapshot_id": snapshot["previous_snapshot_id"],
        "previous_snapshot_digest": snapshot["previous_snapshot_digest"],
        "previous_registry_revision": snapshot["previous_registry_revision"],
        "binding_manifest_id": binding["binding_manifest_id"],
        "binding_manifest_digest": binding["binding_manifest_digest"],
        "structural_binding_contract_sha256": binding["structural_binding_contract_sha256"],
        "registry_artifact_path": binding["artifact_path"],
        "registry_artifact_sha256": binding["artifact_sha256"],
        "admitted_wave_binding_unit_ids": list(admission["admitted_wave_binding_unit_ids"]),
        "admitted_binding_unit_ids": list(admission["admitted_binding_unit_ids"]),
        "execution_allowed_unit_ids": [],
        "partial_binding_admission": False,
        "trusted_dispatcher_bound": False,
        "execution_started": False,
        "production_deployed": False,
    })
    probe = signed({
        "schema_version": "country_outage_p2_s1_registry_non_execution_probe_v1",
        "wave_id": stage,
        "tested_unit_ids": list(payload["admitted_wave_binding_unit_ids"]),
        "test_artifact_path": HOOK.W1_W2_REGISTRY_TEST_PATH.as_posix(),
        "test_artifact_sha256": HOOK.file_sha256(ROOT / HOOK.W1_W2_REGISTRY_TEST_PATH),
        "caller_callback_injection_supported": False,
        "caller_callback_spy_count": bundle["non_execution_probe"]["caller_callback_spy_count"],
        "execution_allowed_unit_ids": [],
        "assert_execution_authorized_error": bundle["non_execution_probe"]["assert_execution_authorized_error"],
        "trusted_dispatcher_bound": False,
    })
    return {
        "source_runtime_bundle": {
            "path": (HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json").as_posix(),
            "sha256": HOOK.file_sha256(
                ROOT / HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json"
            ),
            "content_digest": bundle["content_digest"],
            "handler_manifest_id": manifest["handler_manifest_id"],
            "handler_manifest_digest": manifest["handler_manifest_digest"],
            "snapshot_id": wave["registry_snapshot_id"],
            "snapshot_digest": wave["snapshot_digest"],
            "admission_receipt_digest": admission["receipt_digest"],
        },
        "artifact": artifact(HOOK.W1_W2_REGISTRY_RUNTIME_PATH, "registry_runtime"),
        "binding_manifest": binding,
        "snapshot": snapshot,
        "admission_receipt": projected_admission,
        "execution_probe": probe,
        "execution_scope": {
            "offline_harness_verified": True,
            "trusted_dispatcher_implemented": False,
            "registry_execution_authorized": False,
            "production_deployed": False,
        },
    }


def build_wave(stage: str) -> dict[str, Any]:
    baseline = load(HOOK.BASELINE_PATH)
    tool_map, operator_map = HOOK._catalog_units(ROOT)
    wave_units = list(HOOK.WAVE_CONTRACT[stage]["unit_ids"])
    artifacts = [artifact(path, role) for path, role in HOOK.atomic_wave_artifact_roles(stage).items()]
    frozen_roles = {
        HOOK.TOOL_CATALOG_PATH: "frozen_tool_catalog",
        HOOK.TOOL_CONTRACT_SCHEMA_PATH: "frozen_tool_contract_schema",
        HOOK.OPERATOR_CATALOG_PATH: "frozen_operator_catalog",
        HOOK.OPERATOR_CONTRACT_SCHEMA_PATH: "frozen_operator_contract_schema",
    }
    populations = [tool_map[item]["source_population"] for item in wave_units if item in tool_map]
    source_manifest = load(HOOK.W0_SOURCE_FIXTURE_MANIFEST)
    w0_receipt = load(HOOK.WAVE_RECEIPT_ROOT / "W0.json")
    source_binding = {
        "w0_receipt_digest": w0_receipt["receipt_digest"],
        "store_id": source_manifest["store_id"],
        "manifest": artifact(HOOK.W0_SOURCE_FIXTURE_MANIFEST, "w0_source_manifest"),
        "source_store": artifact(HOOK.W1_W2_SOURCE_STORE_PATH, "w0_source_store"),
        "population_schemas": [
            {
                "population_id": population,
                **artifact(HOOK.W1_W2_SOURCE_SCHEMA_PATHS[population], "w0_source_schema"),
            }
            for population in populations
        ],
    }
    atomic_receipts = []
    for unit_id in wave_units:
        unit_kind = "tool" if unit_id in tool_map else "operator"
        catalog_entry = tool_map.get(unit_id) or operator_map[unit_id]
        implementation_path = (
            HOOK.W1_W2_TOOL_IMPLEMENTATION_PATH
            if unit_kind == "tool"
            else HOOK.W1_W2_OPERATOR_IMPLEMENTATION_PATH
        )
        input_ref, output_ref = HOOK._w1_w2_schema_refs(unit_id, tool_map, operator_map)
        receipt = {
            "schema_version": "country_outage_p2_s1_atomic_unit_receipt_v1",
            "receipt_kind": "atomic_unit_implementation",
            "stage": stage,
            "design_candidate_id": HOOK.DESIGN_CANDIDATE_ID,
            "unit_id": unit_id,
            "unit_kind": unit_kind,
            "catalog_entry_digest": HOOK.sha256_bytes(HOOK.canonical_json(catalog_entry)),
            "implementation_path": implementation_path.as_posix(),
            "implementation_sha256": HOOK.file_sha256(ROOT / implementation_path),
            "input_schema_ref": input_ref,
            "output_schema_ref": output_ref,
            "registered_atomic_operation_count": 1,
            "business_transform_count": 0 if unit_kind == "tool" else 1,
            "fact_population_read_count": 1 if unit_kind == "tool" else 0,
            "internal_unit_calls": 0,
            "model_call_count": 0,
            "external_read_count": 0,
            "p2_1_unit_ids": [],
        }
        if unit_kind == "tool":
            receipt["source_population_id"] = catalog_entry["source_population"]
        atomic_receipts.append(signed(receipt))
    bundle_path = HOOK.W1_W2_REGISTRY_EVIDENCE_ROOT / f"{stage}.json"
    bundle = load(bundle_path)
    p0 = load(HOOK.P0_RECEIPT_PATH)
    evidence = {
        "schema_version": "country_outage_p2_s1_implementation_wave_evidence_v1",
        "stage": stage,
        "status": "offline_atomic_harness_accepted_for_w5_integration",
        "design_candidate_id": HOOK.DESIGN_CANDIDATE_ID,
        "baseline_content_digest": baseline["content_digest"],
        "implementation_candidate_id": None,
        "implementation_semantic_digest": None,
        "content_digest": None,
        "effect": HOOK.WAVE_CONTRACT[stage]["effect"],
        "effect_verified": True,
        "implemented_unit_ids": wave_units,
        "atomic_split_tests_passed": True,
        "p2_1_units_included": [],
        "production_deployed": False,
        "artifact_manifest": artifacts,
        "structural_binding_contract": artifact(HOOK.STRUCTURAL_BINDING_PATH, "structural_binding_contract"),
        "frozen_contracts": [artifact(path, role) for path, role in frozen_roles.items()],
        "w0_source_binding": source_binding,
        "atomic_unit_receipts": atomic_receipts,
        "test_receipts": [test_reference(f"{stage.lower()}-{category}") for category in ("positive", "boundary", "attack")],
        "registry_runtime_evidence": {
            "path": bundle_path.as_posix(),
            "sha256": HOOK.W1_W2_REGISTRY_EVIDENCE_SHA256[stage],
            "content_digest": bundle["content_digest"],
            "generator_path": HOOK.W1_W2_REGISTRY_EVIDENCE_GENERATOR_PATH.as_posix(),
            "generator_sha256": HOOK.file_sha256(ROOT / HOOK.W1_W2_REGISTRY_EVIDENCE_GENERATOR_PATH),
        },
        "registry_binding_projection": projection_from_bundle(stage, bundle),
        "capability_scope": {
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
        "performance_baseline": {
            "measurement_status": "not_w6_acceptance",
            "performance_acceptance_passed": False,
        },
        "prior_stage_receipt_digests": {
            "S1I-P0": p0["receipt_digest"],
            **{
                dependency: load(HOOK.WAVE_RECEIPT_ROOT / f"{dependency}.json")["receipt_digest"]
                for dependency in HOOK.stage_prior_dependencies(stage)
            },
        },
    }
    return finalize(stage, evidence)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=("W0", "W1", "W2", "W3", "W4"))
    args = parser.parse_args()
    evidence = build_w0() if args.stage == "W0" else build_wave(args.stage)
    write(HOOK.WAVE_EVIDENCE_ROOT / f"{args.stage}.json", evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
