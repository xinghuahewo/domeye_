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

    def test_runtime_v2_single_turn_forwards_only_controlled_s1_request(self):
        calls = []
        payload = {
            "schema_version": "country_outage_p1_single_turn_v2",
            "answerability": "partial",
            "answer_text": "确定性事件概览",
            "evidence": [],
            "limitations": [],
            "unknowns": [],
        }

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return FakeUpstream(payload, 200)

        with patch(
            "web.api.v2.country_outage_agent_proxy._request_sidecar",
            side_effect=fake_request,
        ):
            response = self.client().post(
                "/api/v2/country-outage/runtime-v2/single-turn",
                json={
                    "event_reference": REFERENCE,
                    "publication_id": "publication-runtime-v2",
                    "revision": 1,
                    "controlled_goal": "event_summary",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[0][0:2], (
            "POST", "/country-outage/runtime-v2/single-turn"
        ))
        self.assertEqual(
            calls[0][2]["headers"]["X-Domeye-Authorization-Scope"],
            "country_outage_event_read",
        )
        self.assertEqual(
            calls[0][2]["json"]["controlled_goal"], "event_summary"
        )

    def test_runtime_v2_rejects_open_goal_and_extra_fields_before_sidecar(self):
        with patch(
            "web.api.v2.country_outage_agent_proxy._request_sidecar"
        ) as request_sidecar:
            response = self.client().post(
                "/api/v2/country-outage/runtime-v2/single-turn",
                json={
                    "event_reference": REFERENCE,
                    "publication_id": "publication-runtime-v2",
                    "revision": 1,
                    "controlled_goal": "root_cause_analysis",
                    "external_urls": ["https://example.test"],
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "invalid_runtime_v2_request",
        )
        request_sidecar.assert_not_called()

    def test_runtime_v2_semantic_turn_forwards_open_question_without_tool_fields(self):
        calls = []
        payload = {
            "schema_version": "country_outage_p1_semantic_turn_v2",
            "answerability": "partial",
            "results": [],
            "evidence": [],
        }

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return FakeUpstream(payload, 200)

        with patch(
            "web.api.v2.country_outage_agent_proxy._request_sidecar",
            side_effect=fake_request,
        ):
            response = self.client().post(
                "/api/v2/country-outage/runtime-v2/semantic-turn",
                json={
                    "event_reference": REFERENCE,
                    "publication_id": "publication-runtime-v2",
                    "revision": 1,
                    "question": "现在还有多少前缀不可见，是不是全国都断了？",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[0][0:2], (
            "POST", "/country-outage/runtime-v2/semantic-turn"
        ))
        self.assertEqual(
            calls[0][2]["json"]["question"],
            "现在还有多少前缀不可见，是不是全国都断了？",
        )
        self.assertNotIn("tools", calls[0][2]["json"])
        self.assertNotIn("external_urls", calls[0][2]["json"])

    def test_runtime_v2_semantic_turn_rejects_tool_and_external_fields(self):
        with patch(
            "web.api.v2.country_outage_agent_proxy._request_sidecar"
        ) as request_sidecar:
            response = self.client().post(
                "/api/v2/country-outage/runtime-v2/semantic-turn",
                json={
                    "event_reference": REFERENCE,
                    "publication_id": "publication-runtime-v2",
                    "revision": 1,
                    "question": "为什么断网",
                    "tool": "root_cause_analysis",
                    "external_urls": ["https://example.test"],
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "invalid_runtime_v2_request",
        )
        request_sidecar.assert_not_called()

    def test_runtime_v2_s3_conversation_and_turn_use_separate_bounded_paths(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return FakeUpstream({"ok": True}, 201)

        with patch(
            "web.api.v2.country_outage_agent_proxy._request_sidecar",
            side_effect=fake_request,
        ):
            created = self.client().post(
                "/api/v2/country-outage/runtime-v2/conversations",
                json={
                    "event_reference": REFERENCE,
                    "publication_id": "publication-runtime-v2",
                    "revision": 1,
                    "idempotency_key": "runtime-v2-create-01",
                },
                headers={"Idempotency-Key": "runtime-v2-create-01"},
            )
            turn = self.client().post(
                "/api/v2/country-outage/runtime-v2/conversations/p1v2_abc/turns",
                json={
                    "question": "到最后还剩多少",
                    "idempotency_key": "runtime-v2-turn-0001",
                },
                headers={"Idempotency-Key": "runtime-v2-turn-0001"},
            )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(turn.status_code, 201)
        self.assertEqual(calls[0][0:2], (
            "POST", "/country-outage/runtime-v2/conversations"
        ))
        self.assertEqual(calls[1][0:2], (
            "POST",
            "/country-outage/runtime-v2/conversations/p1v2_abc/turns",
        ))
        self.assertEqual(
            calls[1][2]["headers"]["Idempotency-Key"],
            "runtime-v2-turn-0001",
        )
        self.assertNotIn("state", calls[1][2]["json"])
        self.assertNotIn("tools", calls[1][2]["json"])

    def test_runtime_v2_s3_rejects_client_state_and_tools_before_sidecar(self):
        with patch(
            "web.api.v2.country_outage_agent_proxy._request_sidecar"
        ) as request_sidecar:
            response = self.client().post(
                "/api/v2/country-outage/runtime-v2/conversations/p1v2_abc/turns",
                json={
                    "question": "继续",
                    "idempotency_key": "runtime-v2-turn-0002",
                    "dialog_state": {"metric": "interrupted_prefix_count"},
                    "tools": ["root_cause_analysis"],
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "invalid_runtime_v2_request",
        )
        request_sidecar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
