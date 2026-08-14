"""P2-S1 W5 本地隔离组合调查 API 薄适配层。

本模块只负责 HTTP 输入边界、受信 principal、幂等/CAS 字段和安全下载。
调查状态机、所有权、Registry admission、Tool/Operator 执行及制品提交全部由
``CountryOutageP2S1InvestigationRuntime`` 实现；handler 不计算业务事实，也不
对 ResultSet 成员执行隐式 fan-out。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from hmac import compare_digest
from typing import Any, Protocol, runtime_checkable

from flask import Response, jsonify, request
from flask_restful import Resource

from .country_outage_agent_proxy import (
    _error,
    _principal,
    _read_json_body,
    _safe_identifier,
)


_COUNTRY_OUTAGE_REFERENCE = re.compile(
    r"^country_outage/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}/"
    r"[A-Z]{2}/[1-9]\d*/r$"
)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_SELECTION_REF = re.compile(r"^[A-Za-z0-9_~./:@+-]{1,512}$")
_EXPORT_FORMATS = {"csv", "json", "markdown"}
_RECEIPT_KINDS = {"tool", "operator", "model", "gate", "cost", "latency", "reuse", "transaction"}
_ARTIFACT_MAX_BYTES = 64 * 1024 * 1024


@runtime_checkable
class CountryOutageP2S1InvestigationRuntime(Protocol):
    """API 所需的最小 runtime 端口；具体实现由 service 层注入。"""

    def create_investigation(self, principal: Mapping[str, str], body: Mapping[str, Any]): ...
    def get_investigation(self, principal: Mapping[str, str], investigation_id: str): ...
    def start_investigation(self, principal: Mapping[str, str], investigation_id: str, body: Mapping[str, Any]): ...
    def cancel_investigation(self, principal: Mapping[str, str], investigation_id: str, body: Mapping[str, Any]): ...
    def cancel_node(self, principal: Mapping[str, str], investigation_id: str, node_id: str, body: Mapping[str, Any]): ...
    def rerun_node(self, principal: Mapping[str, str], investigation_id: str, node_id: str, body: Mapping[str, Any]): ...
    def create_turn(self, principal: Mapping[str, str], investigation_id: str, body: Mapping[str, Any]): ...
    def get_turn(self, principal: Mapping[str, str], investigation_id: str, turn_id: str, turn_revision: int): ...
    def get_result_set(self, principal: Mapping[str, str], investigation_id: str, result_set_id: str, result_set_revision: int, query: Mapping[str, Any]): ...
    def get_evidence_graph(self, principal: Mapping[str, str], investigation_id: str, graph_revision: int): ...
    def get_receipts(self, principal: Mapping[str, str], investigation_id: str, query: Mapping[str, Any]): ...
    def create_export(self, principal: Mapping[str, str], investigation_id: str, body: Mapping[str, Any]): ...
    def get_export(self, principal: Mapping[str, str], investigation_id: str, export_id: str): ...
    def get_export_artifact(self, principal: Mapping[str, str], investigation_id: str, export_id: str): ...


RuntimeFactory = Callable[[], CountryOutageP2S1InvestigationRuntime]
_runtime_factory: RuntimeFactory | None = None


def configure_country_outage_p2_s1_investigation_runtime(
    factory: RuntimeFactory | None,
) -> None:
    """配置 W5 runtime 工厂；测试和应用启动代码可显式注入。"""

    global _runtime_factory
    _runtime_factory = factory


def _runtime() -> CountryOutageP2S1InvestigationRuntime:
    if _runtime_factory is not None:
        return _runtime_factory()
    try:
        from services.country_outage_p2_s1_investigation_runtime import (
            get_country_outage_p2_s1_investigation_runtime,
        )
    except (ImportError, ModuleNotFoundError) as error:
        raise RuntimeError("P2-S1 W5 Investigation Runtime 尚未配置") from error
    return get_country_outage_p2_s1_investigation_runtime()


def _request_principal() -> dict[str, str]:
    user_id, authorization_scope = _principal()
    return {
        "user_id": user_id,
        "authorization_scope": authorization_scope,
    }


def _exact_keys(value: Mapping[str, Any], required: set[str], optional: set[str] | None = None) -> None:
    optional = optional or set()
    keys = set(value)
    if keys != required | (keys & optional):
        missing = sorted(required - keys)
        extra = sorted(keys - required - optional)
        detail = []
        if missing:
            detail.append(f"缺少字段：{', '.join(missing)}")
        if extra:
            detail.append(f"包含未授权字段：{', '.join(extra)}")
        raise ValueError("；".join(detail) or "请求字段不合法")


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field} 必须是正整数")
    return value


def _safe_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 不能为空")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{field} 超过 {maximum} 字符")
    return normalized


def _idempotency(value: Any) -> str:
    if not isinstance(value, str) or not _IDEMPOTENCY_KEY.fullmatch(value):
        raise ValueError("idempotency_key 必须是 8 至 128 位安全字符")
    header = request.headers.get("Idempotency-Key")
    if header is None or not _IDEMPOTENCY_KEY.fullmatch(header) or not compare_digest(header, value):
        raise ValueError("必须提供与请求体一致的 Idempotency-Key Header")
    return value


def _cas_body(value: dict[str, Any], *, extra_required: set[str] | None = None) -> dict[str, Any]:
    extras = extra_required or set()
    _exact_keys(
        value,
        {"idempotency_key", "expected_investigation_revision", "expected_current_digest"} | extras,
    )
    _idempotency(value["idempotency_key"])
    _positive_int(value["expected_investigation_revision"], "expected_investigation_revision")
    digest = value["expected_current_digest"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError("expected_current_digest 必须是 sha256 摘要")
    return value


def _create_body(value: dict[str, Any]) -> dict[str, Any]:
    _exact_keys(
        value,
        {"event_reference", "publication_id", "revision", "goal", "idempotency_key"},
    )
    if not isinstance(value["event_reference"], str) or not _COUNTRY_OUTAGE_REFERENCE.fullmatch(value["event_reference"]):
        raise ValueError("event_reference 不是合法 country_outage 引用")
    _safe_text(value["publication_id"], "publication_id", 256)
    _positive_int(value["revision"], "revision")
    value["goal"] = _safe_text(value["goal"], "goal", 4_000)
    _idempotency(value["idempotency_key"])
    return value


def _turn_body(value: dict[str, Any]) -> dict[str, Any]:
    _cas_body(value, extra_required={"question", "anchor"})
    value["question"] = _safe_text(value["question"], "question", 4_000)
    anchor = value["anchor"]
    if not isinstance(anchor, dict):
        raise ValueError("anchor 必须是对象")
    _exact_keys(anchor, {"node_id", "node_revision"}, {"selection_ref"})
    anchor["node_id"] = _safe_identifier(anchor["node_id"], "anchor.node_id")
    _positive_int(anchor["node_revision"], "anchor.node_revision")
    selection_ref = anchor.get("selection_ref")
    if selection_ref is not None and (
        not isinstance(selection_ref, str)
        or not _SAFE_SELECTION_REF.fullmatch(selection_ref)
    ):
        raise ValueError("anchor.selection_ref 不是安全的已提交选择引用")
    return value


def _export_body(value: dict[str, Any]) -> dict[str, Any]:
    _cas_body(
        value,
        extra_required={"result_set_id", "result_set_revision", "format"},
    )
    value["result_set_id"] = _safe_identifier(value["result_set_id"], "result_set_id")
    _positive_int(value["result_set_revision"], "result_set_revision")
    if value["format"] not in _EXPORT_FORMATS:
        raise ValueError("format 只允许 csv、json 或 markdown")
    return value


def _runtime_error(error: Exception):
    status = getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    retryable = bool(getattr(error, "retryable", False))
    next_action = getattr(error, "next_action", None)
    if isinstance(status, int) and 400 <= status <= 599 and isinstance(code, str):
        return _error(status, code, str(error), retryable=retryable, next_action=next_action)
    if isinstance(error, PermissionError):
        return _error(403, "investigation_forbidden", "无权访问该调查")
    if isinstance(error, LookupError):
        return _error(404, "investigation_not_found", "调查或制品不存在")
    return _error(
        503,
        "investigation_runtime_unavailable",
        "本地隔离 Investigation Runtime 暂不可用",
        retryable=True,
        next_action="确认本地 W5 runtime 已启动并完成恢复后重试",
    )


def _project_investigation(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    public = {
        key: value[key]
        for key in (
            "investigation_id", "investigation_revision", "parent_investigation_revision",
            "status", "current_digest", "identity", "limitations",
            "evidence_graph_revision", "local_execution", "production_deployed",
        )
        if key in value
    }
    plan = value.get("plan")
    if isinstance(plan, Mapping):
        public_plan = {
            key: plan[key]
            for key in (
                "schema_version", "plan_id", "plan_revision", "plan_state", "goal",
                "identity_digest", "registry_snapshot_id", "registry_snapshot_digest",
                "plan_digest",
            )
            if key in plan
        }
        nodes = plan.get("nodes")
        if isinstance(nodes, list):
            plan_fields = ("node_id", "unit_id", "requiredness", "depends_on", "dependency_mode")
            public_plan["nodes"] = [
                {key: node[key] for key in plan_fields if key in node}
                for node in nodes
                if isinstance(node, Mapping)
            ]
        public["plan"] = public_plan
    nodes = value.get("nodes")
    if isinstance(nodes, list):
        node_fields = (
            "node_id", "unit_id", "execution_revision", "state", "failure_code",
            "receipt_digest",
        )
        public_nodes = []
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            public_node = {key: node[key] for key in node_fields if key in node}
            refs = node.get("result_set_refs")
            if isinstance(refs, list):
                public_node["result_set_refs"] = [
                    {
                        key: ref[key]
                        for key in ("result_set_id", "result_set_revision")
                        if key in ref
                    }
                    for ref in refs
                    if isinstance(ref, Mapping)
                ]
            public_nodes.append(public_node)
        public["nodes"] = public_nodes
    turn_refs = value.get("turn_refs")
    if isinstance(turn_refs, list):
        public["turn_refs"] = [
            {key: ref[key] for key in ("turn_id", "turn_revision", "answer_id", "answer_revision") if key in ref}
            for ref in turn_refs if isinstance(ref, Mapping)
        ]
    return public


def _project_envelope(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return payload
    projected = {
        key: payload[key]
        for key in ("accepted", "deduplicated", "export")
        if key in payload
    }
    if "investigation" in payload:
        projected["investigation"] = _project_investigation(payload["investigation"])
    return projected


def _project_action(payload: Any) -> Any:
    """公开版本化回答安全投影；内部模型响应与执行对象不透传。"""

    if not isinstance(payload, Mapping):
        return payload
    projected = _project_envelope(payload)
    if "turn" in payload:
        projected["turn"] = _project_turn(payload["turn"])
    return projected


def _project_turn(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    fields = (
        "turn_id", "turn_revision", "investigation_id", "source_investigation_revision",
        "source_current_digest", "committed_investigation_revision", "question", "anchor",
        "turn_digest",
    )
    projected = {key: value[key] for key in fields if key in value}
    answer = value.get("answer")
    if isinstance(answer, Mapping):
        answer_fields = (
            "answer_id", "answer_revision", "answer_digest", "shared_answer_binding_digest",
            "claims", "limitations", "unknowns", "unknown_ids", "evidence_refs", "answer_text", "plan_ref",
            "result_set_refs", "evidence_graph_ref", "model_receipt_digests",
            "gate_receipt_digests", "fixture_boundary",
        )
        public_answer = {key: answer[key] for key in answer_fields if key in answer}
        claims = answer.get("claims")
        if isinstance(claims, list):
            claim_fields = (
                "claim_id", "claim_kind", "claim_relation", "text", "fact_ids",
                "source_node_ids", "source_value_digests", "evidence_refs",
                "boundary_assertion_ids", "verification_requirements",
            )
            public_answer["claims"] = [
                {key: claim[key] for key in claim_fields if key in claim}
                for claim in claims if isinstance(claim, Mapping)
            ]
        projected["answer"] = public_answer
    return projected


def _project_evidence_graph(payload: Any) -> Any:
    """公开 Graph 的可验证拓扑，隐藏未版本化的内部 payload。"""

    if not isinstance(payload, Mapping):
        return payload
    projected = {
        key: payload[key]
        for key in (
            "schema_version", "graph_id", "graph_revision", "parent_graph_revision",
            "graph_state", "investigation_id", "investigation_revision", "plan_id",
            "plan_revision", "plan_digest", "identity", "edges", "root_node_ids",
            "runtime_boundary", "content_digest", "commit_receipt_digest", "graph_digest",
        )
        if key in payload
    }
    nodes = payload.get("nodes")
    if isinstance(nodes, list):
        node_fields = (
            "node_id", "node_type", "producer_plan_node_id", "identity_digest",
            "payload_digest", "evidence_refs", "producer_receipt_digest",
        )
        projected["nodes"] = [
            {key: node[key] for key in node_fields if key in node}
            for node in nodes
            if isinstance(node, Mapping)
        ]
    return projected


def _project_receipt_page(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return payload
    allowed = (
        "receipt_kind", "receipt_digest", "investigation_id", "node_id", "unit_id",
        "state", "result_digest", "error_digest", "result_set_id",
        "result_set_revision", "manifest_digest", "content_digest", "returned_count",
        "total_count", "graph_id", "graph_revision", "node_count", "edge_count",
        "export_id", "artifact_sha256", "disposition", "owner", "action",
        "receipt_version", "investigation_revision", "owner_principal_id",
        "answer_payload_digest", "plan_digest", "result_set_refs_digest",
        "graph_digest", "shared_answer_binding_digest", "binding_generation",
        "model_role", "model_identity", "gate_id", "source_sidecar_receipt_digest",
        "source_composition_receipt_digest",
        "external_provider_called", "fixture_replay_only",
    )
    receipts = payload.get("receipts")
    return {
        "receipts": [
            {key: receipt[key] for key in allowed if key in receipt}
            for receipt in receipts
            if isinstance(receipt, Mapping)
        ] if isinstance(receipts, list) else [],
        "next_cursor": payload.get("next_cursor"),
    }


def _call(
    method: str,
    *args: Any,
    success_status: int = 200,
    projector: Callable[[Any], Any] | None = None,
):
    try:
        result = getattr(_runtime(), method)(_request_principal(), *args)
    except PermissionError as error:
        # _principal 的认证缺失与 runtime 的所有权拒绝分别映射为 401/403。
        if "Domeye 控制面认证" in str(error):
            return _error(401, "authentication_required", str(error))
        return _runtime_error(error)
    except Exception as error:  # runtime domain error 通过闭合属性映射。
        return _runtime_error(error)
    status = success_status
    payload = result
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], int):
        payload, status = result
    if projector is not None:
        payload = projector(payload)
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


class CountryOutageInvestigationCollectionResource(Resource):
    def post(self):
        try:
            body = _create_body(_read_json_body())
        except ValueError as error:
            return _error(400, "invalid_investigation_request", str(error))
        return _call(
            "create_investigation",
            body,
            success_status=201,
            projector=_project_envelope,
        )


class CountryOutageInvestigationResource(Resource):
    def get(self, investigation_id: str):
        try:
            investigation_id = _safe_identifier(investigation_id, "investigation_id")
        except ValueError as error:
            return _error(400, "invalid_investigation_request", str(error))
        return _call("get_investigation", investigation_id, projector=_project_envelope)


class _InvestigationCasActionResource(Resource):
    runtime_method = ""

    def post(self, investigation_id: str):
        try:
            investigation_id = _safe_identifier(investigation_id, "investigation_id")
            body = _cas_body(_read_json_body())
        except ValueError as error:
            return _error(400, "invalid_investigation_request", str(error))
        return _call(
            self.runtime_method,
            investigation_id,
            body,
            success_status=202,
            projector=_project_action,
        )


class CountryOutageInvestigationStartResource(_InvestigationCasActionResource):
    runtime_method = "start_investigation"


class CountryOutageInvestigationCancelResource(_InvestigationCasActionResource):
    runtime_method = "cancel_investigation"


class _InvestigationNodeCasActionResource(Resource):
    runtime_method = ""

    def post(self, investigation_id: str, node_id: str):
        try:
            investigation_id = _safe_identifier(investigation_id, "investigation_id")
            node_id = _safe_identifier(node_id, "node_id")
            body = _cas_body(_read_json_body())
        except ValueError as error:
            return _error(400, "invalid_investigation_request", str(error))
        return _call(
            self.runtime_method,
            investigation_id,
            node_id,
            body,
            success_status=202,
            projector=_project_action,
        )


class CountryOutageInvestigationNodeCancelResource(_InvestigationNodeCasActionResource):
    runtime_method = "cancel_node"


class CountryOutageInvestigationNodeRerunResource(_InvestigationNodeCasActionResource):
    runtime_method = "rerun_node"


class CountryOutageInvestigationTurnCollectionResource(Resource):
    def post(self, investigation_id: str):
        try:
            investigation_id = _safe_identifier(investigation_id, "investigation_id")
            body = _turn_body(_read_json_body())
        except ValueError as error:
            return _error(400, "invalid_investigation_request", str(error))
        return _call(
            "create_turn",
            investigation_id,
            body,
            success_status=202,
            projector=_project_action,
        )


class CountryOutageInvestigationTurnResource(Resource):
    def get(self, investigation_id: str, turn_id: str, turn_revision: int):
        try:
            investigation_id = _safe_identifier(investigation_id, "investigation_id")
            turn_id = _safe_identifier(turn_id, "turn_id")
            turn_revision = _positive_int(turn_revision, "turn_revision")
        except ValueError as error:
            return _error(400, "invalid_investigation_request", str(error))
        return _call(
            "get_turn", investigation_id, turn_id, turn_revision,
            projector=lambda payload: {"turn": _project_turn(payload["turn"])} if isinstance(payload, Mapping) and "turn" in payload else payload,
        )


class CountryOutageInvestigationResultSetResource(Resource):
    def get(self, investigation_id: str, result_set_id: str, result_set_revision: int):
        try:
            investigation_id = _safe_identifier(investigation_id, "investigation_id")
            result_set_id = _safe_identifier(result_set_id, "result_set_id")
            result_set_revision = _positive_int(result_set_revision, "result_set_revision")
            allowed = {"page_token", "page_size"}
            if set(request.args) - allowed:
                raise ValueError("ResultSet 查询包含未授权参数")
            page_token = request.args.get("page_token")
            if page_token is not None and (
                not page_token or len(page_token) > 1024 or any(ord(char) < 32 for char in page_token)
            ):
                raise ValueError("page_token 不合法")
            raw_page_size = request.args.get("page_size", "50")
            try:
                page_size = int(raw_page_size)
            except ValueError as error:
                raise ValueError("page_size 必须是整数") from error
            if page_size < 1 or page_size > 200:
                raise ValueError("page_size 必须在 1 至 200 之间")
            query = {"page_size": page_size, "page_token": page_token}
        except ValueError as error:
            return _error(400, "invalid_investigation_request", str(error))
        return _call(
            "get_result_set",
            investigation_id,
            result_set_id,
            result_set_revision,
            query,
        )


class CountryOutageInvestigationEvidenceGraphResource(Resource):
    def get(self, investigation_id: str, graph_revision: int):
        try:
            investigation_id = _safe_identifier(investigation_id, "investigation_id")
            graph_revision = _positive_int(graph_revision, "graph_revision")
        except ValueError as error:
            return _error(400, "invalid_investigation_request", str(error))
        return _call(
            "get_evidence_graph",
            investigation_id,
            graph_revision,
            projector=_project_evidence_graph,
        )


class CountryOutageInvestigationReceiptCollectionResource(Resource):
    def get(self, investigation_id: str):
        try:
            investigation_id = _safe_identifier(investigation_id, "investigation_id")
            if set(request.args) - {"kind", "cursor"}:
                raise ValueError("回执查询包含未授权参数")
            kind = request.args.get("kind")
            if kind is not None and kind not in _RECEIPT_KINDS:
                raise ValueError("kind 不是允许的回执类型")
            cursor = request.args.get("cursor")
            if cursor is not None and (not cursor or len(cursor) > 512):
                raise ValueError("cursor 不合法")
            query = {"kind": kind, "cursor": cursor}
        except ValueError as error:
            return _error(400, "invalid_investigation_request", str(error))
        return _call(
            "get_receipts",
            investigation_id,
            query,
            projector=_project_receipt_page,
        )


class CountryOutageInvestigationExportCollectionResource(Resource):
    def post(self, investigation_id: str):
        try:
            investigation_id = _safe_identifier(investigation_id, "investigation_id")
            body = _export_body(_read_json_body())
        except ValueError as error:
            return _error(400, "invalid_investigation_request", str(error))
        return _call(
            "create_export",
            investigation_id,
            body,
            success_status=202,
            projector=_project_action,
        )


class CountryOutageInvestigationExportResource(Resource):
    def get(self, investigation_id: str, export_id: str):
        try:
            investigation_id = _safe_identifier(investigation_id, "investigation_id")
            export_id = _safe_identifier(export_id, "export_id")
        except ValueError as error:
            return _error(400, "invalid_investigation_request", str(error))
        return _call("get_export", investigation_id, export_id)


class CountryOutageInvestigationExportArtifactResource(Resource):
    def get(self, investigation_id: str, export_id: str):
        try:
            investigation_id = _safe_identifier(investigation_id, "investigation_id")
            export_id = _safe_identifier(export_id, "export_id")
            artifact = _runtime().get_export_artifact(
                _request_principal(), investigation_id, export_id
            )
            if not isinstance(artifact, Mapping):
                raise RuntimeError("runtime 返回了无效导出制品")
            content = artifact.get("content")
            content_type = artifact.get("content_type")
            filename = artifact.get("filename")
            digest = artifact.get("sha256")
            if not isinstance(content, bytes) or len(content) > _ARTIFACT_MAX_BYTES:
                raise RuntimeError("导出制品字节无效或超过 64 MiB")
            if content_type not in {
                "text/csv; charset=utf-8",
                "application/json",
                "text/markdown; charset=utf-8",
            }:
                raise RuntimeError("导出制品 Content-Type 不在冻结 allowlist")
            if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", filename):
                raise RuntimeError("导出制品文件名不安全")
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise RuntimeError("导出制品缺少合法摘要")
        except PermissionError as error:
            if "Domeye 控制面认证" in str(error):
                return _error(401, "authentication_required", str(error))
            return _runtime_error(error)
        except Exception as error:
            return _runtime_error(error)

        response = Response(content, status=200, content_type=content_type)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.headers["X-Content-SHA256"] = digest
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
