from __future__ import annotations

import json

import pytest
from flask import jsonify, request

from web.api.v2 import country_outage_agent_proxy as proxy
from web.country_outage_agent_identity import (
    INTERNAL_FIXED_HISTORY_MODE,
    INTERNAL_USER_ID_ENV,
    TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY,
    WSGI_REMOTE_USER_MODE,
)


REFERENCE = "country_outage/2026-02-27 09:12:32/IR/1/r"


class FakeUpstream:
    status_code = 200
    content = json.dumps(
        {
            "report_id": "report-identity-test",
            "run_id": "run-identity-test",
            "state": "queued",
        }
    ).encode()
    headers = {"Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def identity_environment(monkeypatch):
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_SHARED_TOKEN",
        "test-only-country-outage-agent-token",
    )
    monkeypatch.delenv("COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", raising=False)
    monkeypatch.delenv(INTERNAL_USER_ID_ENV, raising=False)


def _report_request():
    return {
        "event_reference": REFERENCE,
        "publication_id": "publication-identity-test",
        "revision": 1,
        "idempotency_key": "identity-request-1",
    }


def _capture_upstream(monkeypatch):
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return FakeUpstream()

    monkeypatch.setattr(proxy, "_request_sidecar", fake_request)
    return calls


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Remote-User": "browser-forged-user"},
        {"Remote-User": "browser-forged-user"},
        {
            "X-Domeye-User": "browser-forged-user",
            "X-Domeye-Authorization-Scope": "admin",
        },
    ],
)
def test_browser_identity_and_scope_headers_never_create_principal(
    client, monkeypatch, headers
):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", WSGI_REMOTE_USER_MODE
    )

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        headers=headers,
    )

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "authentication_required"
    assert calls == []


def test_remote_user_mode_must_be_explicitly_enabled(client, monkeypatch):
    calls = _capture_upstream(monkeypatch)

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        environ_overrides={
            "REMOTE_USER": "oidc-user",
            TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY:
                "country_outage_event_read:IR",
        },
    )

    assert response.status_code == 401
    assert calls == []


def test_remote_user_is_rejected_for_non_loopback_source(client, monkeypatch):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", WSGI_REMOTE_USER_MODE
    )

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        environ_overrides={
            "REMOTE_ADDR": "192.0.2.10",
            "REMOTE_USER": "oidc-user",
            TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY:
                "country_outage_event_read:IR",
        },
    )

    assert response.status_code == 401
    assert calls == []


@pytest.mark.parametrize(
    "remote_user",
    [None, "", "   ", "user\nforged", "x" * 257],
)
def test_missing_or_invalid_remote_user_is_rejected(
    client, monkeypatch, remote_user
):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", WSGI_REMOTE_USER_MODE
    )
    environ_overrides = {"REMOTE_ADDR": "127.0.0.1"}
    if remote_user is not None:
        environ_overrides["REMOTE_USER"] = remote_user
    environ_overrides[TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY] = (
        "country_outage_event_read:IR"
    )

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        environ_overrides=environ_overrides,
    )

    assert response.status_code == 401
    assert calls == []


def test_remote_user_without_trusted_event_scope_fails_closed(
    client, monkeypatch
):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", WSGI_REMOTE_USER_MODE
    )

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        environ_overrides={
            "REMOTE_ADDR": "127.0.0.1",
            "REMOTE_USER": "authenticated-but-not-authorized",
        },
    )

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "authentication_required"
    assert calls == []


@pytest.mark.parametrize(
    "authorization_scope",
    [
        "",
        "admin",
        "country_outage_event_write",
        "country_outage_event_read:ir",
        "country_outage_event_read:IR,admin",
        "country_outage_event_read:IR,,country_outage_event_read:CN",
        "country_outage_event_read:IR\ncountry_outage_event_read:CN",
    ],
)
def test_invalid_trusted_event_scope_fails_closed(
    client, monkeypatch, authorization_scope
):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", WSGI_REMOTE_USER_MODE
    )

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        environ_overrides={
            "REMOTE_ADDR": "127.0.0.1",
            "REMOTE_USER": "oidc-user",
            TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY: authorization_scope,
        },
    )

    assert response.status_code == 401
    assert calls == []


