from __future__ import annotations

import os
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from web.api.v2 import country_outage_investigations as api
from web.country_outage_agent_identity import (
    TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY,
    WSGI_REMOTE_USER_MODE,
)


REFERENCE = "country_outage/2026-02-27 09:12:32/IR/1/r"
DIGEST = "sha256:" + "a" * 64


class RuntimeDomainError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retryable = False


class FakeInvestigationRuntime:
    def __init__(self):
        self.calls: list[tuple] = []
        self.owner = "w5-test-user"

    def _own(self, principal, investigation_id="inv_1"):
        if investigation_id != "inv_1" or principal["user_id"] != self.owner:
            raise RuntimeDomainError(404, "investigation_not_found", "调查不存在")

    def create_investigation(self, principal, body):
        self.calls.append(("create", principal, body))
        self.owner = principal["user_id"]
        return {"investigation": {"investigation_id": "inv_1", "state": "admitted"}}, 201

    def get_investigation(self, principal, investigation_id):
        self._own(principal, investigation_id)
        self.calls.append(("get", investigation_id))
        return {"investigation": {"investigation_id": investigation_id, "state": "running"}}

    def start_investigation(self, principal, investigation_id, body):
        self._own(principal, investigation_id)
        self.calls.append(("start", investigation_id, body))
        return {"accepted": True}, 202

    def cancel_investigation(self, principal, investigation_id, body):
        self._own(principal, investigation_id)
        self.calls.append(("cancel", investigation_id, body))
        return {"accepted": True}, 202

    def cancel_node(self, principal, investigation_id, node_id, body):
        self._own(principal, investigation_id)
        self.calls.append(("cancel_node", node_id, body))
        return {"accepted": True}, 202

    def rerun_node(self, principal, investigation_id, node_id, body):
        self._own(principal, investigation_id)
        if body["expected_investigation_revision"] != 2:
            raise RuntimeDomainError(409, "revision_conflict", "调查 revision 已变化")
        self.calls.append(("rerun", node_id, body))
        return {"accepted": True, "revision": 3}, 202

    def create_turn(self, principal, investigation_id, body):
        self._own(principal, investigation_id)
        self.calls.append(("turn", body))
        return {"accepted": True, "turn": {"answer": "未版本化内部回答不得透传"}}, 202

    def get_result_set(self, principal, investigation_id, result_set_id, result_set_revision, query):
        self._own(principal, investigation_id)
        if result_set_id != "rs_1" or result_set_revision != 2:
            raise RuntimeDomainError(404, "result_set_not_found", "ResultSet 不属于该调查 revision")
        self.calls.append(("result", query))
        return {"result_set_id": result_set_id, "result_set_revision": 2, "members": []}

    def get_evidence_graph(self, principal, investigation_id, graph_revision):
        self._own(principal, investigation_id)
        self.calls.append(("graph", graph_revision))
        return {
            "graph_revision": graph_revision,
            "nodes": [{
                "node_id": "egn_" + "a" * 64,
                "node_type": "fact",
                "producer_plan_node_id": "node_1",
                "identity_digest": "b" * 64,
                "payload": {"cause": "任意自报字段不得成为第二业务 API"},
                "payload_digest": "c" * 64,
                "evidence_refs": ["evidence:1"],
                "producer_receipt_digest": "d" * 64,
                "forged_claim": "national_user_impact",
            }],
            "edges": [],
            "forged_claim": "production_deployed",
        }

    def get_receipts(self, principal, investigation_id, query):
        self._own(principal, investigation_id)
        self.calls.append(("receipts", query))
        return {"receipts": [], "next_cursor": None}

    def create_export(self, principal, investigation_id, body):
        self._own(principal, investigation_id)
        if body["result_set_revision"] != 2:
            raise RuntimeDomainError(409, "stale_result_set_revision", "不得导出旧 ResultSet revision")
        self.calls.append(("export", body))
        return {"export": {"export_id": "exp_1", "state": "requested"}}, 202

    def get_export(self, principal, investigation_id, export_id):
        self._own(principal, investigation_id)
        return {"export": {"export_id": export_id, "state": "committed"}}

    def get_export_artifact(self, principal, investigation_id, export_id):
        self._own(principal, investigation_id)
        return {
            "content": b"asn,status\n64500,partial\n",
            "content_type": "text/csv; charset=utf-8",
            "filename": "result-set.csv",
            "sha256": "sha256:" + "b" * 64,
        }


def mutation_body(revision=2):
    return {
        "idempotency_key": "w5-action-0001",
        "expected_investigation_revision": revision,
        "expected_current_digest": DIGEST,
    }


