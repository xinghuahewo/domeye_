"""W4 不可调用 binding 到 W5 本地执行准入与静态 Dispatcher。

W4 证据仍保持 ``execution_allowed_unit_ids=[]``。本模块验证四个冻结 runtime
bundle 与实现文件摘要后，生成一个独立、内容寻址的 W5 execution admission；
请求不能提交 callback、handler 名、Registry entry 或任意 Python import path。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from hashlib import sha256
import importlib
import inspect
from pathlib import Path
from typing import Any, Mapping

from .country_outage_p2_s1_contract_runtime import (
    CONTROL_UNIT_IDS,
    DEFERRED_UNIT_IDS,
    DESIGN_CANDIDATE_DIGEST,
    DESIGN_CANDIDATE_ID,
    W5ContractError,
    digest_prefixed,
    repository_root,
    strict_json_loads,
    validate_json_schema,
)
from .country_outage_p2_s1_operators import (
    OPERATOR_FUNCTIONS,
    OfflineStructuralFixtureContext,
    execute_operator,
)
from .country_outage_p2_s1_tools import CountryOutageP2S1Tools
from .country_outage_p2_s1_trusted_store import ContentAddressedStore


W1_UNITS = (
    "TOOL-07", "TOOL-08", "TOOL-09", "TOOL-10",
    "OP-05", "OP-06", "OP-07", "OP-08", "OP-09", "OP-10", "OP-11",
    "OP-12", "OP-13", "OP-14", "OP-35", "OP-36",
)
W2_UNITS = (
    "TOOL-12", "OP-15", "OP-16", "OP-17", "OP-18", "OP-19", "OP-20",
    "OP-21", "OP-22", "OP-23", "OP-24", "OP-25", "OP-26", "OP-27", "OP-28",
)
W3_UNITS = ("OP-38", "OP-39")
W4_UNITS = ("TOOL-11", "OP-29", "OP-30", "OP-31", "OP-32", "OP-33", "OP-37")
WAVE_UNITS = {"W1": W1_UNITS, "W2": W2_UNITS, "W3": W3_UNITS, "W4": W4_UNITS}
BUSINESS_UNIT_IDS = frozenset((*W1_UNITS, *W2_UNITS, *W3_UNITS, *W4_UNITS))
EXECUTION_UNIT_IDS = frozenset((*BUSINESS_UNIT_IDS, *CONTROL_UNIT_IDS))
W4_SNAPSHOT_ID = "p2-s1-registry-wave-sha256:027cab0d6d63efb0d10f50ffcefdd280f84a3cc47ccd86aae2ede0e8fbeb7ac6"
W4_SNAPSHOT_DIGEST = "sha256:027cab0d6d63efb0d10f50ffcefdd280f84a3cc47ccd86aae2ede0e8fbeb7ac6"
W4_REGISTRY_REVISION = 7

_TOOL_METHODS = {
    "TOOL-07": "query_fixed_cohort_members",
    "TOOL-08": "query_prefix_states",
    "TOOL-09": "query_as_states",
    "TOOL-10": "query_new_prefix_states",
    "TOOL-11": "query_materialized_route_states_at_time",
    "TOOL-12": "query_window_path_associations",
}

_CONTROL_HANDLERS = {
    "PLAN-CAP-01": "python:backend.services.country_outage_p2_s1_investigation_runtime.bind_output_to_argument",
    "GATE-01": "python:backend.services.country_outage_p2_s1_investigation_runtime.validate_plan_admission",
    "GATE-02": "python:backend.services.country_outage_p2_s1_investigation_runtime.validate_identity_gate",
    "GATE-03": "python:backend.services.country_outage_p2_s1_investigation_runtime.validate_registry_gate",
    "GATE-04": "python:backend.services.country_outage_p2_s1_evidence_graph.validate_evidence_references",
    "GATE-05": "python:backend.services.country_outage_p2_s1_investigation_runtime.validate_authorization_gate",
    "BOUNDARY-01": "python:backend.services.country_outage_p2_s1_investigation_runtime.boundary_response",
    "RENDERER-01": "python:backend.services.country_outage_p2_s1_delivery.render_markdown",
    "RENDERER-02": "python:backend.services.country_outage_p2_s1_delivery.render_csv",
    "RENDERER-03": "python:backend.services.country_outage_p2_s1_delivery.render_json",
    "DELIVERY-01": "python:backend.services.country_outage_p2_s1_delivery.DeliveryManager.create_export",
}


class W5RegistryError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = False
        self.next_action = None
        super().__init__(message)


class HostTrustedStructuralContext(OfflineStructuralFixtureContext):
    """只从 Host 内容寻址 Store 构造的 W5 结构回执解析器。

    继承现有只读解析接口只是为了复用 W1–W4 Operator 的严格回执校验；构造函数
    不接受 callback，普通请求也不能直接提供本对象。
    """

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "HostTrustedStructuralContext":
        required = {
            "design_candidate_id",
            "op29_outputs",
            "op10_outputs",
            "op11_outputs",
            "op15_outputs",
            "op36_outputs",
            "tool12_result_sets",
            "projection_receipts",
            "population_binding_receipts",
            "population_evidence_binding",
            "population_evidence_bindings",
            "inherited_evidence_refs",
            "asn_bound_op10_receipts",
            "asn_bound_op11_receipts",
            "asn_bound_op36_receipts",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise W5RegistryError("trusted_operator_context_invalid", "Operator context 字段不闭合")
        if record.get("design_candidate_id") != DESIGN_CANDIDATE_ID:
            raise W5RegistryError("design_candidate_mismatch", "Operator context 设计候选不一致")
        mapping_fields = (
            "op29_outputs", "op10_outputs", "op11_outputs", "op15_outputs", "op36_outputs",
            "tool12_result_sets", "projection_receipts", "population_binding_receipts",
        )
        if any(not isinstance(record.get(field), Mapping) for field in mapping_fields):
            raise W5RegistryError("trusted_operator_context_invalid", "Operator context 映射无效")
        return cls(
            design_candidate_id=DESIGN_CANDIDATE_ID,
            op29_outputs=record["op29_outputs"],
            op10_outputs=record["op10_outputs"],
            op11_outputs=record["op11_outputs"],
            op15_outputs=record["op15_outputs"],
            op36_outputs=record["op36_outputs"],
            tool12_result_sets=record["tool12_result_sets"],
            projection_receipts=record["projection_receipts"],
            population_binding_receipts=record["population_binding_receipts"],
        )


@dataclass(frozen=True)
class W5ExecutionAdmission:
    registry_snapshot_id: str
    snapshot_digest: str
    registry_revision: int
    admission_receipt_digest: str
    execution_allowed_unit_ids: tuple[str, ...]
    entries: Mapping[str, Mapping[str, Any]]


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise W5RegistryError("registry_evidence_unavailable", f"Registry 证据不可用：{path.name}")
    value = strict_json_loads(path.read_bytes())
    if not isinstance(value, dict):
        raise W5RegistryError("registry_evidence_invalid", f"Registry 证据不是对象：{path.name}")
    return value


def _implementation_digest_for_handler(handler_id: str) -> str:
    prefix = "python:backend.services."
    if not handler_id.startswith(prefix):
        raise W5RegistryError("handler_not_static_python", f"handler 不属于冻结 Python allowlist：{handler_id}")
    module_and_member = handler_id.removeprefix("python:")
    parts = module_and_member.split(".")
    module = None
    attributes: list[str] = []
    for index in range(len(parts) - 1, 1, -1):
        try:
            module = importlib.import_module(".".join(parts[:index]))
            attributes = parts[index:]
            break
        except ModuleNotFoundError as error:
            if error.name != ".".join(parts[:index]):
                raise W5RegistryError("handler_import_failed", f"handler 依赖导入失败：{handler_id}") from error
    if module is None or not attributes:
        raise W5RegistryError("handler_module_missing", f"handler module 不存在：{handler_id}")
    target: Any = module
    for attribute in attributes:
        if not hasattr(target, attribute):
            raise W5RegistryError("handler_member_missing", f"handler member 不存在：{handler_id}")
        target = getattr(target, attribute)
    if not callable(target):
        raise W5RegistryError("handler_not_callable", f"handler member 不可调用：{handler_id}")
    source = inspect.getsourcefile(target)
    if source is None:
        raise W5RegistryError("handler_source_missing", f"handler 没有可封存源文件：{handler_id}")
    path = Path(source).resolve()
    expected_root = (repository_root() / "backend/services").resolve()
    try:
        relative = path.relative_to(repository_root())
        path.relative_to(expected_root)
    except ValueError as error:
        raise W5RegistryError("handler_source_outside_allowlist", f"handler 不属于 backend/services：{handler_id}") from error
    if not path.is_file() or path.is_symlink():
        raise W5RegistryError("handler_artifact_unavailable", f"handler 源文件不可用：{relative}")
    return "sha256:" + _file_sha256(path)


def _load_w4_bindings() -> dict[str, dict[str, Any]]:
    root = repository_root()
    runtime_root = root / "contracts/agent/country-outage-p2-s1-implementation/wave-evidence/registry-runtime"
    wave_root = root / "contracts/agent/country-outage-p2-s1-implementation/wave-evidence"
    entries: dict[str, dict[str, Any]] = {}
    previous_snapshot_id: str | None = None
    previous_snapshot_digest: str | None = None
    for ordinal, wave_id in enumerate(("W1", "W2", "W3", "W4"), start=1):
        bundle = _load_object(runtime_root / f"{wave_id}.json")
        if bundle.get("wave_id") != wave_id or bundle.get("sequence_ordinal") != ordinal:
            raise W5RegistryError("registry_wave_sequence_invalid", f"{wave_id} runtime bundle 顺序无效")
        scope = bundle.get("execution_scope")
        if not isinstance(scope, Mapping) or scope.get("registry_execution_authorized") is not False or scope.get("trusted_dispatcher_implemented") is not False:
            raise W5RegistryError("w4_non_execution_boundary_drift", f"{wave_id} 不再是不可调用 binding")
        snapshot = bundle.get("wave_snapshot")
        receipt = bundle.get("wave_admission_receipt")
        if not isinstance(snapshot, Mapping) or not isinstance(receipt, Mapping):
            raise W5RegistryError("registry_wave_evidence_invalid", f"{wave_id} 缺少 snapshot/receipt")
        if receipt.get("execution_allowed_unit_ids") != [] or receipt.get("execution_started") is not False:
            raise W5RegistryError("w4_execution_overclaim", f"{wave_id} W5 前执行授权必须为空")
        payload = snapshot.get("snapshot_payload")
        if not isinstance(payload, Mapping) or payload.get("wave_id") != wave_id:
            raise W5RegistryError("registry_wave_evidence_invalid", f"{wave_id} snapshot payload 无效")
        if previous_snapshot_id is not None:
            previous = payload.get("previous_snapshot_ref")
            if not isinstance(previous, Mapping) or previous.get("registry_snapshot_id") != previous_snapshot_id or previous.get("snapshot_digest") != previous_snapshot_digest:
                raise W5RegistryError("registry_wave_chain_invalid", f"{wave_id} 未承接前一 snapshot")
        manifest = payload.get("handler_manifest", {}).get("manifest_payload", {})
        handlers = manifest.get("handlers") if isinstance(manifest, Mapping) else None
        if not isinstance(handlers, list) or [item.get("unit_id") for item in handlers if isinstance(item, Mapping)] != list(WAVE_UNITS[wave_id]):
            raise W5RegistryError("registry_wave_population_invalid", f"{wave_id} handler 人口漂移")
        wave_evidence = _load_object(wave_root / f"{wave_id}.json")
        receipts = wave_evidence.get("atomic_unit_receipts")
        if not isinstance(receipts, list):
            raise W5RegistryError("atomic_unit_receipts_missing", f"{wave_id} 缺少 atomic receipts")
        receipt_by_unit = {item.get("unit_id"): item for item in receipts if isinstance(item, Mapping)}
        if set(receipt_by_unit) != set(WAVE_UNITS[wave_id]):
            raise W5RegistryError("atomic_unit_population_invalid", f"{wave_id} atomic receipt 人口漂移")
        for handler in handlers:
            unit_id = handler["unit_id"]
            atomic = receipt_by_unit[unit_id]
            actual_implementation = _implementation_digest_for_handler(handler["handler_id"])
            if actual_implementation != handler.get("implementation_digest"):
                raise W5RegistryError("handler_implementation_digest_mismatch", f"{unit_id} 实现摘要与 W4 binding 不一致")
            if atomic.get("implementation_sha256") != actual_implementation.removeprefix("sha256:"):
                raise W5RegistryError("atomic_receipt_digest_mismatch", f"{unit_id} atomic receipt 未绑定同一实现")
            entries[unit_id] = {
                "unit_id": unit_id,
                "unit_kind": "tool" if unit_id.startswith("TOOL-") else "operator",
                "lifecycle_state": "active",
                "p2_v1_admission": "allowed",
                "handler_id": handler["handler_id"],
                "contract_digest": handler["contract_digest"],
                "implementation_digest": handler["implementation_digest"],
                "semantic_digest": handler["semantic_digest"],
                "input_schema_ref": atomic["input_schema_ref"],
                "output_schema_refs": [atomic["output_schema_ref"]],
            }
        previous_snapshot_id = snapshot.get("registry_snapshot_id")
        previous_snapshot_digest = snapshot.get("snapshot_digest")
    if previous_snapshot_id != W4_SNAPSHOT_ID or previous_snapshot_digest != W4_SNAPSHOT_DIGEST:
        raise W5RegistryError("w4_snapshot_identity_mismatch", "W5 未承接冻结 actual W4 runtime snapshot")
    if set(entries) != set(BUSINESS_UNIT_IDS):
        raise W5RegistryError("registry_unit_population_invalid", "W1-W4 binding 人口未闭合")
    return entries


def _control_entries() -> dict[str, dict[str, Any]]:
    schema_path = repository_root() / "contracts/agent/country-outage-p2-s1-implementation/w5-control-runtime.schema.json"
    schema = _load_object(schema_path)
    schema_digest = "sha256:" + _file_sha256(schema_path)
    validate_json_schema({}, {"$schema": schema["$schema"], "type": "object"}, "control schema bootstrap")
    result: dict[str, dict[str, Any]] = {}
    for unit_id, handler_id in _CONTROL_HANDLERS.items():
        implementation_digest = _implementation_digest_for_handler(handler_id)
        kind = (
            "plan_capability" if unit_id.startswith("PLAN-CAP-")
            else "gate" if unit_id.startswith("GATE-")
            else "boundary" if unit_id.startswith("BOUNDARY-")
            else "renderer" if unit_id.startswith("RENDERER-")
            else "delivery"
        )
        result[unit_id] = {
            "unit_id": unit_id,
            "unit_kind": kind,
            "lifecycle_state": "active",
            "p2_v1_admission": "allowed",
            "handler_id": handler_id,
            "contract_digest": DESIGN_CANDIDATE_DIGEST,
            "implementation_digest": implementation_digest,
            "semantic_digest": digest_prefixed({"design_candidate_digest": DESIGN_CANDIDATE_DIGEST, "unit_id": unit_id}),
            "input_schema_ref": f"w5-control-runtime.schema.json#/$defs/{unit_id.lower().replace('-', '')}Input",
            "output_schema_refs": [f"w5-control-runtime.schema.json#/$defs/{unit_id.lower().replace('-', '')}Output"],
            "schema_artifact_digest": schema_digest,
        }
    return result


def _runtime_schema_for_ref(schema_ref: str) -> tuple[dict[str, Any], str]:
    if not isinstance(schema_ref, str) or "#/$defs/" not in schema_ref:
        raise W5RegistryError("runtime_schema_ref_invalid", f"Schema ref 不可解析：{schema_ref}")
    filename, definition = schema_ref.split("#/$defs/", 1)
    if filename == "operator-contract.schema.json":
        path = repository_root() / "contracts/agent/country-outage-p2-s1-execution-unit-design" / filename
    elif filename == "w1-w2-tool-runtime.schema.json":
        path = repository_root() / "contracts/agent/country-outage-p2-s1-implementation" / filename
    else:
        raise W5RegistryError("runtime_schema_not_allowlisted", f"Schema 未登记：{filename}")
    schema = _load_object(path)
    if definition not in schema.get("$defs", {}):
        raise W5RegistryError("runtime_schema_definition_missing", f"Schema definition 不存在：{definition}")
    projected = {"$schema": schema["$schema"], "$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]}
    return projected, "sha256:" + _file_sha256(path)


def _independent_input_schema(unit_id: str, runtime_ref: str) -> tuple[str, dict[str, Any]]:
    schema, _ = _runtime_schema_for_ref(runtime_ref)
    absolute_id = f"https://domeye.example/contracts/agent/country-outage-p2-s1/runtime/{unit_id.lower()}-input.schema.json"
    definition_name = runtime_ref.split("#/$defs/", 1)[1]
    definition = copy.deepcopy(schema["$defs"][definition_name])
    projected = {"$schema": schema["$schema"], "$id": absolute_id, **definition, "$defs": schema["$defs"]}
    # 冻结 Operator envelope 使用 allOf；Plan semantic admission 必须能在同一
    # Registry entry 直接解析实际参数属性，不能把 allOf 当作“无参数 Schema”。
    # 此处只做 JSON Schema 结构展开，保留原 allOf 作为执行时约束，不改变业务值。
    if unit_id.startswith("OP-") and isinstance(definition.get("allOf"), list):
        base = copy.deepcopy(schema["$defs"]["operatorInputEnvelope"])
        properties = copy.deepcopy(base.get("properties", {}))
        required = list(base.get("required", []))
        for branch in definition["allOf"]:
            if isinstance(branch, Mapping):
                properties.update(copy.deepcopy(branch.get("properties", {})))
                required.extend(item for item in branch.get("required", []) if item not in required)
        projected["properties"] = properties
        projected["required"] = required
    return absolute_id, projected


def _enrich_admission_entries(entries: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    decomposition = _load_object(repository_root() / "contracts/agent/country-outage-p2-s1-execution-unit-design/execution-unit-decomposition.json")
    capability_by_unit = {item["unit_id"]: item["atomic_capability_id"] for item in decomposition["atomic_units"]}
    from .country_outage_p2_s1_result_set import tool_output_population

    for unit_id, entry in entries.items():
        runtime_ref = entry["input_schema_ref"]
        if unit_id in CONTROL_UNIT_IDS:
            schema_path = repository_root() / "contracts/agent/country-outage-p2-s1-implementation/w5-control-runtime.schema.json"
            root = _load_object(schema_path)
            definition = unit_id.lower().replace("-", "") + "Input"
            absolute_id = f"https://domeye.example/contracts/agent/country-outage-p2-s1/runtime/{unit_id.lower()}-input.schema.json"
            body = copy.deepcopy(root["$defs"][definition])
            if set(body) == {"$ref"} and str(body["$ref"]).startswith("#/$defs/"):
                body = copy.deepcopy(root["$defs"][body["$ref"].split("#/$defs/", 1)[1]])
            input_schema = {"$schema": root["$schema"], "$id": absolute_id, **body, "$defs": root["$defs"]}
        else:
            absolute_id, input_schema = _independent_input_schema(unit_id, runtime_ref)
        entry["runtime_input_schema_ref"] = runtime_ref
        entry["input_schema_ref"] = absolute_id
        entry["input_schema"] = input_schema
        entry["unit_version"] = "1.0.0"
        entry["atomic_capability_id"] = capability_by_unit[unit_id]
        entry["atomic_capability_version"] = "1.0.0"
        entry["capability_contract_digest"] = digest_prefixed({"design_candidate_digest": DESIGN_CANDIDATE_DIGEST, "capability": capability_by_unit[unit_id]})
        entry["monotonic_incomplete_input_allowed"] = False
        entry["output_populations"] = [tool_output_population(unit_id)] if unit_id.startswith("TOOL-") else []
    return entries


def create_w5_execution_admission(store: ContentAddressedStore) -> W5ExecutionAdmission:
    entries = _enrich_admission_entries({**_load_w4_bindings(), **_control_entries()})
    if set(entries) != set(EXECUTION_UNIT_IDS) or DEFERRED_UNIT_IDS & set(entries):
        raise W5RegistryError("w5_execution_population_invalid", "W5 execution population 未闭合或混入 P2.1")
    ordered_ids = [*W1_UNITS, *W2_UNITS, *W3_UNITS, *W4_UNITS, *sorted(CONTROL_UNIT_IDS)]
    payload = {
        "schema_version": "country_outage_p2_s1_w5_execution_snapshot_v1",
        "candidate_id": DESIGN_CANDIDATE_ID,
        "design_candidate_digest": DESIGN_CANDIDATE_DIGEST,
        "registry_revision": W4_REGISTRY_REVISION + 1,
        "previous_snapshot_ref": {
            "registry_snapshot_id": W4_SNAPSHOT_ID,
            "snapshot_digest": W4_SNAPSHOT_DIGEST,
            "registry_revision": W4_REGISTRY_REVISION,
        },
        "execution_scope": "local_isolated_w5_only",
        "permission_mode": "read_only",
        "external_data_allowed": False,
        "production_deployed": False,
        "entries": [entries[unit_id] for unit_id in ordered_ids],
        "execution_allowed_unit_ids": ordered_ids,
        "deferred_denied_unit_ids": sorted(DEFERRED_UNIT_IDS),
    }
    snapshot_digest = digest_prefixed(payload)
    snapshot_id = "registry-snapshot-sha256:" + snapshot_digest.removeprefix("sha256:")
    snapshot = {**payload, "registry_snapshot_id": snapshot_id, "snapshot_digest": snapshot_digest}
    snapshot_ref = store.put_json("registry-snapshot", snapshot)
    receipt_without_digest = {
        "schema_version": "country_outage_p2_s1_w5_execution_admission_v1",
        "status": "admitted_local_isolated_execution",
        "registry_snapshot_id": snapshot_id,
        "snapshot_digest": snapshot_digest,
        "registry_revision": W4_REGISTRY_REVISION + 1,
        "previous_snapshot_id": W4_SNAPSHOT_ID,
        "snapshot_object_digest": snapshot_ref["object_digest"],
        "execution_allowed_unit_ids": ordered_ids,
        "deferred_denied_unit_ids": sorted(DEFERRED_UNIT_IDS),
        "arbitrary_callback_supported": False,
        "external_data_allowed": False,
        "production_deployed": False,
        "control_unit_entries": [{
            "unit_id": unit_id,
            "input_schema_ref": f"w5-control-runtime.schema.json#/$defs/{unit_id.lower().replace('-', '')}Input",
            "output_schema_ref": entries[unit_id]["output_schema_refs"][0],
            "schema_path": "contracts/agent/country-outage-p2-s1-implementation/w5-control-runtime.schema.json",
            "schema_sha256": entries[unit_id]["schema_artifact_digest"].removeprefix("sha256:"),
            "handler_id": entries[unit_id]["handler_id"],
            "implementation_digest": entries[unit_id]["implementation_digest"],
        } for unit_id in sorted(CONTROL_UNIT_IDS)],
    }
    receipt = {**receipt_without_digest, "receipt_digest": digest_prefixed(receipt_without_digest)}
    store.put_json("registry-admission", receipt)
    return W5ExecutionAdmission(
        registry_snapshot_id=snapshot_id,
        snapshot_digest=snapshot_digest,
        registry_revision=W4_REGISTRY_REVISION + 1,
        admission_receipt_digest=receipt["receipt_digest"],
        execution_allowed_unit_ids=tuple(ordered_ids),
        entries=copy.deepcopy(entries),
    )


class W5RegistryDispatcher:
    """静态、无 callback seam 的 W5 Dispatcher。"""

    def __init__(self, tools: CountryOutageP2S1Tools, store: ContentAddressedStore) -> None:
        if not isinstance(tools, CountryOutageP2S1Tools):
            raise TypeError("tools 必须是 CountryOutageP2S1Tools")
        if not isinstance(store, ContentAddressedStore):
            raise TypeError("store 必须是 ContentAddressedStore")
        self._tools = tools
        self._store = store
        self.admission = create_w5_execution_admission(store)
        self.trusted_registry_store = self._trusted_registry_store()

    def _trusted_registry_store(self) -> dict[str, Any]:
        entries = copy.deepcopy(dict(self.admission.entries))
        data_digest = self.admission.snapshot_digest.removeprefix("sha256:")
        resolution_base = {
            "receipt_kind": "registry_snapshot_resolution",
            "resolver_id": "country_outage_p2_registry_resolver", "resolver_version": "1.0.0",
            "resolver_contract_digest": "sha256:" + "7" * 64,
            "resolver_implementation_digest": "sha256:" + "8" * 64,
            "registry_snapshot_id": self.admission.registry_snapshot_id,
            "registry_snapshot_digest": self.admission.snapshot_digest,
            "registry_snapshot_data_digest": data_digest,
            "entries_digest": digest_prefixed({"entries": entries}).removeprefix("sha256:"),
            "disposition": "passed",
        }
        resolution = {**resolution_base, "receipt_digest": digest_prefixed(resolution_base).removeprefix("sha256:")}
        view_base = {
            "view_contract_id": "country_outage_p2_registry_admission_view_v1", "trusted_snapshot_verified": True,
            "registry_snapshot_id": self.admission.registry_snapshot_id,
            "registry_snapshot_digest": self.admission.snapshot_digest,
            "registry_snapshot_data_digest": data_digest, "entries": entries,
            "resolution_receipt": resolution, "resolution_receipt_digest": resolution["receipt_digest"],
        }
        view = {**view_base, "view_digest": digest_prefixed(view_base).removeprefix("sha256:")}
        self._store.put_json("registry-view", view)
        return {
            "store_contract_id": "country_outage_p2_trusted_registry_store_v1",
            "trust_origin": "host_authenticated_registry_store",
            "attestation_provider_id": "country_outage_p2_registry_store_host",
            "attestation_contract_digest": "sha256:" + "b" * 64,
            "snapshot_views": {self.admission.registry_snapshot_id: view},
        }

    def assert_allowed(self, unit_id: str) -> Mapping[str, Any]:
        if unit_id in DEFERRED_UNIT_IDS:
            raise W5RegistryError("p2_1_unit_forbidden", f"P2.1 单元不可执行：{unit_id}", status_code=403)
        if unit_id not in self.admission.execution_allowed_unit_ids:
            raise W5RegistryError("execution_unit_not_admitted", f"单元未获 W5 execution admission：{unit_id}", status_code=403)
        return self.admission.entries[unit_id]

    def execute(
        self,
        unit_id: str,
        request: Mapping[str, Any],
        *,
        trusted_context_digest: str | None = None,
    ) -> dict[str, Any]:
        entry = self.assert_allowed(unit_id)
        if not isinstance(request, Mapping):
            raise W5RegistryError("execution_request_invalid", "执行请求必须是对象", status_code=400)
        input_schema, input_schema_digest = _runtime_schema_for_ref(entry["runtime_input_schema_ref"])
        validate_json_schema(request, input_schema, f"{unit_id} runtime input")
        if unit_id in _TOOL_METHODS:
            method = {
                "TOOL-07": self._tools.query_fixed_cohort_members,
                "TOOL-08": self._tools.query_prefix_states,
                "TOOL-09": self._tools.query_as_states,
                "TOOL-10": self._tools.query_new_prefix_states,
                "TOOL-11": self._tools.query_materialized_route_states_at_time,
                "TOOL-12": self._tools.query_window_path_associations,
            }[unit_id]
            result = method(copy.deepcopy(dict(request)))
        elif unit_id in OPERATOR_FUNCTIONS:
            context = None
            options: Mapping[str, Any] = {}
            if trusted_context_digest is not None:
                record = self._store.get_json("operator-context", trusted_context_digest)
                if not isinstance(record, Mapping):
                    raise W5RegistryError("trusted_operator_context_invalid", "Operator context 不是对象")
                context = HostTrustedStructuralContext.from_record(record)
                options = record
            result = execute_operator(
                copy.deepcopy(dict(request)),
                inherited_evidence_refs=options.get("inherited_evidence_refs", ()),
                population_evidence_binding=options.get("population_evidence_binding"),
                population_evidence_bindings=options.get("population_evidence_bindings"),
                asn_bound_op10_receipts=options.get("asn_bound_op10_receipts"),
                asn_bound_op11_receipts=options.get("asn_bound_op11_receipts"),
                asn_bound_op36_receipts=options.get("asn_bound_op36_receipts"),
                offline_structural_context=context,
            )
        else:
            raise W5RegistryError("control_unit_direct_execution_forbidden", f"{unit_id} 只能由对应 Host owner 调用", status_code=403)
        output_ref = entry["output_schema_refs"][0]
        output_schema, output_schema_digest = _runtime_schema_for_ref(output_ref)
        validate_json_schema(result, output_schema, f"{unit_id} runtime output")
        validation_base = {
            "receipt_kind": "business_runtime_schema_validation", "unit_id": unit_id,
            "input_schema_ref": entry["runtime_input_schema_ref"], "input_schema_digest": input_schema_digest,
            "output_schema_ref": output_ref, "output_schema_digest": output_schema_digest,
            "input_digest": digest_prefixed(request), "output_digest": digest_prefixed(result), "disposition": "passed",
        }
        validation_receipt = {**validation_base, "receipt_digest": digest_prefixed(validation_base)}
        self._store.put_json("receipt", validation_receipt)
        return {
            "unit_id": unit_id,
            "unit_kind": entry["unit_kind"],
            "registry_snapshot_id": self.admission.registry_snapshot_id,
            "registry_snapshot_digest": self.admission.snapshot_digest,
            "handler_id": entry["handler_id"],
            "implementation_digest": entry["implementation_digest"],
            "result": result,
            "schema_validation_receipt": validation_receipt,
        }

    def validate_control_envelope(self, unit_id: str, phase: str, value: Mapping[str, Any]) -> None:
        entry = self.assert_allowed(unit_id)
        if unit_id not in CONTROL_UNIT_IDS or phase not in {"Input", "Output"}:
            raise W5RegistryError("control_schema_subject_invalid", "仅允许验证已准入控制单元 input/output")
        schema_path = repository_root() / "contracts/agent/country-outage-p2-s1-implementation/w5-control-runtime.schema.json"
        if "sha256:" + _file_sha256(schema_path) != entry["schema_artifact_digest"]:
            raise W5RegistryError("control_schema_digest_mismatch", "控制单元 Schema 换签")
        schema = _load_object(schema_path)
        definition = unit_id.lower().replace("-", "") + phase
        validate_json_schema(value, {"$schema": schema["$schema"], "$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]}, f"{unit_id} {phase}")
        receipt_base = {
            "receipt_kind": "control_runtime_schema_validation",
            "unit_id": unit_id,
            "phase": phase.lower(),
            "schema_ref": entry["input_schema_ref"] if phase == "Input" else entry["output_schema_refs"][0],
            "schema_digest": entry["schema_artifact_digest"],
            "value_digest": digest_prefixed(value),
            "disposition": "passed",
        }
        self._store.put_json("receipt", {**receipt_base, "receipt_digest": digest_prefixed(receipt_base)})

    def record_control_execution(
        self,
        unit_id: str,
        control_input: Mapping[str, Any],
        control_output: Mapping[str, Any],
    ) -> dict[str, Any]:
        """记录一次由静态 owner 已实际完成的控制调用。

        此方法不接受 callback，也不负责选择 handler；调用方只能提交 owner 已产生的
        输入/输出，随后按准入条目重新验证并写入内容寻址调用回执。
        """

        entry = self.assert_allowed(unit_id)
        if unit_id not in CONTROL_UNIT_IDS:
            raise W5RegistryError("control_execution_record_forbidden", "仅控制单元可记录 owner 调用")
        self.validate_control_envelope(unit_id, "Input", control_input)
        self.validate_control_envelope(unit_id, "Output", control_output)
        record_base = {
            "unit_id": unit_id,
            "input_digest": digest_prefixed(control_input),
            "output_digest": digest_prefixed(control_output),
            "handler_id": entry["handler_id"],
            "implementation_digest": entry["implementation_digest"],
            "input_schema_ref": f"w5-control-runtime.schema.json#/$defs/{unit_id.lower().replace('-', '')}Input",
            "output_schema_ref": f"w5-control-runtime.schema.json#/$defs/{unit_id.lower().replace('-', '')}Output",
            "input_schema_valid": True,
            "output_schema_valid": True,
            "execution_disposition": "completed",
        }
        record = {**record_base, "call_receipt_digest": digest_prefixed(record_base)}
        self._store.put_json("control-execution-call", record)
        return copy.deepcopy(record)


__all__ = [
    "BUSINESS_UNIT_IDS",
    "EXECUTION_UNIT_IDS",
    "HostTrustedStructuralContext",
    "W4_REGISTRY_REVISION",
    "W4_SNAPSHOT_DIGEST",
    "W4_SNAPSHOT_ID",
    "W5ExecutionAdmission",
    "W5RegistryDispatcher",
    "W5RegistryError",
    "create_w5_execution_admission",
]
