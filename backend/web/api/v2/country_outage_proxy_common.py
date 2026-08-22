"""国家中断交互式 Agent 控制面代理的共享安全边界。"""

from __future__ import annotations

import json
import os
import re
from hmac import compare_digest

from flask import jsonify, request


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_JSON_REQUEST_MAX_BYTES = 64 * 1024


def _error(
    status: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    next_action: str | None = None,
):
    detail = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if next_action:
        detail["next_action"] = next_action
    response = jsonify({"error": detail})
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _internal_token() -> str:
    value = os.environ.get("COUNTRY_OUTAGE_AGENT_SHARED_TOKEN", "").strip()
    if len(value) < 24:
        raise RuntimeError("国家中断 Agent 内部凭据尚未安全配置")
    return value


def _principal() -> tuple[str, str]:
    user_id = request.environ.get("domeye.authenticated_user_id")
    authorization_scope = request.environ.get("domeye.authorization_scope")
    if isinstance(user_id, str) and isinstance(authorization_scope, str):
        user_id = user_id.strip()
        authorization_scope = authorization_scope.strip()
        if user_id and authorization_scope:
            return user_id[:256], authorization_scope[:512]
    raise PermissionError("需要经过 Domeye 控制面认证后访问回答服务")


def _request_headers(
    *,
    accept: str,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    user_id, authorization_scope = _principal()
    headers = {
        "Accept": accept,
        "Authorization": f"Bearer {_internal_token()}",
        "X-Domeye-User": user_id,
        "X-Domeye-Authorization-Scope": authorization_scope,
    }
    content_type = request.headers.get("Content-Type")
    if content_type:
        headers["Content-Type"] = content_type
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id[:128]
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _read_json_body() -> dict:
    content_length = request.content_length
    if content_length is not None and content_length > _JSON_REQUEST_MAX_BYTES:
        raise ValueError("请求正文超过 64 KiB")
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("请求正文必须是 JSON 对象")
    encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
    if len(encoded) > _JSON_REQUEST_MAX_BYTES:
        raise ValueError("请求正文超过 64 KiB")
    return value


def _validate_idempotency_header(body_value: str) -> None:
    header_value = request.headers.get("Idempotency-Key")
    if header_value is not None:
        if (
            not _IDEMPOTENCY_KEY.fullmatch(header_value)
            or not compare_digest(header_value, body_value)
        ):
            raise ValueError("Header 与请求体的 idempotency_key 不一致")


def _safe_identifier(value: str, label: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} 无效")
    return value
