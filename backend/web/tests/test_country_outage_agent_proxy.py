from __future__ import annotations

import json

import pytest
import requests

from web.api.v2 import country_outage_agent_proxy as proxy
from web.country_outage_agent_identity import (
    TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY,
    WSGI_REMOTE_USER_MODE,
)


REFERENCE = "country_outage/2026-02-27 09:12:32/IR/1/r"


@pytest.fixture(autouse=True)
def proxy_environment(monkeypatch):
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_SHARED_TOKEN",
        "test-only-country-outage-agent-token",
    )
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", WSGI_REMOTE_USER_MODE
    )
    monkeypatch.delenv(
        "COUNTRY_OUTAGE_AGENT_ACCEPTANCE_MODE", raising=False
    )
    monkeypatch.delenv(
        "COUNTRY_OUTAGE_AGENT_ACCEPTANCE_USER_ID", raising=False
    )
    monkeypatch.delenv(
        "COUNTRY_OUTAGE_AGENT_ACCEPTANCE_SCOPE", raising=False
    )


@pytest.fixture()
def client(app):
    value = app.test_client()
    value.environ_base["REMOTE_USER"] = "trusted-test-user"
    value.environ_base[
        TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY
    ] = "country_outage_event_read"
    return value


def test_legacy_static_acceptance_environment_cannot_create_principal(
    app, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        proxy,
        "_request_sidecar",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setenv("COUNTRY_OUTAGE_AGENT_ACCEPTANCE_MODE", "1")
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_ACCEPTANCE_USER_ID", "legacy-static-user"
    )
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_ACCEPTANCE_SCOPE",
        "country_outage_event_read",
    )
    monkeypatch.delenv("COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", raising=False)

    response = app.test_client().post(
        "/api/v2/country-outage/reports",
        json=report_request(),
    )

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "authentication_required"
    assert calls == []


class FakeUpstream:
    def __init__(
        self,
        content=b"",
        status_code=200,
        content_type="application/json",
        chunks=None,
        headers=None,
    ):
        self.content = content
        self.status_code = status_code
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self._chunks = chunks if chunks is not None else [content]
        self.closed = False

    def iter_content(self, chunk_size):
        del chunk_size
        yield from self._chunks

    def close(self):
        self.closed = True


def report_request():
    return {
        "event_reference": REFERENCE,
        "publication_id": "publication-test",
        "revision": 1,
        "idempotency_key": "report-request-1",
    }


def test_proxy_is_disabled_without_local_sidecar(client, monkeypatch):
    monkeypatch.delenv("COUNTRY_OUTAGE_AGENT_URL", raising=False)
    response = client.post(
        "/api/v2/country-outage/reports",
        json=report_request(),
    )
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "agent_unavailable"


def test_external_evidence_capability_is_transparently_read_from_orchestrator(
    client, monkeypatch
):
    calls = []
    payload = {
        "schema_version": "country_outage_external_evidence_capability_v1",
        "capability": "external_evidence",
        "state": "not_configured",
        "provider": "disabled",
        "checked_at": "2026-07-30T10:00:00Z",
        "policy": None,
        "reason_code": "external_evidence_not_configured",
    }

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return FakeUpstream(json.dumps(payload).encode())

    monkeypatch.setattr(proxy, "_request_sidecar", fake_request)
    response = client.get(
        "/api/v2/country-outage/capabilities/external-evidence"
    )

    assert response.status_code == 200
    assert response.get_json() == payload
    assert calls[0][0:2] == (
        "GET",
        "/country-outage/capabilities/external-evidence",
    )
    assert calls[0][2]["json"] is None
    assert (
        calls[0][2]["headers"]["X-Domeye-User"]
        == "trusted-test-user"
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "event_reference"),
        (
            {
                "event_reference": (
                    "country_outage/2026-02-27 09:12:32/IR/1/r/extra"
                )
            },
            "event_reference",
        ),
        (
            {
                "event_reference": (
                    "prefix_outage/2026-02-27 09:12:32/IR/1/r"
                )
            },
            "event_reference",
        ),
        (
            {
                "event_reference": REFERENCE,
                "publication_id": "publication-test",
                "revision": 0,
                "idempotency_key": "invalid-revision",
            },
            "revision",
        ),
    ],
)
def test_report_proxy_rejects_out_of_scope_input(client, payload, message):
    response = client.post("/api/v2/country-outage/reports", json=payload)
    assert response.status_code == 400
    assert message in response.get_json()["error"]["message"]


