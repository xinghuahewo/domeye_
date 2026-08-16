"""P2-S1 W5 本地隔离 Investigation Runtime。

调查计划在构造时静态注入；请求无法插入 callback、业务单元或隐藏
fan-out。每次可变操作都使用 investigation revision + current digest CAS。
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .country_outage_p2_s1_contract_runtime import (
    DESIGN_CANDIDATE_ID,
    W5ContractError,
    canonical_json,
    digest_hex,
    digest_prefixed,
    digest_without_fields,
    ancestors_by_node,
    load_frozen_contract,
    repository_root,
    validate_identity,
    validate_json_schema,
    validate_principal,
    validate_static_dag,
)
from .country_outage_p2_s1_delivery import DeliveryManager
from .country_outage_p2_s1_evidence_graph import EvidenceGraphManager, validate_evidence_references
from .country_outage_p2_s1_registry_dispatcher import W5RegistryDispatcher
from .country_outage_p2_s1_result_set import ResultSetManager, validate_result_set
from .country_outage_p2_s1_trusted_store import ContentAddressedStore


class ModelTurnPort(Protocol):
    def create_turn(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


class PlanningGroundingPort(Protocol):
    def resolve(self, *, goal: str, identity: Mapping[str, Any]) -> Mapping[str, Any]: ...


class TrustedFixturePlanningGroundingPort:
    """Host-owned 离线 Sol planning/grounding fixture resolver。"""

    def __init__(self, *, fixture_catalog: Mapping[str, Any], fixture_id: str) -> None:
        entry = fixture_catalog.get(fixture_id) if isinstance(fixture_catalog, Mapping) else None
        if not isinstance(entry, Mapping) or entry.get("fixture_id") != fixture_id or entry.get("catalog_entry_digest") != digest_prefixed({key: value for key, value in entry.items() if key != "catalog_entry_digest"}):
            raise W5InvestigationError("planning_fixture_catalog_invalid", "Host planning fixture catalog未闭合")
        self._payload = copy.deepcopy(dict(entry))
        self.fixture_digest = entry["catalog_entry_digest"]

    def resolve(self, *, goal: str, identity: Mapping[str, Any]) -> Mapping[str, Any]:
        projection = self._payload["trusted_grounding_plan_projection"]
        recipe = projection["grounded_execution_recipe"]
        if recipe.get("recipe_digest") != digest_prefixed({key: value for key, value in recipe.items() if key != "recipe_digest"}) or projection.get("grounding_plan_projection_digest") != digest_prefixed({key: value for key, value in projection.items() if key != "grounding_plan_projection_digest"}):
            raise W5InvestigationError("planning_fixture_projection_digest_drift", "受信fixture projection/recipe摘要漂移")
        if goal != self._payload["goal"] or recipe["goal_digest"] != digest_prefixed(goal):
            raise W5InvestigationError("grounding_fixture_goal_mismatch", "goal 未命中受信 Sol planning fixture", status_code=409)
        identity_digest = identity["identity_digest"]
        semantic_base = {"receipt_kind": "sol_semantic_plan_fixture_replay", "fixture_digest": self.fixture_digest, "goal_digest": digest_hex(goal), "identity_digest": identity_digest, "disposition": "completed"}
        semantic = {**semantic_base, "receipt_digest": digest_hex(semantic_base)}
        nodes = recipe["nodes"]
        execution_nodes_digest = digest_hex({"nodes": nodes})
        grounding_base = {"receipt_kind": "host_plan_grounding", "fixture_digest": self.fixture_digest, "goal_digest": digest_hex(goal), "identity_digest": identity_digest, "semantic_plan_receipt_digest": semantic["receipt_digest"], "execution_nodes_digest": execution_nodes_digest, "grounding_plan_projection_digest": execution_nodes_digest, "disposition": "passed"}
        grounding = {**grounding_base, "receipt_digest": digest_hex(grounding_base)}
        return {"fixture_digest": self.fixture_digest, "trusted_grounding_plan_projection": copy.deepcopy(projection), "semantic_plan_receipt": semantic, "grounding_receipt": grounding}


class LocalFixtureSidecarPlanningGroundingPort:
    """受控 loopback Sidecar planning-only 端口；完整 Plan 仍由 Python Host 构造。"""

    def __init__(
        self,
        *,
        base_url: str,
        shared_token: str,
        fixture_id: str,
        binding_summary: Mapping[str, Any],
        timeout_seconds: float = 30.0,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("W5 planning Sidecar 只允许无用户信息的 loopback HTTP")
        if len(shared_token) < 24 or not fixture_id:
            raise ValueError("W5 planning Sidecar 受信配置无效")
        self.base_url = base_url.rstrip("/")
        self.shared_token = shared_token
        self.fixture_id = fixture_id
        self.binding_summary = copy.deepcopy(dict(binding_summary))
        self.timeout_seconds = timeout_seconds

    def resolve(self, *, goal: str, identity: Mapping[str, Any]) -> Mapping[str, Any]:
        summary = copy.deepcopy(self.binding_summary)
        identity_pairs = {
            "incident_id": identity["incident_id"], "publication_id": identity["publication_id"],
            "publication_revision": identity["publication_revision"], "publication_digest": identity["publication_digest"],
            "collector_id": identity["collector_id"], "cohort_id": identity["cohort_id"], "cohort_digest": identity["cohort_digest"],
            "window_start_utc": identity["window_start_utc"], "window_end_utc": identity["window_end_utc"],
            "data_through_utc": identity["data_through_utc"], "finality": identity["finality"],
            "binding_generation": identity["binding_generation"], "registry_snapshot_id": identity["registry_snapshot_id"],
            "registry_snapshot_digest": identity["registry_snapshot_digest"],
        }
        if any(summary.get(field) != expected for field, expected in identity_pairs.items()):
            raise W5InvestigationError("planning_sidecar_binding_summary_mismatch", "Sidecar binding summary 与调查身份不一致")
        body = {
            "fixture_id": self.fixture_id,
            "goal": goal,
            "goal_digest": digest_prefixed(goal),
            "binding_summary": summary,
            "binding_summary_digest": digest_prefixed(summary),
            "idempotency_key": "planning-" + digest_hex({"fixture_id": self.fixture_id, "goal": goal, "identity_digest": identity["identity_digest"]}),
        }
        raw = json.dumps(body, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request = Request(
            self.base_url + "/country-outage/p2-s1-w5/planning-groundings",
            data=raw,
            method="POST",
            headers={"Authorization": f"Bearer {self.shared_token}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise W5InvestigationError("planning_grounding_sidecar_unavailable", "受信 planning Sidecar 不可用", status_code=503, retryable=True) from exc
        if not isinstance(result, Mapping) or result.get("schema_version") != "country_outage_p2_s1_w5_planning_grounding_result_v1" or result.get("disposition") != "grounded_projection":
            raise W5InvestigationError("planning_grounding_sidecar_invalid", "Sidecar 未返回受信 grounded projection", status_code=503)
        if result.get("goal_digest") != body["goal_digest"] or result.get("binding_summary_digest") != body["binding_summary_digest"] or result.get("fixture_id") != self.fixture_id:
            raise W5InvestigationError("planning_grounding_sidecar_binding_drift", "Sidecar planning 结果绑定漂移")
        boundary = result.get("full_investigation_plan")
        execution = result.get("execution_boundary")
        projection = result.get("trusted_grounding_plan_projection")
        if not isinstance(boundary, Mapping) or boundary.get("status") != "host_runtime_required" or boundary.get("projection_is_full_plan") is not False or not isinstance(projection, Mapping) or not isinstance(execution, Mapping) or execution.get("external_provider_called") is not False:
            raise W5InvestigationError("planning_grounding_sidecar_boundary_invalid", "Sidecar 越过 planning-only 边界")
        semantic_source = result.get("semantic_plan_validation_receipt")
        grounding_source = result.get("host_grounding_receipt")
        if not isinstance(semantic_source, Mapping) or not isinstance(grounding_source, Mapping):
            raise W5InvestigationError("planning_grounding_sidecar_receipt_missing", "Sidecar planning/grounding 回执缺失")
        semantic_base = {
            "receipt_kind": "sol_semantic_plan_sidecar_fixture_replay", "fixture_digest": result["fixture_digest"],
            "goal_digest": digest_hex(goal), "identity_digest": identity["identity_digest"],
            "sidecar_semantic_validation_receipt_digest": semantic_source["receipt_digest"],
            "teacher_semantic_plan_digest": result["teacher_semantic_plan"]["output_digest"], "disposition": "completed",
        }
        semantic = {**semantic_base, "receipt_digest": digest_hex(semantic_base)}
        if projection.get("schema_version") != "country_outage_p2_grounding_plan_projection_v2":
            raise W5InvestigationError("planning_grounding_projection_version_invalid", "Sidecar projection 不是v2 recipe")
        recipe = projection.get("grounded_execution_recipe")
        if not isinstance(recipe, Mapping) or recipe.get("recipe_digest") != digest_prefixed({key: value for key, value in recipe.items() if key != "recipe_digest"}):
            raise W5InvestigationError("planning_grounding_recipe_digest_drift", "Sidecar grounded recipe 摘要漂移")
        expected_projection_digest = digest_prefixed({key: value for key, value in projection.items() if key != "grounding_plan_projection_digest"})
        if projection.get("grounding_plan_projection_digest") != expected_projection_digest:
            raise W5InvestigationError("planning_grounding_projection_digest_drift", "Sidecar projection 摘要漂移")
        recipe_nodes = [{
            "node_id": item["node_id"], "unit_id": item["unit_id"], "depends_on": copy.deepcopy(item["depends_on"]),
            "dependency_mode": item["dependency_mode"], "requiredness": item["requiredness"],
            "parameters": copy.deepcopy(item["parameters"]), "input_bindings": copy.deepcopy(item["input_binding_sources"]),
        } for item in recipe["nodes"]]
        if recipe.get("goal_digest") != digest_prefixed(goal) or recipe.get("registry_snapshot_id") != identity["registry_snapshot_id"] or recipe.get("registry_snapshot_digest") != identity["registry_snapshot_digest"]:
            raise W5InvestigationError("planning_grounding_recipe_binding_drift", "Sidecar recipe goal/Registry绑定漂移")
        projection_digest = projection["grounding_plan_projection_digest"]
        grounding_base = {
            "receipt_kind": "host_plan_grounding_from_sidecar_projection", "fixture_digest": result["fixture_digest"],
            "goal_digest": digest_hex(goal), "identity_digest": identity["identity_digest"],
            "semantic_plan_receipt_digest": semantic["receipt_digest"],
            "sidecar_host_grounding_receipt_digest": grounding_source["receipt_digest"],
            "grounding_plan_projection_digest": projection_digest,
            "execution_nodes_digest": digest_hex({"nodes": recipe_nodes}),
            "grounded_execution_recipe_digest": recipe["recipe_digest"], "disposition": "passed",
        }
        grounding = {**grounding_base, "receipt_digest": digest_hex(grounding_base)}
        return {"fixture_digest": result["fixture_digest"], "trusted_grounding_plan_projection": copy.deepcopy(dict(projection)), "semantic_plan_receipt": semantic, "grounding_receipt": grounding}


class W5InvestigationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
        next_action: str | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.next_action = next_action
        super().__init__(message)


_DEFAULT_PLAN_NODES = (
    {
        "node_id": "boundary_01",
        "unit_id": "BOUNDARY-01",
        "depends_on": [],
        "dependency_mode": "hard",
        "requiredness": "boundary_only",
        "parameters": {},
        "input_bindings": [],
    },
)
_TERMINAL = {"completed", "partially_completed", "cancelled", "failed"}


def _design_plan_instance(
    *,
    identity: Mapping[str, Any],
    goal: str,
    execution_nodes: Sequence[Mapping[str, Any]],
    dispatcher: W5RegistryDispatcher,
    planning_receipt_binding: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    decomposition_path = repository_root() / "contracts/agent/country-outage-p2-s1-execution-unit-design/execution-unit-decomposition.json"
    decomposition = json.loads(decomposition_path.read_text(encoding="utf-8"))
    capabilities = {item["unit_id"]: item["atomic_capability_id"] for item in decomposition["atomic_units"]}
    design_identity = copy.deepcopy(dict(identity))
    design_identity.pop("identity_digest", None)
    design_identity["binding_digest"] = digest_without_fields(design_identity, "binding_digest")
    # 这是 Tool/Operator Host 设计上限，不是模型费用测量；使用有限大数
    # 表示 W5 本地原子单元不因货币预算为 0 而被误拒。
    budget = {"max_wall_ms": 30000, "max_rows": 100000, "max_bytes": 67108864, "max_cost_amount": 1000000000, "cost_currency": "USD"}
    design_nodes = []
    parameter_bindings: dict[str, Any] = {}
    for wave, execution in enumerate(execution_nodes):
        unit_id = execution["unit_id"]
        entry = dispatcher.admission.entries[unit_id]
        parameters = copy.deepcopy(dict(execution["parameters"]))
        if unit_id.startswith("TOOL-"):
            tool_identity = copy.deepcopy(dict(identity))
            tool_identity.pop("identity_digest", None)
            tool_identity.pop("binding_digest", None)
            tool_identity["registry_snapshot_digest"] = str(tool_identity["registry_snapshot_digest"]).removeprefix("sha256:")
            parameters["identity"] = tool_identity
            parameters.setdefault("page_size", 200)
            parameters.setdefault("page_token", None)
        elif unit_id.startswith("OP-"):
            operator_identity = copy.deepcopy(dict(identity))
            operator_identity.pop("identity_digest", None)
            operator_identity.pop("binding_digest", None)
            operator_identity.pop("finality", None)
            operator_identity["registry_snapshot_digest"] = str(operator_identity["registry_snapshot_digest"]).removeprefix("sha256:")
            parameters["identity"] = operator_identity
            inputs = copy.deepcopy(dict(parameters.get("inputs", {})))
            inputs["identity"] = operator_identity
            if unit_id == "OP-37" and isinstance(inputs.get("op29_temporal_receipt"), Mapping):
                inputs["op29_temporal_receipt"] = {
                    **copy.deepcopy(dict(inputs["op29_temporal_receipt"])),
                    "identity": operator_identity,
                }
            parameters["inputs"] = inputs
            parameters["input_digests"] = [digest_hex(inputs)]
        elif unit_id.startswith("GATE-"):
            parameters = {"subject_digest": identity["identity_digest"]}
        parameter_bindings[execution["node_id"]] = parameters
        bindings = copy.deepcopy(execution["input_bindings"])
        bound_names = {item["input_name"] for item in bindings}
        for name, value in parameters.items():
            if name in bound_names:
                continue
            source_ref = f"trusted-grounding-fixture:{execution['node_id']}:{name}"
            material = {"input_name": name, "source_kind": "user_parameter", "source_ref": source_ref, "bound_parameter_value": value}
            bindings.append({"input_name": name, "source_kind": "user_parameter", "source_ref": source_ref, "source_digest": digest_hex(material), "source_artifact_digest": None})
        design_nodes.append({
            "node_id": execution["node_id"],
            "execution_unit": {
                "unit_id": unit_id,
                "unit_kind": entry["unit_kind"],
                "unit_version": entry["unit_version"],
                "contract_digest": entry["contract_digest"],
                "unit_implementation_digest": entry["implementation_digest"],
                "unit_semantic_digest": entry["semantic_digest"],
                "atomic_capability_id": entry["atomic_capability_id"],
                "atomic_capability_version": entry["atomic_capability_version"],
                "capability_contract_digest": entry["capability_contract_digest"],
            },
            "depends_on": copy.deepcopy(execution["depends_on"]),
            "dependency_mode": execution["dependency_mode"],
            "requiredness": execution["requiredness"],
            "wave": wave,
            "input_bindings": bindings,
            "parameters_digest": digest_hex({"parameters": parameters}),
            "expected_output_schema_ref": entry["output_schema_refs"][0],
            "completeness_requirement": "complete",
            "incomplete_input_policy": "fail_closed",
            "monotonicity_contract_ref": None,
            "budget_allocation": budget,
            "permission_scope_id": "country_outage:control" if entry["unit_kind"] not in {"tool", "operator"} else f"country_outage:{'read' if entry['unit_kind']=='tool' else 'derive'}",
            "cancellation_policy": "cooperative",
        })
    plan_id = "plan_" + digest_hex({"goal": goal, "identity": design_identity, "nodes": design_nodes})
    definition = {
        "plan_id": plan_id, "plan_revision": 1, "parent_plan_revision": None, "plan_state": "admitted",
        "goal_digest": digest_hex(goal), "identity": design_identity,
        "registry_snapshot_id": dispatcher.admission.registry_snapshot_id,
        "registry_snapshot_digest": dispatcher.admission.snapshot_digest,
        "nodes": design_nodes, "dag_digest": digest_hex({"nodes": design_nodes}), "budget": budget,
        "answer_execution_policy": {"teacher_required": True, "mode": "sol_teacher_then_ds_student", "authorization_digest": None},
        "permission_set_digest": digest_hex({"scopes": sorted({node["permission_scope_id"] for node in design_nodes})}),
        "admission_receipt_digest": None,
    }
    # S1D-4 冻结语义 validator 合同身份；W5 自身实现字节摘要另写 execution projection receipt。
    validator_source_digest = "sha256:" + "a" * 64
    validator_contract_digest = "sha256:" + "9" * 64
    admission_base = {
        "receipt_kind": "plan_admission", "validator_id": "country_outage_p2_plan_admission_validator",
        "validator_version": "1.0.0", "validator_contract_digest": validator_contract_digest,
        "validator_implementation_digest": validator_source_digest,
        "plan_id": plan_id, "plan_revision": 1,
        "plan_subject_digest": digest_without_fields(definition, "admission_receipt_digest"),
        "identity_digest": design_identity["binding_digest"], "dag_digest": definition["dag_digest"],
        "registry_snapshot_id": dispatcher.admission.registry_snapshot_id, "registry_snapshot_digest": dispatcher.admission.snapshot_digest,
        "parameter_bindings_digest": digest_hex({"parameter_bindings": parameter_bindings}),
        "semantic_plan_receipt_digest": planning_receipt_binding["semantic_plan_receipt_digest"],
        "grounding_receipt_digest": planning_receipt_binding["grounding_receipt_digest"],
        "grounding_plan_projection_digest": planning_receipt_binding["grounding_plan_projection_digest"],
        "validated_unit_ids": [item["unit_id"] for item in execution_nodes],
        "semantic_checks": [
            "identity_and_time", "registry_entry_exact", "capability_exact", "input_output_schema_exact",
            "parameter_binding_recipe", "permission_budget", "dag_and_wave", "p2_1_denied",
        ],
        "monetary_budget_interpretation": "tool_operator_design_upper_not_model_cost_measurement",
        "disposition": "passed",
    }
    admission = {**admission_base, "receipt_digest": digest_hex(admission_base)}
    definition["admission_receipt_digest"] = admission["receipt_digest"]
    node_revisions = [{
        "node_id": item["node_id"], "execution_revision": 1, "parent_execution_revision": None,
        "state": "pending", "idempotency_key": f"{plan_id}:{item['node_id']}:1",
        "input_digest": digest_hex({"parameters": parameter_bindings[item["node_id"]]}),
        "result_digest": None, "receipt_digest": None, "failure_code": None,
    } for item in execution_nodes]
    snapshot = {
        "investigation_id": "design_" + digest_hex(plan_id), "investigation_revision": 1, "parent_investigation_revision": None,
        "plan_id": plan_id, "plan_revision": 1, "status": "pending", "node_execution_revisions": node_revisions,
        "evidence_graph_revision": None,
    }
    snapshot["snapshot_digest"] = digest_without_fields(snapshot, "snapshot_digest")
    instance = {
        "schema_version": "country_outage_p2_investigation_plan_v1",
        "plan_definition": definition, "investigation_snapshot": snapshot,
        "design_boundary": {"design_only": True, "runtime_implemented": False, "production_deployed": False},
    }
    validate_json_schema(instance, load_frozen_contract("investigation-plan"), "frozen InvestigationPlan")
    return instance, {
        "parameters": parameter_bindings,
        "admission_receipt": admission,
        "planning_receipt_binding": copy.deepcopy(dict(planning_receipt_binding)),
    }


def _validate_design_plan_semantics(
    instance: Mapping[str, Any],
    support: Mapping[str, Any],
    dispatcher: W5RegistryDispatcher,
) -> None:
    validate_json_schema(instance, load_frozen_contract("investigation-plan"), "frozen InvestigationPlan")
    definition = instance["plan_definition"]
    identity = validate_identity(definition["identity"], require_binding_digest=True)
    if definition["registry_snapshot_id"] != dispatcher.admission.registry_snapshot_id or definition["registry_snapshot_digest"] != dispatcher.admission.snapshot_digest:
        raise W5InvestigationError("plan_registry_mismatch", "Plan Registry 身份与 execution admission 不一致")
    runtime_nodes = []
    parameters_by_node = support["parameters"]
    unit_ids: list[str] = []
    for design_node in definition["nodes"]:
        unit = design_node["execution_unit"]
        unit_id = unit["unit_id"]
        entry = dispatcher.assert_allowed(unit_id)
        unit_ids.append(unit_id)
        exact = {
            "unit_kind": entry["unit_kind"], "contract_digest": entry["contract_digest"],
            "unit_implementation_digest": entry["implementation_digest"], "unit_semantic_digest": entry["semantic_digest"],
        }
        if any(unit.get(field) != expected for field, expected in exact.items()):
            raise W5InvestigationError("plan_registry_entry_drift", f"{unit_id} Registry/contract/implementation/semantic 漂移")
        parameters = parameters_by_node.get(design_node["node_id"])
        if not isinstance(parameters, Mapping) or design_node["parameters_digest"] != digest_hex({"parameters": parameters}):
            raise W5InvestigationError("plan_parameters_digest_mismatch", f"{design_node['node_id']} 参数摘要不一致")
        bindings = design_node["input_bindings"]
        if len(bindings) != len({item["input_name"] for item in bindings}):
            raise W5InvestigationError("plan_binding_duplicate", "Plan input binding 重复")
        for binding in bindings:
            target: Any = parameters
            for part in binding["input_name"].split("."):
                if not isinstance(target, Mapping) or part not in target:
                    raise W5InvestigationError("plan_bound_parameter_missing", "Plan binding 没有对应参数值")
                target = target[part]
            recipe = {"input_name": binding["input_name"], "source_kind": binding["source_kind"], "source_ref": binding["source_ref"], "bound_parameter_value": target}
            if binding["source_digest"] != digest_hex(recipe):
                raise W5InvestigationError("plan_binding_digest_mismatch", "Plan binding source_digest 无法重算")
        if set(parameters) - {item["input_name"] for item in bindings}:
            raise W5InvestigationError("plan_parameter_binding_missing", "Plan 参数未全部具名绑定")
        expected_permission = "country_outage:control" if entry["unit_kind"] not in {"tool", "operator"} else f"country_outage:{'read' if entry['unit_kind']=='tool' else 'derive'}"
        if design_node["permission_scope_id"] != expected_permission or design_node["budget_allocation"] != definition["budget"]:
            raise W5InvestigationError("plan_permission_budget_mismatch", f"{unit_id} permission/budget 漂移")
        if design_node["expected_output_schema_ref"] not in entry["output_schema_refs"]:
            raise W5InvestigationError("plan_output_schema_mismatch", f"{unit_id} output schema 未准入")
        runtime_nodes.append({
            "node_id": design_node["node_id"], "unit_id": unit_id, "depends_on": design_node["depends_on"],
            "dependency_mode": design_node["dependency_mode"], "requiredness": design_node["requiredness"],
            "parameters": copy.deepcopy(dict(parameters)), "input_bindings": copy.deepcopy(design_node["input_bindings"]),
        })
    validate_static_dag(runtime_nodes, dispatcher.admission.execution_allowed_unit_ids)
    if definition["dag_digest"] != digest_hex({"nodes": definition["nodes"]}) or definition["identity"]["binding_digest"] != identity["binding_digest"]:
        raise W5InvestigationError("plan_dag_or_identity_digest_mismatch", "Plan DAG/identity 摘要不一致")
    receipt = support["admission_receipt"]
    planning_binding = support["planning_receipt_binding"]
    if (
        receipt["plan_subject_digest"] != digest_without_fields(definition, "admission_receipt_digest")
        or receipt["validated_unit_ids"] != unit_ids
        or receipt["receipt_digest"] != digest_without_fields(receipt, "receipt_digest")
        or any(receipt.get(field) != value for field, value in planning_binding.items())
    ):
        raise W5InvestigationError("plan_admission_receipt_invalid", "Host Plan admission receipt 无法重算")


def bind_output_to_argument(parameters: Mapping[str, Any], records: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if set(parameters) != {"source_node_id", "source_path", "target_name"}:
        raise W5InvestigationError("plan_capability_parameters_invalid", "PLAN-CAP-01 参数必须闭合")
    source = records.get(str(parameters["source_node_id"]))
    if source is None or source.get("state") not in {"succeeded", "reused"}:
        raise W5InvestigationError("plan_capability_source_unavailable", "PLAN-CAP-01 源节点未成功")
    value: Any = source.get("result")
    path = parameters["source_path"]
    if not isinstance(path, str) or not path or "[" in path:
        raise W5InvestigationError("plan_capability_path_invalid", "source_path 仅允许对象字段路径")
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise W5InvestigationError("plan_capability_path_missing", "source_path 不存在")
        value = value[part]
    if isinstance(value, (Mapping, list)):
        raise W5InvestigationError("plan_capability_fanout_forbidden", "PLAN-CAP-01 只能绑定单一标量")
    return {"argument_name": parameters["target_name"], "value": copy.deepcopy(value), "value_digest": digest_hex(value), "source_node_id": source["node_id"], "source_path": path}


def validate_plan_admission(plan: Mapping[str, Any], allowed_units: Sequence[str]) -> dict[str, Any]:
    validate_static_dag(plan["nodes"], allowed_units)
    return {"gate_id": "GATE-01", "status": "passed", "subject_digest": plan["plan_digest"]}


def validate_identity_gate(identity: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_identity(identity)
    return {"gate_id": "GATE-02", "status": "passed", "subject_digest": validated["identity_digest"]}


def validate_registry_gate(plan: Mapping[str, Any], dispatcher: W5RegistryDispatcher) -> dict[str, Any]:
    for node in plan["nodes"]:
        dispatcher.assert_allowed(node["unit_id"])
    return {"gate_id": "GATE-03", "status": "passed", "subject_digest": dispatcher.admission.admission_receipt_digest}


def validate_authorization_gate(owner_principal_id: str, principal_id: str) -> dict[str, Any]:
    if owner_principal_id != principal_id:
        raise PermissionError("调查所有权不匹配")
    return {"gate_id": "GATE-05", "status": "passed", "subject_digest": digest_hex({"owner_principal_id": owner_principal_id, "principal_id": principal_id})}


def boundary_response() -> dict[str, Any]:
    return {
        "status": "bounded",
        "limitations": [
            {"code": "rrc25_control_plane_only", "severity": "warning", "message_zh": "仅表达 RRC25 BGP 控制面观测。"},
            {"code": "no_rca_or_impact_claim", "severity": "warning", "message_zh": "不推断原因、责任、用户影响或恢复。"},
        ],
    }


def build_local_fixture_runtime(
    repository_root_path: str | Path,
    store_root: str | Path,
    *,
    event_reference: str = "country_outage/2026-02-27 00:00:00/IR/1/r",
    planning_grounding_port: PlanningGroundingPort,
    model_port: ModelTurnPort | None = None,
    page_token_key: bytes = b"country-outage-p2-s1-w5-fixture-page-key",
    query_receipt_key: bytes = b"country-outage-p2-s1-w5-fixture-receipt-key",
) -> "CountryOutageP2S1InvestigationRuntime":
    """从冻结 source-store manifest 构造可供 evidence runner 使用的正式 runtime。"""

    from .country_outage_p2_s1_source_store import CountryOutageP2S1SourceStore
    from .country_outage_p2_s1_tools import CountryOutageP2S1Tools

    root = Path(repository_root_path).resolve()
    contract_root = root / "contracts/data/country-outage-p2-s1"
    source_root = contract_root / "test-fixture/source-store"
    source_store = CountryOutageP2S1SourceStore(source_root, contract_root=contract_root)
    manifest_identity = copy.deepcopy(dict(source_store.manifest["identity"]))
    manifest_identity.pop("grid_seconds", None)
    manifest_identity.update({
        "registry_snapshot_id": "w4-binding-placeholder",
        "registry_snapshot_digest": "0" * 64,
        "binding_generation": 1,
    })
    store = ContentAddressedStore(store_root)
    tools = CountryOutageP2S1Tools(
        source_store,
        page_token_key=page_token_key,
        query_receipt_key=query_receipt_key,
    )
    dispatcher = W5RegistryDispatcher(tools, store)
    return CountryOutageP2S1InvestigationRuntime(
        store=store,
        dispatcher=dispatcher,
        identity_records={event_reference: manifest_identity},
        planning_grounding_port=planning_grounding_port,
        model_port=model_port,
    )


class LocalFixtureSidecarModelPort:
    """loopback-only W5 fixture Sidecar adapter；不允许传递模型或证据。"""

    def __init__(
        self,
        *,
        base_url: str,
        shared_token: str,
        fixture_id: str,
        expected_fixture_digest: str,
        expected_identity: Mapping[str, Any],
        timeout_seconds: float = 30.0,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("W5 Sidecar 只允许无用户信息的 loopback HTTP")
        if len(shared_token) < 24 or not fixture_id or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_fixture_digest):
            raise ValueError("W5 Sidecar 受信配置无效")
        self.base_url = base_url.rstrip("/")
        self.shared_token = shared_token
        self.fixture_id = fixture_id
        self.expected_fixture_digest = expected_fixture_digest
        self.expected_identity = copy.deepcopy(dict(expected_identity))
        self.timeout_seconds = timeout_seconds

    def _json(self, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        raw = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=raw,
            method="GET" if body is None else "POST",
            headers={"Authorization": f"Bearer {self.shared_token}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise W5InvestigationError("model_sidecar_failed", f"W5 Sidecar HTTP {response.status}", status_code=503)
                payload = response.read(8 * 1024 * 1024 + 1)
        except (HTTPError, URLError, TimeoutError) as error:
            raise W5InvestigationError("model_sidecar_unavailable", "W5 fixture Sidecar 不可用", status_code=503, retryable=True) from error
        if len(payload) > 8 * 1024 * 1024:
            raise W5InvestigationError("model_sidecar_response_too_large", "W5 Sidecar 响应超限", status_code=502)
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise W5InvestigationError("model_sidecar_response_invalid", "W5 Sidecar 响应非 JSON", status_code=502) from error
        if not isinstance(value, dict):
            raise W5InvestigationError("model_sidecar_response_invalid", "W5 Sidecar 响应必须是对象", status_code=502)
        return value

    def create_turn(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        ready = self._json("/readyz")
        if ready.get("ready") is not True or ready.get("execution_mode") != "trusted_fixture_replay_only" or ready.get("external_provider_enabled") is not False or ready.get("production_deployed") is not False:
            raise W5InvestigationError("model_sidecar_boundary_invalid", "W5 Sidecar readiness 边界不可信", status_code=503)
        key = "turn-" + digest_hex({"fixture_id": self.fixture_id, "request": request})[:48]
        result = self._json("/runs", {"fixture_id": self.fixture_id, "idempotency_key": key})
        if result.get("schema_version") != "country_outage_p2_s1_w5_run_result_v1" or result.get("fixture_id") != self.fixture_id or result.get("fixture_digest") != self.expected_fixture_digest:
            raise W5InvestigationError("model_sidecar_binding_mismatch", "W5 Sidecar fixture 身份漂移", status_code=502)
        flow = result.get("flow")
        if not isinstance(flow, Mapping):
            raise W5InvestigationError("model_sidecar_flow_unavailable", "Sol planning 未产生可发布流", status_code=503, retryable=True)
        binding = flow.get("binding")
        if not isinstance(binding, Mapping):
            binding = flow.get("shared_answer_binding")
        identity_fields = ("incident_id", "publication_id", "publication_revision", "collector_id", "cohort_id")
        if not isinstance(binding, Mapping) or any(binding.get(field) != self.expected_identity.get(field) for field in identity_fields):
            raise W5InvestigationError("model_sidecar_identity_mismatch", "Sol→Host→DS 流未绑定同一 investigation 身份", status_code=502)
        if flow.get("final_disposition") != "aligned_published" or not isinstance(flow.get("published_answer"), Mapping):
            raise W5InvestigationError("model_sidecar_answer_not_publishable", "DS 答案未通过 Host 硬门", status_code=409)
        return {
            "schema_version": "country_outage_p2_s1_w5_model_turn_v1",
            "fixture_id": self.fixture_id,
            "fixture_digest": self.expected_fixture_digest,
            "flow": copy.deepcopy(dict(flow)),
            "model_call_summary": copy.deepcopy(result.get("model_call_summary")),
            "production_deployed": False,
        }


def _event_country(event_reference: str) -> str:
    match = re.search(r"/([A-Z]{2})/[1-9]\d*/r$", event_reference)
    if match is None:
        raise W5InvestigationError("event_reference_invalid", "event_reference 缺少国家身份", status_code=400)
    return match.group(1)


def _principal(principal: Mapping[str, Any], country: str) -> dict[str, Any]:
    required_scope = f"country_outage_event_read:{country}"
    if isinstance(principal, Mapping) and set(principal) == {"user_id", "authorization_scope"}:
        scope = principal.get("authorization_scope")
        if not isinstance(scope, str) or not scope.strip():
            raise W5InvestigationError("permission_denied", "authorization_scope 不能为空", status_code=403)
        try:
            return validate_principal(principal, required_scope)
        except W5ContractError as error:
            raise W5InvestigationError(error.code, str(error), status_code=403) from error
    if isinstance(principal, Mapping) and isinstance(principal.get("scopes"), list) and principal["scopes"]:
        try:
            return validate_principal(principal, required_scope)
        except W5ContractError as error:
            raise W5InvestigationError(error.code, str(error), status_code=403) from error
    raise W5InvestigationError("principal_invalid", "principal 无效", status_code=403)


def _exact(value: Mapping[str, Any], fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise W5InvestigationError("request_fields_invalid", f"{label} 字段集合不闭合", status_code=400)
    return copy.deepcopy(dict(value))


def _evidence_refs(result: Any) -> list[Any]:
    if isinstance(result, Mapping):
        refs = result.get("evidence_refs", [])
        if isinstance(refs, list):
            return copy.deepcopy(refs)
    return []


class CountryOutageP2S1InvestigationRuntime:
    """API 与 Host 可直接调用的实体 runtime。"""

    def __init__(
        self,
        *,
        store: ContentAddressedStore,
        dispatcher: W5RegistryDispatcher,
        identity_records: Mapping[str, Mapping[str, Any]],
        planning_grounding_port: PlanningGroundingPort | None = None,
        model_port: ModelTurnPort | None = None,
    ) -> None:
        if dispatcher._store is not store:  # Host 不得拆分 admission 与事务 Store。
            raise TypeError("dispatcher 与 runtime 必须共享同一 trusted store")
        self.store = store
        self.dispatcher = dispatcher
        self.result_sets = ResultSetManager(store, dispatcher._tools)
        self.graphs = EvidenceGraphManager(store, dispatcher)
        self.delivery = DeliveryManager(store, dispatcher)
        self.model_port = model_port
        self.identity_records = {str(key): copy.deepcopy(dict(value)) for key, value in identity_records.items()}
        self.planning_grounding_port = planning_grounding_port

    def _admission_event(
        self,
        *,
        execution_id: str,
        investigation_id: str,
        event_kind: str,
        artifact_kind: str,
        artifact_digest: str,
        design_validator_receipt_digest: str | None,
        runtime_admission_receipt_digest: str | None,
        parameter_bindings_digest: str,
        action_subject_digest: str,
    ) -> dict[str, Any]:
        prior = sorted(
            (item for item in self.store.list_json("admission-event") if item.get("execution_id") == execution_id),
            key=lambda item: item["sequence"],
        )
        base = {
            "schema_version": "country_outage_p2_s1_w5_admission_event_v1",
            "event_kind": event_kind,
            "execution_id": execution_id,
            "investigation_id": investigation_id,
            "sequence": len(prior) + 1,
            "previous_event_digest": prior[-1]["event_digest"] if prior else None,
            "artifact_kind": artifact_kind,
            "artifact_digest": artifact_digest,
            "design_validator_receipt_digest": design_validator_receipt_digest,
            "runtime_admission_receipt_digest": runtime_admission_receipt_digest,
            "registry_snapshot_digest": self.dispatcher.admission.snapshot_digest,
            "parameter_bindings_digest": parameter_bindings_digest,
            "action": event_kind,
            "action_subject_digest": action_subject_digest,
        }
        event = {**base, "event_digest": digest_prefixed(base)}
        self.store.put_json("admission-event", event)
        return event

    def _admission_execution_context(
        self,
        *,
        investigation_id: str,
        base_investigation_revision: int,
        idempotency_key: str,
        plan_artifact_digest: str,
    ) -> dict[str, Any]:
        """冻结一条execution事件链的可重算内容身份，不包含物理Store路径。"""

        idempotency_key_digest = digest_prefixed({"idempotency_key": idempotency_key})
        identity = {
            "investigation_id": investigation_id,
            "base_investigation_revision": base_investigation_revision,
            "idempotency_key_digest": idempotency_key_digest,
            "plan_artifact_digest": plan_artifact_digest,
            "registry_snapshot_digest": self.dispatcher.admission.snapshot_digest,
        }
        context = {
            "schema_version": "country_outage_p2_s1_w5_admission_event_context_v1",
            "execution_id": "w5-execution-sha256:" + digest_hex(identity),
            **identity,
        }
        self.store.put_json("admission-event-context", context)
        return context

    def _runtime_artifact_admission(
        self,
        *,
        artifact_kind: str,
        design_artifact: Mapping[str, Any],
        frozen_design_validator_receipt_digest: str,
        runtime_receipt_kind: str,
        validator_id: str,
        implementation_path: Path,
        parameter_bindings_digest: str,
        authorizes_dispatcher_execution: bool,
    ) -> dict[str, Any]:
        schema_name = {"InvestigationPlan": "investigation-plan", "ResultSet": "result-set", "EvidenceGraph": "evidence-graph"}[artifact_kind]
        schema_path = repository_root() / f"contracts/agent/country-outage-p2-s1-execution-unit-design/{schema_name}.schema.json"
        import hashlib
        design_artifact_digest = self.store.put_json(f"design-{artifact_kind.lower()}", design_artifact)["object_digest"]
        runtime_subject_digest = self.store.put_json(f"runtime-{artifact_kind.lower()}", design_artifact)["object_digest"]
        base = {
            "schema_version": "country_outage_p2_s1_w5_runtime_artifact_admission_receipt_v1",
            "artifact_kind": artifact_kind,
            "design_artifact_digest": design_artifact_digest,
            "runtime_subject_digest": runtime_subject_digest,
            "frozen_design_validator_receipt_digest": frozen_design_validator_receipt_digest,
            "runtime_receipt_kind": runtime_receipt_kind,
            "validator_id": validator_id, "validator_version": "1.0.0",
            "validator_contract_digest": "sha256:" + hashlib.sha256(schema_path.read_bytes()).hexdigest(),
            "validator_implementation_digest": "sha256:" + hashlib.sha256(implementation_path.read_bytes()).hexdigest(),
            "registry_snapshot_digest": self.dispatcher.admission.snapshot_digest,
            "parameter_bindings_digest": parameter_bindings_digest if parameter_bindings_digest.startswith("sha256:") else "sha256:" + parameter_bindings_digest,
            "trusted_store_snapshot_digest": digest_prefixed({
                "registry_snapshot_digest": self.dispatcher.admission.snapshot_digest,
                "design_artifact_digest": design_artifact_digest,
                "runtime_subject_digest": runtime_subject_digest,
                "frozen_design_validator_receipt_digest": frozen_design_validator_receipt_digest,
            }),
            "trusted_store_resolved": True,
            "authorizes_dispatcher_execution": authorizes_dispatcher_execution,
            "disposition": "passed",
        }
        receipt = {**base, "receipt_digest": digest_prefixed(base)}
        ref = self.store.put_json("runtime-artifact-admission", receipt)
        if self.store.get_json("runtime-artifact-admission", ref["object_digest"]) != receipt:
            raise W5InvestigationError("runtime_artifact_admission_unresolved", f"{artifact_kind} runtime admission 未从受信store解析")
        return receipt

    def _frozen_design_semantic_admission(
        self,
        *,
        artifact_kind: str,
        artifact: Mapping[str, Any],
        plan_support: Mapping[str, Any] | None = None,
        graph_context: Mapping[str, Any] | None = None,
        result_sets: Mapping[tuple[str, int], Mapping[str, Any]] | None = None,
        previous_graph: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行冻结design Hook的精确验收入口并写A层内容寻址回执。

        W5只允许仓库固定路径与固定入口，不接收调用方函数/callback。正式Host的
        backend semantic validator仍先执行；本层是同候选冻结design Oracle replay。
        """

        hook_path = repository_root() / ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py"
        spec = importlib.util.spec_from_file_location("country_outage_p2_s1_w5_frozen_design_alignment", hook_path)
        if spec is None or spec.loader is None:
            raise W5InvestigationError("frozen_design_validator_unavailable", "冻结design validator不可加载")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        schema_name = {"InvestigationPlan": "investigation-plan", "ResultSet": "result-set", "EvidenceGraph": "evidence-graph"}[artifact_kind]
        entrypoint_name = {
            "InvestigationPlan": "validate_investigation_plan_instance",
            "ResultSet": "validate_result_set_instance",
            "EvidenceGraph": "validate_evidence_graph_instance",
        }[artifact_kind]
        schema = load_frozen_contract(schema_name)
        if artifact_kind == "InvestigationPlan":
            if not isinstance(plan_support, Mapping):
                raise W5InvestigationError("plan_semantic_support_missing", "Plan replay缺少受信support")
            admissions = {
                item["receipt_digest"]: item for item in self.store.list_json("plan-admission-receipt")
                if isinstance(item.get("receipt_digest"), str)
            }
            hook.validate_investigation_plan_instance(
                artifact, schema=schema, trusted_registry_store=self.dispatcher.trusted_registry_store,
                trusted_admission_receipt_store=admissions, parameter_bindings=plan_support["parameters"],
            )
        elif artifact_kind == "ResultSet":
            resolved_members = validate_result_set(artifact, self.store)
            receipt_store = {
                item["receipt_digest"]: item for item in [
                    *self.store.list_json("receipt"), *self.store.list_json("receipt-candidate")
                ]
                if isinstance(item.get("receipt_digest"), str)
            }
            hook.validate_result_set_instance(
                artifact, schema=schema, resolved_members=resolved_members,
                trusted_registry_store=self.dispatcher.trusted_registry_store, receipt_store=receipt_store,
            )
        else:
            if not isinstance(graph_context, Mapping) or not isinstance(result_sets, Mapping):
                raise W5InvestigationError("graph_semantic_support_missing", "Graph replay缺少受信closure")
            receipt_store = {
                item["receipt_digest"]: item for item in [
                    *self.store.list_json("receipt"), *self.store.list_json("receipt-candidate")
                ]
                if isinstance(item.get("receipt_digest"), str)
            }
            result_set_members = {
                key: {item[value["member_identity"]]: item for item in validate_result_set(value, self.store)}
                for key, value in result_sets.items()
            }
            hook.validate_evidence_graph_instance(
                artifact, schema=schema, trusted_registry_store=self.dispatcher.trusted_registry_store,
                result_sets=result_sets, plan_definition=graph_context["plan_definition"],
                investigation_snapshot=graph_context["investigation_snapshot"], receipt_store=receipt_store,
                result_set_members=result_set_members, previous_graph=previous_graph,
            )
        schema_path = repository_root() / f"contracts/agent/country-outage-p2-s1-execution-unit-design/{schema_name}.schema.json"
        artifact_digest = self.store.put_json(f"design-{artifact_kind.lower()}", artifact)["object_digest"]
        validator_id = f"country_outage_p2_s1_w5_{schema_name.replace('-', '_')}_semantic_validator"
        entrypoint = f".codex/hooks/country_outage_agent_p2_s1_design_alignment.py::{entrypoint_name}"
        implementation_digest = "sha256:" + hashlib.sha256(hook_path.read_bytes()).hexdigest()
        trusted_snapshot_digest = digest_prefixed({
            "registry_snapshot_digest": self.dispatcher.admission.snapshot_digest,
            "artifact_digest": artifact_digest,
        })
        base = {
            "schema_version": "country_outage_p2_s1_w5_design_semantic_validator_receipt_v1",
            "artifact_kind": artifact_kind, "artifact_digest": artifact_digest,
            "validator_id": validator_id, "validator_version": "1.0.0",
            "validator_entrypoints": [entrypoint],
            "validator_contract_digest": "sha256:" + hashlib.sha256(schema_path.read_bytes()).hexdigest(),
            "validator_implementation_digests": {entrypoint: implementation_digest},
            "trusted_store_snapshot_digest": trusted_snapshot_digest,
            "draft_schema_error_codes": [], "semantic_error_codes": [], "disposition": "passed",
        }
        receipt = {**base, "receipt_digest": digest_hex(base)}
        self.store.put_json("design-semantic-validator-receipt", receipt)
        return receipt

    def _resolve_runtime_artifact_admission(
        self,
        *,
        receipt_digest: str,
        artifact_kind: str,
        artifact: Mapping[str, Any],
        parameter_bindings_digest: str,
        authorizes_dispatcher_execution: bool,
    ) -> dict[str, Any]:
        """从受信store严格解析唯一A/B链并重新绑定当前合同与实现字节。"""

        all_runtime_receipts = self.store.list_json("runtime-artifact-admission")
        matches = [item for item in all_runtime_receipts if item.get("receipt_digest") == receipt_digest]
        if len(matches) != 1:
            raise W5InvestigationError("runtime_artifact_admission_not_unique", "runtime admission 缺失或重复", status_code=403)
        receipt = matches[0]
        if receipt.get("receipt_digest") != digest_prefixed({key: value for key, value in receipt.items() if key != "receipt_digest"}):
            raise W5InvestigationError("runtime_artifact_admission_digest_mismatch", "runtime admission 自摘要无效", status_code=403)
        schema_name = {"InvestigationPlan": "investigation-plan", "ResultSet": "result-set", "EvidenceGraph": "evidence-graph"}[artifact_kind]
        runtime_kind, validator_id, implementation_name, expected_authorizes = {
            "InvestigationPlan": ("plan_admission", "country_outage_p2_s1_w5_host_plan_admission_validator", "country_outage_p2_s1_investigation_runtime.py", True),
            "ResultSet": ("result_set_freeze", "country_outage_p2_s1_w5_result_set_freeze_validator", "country_outage_p2_s1_result_set.py", False),
            "EvidenceGraph": ("evidence_graph_commit", "country_outage_p2_s1_w5_evidence_graph_commit_validator", "country_outage_p2_s1_evidence_graph.py", False),
        }[artifact_kind]
        schema_path = repository_root() / f"contracts/agent/country-outage-p2-s1-execution-unit-design/{schema_name}.schema.json"
        implementation_path = Path(__file__).with_name(implementation_name)
        artifact_digest = digest_prefixed(artifact)
        expected_parameter_digest = parameter_bindings_digest if parameter_bindings_digest.startswith("sha256:") else "sha256:" + parameter_bindings_digest
        expected = {
            "schema_version": "country_outage_p2_s1_w5_runtime_artifact_admission_receipt_v1",
            "artifact_kind": artifact_kind, "design_artifact_digest": artifact_digest,
            "runtime_subject_digest": artifact_digest, "runtime_receipt_kind": runtime_kind,
            "validator_id": validator_id, "validator_version": "1.0.0",
            "validator_contract_digest": "sha256:" + hashlib.sha256(schema_path.read_bytes()).hexdigest(),
            "validator_implementation_digest": "sha256:" + hashlib.sha256(implementation_path.read_bytes()).hexdigest(),
            "registry_snapshot_digest": self.dispatcher.admission.snapshot_digest,
            "parameter_bindings_digest": expected_parameter_digest,
            "trusted_store_resolved": True,
            "authorizes_dispatcher_execution": expected_authorizes,
            "disposition": "passed",
        }
        if authorizes_dispatcher_execution != expected_authorizes or any(receipt.get(field) != value for field, value in expected.items()):
            raise W5InvestigationError("runtime_artifact_admission_binding_mismatch", f"{artifact_kind} runtime admission绑定漂移", status_code=403)
        parallel = [
            item for item in all_runtime_receipts
            if all(item.get(field) == value for field, value in {
                "artifact_kind": artifact_kind, "design_artifact_digest": artifact_digest,
                "runtime_receipt_kind": runtime_kind, "registry_snapshot_digest": self.dispatcher.admission.snapshot_digest,
                "parameter_bindings_digest": expected_parameter_digest,
            }.items())
        ]
        if len(parallel) != 1 or parallel[0].get("receipt_digest") != receipt_digest:
            raise W5InvestigationError("runtime_artifact_admission_parallel_signature", "同一subject存在平行B层签名", status_code=403)
        design_stored = self.store.get_json(f"design-{artifact_kind.lower()}", receipt["design_artifact_digest"])
        runtime_stored = self.store.get_json(f"runtime-{artifact_kind.lower()}", receipt["runtime_subject_digest"])
        if design_stored != artifact or runtime_stored != artifact:
            raise W5InvestigationError("runtime_artifact_subject_unresolved", f"{artifact_kind} subject未从受信store解析", status_code=403)
        a_matches = [
            item for item in self.store.list_json("design-semantic-validator-receipt")
            if item.get("receipt_digest") == receipt.get("frozen_design_validator_receipt_digest")
        ]
        if len(a_matches) != 1:
            raise W5InvestigationError("frozen_design_validator_receipt_not_unique", "A层validator receipt缺失或重复", status_code=403)
        a_receipt = a_matches[0]
        hook_path = repository_root() / ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py"
        entrypoint_name = {
            "InvestigationPlan": "validate_investigation_plan_instance",
            "ResultSet": "validate_result_set_instance",
            "EvidenceGraph": "validate_evidence_graph_instance",
        }[artifact_kind]
        expected_entrypoint = f".codex/hooks/country_outage_agent_p2_s1_design_alignment.py::{entrypoint_name}"
        expected_a_validator_id = f"country_outage_p2_s1_w5_{schema_name.replace('-', '_')}_semantic_validator"
        expected_hook_digest = "sha256:" + hashlib.sha256(hook_path.read_bytes()).hexdigest()
        if (
            a_receipt.get("receipt_digest") != digest_hex({key: value for key, value in a_receipt.items() if key != "receipt_digest"})
            or a_receipt.get("artifact_kind") != artifact_kind
            or a_receipt.get("artifact_digest") != artifact_digest
            or a_receipt.get("validator_contract_digest") != expected["validator_contract_digest"]
            or a_receipt.get("validator_id") != expected_a_validator_id
            or a_receipt.get("validator_version") != "1.0.0"
            or a_receipt.get("validator_entrypoints") != [expected_entrypoint]
            or a_receipt.get("validator_implementation_digests") != {expected_entrypoint: expected_hook_digest}
            or a_receipt.get("disposition") != "passed"
            or a_receipt.get("draft_schema_error_codes") != []
            or a_receipt.get("semantic_error_codes") != []
        ):
            raise W5InvestigationError("frozen_design_validator_receipt_binding_mismatch", "A层validator receipt绑定漂移", status_code=403)
        trusted_digest = digest_prefixed({
            "registry_snapshot_digest": self.dispatcher.admission.snapshot_digest,
            "design_artifact_digest": artifact_digest,
            "runtime_subject_digest": artifact_digest,
            "frozen_design_validator_receipt_digest": a_receipt["receipt_digest"],
        })
        if receipt.get("trusted_store_snapshot_digest") != trusted_digest:
            raise W5InvestigationError("runtime_artifact_store_snapshot_mismatch", "runtime admission受信store摘要漂移", status_code=403)
        return copy.deepcopy(receipt)

    def _identity(self, event_reference: str, publication_id: str, revision: int) -> dict[str, Any]:
        raw = self.identity_records.get(event_reference)
        if raw is None:
            raise W5InvestigationError("event_identity_not_found", "未找到已封存的事件身份", status_code=404)
        identity = copy.deepcopy(raw)
        identity.pop("binding_digest", None)
        identity.pop("identity_digest", None)
        if identity.get("publication_id") != publication_id or identity.get("publication_revision") != revision:
            raise W5InvestigationError("publication_identity_mismatch", "publication_id/revision 与事件引用不一致", status_code=409)
        identity["registry_snapshot_id"] = self.dispatcher.admission.registry_snapshot_id
        identity["registry_snapshot_digest"] = self.dispatcher.admission.snapshot_digest
        identity["binding_generation"] = int(identity.get("binding_generation", 0)) + 1
        identity["identity_digest"] = digest_without_fields(identity, "identity_digest")
        return validate_identity(identity)

    def _plan(self, identity: Mapping[str, Any], goal: str) -> dict[str, Any]:
        if self.planning_grounding_port is None:
            raise W5InvestigationError("planning_grounding_port_not_configured", "create 缺少受信 Sol planning→Host Grounding port", status_code=503)
        resolved = self.planning_grounding_port.resolve(goal=goal, identity=copy.deepcopy(dict(identity)))
        required = {"fixture_digest", "trusted_grounding_plan_projection", "semantic_plan_receipt", "grounding_receipt"}
        if not isinstance(resolved, Mapping) or set(resolved) != required:
            raise W5InvestigationError("planning_grounding_resolution_invalid", "planning/grounding resolver 输出不闭合")
        projection = resolved["trusted_grounding_plan_projection"]
        if not isinstance(projection, Mapping) or projection.get("schema_version") != "country_outage_p2_grounding_plan_projection_v2":
            raise W5InvestigationError("planning_grounding_projection_invalid", "grounding projection v2缺失")
        recipe = projection.get("grounded_execution_recipe")
        if not isinstance(recipe, Mapping) or recipe.get("recipe_digest") != digest_prefixed({key: value for key, value in recipe.items() if key != "recipe_digest"}):
            raise W5InvestigationError("planning_grounding_recipe_invalid", "grounded recipe摘要无法重算")
        plan_nodes = [{
            "node_id": item["node_id"], "unit_id": item["unit_id"], "depends_on": copy.deepcopy(item["depends_on"]),
            "dependency_mode": item["dependency_mode"], "requiredness": item["requiredness"],
            "parameters": copy.deepcopy(item["parameters"]), "input_bindings": copy.deepcopy(item["input_binding_sources"]),
        } for item in recipe["nodes"]]
        semantic = resolved["semantic_plan_receipt"]
        grounding = resolved["grounding_receipt"]
        if semantic.get("fixture_digest") != resolved["fixture_digest"] or grounding.get("fixture_digest") != resolved["fixture_digest"] or semantic.get("goal_digest") != digest_hex(goal) or grounding.get("identity_digest") != identity["identity_digest"] or semantic.get("receipt_digest") != digest_without_fields(semantic, "receipt_digest") or grounding.get("receipt_digest") != digest_without_fields(grounding, "receipt_digest"):
            raise W5InvestigationError("planning_grounding_binding_mismatch", "Sol semantic plan/Host grounding receipt 身份或摘要不一致")
        self.store.put_json("receipt", semantic)
        self.store.put_json("receipt", grounding)
        validate_static_dag(plan_nodes, self.dispatcher.admission.execution_allowed_unit_ids)
        design_plan, design_support = _design_plan_instance(
            identity=identity, goal=goal, execution_nodes=plan_nodes, dispatcher=self.dispatcher,
            planning_receipt_binding={
                "semantic_plan_receipt_digest": semantic["receipt_digest"],
                "grounding_receipt_digest": grounding["receipt_digest"],
                "grounding_plan_projection_digest": projection["grounding_plan_projection_digest"],
            },
        )
        _validate_design_plan_semantics(design_plan, design_support, self.dispatcher)
        admission_ref = self.store.put_json("plan-admission-receipt", design_support["admission_receipt"])
        resolved_admission = self.store.get_json("plan-admission-receipt", admission_ref["object_digest"])
        if resolved_admission != design_support["admission_receipt"]:
            raise W5InvestigationError("plan_admission_receipt_resolution_failed", "Host trusted store 无法解析 Plan admission receipt")
        design_validator_receipt = self._frozen_design_semantic_admission(
            artifact_kind="InvestigationPlan", artifact=design_plan, plan_support=design_support,
        )
        runtime_admission = self._runtime_artifact_admission(
            artifact_kind="InvestigationPlan", design_artifact=design_plan,
            frozen_design_validator_receipt_digest=design_validator_receipt["receipt_digest"],
            runtime_receipt_kind="plan_admission", validator_id="country_outage_p2_s1_w5_host_plan_admission_validator",
            implementation_path=Path(__file__), parameter_bindings_digest=digest_hex({"parameter_bindings": design_support["parameters"]}),
            authorizes_dispatcher_execution=True,
        )
        design_ref = self.store.put_json("design-investigation-plan", design_plan)
        identity_digest = identity["identity_digest"]
        plan_id = design_plan["plan_definition"]["plan_id"]
        projection_base = {
            "receipt_kind": "w5_execution_projection", "source_design_plan_digest": design_ref["object_digest"],
            "source_plan_id": plan_id, "source_plan_revision": 1,
            "identity_digest": identity_digest, "goal_digest": digest_hex(goal),
            "execution_nodes_digest": digest_hex({"nodes": plan_nodes}),
            "parameter_bindings_digest": digest_hex({"parameter_bindings": design_support["parameters"]}),
            "disposition": "projected_without_business_transformation",
        }
        projection = {**projection_base, "receipt_digest": digest_hex(projection_base)}
        self.store.put_json("receipt", projection)
        base = {
            "schema_version": "country_outage_p2_s1_w5_investigation_plan_v1",
            "plan_id": plan_id,
            "plan_revision": 1,
            "plan_state": "admitted",
            "goal": goal,
            "identity_digest": identity_digest,
            "registry_snapshot_id": self.dispatcher.admission.registry_snapshot_id,
            "registry_snapshot_digest": self.dispatcher.admission.snapshot_digest,
            "nodes": copy.deepcopy(plan_nodes),
            "planning_fixture_digest": resolved["fixture_digest"],
            "semantic_plan_receipt_digest": semantic["receipt_digest"],
            "grounding_receipt_digest": grounding["receipt_digest"],
            "source_design_plan_digest": design_ref["object_digest"],
            "design_plan_definition": copy.deepcopy(design_plan["plan_definition"]),
            "source_plan_admission_receipt_digest": design_support["admission_receipt"]["receipt_digest"],
            "execution_projection_receipt_digest": projection["receipt_digest"],
            "runtime_plan_admission_receipt_digest": runtime_admission["receipt_digest"],
            "design_parameter_bindings": copy.deepcopy(design_support["parameters"]),
            "design_plan_nodes": copy.deepcopy(design_plan["plan_definition"]["nodes"]),
        }
        return {**base, "plan_digest": digest_hex(base)}

    def _load(self, investigation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        pointer = self.store.read_pointer("investigation", investigation_id)
        if pointer is None:
            raise LookupError("investigation 不存在")
        value = self.store.get_json("investigation", pointer["object_digest"])
        if not isinstance(value, Mapping) or value.get("investigation_id") != investigation_id or value.get("investigation_revision") != pointer["revision"]:
            raise W5InvestigationError("investigation_pointer_invalid", "investigation current pointer 不闭合")
        return copy.deepcopy(dict(value)), pointer

    def _owned(self, principal: Mapping[str, Any], investigation_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        value, pointer = self._load(investigation_id)
        actor = _principal(principal, _event_country(value["event_reference"]))
        if value.get("owner_principal_id") != actor["principal_id"]:
            raise LookupError("investigation 不存在")
        authorization = validate_authorization_gate(value["owner_principal_id"], actor["principal_id"])
        self.dispatcher.record_control_execution("GATE-05", {"subject_digest": authorization["subject_digest"]}, authorization)
        return actor, value, pointer

    def _cas(self, body: Mapping[str, Any], current: Mapping[str, Any]) -> tuple[int, str, str]:
        for field in ("expected_investigation_revision", "expected_current_digest", "idempotency_key"):
            if field not in body:
                raise W5InvestigationError("cas_fields_missing", f"缺少 {field}", status_code=400)
        revision = body["expected_investigation_revision"]
        digest = body["expected_current_digest"]
        key = body["idempotency_key"]
        if revision != current["revision"] or digest != current["object_digest"]:
            raise W5InvestigationError(
                "compare_and_swap_conflict",
                "investigation revision/current_digest 已变化",
                retryable=True,
                next_action="读取最新 investigation 后重试",
            )
        return revision, digest, key

    def _commit(
        self,
        *,
        snapshot: Mapping[str, Any],
        expected_digest: str | None,
        expected_revision: int | None,
        idempotency_key: str,
        request: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        stored = self.store.put_json("investigation", snapshot)
        pointer = self.store.compare_and_swap_pointer(
            "investigation",
            str(snapshot["investigation_id"]),
            expected_current_digest=expected_digest,
            expected_revision=expected_revision,
            new_object_digest=stored["object_digest"],
            new_revision=int(snapshot["investigation_revision"]),
            idempotency_key=idempotency_key,
            request_digest=digest_prefixed(request),
        )
        if pointer.get("replayed"):
            live = self.store.get_json("investigation", pointer["object_digest"])
            return copy.deepcopy(dict(live)), True
        return copy.deepcopy(dict(snapshot)), False

    def _view(self, value: Mapping[str, Any], current_digest: str) -> dict[str, Any]:
        nodes = []
        for node in value["nodes"]:
            nodes.append({
                "node_id": node["node_id"],
                "execution_revision": node["execution_revision"],
                "state": node["state"],
                "result_set_refs": copy.deepcopy(node.get("result_set_refs", [])),
                "failure_code": node.get("failure_code"),
                "unit_id": node["unit_id"],
                "receipt_digest": node.get("receipt_digest"),
            })
        return {
            "investigation_id": value["investigation_id"],
            "investigation_revision": value["investigation_revision"],
            "parent_investigation_revision": value.get("parent_investigation_revision"),
            "current_digest": current_digest,
            "status": value["status"],
            "identity": copy.deepcopy(value["identity"]),
            "plan": copy.deepcopy(value["plan"]),
            "nodes": nodes,
            "limitations": copy.deepcopy(value["limitations"]),
            "evidence_graph_revision": value.get("evidence_graph_revision"),
            "local_execution": True,
            "production_deployed": False,
        }

    def create_investigation(self, principal: Mapping[str, Any], body: Mapping[str, Any]):
        request = _exact(body, {"event_reference", "publication_id", "revision", "goal", "idempotency_key"}, "create")
        actor = _principal(principal, _event_country(request["event_reference"]))
        identity = self._identity(request["event_reference"], request["publication_id"], request["revision"])
        plan = self._plan(identity, request["goal"])
        investigation_id = "inv_" + digest_hex({
            "owner": actor["principal_id"], "event_reference": request["event_reference"],
            "publication_id": request["publication_id"], "revision": request["revision"], "goal": request["goal"],
        })[:60]
        nodes = [{
            "node_id": node["node_id"], "unit_id": node["unit_id"], "execution_revision": 1,
            "state": "pending", "result_set_refs": [], "failure_code": None,
            "record_digest": None, "receipt_digest": None,
        } for node in plan["nodes"]]
        snapshot = {
            "schema_version": "country_outage_p2_s1_w5_investigation_v1",
            "investigation_id": investigation_id,
            "investigation_revision": 1,
            "parent_investigation_revision": None,
            "owner_principal_id": actor["principal_id"],
            "status": "admitted",
            "event_reference": request["event_reference"],
            "identity": identity,
            "plan": plan,
            "nodes": nodes,
            "limitations": [{"code": "local_w5_only", "severity": "info", "message_zh": "仅本地隔离执行，未部署生产。"}],
            "evidence_graph_revision": None,
            "evidence_graph_refs": [],
            "result_set_refs": [],
            "receipt_refs": [],
            "export_refs": [],
            "turn_refs": [],
            "runtime_boundary": {"local_execution": True, "runtime_implemented": True, "production_deployed": False},
        }
        committed, replayed = self._commit(
            snapshot=snapshot, expected_digest=None, expected_revision=None,
            idempotency_key=request["idempotency_key"], request=request,
        )
        pointer = self.store.read_pointer("investigation", investigation_id)
        return {"investigation": self._view(committed, pointer["object_digest"]), "deduplicated": replayed}, 201

    def get_investigation(self, principal: Mapping[str, Any], investigation_id: str):
        _, value, pointer = self._owned(principal, investigation_id)
        return {"investigation": self._view(value, pointer["object_digest"])}

    def _receipt(self, investigation_id: str, node: Mapping[str, Any], state: str, result: Any, error: Any) -> dict[str, Any]:
        kind = "tool" if str(node["unit_id"]).startswith("TOOL-") else "operator" if str(node["unit_id"]).startswith("OP-") else "transaction"
        base = {
            "receipt_kind": kind,
            "investigation_id": investigation_id,
            "node_id": node["node_id"],
            "unit_id": node["unit_id"],
            "state": state,
            "result_digest": digest_hex(result) if result is not None else None,
            "error_digest": digest_hex(error) if error is not None else None,
        }
        receipt = {**base, "receipt_digest": digest_hex(base)}
        self.store.put_json("receipt", receipt)
        return receipt

    @staticmethod
    def _execution_effects(
        *,
        state: str,
        result: Any,
        calls: Sequence[Mapping[str, Any]],
        started_ns: int,
        completed_ns: int,
        cancellation_observed: bool,
        budget: Mapping[str, Any],
    ) -> dict[str, Any]:
        rows = sum(int(item.get("returned_count", 0)) for item in calls)
        returned = int(result.get("returned_count", rows)) if isinstance(result, Mapping) else rows
        encoded_bytes = len(canonical_json(result).encode("utf-8")) if result is not None else 0
        duration_ms = max(0.0, (completed_ns - started_ns) / 1_000_000)
        limits = {field: budget[field] for field in ("max_wall_ms", "max_rows", "max_bytes")}
        exceeded = []
        if duration_ms > limits["max_wall_ms"]:
            exceeded.append("max_wall_ms")
        if returned > limits["max_rows"]:
            exceeded.append("max_rows")
        if encoded_bytes > limits["max_bytes"]:
            exceeded.append("max_bytes")
        return {
            "clock": "time.monotonic_ns",
            "started_monotonic_ns": started_ns,
            "completed_monotonic_ns": completed_ns,
            "duration_ms": duration_ms,
            "scanned_rows": rows,
            "returned_rows": returned,
            "output_bytes": encoded_bytes,
            "cancel_point": "before_node_handler" if cancellation_observed else None,
            "non_cost_budget": {"limits": limits, "exceeded_limits": exceeded, "disposition": "exceeded" if exceeded else "passed"},
            "model_usage": {"disposition": "not_applicable", "reason": "tool_operator_control_node_has_no_model_call"},
            "state": state,
        }

    def _plan_cap(self, parameters: Mapping[str, Any], records: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        return bind_output_to_argument(parameters, records)

    def _control(self, unit_id: str, parameters: Mapping[str, Any], *, snapshot: Mapping[str, Any], records: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        if unit_id == "PLAN-CAP-01":
            control_input = {"parameters": copy.deepcopy(dict(parameters)), "ancestor_record_digests": sorted(str(item["record_digest"]) for item in records.values() if item.get("record_digest"))}
            result = self._plan_cap(parameters, records)
            self.dispatcher.record_control_execution(unit_id, control_input, result)
            return result
        if unit_id == "GATE-01":
            result = validate_plan_admission(snapshot["plan"], self.dispatcher.admission.execution_allowed_unit_ids)
            subject = snapshot["plan"]["plan_digest"]
        if unit_id == "GATE-02":
            result = validate_identity_gate(snapshot["identity"])
            subject = snapshot["identity"]["identity_digest"]
        if unit_id == "GATE-03":
            result = validate_registry_gate(snapshot["plan"], self.dispatcher)
            subject = self.dispatcher.admission.admission_receipt_digest
        if unit_id == "GATE-04":
            provisional = {
                "schema_version": "country_outage_p2_s1_w5_evidence_graph_v1",
                "graph_state": "committed",
                "identity": snapshot["identity"],
                "nodes": [],
                "edges": [],
                "root_node_ids": [],
                "content_digest": "",
            }
            provisional["content_digest"] = digest_without_fields(provisional, "content_digest", "commit_receipt_digest")
            validate_evidence_references(provisional)
            result = {"gate_id": unit_id, "status": "passed", "subject_digest": provisional["content_digest"]}
            subject = provisional["content_digest"]
        if unit_id == "GATE-05":
            result = validate_authorization_gate(snapshot["owner_principal_id"], snapshot["execution_principal_id"])
            subject = result["subject_digest"]
        if unit_id.startswith("GATE-"):
            self.dispatcher.record_control_execution(unit_id, {"subject_digest": subject}, result)
            return result
        if unit_id == "BOUNDARY-01":
            result = boundary_response()
            self.dispatcher.record_control_execution(unit_id, {}, result)
            return result
        raise W5InvestigationError("control_unit_not_directly_plannable", f"{unit_id} 不得作为调查业务节点")

    def _bound_request(
        self,
        node: Mapping[str, Any],
        records: Mapping[str, Mapping[str, Any]],
        plan_nodes: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        request = copy.deepcopy(dict(node["parameters"]))
        ancestor_ids = ancestors_by_node(plan_nodes)[node["node_id"]]
        for binding in node.get("input_bindings", []):
            required = {"input_name", "source_kind", "source_ref", "source_digest", "source_artifact_digest"}
            if not isinstance(binding, Mapping) or set(binding) != required:
                raise W5InvestigationError("input_binding_invalid", "input binding 字段必须闭合")
            source_ref = binding["source_ref"]
            if binding["source_kind"] not in {"node_result", "result_set", "operator_receipt"} or source_ref not in ancestor_ids:
                raise W5InvestigationError("input_binding_source_forbidden", "input binding 只能引用已提交祖先")
            source = records.get(source_ref)
            if source is None or source.get("state") not in {"succeeded", "reused"}:
                raise W5InvestigationError("input_binding_source_unavailable", "input binding 源节点未成功")
            if source.get("result_digest") != binding["source_artifact_digest"]:
                raise W5InvestigationError("input_binding_artifact_digest_mismatch", "input binding 源制品摘要不一致")
            if binding["source_kind"] == "operator_receipt":
                value: Any = source["receipt_digest"]
            elif source["unit_id"] == "PLAN-CAP-01":
                value = source["result"]["value"]
                if isinstance(value, (Mapping, list)):
                    raise W5InvestigationError("input_binding_fanout_forbidden", "PLAN-CAP-01 binding 只允许单一标量")
            else:
                value = source["result"]
            input_name = binding["input_name"]
            target: Any = request
            for part in input_name.split("."):
                if not isinstance(target, Mapping) or part not in target:
                    raise W5InvestigationError("input_binding_parameter_missing", "冻结 Plan parameters 缺少被绑定值")
                target = target[part]
            if target != value:
                raise W5InvestigationError("input_binding_bound_value_mismatch", "祖先值与冻结参数不一致")
            recipe = {"input_name": input_name, "source_kind": binding["source_kind"], "source_ref": source_ref, "bound_parameter_value": target}
            if digest_hex(recipe) != binding["source_digest"]:
                raise W5InvestigationError("input_binding_source_digest_mismatch", "input binding 内容摘要不一致")
            if not isinstance(input_name, str) or not input_name or "[" in input_name:
                raise W5InvestigationError("input_binding_target_invalid", "input_name 仅允许闭合对象路径")
        return request

    def _business(
        self,
        node: Mapping[str, Any],
        identity: Mapping[str, Any],
        records: Mapping[str, Mapping[str, Any]],
        plan_nodes: Sequence[Mapping[str, Any]],
        execution_event_context: dict[str, Any] | None = None,
    ) -> tuple[Any, list[dict[str, Any]]]:
        unit_id = node["unit_id"]
        request = self._bound_request(node, records, plan_nodes)
        if str(unit_id).startswith("TOOL-"):
            tool_identity = copy.deepcopy(dict(identity))
            tool_identity.pop("identity_digest", None)
            tool_identity.pop("binding_digest", None)
            tool_identity["registry_snapshot_digest"] = str(tool_identity["registry_snapshot_digest"]).removeprefix("sha256:")
            request["identity"] = tool_identity
            request.setdefault("page_size", 200)
            request["page_token"] = None
            pages: list[dict[str, Any]] = []
            calls: list[dict[str, Any]] = []
            for page_index in range(1024):
                dispatched = self.dispatcher.execute(unit_id, request)
                schema_receipt = dispatched["schema_validation_receipt"]
                if execution_event_context is not None and not execution_event_context.get("first_dispatch_recorded"):
                    self._admission_event(
                        execution_id=execution_event_context["execution_id"],
                        investigation_id=execution_event_context["investigation_id"],
                        event_kind="first_dispatch",
                        artifact_kind="InvestigationPlan",
                        artifact_digest=execution_event_context["plan_artifact_digest"],
                        design_validator_receipt_digest=execution_event_context["plan_design_validator_receipt_digest"],
                        runtime_admission_receipt_digest=execution_event_context["plan_runtime_admission_receipt_digest"],
                        parameter_bindings_digest=execution_event_context["plan_parameter_bindings_digest"],
                        action_subject_digest=schema_receipt["receipt_digest"],
                    )
                    execution_event_context["first_dispatch_recorded"] = True
                page = copy.deepcopy(dispatched["result"])
                pages.append(page)
                calls.append({
                    "page_index": page_index,
                    "tool_run_id": page["tool_run_id"],
                    "query_receipt_digest": page["query_receipt_digest"],
                    "returned_count": page["returned_count"],
                    "schema_validation_receipt_digest": schema_receipt["receipt_digest"],
                })
                token = page.get("next_page_token")
                if token is None:
                    break
                request["page_token"] = token
            else:
                raise W5InvestigationError("tool_page_limit_exceeded", "Tool 页链超过 1024 页")
            result_set = self.result_sets.freeze_tool_pages(
                identity=identity,
                pages=pages,
                tool_contract_digest=self.dispatcher.admission.entries[unit_id]["contract_digest"],
            )
            return result_set, calls
        if str(unit_id).startswith("OP-"):
            operator_identity = copy.deepcopy(dict(identity))
            operator_identity.pop("identity_digest", None)
            operator_identity.pop("binding_digest", None)
            operator_identity.pop("finality", None)
            operator_identity["registry_snapshot_digest"] = str(operator_identity["registry_snapshot_digest"]).removeprefix("sha256:")
            request["identity"] = operator_identity
            if isinstance(request.get("inputs"), Mapping):
                request["inputs"] = {**request["inputs"], "identity": operator_identity}
                request["input_digests"] = [digest_hex(request["inputs"])]
            trusted_context_digest = None
            inherited_ref_ids = sorted({
                str(evidence_ref)
                for source_id, record in records.items()
                if source_id in ancestors_by_node(plan_nodes)[node["node_id"]]
                for evidence_ref in record.get("evidence_refs", [])
                if isinstance(evidence_ref, str) and evidence_ref
            })
            inherited_evidence_refs = [
                {
                    "evidence_id": f"ancestor:{node['node_id']}:{index}",
                    "source_digest": digest_hex({"ancestor_evidence_ref": evidence_ref}),
                    "member_key": evidence_ref,
                }
                for index, evidence_ref in enumerate(inherited_ref_ids)
            ]
            context_record = {
                "design_candidate_id": DESIGN_CANDIDATE_ID,
                "op29_outputs": {}, "op10_outputs": {}, "op11_outputs": {}, "op15_outputs": {}, "op36_outputs": {},
                "tool12_result_sets": {}, "projection_receipts": {}, "population_binding_receipts": {},
                "population_evidence_binding": None, "population_evidence_bindings": None,
                "inherited_evidence_refs": inherited_evidence_refs,
                "asn_bound_op10_receipts": None, "asn_bound_op11_receipts": None, "asn_bound_op36_receipts": None,
            }
            if unit_id in {"OP-30", "OP-31", "OP-32"}:
                if not inherited_evidence_refs:
                    raise W5InvestigationError("vp_expected_population_evidence_missing", f"{unit_id} 缺少祖先人口 evidence")
                trusted_context_digest = self.store.put_json("operator-context", context_record)["object_digest"]
            if unit_id == "OP-33":
                bindings: dict[str, dict[str, Any]] = {}
                for input_name in ("new_prefix_state_rows", "route_state_rows"):
                    population = request["inputs"].get(input_name)
                    if not isinstance(population, list):
                        raise W5InvestigationError("op33_population_input_invalid", f"{input_name} 必须为冻结数组")
                    completeness_digest = digest_hex({"node_id": node["node_id"], "input_name": input_name, "complete": True})
                    binding_base = {
                        "schema_version": "country_outage_p2_s1_population_evidence_binding_receipt_v1",
                        "receipt_kind": "population_evidence_binding", "design_candidate_id": DESIGN_CANDIDATE_ID,
                        "operator_id": "OP-33", "operator_input_name": input_name,
                        "operator_input_digest": digest_hex(population), "identity_digest": digest_hex(operator_identity),
                        "source_population_ref": {
                            "source_kind": "frozen_result_set", "artifact_id": f"trusted-plan-parameter:{node['node_id']}:{input_name}",
                            "artifact_revision": 1, "population_id": input_name,
                            "content_digest": digest_hex({"population": population}),
                            "manifest_digest": digest_hex({"node_id": node["node_id"], "input_name": input_name}),
                            "completeness_receipt_digest": completeness_digest,
                        },
                        "set_completeness": "complete", "member_count": len(population),
                        "member_keys_digest": digest_hex(sorted(digest_hex(row) for row in population)),
                        "population_evidence_ref": {
                            "evidence_id": f"population:{node['node_id']}:{input_name}",
                            "source_digest": completeness_digest, "member_key": f"population:{input_name}",
                        },
                        "validator": {
                            "validator_id": "country_outage_p2_s1_structural_binding_validator", "validator_version": "1.0.0",
                            "contract_digest": digest_hex({"contract": "population_evidence_binding"}),
                            "implementation_digest": digest_hex({"implementation": "w5_host_structural_binding"}),
                        },
                        "business_transform_count": 0,
                    }
                    binding = {**binding_base, "receipt_digest": digest_hex(binding_base)}
                    self.store.put_json("receipt", binding)
                    bindings[input_name] = binding
                context_record["population_binding_receipts"] = {item["receipt_digest"]: item for item in bindings.values()}
                context_record["population_evidence_bindings"] = bindings
                trusted_context_digest = self.store.put_json("operator-context", context_record)["object_digest"]
            if unit_id == "OP-37":
                ancestor_ids = ancestors_by_node(plan_nodes)[node["node_id"]]
                sources = [
                    record for source_id, record in records.items()
                    if source_id in ancestor_ids and record.get("unit_id") == "OP-29"
                    and record.get("state") in {"succeeded", "reused"}
                    and isinstance(record.get("result"), Mapping)
                ]
                if len(sources) != 1:
                    raise W5InvestigationError("op37_trusted_op29_ancestor_missing", "OP-37 必须解析唯一已提交 OP-29 祖先")
                op29_output = copy.deepcopy(dict(sources[0]["result"]))
                op29_receipt = {
                    "identity": copy.deepcopy(op29_output["identity"]),
                    "operator_id": "OP-29",
                    **{
                        key: copy.deepcopy(op29_output["result"][key])
                        for key in ("left_digest", "right_digest", "relation", "comparable", "profile_digest")
                    },
                    "output_digest": op29_output["output_digest"],
                    "evidence_refs": copy.deepcopy(op29_output["evidence_refs"]),
                }
                request["inputs"] = {**request["inputs"], "op29_temporal_receipt": op29_receipt}
                request["input_digests"] = [digest_hex(request["inputs"])]
                projection_base = {
                    "receipt_kind": "op29_to_op37_structural_projection",
                    "source_node_id": sources[0]["node_id"],
                    "source_execution_receipt_digest": sources[0]["receipt_digest"],
                    "source_operator_output_digest": op29_output["output_digest"],
                    "target_node_id": node["node_id"],
                    "target_input_name": "inputs.op29_temporal_receipt",
                    "projected_value_digest": digest_hex(op29_receipt),
                    "business_transform_count": 0,
                    "disposition": "passed",
                }
                self.store.put_json("receipt", {**projection_base, "receipt_digest": digest_hex(projection_base)})
                context_record["op29_outputs"] = {op29_output["output_digest"]: op29_output}
                trusted_context_digest = self.store.put_json("operator-context", context_record)["object_digest"]
            dispatched = self.dispatcher.execute(unit_id, request, trusted_context_digest=trusted_context_digest)
            schema_receipt = dispatched["schema_validation_receipt"]
            if execution_event_context is not None and not execution_event_context.get("first_dispatch_recorded"):
                self._admission_event(
                    execution_id=execution_event_context["execution_id"],
                    investigation_id=execution_event_context["investigation_id"],
                    event_kind="first_dispatch",
                    artifact_kind="InvestigationPlan",
                    artifact_digest=execution_event_context["plan_artifact_digest"],
                    design_validator_receipt_digest=execution_event_context["plan_design_validator_receipt_digest"],
                    runtime_admission_receipt_digest=execution_event_context["plan_runtime_admission_receipt_digest"],
                    parameter_bindings_digest=execution_event_context["plan_parameter_bindings_digest"],
                    action_subject_digest=schema_receipt["receipt_digest"],
                )
                execution_event_context["first_dispatch_recorded"] = True
            return dispatched["result"], [{"schema_validation_receipt_digest": schema_receipt["receipt_digest"]}]
        raise W5InvestigationError("business_unit_invalid", f"非业务单元：{unit_id}")

    def _execute(
        self,
        snapshot: Mapping[str, Any],
        *,
        rerun_ids: set[str] | None = None,
        execution_event_context: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        plan_nodes = {node["node_id"]: node for node in snapshot["plan"]["nodes"]}
        topology = validate_static_dag(snapshot["plan"]["nodes"], self.dispatcher.admission.execution_allowed_unit_ids)
        previous_records: dict[str, dict[str, Any]] = {}
        for node_view in snapshot["nodes"]:
            digest = node_view.get("record_digest")
            if digest:
                previous_records[node_view["node_id"]] = self.store.get_json("execution-record", digest)
        records: dict[str, dict[str, Any]] = {}
        result_refs: list[dict[str, Any]] = []
        receipt_refs: list[dict[str, Any]] = []
        for node_id in topology:
            node = plan_nodes[node_id]
            if rerun_ids is not None and node_id not in rerun_ids and node_id in previous_records:
                prior = copy.deepcopy(previous_records[node_id])
                prior["state"] = "reused"
                reuse = self._receipt(snapshot["investigation_id"], node, "reused", prior.get("result"), None)
                prior["receipt_digest"] = reuse["receipt_digest"]
                prior["execution_revision"] = int(prior["execution_revision"]) + 1
                prior_stored = self.store.put_json("execution-record", prior)
                prior["record_digest"] = prior_stored["object_digest"]
                records[node_id] = prior
                receipt_refs.append({"kind": "reuse", "digest": reuse["receipt_digest"]})
                result_refs.extend(prior.get("result_set_refs", []))
                continue
            live_pointer = self.store.read_pointer("investigation", snapshot["investigation_id"])
            live_snapshot = self.store.get_json("investigation", live_pointer["object_digest"])
            cancellation_observed = live_snapshot.get("status") in {"cancel_requested", "cancelled"}
            dependency_failed = any(records.get(dep, {}).get("state") not in {"succeeded", "reused"} for dep in node["depends_on"])
            cancelled = next((item for item in snapshot["nodes"] if item["node_id"] == node_id and item["state"] == "cancelled"), None)
            result = None
            error = None
            calls: list[dict[str, Any]] = []
            started_ns = time.monotonic_ns()
            if cancellation_observed or cancelled:
                state = "cancelled"
                error = {"code": "node_cancelled", "message_zh": "节点在执行前已取消。"}
            elif dependency_failed and node["dependency_mode"] == "hard":
                state = "skipped"
                error = {"code": "dependency_failed", "message_zh": "hard dependency 未成功。"}
            else:
                try:
                    unit_id = node["unit_id"]
                    self.dispatcher.assert_allowed(unit_id)
                    if unit_id.startswith(("TOOL-", "OP-")):
                        result, calls = self._business(
                            node, snapshot["identity"], records, snapshot["plan"]["nodes"],
                            execution_event_context=execution_event_context,
                        )
                    else:
                        result = self._control(unit_id, node["parameters"], snapshot=snapshot, records=records)
                    state = "succeeded"
                except Exception as exc:  # 节点失败局部化，其他软依赖可继续。
                    state = "failed"
                    error = {"code": getattr(exc, "code", exc.__class__.__name__), "message_zh": str(exc)}
            completed_ns = time.monotonic_ns()
            budget = next(
                item["budget_allocation"]
                for item in snapshot["plan"].get("design_plan_nodes", [])
                if item["node_id"] == node_id
            ) if snapshot["plan"].get("design_plan_nodes") else {"max_wall_ms": 30000, "max_rows": 100000, "max_bytes": 67108864}
            effects = self._execution_effects(
                state=state, result=result, calls=calls, started_ns=started_ns, completed_ns=completed_ns,
                cancellation_observed=bool(cancellation_observed or cancelled), budget=budget,
            )
            if effects["non_cost_budget"]["disposition"] == "exceeded" and state == "succeeded":
                state = "failed"
                error = {"code": "non_cost_budget_exceeded", "message_zh": f"节点超出结构预算：{effects['non_cost_budget']['exceeded_limits']}"}
                effects["state"] = state
            receipt = self._receipt(snapshot["investigation_id"], node, state, result, error)
            refs: list[dict[str, Any]] = []
            if isinstance(result, Mapping) and result.get("schema_version") == "country_outage_p2_result_set_v1":
                stored_result = self.store.put_json("result-set-candidate", result)
                ref = {"result_set_id": result["result_set_id"], "result_set_revision": result["result_set_revision"], "object_digest": stored_result["object_digest"]}
                refs.append(ref)
                result_refs.append(ref)
            record = {
                "schema_version": "country_outage_p2_s1_w5_execution_record_v1",
                "investigation_id": snapshot["investigation_id"],
                "node_id": node_id,
                "unit_id": node["unit_id"],
                "unit_kind": self.dispatcher.admission.entries[node["unit_id"]]["unit_kind"],
                "execution_revision": int(next(item for item in snapshot["nodes"] if item["node_id"] == node_id)["execution_revision"]),
                "state": state,
                "result": result,
                "result_digest": digest_hex(result) if result is not None else None,
                "error": error,
                "evidence_refs": _evidence_refs(result),
                "atomic_call_receipts": calls,
                "result_set_refs": refs,
                "receipt_digest": receipt["receipt_digest"],
            }
            stored = self.store.put_json("execution-record", record)
            record["record_digest"] = stored["object_digest"]
            # 真实耗时属于本次运行测量，不属于事实/计划/Graph 的语义身份。将它单独
            # 内容寻址，既保留 W6 性能证据，也避免 monotonic clock 经 record digest
            # 污染最终 Investigation CAS 与可重放 admission event chain。
            self.store.put_json("execution-measurement", {
                "schema_version": "country_outage_p2_s1_w5_execution_measurement_v1",
                "investigation_id": snapshot["investigation_id"],
                "node_id": node_id,
                "unit_id": node["unit_id"],
                "execution_revision": record["execution_revision"],
                "execution_record_digest": record["record_digest"],
                "runtime_effects": effects,
            })
            records[node_id] = record
            receipt_refs.append({"kind": receipt["receipt_kind"], "digest": receipt["receipt_digest"]})
        return [records[node_id] for node_id in topology], result_refs, receipt_refs

    def _execute_and_commit(self, value: Mapping[str, Any], pointer: Mapping[str, Any], body: Mapping[str, Any], *, actor_principal_id: str, rerun_ids: set[str] | None = None):
        key = body.get("idempotency_key")
        if not isinstance(key, str) or not key:
            raise W5InvestigationError("cas_fields_missing", "缺少 idempotency_key", status_code=400)
        prior = self.store.read_idempotency("investigation", value["investigation_id"], key)
        if prior is not None:
            if prior["request_digest"] != digest_prefixed(body):
                raise W5InvestigationError("idempotency_conflict", "同一幂等 key 对应不同请求")
            live, live_pointer = self._load(value["investigation_id"])
            return {"accepted": True, "deduplicated": True, "investigation": self._view(live, live_pointer["object_digest"])}, 202
        revision, expected, key = self._cas(body, pointer)
        if value["status"] == "cancelled":
            raise W5InvestigationError("investigation_cancelled", "已取消调查不得执行")
        design_plan = self.store.get_json("design-investigation-plan", value["plan"]["source_design_plan_digest"])
        plan_b = self._resolve_runtime_artifact_admission(
            receipt_digest=value["plan"]["runtime_plan_admission_receipt_digest"],
            artifact_kind="InvestigationPlan", artifact=design_plan,
            parameter_bindings_digest=digest_prefixed({"parameter_bindings": value["plan"]["design_parameter_bindings"]}),
            authorizes_dispatcher_execution=True,
        )
        plan_a = next(item for item in self.store.list_json("design-semantic-validator-receipt") if item["receipt_digest"] == plan_b["frozen_design_validator_receipt_digest"])
        plan_parameters = digest_prefixed({"parameter_bindings": value["plan"]["design_parameter_bindings"]})
        event_context = self._admission_execution_context(
            investigation_id=value["investigation_id"],
            base_investigation_revision=revision,
            idempotency_key=key,
            plan_artifact_digest=plan_b["design_artifact_digest"],
        )
        execution_id = event_context["execution_id"]
        event_context.update({
            "plan_design_validator_receipt_digest": plan_a["receipt_digest"],
            "plan_runtime_admission_receipt_digest": plan_b["receipt_digest"],
            "plan_parameter_bindings_digest": plan_parameters,
            "first_dispatch_recorded": False,
        })
        self._admission_event(
            execution_id=execution_id, investigation_id=value["investigation_id"],
            event_kind="plan_design_validated", artifact_kind="InvestigationPlan",
            artifact_digest=plan_b["design_artifact_digest"],
            design_validator_receipt_digest=plan_a["receipt_digest"],
            runtime_admission_receipt_digest=None,
            parameter_bindings_digest=plan_parameters,
            action_subject_digest="sha256:" + plan_a["receipt_digest"],
        )
        self._admission_event(
            execution_id=execution_id, investigation_id=value["investigation_id"],
            event_kind="plan_runtime_admitted", artifact_kind="InvestigationPlan",
            artifact_digest=plan_b["design_artifact_digest"],
            design_validator_receipt_digest=plan_a["receipt_digest"],
            runtime_admission_receipt_digest=plan_b["receipt_digest"],
            parameter_bindings_digest=plan_parameters,
            action_subject_digest=plan_b["receipt_digest"],
        )
        running_snapshot = {
            **copy.deepcopy(dict(value)),
            "investigation_revision": revision + 1,
            "parent_investigation_revision": revision,
            "status": "running",
            "execution_principal_id": actor_principal_id,
        }
        running, _ = self._commit(
            snapshot=running_snapshot,
            expected_digest=expected,
            expected_revision=revision,
            idempotency_key=key + ".running",
            request={**body, "phase": "running"},
        )
        running_pointer = self.store.read_pointer("investigation", value["investigation_id"])
        self._admission_event(
            execution_id=execution_id, investigation_id=value["investigation_id"],
            event_kind="running_committed", artifact_kind="InvestigationPlan",
            artifact_digest=plan_b["design_artifact_digest"],
            design_validator_receipt_digest=plan_a["receipt_digest"],
            runtime_admission_receipt_digest=plan_b["receipt_digest"],
            parameter_bindings_digest=plan_parameters,
            action_subject_digest=running_pointer["object_digest"],
        )
        records, result_refs, receipt_refs = self._execute(
            running, rerun_ids=rerun_ids, execution_event_context=event_context,
        )
        live_pointer = self.store.read_pointer("investigation", value["investigation_id"])
        if live_pointer["object_digest"] != running_pointer["object_digest"]:
            live = self.store.get_json("investigation", live_pointer["object_digest"])
            if live.get("status") == "cancelled":
                return {"accepted": True, "deduplicated": False, "investigation": self._view(live, live_pointer["object_digest"])}, 202
            raise W5InvestigationError("compare_and_swap_conflict", "执行期间 investigation current 已变化", retryable=True)
        if not event_context["first_dispatch_recorded"]:
            raise W5InvestigationError("first_business_dispatch_missing", "正常执行路径未产生可验证的首个业务单元调用")
        failed = [record for record in records if record["state"] in {"failed", "cancelled", "skipped"}]
        required_failed = any(
            record["state"] in {"failed", "cancelled", "skipped"}
            and next(node for node in value["plan"]["nodes"] if node["node_id"] == record["node_id"])["requiredness"] == "required"
            for record in records
        )
        status = "failed" if required_failed and len(failed) == len(records) else "partially_completed" if failed else "completed"
        new_revision = revision + 2
        committed_result_sets: dict[tuple[str, int], Mapping[str, Any]] = {}
        for record in records:
            result = record.get("result")
            if isinstance(result, Mapping) and result.get("schema_version") == "country_outage_p2_result_set_v1":
                result_parameters = digest_prefixed({"parameters": value["plan"]["design_parameter_bindings"].get(record["node_id"], {})})
                result_digest = digest_prefixed(result)
                self._admission_event(
                    execution_id=execution_id, investigation_id=value["investigation_id"],
                    event_kind="result_set_built", artifact_kind="ResultSet",
                    artifact_digest=result_digest,
                    design_validator_receipt_digest=None, runtime_admission_receipt_digest=None,
                    parameter_bindings_digest=result_parameters, action_subject_digest=result_digest,
                )
                design_validator_receipt = self._frozen_design_semantic_admission(
                    artifact_kind="ResultSet", artifact=result,
                )
                self._admission_event(
                    execution_id=execution_id, investigation_id=value["investigation_id"],
                    event_kind="result_set_design_validated", artifact_kind="ResultSet",
                    artifact_digest=result_digest,
                    design_validator_receipt_digest=design_validator_receipt["receipt_digest"],
                    runtime_admission_receipt_digest=None,
                    parameter_bindings_digest=result_parameters,
                    action_subject_digest="sha256:" + design_validator_receipt["receipt_digest"],
                )
                admission = self._runtime_artifact_admission(
                    artifact_kind="ResultSet", design_artifact=result,
                    frozen_design_validator_receipt_digest=design_validator_receipt["receipt_digest"],
                    runtime_receipt_kind="result_set_freeze", validator_id="country_outage_p2_s1_w5_result_set_freeze_validator",
                    implementation_path=Path(__file__).with_name("country_outage_p2_s1_result_set.py"),
                    parameter_bindings_digest=digest_hex({"parameters": value["plan"]["design_parameter_bindings"].get(record["node_id"], {})}),
                    authorizes_dispatcher_execution=False,
                )
                self._resolve_runtime_artifact_admission(
                    receipt_digest=admission["receipt_digest"], artifact_kind="ResultSet", artifact=result,
                    parameter_bindings_digest=digest_prefixed({"parameters": value["plan"]["design_parameter_bindings"].get(record["node_id"], {})}),
                    authorizes_dispatcher_execution=False,
                )
                self._admission_event(
                    execution_id=execution_id, investigation_id=value["investigation_id"],
                    event_kind="result_set_runtime_admitted", artifact_kind="ResultSet",
                    artifact_digest=result_digest,
                    design_validator_receipt_digest=design_validator_receipt["receipt_digest"],
                    runtime_admission_receipt_digest=admission["receipt_digest"],
                    parameter_bindings_digest=result_parameters,
                    action_subject_digest=admission["receipt_digest"],
                )
                result_receipt_digests = {
                    result["query_receipt_digest"], result["freeze_receipt_digest"],
                    *(item["page_receipt_digest"] for item in result["page_manifest"]),
                }
                for candidate in self.store.list_json("receipt-candidate"):
                    if candidate.get("receipt_digest") in result_receipt_digests:
                        self.store.put_json("receipt", candidate)
                result_ref = self.store.put_json("result-set", result)
                self._admission_event(
                    execution_id=execution_id, investigation_id=value["investigation_id"],
                    event_kind="result_set_published", artifact_kind="ResultSet",
                    artifact_digest=result_digest,
                    design_validator_receipt_digest=design_validator_receipt["receipt_digest"],
                    runtime_admission_receipt_digest=admission["receipt_digest"],
                    parameter_bindings_digest=result_parameters,
                    action_subject_digest=result_ref["object_digest"],
                )
                receipt_refs.append({"kind": "runtime_result_set_admission", "digest": admission["receipt_digest"]})
                committed_result_sets[(result["result_set_id"], result["result_set_revision"])] = result
        graph = self.graphs.commit(
            investigation_id=value["investigation_id"], investigation_revision=new_revision,
            plan=value["plan"], identity=value["identity"], execution_records=records,
            investigation_status=status,
            parent_graph_revision=value.get("evidence_graph_revision"),
        )
        graph_context = next(
            item for item in self.store.list_json("graph-validation-context-candidate")
            if item.get("graph_digest") == graph["graph_digest"]
        )
        graph_parameters = digest_prefixed({"parameter_bindings": value["plan"]["design_parameter_bindings"]})
        graph_digest = digest_prefixed(graph)
        self._admission_event(
            execution_id=execution_id, investigation_id=value["investigation_id"],
            event_kind="evidence_graph_built", artifact_kind="EvidenceGraph",
            artifact_digest=graph_digest,
            design_validator_receipt_digest=None, runtime_admission_receipt_digest=None,
            parameter_bindings_digest=graph_parameters, action_subject_digest=graph_digest,
        )
        graph_design_validator_receipt = self._frozen_design_semantic_admission(
            artifact_kind="EvidenceGraph", artifact=graph, graph_context=graph_context,
            result_sets=committed_result_sets,
            previous_graph=(
                self.store.get_json("evidence-graph", value["evidence_graph_refs"][-1]["object_digest"])
                if value.get("evidence_graph_refs") else None
            ),
        )
        self._admission_event(
            execution_id=execution_id, investigation_id=value["investigation_id"],
            event_kind="evidence_graph_design_validated", artifact_kind="EvidenceGraph",
            artifact_digest=graph_digest,
            design_validator_receipt_digest=graph_design_validator_receipt["receipt_digest"],
            runtime_admission_receipt_digest=None,
            parameter_bindings_digest=graph_parameters,
            action_subject_digest="sha256:" + graph_design_validator_receipt["receipt_digest"],
        )
        graph_admission = self._runtime_artifact_admission(
            artifact_kind="EvidenceGraph", design_artifact=graph,
            frozen_design_validator_receipt_digest=graph_design_validator_receipt["receipt_digest"],
            runtime_receipt_kind="evidence_graph_commit", validator_id="country_outage_p2_s1_w5_evidence_graph_commit_validator",
            implementation_path=Path(__file__).with_name("country_outage_p2_s1_evidence_graph.py"),
            parameter_bindings_digest=digest_hex({"parameter_bindings": value["plan"]["design_parameter_bindings"]}),
            authorizes_dispatcher_execution=False,
        )
        self._resolve_runtime_artifact_admission(
            receipt_digest=graph_admission["receipt_digest"], artifact_kind="EvidenceGraph", artifact=graph,
            parameter_bindings_digest=digest_prefixed({"parameter_bindings": value["plan"]["design_parameter_bindings"]}),
            authorizes_dispatcher_execution=False,
        )
        self._admission_event(
            execution_id=execution_id, investigation_id=value["investigation_id"],
            event_kind="evidence_graph_runtime_admitted", artifact_kind="EvidenceGraph",
            artifact_digest=graph_digest,
            design_validator_receipt_digest=graph_design_validator_receipt["receipt_digest"],
            runtime_admission_receipt_digest=graph_admission["receipt_digest"],
            parameter_bindings_digest=graph_parameters,
            action_subject_digest=graph_admission["receipt_digest"],
        )
        graph_receipt_digests = {
            graph["closure_receipt_digest"], graph["commit_receipt_digest"],
            *(node["producer_ref"]["run_receipt_digest"] for node in graph["nodes"]),
            *(edge["producer_ref"]["run_receipt_digest"] for edge in graph["edges"]),
        }
        for candidate in self.store.list_json("receipt-candidate"):
            if candidate.get("receipt_digest") in graph_receipt_digests:
                self.store.put_json("receipt", candidate)
        graph_receipts_subject = digest_prefixed({"published_receipt_digests": sorted(graph_receipt_digests)})
        self._admission_event(
            execution_id=execution_id, investigation_id=value["investigation_id"],
            event_kind="evidence_graph_receipts_published", artifact_kind="EvidenceGraph",
            artifact_digest=graph_digest,
            design_validator_receipt_digest=graph_design_validator_receipt["receipt_digest"],
            runtime_admission_receipt_digest=graph_admission["receipt_digest"],
            parameter_bindings_digest=graph_parameters,
            action_subject_digest=graph_receipts_subject,
        )
        self.store.put_json("graph-validation-context", graph_context)
        graph_ref = self.store.put_json("evidence-graph", graph)
        self._admission_event(
            execution_id=execution_id, investigation_id=value["investigation_id"],
            event_kind="evidence_graph_published", artifact_kind="EvidenceGraph",
            artifact_digest=graph_digest,
            design_validator_receipt_digest=graph_design_validator_receipt["receipt_digest"],
            runtime_admission_receipt_digest=graph_admission["receipt_digest"],
            parameter_bindings_digest=graph_parameters,
            action_subject_digest=graph_ref["object_digest"],
        )
        receipt_refs.append({"kind": "runtime_evidence_graph_admission", "digest": graph_admission["receipt_digest"]})
        node_views = [{
            "node_id": record["node_id"], "unit_id": record["unit_id"],
            "execution_revision": record["execution_revision"],
            "state": "committed" if record["state"] == "succeeded" else "reused" if record["state"] == "reused" else "skipped_dependency_failed" if record["state"] == "skipped" else record["state"],
            "result_set_refs": record["result_set_refs"],
            "failure_code": record["error"]["code"] if record.get("error") else None,
            "record_digest": record["record_digest"], "receipt_digest": record["receipt_digest"],
        } for record in records]
        snapshot = {
            **copy.deepcopy(dict(running)),
            "investigation_revision": new_revision,
            "parent_investigation_revision": revision + 1,
            "status": status,
            "nodes": node_views,
            "evidence_graph_revision": graph["graph_revision"],
            "evidence_graph_refs": [*running.get("evidence_graph_refs", []), {"graph_revision": graph["graph_revision"], "object_digest": self.store.put_json("evidence-graph", graph)["object_digest"]}],
            "result_set_refs": [*running.get("result_set_refs", []), *result_refs],
            "receipt_refs": [*running.get("receipt_refs", []), *receipt_refs],
        }
        snapshot.pop("execution_principal_id", None)
        committed, replayed = self._commit(snapshot=snapshot, expected_digest=running_pointer["object_digest"], expected_revision=revision + 1, idempotency_key=key, request=body)
        new_pointer = self.store.read_pointer("investigation", value["investigation_id"])
        self._admission_event(
            execution_id=execution_id, investigation_id=value["investigation_id"],
            event_kind="final_investigation_cas_committed", artifact_kind="InvestigationCommit",
            artifact_digest=new_pointer["object_digest"],
            design_validator_receipt_digest=None, runtime_admission_receipt_digest=None,
            parameter_bindings_digest=plan_parameters,
            action_subject_digest=new_pointer["object_digest"],
        )
        return {"accepted": True, "deduplicated": replayed, "investigation": self._view(committed, new_pointer["object_digest"])}, 202

    def _execute_and_commit_fail_closed(
        self,
        value: Mapping[str, Any],
        pointer: Mapping[str, Any],
        body: Mapping[str, Any],
        *,
        actor_principal_id: str,
        rerun_ids: set[str] | None = None,
    ):
        """执行异常后把本次 own running revision 收敛为可审计 failed 终态。"""

        try:
            return self._execute_and_commit(
                value,
                pointer,
                body,
                actor_principal_id=actor_principal_id,
                rerun_ids=rerun_ids,
            )
        except Exception as error:
            # Plan A/B 在 running CAS 前失败时不得改变指针；取消或并发 CAS 已取得
            # current 时也不得由旧 worker 覆盖。只有本 actor 仍持有的 running 才收口。
            live_pointer = self.store.read_pointer("investigation", value["investigation_id"])
            if live_pointer is not None:
                live = self.store.get_json("investigation", live_pointer["object_digest"])
                if (
                    live.get("status") == "running"
                    and live.get("execution_principal_id") == actor_principal_id
                ):
                    contexts = [
                        item for item in self.store.list_json("admission-event-context")
                        if item.get("investigation_id") == value["investigation_id"]
                        and item.get("base_investigation_revision") == value["investigation_revision"]
                    ]
                    events = [] if len(contexts) != 1 else [
                        item for item in self.store.list_json("admission-event")
                        if item.get("execution_id") == contexts[0]["execution_id"]
                    ]
                    published_result_refs = []
                    published_receipt_refs = []
                    for event in events:
                        if event.get("event_kind") != "result_set_published":
                            continue
                        result = self.store.get_json("result-set", event["action_subject_digest"])
                        published_result_refs.append({
                            "result_set_id": result["result_set_id"],
                            "result_set_revision": result["result_set_revision"],
                            "object_digest": event["action_subject_digest"],
                        })
                        published_receipt_refs.extend([
                            {"kind": "design_result_set_validation", "digest": event["design_validator_receipt_digest"]},
                            {"kind": "runtime_result_set_admission", "digest": event["runtime_admission_receipt_digest"]},
                        ])
                    latest_records = {
                        item["node_id"]: item
                        for item in self.store.list_json("execution-record")
                        if item.get("investigation_id") == value["investigation_id"]
                    }
                    failed_nodes = copy.deepcopy(live["nodes"])
                    for node_view in failed_nodes:
                        record = latest_records.get(node_view["node_id"])
                        if record is None:
                            continue
                        node_view.update({
                            "execution_revision": record["execution_revision"],
                            "state": "committed" if record["state"] == "succeeded" else record["state"],
                            "result_set_refs": copy.deepcopy(record.get("result_set_refs", [])),
                            "failure_code": record.get("error", {}).get("code") if record.get("error") else None,
                            "record_digest": record.get("record_digest", digest_prefixed(record)),
                            "receipt_digest": record["receipt_digest"],
                        })
                    error_code = str(getattr(error, "code", error.__class__.__name__))
                    failure_base = {
                        "receipt_kind": "transaction_failure",
                        "investigation_id": value["investigation_id"],
                        "execution_id": contexts[0]["execution_id"] if len(contexts) == 1 else None,
                        "failed_stage": events[-1]["event_kind"] if events else "before_running",
                        "error_code": error_code,
                        "published_result_set_digests": sorted(ref["object_digest"] for ref in published_result_refs),
                        "graph_published": False,
                        "disposition": "failed_closed",
                    }
                    failure_receipt = {**failure_base, "receipt_digest": digest_prefixed(failure_base)}
                    self.store.put_json("receipt", failure_receipt)
                    failed = {
                        **copy.deepcopy(dict(live)),
                        "investigation_revision": int(live["investigation_revision"]) + 1,
                        "parent_investigation_revision": int(live["investigation_revision"]),
                        "status": "failed",
                        "nodes": failed_nodes,
                        "result_set_refs": [*live.get("result_set_refs", []), *published_result_refs],
                        "receipt_refs": [
                            *live.get("receipt_refs", []),
                            *published_receipt_refs,
                            {"kind": "transaction_failure", "digest": failure_receipt["receipt_digest"]},
                        ],
                    }
                    failed.pop("execution_principal_id", None)
                    failure_identity = {
                        "investigation_id": value["investigation_id"],
                        "running_digest": live_pointer["object_digest"],
                        "request_digest": digest_prefixed(body),
                    }
                    try:
                        self._commit(
                            snapshot=failed,
                            expected_digest=live_pointer["object_digest"],
                            expected_revision=int(live_pointer["revision"]),
                            idempotency_key=str(body["idempotency_key"]),
                            request=body,
                        )
                    except Exception as recovery_error:
                        # 保留原始业务/准入异常；并发 current 已变化时不能越权修正。
                        if self.store.read_pointer("investigation", value["investigation_id"])["object_digest"] == live_pointer["object_digest"]:
                            raise W5InvestigationError(
                                "runtime_failure_commit_failed",
                                f"运行失败终态提交失败：{recovery_error}",
                            ) from error
            raise

    def start_investigation(self, principal: Mapping[str, Any], investigation_id: str, body: Mapping[str, Any]):
        actor, value, pointer = self._owned(principal, investigation_id)
        if value["status"] not in {"admitted", "pending", "partially_completed", "failed"}:
            raise W5InvestigationError("investigation_state_conflict", f"当前状态不可 start：{value['status']}")
        return self._execute_and_commit_fail_closed(
            value, pointer, body, actor_principal_id=actor["principal_id"]
        )

    def cancel_investigation(self, principal: Mapping[str, Any], investigation_id: str, body: Mapping[str, Any]):
        _, value, pointer = self._owned(principal, investigation_id)
        revision, expected, key = self._cas(body, pointer)
        if value["status"] in _TERMINAL:
            raise W5InvestigationError("investigation_state_conflict", "终态调查不可取消")
        snapshot = {**value, "investigation_revision": revision + 1, "parent_investigation_revision": revision, "status": "cancelled"}
        committed, replayed = self._commit(snapshot=snapshot, expected_digest=expected, expected_revision=revision, idempotency_key=key, request=body)
        live = self.store.read_pointer("investigation", investigation_id)
        return {"accepted": True, "deduplicated": replayed, "investigation": self._view(committed, live["object_digest"])}, 202

    def cancel_node(self, principal: Mapping[str, Any], investigation_id: str, node_id: str, body: Mapping[str, Any]):
        _, value, pointer = self._owned(principal, investigation_id)
        revision, expected, key = self._cas(body, pointer)
        nodes = copy.deepcopy(value["nodes"])
        target = next((node for node in nodes if node["node_id"] == node_id), None)
        if target is None:
            raise LookupError("node 不存在")
        if target["state"] not in {"pending", "ready"}:
            raise W5InvestigationError("node_state_conflict", "只能取消未执行节点")
        target["state"] = "cancelled"
        target["failure_code"] = "node_cancelled"
        snapshot = {**value, "investigation_revision": revision + 1, "parent_investigation_revision": revision, "status": "pending", "nodes": nodes}
        committed, replayed = self._commit(snapshot=snapshot, expected_digest=expected, expected_revision=revision, idempotency_key=key, request={**body, "node_id": node_id})
        live = self.store.read_pointer("investigation", investigation_id)
        return {"accepted": True, "deduplicated": replayed, "investigation": self._view(committed, live["object_digest"])}, 202

    def rerun_node(self, principal: Mapping[str, Any], investigation_id: str, node_id: str, body: Mapping[str, Any]):
        actor, value, pointer = self._owned(principal, investigation_id)
        if value["status"] not in _TERMINAL:
            raise W5InvestigationError("investigation_state_conflict", "仅终态调查可重跑")
        plan_ids = {node["node_id"] for node in value["plan"]["nodes"]}
        if node_id not in plan_ids:
            raise LookupError("node 不存在")
        impacted = {node_id}
        changed = True
        while changed:
            changed = False
            for node in value["plan"]["nodes"]:
                if node["node_id"] not in impacted and any(dep in impacted for dep in node["depends_on"]):
                    impacted.add(node["node_id"])
                    changed = True
        return self._execute_and_commit_fail_closed(
            value,
            pointer,
            {**body, "rerun_node_id": node_id, "impact_closure": sorted(impacted)},
            actor_principal_id=actor["principal_id"],
            rerun_ids=impacted,
        )

    def create_turn(self, principal: Mapping[str, Any], investigation_id: str, body: Mapping[str, Any]):
        _, value, pointer = self._owned(principal, investigation_id)
        revision, expected, key = self._cas(body, pointer)
        if self.model_port is None:
            raise W5InvestigationError("model_port_not_configured", "W5 model port 未配置，未伪造回答", status_code=503, retryable=True)
        anchor = body.get("anchor")
        if not isinstance(anchor, Mapping) or set(anchor) not in ({"node_id", "node_revision"}, {"node_id", "node_revision", "selection_ref"}):
            raise W5InvestigationError("turn_anchor_invalid", "turn anchor 必须显式引用已提交节点", status_code=400)
        node = next((item for item in value["nodes"] if item["node_id"] == anchor["node_id"]), None)
        if node is None or node["execution_revision"] != anchor["node_revision"] or node["state"] not in {"committed", "reused"}:
            raise W5InvestigationError("turn_anchor_stale", "turn anchor 不属于当前已提交节点")
        request = {
            "investigation_id": investigation_id, "investigation_revision": revision,
            "identity": value["identity"], "plan": value["plan"], "question": body["question"], "anchor": copy.deepcopy(dict(anchor)),
            "evidence_graph_revision": value.get("evidence_graph_revision"),
        }
        response = self.model_port.create_turn(copy.deepcopy(request))
        if not isinstance(response, Mapping) or response.get("production_deployed") is not False:
            raise W5InvestigationError("model_turn_invalid", "model port 响应未通过 W5 边界")
        turn = {"turn_id": "turn_" + digest_hex({"request": request, "response": response}), "request": request, "response": copy.deepcopy(dict(response))}
        stored = self.store.put_json("model-turn", turn)
        snapshot = {**value, "investigation_revision": revision + 1, "parent_investigation_revision": revision, "turn_refs": [*value.get("turn_refs", []), {"turn_id": turn["turn_id"], "object_digest": stored["object_digest"]}]}
        committed, replayed = self._commit(snapshot=snapshot, expected_digest=expected, expected_revision=revision, idempotency_key=key, request=body)
        live = self.store.read_pointer("investigation", investigation_id)
        return {"accepted": True, "deduplicated": replayed, "turn": turn, "investigation": self._view(committed, live["object_digest"])}, 202

    def _result_ref(self, value: Mapping[str, Any], result_set_id: str, revision: int) -> Mapping[str, Any]:
        matches = [ref for ref in value.get("result_set_refs", []) if ref["result_set_id"] == result_set_id and ref["result_set_revision"] == revision]
        if not matches:
            raise LookupError("ResultSet 不属于调查")
        return matches[-1]

    def get_result_set(self, principal: Mapping[str, Any], investigation_id: str, result_set_id: str, result_set_revision: int, query: Mapping[str, Any]):
        _, value, _ = self._owned(principal, investigation_id)
        ref = self._result_ref(value, result_set_id, result_set_revision)
        result_set = self.store.get_json("result-set", ref["object_digest"])
        return self.result_sets.page(result_set, page_size=query.get("page_size", 50), page_token=query.get("page_token"))

    def get_evidence_graph(self, principal: Mapping[str, Any], investigation_id: str, graph_revision: int):
        _, value, _ = self._owned(principal, investigation_id)
        ref = next((item for item in value.get("evidence_graph_refs", []) if item["graph_revision"] == graph_revision), None)
        if ref is None:
            raise LookupError("Evidence Graph revision 不属于调查")
        graph = self.store.get_json("evidence-graph", ref["object_digest"])
        self.graphs.validate_trusted_closure(graph)
        # API 投影可使用前缀摘要；受信 store 中的设计 Graph 保持冻结 Schema 要求的 hex64。
        return {**graph, "graph_digest": "sha256:" + graph["graph_digest"]}

    def get_receipts(self, principal: Mapping[str, Any], investigation_id: str, query: Mapping[str, Any]):
        self._owned(principal, investigation_id)
        receipts = [item for item in self.store.list_json("receipt") if item.get("investigation_id") == investigation_id]
        kind = query.get("kind")
        if kind is not None:
            receipts = [item for item in receipts if item.get("receipt_kind") == kind]
        receipts.sort(key=lambda item: (str(item.get("node_id", "")), str(item.get("receipt_digest", ""))))
        offset = 0
        cursor = query.get("cursor")
        if cursor:
            record = self.store.get_json("receipt-cursor", cursor)
            if record.get("investigation_id") != investigation_id or record.get("kind") != kind:
                raise W5InvestigationError("receipt_cursor_context_mismatch", "receipt cursor 不属于当前查询", status_code=400)
            offset = record["offset"]
        page = receipts[offset : offset + 100]
        next_cursor = None
        if offset + len(page) < len(receipts):
            next_cursor = self.store.put_json("receipt-cursor", {"investigation_id": investigation_id, "kind": kind, "offset": offset + len(page)})["object_digest"]
        return {"receipts": page, "next_cursor": next_cursor}

    def create_export(self, principal: Mapping[str, Any], investigation_id: str, body: Mapping[str, Any]):
        _, value, pointer = self._owned(principal, investigation_id)
        revision, expected, key = self._cas(body, pointer)
        ref = self._result_ref(value, body["result_set_id"], body["result_set_revision"])
        result_set = self.store.get_json("result-set", ref["object_digest"])
        auth_base = {"receipt_kind": "authorization", "investigation_id": investigation_id, "owner": value["owner_principal_id"], "action": "export"}
        auth = {**auth_base, "receipt_digest": digest_hex(auth_base)}
        self.store.put_json("receipt", auth)
        export = self.delivery.create_export(
            investigation_id=investigation_id, investigation_revision=revision + 1,
            result_set=result_set, format_name=body["format"], authorization_receipt_digest=auth["receipt_digest"],
        )
        snapshot = {**value, "investigation_revision": revision + 1, "parent_investigation_revision": revision, "export_refs": [*value.get("export_refs", []), {"export_id": export["export_id"], "object_digest": export["object_digest"]}]}
        committed, replayed = self._commit(snapshot=snapshot, expected_digest=expected, expected_revision=revision, idempotency_key=key, request=body)
        live = self.store.read_pointer("investigation", investigation_id)
        return {"accepted": True, "deduplicated": replayed, "export": self._export_view(export), "investigation": self._view(committed, live["object_digest"])}, 202

    @staticmethod
    def _export_view(export: Mapping[str, Any]) -> dict[str, Any]:
        return {"export_id": export["export_id"], "state": "committed", "format": export["format"], "result_set_id": export["result_set_id"], "result_set_revision": export["result_set_revision"], "sha256": export["artifact_sha256"]}

    def _export(self, value: Mapping[str, Any], export_id: str) -> dict[str, Any]:
        ref = next((item for item in value.get("export_refs", []) if item["export_id"] == export_id), None)
        if ref is None:
            raise LookupError("export 不属于调查")
        return self.store.get_json("export", ref["object_digest"])

    def get_export(self, principal: Mapping[str, Any], investigation_id: str, export_id: str):
        _, value, _ = self._owned(principal, investigation_id)
        return {"export": self._export_view(self._export(value, export_id))}

    def get_export_artifact(self, principal: Mapping[str, Any], investigation_id: str, export_id: str):
        _, value, _ = self._owned(principal, investigation_id)
        export = self._export(value, export_id)
        content, content_type, filename = self.delivery.artifact(export)
        return {"content": content, "content_type": content_type, "filename": filename[:180], "sha256": export["artifact_sha256"]}


_runtime_instance: CountryOutageP2S1InvestigationRuntime | None = None


def configure_country_outage_p2_s1_investigation_runtime(runtime: CountryOutageP2S1InvestigationRuntime | None) -> None:
    global _runtime_instance
    _runtime_instance = runtime


def get_country_outage_p2_s1_investigation_runtime() -> CountryOutageP2S1InvestigationRuntime:
    if _runtime_instance is None:
        raise W5InvestigationError("investigation_runtime_not_configured", "W5 本地 runtime 未配置", status_code=503, retryable=True)
    return _runtime_instance


__all__ = [
    "CountryOutageP2S1InvestigationRuntime",
    "LocalFixtureSidecarModelPort",
    "ModelTurnPort",
    "PlanningGroundingPort",
    "TrustedFixturePlanningGroundingPort",
    "W5InvestigationError",
    "bind_output_to_argument",
    "boundary_response",
    "build_local_fixture_runtime",
    "configure_country_outage_p2_s1_investigation_runtime",
    "get_country_outage_p2_s1_investigation_runtime",
    "validate_authorization_gate",
    "validate_identity_gate",
    "validate_plan_admission",
    "validate_registry_gate",
]