class CountryOutageP2S1InvestigationApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from run import create_app

        cls.environment = patch.dict(
            os.environ,
            {"COUNTRY_OUTAGE_AGENT_IDENTITY_MODE": WSGI_REMOTE_USER_MODE},
        )
        cls.environment.start()
        cls.app = create_app("testing")

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()

    def setUp(self):
        self.runtime = FakeInvestigationRuntime()
        api.configure_country_outage_p2_s1_investigation_runtime(lambda: self.runtime)
        self.client = self.app.test_client()
        self.client.environ_base["REMOTE_USER"] = "w5-test-user"
        self.client.environ_base[TRUSTED_AUTHORIZATION_SCOPE_ENVIRON_KEY] = (
            "country_outage_event_read:IR"
        )

    def tearDown(self):
        api.configure_country_outage_p2_s1_investigation_runtime(None)

    def post(self, path, body):
        return self.client.post(
            path,
            json=body,
            headers={"Idempotency-Key": body["idempotency_key"]},
        )

    def test_complete_local_journey_routes_are_thin_and_identity_bound(self):
        create_body = {
            "event_reference": REFERENCE,
            "publication_id": "publication-test",
            "revision": 1,
            "goal": "给出事件全景、一个精确时点下钻和证据一致性",
            "idempotency_key": "w5-create-0001",
        }
        self.assertEqual(
            self.post("/api/v2/country-outage/investigations", create_body).status_code,
            201,
        )
        self.assertEqual(
            self.client.get("/api/v2/country-outage/investigations/inv_1").status_code,
            200,
        )
        for suffix in ("start", "cancel"):
            body = {**mutation_body(), "idempotency_key": f"w5-{suffix}-0001"}
            self.assertEqual(
                self.post(f"/api/v2/country-outage/investigations/inv_1/{suffix}", body).status_code,
                202,
            )
        node_body = {**mutation_body(), "idempotency_key": "w5-node-cancel-1"}
        self.assertEqual(
            self.post(
                "/api/v2/country-outage/investigations/inv_1/nodes/node_1/cancel",
                node_body,
            ).status_code,
            202,
        )
        rerun_body = {**mutation_body(), "idempotency_key": "w5-node-rerun-1"}
        self.assertEqual(
            self.post(
                "/api/v2/country-outage/investigations/inv_1/nodes/node_1/reruns",
                rerun_body,
            ).status_code,
            202,
        )
        turn_body = {
            **mutation_body(),
            "idempotency_key": "w5-turn-00001",
            "question": "展开那个时间点",
            "anchor": {"node_id": "node_1", "node_revision": 2, "selection_ref": "timepoint:peak"},
        }
        self.assertEqual(
            self.post("/api/v2/country-outage/investigations/inv_1/turns", turn_body).status_code,
            202,
        )
        self.assertEqual(
            self.client.get(
                "/api/v2/country-outage/investigations/inv_1/result-sets/rs_1/revisions/2?page_size=20"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                "/api/v2/country-outage/investigations/inv_1/evidence-graphs/2"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                "/api/v2/country-outage/investigations/inv_1/receipts?kind=tool"
            ).status_code,
            200,
        )
        export_body = {
            **mutation_body(),
            "idempotency_key": "w5-export-0001",
            "result_set_id": "rs_1",
            "result_set_revision": 2,
            "format": "csv",
        }
        self.assertEqual(
            self.post("/api/v2/country-outage/investigations/inv_1/exports", export_body).status_code,
            202,
        )
        artifact = self.client.get(
            "/api/v2/country-outage/investigations/inv_1/exports/exp_1/artifact"
        )
        self.assertEqual(artifact.status_code, 200)
        self.assertEqual(artifact.headers["X-Content-Type-Options"], "nosniff")
        self.assertTrue(artifact.headers["X-Content-SHA256"].startswith("sha256:"))

    def test_rejects_hidden_fan_out_and_missing_explicit_anchor_before_runtime(self):
        before = list(self.runtime.calls)
        invalid_create = {
            "event_reference": REFERENCE,
            "publication_id": "publication-test",
            "revision": 1,
            "goal": "逐成员执行",
            "idempotency_key": "w5-create-0002",
            "unit_ids": ["PLAN-CAP-02"],
        }
        self.assertEqual(
            self.post("/api/v2/country-outage/investigations", invalid_create).status_code,
            400,
        )
        no_anchor = {
            **mutation_body(),
            "idempotency_key": "w5-turn-00002",
            "question": "展开那个时间点",
        }
        response = self.post(
            "/api/v2/country-outage/investigations/inv_1/turns", no_anchor
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.runtime.calls, before)

    def test_requires_matching_idempotency_header_and_rejects_stale_cas(self):
        body = {**mutation_body(revision=1), "idempotency_key": "w5-rerun-stale1"}
        mismatch = self.client.post(
            "/api/v2/country-outage/investigations/inv_1/nodes/node_1/reruns",
            json=body,
            headers={"Idempotency-Key": "w5-rerun-other1"},
        )
        self.assertEqual(mismatch.status_code, 400)
        stale = self.post(
            "/api/v2/country-outage/investigations/inv_1/nodes/node_1/reruns",
            body,
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()["error"]["code"], "revision_conflict")

    def test_masks_cross_owner_and_cross_investigation_result_sets(self):
        self.client.environ_base["REMOTE_USER"] = "another-user"
        hidden = self.client.get("/api/v2/country-outage/investigations/inv_1")
        self.assertEqual(hidden.status_code, 404)
        self.client.environ_base["REMOTE_USER"] = "w5-test-user"
        cross = self.client.get(
            "/api/v2/country-outage/investigations/inv_1/result-sets/rs_other/revisions/2"
        )
        self.assertEqual(cross.status_code, 404)

    def test_rejects_old_result_revision_export(self):
        body = {
            **mutation_body(),
            "idempotency_key": "w5-export-old01",
            "result_set_id": "rs_1",
            "result_set_revision": 1,
            "format": "json",
        }
        response = self.post(
            "/api/v2/country-outage/investigations/inv_1/exports", body
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["error"]["code"], "stale_result_set_revision"
        )

    def test_public_projection_drops_unversioned_turn_and_graph_payload(self):
        turn_body = {
            **mutation_body(),
            "idempotency_key": "w5-turn-project1",
            "question": "只读已提交节点",
            "anchor": {"node_id": "node_1", "node_revision": 2},
        }
        turn = self.post(
            "/api/v2/country-outage/investigations/inv_1/turns", turn_body
        )
        self.assertEqual(turn.status_code, 202)
        self.assertEqual(turn.get_json(), {"accepted": True})

        graph = self.client.get(
            "/api/v2/country-outage/investigations/inv_1/evidence-graphs/2"
        )
        self.assertEqual(graph.status_code, 200)
        payload = graph.get_json()
        self.assertNotIn("forged_claim", payload)
        self.assertNotIn("payload", payload["nodes"][0])
        self.assertNotIn("forged_claim", payload["nodes"][0])
        self.assertEqual(payload["nodes"][0]["payload_digest"], "c" * 64)

    def test_openapi_w5_success_contracts_are_closed_and_typed(self):
        contract = json.loads(
            (BACKEND_ROOT.parent / "contracts" / "openapi.json").read_text(
                encoding="utf-8"
            )
        )
        schemas = contract["components"]["schemas"]
        closed = (
            "CountryOutageInvestigationIdentity",
            "CountryOutageInvestigationPlanNode",
            "CountryOutageInvestigationPlan",
            "CountryOutageInvestigationResultSetRef",
            "CountryOutageInvestigationNode",
            "CountryOutageInvestigationLimitation",
            "CountryOutageInvestigation",
            "CountryOutageInvestigationEnvelope",
            "CountryOutageInvestigationActionResponse",
            "CountryOutageInvestigationResultSet",
            "CountryOutageInvestigationEvidenceNode",
            "CountryOutageInvestigationEvidenceEdge",
            "CountryOutageInvestigationEvidenceGraph",
            "CountryOutageInvestigationReceipt",
            "CountryOutageInvestigationReceiptPage",
            "CountryOutageInvestigationExport",
            "CountryOutageInvestigationExportEnvelope",
            "CountryOutageInvestigationExportActionResponse",
        )
        for schema_name in closed:
            with self.subTest(schema=schema_name):
                self.assertIs(
                    schemas[schema_name].get("additionalProperties"),
                    False,
                    f"{schema_name} 必须拒绝任意自报字段",
                )

        member = schemas["CountryOutageInvestigationResultSet"]["properties"]["members"]
        self.assertEqual(
            member["items"]["$ref"],
            "#/components/schemas/CountryOutageInvestigationResultSetMember",
        )
        self.assertEqual(
            len(schemas["CountryOutageInvestigationResultSetMember"]["oneOf"]),
            6,
        )
        for index in range(7, 13):
            self.assertIs(
                schemas[f"CountryOutageInvestigationTool{index:02d}Member"][
                    "additionalProperties"
                ],
                False,
            )

        graph = schemas["CountryOutageInvestigationEvidenceGraph"]
        self.assertEqual(
            graph["properties"]["nodes"]["items"]["$ref"],
            "#/components/schemas/CountryOutageInvestigationEvidenceNode",
        )
        self.assertEqual(
            graph["properties"]["edges"]["items"]["$ref"],
            "#/components/schemas/CountryOutageInvestigationEvidenceEdge",
        )
        self.assertNotIn(
            "payload", schemas["CountryOutageInvestigationEvidenceNode"]["properties"]
        )
        self.assertNotIn(
            "parameters", schemas["CountryOutageInvestigationPlanNode"]["properties"]
        )
        self.assertNotIn(
            "object_digest", schemas["CountryOutageInvestigationResultSetRef"]["properties"]
        )
        self.assertEqual(
            schemas["CountryOutageInvestigationReceiptPage"]["properties"][
                "receipts"
            ]["items"]["$ref"],
            "#/components/schemas/CountryOutageInvestigationReceipt",
        )

        artifact_json = contract["paths"][
            "/api/v2/country-outage/investigations/{investigation_id}/exports/{export_id}/artifact"
        ]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(
            artifact_json["items"]["$ref"],
            "#/components/schemas/CountryOutageInvestigationResultSetMember",
        )


if __name__ == "__main__":
    unittest.main()