def test_report_and_question_proxy_only_forward_fixed_json(
    client, monkeypatch
):
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return FakeUpstream(
            json.dumps(
                {
                    "report_id": "report-test",
                    "run_id": "run-test",
                    "state": "queued",
                }
            ).encode()
        )

    monkeypatch.setattr(proxy, "_request_sidecar", fake_request)
    report_response = client.post(
        "/api/v2/country-outage/reports",
        json=report_request(),
        headers={"Idempotency-Key": "report-request-1"},
    )
    assert report_response.status_code == 200
    assert calls[0][0:2] == ("POST", "/country-outage/reports")
    assert calls[0][2]["json"] == report_request()
    assert calls[0][2]["headers"]["Idempotency-Key"] == "report-request-1"
    assert (
        calls[0][2]["headers"]["X-Domeye-User"]
        == "trusted-test-user"
    )
    assert (
        calls[0][2]["headers"]["X-Domeye-Authorization-Scope"]
        == "country_outage_event_read"
    )
    assert calls[0][2]["headers"]["Authorization"].startswith("Bearer ")

    question = {
        "question": "最低覆盖率是多少？",
        "evidence_mode": "domeye_only",
        "idempotency_key": "question-1",
        "quote": {
            "kind": "section_paragraph",
            "section_id": "visibility",
            "paragraph_index": 1,
            "evidence_refs": ["series:/series/12"],
        },
    }
    question_response = client.post(
        "/api/v2/country-outage/reports/report-test/questions",
        json=question,
    )
    assert question_response.status_code == 200
    assert calls[1][0:2] == (
        "POST",
        "/country-outage/reports/report-test/questions",
    )
    assert calls[1][2]["json"] == question

    external_question = {
        "question": "读取并核验指定 URL",
        "evidence_mode": "domeye_plus_external",
        "external_authorization": {
            "authorized": True,
            "authorized_at": "2026-07-28T15:00:00Z",
        },
        "external_urls": ["https://radar.cloudflare.com/notice"],
        "idempotency_key": "external-authorized-1",
    }
    external_response = client.post(
        "/api/v2/country-outage/reports/report-test/questions",
        json=external_question,
    )
    assert external_response.status_code == 200
    assert calls[2][0:2] == (
        "POST",
        "/country-outage/reports/report-test/questions",
    )
    assert calls[2][2]["json"] == external_question

    external = client.post(
        "/api/v2/country-outage/reports/report-test/questions",
        json={
            "question": "联网查原因",
            "evidence_mode": "domeye_plus_external",
            "idempotency_key": "external-not-authorized",
        },
    )
    assert external.status_code == 400
    assert "显式授权" in external.get_json()["error"]["message"]


@pytest.mark.parametrize(
    "external_fields",
    [
        {
            "external_authorization": {
                "authorized": False,
                "authorized_at": "2026-07-28T15:00:00Z",
            },
        },
        {
            "external_authorization": {
                "authorized": True,
                "authorized_at": "not-a-time",
            },
        },
        {
            "external_authorization": {
                "authorized": True,
                "authorized_at": "2026-07-28T15:00:00Z",
                "extra": "blocked",
            },
        },
        {
            "external_authorization": {
                "authorized": True,
                "authorized_at": "2026-07-28T15:00:00Z",
            },
        },
        {
            "external_authorization": {
                "authorized": True,
                "authorized_at": "2026-07-28T15:00:00Z",
            },
            "external_urls": [],
        },
        {
            "external_authorization": {
                "authorized": True,
                "authorized_at": "2026-07-28T15:00:00Z",
            },
            "external_urls": [
                "https://BGP.HE.NET/country/IR#one",
                "https://bgp.he.net/country/IR#two",
            ],
        },
        {
            "external_authorization": {
                "authorized": True,
                "authorized_at": "2026-07-28T15:00:00Z",
            },
            "external_urls": ["file:///etc/passwd"],
        },
        {
            "external_authorization": {
                "authorized": True,
                "authorized_at": "2026-07-28T15:00:00Z",
            },
            "external_urls": ["https://user:secret@public.example/"],
        },
        {
            "external_authorization": {
                "authorized": True,
                "authorized_at": "2026-07-28T15:00:00Z",
            },
            "external_urls": ["https://public.example:8443/"],
        },
        {
            "external_authorization": {
                "authorized": True,
                "authorized_at": "2026-07-28T15:00:00Z",
            },
            "external_urls": [
                f"https://public.example/{index}" for index in range(6)
            ],
        },
    ],
)
def test_external_proxy_rejects_unsafe_or_ambiguous_authorization(
    client, monkeypatch, external_fields
):
    calls = []
    monkeypatch.setattr(
        proxy,
        "_request_sidecar",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    response = client.post(
        "/api/v2/country-outage/reports/report-test/questions",
        json={
            "question": "读取公开来源",
            "evidence_mode": "domeye_plus_external",
            "idempotency_key": "external-safety-1",
            **external_fields,
        },
    )
    assert response.status_code == 400
    assert calls == []


def test_external_proxy_does_not_duplicate_capability_source_policy(
    client, monkeypatch
):
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return FakeUpstream(
            json.dumps(
                {
                    "question_id": "question-test",
                    "run_id": "run-test",
                    "state": "queued",
                }
            ).encode()
        )

    monkeypatch.setattr(proxy, "_request_sidecar", fake_request)
    url = "https://policy-owned.example/source"
    response = client.post(
        "/api/v2/country-outage/reports/report-test/questions",
        json={
            "question": "读取公开来源",
            "evidence_mode": "domeye_plus_external",
            "external_authorization": {
                "authorized": True,
                "authorized_at": "2026-07-28T15:00:00Z",
            },
            "external_urls": [url],
            "idempotency_key": "external-host-blocked-1",
        },
    )
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][2]["json"]["external_urls"] == [url]