@pytest.mark.parametrize("loopback", ["127.0.0.1", "::1"])
@pytest.mark.parametrize(
    ("authorization_scope", "expected_scope"),
    [
        ("country_outage_event_read", "country_outage_event_read"),
        (
            "country_outage_event_read:IR,"
            "country_outage_event_read,"
            "country_outage_event_read:CN",
            "country_outage_event_read",
        ),
        (
            "country_outage_event_read:IR,"
            "country_outage_event_read:IR",
            "country_outage_event_read:IR",
        ),
    ],
)
def test_loopback_wsgi_remote_user_maps_to_trusted_event_scope(
    client,
    monkeypatch,
    loopback,
    authorization_scope,
    expected_scope,
):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", WSGI_REMOTE_USER_MODE
    )

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        headers={
            "X-Domeye-Authorization-Scope": "admin",
            "X-Domeye-User": "browser-forged-user",
        },
        environ_overrides={
            "REMOTE_ADDR": loopback,
            "REMOTE_USER": "  verified-oidc-user  ",
            TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY: authorization_scope,
        },
    )

    assert response.status_code == 200
    assert len(calls) == 1
    forwarded = calls[0][2]["headers"]
    assert forwarded["X-Domeye-User"] == "verified-oidc-user"
    assert (
        forwarded["X-Domeye-Authorization-Scope"]
        == expected_scope
    )


def test_non_matching_country_scope_is_forwarded_without_expansion(
    client, monkeypatch
):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", WSGI_REMOTE_USER_MODE
    )

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        environ_overrides={
            "REMOTE_ADDR": "127.0.0.1",
            "REMOTE_USER": "no-ir-access-user",
            TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY:
                "country_outage_event_read:CN",
        },
    )

    assert response.status_code == 200
    forwarded = calls[0][2]["headers"]
    assert forwarded["X-Domeye-User"] == "no-ir-access-user"
    assert (
        forwarded["X-Domeye-Authorization-Scope"]
        == "country_outage_event_read:CN"
    )


def test_browser_cannot_supply_trusted_wsgi_authorization_scope(
    client, monkeypatch
):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", WSGI_REMOTE_USER_MODE
    )

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        headers={
            "Domeye-Country-Outage-Authorization-Scope":
                "country_outage_event_read",
            "X-Domeye-Authorization-Scope": "country_outage_event_read",
        },
        environ_overrides={
            "REMOTE_ADDR": "127.0.0.1",
            "REMOTE_USER": "oidc-user",
        },
    )

    assert response.status_code == 401
    assert calls == []


def test_existing_domeye_wsgi_principal_remains_compatible(
    client, monkeypatch
):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", WSGI_REMOTE_USER_MODE
    )

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        environ_overrides={
            "REMOTE_ADDR": "192.0.2.10",
            "REMOTE_USER": "must-not-override",
            "domeye.authenticated_user_id": "existing-trusted-user",
            "domeye.authorization_scope": "country_outage_event_read:IR",
        },
    )

    assert response.status_code == 200
    forwarded = calls[0][2]["headers"]
    assert forwarded["X-Domeye-User"] == "existing-trusted-user"
    assert (
        forwarded["X-Domeye-Authorization-Scope"]
        == "country_outage_event_read:IR"
    )


