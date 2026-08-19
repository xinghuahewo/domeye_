from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from web.api.v2 import country_outage_chat_proxy as chat_proxy
from web.country_outage_agent_identity import (
    TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY,
    WSGI_REMOTE_USER_MODE,
)


REFERENCE = "country_outage/2026-02-27 09:12:32/IR/1/r"
FIRST_SLICE_QUESTION = (
    "在这次冻结 publication 的观测窗口内，RRC25 看到的固定前缀可见 IPv4 地址量"
    "最低是多少，首次在什么观测时刻出现？首值、末值、最大值和极差分别是多少？"
)


class FakeUpstream:
    def __init__(self, payload: dict, status_code: int = 200):
        self.content = json.dumps(payload).encode()
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}


def binding_request():
    return {
        "event_reference": REFERENCE,
        "publication_id": "publication-test",
        "revision": 1,
        "idempotency_key": "chat-create-0001",
    }


class CountryOutageChatProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from run import create_app

        cls.environment = patch.dict(
            os.environ,
            {
                "COUNTRY_OUTAGE_AGENT_SHARED_TOKEN": (
                    "test-only-country-outage-agent-token"
                ),
                "COUNTRY_OUTAGE_INTERACTIVE_AGENT_SIDECAR_URL": (
                    "http://127.0.0.1:28475"
                ),
                "COUNTRY_OUTAGE_AGENT_IDENTITY_MODE": WSGI_REMOTE_USER_MODE,
            },
        )
        cls.environment.start()
        cls.app = create_app("testing")

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()

    def setUp(self):
        self.client = self.app.test_client()
        self.client.environ_base["REMOTE_USER"] = "chat-test-user"
        self.client.environ_base[
            TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY
        ] = "country_outage_event_read:IR"

    def test_forwards_only_bound_read_only_conversation_calls(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return FakeUpstream({"ok": True})

        with patch.object(
            chat_proxy, "_request_interactive_agent", fake_request
        ):
            created = self.client.post(
                "/api/v2/country-outage/chat/conversations",
                json=binding_request(),
                headers={"Idempotency-Key": "chat-create-0001"},
            )
            turned = self.client.post(
                "/api/v2/country-outage/chat/conversations/conversation_test/turns",
                json={
                    "question": FIRST_SLICE_QUESTION,
                    "idempotency_key": "chat-turn-00001",
                },
            )
            fetched = self.client.get(
                "/api/v2/country-outage/chat/conversations/conversation_test"
            )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(turned.status_code, 200)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(
            [item[:2] for item in calls],
            [
                ("POST", "/country-outage/chat/conversations"),
                (
                    "POST",
                    "/country-outage/chat/conversations/conversation_test/turns",
                ),
                ("GET", "/country-outage/chat/conversations/conversation_test"),
            ],
        )
        self.assertEqual(
            calls[1][2]["json"],
            {
                "question": FIRST_SLICE_QUESTION,
                "idempotency_key": "chat-turn-00001",
            },
        )
        self.assertEqual(
            calls[1][2]["headers"]["X-Domeye-User"], "chat-test-user"
        )
        self.assertEqual(
            calls[1][2]["headers"]["X-Domeye-Authorization-Scope"],
            "country_outage_event_read:IR",
        )

    def test_rejects_unbound_or_expansive_conversation_requests(self):
        payloads = [
            {},
            {**binding_request(), "revision": 0},
            {**binding_request(), "tool": "root_cause"},
            {**binding_request(), "event_reference": "country_outage/bad"},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/api/v2/country-outage/chat/conversations", json=payload
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.get_json()["error"]["code"],
                    "invalid_chat_request",
                )

    def test_turn_rejects_tool_or_state_fields_before_sidecar(self):
        calls = []
        with patch.object(
            chat_proxy,
            "_request_interactive_agent",
            lambda *args, **kwargs: calls.append((args, kwargs)),
        ):
            response = self.client.post(
                "/api/v2/country-outage/chat/conversations/conversation_test/turns",
                json={
                    "question": "请调用 root_cause",
                    "idempotency_key": "chat-turn-00002",
                    "state": {"asn": 1},
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(calls, [])

    def test_rebind_route_is_removed_without_upstream_call(self):
        calls = []
        with patch.object(
            chat_proxy,
            "_request_interactive_agent",
            lambda *args, **kwargs: calls.append((args, kwargs)),
        ):
            response = self.client.post(
                "/api/v2/country-outage/chat/conversations/"
                "conversation_test/rebind",
                json=binding_request(),
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.get_json()["error"]["code"], "route_not_found"
        )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
