#!/usr/bin/env python3
"""国家中断 Agent P2-S0A 离线 Registry 生命周期治理入口。

该程序只操作调用者显式指定的本地 JSON 文件，不读取生产 runtime root，
不调用部署脚本，也不改变 P1/P1.1/P2 运行时。标准库实现是有意选择：候选
可以在无额外依赖的发布审查环境中重放。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


REGISTRY_SCHEMA = "country_outage_p2_s0a_registry_set_v1"
CAPABILITY_SCHEMA = "country_outage_p2_s0a_capability_registry_v1"
UNIT_SCHEMA = "country_outage_p2_s0a_execution_unit_registry_v1"
SNAPSHOT_SCHEMA = "country_outage_p2_s0a_registry_snapshot_v1"
PROPOSAL_SCHEMA = "country_outage_p2_s0a_migration_proposal_v1"
CANDIDATE_SCHEMA = "country_outage_p2_s0a_candidate_v1"
RECEIPT_SCHEMA = "country_outage_p2_s0a_governance_receipt_v1"
ACTIVATION_SCOPE = "offline_candidate_only"
RUNTIME_INTEGRATION = "not_implemented"
STATES = (
    "discovered",
    "proposed",
    "oracle_ready",
    "certified",
    "active",
    "deprecated",
    "retired",
    "tombstoned",
)
TRANSITIONS = {
    "discovered": {"proposed"},
    "proposed": {"oracle_ready"},
    "oracle_ready": {"certified"},
    "certified": {"active"},
    "active": {"deprecated"},
    "deprecated": {"active", "retired"},
    "retired": {"tombstoned"},
    "tombstoned": set(),
}
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
CAPABILITY_ID_RE = re.compile(r"^CAP-(?:[0-9]{3}|TREND-[0-9]{3})$")
UNIT_ID_RE = re.compile(r"^(?:TOOL|OP)-[0-9]{2}$")
CANDIDATE_ID_RE = re.compile(r"^p2-s0a-[a-f0-9]{16}$")


class GovernanceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GovernanceError("invalid_timestamp", f"时间不是规范 UTC：{value}") from exc
    if parsed.tzinfo is None:
        raise GovernanceError("invalid_timestamp", f"时间缺少时区：{value}")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_semver(value: str) -> Tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value or "")
    if not match:
        raise GovernanceError("invalid_semver", f"SemVer 无效：{value}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def next_patch(value: str) -> str:
    major, minor, patch = parse_semver(value)
    return f"{major}.{minor}.{patch + 1}"


def read_json(path: Path) -> Any:
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise GovernanceError("file_invalid", f"不是规范普通文件：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GovernanceError("json_invalid", f"JSON 无效：{path}：{exc}") from exc


def _ensure_safe_parent(path: Path) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    current = parent
    while True:
        if current.is_symlink():
            raise GovernanceError("unsafe_path", f"写入父目录包含符号链接：{current}")
        if current == current.parent:
            break
        current = current.parent


def write_json_atomic(path: Path, value: Any) -> None:
    path = path.resolve(strict=False)
    _ensure_safe_parent(path)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise GovernanceError("unsafe_path", f"目标不是规范普通文件：{path}")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary_path), str(path))
        directory_descriptor = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()


def require_mapping(value: Any, label: str) -> MutableMapping[str, Any]:
    if not isinstance(value, dict):
        raise GovernanceError("schema_invalid", f"{label} 必须是对象")
    return value


def require_list(value: Any, label: str) -> List[Any]:
    if not isinstance(value, list):
        raise GovernanceError("schema_invalid", f"{label} 必须是数组")
    return value


def require_keys(value: Mapping[str, Any], keys: Iterable[str], label: str) -> None:
    missing = [key for key in keys if key not in value]
    if missing:
        raise GovernanceError("schema_invalid", f"{label} 缺少字段：{', '.join(missing)}")


def lifecycle_event(
    sequence: int,
    action: str,
    actor: str,
    reason: str,
    from_state: Optional[str],
    to_state: str,
    at_utc: str,
) -> Dict[str, Any]:
    payload = {
        "sequence": sequence,
        "action": action,
        "actor": actor,
        "reason": reason,
        "from_state": from_state,
        "to_state": to_state,
        "at_utc": parse_utc(at_utc),
    }
    return {**payload, "receipt_digest": digest_value(payload)}


def migration_history(at_utc: str, final_state: str = "oracle_ready") -> List[Dict[str, Any]]:
    stop = STATES.index(final_state)
    history: List[Dict[str, Any]] = []
    previous: Optional[str] = None
    for index, state in enumerate(STATES[: stop + 1], 1):
        history.append(
            lifecycle_event(
                index,
                "legacy_migration",
                "registry-governance-builder-v1",
                "将当前已冻结 P1 合同迁移为离线治理候选，不改变运行时行为",
                previous,
                state,
                at_utc,
            )
        )
        previous = state
    return history


def _entry_key(entry: Mapping[str, Any], kind: str) -> Tuple[str, str]:
    stable_field = "capability_id" if kind == "capability" else "unit_id"
    return str(entry.get(stable_field, "")), str(entry.get("version", ""))


def _registry_entries(registry_set: Mapping[str, Any], kind: str) -> List[MutableMapping[str, Any]]:
    registry_key = "capability_registry" if kind == "capability" else "execution_unit_registry"
    registry = require_mapping(registry_set.get(registry_key), registry_key)
    entries = require_list(registry.get("entries"), f"{registry_key}.entries")
    for item in entries:
        require_mapping(item, f"{registry_key}.entries[]")
    return entries  # type: ignore[return-value]


def _find_entry(registry_set: Mapping[str, Any], kind: str, stable_id: str, version: str) -> MutableMapping[str, Any]:
    for entry in _registry_entries(registry_set, kind):
        if _entry_key(entry, kind) == (stable_id, version):
            return entry
    raise GovernanceError("entry_not_found", f"未找到 {kind} {stable_id}@{version}")


def _active_entries(registry_set: Mapping[str, Any], kind: str) -> List[MutableMapping[str, Any]]:
    return [entry for entry in _registry_entries(registry_set, kind) if entry.get("state") == "active"]


def _validate_digest(value: Any, label: str) -> None:
    if not isinstance(value, str) or not DIGEST_RE.fullmatch(value):
        raise GovernanceError("digest_invalid", f"{label} 不是 SHA-256 摘要")


def _validate_history(entry: Mapping[str, Any], label: str) -> None:
    history = require_list(entry.get("lifecycle_history"), f"{label}.lifecycle_history")
    if not history:
        raise GovernanceError("lifecycle_history_missing", f"{label} 缺少生命周期历史")
    previous: Optional[str] = None
    expected_sequence = 1
    for raw in history:
        event = require_mapping(raw, f"{label}.lifecycle_history[]")
        require_keys(event, ("sequence", "action", "actor", "reason", "from_state", "to_state", "at_utc", "receipt_digest"), label)
        if event["sequence"] != expected_sequence:
            raise GovernanceError("lifecycle_sequence_invalid", f"{label} 生命周期序号不连续")
        if event["from_state"] != previous:
            raise GovernanceError("lifecycle_transition_invalid", f"{label} from_state 不连续")
        state = event["to_state"]
        if state not in STATES:
            raise GovernanceError("lifecycle_state_invalid", f"{label} 状态无效：{state}")
        if previous is None and state != "discovered":
            raise GovernanceError("lifecycle_transition_invalid", f"{label} 必须从 discovered 开始")
        if previous is not None and state not in TRANSITIONS[previous]:
            raise GovernanceError("lifecycle_transition_invalid", f"{label} 非法迁移 {previous}->{state}")
        payload = {key: event[key] for key in ("sequence", "action", "actor", "reason", "from_state", "to_state", "at_utc")}
        if event["receipt_digest"] != digest_value(payload):
            raise GovernanceError("digest_mismatch", f"{label} 生命周期回执摘要漂移")
        parse_utc(event["at_utc"])
        previous = state
        expected_sequence += 1
    if previous != entry.get("state"):
        raise GovernanceError("lifecycle_state_mismatch", f"{label} 当前状态与历史末态不一致")


def _validate_entry_digests(entry: Mapping[str, Any], kind: str, label: str) -> None:
    for field in ("contract_digest", "implementation_digest", "semantic_digest"):
        _validate_digest(entry.get(field), f"{label}.{field}")
    if entry.get("state") == "tombstoned":
        if entry.get("tombstone") is None:
            raise GovernanceError("tombstone_missing", f"{label} Tombstone 元数据缺失")
        return
    contract_material = require_mapping(entry.get("contract_material"), f"{label}.contract_material")
    semantic_material = require_mapping(entry.get("semantic_material"), f"{label}.semantic_material")
    if entry["contract_digest"] != digest_value(contract_material):
        raise GovernanceError("digest_mismatch", f"{label} contract_digest 漂移")
    if entry["semantic_digest"] != digest_value(semantic_material):
        raise GovernanceError("digest_mismatch", f"{label} semantic_digest 漂移")
    if kind == "execution_unit":
        implementation_files = require_list(entry.get("implementation_files"), f"{label}.implementation_files")
        if not implementation_files:
            raise GovernanceError("implementation_identity_missing", f"{label} 实现文件为空")
        if entry["implementation_digest"] != digest_value(implementation_files):
            raise GovernanceError("digest_mismatch", f"{label} implementation_digest 漂移")
    else:
        references = require_list(entry.get("execution_units"), f"{label}.execution_units")
        if entry["implementation_digest"] != digest_value(references):
            raise GovernanceError("digest_mismatch", f"{label} implementation_digest 漂移")


def validate_registry_set(registry_set: Mapping[str, Any], require_active_snapshot: bool = False) -> Dict[str, Any]:
    require_keys(
        registry_set,
        (
            "schema_version",
            "candidate_id",
            "registry_revision",
            "activation_scope",
            "runtime_integration",
            "capability_registry",
            "execution_unit_registry",
            "lifecycle_log",
            "active_snapshot_id",
            "previous_snapshot_id",
            "snapshot_history",
        ),
        "registry_set",
    )
    if registry_set["schema_version"] != REGISTRY_SCHEMA:
        raise GovernanceError("schema_invalid", "registry_set schema_version 无效")
    if not CANDIDATE_ID_RE.fullmatch(str(registry_set["candidate_id"])):
        raise GovernanceError("candidate_identity_invalid", "candidate_id 无效")
    if not isinstance(registry_set["registry_revision"], int) or registry_set["registry_revision"] < 1:
        raise GovernanceError("registry_revision_invalid", "registry_revision 必须是正整数")
    if registry_set["activation_scope"] != ACTIVATION_SCOPE or registry_set["runtime_integration"] != RUNTIME_INTEGRATION:
        raise GovernanceError("boundary_violation", "离线激活或未接入运行时边界漂移")
    capability_registry = require_mapping(registry_set["capability_registry"], "capability_registry")
    unit_registry = require_mapping(registry_set["execution_unit_registry"], "execution_unit_registry")
    if capability_registry.get("schema_version") != CAPABILITY_SCHEMA or capability_registry.get("registry_name") != "Capability Registry":
        raise GovernanceError("schema_invalid", "Capability Registry 身份无效")
    if unit_registry.get("schema_version") != UNIT_SCHEMA or unit_registry.get("registry_name") != "Execution Unit Registry":
        raise GovernanceError("schema_invalid", "Execution Unit Registry 身份无效")
    capability_entries = _registry_entries(registry_set, "capability")
    unit_entries = _registry_entries(registry_set, "execution_unit")
    seen_capabilities: set[Tuple[str, str]] = set()
    seen_units: set[Tuple[str, str]] = set()
    unit_by_key: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for index, entry in enumerate(unit_entries):
        label = f"execution_unit_registry.entries[{index}]"
        require_keys(entry, ("unit_id", "version", "state", "kind", "capability_ids", "permission", "source", "contract_material", "semantic_material", "contract_digest", "implementation_digest", "semantic_digest", "implementation_files", "oracle_refs", "certification_evidence", "tombstone", "lifecycle_history"), label)
        unit_id, version = _entry_key(entry, "execution_unit")
        if not UNIT_ID_RE.fullmatch(unit_id):
            raise GovernanceError("stable_id_invalid", f"{label} unit_id 无效")
        parse_semver(version)
        if (unit_id, version) in seen_units:
            raise GovernanceError("stable_id_version_reused", f"重复执行单元 {unit_id}@{version}")
        seen_units.add((unit_id, version))
        unit_by_key[(unit_id, version)] = entry
        if entry["state"] not in STATES:
            raise GovernanceError("lifecycle_state_invalid", f"{label} 状态无效")
        if entry["kind"] not in ("read_tool", "deterministic_operator"):
            raise GovernanceError("kind_invalid", f"{label} kind 无效")
        if not require_list(entry["oracle_refs"], f"{label}.oracle_refs"):
            raise GovernanceError("oracle_missing", f"{label} 无 Oracle")
        _validate_entry_digests(entry, "execution_unit", label)
        _validate_history(entry, label)
        if STATES.index(entry["state"]) >= STATES.index("certified") and entry["certification_evidence"] is None:
            raise GovernanceError("certification_evidence_missing", f"{label} 缺少认证证据")
    for index, entry in enumerate(capability_entries):
        label = f"capability_registry.entries[{index}]"
        require_keys(entry, ("capability_id", "version", "state", "user_outcome", "execution_units", "permission", "identity_constraints", "boundaries", "source", "contract_material", "semantic_material", "contract_digest", "implementation_digest", "semantic_digest", "oracle_refs", "certification_evidence", "tombstone", "lifecycle_history"), label)
        capability_id, version = _entry_key(entry, "capability")
        if not CAPABILITY_ID_RE.fullmatch(capability_id):
            raise GovernanceError("stable_id_invalid", f"{label} capability_id 无效")
        parse_semver(version)
        if (capability_id, version) in seen_capabilities:
            raise GovernanceError("stable_id_version_reused", f"重复 Capability {capability_id}@{version}")
        seen_capabilities.add((capability_id, version))
        if entry["state"] not in STATES:
            raise GovernanceError("lifecycle_state_invalid", f"{label} 状态无效")
        identity = require_mapping(entry["identity_constraints"], f"{label}.identity_constraints")
        if identity.get("event_type") != "country_outage" or identity.get("collector_id") != "rrc25":
            raise GovernanceError("boundary_violation", f"{label} 越出 RRC25 country_outage")
        if not require_list(entry["boundaries"], f"{label}.boundaries"):
            raise GovernanceError("boundary_missing", f"{label} 边界为空")
        _validate_entry_digests(entry, "capability", label)
        _validate_history(entry, label)
        if STATES.index(entry["state"]) >= STATES.index("certified") and entry["certification_evidence"] is None:
            raise GovernanceError("certification_evidence_missing", f"{label} 缺少认证证据")
        for unit_ref in require_list(entry["execution_units"], f"{label}.execution_units"):
            reference = require_mapping(unit_ref, f"{label}.execution_units[]")
            unit_key = (str(reference.get("unit_id", "")), str(reference.get("version", "")))
            unit = unit_by_key.get(unit_key)
            if unit is None:
                raise GovernanceError("cross_reference_invalid", f"{label} 引用不存在的执行单元 {unit_key}")
            for field in ("contract_digest", "implementation_digest", "semantic_digest"):
                if reference.get(field) != unit.get(field):
                    raise GovernanceError("digest_mismatch", f"{label} 的 {unit_key} {field} 漂移")
            if capability_id not in unit.get("capability_ids", []):
                raise GovernanceError("cross_reference_invalid", f"{label} 与 {unit_key} 反向映射不一致")
            if entry["state"] == "active" and unit.get("state") != "active":
                raise GovernanceError("dependency_not_active", f"{label} 依赖非 active 执行单元")
    for unit in unit_entries:
        for capability_id in unit.get("capability_ids", []):
            matching = [entry for entry in capability_entries if entry.get("capability_id") == capability_id and entry.get("state") != "tombstoned"]
            if not matching:
                raise GovernanceError("cross_reference_invalid", f"{unit['unit_id']} 反向 Capability {capability_id} 不存在")
    active_snapshot = registry_set.get("active_snapshot_id")
    if active_snapshot is not None and (not isinstance(active_snapshot, str) or not active_snapshot.startswith("registry-snapshot-sha256:") or len(active_snapshot.split(":", 1)[1]) != 64):
        raise GovernanceError("registry_snapshot_invalid", "active_snapshot_id 无效")
    if require_active_snapshot and _active_entries(registry_set, "capability") and not active_snapshot:
        raise GovernanceError("registry_snapshot_missing", "active 条目缺少活动快照")
    snapshot_history = require_list(registry_set["snapshot_history"], "snapshot_history")
    if len(snapshot_history) != len(set(snapshot_history)):
        raise GovernanceError("snapshot_history_invalid", "snapshot_history 重复")
    return {
        "status": "valid",
        "candidate_id": registry_set["candidate_id"],
        "registry_revision": registry_set["registry_revision"],
        "capability_count": len(capability_entries),
        "execution_unit_count": len(unit_entries),
        "active_capability_count": len(_active_entries(registry_set, "capability")),
        "active_execution_unit_count": len(_active_entries(registry_set, "execution_unit")),
        "active_snapshot_id": active_snapshot,
        "runtime_integration": RUNTIME_INTEGRATION,
    }


def _implementation_manifest(repo_root: Path, relative_paths: Sequence[str]) -> List[Dict[str, str]]:
    manifest: List[Dict[str, str]] = []
    for relative in relative_paths:
        path = repo_root / relative
        if not path.is_file() or path.is_symlink():
            raise GovernanceError("implementation_identity_missing", f"实现文件不存在：{relative}")
        manifest.append({"path": relative, "sha256": digest_file(path)})
    return manifest


BASE_IMPLEMENTATION_FILES = {
    "TOOL-01": ["agent-sidecar/src/chat/runtime-v2-single-turn.ts", "agent-sidecar/src/chat/runtime-v2-semantic.ts"],
    "TOOL-02": ["agent-sidecar/src/chat/page-capability-executor.ts", "agent-sidecar/src/chat/runtime-v2-semantic.ts"],
    "TOOL-03": ["agent-sidecar/src/chat/page-capability-executor.ts", "agent-sidecar/src/chat/runtime-v2-semantic.ts"],
    "TOOL-04": ["agent-sidecar/src/chat/page-capability-executor.ts", "agent-sidecar/src/chat/runtime-v2-semantic.ts"],
    "TOOL-05": ["agent-sidecar/src/chat/page-capability-executor.ts", "agent-sidecar/src/chat/runtime-v2-semantic.ts"],
    "TOOL-06": ["agent-sidecar/src/chat/page-capability-executor.ts", "agent-sidecar/src/chat/runtime-v2-semantic.ts"],
    "OP-01": ["agent-sidecar/src/chat/page-capability-series.ts", "agent-sidecar/src/chat/runtime-v2-semantic.ts"],
    "OP-02": ["agent-sidecar/src/chat/page-capability-series.ts", "agent-sidecar/src/chat/runtime-v2-semantic.ts"],
    "OP-03": ["agent-sidecar/src/chat/page-capability-executor.ts", "agent-sidecar/src/chat/runtime-v2-semantic.ts"],
    "OP-04": [
        "agent-sidecar/src/chat/event-window-trend.ts",
        "agent-sidecar/src/chat/trend-aware-grounder.ts",
        "agent-sidecar/src/chat/page-capability-executor.ts",
        "contracts/agent/country-outage-p1-trend-operator/v1/operator-contract.json",
        "contracts/agent/country-outage-p1-trend-operator/v1/trend-profiles.json",
        "contracts/agent/country-outage-p1-trend-operator/v1/p1-integration-contract.json",
    ],
}


def _unit_semantic_material(unit: Mapping[str, Any]) -> Dict[str, Any]:
    keys = (
        "unit_id",
        "kind",
        "name",
        "purpose",
        "capability_ids",
        "permission",
        "source_operation",
        "time_semantics",
        "units",
        "pagination",
        "null_semantics",
        "errors",
        "timeout_ms",
        "evidence_refs",
        "forbidden_uses",
    )
    return {key: copy.deepcopy(unit.get(key)) for key in keys}


def _source_identity(repo_root: Path) -> Dict[str, Any]:
    paths = [
        "contracts/agent/country-outage-p1-page-coverage/s2/capability-catalog.json",
        "contracts/agent/country-outage-p1-page-coverage/s2/tool-contracts.json",
        "contracts/agent/country-outage-p1-page-coverage/s2/oracle.json",
        "contracts/agent/country-outage-p1-trend-operator/v1/operator-contract.json",
        "contracts/agent/country-outage-p1-trend-operator/v1/p1-integration-contract.json",
        "contracts/agent/country-outage-p1-trend-operator/v1/synthetic-oracle.json",
        "contracts/agent/country-outage-p1-trend-operator/v1/trend-profiles.json",
    ]
    files = [{"path": path, "sha256": digest_file(repo_root / path)} for path in paths]
    return {"files": files, "source_digest": digest_value(files)}


def build_migration_proposal(repo_root: Path, created_at: str) -> Dict[str, Any]:
    created_at = parse_utc(created_at)
    capability_path = repo_root / "contracts/agent/country-outage-p1-page-coverage/s2/capability-catalog.json"
    tool_path = repo_root / "contracts/agent/country-outage-p1-page-coverage/s2/tool-contracts.json"
    oracle_path = repo_root / "contracts/agent/country-outage-p1-page-coverage/s2/oracle.json"
    op_path = repo_root / "contracts/agent/country-outage-p1-trend-operator/v1/operator-contract.json"
    integration_path = repo_root / "contracts/agent/country-outage-p1-trend-operator/v1/p1-integration-contract.json"
    synthetic_oracle_path = repo_root / "contracts/agent/country-outage-p1-trend-operator/v1/synthetic-oracle.json"
    profiles_path = repo_root / "contracts/agent/country-outage-p1-trend-operator/v1/trend-profiles.json"
    catalog = require_mapping(read_json(capability_path), "P1 Capability Catalog")
    contracts = require_mapping(read_json(tool_path), "P1 Typed Tool Contract")
    oracle = require_mapping(read_json(oracle_path), "P1 Oracle")
    op_contract = require_mapping(read_json(op_path), "OP-04 Contract")
    integration = require_mapping(read_json(integration_path), "OP-04 Integration")
    synthetic_oracle = require_mapping(read_json(synthetic_oracle_path), "OP-04 Oracle")
    profiles = require_mapping(read_json(profiles_path), "OP-04 Profiles")
    source_identity = _source_identity(repo_root)
    candidate_id = "p2-s0a-" + source_identity["source_digest"].split(":", 1)[1][:16]
    unit_entries: List[Dict[str, Any]] = []
    base_units = require_list(contracts.get("execution_units"), "execution_units")
    for index, raw in enumerate(base_units):
        unit = copy.deepcopy(require_mapping(raw, f"execution_units[{index}]"))
        unit_id = str(unit.get("unit_id", ""))
        implementation_files = _implementation_manifest(repo_root, BASE_IMPLEMENTATION_FILES[unit_id])
        semantic_material = _unit_semantic_material(unit)
        unit_entries.append(
            {
                "unit_id": unit_id,
                "version": "1.0.0",
                "state": "oracle_ready",
                "kind": unit["kind"],
                "name": unit["name"],
                "purpose": unit["purpose"],
                "capability_ids": copy.deepcopy(unit["capability_ids"]),
                "permission": unit["permission"],
                "source_operation": unit["source_operation"],
                "timeout_ms": unit["timeout_ms"],
                "units": copy.deepcopy(unit.get("units") or ["not_applicable"]),
                "null_semantics": unit["null_semantics"],
                "errors": copy.deepcopy(unit["errors"]),
                "forbidden_uses": copy.deepcopy(unit["forbidden_uses"]),
                "dependencies": [],
                "implementation_files": implementation_files,
                "owner": "country-outage-agent-runtime",
                "source": {
                    "path": str(tool_path.relative_to(repo_root)),
                    "json_pointer": f"/execution_units/{index}",
                    "legacy_revision": str(contracts["contract_revision"]),
                },
                "contract_material": unit,
                "semantic_material": semantic_material,
                "contract_digest": digest_value(unit),
                "implementation_digest": digest_value(implementation_files),
                "semantic_digest": digest_value(semantic_material),
                "oracle_refs": [
                    f"{oracle_path.relative_to(repo_root)}#/capability_coverage/{capability_id}"
                    for capability_id in unit["capability_ids"]
                ],
                "cost_profile": {
                    "model_dependency": "none",
                    "external_model_cost": "zero_external_model_cost",
                    "internal_api_cost": "unknown_not_measured" if unit["kind"] == "read_tool" else "not_applicable",
                },
                "performance_profile": {
                    "legacy_timeout_ms": unit["timeout_ms"],
                    "runtime_percentiles": "unknown_not_measured_in_p2_s0a",
                },
                "certification_evidence": None,
                "replacement": None,
                "migration_deadline": None,
                "tombstone": None,
                "lifecycle_history": migration_history(created_at),
            }
        )
    op04_material = {
        "operator_contract": copy.deepcopy(op_contract),
        "integration_contract": copy.deepcopy(integration),
        "profile_registry": copy.deepcopy(profiles),
    }
    op04_semantics = {
        "unit_id": "OP-04",
        "operator_id": op_contract["operator_id"],
        "operator_version": op_contract["operator_version"],
        "scope": copy.deepcopy(op_contract["scope"]),
        "grounding": copy.deepcopy(integration["grounding"]),
        "publication": copy.deepcopy(integration["publication"]),
        "forbidden_claims": copy.deepcopy(integration["forbidden_claims"]),
        "failure_policy": copy.deepcopy(integration["failure_policy"]),
    }
    op04_files = _implementation_manifest(repo_root, BASE_IMPLEMENTATION_FILES["OP-04"])
    unit_entries.append(
        {
            "unit_id": "OP-04",
            "version": "1.2.0",
            "state": "oracle_ready",
            "kind": "deterministic_operator",
            "name": "event-window-trend",
            "purpose": "把同一 publication 的已登记 RRC25 时序转换为确定性事件窗口趋势事实",
            "capability_ids": ["CAP-TREND-001"],
            "permission": "inherits_source_read_permission",
            "source_operation": "host deterministic operator after TOOL-03",
            "timeout_ms": 1000,
            "units": ["per_registered_metric_no_cross_unit_aggregation"],
            "null_semantics": "null 形成硬断点；全 null 失败关闭；禁止插值、前填充或按 0 处理",
            "errors": copy.deepcopy(op_contract["error_codes"]),
            "forbidden_uses": copy.deepcopy(integration["forbidden_claims"]) + ["formal_historical_trend"],
            "dependencies": [{"unit_id": "TOOL-03", "version": "1.0.0", "relationship": "validated_series_source"}],
            "implementation_files": op04_files,
            "owner": "country-outage-agent-runtime",
            "source": {
                "path": str(op_path.relative_to(repo_root)),
                "json_pointer": "",
                "legacy_revision": f"{op_contract['operator_id']}@{op_contract['operator_version']}",
            },
            "contract_material": op04_material,
            "semantic_material": op04_semantics,
            "contract_digest": digest_value(op04_material),
            "implementation_digest": digest_value(op04_files),
            "semantic_digest": digest_value(op04_semantics),
            "oracle_refs": [
                f"{synthetic_oracle_path.relative_to(repo_root)}#",
                f"{integration_path.relative_to(repo_root)}#",
            ],
            "cost_profile": {"model_dependency": "none", "external_cost": "zero_external_cost"},
            "performance_profile": {"runtime_percentiles": "unknown_not_measured_in_p2_s0a", "governance_timeout_ms": 1000},
            "certification_evidence": None,
            "replacement": None,
            "migration_deadline": None,
            "tombstone": None,
            "lifecycle_history": migration_history(created_at),
        }
    )
    unit_by_id = {entry["unit_id"]: entry for entry in unit_entries}
    capability_entries: List[Dict[str, Any]] = []
    selected = require_list(catalog.get("selected"), "catalog.selected")
    for index, raw in enumerate(selected):
        capability = copy.deepcopy(require_mapping(raw, f"catalog.selected[{index}]"))
        capability_id = str(capability["capability_id"])
        unit = unit_by_id[str(capability["execution_unit"])]
        unit_reference = {
            "unit_id": unit["unit_id"],
            "version": unit["version"],
            "contract_digest": unit["contract_digest"],
            "implementation_digest": unit["implementation_digest"],
            "semantic_digest": unit["semantic_digest"],
        }
        semantic_material = {
            "capability_id": capability_id,
            "user_outcome": capability["user_outcome"],
            "goal_kinds": copy.deepcopy(capability["goal_kinds"]),
            "answer_modes": copy.deepcopy(capability["answer_modes"]),
            "required_for_supported": copy.deepcopy(capability["required_for_supported"]),
            "sufficient_for_partial": copy.deepcopy(capability["sufficient_for_partial"]),
            "evidence_sources": copy.deepcopy(capability["evidence_sources"]),
            "event_type": "country_outage",
            "collector_id": "rrc25",
            "forbidden_claims": ["cause", "responsibility", "recovery", "real_user_impact", "national_outage", "network_rca"],
        }
        capability_entries.append(
            {
                "capability_id": capability_id,
                "version": "1.0.0",
                "state": "oracle_ready",
                "display_name_zh": capability["user_outcome"],
                "user_outcome": capability["user_outcome"],
                "goal_kinds": copy.deepcopy(capability["goal_kinds"]),
                "answer_modes": copy.deepcopy(capability["answer_modes"]),
                "execution_units": [unit_reference],
                "permission": unit["permission"],
                "identity_constraints": {"event_type": "country_outage", "collector_id": "rrc25", "binding": "per_event_publication_revision"},
                "evidence_sources": copy.deepcopy(capability["evidence_sources"]),
                "boundaries": ["rrc25_control_plane_only", "no_cause", "no_recovery", "no_real_user_impact", "no_network_rca"],
                "owner": "country-outage-agent-product",
                "source": {
                    "path": str(capability_path.relative_to(repo_root)),
                    "json_pointer": f"/selected/{index}",
                    "legacy_revision": str(catalog["catalog_revision"]),
                },
                "contract_material": capability,
                "semantic_material": semantic_material,
                "contract_digest": digest_value(capability),
                "implementation_digest": digest_value([unit_reference]),
                "semantic_digest": digest_value(semantic_material),
                "oracle_refs": [f"{oracle_path.relative_to(repo_root)}#/capability_coverage/{capability_id}"],
                "certification_evidence": None,
                "replacement": None,
                "migration_deadline": None,
                "tombstone": None,
                "lifecycle_history": migration_history(created_at),
            }
        )
    op04 = unit_by_id["OP-04"]
    op04_reference = {field: op04[field] for field in ("unit_id", "version", "contract_digest", "implementation_digest", "semantic_digest")}
    trend_capability_material = {
        "capability_id": "CAP-TREND-001",
        "execution_unit": "OP-04",
        "source_execution_unit": "TOOL-03",
        "integration": copy.deepcopy(integration),
    }
    trend_semantics = {
        "capability_id": "CAP-TREND-001",
        "user_outcome": "回答同一 publication 事件窗口内已登记 RRC25 时序怎样变化",
        "goal_kinds": ["event_window_trend"],
        "answer_modes": ["supported", "partial", "invalid_data"],
        "event_type": "country_outage",
        "collector_id": "rrc25",
        "time_scope": "current_publication_window",
        "cross_unit_aggregation": "forbidden",
        "forbidden_claims": copy.deepcopy(integration["forbidden_claims"]),
    }
    capability_entries.append(
        {
            "capability_id": "CAP-TREND-001",
            "version": "1.0.0",
            "state": "oracle_ready",
            "display_name_zh": "事件窗口内时序趋势概括",
            "user_outcome": trend_semantics["user_outcome"],
            "goal_kinds": ["event_window_trend"],
            "answer_modes": ["supported", "partial", "invalid_data"],
            "execution_units": [op04_reference],
            "permission": "inherits_source_read_permission",
            "identity_constraints": {"event_type": "country_outage", "collector_id": "rrc25", "binding": "same_publication_as_tool_03"},
            "evidence_sources": ["series", "derived"],
            "boundaries": ["current_publication_window_only", "no_cross_unit_aggregation", "no_cause", "no_recovery", "no_real_user_impact", "no_network_rca"],
            "owner": "country-outage-agent-product",
            "source": {"path": str(integration_path.relative_to(repo_root)), "json_pointer": "", "legacy_revision": str(integration["schema_version"])},
            "contract_material": trend_capability_material,
            "semantic_material": trend_semantics,
            "contract_digest": digest_value(trend_capability_material),
            "implementation_digest": digest_value([op04_reference]),
            "semantic_digest": digest_value(trend_semantics),
            "oracle_refs": [f"{synthetic_oracle_path.relative_to(repo_root)}#", f"{integration_path.relative_to(repo_root)}#"],
            "certification_evidence": None,
            "replacement": None,
            "migration_deadline": None,
            "tombstone": None,
            "lifecycle_history": migration_history(created_at),
        }
    )
    registry_set = {
        "schema_version": REGISTRY_SCHEMA,
        "candidate_id": candidate_id,
        "registry_revision": 1,
        "activation_scope": ACTIVATION_SCOPE,
        "runtime_integration": RUNTIME_INTEGRATION,
        "capability_registry": {"schema_version": CAPABILITY_SCHEMA, "registry_name": "Capability Registry", "entries": capability_entries},
        "execution_unit_registry": {"schema_version": UNIT_SCHEMA, "registry_name": "Execution Unit Registry", "entries": unit_entries},
        "lifecycle_log": [lifecycle_event(1, "bootstrap_migration_proposal", "registry-governance-builder-v1", "创建离线迁移 proposal，尚未认证或激活", None, "oracle_ready", created_at)],
        "active_snapshot_id": None,
        "previous_snapshot_id": None,
        "snapshot_history": [],
    }
    validation = validate_registry_set(registry_set)
    if validation["capability_count"] != 18 or validation["execution_unit_count"] != 10:
        raise GovernanceError("migration_incomplete", "迁移数量不是 18 Capability / 10 Execution Unit")
    return {
        "schema_version": PROPOSAL_SCHEMA,
        "candidate_id": candidate_id,
        "created_at": created_at,
        "source_identity": source_identity,
        "migration_summary": {
            "capability_count": 18,
            "execution_unit_count": 10,
            "tool_count": 6,
            "base_operator_count": 3,
            "independent_operator_count": 1,
            "op04_version": "1.2.0",
            "runtime_behavior_changed": False,
            "production_deployed": False,
        },
        "registry_set": registry_set,
        "proposal_digest": digest_value(registry_set),
    }


def _certification_evidence(repo_root: Path, review_path: Path) -> Dict[str, str]:
    oracle_path = repo_root / "contracts/agent/country-outage-p2-s0a-lifecycle/governance-oracle.json"
    manager_path = repo_root / "dev/tools/manage_country_outage_p2_registry.py"
    security = {
        "manager_digest": digest_file(manager_path),
        "atomic_write": "same_directory_fsync_replace",
        "symlink_write": "forbidden",
        "production_runtime_root": "never_defaulted_or_accessed",
        "permissions": "role_checked",
    }
    cost = {
        "offline_governance_external_requests": 0,
        "offline_governance_model_calls": 0,
        "runtime_tool_cost": "unknown_not_measured_in_p2_s0a",
        "runtime_operator_external_cost": "zero_for_deterministic_operators",
    }
    performance = {
        "scope": "offline_registry_validation_only",
        "budget_ms": 1000,
        "runtime_tool_percentiles": "unknown_not_measured_in_p2_s0a",
        "runtime_operator_percentiles": "unknown_not_measured_in_p2_s0a",
    }
    rollback = {
        "single_unit": "required_by_governance_oracle",
        "whole_edition": "required_by_governance_oracle",
        "production_rollback_invoked": False,
    }
    return {
        "oracle_digest": digest_file(oracle_path),
        "product_semantic_review_digest": digest_file(review_path),
        "security_digest": digest_value(security),
        "cost_audit_digest": digest_value(cost),
        "performance_audit_digest": digest_value(performance),
        "rollback_evidence_digest": digest_value(rollback),
    }


def _append_transition(entry: MutableMapping[str, Any], to_state: str, actor: str, reason: str, at_utc: str) -> None:
    current = str(entry["state"])
    if to_state not in TRANSITIONS[current]:
        raise GovernanceError("lifecycle_transition_invalid", f"非法迁移 {current}->{to_state}")
    history = require_list(entry["lifecycle_history"], "lifecycle_history")
    history.append(lifecycle_event(len(history) + 1, "transition", actor, reason, current, to_state, at_utc))
    entry["state"] = to_state


def snapshot_payload(registry_set: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "candidate_id": registry_set["candidate_id"],
        "registry_revision": registry_set["registry_revision"],
        "activation_scope": registry_set["activation_scope"],
        "runtime_integration": registry_set["runtime_integration"],
        "capability_registry": copy.deepcopy(registry_set["capability_registry"]),
        "execution_unit_registry": copy.deepcopy(registry_set["execution_unit_registry"]),
    }


def build_snapshot(registry_set: Mapping[str, Any], created_at: str) -> Dict[str, Any]:
    payload = snapshot_payload(registry_set)
    snapshot_id = "registry-snapshot-sha256:" + digest_value(payload).split(":", 1)[1]
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "registry_snapshot_id": snapshot_id,
        "created_at": parse_utc(created_at),
        "snapshot_payload": payload,
        "snapshot_digest": digest_value(payload),
        "production_deployed": False,
    }


def validate_snapshot(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    require_keys(snapshot, ("schema_version", "registry_snapshot_id", "created_at", "snapshot_payload", "snapshot_digest", "production_deployed"), "snapshot")
    if snapshot["schema_version"] != SNAPSHOT_SCHEMA or snapshot["production_deployed"] is not False:
        raise GovernanceError("registry_snapshot_invalid", "快照 Schema 或非部署边界无效")
    payload = require_mapping(snapshot["snapshot_payload"], "snapshot_payload")
    expected_digest = digest_value(payload)
    expected_id = "registry-snapshot-sha256:" + expected_digest.split(":", 1)[1]
    if snapshot["snapshot_digest"] != expected_digest or snapshot["registry_snapshot_id"] != expected_id:
        raise GovernanceError("digest_mismatch", "快照摘要漂移")
    parse_utc(str(snapshot["created_at"]))
    return {"status": "valid", "registry_snapshot_id": expected_id, "registry_revision": payload.get("registry_revision")}


def finalize_migration(repo_root: Path, proposal: Mapping[str, Any], review: Mapping[str, Any], created_at: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    created_at = parse_utc(created_at)
    if proposal.get("schema_version") != PROPOSAL_SCHEMA:
        raise GovernanceError("proposal_invalid", "迁移 proposal Schema 无效")
    if review.get("schema_version") != "country_outage_p2_s0a_product_semantic_review_v1" or review.get("status") != "PASS" or review.get("blocking_count") != 0:
        raise GovernanceError("product_semantic_review_failed", "独立产品语义 Reviewer 未通过")
    if review.get("candidate_id") != proposal.get("candidate_id"):
        raise GovernanceError("candidate_identity_conflict", "Reviewer 与 proposal candidate 不一致")
    registry_set = copy.deepcopy(require_mapping(proposal.get("registry_set"), "proposal.registry_set"))
    review_path_value = review.get("receipt_path")
    if not isinstance(review_path_value, str) or not review_path_value:
        raise GovernanceError("product_semantic_review_invalid", "Reviewer 缺少 receipt_path")
    review_path = (repo_root / review_path_value).resolve()
    evidence = _certification_evidence(repo_root, review_path)
    for kind in ("execution_unit", "capability"):
        for entry in _registry_entries(registry_set, kind):
            entry["certification_evidence"] = copy.deepcopy(evidence)
            _append_transition(entry, "certified", "product-semantic-reviewer-v1", "Oracle、产品语义、安全、费用、性能与回滚合同闭合", created_at)
            _append_transition(entry, "active", "registry-activation-approver-v1", "激活到 P2-S0A 离线候选快照，不代表生产部署", created_at)
    registry_set["lifecycle_log"].append(
        lifecycle_event(2, "certify_and_activate_migration", "registry-activation-approver-v1", "认证并离线激活完整迁移集合", "oracle_ready", "active", created_at)
    )
    validation_started = time.perf_counter()
    validate_registry_set(registry_set)
    validation_elapsed_ms = round((time.perf_counter() - validation_started) * 1000, 3)
    snapshot = build_snapshot(registry_set, created_at)
    registry_set["active_snapshot_id"] = snapshot["registry_snapshot_id"]
    registry_set["snapshot_history"] = [snapshot["registry_snapshot_id"]]
    validate_registry_set(registry_set, require_active_snapshot=True)
    candidate = {
        "schema_version": CANDIDATE_SCHEMA,
        "candidate_id": registry_set["candidate_id"],
        "created_at": created_at,
        "activation_scope": ACTIVATION_SCOPE,
        "runtime_integration": RUNTIME_INTEGRATION,
        "production_deployed": False,
        "registry_revision": registry_set["registry_revision"],
        "registry_snapshot_id": snapshot["registry_snapshot_id"],
        "migration_summary": copy.deepcopy(proposal["migration_summary"]),
        "source_identity": copy.deepcopy(proposal["source_identity"]),
        "certification_evidence": evidence,
        "governance_validation": {"status": "passed", "elapsed_ms": validation_elapsed_ms, "measurement_scope": "offline_registry_only"},
        "boundaries": {
            "collector_id": "rrc25",
            "event_type": "country_outage",
            "access": "read_only",
            "runtime_registry": "not_implemented",
            "production_deployment": "not_performed",
            "network_rca": False,
        },
    }
    return registry_set, snapshot, candidate


def migration_map(registry_set: Mapping[str, Any]) -> Dict[str, Any]:
    capabilities = [
        {
            "legacy_id": entry["capability_id"],
            "legacy_revision": entry["source"]["legacy_revision"],
            "registry_id": entry["capability_id"],
            "registry_version": entry["version"],
            "contract_digest": entry["contract_digest"],
            "semantic_digest": entry["semantic_digest"],
            "semantic_change": "none",
        }
        for entry in _registry_entries(registry_set, "capability")
    ]
    units = [
        {
            "legacy_id": entry["unit_id"],
            "legacy_revision": entry["source"]["legacy_revision"],
            "registry_id": entry["unit_id"],
            "registry_version": entry["version"],
            "contract_digest": entry["contract_digest"],
            "implementation_digest": entry["implementation_digest"],
            "semantic_digest": entry["semantic_digest"],
            "semantic_change": "none",
        }
        for entry in _registry_entries(registry_set, "execution_unit")
    ]
    return {
        "schema_version": "country_outage_p2_s0a_migration_map_v1",
        "candidate_id": registry_set["candidate_id"],
        "stable_id_reuse": "forbidden",
        "runtime_behavior_changed": False,
        "production_deployed": False,
        "capabilities": capabilities,
        "execution_units": units,
    }


def exported_registry(registry_set: Mapping[str, Any], kind: str) -> Dict[str, Any]:
    key = "capability_registry" if kind == "capability" else "execution_unit_registry"
    return {
        **copy.deepcopy(registry_set[key]),
        "candidate_id": registry_set["candidate_id"],
        "registry_revision": registry_set["registry_revision"],
        "activation_scope": registry_set["activation_scope"],
        "runtime_integration": registry_set["runtime_integration"],
        "active_snapshot_id": registry_set["active_snapshot_id"],
    }


def classify_change(old: Mapping[str, Any], new: Mapping[str, Any], kind: str) -> Dict[str, Any]:
    old_version = parse_semver(str(old["version"]))
    new_version = parse_semver(str(new["version"]))
    if new_version <= old_version:
        raise GovernanceError("version_not_monotonic", "新版本必须大于旧版本")
    if new_version[0] > old_version[0]:
        declared = "major"
    elif new_version[1] > old_version[1]:
        declared = "minor"
    else:
        declared = "patch"
    semantic_changed = new.get("semantic_digest") != old.get("semantic_digest")
    contract_changed = new.get("contract_digest") != old.get("contract_digest")
    implementation_changed = new.get("implementation_digest") != old.get("implementation_digest")
    breaking_reasons: List[str] = []
    if kind == "capability" and new.get("user_outcome") != old.get("user_outcome"):
        breaking_reasons.append("user_outcome_changed_requires_new_capability_id")
    if semantic_changed:
        breaking_reasons.append("semantic_digest_changed")
    if old.get("permission") != new.get("permission"):
        breaking_reasons.append("permission_changed")
    if declared != "major" and breaking_reasons:
        effective = "major"
    else:
        effective = declared
    return {
        "declared_semver_class": declared,
        "effective_compatibility": effective,
        "contract_changed": contract_changed,
        "implementation_changed": implementation_changed,
        "semantic_changed": semantic_changed,
        "breaking_reasons": breaking_reasons,
        "requires_new_stable_id": "user_outcome_changed_requires_new_capability_id" in breaking_reasons,
    }


def impact_analysis(registry_set: Mapping[str, Any], kind: str, stable_id: str, version: str, proposed: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    target = _find_entry(registry_set, kind, stable_id, version)
    impacted_capabilities: set[str] = set()
    impacted_units: set[str] = set()
    if kind == "execution_unit":
        impacted_units.add(stable_id)
        impacted_capabilities.update(str(item) for item in target.get("capability_ids", []))
        for unit in _registry_entries(registry_set, "execution_unit"):
            for dependency in unit.get("dependencies", []):
                if dependency.get("unit_id") == stable_id:
                    impacted_units.add(str(unit["unit_id"]))
                    impacted_capabilities.update(str(item) for item in unit.get("capability_ids", []))
    else:
        impacted_capabilities.add(stable_id)
        impacted_units.update(str(item["unit_id"]) for item in target.get("execution_units", []))
    result: Dict[str, Any] = {
        "target": {"kind": kind, "stable_id": stable_id, "version": version},
        "direct_and_transitive_capabilities": sorted(impacted_capabilities),
        "direct_and_transitive_execution_units": sorted(impacted_units),
        "affected_plan_kinds": ["GroundingPlan", "InvestigationPlan"],
        "affected_evidence": "historical evidence keeps original snapshot; new evidence requires new identity",
        "permissions": sorted({str(entry.get("permission")) for entry in _registry_entries(registry_set, kind) if _entry_key(entry, kind)[0] == stable_id}),
        "cost_and_performance": "must_reaudit_affected_units_only",
        "oracle_selection": ["normal", "missing", "null", "wrong_identity", "unavailable", "boundary", "migration", "tamper", "rollback", "plan_admission"],
    }
    if proposed is not None:
        result["compatibility"] = classify_change(target, proposed, kind)
    return result


def _authorize(role: str, required: str) -> None:
    if role != required:
        raise GovernanceError("permission_denied", f"动作需要 {required}，实际为 {role}")


def _new_receipt(
    registry_before: Mapping[str, Any],
    registry_after: Mapping[str, Any],
    action: str,
    actor: str,
    request_id: str,
    at_utc: str,
    impact: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    payload = {
        "request_id": request_id,
        "candidate_id": registry_before["candidate_id"],
        "action": action,
        "status": "applied" if registry_before != registry_after else "read_only",
        "actor_id": actor,
        "before_revision": registry_before["registry_revision"],
        "after_revision": registry_after["registry_revision"],
        "before_digest": digest_value(registry_before),
        "after_digest": digest_value(registry_after),
        "evidence_refs": [],
        "impact": copy.deepcopy(impact),
        "error_code": None,
        "at_utc": parse_utc(at_utc),
    }
    receipt_id = "govrcpt-sha256:" + digest_value(payload).split(":", 1)[1]
    return {"schema_version": RECEIPT_SCHEMA, "receipt_id": receipt_id, **payload}


def _save_mutation(path: Path, before: Mapping[str, Any], after: MutableMapping[str, Any], action: str, actor: str, request_id: str, at_utc: str, receipt_path: Optional[Path], impact: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    validate_registry_set(after)
    write_json_atomic(path, after)
    receipt = _new_receipt(before, after, action, actor, request_id, at_utc, impact)
    if receipt_path is not None:
        write_json_atomic(receipt_path, receipt)
    return receipt


def operate_create(path: Path, entry_path: Path, kind: str, expected_revision: int, actor: str, role: str, reason: str, request_id: str, at_utc: str, receipt_path: Optional[Path]) -> Dict[str, Any]:
    _authorize(role, "registry:propose")
    before = require_mapping(read_json(path), "registry_set")
    validate_registry_set(before)
    if before["registry_revision"] != expected_revision:
        raise GovernanceError("registry_revision_conflict", "expected_registry_revision 不匹配")
    entry = require_mapping(read_json(entry_path), "entry")
    stable_id, version = _entry_key(entry, kind)
    parse_semver(version)
    if any(_entry_key(item, kind)[0] == stable_id for item in _registry_entries(before, kind)):
        raise GovernanceError("stable_id_reused", f"Create 不得复用稳定 ID：{stable_id}")
    if entry.get("state") not in ("discovered", "proposed"):
        raise GovernanceError("lifecycle_state_invalid", "Create 只能创建 discovered/proposed")
    after = copy.deepcopy(before)
    _registry_entries(after, kind).append(copy.deepcopy(entry))
    after["registry_revision"] += 1
    after["previous_snapshot_id"] = before.get("active_snapshot_id")
    after["active_snapshot_id"] = None
    after["lifecycle_log"].append(lifecycle_event(len(after["lifecycle_log"]) + 1, "create", actor, reason, None, str(entry["state"]), at_utc))
    return _save_mutation(path, before, after, "create", actor, request_id, at_utc, receipt_path)


def operate_update(path: Path, entry_path: Path, kind: str, expected_revision: int, actor: str, role: str, reason: str, request_id: str, at_utc: str, receipt_path: Optional[Path]) -> Dict[str, Any]:
    _authorize(role, "registry:propose")
    before = require_mapping(read_json(path), "registry_set")
    validate_registry_set(before)
    if before["registry_revision"] != expected_revision:
        raise GovernanceError("registry_revision_conflict", "expected_registry_revision 不匹配")
    entry = require_mapping(read_json(entry_path), "entry")
    stable_id, version = _entry_key(entry, kind)
    versions = [item for item in _registry_entries(before, kind) if _entry_key(item, kind)[0] == stable_id]
    if not versions:
        raise GovernanceError("entry_not_found", "Update 需要既有稳定 ID")
    if any(_entry_key(item, kind)[1] == version for item in versions):
        raise GovernanceError("stable_id_version_reused", "Update 不得原地改写既有 ID+SemVer")
    latest = max(versions, key=lambda item: parse_semver(str(item["version"])))
    impact = impact_analysis(before, kind, stable_id, str(latest["version"]), entry)
    compatibility = impact["compatibility"]
    if compatibility["requires_new_stable_id"]:
        raise GovernanceError("new_stable_id_required", "用户结果改变必须创建新 Capability ID")
    if entry.get("state") != "proposed":
        raise GovernanceError("lifecycle_state_invalid", "Update 新版本必须从 proposed 开始")
    after = copy.deepcopy(before)
    _registry_entries(after, kind).append(copy.deepcopy(entry))
    after["registry_revision"] += 1
    after["previous_snapshot_id"] = before.get("active_snapshot_id")
    after["active_snapshot_id"] = None
    after["lifecycle_log"].append(lifecycle_event(len(after["lifecycle_log"]) + 1, "update", actor, reason, str(latest["state"]), "proposed", at_utc))
    return _save_mutation(path, before, after, "update", actor, request_id, at_utc, receipt_path, impact)


def transition_role(to_state: str) -> str:
    if to_state == "proposed":
        return "registry:propose"
    if to_state in ("oracle_ready", "certified"):
        return "registry:certify"
    if to_state in ("active", "deprecated"):
        return "registry:activate"
    if to_state == "retired":
        return "registry:retire"
    raise GovernanceError("action_invalid", f"transition 不支持目标状态：{to_state}")


def operate_transition(path: Path, kind: str, stable_id: str, version: str, to_state: str, expected_revision: int, actor: str, role: str, reason: str, request_id: str, at_utc: str, receipt_path: Optional[Path], certification_path: Optional[Path], replacement: Optional[str], migration_deadline: Optional[str], snapshot_out: Optional[Path]) -> Dict[str, Any]:
    _authorize(role, transition_role(to_state))
    before = require_mapping(read_json(path), "registry_set")
    validate_registry_set(before)
    if before["registry_revision"] != expected_revision:
        raise GovernanceError("registry_revision_conflict", "expected_registry_revision 不匹配")
    after = copy.deepcopy(before)
    entry = _find_entry(after, kind, stable_id, version)
    current = str(entry["state"])
    if to_state not in TRANSITIONS[current]:
        raise GovernanceError("lifecycle_transition_invalid", f"非法迁移 {current}->{to_state}")
    if to_state == "oracle_ready" and len(entry.get("oracle_refs", [])) < 1:
        raise GovernanceError("oracle_missing", "oracle_ready 需要 Oracle")
    if to_state == "certified":
        if certification_path is None:
            raise GovernanceError("certification_evidence_missing", "certified 需要证据文件")
        evidence = require_mapping(read_json(certification_path), "certification_evidence")
        required = ("oracle_digest", "product_semantic_review_digest", "security_digest", "cost_audit_digest", "performance_audit_digest", "rollback_evidence_digest")
        require_keys(evidence, required, "certification_evidence")
        for field in required:
            _validate_digest(evidence[field], field)
        entry["certification_evidence"] = copy.deepcopy(evidence)
    if to_state == "deprecated":
        if replacement is None:
            raise GovernanceError("replacement_missing", "deprecated 需要 replacement 或 no_replacement 原因")
        if migration_deadline is None:
            raise GovernanceError("migration_deadline_missing", "deprecated 需要迁移期限")
        entry["replacement"] = {"target": replacement}
        entry["migration_deadline"] = parse_utc(migration_deadline)
    if to_state == "retired":
        active_snapshot_id = before.get("active_snapshot_id")
        if active_snapshot_id and entry.get("state") == "active":
            raise GovernanceError("active_dependency_blocks_retire", "活动版本必须先 deprecated 并从新快照移除")
    _append_transition(entry, to_state, actor, reason, at_utc)
    after["registry_revision"] += 1
    after["previous_snapshot_id"] = before.get("active_snapshot_id")
    after["active_snapshot_id"] = None
    snapshot = None
    if to_state == "active":
        if snapshot_out is None:
            raise GovernanceError("registry_snapshot_missing", "active 迁移必须同时生成快照")
        for capability in _active_entries(after, "capability"):
            for unit_ref in capability["execution_units"]:
                unit = _find_entry(after, "execution_unit", unit_ref["unit_id"], unit_ref["version"])
                if unit["state"] != "active":
                    raise GovernanceError("dependency_not_active", "Capability 激活依赖非 active 单元")
        snapshot = build_snapshot(after, at_utc)
        after["active_snapshot_id"] = snapshot["registry_snapshot_id"]
        after["snapshot_history"].append(snapshot["registry_snapshot_id"])
    after["lifecycle_log"].append(lifecycle_event(len(after["lifecycle_log"]) + 1, "transition", actor, reason, current, to_state, at_utc))
    receipt = _save_mutation(path, before, after, "transition", actor, request_id, at_utc, receipt_path, impact_analysis(before, kind, stable_id, version))
    if snapshot is not None and snapshot_out is not None:
        write_json_atomic(snapshot_out, snapshot)
    return receipt


def operate_delete(path: Path, kind: str, stable_id: str, version: str, expected_revision: int, actor: str, role: str, reason: str, request_id: str, at_utc: str, receipt_path: Optional[Path]) -> Dict[str, Any]:
    _authorize(role, "registry:tombstone")
    before = require_mapping(read_json(path), "registry_set")
    validate_registry_set(before)
    if before["registry_revision"] != expected_revision:
        raise GovernanceError("registry_revision_conflict", "expected_registry_revision 不匹配")
    after = copy.deepcopy(before)
    entry = _find_entry(after, kind, stable_id, version)
    if entry["state"] != "retired":
        raise GovernanceError("lifecycle_transition_invalid", "Delete 只允许 retired 版本")
    retained = {field: entry[field] for field in ("contract_digest", "implementation_digest", "semantic_digest")}
    entry["tombstone"] = {
        "deleted_at": parse_utc(at_utc),
        "reason": reason,
        "retained_identity": {"stable_id": stable_id, "version": version},
        "retained_digests": retained,
        "id_reuse_forbidden": True,
    }
    entry["contract_material"] = None
    entry["semantic_material"] = None
    if kind == "execution_unit":
        entry["implementation_files"] = []
    _append_transition(entry, "tombstoned", actor, reason, at_utc)
    after["registry_revision"] += 1
    after["previous_snapshot_id"] = before.get("active_snapshot_id")
    after["active_snapshot_id"] = None
    after["lifecycle_log"].append(lifecycle_event(len(after["lifecycle_log"]) + 1, "delete", actor, reason, "retired", "tombstoned", at_utc))
    return _save_mutation(path, before, after, "delete", actor, request_id, at_utc, receipt_path, impact_analysis(before, kind, stable_id, version))


def operate_rollback_unit(
    path: Path,
    unit_id: str,
    target_version: str,
    expected_revision: int,
    actor: str,
    role: str,
    reason: str,
    request_id: str,
    at_utc: str,
    snapshot_out: Path,
    receipt_path: Optional[Path],
) -> Dict[str, Any]:
    """以新 revision 恢复一个旧已认证单元，并为受影响 Capability 建新 Patch 版本。"""
    _authorize(role, "registry:rollback")
    before = require_mapping(read_json(path), "registry_set")
    validate_registry_set(before, require_active_snapshot=True)
    if before["registry_revision"] != expected_revision:
        raise GovernanceError("registry_revision_conflict", "expected_registry_revision 不匹配")
    after = copy.deepcopy(before)
    target = _find_entry(after, "execution_unit", unit_id, target_version)
    if target["state"] not in ("certified", "deprecated"):
        raise GovernanceError("rollback_target_invalid", "单单元回滚目标必须是 certified 或 deprecated")
    active_versions = [entry for entry in _active_entries(after, "execution_unit") if entry["unit_id"] == unit_id]
    if len(active_versions) != 1:
        raise GovernanceError("active_version_not_unique", f"{unit_id} 活动版本不是唯一一个")
    current = active_versions[0]
    if current["version"] == target_version:
        raise GovernanceError("rollback_target_invalid", "目标版本已经 active")
    impact = impact_analysis(before, "execution_unit", unit_id, str(current["version"]))
    _append_transition(current, "deprecated", actor, reason, at_utc)
    current["replacement"] = {"target": f"{unit_id}@{target_version}", "reason": "single_unit_rollback"}
    current["migration_deadline"] = parse_utc(at_utc)
    _append_transition(target, "active", actor, reason, at_utc)
    for capability in list(_active_entries(after, "capability")):
        references = capability.get("execution_units", [])
        if not any(reference.get("unit_id") == unit_id and reference.get("version") == current["version"] for reference in references):
            continue
        _append_transition(capability, "deprecated", actor, reason, at_utc)
        capability["replacement"] = {"target": f"{capability['capability_id']}@rollback-patch", "reason": "execution_unit_rollback"}
        capability["migration_deadline"] = parse_utc(at_utc)
        replacement = copy.deepcopy(capability)
        replacement["version"] = next_patch(str(capability["version"]))
        existing_versions = {
            entry["version"] for entry in _registry_entries(after, "capability")
            if entry["capability_id"] == capability["capability_id"]
        }
        while replacement["version"] in existing_versions:
            replacement["version"] = next_patch(str(replacement["version"]))
        replacement["state"] = "active"
        replacement["execution_units"] = [
            {
                "unit_id": target["unit_id"],
                "version": target["version"],
                "contract_digest": target["contract_digest"],
                "implementation_digest": target["implementation_digest"],
                "semantic_digest": target["semantic_digest"],
            }
            if reference.get("unit_id") == unit_id
            else copy.deepcopy(reference)
            for reference in references
        ]
        replacement["implementation_digest"] = digest_value(replacement["execution_units"])
        replacement["replacement"] = None
        replacement["migration_deadline"] = None
        replacement["tombstone"] = None
        replacement["lifecycle_history"] = migration_history(at_utc, "active")
        _registry_entries(after, "capability").append(replacement)
    after["registry_revision"] += 1
    after["previous_snapshot_id"] = before["active_snapshot_id"]
    after["active_snapshot_id"] = None
    after["lifecycle_log"].append(
        lifecycle_event(
            len(after["lifecycle_log"]) + 1,
            "rollback_unit",
            actor,
            reason,
            "active",
            "active",
            at_utc,
        )
    )
    snapshot = build_snapshot(after, at_utc)
    after["active_snapshot_id"] = snapshot["registry_snapshot_id"]
    after["snapshot_history"].append(snapshot["registry_snapshot_id"])
    validate_registry_set(after, require_active_snapshot=True)
    write_json_atomic(path, after)
    write_json_atomic(snapshot_out, snapshot)
    receipt = _new_receipt(before, after, "rollback_unit", actor, request_id, at_utc, impact)
    if receipt_path is not None:
        write_json_atomic(receipt_path, receipt)
    return receipt


def operate_rollback_edition(
    path: Path,
    target_snapshot_path: Path,
    expected_revision: int,
    actor: str,
    role: str,
    reason: str,
    request_id: str,
    at_utc: str,
    snapshot_out: Path,
    receipt_path: Optional[Path],
) -> Dict[str, Any]:
    """把完整 Registry set 恢复为一个旧快照的选择，不允许跨快照拼接。"""
    _authorize(role, "registry:rollback")
    before = require_mapping(read_json(path), "registry_set")
    validate_registry_set(before, require_active_snapshot=True)
    if before["registry_revision"] != expected_revision:
        raise GovernanceError("registry_revision_conflict", "expected_registry_revision 不匹配")
    target_snapshot = require_mapping(read_json(target_snapshot_path), "target_snapshot")
    validate_snapshot(target_snapshot)
    target_payload = require_mapping(target_snapshot["snapshot_payload"], "target_snapshot.snapshot_payload")
    if target_payload.get("candidate_id") != before.get("candidate_id"):
        raise GovernanceError("candidate_identity_conflict", "整版回滚目标属于不同 candidate")
    after = copy.deepcopy(before)
    after["capability_registry"] = copy.deepcopy(target_payload["capability_registry"])
    after["execution_unit_registry"] = copy.deepcopy(target_payload["execution_unit_registry"])
    after["registry_revision"] = before["registry_revision"] + 1
    after["previous_snapshot_id"] = before["active_snapshot_id"]
    after["active_snapshot_id"] = None
    after["lifecycle_log"].append(
        lifecycle_event(
            len(after["lifecycle_log"]) + 1,
            "rollback_edition",
            actor,
            reason,
            "active",
            "active",
            at_utc,
        )
    )
    new_snapshot = build_snapshot(after, at_utc)
    after["active_snapshot_id"] = new_snapshot["registry_snapshot_id"]
    after["snapshot_history"].append(new_snapshot["registry_snapshot_id"])
    validate_registry_set(after, require_active_snapshot=True)
    write_json_atomic(path, after)
    write_json_atomic(snapshot_out, new_snapshot)
    impact = {
        "snapshot_mode": "whole_edition",
        "target_snapshot_id": target_snapshot["registry_snapshot_id"],
        "mixed_snapshot": False,
        "new_registry_revision": after["registry_revision"],
    }
    receipt = _new_receipt(before, after, "rollback_edition", actor, request_id, at_utc, impact)
    if receipt_path is not None:
        write_json_atomic(receipt_path, receipt)
    return receipt


def check_plan(snapshot: Mapping[str, Any], plan: Mapping[str, Any]) -> Dict[str, Any]:
    validate_snapshot(snapshot)
    require_keys(plan, ("schema_version", "plan_id", "plan_kind", "registry_snapshot_id", "registry_revision", "event_identity", "nodes"), "plan")
    if plan["schema_version"] != "country_outage_p2_s0a_plan_admission_v1":
        raise GovernanceError("plan_schema_invalid", "计划 Schema 无效")
    if plan["registry_snapshot_id"] != snapshot["registry_snapshot_id"]:
        raise GovernanceError("registry_snapshot_conflict", "计划与快照身份冲突")
    payload = require_mapping(snapshot["snapshot_payload"], "snapshot_payload")
    if plan["registry_revision"] != payload["registry_revision"]:
        raise GovernanceError("registry_revision_conflict", "计划与 Registry revision 冲突")
    event_identity = require_mapping(plan["event_identity"], "event_identity")
    if event_identity.get("event_type") != "country_outage" or event_identity.get("collector_id") != "rrc25":
        raise GovernanceError("boundary_violation", "计划不是 RRC25 country_outage")
    temporary_set = {
        "schema_version": REGISTRY_SCHEMA,
        "candidate_id": payload["candidate_id"],
        "registry_revision": payload["registry_revision"],
        "activation_scope": payload["activation_scope"],
        "runtime_integration": payload["runtime_integration"],
        "capability_registry": payload["capability_registry"],
        "execution_unit_registry": payload["execution_unit_registry"],
        "lifecycle_log": [],
        "active_snapshot_id": snapshot["registry_snapshot_id"],
        "previous_snapshot_id": None,
        "snapshot_history": [snapshot["registry_snapshot_id"]],
    }
    for raw_node in require_list(plan["nodes"], "nodes"):
        node = require_mapping(raw_node, "nodes[]")
        capability = _find_entry(temporary_set, "capability", str(node.get("capability_id")), str(node.get("capability_version")))
        unit = _find_entry(temporary_set, "execution_unit", str(node.get("execution_unit_id")), str(node.get("execution_unit_version")))
        if capability["state"] != "active":
            raise GovernanceError("capability_not_active", f"{capability['capability_id']} 非 active")
        if unit["state"] != "active":
            raise GovernanceError("execution_unit_not_active", f"{unit['unit_id']} 非 active")
        expected = {
            "capability_contract_digest": capability["contract_digest"],
            "unit_contract_digest": unit["contract_digest"],
            "unit_implementation_digest": unit["implementation_digest"],
            "unit_semantic_digest": unit["semantic_digest"],
        }
        for field, value in expected.items():
            if node.get(field) != value:
                raise GovernanceError("digest_mismatch", f"计划节点 {field} 漂移")
        if not any(reference["unit_id"] == unit["unit_id"] and reference["version"] == unit["version"] for reference in capability["execution_units"]):
            raise GovernanceError("capability_unit_mismatch", "Capability 与执行单元映射不一致")
    return {"status": "admitted", "plan_id": plan["plan_id"], "registry_snapshot_id": snapshot["registry_snapshot_id"], "node_count": len(plan["nodes"]), "execution_started": False}


ACCEPTANCE_ARTIFACTS = (
    ".codex/TASK.json",
    ".codex/hooks/country_outage_agent_p2_s0a_alignment.py",
    "docs/agent/P2-组合式调查/Tool与Operator生命周期治理/Task-Spec-最终验收文档.md",
    "docs/agent/P2-组合式调查/Tool与Operator生命周期治理/Plan-分阶段计划.md",
    "contracts/agent/country-outage-p2-s0a-lifecycle/lifecycle-policy.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/registry-set.schema.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/governance-request.schema.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/governance-receipt.schema.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/plan-admission.schema.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/product-semantic-charter.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/question-probes.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/governance-oracle.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/registry-set.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/capability-registry.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/execution-unit-registry.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/registry-snapshot.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/migration-map.json",
    "contracts/agent/country-outage-p2-s0a-lifecycle/candidate.json",
    "dev/tools/manage_country_outage_p2_registry.py",
    "dev/tools/review_country_outage_p2_semantics.py",
    "dev/tests/test_country_outage_p2_s0a_registry.py",
    "dev/tests/test_country_outage_p2_s0a_alignment_hook.py",
    "evaluation/country-outage/p2-s0a-lifecycle/migration-proposal.json",
    "evaluation/country-outage/p2-s0a-lifecycle/product-semantic-proposal-review.json",
    "evaluation/country-outage/p2-s0a-lifecycle/product-semantic-final-review.json",
    "evaluation/country-outage/p2-s0a-lifecycle/registry-test-receipt.json",
    "evaluation/country-outage/p2-s0a-lifecycle/alignment-test-receipt.json",
    "evaluation/country-outage/p2-s0a-lifecycle/governance/plan-admission-input.json",
    "evaluation/country-outage/p2-s0a-lifecycle/governance/plan-admission-receipt.json",
    "evaluation/country-outage/p2-s0a-lifecycle/governance/single-unit-rollback-receipt.json",
    "evaluation/country-outage/p2-s0a-lifecycle/governance/single-unit-rollback-snapshot.json",
    "evaluation/country-outage/p2-s0a-lifecycle/governance/whole-edition-rollback-receipt.json",
    "evaluation/country-outage/p2-s0a-lifecycle/governance/whole-edition-rollback-snapshot.json",
    "evaluation/country-outage/p2-s0a-lifecycle/governance/tombstone-receipt.json",
)


def build_acceptance_manifest(repo_root: Path, accepted_at: str) -> Dict[str, Any]:
    """组合同一离线候选的最终验收摘要，不宣称运行时或生产验收。"""
    accepted_at = parse_utc(accepted_at)
    contract_root = repo_root / "contracts/agent/country-outage-p2-s0a-lifecycle"
    evidence_root = repo_root / "evaluation/country-outage/p2-s0a-lifecycle"
    candidate = require_mapping(read_json(contract_root / "candidate.json"), "candidate")
    registry_set = require_mapping(read_json(contract_root / "registry-set.json"), "registry_set")
    snapshot = require_mapping(read_json(contract_root / "registry-snapshot.json"), "snapshot")
    validate_registry_set(registry_set, require_active_snapshot=True)
    validate_snapshot(snapshot)
    if candidate.get("candidate_id") != registry_set.get("candidate_id"):
        raise GovernanceError("candidate_identity_conflict", "candidate 与 Registry 身份不一致")
    if candidate.get("registry_snapshot_id") != snapshot.get("registry_snapshot_id"):
        raise GovernanceError("registry_snapshot_conflict", "candidate 与快照身份不一致")
    if snapshot.get("snapshot_payload") != snapshot_payload(registry_set):
        raise GovernanceError("registry_snapshot_conflict", "Registry 内容与快照不一致")
    if candidate.get("runtime_integration") != RUNTIME_INTEGRATION or candidate.get("production_deployed") is not False:
        raise GovernanceError("boundary_violation", "candidate 越过非运行时或非部署边界")

    final_review = require_mapping(read_json(evidence_root / "product-semantic-final-review.json"), "final_review")
    registry_tests = require_mapping(read_json(evidence_root / "registry-test-receipt.json"), "registry_tests")
    alignment_tests = require_mapping(read_json(evidence_root / "alignment-test-receipt.json"), "alignment_tests")
    candidate_id = str(candidate["candidate_id"])
    if final_review.get("candidate_id") != candidate_id or final_review.get("status") != "PASS" or final_review.get("blocking_count") != 0:
        raise GovernanceError("semantic_review_failed", "独立产品语义 Reviewer 未对同一 candidate 给出 PASS")
    for label, receipt in (("Registry 测试", registry_tests), ("Alignment 篡改测试", alignment_tests)):
        if receipt.get("candidate_id") != candidate_id or receipt.get("status") != "passed":
            raise GovernanceError("acceptance_evidence_failed", f"{label} 未对同一 candidate 通过")
        if receipt.get("runtime_integration") != RUNTIME_INTEGRATION or receipt.get("production_deployed") is not False:
            raise GovernanceError("boundary_violation", f"{label} 越过非运行时或非部署边界")

    artifacts: List[Dict[str, str]] = []
    for relative_path in ACCEPTANCE_ARTIFACTS:
        path = repo_root / relative_path
        if not path.is_file() or path.is_symlink():
            raise GovernanceError("acceptance_artifact_missing", f"验收制品不是规范普通文件：{relative_path}")
        artifacts.append({"path": relative_path, "sha256": digest_file(path)})
    manifest: Dict[str, Any] = {
        "schema_version": "country_outage_p2_s0a_acceptance_manifest_v1",
        "status": "accepted_offline_candidate",
        "candidate_id": candidate_id,
        "registry_revision": registry_set["registry_revision"],
        "registry_snapshot_id": snapshot["registry_snapshot_id"],
        "accepted_at": accepted_at,
        "gates": {
            "registry_test_count": registry_tests.get("test_count"),
            "alignment_test_count": alignment_tests.get("test_count"),
            "product_semantic_blocking_count": final_review.get("blocking_count"),
            "active_capability_count": len([entry for entry in registry_set["capability_registry"]["entries"] if entry["state"] == "active"]),
            "active_execution_unit_count": len([entry for entry in registry_set["execution_unit_registry"]["entries"] if entry["state"] == "active"]),
        },
        "artifacts": artifacts,
        "runtime_integration": RUNTIME_INTEGRATION,
        "production_deployed": False,
        "acceptance_scope": "同候选离线合同、治理、迁移、语义、回滚与篡改证据；不证明生产运行时能力",
    }
    manifest["manifest_digest"] = digest_value(manifest)
    return manifest


def _print(value: Any) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P2-S0A 离线 Tool/Operator Registry 生命周期治理")
    sub = parser.add_subparsers(dest="command", required=True)
    migrate = sub.add_parser("migrate-proposal")
    migrate.add_argument("--repo-root", required=True)
    migrate.add_argument("--output", required=True)
    migrate.add_argument("--created-at", required=True)
    finalize = sub.add_parser("finalize-migration")
    finalize.add_argument("--repo-root", required=True)
    finalize.add_argument("--proposal", required=True)
    finalize.add_argument("--review", required=True)
    finalize.add_argument("--output-root", required=True)
    finalize.add_argument("--created-at", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--registry-set", required=True)
    validate.add_argument("--snapshot")
    read = sub.add_parser("read")
    read.add_argument("--registry-set", required=True)
    read.add_argument("--kind", choices=("capability", "execution_unit"), required=True)
    read.add_argument("--id", required=True)
    read.add_argument("--version", required=True)
    impact = sub.add_parser("impact")
    impact.add_argument("--registry-set", required=True)
    impact.add_argument("--kind", choices=("capability", "execution_unit"), required=True)
    impact.add_argument("--id", required=True)
    impact.add_argument("--version", required=True)
    impact.add_argument("--proposed")
    for name in ("create", "update"):
        action = sub.add_parser(name)
        action.add_argument("--registry-set", required=True)
        action.add_argument("--entry", required=True)
        action.add_argument("--kind", choices=("capability", "execution_unit"), required=True)
        action.add_argument("--expected-revision", type=int, required=True)
        action.add_argument("--actor", required=True)
        action.add_argument("--role", required=True)
        action.add_argument("--reason", required=True)
        action.add_argument("--request-id", required=True)
        action.add_argument("--at-utc", required=True)
        action.add_argument("--receipt")
    transition = sub.add_parser("transition")
    transition.add_argument("--registry-set", required=True)
    transition.add_argument("--kind", choices=("capability", "execution_unit"), required=True)
    transition.add_argument("--id", required=True)
    transition.add_argument("--version", required=True)
    transition.add_argument("--to-state", required=True)
    transition.add_argument("--expected-revision", type=int, required=True)
    transition.add_argument("--actor", required=True)
    transition.add_argument("--role", required=True)
    transition.add_argument("--reason", required=True)
    transition.add_argument("--request-id", required=True)
    transition.add_argument("--at-utc", required=True)
    transition.add_argument("--receipt")
    transition.add_argument("--certification")
    transition.add_argument("--replacement")
    transition.add_argument("--migration-deadline")
    transition.add_argument("--snapshot-out")
    delete = sub.add_parser("delete")
    delete.add_argument("--registry-set", required=True)
    delete.add_argument("--kind", choices=("capability", "execution_unit"), required=True)
    delete.add_argument("--id", required=True)
    delete.add_argument("--version", required=True)
    delete.add_argument("--expected-revision", type=int, required=True)
    delete.add_argument("--actor", required=True)
    delete.add_argument("--role", required=True)
    delete.add_argument("--reason", required=True)
    delete.add_argument("--request-id", required=True)
    delete.add_argument("--at-utc", required=True)
    delete.add_argument("--receipt")
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--registry-set", required=True)
    snapshot.add_argument("--output", required=True)
    snapshot.add_argument("--created-at", required=True)
    plan = sub.add_parser("check-plan")
    plan.add_argument("--snapshot", required=True)
    plan.add_argument("--plan", required=True)
    rollback_unit = sub.add_parser("rollback-unit")
    rollback_unit.add_argument("--registry-set", required=True)
    rollback_unit.add_argument("--id", required=True)
    rollback_unit.add_argument("--target-version", required=True)
    rollback_unit.add_argument("--expected-revision", type=int, required=True)
    rollback_unit.add_argument("--actor", required=True)
    rollback_unit.add_argument("--role", required=True)
    rollback_unit.add_argument("--reason", required=True)
    rollback_unit.add_argument("--request-id", required=True)
    rollback_unit.add_argument("--at-utc", required=True)
    rollback_unit.add_argument("--snapshot-out", required=True)
    rollback_unit.add_argument("--receipt")
    rollback_edition = sub.add_parser("rollback-edition")
    rollback_edition.add_argument("--registry-set", required=True)
    rollback_edition.add_argument("--target-snapshot", required=True)
    rollback_edition.add_argument("--expected-revision", type=int, required=True)
    rollback_edition.add_argument("--actor", required=True)
    rollback_edition.add_argument("--role", required=True)
    rollback_edition.add_argument("--reason", required=True)
    rollback_edition.add_argument("--request-id", required=True)
    rollback_edition.add_argument("--at-utc", required=True)
    rollback_edition.add_argument("--snapshot-out", required=True)
    rollback_edition.add_argument("--receipt")
    acceptance = sub.add_parser("build-acceptance-manifest")
    acceptance.add_argument("--repo-root", required=True)
    acceptance.add_argument("--output", required=True)
    acceptance.add_argument("--accepted-at", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "migrate-proposal":
            repo_root = Path(args.repo_root).resolve()
            proposal = build_migration_proposal(repo_root, args.created_at)
            write_json_atomic(Path(args.output).resolve(), proposal)
            _print({"status": "created", "candidate_id": proposal["candidate_id"], "proposal_digest": proposal["proposal_digest"], "output": str(Path(args.output).resolve())})
        elif args.command == "finalize-migration":
            repo_root = Path(args.repo_root).resolve()
            proposal = require_mapping(read_json(Path(args.proposal).resolve()), "proposal")
            review = require_mapping(read_json(Path(args.review).resolve()), "review")
            registry_set, snapshot, candidate = finalize_migration(repo_root, proposal, review, args.created_at)
            output_root = Path(args.output_root).resolve()
            write_json_atomic(output_root / "registry-set.json", registry_set)
            write_json_atomic(output_root / "capability-registry.json", exported_registry(registry_set, "capability"))
            write_json_atomic(output_root / "execution-unit-registry.json", exported_registry(registry_set, "execution_unit"))
            write_json_atomic(output_root / "registry-snapshot.json", snapshot)
            write_json_atomic(output_root / "migration-map.json", migration_map(registry_set))
            artifacts = {}
            for name in ("registry-set.json", "capability-registry.json", "execution-unit-registry.json", "registry-snapshot.json", "migration-map.json"):
                artifacts[name] = digest_file(output_root / name)
            candidate["artifact_digests"] = artifacts
            write_json_atomic(output_root / "candidate.json", candidate)
            _print({"status": "finalized", "candidate_id": candidate["candidate_id"], "registry_snapshot_id": candidate["registry_snapshot_id"], "output_root": str(output_root)})
        elif args.command == "validate":
            registry_set = require_mapping(read_json(Path(args.registry_set).resolve()), "registry_set")
            result = validate_registry_set(registry_set, require_active_snapshot=bool(args.snapshot))
            if args.snapshot:
                snapshot = require_mapping(read_json(Path(args.snapshot).resolve()), "snapshot")
                snapshot_result = validate_snapshot(snapshot)
                if snapshot["registry_snapshot_id"] != registry_set.get("active_snapshot_id"):
                    raise GovernanceError("registry_snapshot_conflict", "Registry 活动指针与快照不一致")
                if snapshot["snapshot_payload"] != snapshot_payload(registry_set):
                    raise GovernanceError("registry_snapshot_conflict", "Registry 当前内容与快照不一致")
                result["snapshot"] = snapshot_result
            _print(result)
        elif args.command == "read":
            registry_set = require_mapping(read_json(Path(args.registry_set).resolve()), "registry_set")
            validate_registry_set(registry_set)
            _print({"status": "read_only", "entry": _find_entry(registry_set, args.kind, args.id, args.version), "registry_revision": registry_set["registry_revision"]})
        elif args.command == "impact":
            registry_set = require_mapping(read_json(Path(args.registry_set).resolve()), "registry_set")
            validate_registry_set(registry_set)
            proposed = require_mapping(read_json(Path(args.proposed).resolve()), "proposed") if args.proposed else None
            _print(impact_analysis(registry_set, args.kind, args.id, args.version, proposed))
        elif args.command == "create":
            _print(operate_create(Path(args.registry_set).resolve(), Path(args.entry).resolve(), args.kind, args.expected_revision, args.actor, args.role, args.reason, args.request_id, args.at_utc, Path(args.receipt).resolve() if args.receipt else None))
        elif args.command == "update":
            _print(operate_update(Path(args.registry_set).resolve(), Path(args.entry).resolve(), args.kind, args.expected_revision, args.actor, args.role, args.reason, args.request_id, args.at_utc, Path(args.receipt).resolve() if args.receipt else None))
        elif args.command == "transition":
            _print(operate_transition(Path(args.registry_set).resolve(), args.kind, args.id, args.version, args.to_state, args.expected_revision, args.actor, args.role, args.reason, args.request_id, args.at_utc, Path(args.receipt).resolve() if args.receipt else None, Path(args.certification).resolve() if args.certification else None, args.replacement, args.migration_deadline, Path(args.snapshot_out).resolve() if args.snapshot_out else None))
        elif args.command == "delete":
            _print(operate_delete(Path(args.registry_set).resolve(), args.kind, args.id, args.version, args.expected_revision, args.actor, args.role, args.reason, args.request_id, args.at_utc, Path(args.receipt).resolve() if args.receipt else None))
        elif args.command == "snapshot":
            registry_set = require_mapping(read_json(Path(args.registry_set).resolve()), "registry_set")
            validate_registry_set(registry_set)
            snapshot = build_snapshot(registry_set, args.created_at)
            write_json_atomic(Path(args.output).resolve(), snapshot)
            _print({"status": "created", "registry_snapshot_id": snapshot["registry_snapshot_id"], "output": str(Path(args.output).resolve())})
        elif args.command == "check-plan":
            snapshot = require_mapping(read_json(Path(args.snapshot).resolve()), "snapshot")
            plan = require_mapping(read_json(Path(args.plan).resolve()), "plan")
            _print(check_plan(snapshot, plan))
        elif args.command == "rollback-unit":
            _print(
                operate_rollback_unit(
                    Path(args.registry_set).resolve(),
                    args.id,
                    args.target_version,
                    args.expected_revision,
                    args.actor,
                    args.role,
                    args.reason,
                    args.request_id,
                    args.at_utc,
                    Path(args.snapshot_out).resolve(),
                    Path(args.receipt).resolve() if args.receipt else None,
                )
            )
        elif args.command == "rollback-edition":
            _print(
                operate_rollback_edition(
                    Path(args.registry_set).resolve(),
                    Path(args.target_snapshot).resolve(),
                    args.expected_revision,
                    args.actor,
                    args.role,
                    args.reason,
                    args.request_id,
                    args.at_utc,
                    Path(args.snapshot_out).resolve(),
                    Path(args.receipt).resolve() if args.receipt else None,
                )
            )
        elif args.command == "build-acceptance-manifest":
            repo_root = Path(args.repo_root).resolve()
            manifest = build_acceptance_manifest(repo_root, args.accepted_at)
            output = Path(args.output)
            if not output.is_absolute():
                output = repo_root / output
            write_json_atomic(output, manifest)
            _print({"status": manifest["status"], "candidate_id": manifest["candidate_id"], "manifest_digest": manifest["manifest_digest"], "output": str(output)})
        else:
            raise GovernanceError("action_invalid", f"未知动作：{args.command}")
        return 0
    except GovernanceError as exc:
        _print({"status": "rejected", "error_code": exc.code, "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
