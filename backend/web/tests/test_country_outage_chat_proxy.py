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
CONVERSATION_ID = f"conversation_sha256_{'a' * 64}"
TURN_ID = f"turn_sha256_{'b' * 64}"


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


def public_binding():
    return {
        "event_reference": REFERENCE,
        "event_type": "country_outage",
        "incident_id": "incident-test",
        "publication_id": "publication-test",
        "revision": 1,
        "collector_id": "rrc25",
        "cohort_id": "cohort-test",
        "country_code": "IR",
        "window_start_utc": "2026-02-28T14:00:00Z",
        "window_end_utc": "2026-02-28T15:00:00Z",
        "data_through": "2026-02-28T15:00:00Z",
        "is_final_in_data_range": False,
        "lifecycle_state": "event_end_unknown",
    }


def executing_turn():
    return {
        "turn_id": TURN_ID,
        "turn_number": 1,
        "question": FIRST_SLICE_QUESTION,
        "state": "executing",
        "answer_success": False,
        "workflow_completed": False,
        "created_at": "2026-08-20T10:00:00Z",
    }


def completed_turn(answer_text: str):
    return {
        **executing_turn(),
        "state": "completed",
        "answer_success": True,
        "workflow_completed": True,
        "answer": {
            "schema_version": "domeye_interactive_agent_turn_answer_v2",
            "answerability": "supported",
            "answer_source": "renderer",
            "answer_text": answer_text,
            "basis": {
                "source_label_zh": "RRC25 控制面观测",
                "observed_object_zh": "固定前缀可见 IPv4 地址量",
                "window_start_utc": "2026-02-28T14:00:00Z",
                "window_end_utc": "2026-02-28T15:00:00Z",
                "important_boundary_zh": (
                    "仅代表 RRC25 对固定前缀集合的控制面观测。"
                ),
            },
        },
        "completed_at": "2026-08-20T10:00:03Z",
    }


def failed_turn():
    return {
        **executing_turn(),
        "state": "failed",
        "error": {
            "code": "answer_not_published",
            "message": "本轮未通过回答合同或安全校验，没有发布答案。",
            "retryable": False,
        },
        "completed_at": "2026-08-20T10:00:03Z",
    }


