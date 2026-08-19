"""首个纵向切片交互式 Agent 的窄只读控制面代理。"""

from __future__ import annotations

import os
import re
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
    method: str, path: str, body: dict | None = None
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
    except PermissionError as error:
        return _error(401, "authentication_required", str(error))
    except (requests.RequestException, RuntimeError) as error:
        return _error(
            503,
            "interactive_agent_unavailable",
            str(error),
            retryable=True,
            next_action="确认本机交互式 Agent Sidecar 配置与运行状态后重试",
        )
    content = upstream.content
    if len(content) > _JSON_RESPONSE_MAX_BYTES:
        return _error(
            502,
            "interactive_agent_response_too_large",
            "交互式 Agent 响应超过 2 MiB",
        )
    response = Response(
        content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json"),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


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
            "POST", "/country-outage/chat/conversations", body
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
            "GET", f"/country-outage/chat/conversations/{conversation_id}"
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
        )


class CountryOutageChatRebindResource(Resource):
    def post(self, conversation_id: str):
        del conversation_id
        return _error(
            404,
            "route_not_found",
            "会话重新绑定已移除；数据身份变化时必须新建会话",
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
        )
