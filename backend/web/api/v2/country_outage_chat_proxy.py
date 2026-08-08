"""P1 国家中断事件绑定聊天的同源窄代理。"""

from __future__ import annotations

from collections.abc import Iterator

import requests
from flask import Response, stream_with_context
from flask_restful import Resource

from . import country_outage_agent_proxy as agent_proxy


def _validate_conversation_request(value: dict) -> None:
    allowed = {
        "event_reference",
        "publication_id",
        "revision",
        "idempotency_key",
    }
    if set(value) - allowed:
        raise ValueError("会话请求包含未授权字段")
    reference = value.get("event_reference")
    if not isinstance(reference, str) or not agent_proxy._COUNTRY_OUTAGE_REFERENCE.fullmatch(
        reference
    ):
        raise ValueError("event_reference 不是合法 country_outage 引用")
    publication_id = value.get("publication_id")
    if not isinstance(publication_id, str) or not publication_id or len(publication_id) > 256:
        raise ValueError("publication_id 必须是非空字符串")
    revision = value.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("revision 必须是正整数")
    key = value.get("idempotency_key")
    if not isinstance(key, str) or not agent_proxy._IDEMPOTENCY_KEY.fullmatch(key):
        raise ValueError("idempotency_key 必须是 8 至 128 位安全字符")
    agent_proxy._validate_idempotency_header(key)


def _validate_turn_request(value: dict) -> None:
    if set(value) - {"question", "idempotency_key"}:
        raise ValueError("轮次请求包含未授权字段")
    question = value.get("question")
    if not isinstance(question, str) or not question.strip() or len(question) > 2_000:
        raise ValueError("question 必须是 1 至 2,000 字符的非空文本")
    key = value.get("idempotency_key")
    if not isinstance(key, str) or not agent_proxy._IDEMPOTENCY_KEY.fullmatch(key):
        raise ValueError("idempotency_key 必须是 8 至 128 位安全字符")
    agent_proxy._validate_idempotency_header(key)


def _empty_body() -> dict:
    value = agent_proxy._read_json_body()
    if value:
        raise ValueError("取消请求不接受额外字段")
    return value


class CountryOutageChatConversationCollectionResource(Resource):
    def post(self):
        try:
            body = agent_proxy._read_json_body()
            _validate_conversation_request(body)
        except ValueError as error:
            return agent_proxy._error(400, "invalid_chat_request", str(error))
        return agent_proxy._json_proxy("POST", "/country-outage/chat/conversations", body)


class CountryOutageChatConversationResource(Resource):
    def get(self, conversation_id: str):
        try:
            conversation_id = agent_proxy._safe_identifier(
                conversation_id, "conversation_id"
            )
        except ValueError as error:
            return agent_proxy._error(400, "invalid_chat_request", str(error))
        return agent_proxy._json_proxy(
            "GET", f"/country-outage/chat/conversations/{conversation_id}"
        )


class CountryOutageChatTurnCollectionResource(Resource):
    def post(self, conversation_id: str):
        try:
            conversation_id = agent_proxy._safe_identifier(
                conversation_id, "conversation_id"
            )
            body = agent_proxy._read_json_body()
            _validate_turn_request(body)
        except ValueError as error:
            return agent_proxy._error(400, "invalid_chat_request", str(error))
        return agent_proxy._json_proxy(
            "POST",
            f"/country-outage/chat/conversations/{conversation_id}/turns",
            body,
        )


class CountryOutageChatTurnCancelResource(Resource):
    def post(self, conversation_id: str, turn_id: str):
        try:
            conversation_id = agent_proxy._safe_identifier(
                conversation_id, "conversation_id"
            )
            turn_id = agent_proxy._safe_identifier(turn_id, "turn_id")
            body = _empty_body()
        except ValueError as error:
            return agent_proxy._error(400, "invalid_chat_request", str(error))
        return agent_proxy._json_proxy(
            "POST",
            (
                f"/country-outage/chat/conversations/{conversation_id}/turns/"
                f"{turn_id}/cancel"
            ),
            body,
        )


class CountryOutageChatRebindResource(Resource):
    def post(self, conversation_id: str):
        try:
            conversation_id = agent_proxy._safe_identifier(
                conversation_id, "conversation_id"
            )
            body = agent_proxy._read_json_body()
            _validate_conversation_request(body)
        except ValueError as error:
            return agent_proxy._error(400, "invalid_chat_request", str(error))
        return agent_proxy._json_proxy(
            "POST",
            f"/country-outage/chat/conversations/{conversation_id}/rebind",
            body,
        )


class CountryOutageChatEventResource(Resource):
    def get(self, conversation_id: str):
        try:
            conversation_id = agent_proxy._safe_identifier(
                conversation_id, "conversation_id"
            )
            upstream = agent_proxy._request_sidecar(
                "GET",
                f"/country-outage/chat/conversations/{conversation_id}/events",
                headers=agent_proxy._request_headers(accept="text/event-stream"),
                stream=True,
            )
        except ValueError as error:
            return agent_proxy._error(400, "invalid_chat_request", str(error))
        except PermissionError as error:
            return agent_proxy._error(401, "authentication_required", str(error))
        except (requests.RequestException, RuntimeError) as error:
            return agent_proxy._error(
                503,
                "agent_unavailable",
                str(error),
                retryable=True,
                next_action="确认本机 Sidecar 配置与运行状态后重连",
            )

        if upstream.status_code >= 400:
            content = upstream.content[: agent_proxy._JSON_RESPONSE_MAX_BYTES]
            upstream.close()
            return Response(
                content,
                status=upstream.status_code,
                content_type=upstream.headers.get("Content-Type", "application/json"),
                headers={"Cache-Control": "no-store"},
            )

        def generate() -> Iterator[bytes]:
            try:
                for chunk in upstream.iter_content(chunk_size=16 * 1024):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        response = Response(
            stream_with_context(generate()),
            status=upstream.status_code,
            content_type="text/event-stream; charset=utf-8",
        )
        response.headers["Cache-Control"] = "no-cache, no-store"
        response.headers["X-Accel-Buffering"] = "no"
        return response