def public_conversation(turns=None):
    return {
        "schema_version": "domeye_interactive_agent_conversation_v2",
        "conversation_id": CONVERSATION_ID,
        "binding": public_binding(),
        "turns": list(turns or []),
        "expires_at": "2026-08-20T10:30:00Z",
        "created_at": "2026-08-20T10:00:00Z",
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
            if path.endswith("/turns"):
                return FakeUpstream(
                    {"turn": executing_turn(), "deduplicated": False},
                    201,
                )
            if method == "POST":
                return FakeUpstream(
                    {
                        "conversation": public_conversation(),
                        "deduplicated": False,
                    },
                    201,
                )
            return FakeUpstream({"conversation": public_conversation()})

        with patch.object(
            chat_proxy, "_request_interactive_agent", fake_request
        ):
            created = self.client.post(
                "/api/v2/country-outage/chat/conversations",
                json=binding_request(),
                headers={"Idempotency-Key": "chat-create-0001"},
            )
            turned = self.client.post(
                f"/api/v2/country-outage/chat/conversations/{CONVERSATION_ID}/turns",
                json={
                    "question": FIRST_SLICE_QUESTION,
                    "idempotency_key": "chat-turn-00001",
                },
            )
            fetched = self.client.get(
                f"/api/v2/country-outage/chat/conversations/{CONVERSATION_ID}"
            )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(turned.status_code, 201)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(
            [item[:2] for item in calls],
            [
                ("POST", "/country-outage/chat/conversations"),
                (
                    "POST",
                    f"/country-outage/chat/conversations/{CONVERSATION_ID}/turns",
                ),
                (
                    "GET",
                    f"/country-outage/chat/conversations/{CONVERSATION_ID}",
                ),
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
                f"/api/v2/country-outage/chat/conversations/{CONVERSATION_ID}/turns",
                json={
                    "question": "请调用 root_cause",
                    "idempotency_key": "chat-turn-00002",
                    "state": {"asn": 1},
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(calls, [])

    def test_preserves_guarded_answer_text_without_trimming_or_rewriting(self):
        answer_text = "  最低值为 9,577,728。\n边界说明保持原样。  "
        upstream = FakeUpstream(
            {"turn": completed_turn(answer_text), "deduplicated": False}
        )
        with patch.object(
            chat_proxy,
            "_request_interactive_agent",
            lambda *args, **kwargs: upstream,
        ):
            response = self.client.post(
                f"/api/v2/country-outage/chat/conversations/{CONVERSATION_ID}/turns",
                json={
                    "question": FIRST_SLICE_QUESTION,
                    "idempotency_key": "chat-turn-00003",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, upstream.content)
        self.assertEqual(
            response.get_json()["turn"]["answer"]["answer_text"],
            answer_text,
        )

    def test_fails_closed_on_extra_internal_response_fields(self):
        turn = completed_turn("最低值为 9,577,728。")
        turn["answer"]["candidate_id"] = "manifest:sha256:do-not-leak"
        with patch.object(
            chat_proxy,
            "_request_interactive_agent",
            lambda *args, **kwargs: FakeUpstream(
                {"turn": turn, "deduplicated": False}
            ),
        ):
            response = self.client.post(
                f"/api/v2/country-outage/chat/conversations/{CONVERSATION_ID}/turns",
                json={
                    "question": FIRST_SLICE_QUESTION,
                    "idempotency_key": "chat-turn-00004",
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "interactive_agent_contract_violation",
        )
        self.assertNotIn("do-not-leak", response.get_data(as_text=True))

    def test_fails_closed_when_success_answer_has_no_human_basis(self):
        turn = completed_turn("最低值为 9,577,728。")
        del turn["answer"]["basis"]
        with patch.object(
            chat_proxy,
            "_request_interactive_agent",
            lambda *args, **kwargs: FakeUpstream(
                {"turn": turn, "deduplicated": False}
            ),
        ):
            response = self.client.post(
                f"/api/v2/country-outage/chat/conversations/{CONVERSATION_ID}/turns",
                json={
                    "question": FIRST_SLICE_QUESTION,
                    "idempotency_key": "chat-turn-00005",
                },
            )

        self.assertEqual(response.status_code, 502)

    def test_accepts_only_the_public_turn_error_vocabulary(self):
        turn = failed_turn()
        with patch.object(
            chat_proxy,
            "_request_interactive_agent",
            lambda *args, **kwargs: FakeUpstream(
                {"conversation": public_conversation([turn])}
            ),
        ):
            accepted = self.client.get(
                f"/api/v2/country-outage/chat/conversations/{CONVERSATION_ID}"
            )
        self.assertEqual(accepted.status_code, 200)

        turn["error"] = {
            "code": "renderer_provider_failed",
            "message": "provider request failed at a private endpoint",
            "retryable": True,
        }
        with patch.object(
            chat_proxy,
            "_request_interactive_agent",
            lambda *args, **kwargs: FakeUpstream(
                {"conversation": public_conversation([turn])}
            ),
        ):
            blocked = self.client.get(
                f"/api/v2/country-outage/chat/conversations/{CONVERSATION_ID}"
            )
        self.assertEqual(blocked.status_code, 502)
        self.assertNotIn(
            "private endpoint",
            blocked.get_data(as_text=True),
        )

    def test_does_not_expose_upstream_exception_details(self):
        secret_detail = "http://127.0.0.1:28476/private failed with token"
        with patch.object(
            chat_proxy,
            "_request_interactive_agent",
            side_effect=RuntimeError(secret_detail),
        ):
            response = self.client.post(
                "/api/v2/country-outage/chat/conversations",
                json=binding_request(),
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["error"]["message"],
            "回答服务暂时不可用，请稍后重试",
        )
        self.assertNotIn(secret_detail, response.get_data(as_text=True))

    def test_replaces_upstream_error_text_with_a_stable_public_message(self):
        secret_detail = "candidate digest mismatch at /private/runtime"
        with patch.object(
            chat_proxy,
            "_request_interactive_agent",
            lambda *args, **kwargs: FakeUpstream(
                {
                    "error": {
                        "code": "invalid_request",
                        "message": secret_detail,
                        "retryable": False,
                    }
                },
                400,
            ),
        ):
            response = self.client.post(
                "/api/v2/country-outage/chat/conversations",
                json=binding_request(),
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"],
            {
                "code": "invalid_request",
                "message": "当前请求超出此回答功能支持的范围",
                "retryable": False,
            },
        )
        self.assertNotIn(secret_detail, response.get_data(as_text=True))

    def test_does_not_publish_an_internal_verifier_route(self):
        calls = []
        with patch.object(
            chat_proxy,
            "_request_interactive_agent",
            lambda *args, **kwargs: calls.append((args, kwargs)),
        ):
            response = self.client.get(
                (
                    "/api/v2/country-outage/chat/internal/conversations/"
                    f"{CONVERSATION_ID}/turns/{TURN_ID}"
                )
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(calls, [])

if __name__ == "__main__":
    unittest.main()
