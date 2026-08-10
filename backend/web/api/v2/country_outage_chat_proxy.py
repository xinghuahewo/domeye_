"""P1 页面能力聊天的窄只读控制面代理。"""

from __future__ import annotations

import re

from flask_restful import Resource

from .country_outage_agent_proxy import (
    _error,
    _json_proxy,
    _read_json_body,
    _safe_identifier,
    _validate_idempotency_header,
)


_COUNTRY_OUTAGE_REFERENCE = re.compile(
    r"^country_outage/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}/"
    r"[A-Z]{2}/[1-9]\d*/r$"
)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


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
        return _json_proxy("POST", "/country-outage/chat/conversations", body)


class CountryOutageChatConversationResource(Resource):
    def get(self, conversation_id: str):
        try:
            conversation_id = _safe_identifier(
                conversation_id, "conversation_id"
            )
        except ValueError as error:
            return _error(400, "invalid_chat_request", str(error))
        return _json_proxy(
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
        return _json_proxy(
            "POST",
            f"/country-outage/chat/conversations/{conversation_id}/turns",
            body,
        )


class CountryOutageChatRebindResource(Resource):
    def post(self, conversation_id: str):
        try:
            conversation_id = _safe_identifier(
                conversation_id, "conversation_id"
            )
            body = _binding_request(_read_json_body())
        except ValueError as error:
            return _error(400, "invalid_chat_request", str(error))
        return _json_proxy(
            "POST",
            f"/country-outage/chat/conversations/{conversation_id}/rebind",
            body,
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
        return _json_proxy(
            "POST",
            (
                f"/country-outage/chat/conversations/{conversation_id}/turns/"
                f"{turn_id}/cancel"
            ),
            body,
        )
