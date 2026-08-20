"""首个纵向切片交互式 Agent 的窄只读控制面代理。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlparse

import requests
from flask import Response
from flask_restful import Resource

from .country_outage_agent_proxy import (
    _error,
    _read_json_body,
    _request_headers,
    _safe_identifier,
    _validate_idempotency_header,
)


_COUNTRY_OUTAGE_REFERENCE = re.compile(
    r"^country_outage/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}/"
    r"[A-Z]{2}/[1-9]\d*/r$"
)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_JSON_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
_CONNECT_TIMEOUT_SECONDS = 3
_READ_TIMEOUT_SECONDS = 120

_INTERACTIVE_AGENT_HTTP = requests.Session()
_INTERACTIVE_AGENT_HTTP.trust_env = False

_PUBLIC_ERROR_POLICY = {
    "permission_denied": (403, "无权访问该会话", False),
    "conversation_not_found": (404, "会话不存在", False),
    "conversation_expired": (410, "会话已过期", False),
    "conversation_busy": (409, "当前会话已有问题正在分析", True),
    "idempotency_conflict": (409, "当前请求与已有请求冲突", False),
    "invalid_request": (400, "当前请求超出此回答功能支持的范围", False),
    "data_not_available": (409, "当前数据无法用于本次回答", False),
    "request_cancelled": (409, "请求已取消", False),
    "service_temporarily_unavailable": (
        503,
        "数据暂时不可用，请稍后重试",
        True,
    ),
}

_PUBLIC_TURN_ERROR_POLICY = {
    "answer_temporarily_unavailable": (
        "这次没有形成可靠答案，临时服务异常。请稍后重试。",
        True,
    ),
    "answer_not_published": (
        "本轮未通过回答合同或安全校验，没有发布答案。",
        False,
    ),
    "cancelled": ("本轮已取消，未发布答案", False),
}


def _interactive_agent_base_url() -> str:
    raw = os.environ.get(
        "COUNTRY_OUTAGE_INTERACTIVE_AGENT_SIDECAR_URL", ""
    ).strip()
    if not raw:
        raise RuntimeError("国家中断交互式 Agent Sidecar 尚未配置")
    parsed = urlparse(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            "交互式 Agent Sidecar 地址必须是无凭据的本机 HTTP/HTTPS"
        )
    return raw.rstrip("/")


def _request_interactive_agent(method: str, path: str, **kwargs):
    return _INTERACTIVE_AGENT_HTTP.request(
        method,
        f"{_interactive_agent_base_url()}{path}",
        timeout=(_CONNECT_TIMEOUT_SECONDS, _READ_TIMEOUT_SECONDS),
        allow_redirects=False,
        **kwargs,
    )


def _json_interactive_agent_proxy(
    method: str,
    path: str,
    body: dict | None,
    *,
    success_statuses: frozenset[int],
    validate_success: Callable[[object], None],
):
    try:
        upstream = _request_interactive_agent(
            method,
            path,
            headers=_request_headers(
                accept="application/json",
                idempotency_key=(
                    body.get("idempotency_key")
                    if isinstance(body, dict)
                    and isinstance(body.get("idempotency_key"), str)
                    else None
                ),
            ),
            json=body,
        )
    except PermissionError:
        return _error(
            401,
            "authentication_required",
            "请登录后再使用回答服务",
        )
    except (requests.RequestException, RuntimeError):
        return _error(
            503,
            "interactive_agent_unavailable",
            "回答服务暂时不可用，请稍后重试",
            retryable=True,
            next_action="请稍后重试",
        )
    content = upstream.content
    if len(content) > _JSON_RESPONSE_MAX_BYTES:
        return _error(
            502,
            "interactive_agent_response_too_large",
            "回答服务返回异常，请稍后重试",
        )
    try:
        payload = json.loads(
            content,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        if upstream.status_code in success_statuses:
            validate_success(payload)
        elif upstream.status_code >= 400:
            detail = _validate_error_response(payload)
            return _public_upstream_error(detail)
        else:
            raise ValueError("上游状态码不符合公开合同")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return _error(
            502,
            "interactive_agent_contract_violation",
            "回答服务返回异常，请稍后重试",
        )
    response = Response(
        content,
        status=upstream.status_code,
        content_type="application/json",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _reject_json_constant(value: str):
    raise ValueError(f"JSON 常量不合法：{value}")


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON 对象包含重复字段")
        value[key] = item
    return value


def _exact_object(
    value: object,
    required: set[str],
    optional: set[str] | None = None,
) -> dict:
    if not isinstance(value, dict):
        raise ValueError("字段必须是对象")
    optional = optional or set()
    keys = set(value)
    if not required.issubset(keys) or keys - required - optional:
        raise ValueError("对象字段不符合公开合同")
    return value


def _text(value: object, *, maximum: int = 2_000) -> str:
    # strip 只用于拒绝空白字段；代理始终返回上游原始字节，不改写回答。
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
    ):
        raise ValueError("文本字段不符合公开合同")
    return value


def _positive_integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("整数值不符合公开合同")
    return value


def _date_time(value: object) -> str:
    text = _text(value, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("时间字段不符合公开合同") from error
    if parsed.tzinfo is None:
        raise ValueError("时间字段缺少时区")
    return text


def _validate_binding(value: object) -> None:
    fields = {
        "event_reference",
        "event_type",
        "incident_id",
        "publication_id",
        "revision",
        "collector_id",
        "cohort_id",
        "country_code",
        "window_start_utc",
        "window_end_utc",
        "data_through",
        "is_final_in_data_range",
        "lifecycle_state",
    }
    binding = _exact_object(value, fields)
    if not _COUNTRY_OUTAGE_REFERENCE.fullmatch(
        _text(binding["event_reference"], maximum=256)
    ):
        raise ValueError("事件引用不符合公开合同")
    if binding["event_type"] != "country_outage":
        raise ValueError("事件类型不符合公开合同")
    for key in ("incident_id", "publication_id", "cohort_id"):
        _text(binding[key], maximum=256)
    _positive_integer(binding["revision"])
    if binding["collector_id"] != "rrc25":
        raise ValueError("采集器不符合公开合同")
    if not isinstance(binding["country_code"], str) or not re.fullmatch(
        r"[A-Z]{2}", binding["country_code"]
    ):
        raise ValueError("国家代码不符合公开合同")
    for key in ("window_start_utc", "window_end_utc", "data_through"):
        _date_time(binding[key])
    if not isinstance(binding["is_final_in_data_range"], bool):
        raise ValueError("终态标记不符合公开合同")
    if binding["lifecycle_state"] != "event_end_unknown":
        raise ValueError("生命周期不符合公开合同")


def _validate_basis(value: object) -> None:
    basis = _exact_object(
        value,
        {
            "source_label_zh",
            "observed_object_zh",
            "window_start_utc",
            "window_end_utc",
            "important_boundary_zh",
        },
    )
    _text(basis["source_label_zh"], maximum=80)
    _text(basis["observed_object_zh"], maximum=160)
    _date_time(basis["window_start_utc"])
    _date_time(basis["window_end_utc"])
    _text(basis["important_boundary_zh"], maximum=240)


def _validate_answer(value: object, expected: str) -> None:
    common = {
        "schema_version",
        "answerability",
        "answer_source",
        "answer_text",
    }
    required = common | ({"basis"} if expected == "supported" else set())
    answer = _exact_object(value, required)
    if answer["schema_version"] != "domeye_interactive_agent_turn_answer_v2":
        raise ValueError("回答版本不符合公开合同")
    if answer["answerability"] != expected:
        raise ValueError("回答状态不符合公开合同")
    source = "renderer" if expected == "supported" else "none"
    if answer["answer_source"] != source:
        raise ValueError("回答来源不符合公开合同")
    _text(
        answer["answer_text"],
        maximum=360 if expected == "supported" else 140,
    )
    if expected == "supported":
        _validate_basis(answer["basis"])


def _validate_turn_error(value: object) -> None:
    error = _exact_object(value, {"code", "message", "retryable"})
    code = _text(error["code"], maximum=128)
    policy = _PUBLIC_TURN_ERROR_POLICY.get(code)
    if policy is None:
        raise ValueError("轮次错误类型不符合公开合同")
    message, retryable = policy
    if error["message"] != message or error["retryable"] is not retryable:
        raise ValueError("轮次错误内容不符合公开合同")


def _validate_turn(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("轮次必须是对象")
    state = value.get("state")
    common = {
        "turn_id",
        "turn_number",
        "question",
        "state",
        "answer_success",
        "workflow_completed",
        "created_at",
    }
    terminal = common | {"completed_at"}
    if state == "executing":
        turn = _exact_object(value, common)
    elif state == "completed":
        turn = _exact_object(value, terminal | {"answer"})
    elif state in {"clarification_required", "stopped"}:
        turn = _exact_object(value, terminal | {"answer"})
    elif state in {"failed", "cancelled"}:
        turn = _exact_object(value, terminal | {"error"})
    else:
        raise ValueError("轮次状态不符合公开合同")

    if not isinstance(turn["turn_id"], str) or not re.fullmatch(
        r"turn_sha256_[a-f0-9]{64}", turn["turn_id"]
    ):
        raise ValueError("轮次标识不符合公开合同")
    _positive_integer(turn["turn_number"])
    _text(turn["question"])
    _date_time(turn["created_at"])
    if "completed_at" in turn:
        _date_time(turn["completed_at"])

    completed = state == "completed"
    if turn["answer_success"] is not completed:
        raise ValueError("回答完成标记不符合公开合同")
    if turn["workflow_completed"] is not completed:
        raise ValueError("流程完成标记不符合公开合同")
    if state == "completed":
        _validate_answer(turn["answer"], "supported")
    elif state in {"clarification_required", "stopped"}:
        _validate_answer(turn["answer"], state)
    elif state in {"failed", "cancelled"}:
        _validate_turn_error(turn["error"])


def _validate_conversation(value: object) -> None:
    conversation = _exact_object(
        value,
        {
            "schema_version",
            "conversation_id",
            "binding",
            "turns",
            "expires_at",
            "created_at",
        },
    )
    if conversation["schema_version"] != "domeye_interactive_agent_conversation_v2":
        raise ValueError("会话版本不符合公开合同")
    if not isinstance(conversation["conversation_id"], str) or not re.fullmatch(
        r"conversation_sha256_[a-f0-9]{64}",
        conversation["conversation_id"],
    ):
        raise ValueError("会话标识不符合公开合同")
    _validate_binding(conversation["binding"])
    if not isinstance(conversation["turns"], list):
        raise ValueError("轮次列表不符合公开合同")
    for turn in conversation["turns"]:
        _validate_turn(turn)
    _date_time(conversation["expires_at"])
    _date_time(conversation["created_at"])


def _validate_conversation_create_response(value: object) -> None:
    response = _exact_object(value, {"conversation", "deduplicated"})
    _validate_conversation(response["conversation"])
    if not isinstance(response["deduplicated"], bool):
        raise ValueError("幂等标记不符合公开合同")


def _validate_conversation_get_response(value: object) -> None:
    response = _exact_object(value, {"conversation"})
    _validate_conversation(response["conversation"])


def _validate_turn_create_response(value: object) -> None:
    response = _exact_object(value, {"turn", "deduplicated"})
    _validate_turn(response["turn"])
    if not isinstance(response["deduplicated"], bool):
        raise ValueError("幂等标记不符合公开合同")


def _validate_cancel_response(value: object) -> None:
    response = _exact_object(value, {"turn_id", "state"})
    if not isinstance(response["turn_id"], str) or not re.fullmatch(
        r"turn_sha256_[a-f0-9]{64}", response["turn_id"]
    ):
        raise ValueError("轮次标识不符合公开合同")
    if response["state"] not in {"cancel_requested", "not_active"}:
        raise ValueError("取消状态不符合公开合同")


def _validate_error_response(value: object) -> dict:
    response = _exact_object(value, {"error"})
    error = _exact_object(
        response["error"],
        {"code", "message", "retryable"},
        {"next_action"},
    )
    _text(error["code"], maximum=128)
    _text(error["message"], maximum=1_000)
    if not isinstance(error["retryable"], bool):
        raise ValueError("重试标记不符合公开合同")
    if "next_action" in error:
        _text(error["next_action"], maximum=500)
    return error


def _public_upstream_error(detail: dict):
    policy = _PUBLIC_ERROR_POLICY.get(detail["code"])
    if policy is None:
        return _error(
            503,
            "interactive_agent_unavailable",
            "回答服务暂时不可用，请稍后重试",
            retryable=True,
            next_action="请稍后重试",
        )
    status, message, retryable = policy
    if detail["retryable"] is not retryable:
        raise ValueError("上游重试标记不符合公开合同")
    return _error(
        status,
        detail["code"],
        message,
        retryable=retryable,
    )


def _binding_request(value: dict) -> dict:
    allowed = {
        "event_reference",
        "publication_id",
        "revision",
        "idempotency_key",
    }
    if set(value) - allowed:
        raise ValueError("会话绑定请求包含未授权字段")
    reference = value.get("event_reference")
    publication_id = value.get("publication_id")
    revision = value.get("revision")
    key = value.get("idempotency_key")
    if not isinstance(reference, str) or not _COUNTRY_OUTAGE_REFERENCE.fullmatch(
        reference
    ):
        raise ValueError("event_reference 不是合法 country_outage 引用")
    if (
        not isinstance(publication_id, str)
        or not publication_id
        or len(publication_id) > 256
    ):
        raise ValueError("publication_id 必须是非空字符串")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("revision 必须是正整数")
    if not isinstance(key, str) or not _IDEMPOTENCY_KEY.fullmatch(key):
        raise ValueError("idempotency_key 必须是 8 至 128 位安全字符")
    _validate_idempotency_header(key)
    return value


def _turn_request(value: dict) -> dict:
    if set(value) - {"question", "idempotency_key"}:
        raise ValueError("问题请求包含未授权字段")
    question = value.get("question")
    key = value.get("idempotency_key")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question 不能为空")
    if len(question) > 2_000:
        raise ValueError("question 超过 2,000 字符")
    if not isinstance(key, str) or not _IDEMPOTENCY_KEY.fullmatch(key):
        raise ValueError("idempotency_key 必须是 8 至 128 位安全字符")
    _validate_idempotency_header(key)
    return value


class CountryOutageChatConversationCollectionResource(Resource):
    def post(self):
        try:
            body = _binding_request(_read_json_body())
        except ValueError as error:
            return _error(400, "invalid_chat_request", str(error))
        return _json_interactive_agent_proxy(
            "POST",
            "/country-outage/chat/conversations",
            body,
            success_statuses=frozenset({200, 201}),
            validate_success=_validate_conversation_create_response,
        )


class CountryOutageChatConversationResource(Resource):
    def get(self, conversation_id: str):
        try:
            conversation_id = _safe_identifier(
                conversation_id, "conversation_id"
            )
        except ValueError as error:
            return _error(400, "invalid_chat_request", str(error))
        return _json_interactive_agent_proxy(
            "GET",
            f"/country-outage/chat/conversations/{conversation_id}",
            None,
            success_statuses=frozenset({200}),
            validate_success=_validate_conversation_get_response,
        )


class CountryOutageChatTurnCollectionResource(Resource):
    def post(self, conversation_id: str):
        try:
            conversation_id = _safe_identifier(
                conversation_id, "conversation_id"
            )
            body = _turn_request(_read_json_body())
        except ValueError as error:
            return _error(400, "invalid_chat_request", str(error))
        return _json_interactive_agent_proxy(
            "POST",
            f"/country-outage/chat/conversations/{conversation_id}/turns",
            body,
            success_statuses=frozenset({200, 201}),
            validate_success=_validate_turn_create_response,
        )


class CountryOutageChatCancelResource(Resource):
    def post(self, conversation_id: str, turn_id: str):
        try:
            conversation_id = _safe_identifier(
                conversation_id, "conversation_id"
            )
            turn_id = _safe_identifier(turn_id, "turn_id")
            body = _read_json_body()
            if body:
                raise ValueError("取消请求不接受额外字段")
        except ValueError as error:
            return _error(400, "invalid_chat_request", str(error))
        return _json_interactive_agent_proxy(
            "POST",
            (
                f"/country-outage/chat/conversations/{conversation_id}/turns/"
                f"{turn_id}/cancel"
            ),
            body,
            success_statuses=frozenset({200}),
            validate_success=_validate_cancel_response,
        )
