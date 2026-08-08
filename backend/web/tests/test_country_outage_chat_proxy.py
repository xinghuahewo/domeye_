from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

REFERENCE = "country_outage/2026-02-27 09:12:32/IR/1/r"


class FakeUpstream:
    def __init__(self, payload, status_code=200):
        self.content = json.dumps(payload).encode("utf-8")
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}

    def close(self):
        pass


@unittest.skipUnless(
    importlib.util.find_spec("flask") is not None,
    "当前 Python 环境未安装 Flask；由 backend uv 环境执行",
)
class CountryOutageChatProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["FLASK_CONFIG"] = "testing"
        os.environ["COUNTRY_OUTAGE_AGENT_SHARED_TOKEN"] = (
            "test-only-country-outage-agent-token"
        )
        os.environ["COUNTRY_OUTAGE_AGENT_URL"] = "http://127.0.0.1:28474"
        os.environ["COUNTRY_OUTAGE_AGENT_IDENTITY_MODE"] = "wsgi_remote_user"
        from run import create_app

        cls.app = create_app("testing")

    def client(self):
        value = self.app.test_client()
        value.environ_base["REMOTE_USER"] = "p1-proxy-user"
        value.environ_base[
            "domeye.country_outage_authorization_scope"
        ] = "country_outage_event_read"
        return value

    def test_create_conversation_forwards_only_bound_identity(self):
        calls = []
        payload = {
            "conversation": {
                "schema_version": "country_outage_p1_chat_v1",
                "conversation_id": "conv_proxy",
            },
            "deduplicated": False,
        }

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return FakeUpstream(payload, 201)

        with patch(
            "web.api.v2.country_outage_agent_proxy._request_sidecar",
            side_effect=fake_request,
        ):
            response = self.client().post(
                "/api/v2/country-outage/chat/conversations",
                json={
                    "event_reference": REFERENCE,
                    "publication_id": "publication-proxy",
                    "revision": 1,
                    "idempotency_key": "conversation-proxy-01",
                },
                headers={"Idempotency-Key": "conversation-proxy-01"},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(calls[0][0:2], (
            "POST", "/country-outage/chat/conversations"
        ))
        self.assertEqual(
            calls[0][2]["headers"]["X-Domeye-User"], "p1-proxy-user"
        )
        self.assertEqual(
            calls[0][2]["headers"]["Idempotency-Key"],
            "conversation-proxy-01",
        )
        self.assertEqual(calls[0][2]["json"]["publication_id"], "publication-proxy")

    def test_invalid_reference_and_extra_fields_fail_before_sidecar(self):
        with patch(
            "web.api.v2.country_outage_agent_proxy._request_sidecar"
        ) as request_sidecar:
            response = self.client().post(
                "/api/v2/country-outage/chat/conversations",
                json={
                    "event_reference": "route_leak/not-allowed",
                    "publication_id": "publication-proxy",
                    "revision": 1,
                    "idempotency_key": "conversation-proxy-02",
                    "external_urls": ["https://example.test"],
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_chat_request")
        request_sidecar.assert_not_called()

    def test_turn_forbids_external_evidence_fields(self):
        with patch(
            "web.api.v2.country_outage_agent_proxy._request_sidecar"
        ) as request_sidecar:
            response = self.client().post(
                "/api/v2/country-outage/chat/conversations/conv_proxy/turns",
                json={
                    "question": "查 OONI",
                    "idempotency_key": "turn-proxy-0001",
                    "evidence_mode": "domeye_plus_external",
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("未授权字段", response.get_json()["error"]["message"])
        request_sidecar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
