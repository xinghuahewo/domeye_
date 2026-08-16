"""W5 Evidence Graph 冻结合同实例、引用闭包与提交回执。

本模块持久化的对象本身严格符合 S1D-4 冻结 Evidence Graph Schema；W5
实际运行事实只写入独立 closure/commit receipt，不篡改设计合同边界字段。
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from .country_outage_p2_s1_contract_runtime import (
    digest_hex,
    load_frozen_contract,
    validate_identity,
    validate_json_schema,
)
from .country_outage_p2_s1_trusted_store import ContentAddressedStore


_NODE_TYPES = {"observed_fact", "derived_fact", "result_set", "limitation", "unknown", "execution_failure"}
_EDGE_TYPES = {
    "derived_from", "member_of", "at_time", "precedes", "same_window", "follows",
    "path_contains", "directly_adjacent_in_path", "set_intersects", "set_contains",
    "supports", "conflicts_with", "limited_by", "requires_external_evidence",
}


class W5EvidenceGraphError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = False
        self.next_action = None
        super().__init__(message)


def _hex(value: Any) -> str:
    text = str(value)
    return text.removeprefix("sha256:")


def _payload_digest(payload: Mapping[str, Any]) -> str:
    # 冻结合同明确要求 sha256(canonical({payload}))，不是 payload 裸值摘要。
    return digest_hex({"payload": payload})


def _edge_digest(edge: Mapping[str, Any]) -> str:
    material = copy.deepcopy(dict(edge))
    material.pop("edge_digest", None)
    producer = dict(material["producer_ref"])
    producer.pop("run_receipt_digest", None)
    material["producer_ref"] = producer
    return digest_hex(material)


def _graph_digest(graph: Mapping[str, Any]) -> str:
    fields = (
        "graph_id", "graph_revision", "parent_graph_revision", "investigation_id",
        "investigation_revision", "plan_id", "plan_revision", "plan_digest",
        "identity_digest", "registry_snapshot_id", "registry_snapshot_digest",
        "nodes", "edges", "root_node_ids",
    )
    return digest_hex({field: copy.deepcopy(graph[field]) for field in fields})


def _acyclic(nodes: set[str], edges: Sequence[Mapping[str, Any]]) -> None:
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        if edge["edge_type"] == "derived_from":
            outgoing[edge["from_node_id"]].append(edge["to_node_id"])
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise W5EvidenceGraphError("evidence_graph_cycle", "derived_from 子图存在环")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in outgoing[node_id]:
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in sorted(nodes):
        visit(node_id)


def validate_evidence_references(graph: Mapping[str, Any]) -> None:
    """GATE-04：冻结 Schema、身份、生产者、端点、摘要和 DAG 全部闭包。"""

    validate_json_schema(graph, load_frozen_contract("evidence-graph"), "frozen EvidenceGraph")
    if graph.get("graph_state") != "committed":
        raise W5EvidenceGraphError("evidence_graph_not_committed", "Evidence Graph 未封存")
    nodes = graph["nodes"]
    edges = graph["edges"]
    node_by_id: dict[str, Mapping[str, Any]] = {}
    for node in nodes:
        node_id = node["node_id"]
        if node_id in node_by_id:
            raise W5EvidenceGraphError("evidence_node_duplicate", "Evidence node id 重复")
        if node["node_type"] not in _NODE_TYPES or node["identity_digest"] != graph["identity_digest"]:
            raise W5EvidenceGraphError("evidence_node_binding_invalid", "Evidence node 类型或身份漂移")
        if node["payload_digest"] != _payload_digest(node["payload"]):
            raise W5EvidenceGraphError("evidence_payload_digest_mismatch", "Evidence node payload 摘要不一致")
        if not node["committed"]:
            raise W5EvidenceGraphError("evidence_node_uncommitted", "committed Graph 含未提交节点")
        producer = node["producer_ref"]
        if producer["run_receipt_digest"] == "0" * 64:
            raise W5EvidenceGraphError("producer_receipt_missing", "生产回执不得为空哨兵")
        node_by_id[node_id] = node
    edge_ids: set[str] = set()
    for edge in edges:
        if edge["edge_id"] in edge_ids or edge["edge_type"] not in _EDGE_TYPES:
            raise W5EvidenceGraphError("evidence_edge_invalid", "Evidence edge id/type 无效")
        if edge["from_node_id"] not in node_by_id or edge["to_node_id"] not in node_by_id:
            raise W5EvidenceGraphError("evidence_edge_dangling", "Evidence edge 端点不存在")
        if edge["edge_digest"] != _edge_digest(edge):
            raise W5EvidenceGraphError("evidence_edge_digest_mismatch", "Evidence edge 摘要不一致")
        edge_ids.add(edge["edge_id"])
    _acyclic(set(node_by_id), edges)
    if not graph["root_node_ids"] or any(node_id not in node_by_id for node_id in graph["root_node_ids"]):
        raise W5EvidenceGraphError("evidence_root_invalid", "root_node_ids 未闭包")
    if graph["graph_digest"] != _graph_digest(graph):
        raise W5EvidenceGraphError("evidence_graph_digest_mismatch", "Evidence Graph graph_digest 无法重算")


class EvidenceGraphManager:
    def __init__(self, store: ContentAddressedStore, dispatcher: Any | None = None) -> None:
        self.store = store
        self.dispatcher = dispatcher

    def _producer(self, record: Mapping[str, Any], *, output_digest: str, receipt_kind: str = "producer") -> dict[str, Any]:
        unit_id = str(record["unit_id"])
        if self.dispatcher is None:
            raise W5EvidenceGraphError("registry_dispatcher_missing", "Graph producer 必须解析 W5 Registry")
        entry = self.dispatcher.assert_allowed(unit_id)
        kind = "tool" if unit_id.startswith("TOOL-") else "operator"
        producer_receipt_base = {
            "receipt_kind": receipt_kind, "producer_id": unit_id,
            "source_execution_receipt_digest": _hex(record["receipt_digest"]),
            "output_digest": output_digest, "disposition": "committed",
        }
        producer_receipt = {**producer_receipt_base, "receipt_digest": digest_hex(producer_receipt_base)}
        self.store.put_json("receipt-candidate", producer_receipt)
        return {
            "producer_kind": kind,
            "producer_id": unit_id,
            "producer_version": entry["unit_version"],
            "contract_digest": _hex(entry["contract_digest"]),
            "run_receipt_digest": producer_receipt["receipt_digest"],
        }

    def validate_trusted_closure(self, graph: Mapping[str, Any]) -> None:
        """从 ContentAddressedStore 解析 closure/commit/producer/ResultSet freeze 回执。"""

        validate_evidence_references(graph)
        receipts = {
            _hex(item.get("receipt_digest")): item
            for item in [*self.store.list_json("receipt"), *self.store.list_json("receipt-candidate")]
            if isinstance(item.get("receipt_digest"), str)
            and _hex(item["receipt_digest"]) == digest_hex({key: value for key, value in item.items() if key != "receipt_digest"})
        }
        closure = receipts.get(graph["closure_receipt_digest"])
        commit = receipts.get(graph["commit_receipt_digest"])
        if not isinstance(closure, Mapping) or any(closure.get(field) != expected for field, expected in {
            "receipt_kind": "graph_closure", "graph_id": graph["graph_id"],
            "graph_revision": graph["graph_revision"], "identity_digest": graph["identity_digest"],
            "registry_snapshot_id": graph["registry_snapshot_id"],
            "registry_snapshot_digest": graph["registry_snapshot_digest"], "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]), "disposition": "passed",
        }.items()):
            raise W5EvidenceGraphError("graph_closure_receipt_invalid", "Graph closure receipt 未解析或绑定漂移")
        if not isinstance(commit, Mapping) or any(commit.get(field) != expected for field, expected in {
            "receipt_kind": "graph_commit", "graph_id": graph["graph_id"],
            "graph_revision": graph["graph_revision"], "graph_digest": graph["graph_digest"],
            "closure_receipt_digest": graph["closure_receipt_digest"], "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]), "disposition": "committed",
        }.items()):
            raise W5EvidenceGraphError("graph_commit_receipt_invalid", "Graph commit receipt 未解析或绑定漂移")
        expected_producers = sorted(node["producer_ref"]["run_receipt_digest"] for node in graph["nodes"])
        if closure.get("producer_run_receipt_digests") != expected_producers or any(digest not in receipts for digest in expected_producers):
            raise W5EvidenceGraphError("graph_producer_receipt_closure_invalid", "Graph producer receipt 未闭包")
        freeze_digests = closure.get("result_set_freeze_receipt_digests")
        if not isinstance(freeze_digests, list) or any(
            digest not in receipts or receipts[digest].get("receipt_kind") != "freeze"
            for digest in freeze_digests
        ):
            raise W5EvidenceGraphError("graph_result_set_receipt_closure_invalid", "Graph ResultSet freeze receipt 未闭包")

    def _node_payload(
        self,
        record: Mapping[str, Any],
        dependency_node_ids: Sequence[str],
        dependency_payload_digests: Sequence[str],
    ) -> tuple[str, dict[str, Any], str]:
        state = record["state"]
        result = record.get("result")
        unit_id = str(record["unit_id"])
        if state not in {"succeeded", "reused"}:
            failure_kind = "cancelled" if state == "cancelled" else "skipped_dependency_failed" if state == "skipped" else "failed"
            return "execution_failure", {
                "payload_type": "execution_failure",
                "failure_receipt_ref": _hex(record["receipt_digest"]),
                "failure_kind": failure_kind,
                "affected_scope": str(record["node_id"]),
                "publishable_fact_output": False,
            }, "not_applicable"
        if isinstance(result, Mapping) and result.get("schema_version") == "country_outage_p2_result_set_v1":
            return "result_set", {
                "payload_type": "result_set",
                "result_set_id": result["result_set_id"],
                "result_set_revision": result["result_set_revision"],
                "manifest_digest": result["manifest_digest"],
                "content_digest": result["content_digest"],
                "view_ref": None,
            }, result["set_completeness"]
        if unit_id.startswith("OP-"):
            if not dependency_node_ids or not dependency_payload_digests:
                raise W5EvidenceGraphError("operator_provenance_missing", "Operator Graph 节点缺少已提交祖先")
            return "derived_fact", {
                "payload_type": "derived_fact",
                "fact_id": "fact-sha256:" + digest_hex({"node_id": record["node_id"], "result_digest": record["result_digest"]}),
                "operator_id": unit_id,
                "operator_output_digest": _hex(record["result_digest"]),
                "operator_input_digests": list(dependency_payload_digests),
            }, "complete"
        raise W5EvidenceGraphError("non_fact_record_forbidden", "Gate/Boundary/Plan control receipt 不得进入事实 Graph")

    def commit(
        self,
        *,
        investigation_id: str,
        investigation_revision: int,
        plan: Mapping[str, Any],
        identity: Mapping[str, Any],
        execution_records: Sequence[Mapping[str, Any]],
        investigation_status: str,
        parent_graph_revision: int | None = None,
    ) -> dict[str, Any]:
        source_identity = validate_identity(identity)
        graph_revision = 1 if parent_graph_revision is None else parent_graph_revision + 1
        graph_id = "evidence-graph-sha256:" + digest_hex({"investigation_id": investigation_id, "plan_id": plan["plan_id"]})
        design_definition = plan.get("design_plan_definition")
        if not isinstance(design_definition, Mapping):
            raise W5EvidenceGraphError("design_plan_unresolved", "Graph 必须绑定完整冻结 Plan definition")
        graph_identity_digest = digest_hex({
            key: (str(value).removeprefix("sha256:") if key == "registry_snapshot_digest" else value)
            for key, value in design_definition["identity"].items() if key != "binding_digest"
        })
        plan_nodes = {node["node_id"]: node for node in plan["nodes"]}
        all_records = {str(record["node_id"]): record for record in execution_records}
        records = {
            node_id: record for node_id, record in all_records.items()
            if str(record["unit_id"]).startswith(("TOOL-", "OP-"))
        }
        if not records:
            raise W5EvidenceGraphError("fact_graph_empty", "调查没有可提交的 Tool/Operator 事实节点")

        node_ids = {
            node_id: "evidence-node-sha256:" + digest_hex({"graph_id": graph_id, "plan_node_id": node_id, "receipt_digest": records[node_id]["receipt_digest"]})
            for node_id in records
        }
        nodes: list[dict[str, Any]] = []
        payload_digest_by_plan: dict[str, str] = {}
        for node_id in [node["node_id"] for node in plan["nodes"] if node["node_id"] in records]:
            record = records[node_id]
            dependencies = [dep for dep in plan_nodes[node_id]["depends_on"] if dep in node_ids]
            dep_graph_ids = [node_ids[dep] for dep in dependencies]
            dep_payload_digests = [payload_digest_by_plan[dep] for dep in dependencies if dep in payload_digest_by_plan]
            node_type, payload, completeness = self._node_payload(record, dep_graph_ids, dep_payload_digests)
            payload_digest = _payload_digest(payload)
            evidence_refs = sorted(set(str(item) for item in record.get("evidence_refs", []) if item))
            if node_type == "derived_fact" and not evidence_refs:
                raise W5EvidenceGraphError("operator_evidence_missing", "Operator derived fact 缺少 evidence_refs")
            producer = self._producer(record, output_digest=payload_digest)
            nodes.append({
                "node_id": node_ids[node_id],
                "node_type": node_type,
                "identity_digest": graph_identity_digest,
                "producer_ref": producer,
                "provenance_node_ids": dep_graph_ids if node_type == "derived_fact" else [],
                "evidence_refs": evidence_refs,
                "completeness": completeness,
                "payload": payload,
                "payload_digest": payload_digest,
                "committed": True,
            })
            payload_digest_by_plan[node_id] = payload_digest

        edges: list[dict[str, Any]] = []
        for node_id, record in records.items():
            if not str(record["unit_id"]).startswith("OP-"):
                continue
            for dependency in plan_nodes[node_id]["depends_on"]:
                if dependency not in records:
                    continue
                edge_base = {
                    "edge_id": "evidence-edge-sha256:" + digest_hex({"from": node_ids[dependency], "to": node_ids[node_id], "receipt": record["receipt_digest"]}),
                    "edge_type": "derived_from",
                    "from_node_id": node_ids[dependency],
                    "to_node_id": node_ids[node_id],
                    "producer_ref": {
                        "producer_kind": "operator", "producer_id": record["unit_id"],
                        "producer_version": self.dispatcher.admission.entries[record["unit_id"]]["unit_version"],
                        "contract_digest": _hex(self.dispatcher.admission.entries[record["unit_id"]]["contract_digest"]),
                        "run_receipt_digest": "0" * 64,
                    },
                    "relation_receipt_ref": None,
                }
                edge_digest = _edge_digest(edge_base)
                producer = self._producer(record, output_digest=edge_digest, receipt_kind="edge_producer")
                edges.append({**edge_base, "producer_ref": producer, "edge_digest": edge_digest})
        targets = {edge["to_node_id"] for edge in edges}
        roots = sorted(node["node_id"] for node in nodes if node["node_id"] not in targets)
        graph_base = {
            "schema_version": "country_outage_p2_evidence_graph_v1",
            "graph_id": graph_id,
            "graph_revision": graph_revision,
            "parent_graph_revision": parent_graph_revision,
            "graph_state": "committed",
            "investigation_id": investigation_id,
            "investigation_revision": investigation_revision,
            "plan_id": design_definition["plan_id"],
            "plan_revision": design_definition["plan_revision"],
            "plan_digest": digest_hex({"plan_definition": design_definition}),
            "identity_digest": graph_identity_digest,
            "registry_snapshot_id": source_identity["registry_snapshot_id"],
            "registry_snapshot_digest": _hex(source_identity["registry_snapshot_digest"]),
            "nodes": nodes,
            "edges": edges,
            "root_node_ids": roots,
            "design_boundary": {"design_only": True, "runtime_implemented": False, "production_deployed": False},
        }
        graph_digest = _graph_digest(graph_base)
        closure_base = {
            "receipt_kind": "graph_closure", "graph_id": graph_id, "graph_revision": graph_revision,
            "graph_digest": graph_digest, "identity_digest": graph_identity_digest,
            "registry_snapshot_id": source_identity["registry_snapshot_id"],
            "registry_snapshot_digest": _hex(source_identity["registry_snapshot_digest"]),
            "producer_run_receipt_digests": sorted(node["producer_ref"]["run_receipt_digest"] for node in nodes),
            "result_set_freeze_receipt_digests": sorted(
                record["result"]["freeze_receipt_digest"] for record in records.values()
                if isinstance(record.get("result"), Mapping) and record["result"].get("schema_version") == "country_outage_p2_result_set_v1"
            ),
            "node_count": len(nodes), "edge_count": len(edges), "disposition": "passed",
        }
        closure = {**closure_base, "receipt_digest": digest_hex(closure_base)}
        self.store.put_json("receipt-candidate", closure)
        graph_with_digest = {**graph_base, "closure_receipt_digest": closure["receipt_digest"], "graph_digest": graph_digest}
        commit_base = {
            "receipt_kind": "graph_commit",
            "graph_id": graph_id,
            "graph_revision": graph_revision,
            "graph_digest": graph_with_digest["graph_digest"],
            "closure_receipt_digest": closure["receipt_digest"],
            "node_count": len(nodes),
            "edge_count": len(edges),
            "disposition": "committed",
        }
        commit_receipt = {**commit_base, "receipt_digest": digest_hex(commit_base)}
        graph = {**graph_with_digest, "commit_receipt_digest": commit_receipt["receipt_digest"]}
        terminal_snapshot = {
            "investigation_id": investigation_id, "investigation_revision": investigation_revision,
            "parent_investigation_revision": investigation_revision - 1,
            "plan_id": design_definition["plan_id"], "plan_revision": design_definition["plan_revision"],
            "status": investigation_status,
            "node_execution_revisions": [{
                "node_id": record["node_id"], "execution_revision": record["execution_revision"],
                "parent_execution_revision": None, "state": "committed" if record["state"] == "succeeded" else record["state"],
                "idempotency_key": f"{investigation_id}:{record['node_id']}:{record['execution_revision']}",
                "input_digest": digest_hex({"parameters": plan.get("design_parameter_bindings", {}).get(record["node_id"], {})}),
                "result_digest": record.get("result_digest") if record["state"] in {"succeeded", "reused"} else None,
                "receipt_digest": _hex(record["receipt_digest"]),
                "failure_code": record.get("error", {}).get("code") if isinstance(record.get("error"), Mapping) else None,
            } for record in execution_records],
            "evidence_graph_revision": graph_revision,
        }
        terminal_snapshot["snapshot_digest"] = digest_hex(terminal_snapshot)
        context = {
            "receipt_kind": "evidence_graph_design_validation_context",
            "graph_digest": graph["graph_digest"], "plan_definition": copy.deepcopy(design_definition),
            "investigation_snapshot": terminal_snapshot,
        }
        context["receipt_digest"] = digest_hex(context)
        self.store.put_json("graph-validation-context-candidate", context)
        validate_evidence_references(graph)
        if self.dispatcher is not None:
            self.dispatcher.record_control_execution(
                "GATE-04",
                {"subject_digest": graph["graph_digest"]},
                {"gate_id": "GATE-04", "status": "passed", "subject_digest": graph["graph_digest"]},
            )
        self.store.put_json("receipt-candidate", commit_receipt)
        self.validate_trusted_closure(graph)
        self.store.put_json("evidence-graph-candidate", graph)
        return graph


__all__ = ["EvidenceGraphManager", "W5EvidenceGraphError", "validate_evidence_references"]
