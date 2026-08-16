"""P2-S1 W5 Host 合同、摘要与闭包验证基础。

本模块只验证结构、身份、摘要和引用闭包，不执行 Tool/Operator 业务语义。设计
Schema 从当前仓库的冻结合同目录只读加载；普通请求不能选择任意文件或 Schema。
"""

from __future__ import annotations

import copy
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker


# backend 进程会以 ``services.*`` 导入，而既有 W1/W2 原子模块内部仍
# 使用 ``backend.services.*``。只将由 __file__ 确定的仓库根加入搜索路径，
# 既不接受请求路径，也不改变新 W5 模块之间的相对导入。
if __package__ == "services":
    _REPOSITORY_IMPORT_ROOT = str(Path(__file__).resolve().parents[2])
    if _REPOSITORY_IMPORT_ROOT not in sys.path:
        sys.path.insert(0, _REPOSITORY_IMPORT_ROOT)


DESIGN_CANDIDATE_ID = "country-outage-p2-s1-s1d-6-04135cee55b39ce5d574f7e4"
DESIGN_CANDIDATE_DIGEST = "sha256:d0256d9f1246191df2d48432655ea384acb2e5a6844b15a78f80e4c9f5e55e74"
DEFERRED_UNIT_IDS = frozenset({"TOOL-13", "OP-34", "PLAN-CAP-02"})
CONTROL_UNIT_IDS = frozenset(
    {
        "PLAN-CAP-01",
        "GATE-01",
        "GATE-02",
        "GATE-03",
        "GATE-04",
        "GATE-05",
        "BOUNDARY-01",
        "RENDERER-01",
        "RENDERER-02",
        "RENDERER-03",
        "DELIVERY-01",
    }
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
PREFIXED_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_FROZEN_CONTRACTS = {
    "investigation-plan": "investigation-plan.schema.json",
    "result-set": "result-set.schema.json",
    "evidence-graph": "evidence-graph.schema.json",
    "runtime-consistency": "runtime-commit-consistency-contract.json",
    "operator": "operator-contract.schema.json",
    "model-flow": "dual-model-answer-flow.schema.json",
}


class W5ContractError(ValueError):
    """合同验证失败，code 可稳定用于 API/测试断言。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def canonical_json(value: Any) -> str:
    """冻结 Python canonical JSON；拒绝 NaN、Infinity 和非 JSON 值。"""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise W5ContractError("non_canonical_json", str(error)) from error


def digest_hex(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest_prefixed(value: Any) -> str:
    return "sha256:" + digest_hex(value)


def digest_without_fields(value: Mapping[str, Any], *excluded: str, prefixed: bool = False) -> str:
    subject = {key: copy.deepcopy(item) for key, item in value.items() if key not in excluded}
    return digest_prefixed(subject) if prefixed else digest_hex(subject)


def _no_duplicate_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise W5ContractError("duplicate_json_key", f"重复 JSON key：{key}")
        result[key] = value
    return result


def strict_json_loads(raw: str | bytes) -> Any:
    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicate_object)
    except W5ContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise W5ContractError("invalid_json", str(error)) from error
    _reject_non_json(value, "$")
    return value


def _reject_non_json(value: Any, location: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise W5ContractError("non_finite_number", f"{location} 包含非有限数字")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_non_json(item, f"{location}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise W5ContractError("non_json_key", f"{location} 包含非字符串 key")
            _reject_non_json(item, f"{location}.{key}")
        return
    raise W5ContractError("non_json_value", f"{location} 包含非 JSON 类型")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_frozen_contract(contract_id: str) -> dict[str, Any]:
    filename = _FROZEN_CONTRACTS.get(contract_id)
    if filename is None:
        raise W5ContractError("contract_not_allowlisted", f"合同未登记：{contract_id}")
    path = (
        repository_root()
        / "contracts/agent/country-outage-p2-s1-execution-unit-design"
        / filename
    )
    if path.is_symlink() or not path.is_file():
        raise W5ContractError("contract_unavailable", f"冻结合同不可用：{filename}")
    value = strict_json_loads(path.read_bytes())
    if not isinstance(value, dict):
        raise W5ContractError("contract_invalid", f"冻结合同不是对象：{filename}")
    return value


def validate_json_schema(instance: Any, schema: Mapping[str, Any], subject: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance),
            key=lambda item: (list(item.absolute_path), item.message),
        )
    except Exception as error:  # pragma: no cover - jsonschema 内部错误仍需 fail closed
        raise W5ContractError("schema_validator_failed", f"{subject} Schema 验证器失败：{error}") from error
    if errors:
        error = errors[0]
        location = "/".join(str(item) for item in error.absolute_path) or "$"
        raise W5ContractError("schema_validation_failed", f"{subject}@{location}：{error.message}")


def validate_prefixed_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or PREFIXED_SHA256.fullmatch(value) is None:
        raise W5ContractError("digest_invalid", f"{label} 不是 sha256:hex64")
    return value


def validate_hex_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise W5ContractError("digest_invalid", f"{label} 不是 lowercase hex64")
    return value


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise W5ContractError("identity_time_invalid", f"{label} 必须是 UTC 时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise W5ContractError("identity_time_invalid", f"{label} 不能解析") from error
    if parsed.tzinfo is None:
        raise W5ContractError("identity_time_invalid", f"{label} 缺少时区")
    return parsed


def validate_identity(identity: Mapping[str, Any], *, require_binding_digest: bool = False) -> dict[str, Any]:
    required = {
        "incident_id",
        "publication_id",
        "publication_revision",
        "publication_digest",
        "collector_id",
        "cohort_id",
        "cohort_digest",
        "window_start_utc",
        "window_end_utc",
        "data_through_utc",
        "finality",
        "registry_snapshot_id",
        "registry_snapshot_digest",
        "binding_generation",
    }
    allowed = required | {"binding_digest", "identity_digest"}
    if not isinstance(identity, Mapping) or not required.issubset(identity) or not set(identity).issubset(allowed):
        raise W5ContractError("identity_invalid", "身份字段集合不符合 W5 合同")
    if identity.get("collector_id") != "rrc25":
        raise W5ContractError("collector_boundary_violation", "仅允许 rrc25")
    if identity.get("finality") not in {"event_end_unknown", "event_end_known"}:
        raise W5ContractError("identity_invalid", "finality 无效")
    if isinstance(identity.get("publication_revision"), bool) or not isinstance(identity.get("publication_revision"), int) or identity["publication_revision"] < 1:
        raise W5ContractError("identity_invalid", "publication_revision 无效")
    if isinstance(identity.get("binding_generation"), bool) or not isinstance(identity.get("binding_generation"), int) or identity["binding_generation"] < 1:
        raise W5ContractError("identity_invalid", "binding_generation 无效")
    for field in ("incident_id", "publication_id", "cohort_id", "registry_snapshot_id"):
        if not isinstance(identity.get(field), str) or not identity[field]:
            raise W5ContractError("identity_invalid", f"{field} 必须非空")
    for field in ("publication_digest", "cohort_digest"):
        validate_hex_digest(identity.get(field), f"identity.{field}")
    registry_digest = identity.get("registry_snapshot_digest")
    if not isinstance(registry_digest, str) or not (
        HEX64.fullmatch(registry_digest) or PREFIXED_SHA256.fullmatch(registry_digest)
    ):
        raise W5ContractError("identity_invalid", "registry_snapshot_digest 无效")
    start = _parse_time(identity.get("window_start_utc"), "window_start_utc")
    end = _parse_time(identity.get("window_end_utc"), "window_end_utc")
    through = _parse_time(identity.get("data_through_utc"), "data_through_utc")
    if not start <= end <= through:
        raise W5ContractError("identity_time_order_invalid", "必须满足 start <= end <= data_through")
    result = copy.deepcopy(dict(identity))
    digest_field = "binding_digest" if require_binding_digest else (
        "binding_digest" if "binding_digest" in result else "identity_digest" if "identity_digest" in result else None
    )
    if require_binding_digest and digest_field != "binding_digest":
        raise W5ContractError("identity_digest_missing", "计划身份缺少 binding_digest")
    if digest_field is not None:
        expected = digest_without_fields(result, digest_field)
        if result.get(digest_field) != expected:
            raise W5ContractError("identity_digest_mismatch", f"{digest_field} 无法重算")
    return result


def identity_data_digest(identity: Mapping[str, Any]) -> str:
    subject = dict(identity)
    subject.pop("binding_digest", None)
    subject.pop("identity_digest", None)
    subject["registry_snapshot_digest"] = str(subject["registry_snapshot_digest"]).removeprefix("sha256:")
    return digest_hex(subject)


def validate_principal(principal: Mapping[str, Any], required_scope: str) -> dict[str, Any]:
    if not isinstance(principal, Mapping):
        raise W5ContractError("principal_invalid", "principal 必须是对象")
    if set(principal) == {"user_id", "authorization_scope"}:
        principal_id = principal.get("user_id")
        raw_scope = principal.get("authorization_scope")
        scopes = raw_scope.split() if isinstance(raw_scope, str) else None
    elif set(principal) == {"principal_id", "scopes"}:
        principal_id = principal.get("principal_id")
        scopes = principal.get("scopes")
    else:
        raise W5ContractError("principal_invalid", "principal 字段不闭合")
    if not isinstance(principal_id, str) or not principal_id:
        raise W5ContractError("principal_invalid", "principal_id 不能为空")
    if not isinstance(scopes, list) or any(not isinstance(item, str) or not item for item in scopes):
        raise W5ContractError("principal_invalid", "scopes 无效")
    if len(scopes) != len(set(scopes)) or required_scope not in scopes:
        raise W5ContractError("permission_denied", f"缺少权限：{required_scope}")
    return {"principal_id": principal_id, "scopes": list(scopes)}


def validate_static_dag(nodes: Sequence[Mapping[str, Any]], allowed_units: Iterable[str]) -> list[str]:
    if not isinstance(nodes, Sequence) or isinstance(nodes, (str, bytes)) or not nodes:
        raise W5ContractError("plan_dag_invalid", "静态 DAG 至少包含一个节点")
    allow = set(allowed_units)
    by_id: dict[str, Mapping[str, Any]] = {}
    order: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            raise W5ContractError("plan_node_invalid", "节点必须是对象")
        required = {"node_id", "unit_id", "depends_on", "dependency_mode", "requiredness", "parameters", "input_bindings"}
        if set(node) != required:
            raise W5ContractError("plan_node_invalid", "节点字段必须闭合且不得携带 fallback/internal_dag")
        node_id = node.get("node_id")
        unit_id = node.get("unit_id")
        if not isinstance(node_id, str) or not node_id or node_id in by_id:
            raise W5ContractError("plan_node_invalid", "node_id 缺失或重复")
        if unit_id in DEFERRED_UNIT_IDS:
            raise W5ContractError("p2_1_unit_forbidden", f"P2.1 单元不可执行：{unit_id}")
        if unit_id not in allow:
            raise W5ContractError("execution_unit_not_admitted", f"单元未获 W5 execution admission：{unit_id}")
        if node.get("dependency_mode") not in {"hard", "soft"} or node.get("requiredness") not in {"required", "optional", "boundary_only"}:
            raise W5ContractError("plan_node_invalid", f"{node_id} 依赖/必要性无效")
        dependencies = node.get("depends_on")
        if not isinstance(dependencies, list) or len(dependencies) != len(set(dependencies)) or any(not isinstance(item, str) or not item for item in dependencies):
            raise W5ContractError("plan_dag_invalid", f"{node_id}.depends_on 无效")
        if not isinstance(node.get("parameters"), Mapping) or not isinstance(node.get("input_bindings"), list):
            raise W5ContractError("plan_node_invalid", f"{node_id} 参数或输入绑定无效")
        by_id[node_id] = node
        order.append(node_id)
    for node_id, node in by_id.items():
        for dependency in node["depends_on"]:
            if dependency not in by_id:
                raise W5ContractError("plan_dag_invalid", f"{node_id} 依赖未知节点 {dependency}")
    visiting: set[str] = set()
    visited: set[str] = set()
    topology: list[str] = []

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise W5ContractError("plan_dag_cycle", f"DAG 存在环：{node_id}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in by_id[node_id]["depends_on"]:
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)
        topology.append(node_id)

    for node_id in order:
        visit(node_id)
    return topology


def ancestors_by_node(nodes: Sequence[Mapping[str, Any]]) -> dict[str, set[str]]:
    by_id = {str(node["node_id"]): node for node in nodes}
    cache: dict[str, set[str]] = {}

    def ancestors(node_id: str) -> set[str]:
        if node_id not in cache:
            result: set[str] = set()
            for dependency in by_id[node_id]["depends_on"]:
                result.add(dependency)
                result.update(ancestors(dependency))
            cache[node_id] = result
        return cache[node_id]

    return {node_id: ancestors(node_id) for node_id in by_id}


def assert_exact_keys(value: Mapping[str, Any], keys: Iterable[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise W5ContractError("closed_object_violation", f"{label} 字段集合不闭合")


__all__ = [
    "CONTROL_UNIT_IDS",
    "DEFERRED_UNIT_IDS",
    "DESIGN_CANDIDATE_DIGEST",
    "DESIGN_CANDIDATE_ID",
    "HEX64",
    "PREFIXED_SHA256",
    "W5ContractError",
    "ancestors_by_node",
    "assert_exact_keys",
    "canonical_json",
    "digest_hex",
    "digest_prefixed",
    "digest_without_fields",
    "identity_data_digest",
    "load_frozen_contract",
    "repository_root",
    "strict_json_loads",
    "validate_hex_digest",
    "validate_identity",
    "validate_json_schema",
    "validate_prefixed_digest",
    "validate_principal",
    "validate_static_dag",
]