@pytest.mark.parametrize(
    "url",
    [
        "https://bgp.he.net/",
        "https://sub.bgp.he.net/path",
        "https://radar.cloudflare.com/",
        "https://api.radar.cloudflare.com/path",
    ],
)
def test_external_proxy_accepts_exact_hosts_and_point_boundary_subdomains(
    client, monkeypatch, url
):
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return FakeUpstream(
            json.dumps(
                {
                    "question_id": "question-test",
                    "run_id": "run-test",
                    "state": "queued",
                }
            ).encode()
        )

    monkeypatch.setattr(proxy, "_request_sidecar", fake_request)
    response = client.post(
        "/api/v2/country-outage/reports/report-test/questions",
        json={
            "question": "读取公开来源",
            "evidence_mode": "domeye_plus_external",
            "external_authorization": {
                "authorized": True,
                "authorized_at": "2026-07-28T15:00:00Z",
            },
            "external_urls": [url],
            "idempotency_key": "external-host-allowed-1",
        },
    )
    assert response.status_code == 200
    assert len(calls) == 1


def test_sse_proxy_forwards_resume_cursor_without_buffering(
    client, monkeypatch
):
    upstream = FakeUpstream(
        status_code=200,
        content_type="text/event-stream",
        chunks=[
            b"id: 7\nevent: report_state\ndata: {\"state\":\"validating\"}\n\n",
            b"id: 8\nevent: report_state\ndata: {\"state\":\"completed\"}\n\n",
        ],
    )
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return upstream

    monkeypatch.setattr(proxy, "_request_sidecar", fake_request)
    response = client.get(
        "/api/v2/country-outage/reports/report-test/events",
        headers={"Last-Event-ID": "6"},
    )
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert response.headers["X-Accel-Buffering"] == "no"
    assert response.headers["Cache-Control"] == "no-cache, no-store"
    assert b"event: report_state" in response.data
    assert upstream.closed is True
    assert captured["method"] == "GET"
    assert captured["path"] == "/country-outage/reports/report-test/events"
    assert captured["headers"]["Last-Event-ID"] == "6"
    assert captured["stream"] is True


def test_download_proxy_preserves_filename_and_content_identity(
    client, monkeypatch
):
    pdf = b"%PDF-1.4\nacceptance\n%%EOF\n"
    upstream = FakeUpstream(
        content=pdf,
        content_type="application/pdf",
        chunks=[pdf],
        headers={
            "Content-Disposition": 'attachment; filename="IR-report.pdf"',
            "X-Artifact-Id": "report-test",
            "X-Content-SHA256": "a" * 64,
        },
    )
    monkeypatch.setattr(
        proxy,
        "_request_sidecar",
        lambda *args, **kwargs: upstream,
    )
    response = client.get(
        "/api/v2/country-outage/reports/report-test/artifacts/pdf"
    )
    assert response.status_code == 200
    assert response.data == pdf
    assert response.mimetype == "application/pdf"
    assert response.headers["Content-Disposition"].endswith(
        '"IR-report.pdf"'
    )
    assert response.headers["X-Artifact-Id"] == "report-test"
    assert response.headers["X-Content-SHA256"] == "a" * 64
    assert response.headers["Cache-Control"] == "private, no-store"
    assert upstream.closed is True


