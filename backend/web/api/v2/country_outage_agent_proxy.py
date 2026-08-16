"""国家中断 Agent Sidecar 的窄只读控制面代理。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from datetime import datetime
from hmac import compare_digest
from urllib.parse import urlparse

import requests
from flask import Response, jsonify, request, stream_with_context
from flask_restful import Resource


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_COUNTRY_OUTAGE_REFERENCE = re.compile(
    r"^country_outage/\d{4}-\d{2}-\d{2}[ +]\d{2}:\d{2}:\d{2}/"
    r"[A-Z]{2}/[1-9]\d*/r$"
)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SAFE_SECTION_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_EVIDENCE_REF = re.compile(
    r"^(?:[a-z][a-z0-9_]*:[A-Za-z0-9_~./:@+-]{1,480}|"
    r"fact_[a-f0-9]{16,64})$"
)
_JSON_REQUEST_MAX_BYTES = 64 * 1024
_JSON_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
_MARKDOWN_MAX_BYTES = 2 * 1024 * 1024
_PDF_MAX_BYTES = 10 * 1024 * 1024
_CONNECT_TIMEOUT_SECONDS = 3
_READ_TIMEOUT_SECONDS = 90

# Sidecar 只允许本机地址。禁止 requests 继承 HTTP(S)_PROXY、NETRC 等进程环境，
# 避免内部 Bearer Token 或 Domeye 用户身份被环境代理转发。
_SIDECAR_HTTP = requests.Session()
_SIDECAR_HTTP.trust_env = False


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


def _agent_base_url() -> str:
    raw = os.environ.get("COUNTRY_OUTAGE_AGENT_URL", "").strip()
    if not raw:
        raise RuntimeError("国家中断 Agent Sidecar 尚未配置")
    parsed = urlparse(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("国家中断 Agent Sidecar 地址必须是无凭据的本机 HTTP/HTTPS")
    return raw.rstrip("/")


def _sidecar_url(path: str) -> str:
    return f"{_agent_base_url()}{path}"


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
    raise PermissionError("需要经过 Domeye 控制面认证后访问国家中断报告")


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


def _request_sidecar(method: str, path: str, **kwargs):
    return _SIDECAR_HTTP.request(
        method,
        _sidecar_url(path),
        timeout=(_CONNECT_TIMEOUT_SECONDS, _READ_TIMEOUT_SECONDS),
        allow_redirects=False,
        **kwargs,
    )


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


def _validate_report_request(value: dict) -> None:
    allowed = {
        "event_reference",
        "publication_id",
        "revision",
        "idempotency_key",
    }
    if set(value) - allowed:
        raise ValueError("报告请求包含未授权字段")
    reference = value.get("event_reference")
    if not isinstance(reference, str) or not _COUNTRY_OUTAGE_REFERENCE.fullmatch(
        reference
    ):
        raise ValueError("event_reference 不是合法 country_outage 引用")
    publication_id = value.get("publication_id")
    if (
        not isinstance(publication_id, str)
        or not publication_id
        or len(publication_id) > 256
    ):
        raise ValueError("publication_id 必须是非空字符串")
    revision = value.get("revision")
    if (
        not isinstance(revision, int) or isinstance(revision, bool) or revision < 1
    ):
        raise ValueError("revision 必须是正整数")
    idempotency_key = value.get("idempotency_key")
    if (
        not isinstance(idempotency_key, str)
        or not _IDEMPOTENCY_KEY.fullmatch(idempotency_key)
    ):
        raise ValueError("idempotency_key 必须是 8 至 128 位安全字符")
    _validate_idempotency_header(idempotency_key)


def _validate_idempotency_header(body_value: str) -> None:
    header_value = request.headers.get("Idempotency-Key")
    if header_value is not None:
        if (
            not _IDEMPOTENCY_KEY.fullmatch(header_value)
            or not compare_digest(header_value, body_value)
        ):
            raise ValueError("Header 与请求体的 idempotency_key 不一致")


def _validate_question_quote(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("quote 必须是对象")
    allowed = {
        "kind",
        "section_id",
        "paragraph_index",
        "highlight_index",
        "evidence_refs",
    }
    if set(value) - allowed:
        raise ValueError("quote 包含未授权字段")
    kind = value.get("kind")
    if kind not in {"summary", "highlight", "section_paragraph"}:
        raise ValueError("quote.kind 无效")
    evidence_refs = value.get("evidence_refs", [])
    if (
        not isinstance(evidence_refs, list)
        or len(evidence_refs) > 20
        or any(
            not isinstance(item, str)
            or not _SAFE_EVIDENCE_REF.fullmatch(item)
            for item in evidence_refs
        )
    ):
        raise ValueError("quote.evidence_refs 无效")
    if kind == "summary":
        if any(
            key in value
            for key in ("section_id", "paragraph_index", "highlight_index")
        ):
            raise ValueError("summary quote 不接受位置索引")
        return
    if kind == "highlight":
        index = value.get("highlight_index")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or "section_id" in value
            or "paragraph_index" in value
        ):
            raise ValueError("highlight quote 必须提供 highlight_index")
        return
    section_id = value.get("section_id")
    paragraph_index = value.get("paragraph_index")
    if (
        not isinstance(section_id, str)
        or not _SAFE_SECTION_ID.fullmatch(section_id)
        or not isinstance(paragraph_index, int)
        or isinstance(paragraph_index, bool)
        or paragraph_index < 0
        or "highlight_index" in value
    ):
        raise ValueError(
            "section_paragraph quote 必须提供 section_id 和 paragraph_index"
        )


def _validate_external_authorization(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("external_authorization 必须是对象")
    if set(value) != {"authorized", "authorized_at"}:
        raise ValueError("external_authorization 字段不完整或包含额外字段")
    if value.get("authorized") is not True:
        raise ValueError("外部证据必须由用户显式授权")
    authorized_at = value.get("authorized_at")
    if not isinstance(authorized_at, str) or not authorized_at.strip():
        raise ValueError("external_authorization.authorized_at 无效")
    try:
        datetime.fromisoformat(authorized_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            "external_authorization.authorized_at 必须是 ISO 8601 时间"
        ) from error


def _validate_external_urls(value: object) -> None:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 5
        or any(not isinstance(item, str) for item in value)
    ):
        raise ValueError(
            "domeye_plus_external 必须包含 1 至 5 个指定公开 URL"
        )
    normalized_urls: set[
        tuple[str, str, int, str, str, str]
    ] = set()
    for raw in value:
        if not raw or len(raw) > 2_048:
            raise ValueError("external_urls 包含空值或超长 URL")
        parsed = urlparse(raw)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("external_urls 包含无效端口") from error
        expected_port = 443 if parsed.scheme == "https" else 80
        hostname = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username
            or parsed.password
            or (port is not None and port != expected_port)
        ):
            raise ValueError(
                "外部证据只接受标准端口且不含认证信息的公开 HTTP/HTTPS URL"
            )
        normalized_hostname = hostname.lower().rstrip(".")
        normalized = (
            parsed.scheme.lower(),
            normalized_hostname,
            port or expected_port,
            parsed.path or "/",
            parsed.params,
            parsed.query,
        )
        if normalized in normalized_urls:
            raise ValueError("external_urls 不能包含重复的指定 URL")
        normalized_urls.add(normalized)


def _validate_question_request(value: dict) -> None:
    allowed = {
        "question",
        "evidence_mode",
        "idempotency_key",
        "quote",
        "external_authorization",
        "external_urls",
    }
    if set(value) - allowed:
        raise ValueError("问题请求包含未授权字段")
    question = value.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question 不能为空")
    if len(question) > 4_000:
        raise ValueError("question 超过 4,000 字符")
    evidence_mode = value.get("evidence_mode")
    if evidence_mode not in {"domeye_only", "domeye_plus_external"}:
        raise ValueError(
            "evidence_mode 只允许 domeye_only 或 domeye_plus_external"
        )
    idempotency_key = value.get("idempotency_key")
    if (
        not isinstance(idempotency_key, str)
        or not _IDEMPOTENCY_KEY.fullmatch(idempotency_key)
    ):
        raise ValueError("idempotency_key 必须是 8 至 128 位安全字符")
    _validate_idempotency_header(idempotency_key)
    if "quote" in value:
        _validate_question_quote(value["quote"])
    if evidence_mode == "domeye_only":
        if (
            "external_authorization" in value
            or "external_urls" in value
        ):
            raise ValueError("domeye_only 不能携带外部授权或 URL")
        return
    if "external_authorization" not in value:
        raise ValueError("外部证据必须由用户显式授权")
    _validate_external_authorization(value["external_authorization"])
    if "external_urls" not in value:
        raise ValueError(
            "domeye_plus_external 必须包含 1 至 5 个指定公开 URL"
        )
    _validate_external_urls(value["external_urls"])


def _json_proxy(method: str, path: str, body: dict | None = None):
    try:
        upstream = _request_sidecar(
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
            "agent_unavailable",
            str(error),
            retryable=True,
            next_action="确认本机 Sidecar 配置与运行状态后重试",
        )
    content = upstream.content
    if len(content) > _JSON_RESPONSE_MAX_BYTES:
        return _error(502, "agent_response_too_large", "Agent 响应超过 2 MiB")
    response = Response(
        content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json"),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _safe_identifier(value: str, label: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} 无效")
    return value


def _artifact_proxy(
    *,
    path: str,
    accept: str,
    maximum_bytes: int,
    artifact_label: str,
):
    try:
        upstream = _request_sidecar(
            "GET",
            path,
            headers=_request_headers(accept=accept),
            stream=True,
        )
    except PermissionError as error:
        return _error(401, "authentication_required", str(error))
    except (requests.RequestException, RuntimeError) as error:
        return _error(
            503,
            "agent_unavailable",
            str(error),
            retryable=True,
            next_action="确认本机 Sidecar 配置与运行状态后重试下载",
        )

    content = bytearray()
    try:
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            content.extend(chunk)
            if len(content) > maximum_bytes:
                return _error(
                    502,
                    "agent_artifact_too_large",
                    f"{artifact_label} 制品超过冻结大小上限",
                )
    finally:
        upstream.close()

    response = Response(
        bytes(content),
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", accept),
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    disposition = upstream.headers.get("Content-Disposition")
    if disposition:
        response.headers["Content-Disposition"] = disposition
    for name in ("X-Artifact-Id", "X-Content-SHA256"):
        value = upstream.headers.get(name)
        if value:
            response.headers[name] = value
    return response


class CountryOutageAgentReportCollectionResource(Resource):
    def post(self):
        try:
            body = _read_json_body()
            _validate_report_request(body)
        except ValueError as error:
            return _error(400, "invalid_agent_request", str(error))
        return _json_proxy("POST", "/country-outage/reports", body)


class CountryOutageAgentExternalEvidenceCapabilityResource(Resource):
    def get(self):
        return _json_proxy(
            "GET",
            "/country-outage/capabilities/external-evidence",
        )


class CountryOutageAgentQuestionResource(Resource):
    def post(self, report_id: str):
        try:
            report_id = _safe_identifier(report_id, "report_id")
            body = _read_json_body()
            _validate_question_request(body)
        except ValueError as error:
            return _error(400, "invalid_agent_request", str(error))
        return _json_proxy(
            "POST",
            f"/country-outage/reports/{report_id}/questions",
            body,
        )


class CountryOutageAgentAbortResource(Resource):
    def post(self, run_id: str):
        try:
            run_id = _safe_identifier(run_id, "run_id")
            body = _read_json_body()
            if body:
                raise ValueError("取消请求不接受额外字段")
        except ValueError as error:
            return _error(400, "invalid_agent_request", str(error))
        return _json_proxy(
            "POST",
            f"/country-outage/runs/{run_id}/abort",
            body,
        )


class CountryOutageAgentEventResource(Resource):
    def get(self, report_id: str):
        try:
            report_id = _safe_identifier(report_id, "report_id")
            upstream = _request_sidecar(
                "GET",
                f"/country-outage/reports/{report_id}/events",
                headers=_request_headers(accept="text/event-stream"),
                stream=True,
            )
        except ValueError as error:
            return _error(400, "invalid_agent_request", str(error))
        except PermissionError as error:
            return _error(401, "authentication_required", str(error))
        except (requests.RequestException, RuntimeError) as error:
            return _error(
                503,
                "agent_unavailable",
                str(error),
                retryable=True,
                next_action="确认本机 Sidecar 配置与运行状态后重连",
            )

        if upstream.status_code >= 400:
            content = upstream.content[:_JSON_RESPONSE_MAX_BYTES]
            upstream.close()
            return Response(
                content,
                status=upstream.status_code,
                content_type=upstream.headers.get(
                    "Content-Type", "application/json"
                ),
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


class CountryOutageAgentArtifactResource(Resource):
    def get(self, report_id: str, artifact_format: str):
        try:
            report_id = _safe_identifier(report_id, "report_id")
            if artifact_format not in {"markdown", "pdf"}:
                raise ValueError("artifact_format 只允许 markdown 或 pdf")
        except ValueError as error:
            return _error(400, "invalid_agent_request", str(error))

        maximum = (
            _MARKDOWN_MAX_BYTES
            if artifact_format == "markdown"
            else _PDF_MAX_BYTES
        )
        accept = (
            "text/markdown; charset=utf-8"
            if artifact_format == "markdown"
            else "application/pdf"
        )
        return _artifact_proxy(
            path=(
                f"/country-outage/reports/{report_id}/artifacts/"
                f"{artifact_format}"
            ),
            accept=accept,
            maximum_bytes=maximum,
            artifact_label=artifact_format,
        )


class CountryOutageAgentExternalAppendixArtifactResource(Resource):
    def get(self, report_id: str, question_id: str):
        try:
            report_id = _safe_identifier(report_id, "report_id")
            question_id = _safe_identifier(question_id, "question_id")
        except ValueError as error:
            return _error(400, "invalid_agent_request", str(error))
        return _artifact_proxy(
            path=(
                f"/country-outage/reports/{report_id}/questions/"
                f"{question_id}/artifacts/external-appendix"
            ),
            accept="text/markdown; charset=utf-8",
            maximum_bytes=_MARKDOWN_MAX_BYTES,
            artifact_label="external-appendix",
        )