def test_identity_adapter_does_not_touch_other_routes(
    app, monkeypatch
):
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE", WSGI_REMOTE_USER_MODE
    )

    @app.get("/identity-adapter-probe")
    def identity_adapter_probe():
        return jsonify(
            {
                "user": request.environ.get(
                    "domeye.authenticated_user_id"
                ),
                "scope": request.environ.get(
                    "domeye.authorization_scope"
                ),
            }
        )

    response = app.test_client().get(
        "/identity-adapter-probe",
        environ_overrides={
            "REMOTE_ADDR": "127.0.0.1",
            "REMOTE_USER": "oidc-user",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {"user": None, "scope": None}


@pytest.mark.parametrize("loopback", ["127.0.0.1", "::1"])
def test_internal_fixed_history_mode_maps_safe_environment_user_to_ir_read_scope(
    client, monkeypatch, loopback
):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE",
        INTERNAL_FIXED_HISTORY_MODE,
    )
    monkeypatch.setenv(INTERNAL_USER_ID_ENV, "internal-history-observer")

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        headers={
            "X-Domeye-User": "browser-forged-user",
            "X-Domeye-Authorization-Scope": "admin",
            "Remote-User": "browser-forged-user",
        },
        environ_overrides={
            "REMOTE_ADDR": loopback,
            "REMOTE_USER": "wsgi-user-must-be-ignored",
            TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY:
                "country_outage_event_read:CN",
            "domeye.authenticated_user_id": "existing-user-must-be-ignored",
            "domeye.authorization_scope": "country_outage_event_read",
        },
    )

    assert response.status_code == 200
    assert len(calls) == 1
    forwarded = calls[0][2]["headers"]
    assert forwarded["X-Domeye-User"] == "internal-history-observer"
    assert (
        forwarded["X-Domeye-Authorization-Scope"]
        == "country_outage_event_read:IR"
    )


def test_internal_fixed_history_mode_must_be_explicitly_enabled(
    client, monkeypatch
):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(INTERNAL_USER_ID_ENV, "internal-history-observer")

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 401
    assert calls == []


def test_internal_fixed_history_mode_rejects_non_loopback_even_with_existing_principal(
    client, monkeypatch
):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE",
        INTERNAL_FIXED_HISTORY_MODE,
    )
    monkeypatch.setenv(INTERNAL_USER_ID_ENV, "internal-history-observer")

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        environ_overrides={
            "REMOTE_ADDR": "192.0.2.10",
            "domeye.authenticated_user_id": "existing-user",
            "domeye.authorization_scope": "country_outage_event_read:IR",
        },
    )

    assert response.status_code == 401
    assert calls == []


@pytest.mark.parametrize(
    "user_id",
    [
        None,
        "",
        " internal-history-observer",
        "internal-history-observer ",
        "internal history observer",
        "internal/history-observer",
        "内部观测用户",
        "user\nforged",
        "-must-not-start-with-punctuation",
        "x" * 129,
    ],
)
def test_internal_fixed_history_mode_rejects_unsafe_environment_user_id(
    client, monkeypatch, user_id
):
    calls = _capture_upstream(monkeypatch)
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE",
        INTERNAL_FIXED_HISTORY_MODE,
    )
    if user_id is None:
        monkeypatch.delenv(INTERNAL_USER_ID_ENV, raising=False)
    else:
        monkeypatch.setenv(INTERNAL_USER_ID_ENV, user_id)

    response = client.post(
        "/api/v2/country-outage/reports",
        json=_report_request(),
        headers={
            "X-Domeye-User": "browser-forged-user",
            "X-Domeye-Authorization-Scope":
                "country_outage_event_read:IR",
        },
        environ_overrides={
            "REMOTE_ADDR": "127.0.0.1",
            "REMOTE_USER": "wsgi-user-must-not-rescue-invalid-env",
            TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY:
                "country_outage_event_read:IR",
            "domeye.authenticated_user_id":
                "existing-user-must-not-rescue-invalid-env",
            "domeye.authorization_scope":
                "country_outage_event_read:IR",
        },
    )

    assert response.status_code == 401
    assert calls == []


def test_internal_fixed_history_mode_does_not_touch_other_routes(
    app, monkeypatch
):
    monkeypatch.setenv(
        "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE",
        INTERNAL_FIXED_HISTORY_MODE,
    )
    monkeypatch.setenv(INTERNAL_USER_ID_ENV, "internal-history-observer")

    @app.get("/internal-identity-adapter-probe")
    def internal_identity_adapter_probe():
        return jsonify(
            {
                "user": request.environ.get(
                    "domeye.authenticated_user_id"
                ),
                "scope": request.environ.get(
                    "domeye.authorization_scope"
                ),
            }
        )

    response = app.test_client().get(
        "/internal-identity-adapter-probe",
        headers={
            "X-Domeye-User": "browser-forged-user",
            "X-Domeye-Authorization-Scope": "admin",
        },
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"user": None, "scope": None}