def test_external_appendix_download_proxy_is_bound_to_report_and_question(
    client, monkeypatch
):
    appendix = "# 国家中断外部来源核验附录\n".encode()
    upstream = FakeUpstream(
        content=appendix,
        content_type="text/markdown; charset=utf-8",
        chunks=[appendix],
        headers={
            "Content-Disposition": (
                'attachment; filename="IR_external_q1.md"'
            ),
            "X-Artifact-Id": "external_appendix_" + "b" * 32,
            "X-Content-SHA256": "c" * 64,
        },
    )
    captured = {}

    def fake_request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return upstream

    monkeypatch.setattr(proxy, "_request_sidecar", fake_request)
    response = client.get(
        (
            "/api/v2/country-outage/reports/report-test/questions/"
            "question-test/artifacts/external-appendix"
        )
    )

    assert response.status_code == 200
    assert response.data == appendix
    assert response.mimetype == "text/markdown"
    assert captured["method"] == "GET"
    assert captured["path"] == (
        "/country-outage/reports/report-test/questions/question-test/"
        "artifacts/external-appendix"
    )
    assert captured["headers"]["Accept"] == (
        "text/markdown; charset=utf-8"
    )
    assert captured["headers"]["X-Domeye-User"] == "trusted-test-user"
    assert captured["headers"]["X-Domeye-Authorization-Scope"] == (
        "country_outage_event_read"
    )
    assert captured["stream"] is True
    assert response.headers["Content-Disposition"].endswith(
        '"IR_external_q1.md"'
    )
    assert response.headers["X-Artifact-Id"] == (
        "external_appendix_" + "b" * 32
    )
    assert response.headers["X-Content-SHA256"] == "c" * 64
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert upstream.closed is True


@pytest.mark.parametrize(
    "path",
    [
        (
            "/api/v2/country-outage/reports/bad.report/questions/"
            "question-test/artifacts/external-appendix"
        ),
        (
            "/api/v2/country-outage/reports/report-test/questions/"
            "bad.question/artifacts/external-appendix"
        ),
    ],
)
def test_external_appendix_download_rejects_unsafe_identifiers(
    client, monkeypatch, path
):
    calls = []
    monkeypatch.setattr(
        proxy,
        "_request_sidecar",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = client.get(path)

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_agent_request"
    assert calls == []


def test_sidecar_address_and_failures_are_constrained(client, monkeypatch):
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_URL", "http://169.254.169.254/latest"
    )
    response = client.post(
        "/api/v2/country-outage/reports",
        json=report_request(),
    )
    assert response.status_code == 503
    assert "本机" in response.get_json()["error"]["message"]

    monkeypatch.setattr(
        proxy,
        "_request_sidecar",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            requests.Timeout("sidecar timeout")
        ),
    )
    response = client.post(
        "/api/v2/country-outage/reports",
        json=report_request(),
    )
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "agent_unavailable"


def test_sidecar_http_client_does_not_inherit_environment_proxy_or_netrc():
    assert proxy._SIDECAR_HTTP.trust_env is False


def test_browser_identity_headers_are_ignored_and_wsgi_identity_is_required(
    client, monkeypatch
):
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return FakeUpstream(content=b"{}")

    monkeypatch.setattr(proxy, "_request_sidecar", fake_request)

    rejected = client.post(
        "/api/v2/country-outage/reports",
        json=report_request(),
        headers={
            "X-Domeye-User": "browser-forged-user",
            "X-Domeye-Roles": "admin",
        },
        environ_overrides={
            "REMOTE_USER": "",
            TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY: "",
        },
    )
    assert rejected.status_code == 401
    assert rejected.get_json()["error"]["code"] == "authentication_required"
    assert calls == []

    accepted = client.post(
        "/api/v2/country-outage/reports",
        json=report_request(),
        headers={"X-Domeye-User": "browser-forged-user"},
        environ_overrides={
            "domeye.authenticated_user_id": "trusted-user",
            "domeye.authorization_scope": "country_outage_event_read:IR",
        },
    )
    assert accepted.status_code == 200
    forwarded = calls[0][2]["headers"]
    assert forwarded["X-Domeye-User"] == "trusted-user"
    assert (
        forwarded["X-Domeye-Authorization-Scope"]
        == "country_outage_event_read:IR"
    )
