from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
from jsonschema import Draft202012Validator

from backend.services.country_outage_p2_s1_contract_runtime import canonical_json, digest_prefixed, load_frozen_contract
from backend.services import country_outage_p2_s1_operators as operators
from backend.services.country_outage_p2_s1_evidence_graph import validate_evidence_references
from backend.services.country_outage_p2_s1_investigation_runtime import (
    TrustedFixturePlanningGroundingPort,
    W5InvestigationError,
    build_local_fixture_runtime,
)
from backend.services.country_outage_p2_s1_registry_dispatcher import (
    W5RegistryError,
    _implementation_digest_for_handler,
)
from backend.services.country_outage_p2_s1_result_set import validate_result_set
from backend.services.country_outage_p2_s1_trusted_store import ContentAddressedStore, W5StoreError


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
EVENT_REFERENCE = "country_outage/2026-02-27 00:00:00/IR/1/r"
PRINCIPAL = {"user_id": "w5-runtime-user", "authorization_scope": "country_outage_event_read:IR"}


def node(node_id: str, unit_id: str, depends_on=(), *, requiredness="required", parameters=None, bindings=None, mode="hard"):
    return {
        "node_id": node_id,
        "unit_id": unit_id,
        "depends_on": list(depends_on),
        "dependency_mode": mode,
        "requiredness": requiredness,
        "parameters": parameters or {},
        "input_bindings": bindings or [],
    }


def trusted_projection_catalog(fixture_id: str, goal: str, execution_recipe):
    capabilities = {
        "GATE-01": "validate.identity", "GATE-02": "validate.permission", "GATE-03": "validate.registry_snapshot",
        "BOUNDARY-01": "respond.boundary", "TOOL-07": "read.fixed_cohort_members", "TOOL-11": "read.materialized_route_states_at_time",
        "OP-29": "time.evidence_relation", "OP-37": "classify.evidence_consistency",
        "OP-30": "vp.visibility_consistency", "OP-31": "vp.origin_consistency",
        "OP-32": "vp.path_consistency", "OP-33": "join.new_prefix_route_state",
        "PLAN-CAP-02": "plan.dynamic_fanout",
    }
    recipe_base = {
        "schema_version": "country_outage_p2_s1_w5_grounded_execution_recipe_v1", "template_group_id": fixture_id,
        "template_group_digest": digest_prefixed({"fixture_id": fixture_id, "execution_recipe": execution_recipe}),
        "fixture_id": fixture_id, "question_id": fixture_id, "question_digest": digest_prefixed(fixture_id),
        "goal_digest": digest_prefixed(goal), "binding_summary_digest": digest_prefixed({"fixture_id": fixture_id}),
        "semantic_plan_digest": digest_prefixed({"capabilities": [capabilities[item["unit_id"]] for item in execution_recipe]}),
        "semantic_capability_ids": [capabilities[item["unit_id"]] for item in execution_recipe],
        "plan_id": "fixture-plan-" + fixture_id, "plan_revision": 1,
        "registry_snapshot_id": "host_runtime_resolution_required", "registry_snapshot_digest": "sha256:" + "0" * 64,
        "nodes": [{
            "node_id": item["node_id"], "depends_on": item["depends_on"], "dependency_mode": item["dependency_mode"],
            "requiredness": item["requiredness"], "unit_id": item["unit_id"], "atomic_capability_id": capabilities[item["unit_id"]],
            "parameters": item["parameters"], "input_binding_sources": item["input_bindings"],
        } for item in execution_recipe],
    }
    recipe = {**recipe_base, "recipe_digest": digest_prefixed(recipe_base)}
    projection_base = {
        "schema_version": "country_outage_p2_grounding_plan_projection_v2", "plan_id": recipe["plan_id"], "plan_revision": 1,
        "admitted_capability_ids": recipe["semantic_capability_ids"], "registry_snapshot_id": recipe["registry_snapshot_id"],
        "registry_snapshot_digest": recipe["registry_snapshot_digest"], "effective_teacher_required": True,
        "degraded_authorization_digest": None, "grounded_execution_recipe": recipe,
    }
    projection = {**projection_base, "grounding_plan_projection_digest": digest_prefixed(projection_base)}
    entry_base = {"fixture_id": fixture_id, "goal": goal, "trusted_grounding_plan_projection": projection}
    return {fixture_id: {**entry_base, "catalog_entry_digest": digest_prefixed(entry_base)}}


class CountryOutageP2S1RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.plan = [
            node("gate01", "GATE-01"),
            node("gate02", "GATE-02"),
            node("gate03", "GATE-03"),
            node("tool07", "TOOL-07", ("gate01", "gate02", "gate03"), parameters={"page_size": 1}),
            node("boundary", "BOUNDARY-01", ("tool07",), requiredness="boundary_only", mode="soft"),
        ]
        self.runtime = build_local_fixture_runtime(
            REPOSITORY_ROOT,
            self.temporary.name,
            planning_grounding_port=TrustedFixturePlanningGroundingPort(
                fixture_catalog=trusted_projection_catalog("panorama", "执行国家事件全景、证据闭包与导出", self.plan), fixture_id="panorama",
            ),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, suffix="0001"):
        return self.runtime.create_investigation(
            PRINCIPAL,
            {
                "event_reference": EVENT_REFERENCE,
                "publication_id": "publication_fixture_ir_1",
                "revision": 1,
                "goal": "执行国家事件全景、证据闭包与导出",
                "idempotency_key": f"create-{suffix}",
            },
        )[0]["investigation"]

    @staticmethod
    def design_hook():
        path = REPOSITORY_ROOT / ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py"
        spec = importlib.util.spec_from_file_location("country_outage_p2_s1_design_alignment", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def _actual_design_validation_inputs(self, suffix="validator"):
        created = self.create(suffix)
        completed = self.runtime.start_investigation(
            PRINCIPAL, created["investigation_id"], self.cas(created, f"start-{suffix}")
        )[0]["investigation"]
        plan = self.runtime.store.list_json("design-investigation-plan")[-1]
        result_set = self.runtime.store.list_json("result-set")[-1]
        graph = self.runtime.store.list_json("evidence-graph")[-1]
        graph_context = self.runtime.store.list_json("graph-validation-context")[-1]
        receipts = {
            item["receipt_digest"]: item for item in self.runtime.store.list_json("receipt")
            if isinstance(item.get("receipt_digest"), str)
        }
        admissions = {
            item["receipt_digest"]: item for item in self.runtime.store.list_json("plan-admission-receipt")
        }
        members = validate_result_set(result_set, self.runtime.store)
        return completed, plan, result_set, graph, graph_context, receipts, admissions, members

    @staticmethod
    def cas(view, key):
        return {
            "expected_investigation_revision": view["investigation_revision"],
            "expected_current_digest": view["current_digest"],
            "idempotency_key": key,
        }

    def test_complex_tool_result_graph_export_cas_and_rerun_closure(self):
        created = self.create()
        completed = self.runtime.start_investigation(
            PRINCIPAL, created["investigation_id"], self.cas(created, "start-0001")
        )[0]["investigation"]
        self.assertEqual(completed["status"], "completed", completed["nodes"])
        self.assertEqual(completed["investigation_revision"], 3)
        result_ref = next(ref for item in completed["nodes"] for ref in item["result_set_refs"])
        page = self.runtime.get_result_set(
            PRINCIPAL, completed["investigation_id"], result_ref["result_set_id"], 1,
            {"page_size": 1, "page_token": None},
        )
        self.assertEqual(page["returned_count"], 1)
        stored_result_set = self.runtime.store.get_json("result-set", result_ref["object_digest"])
        self.assertEqual(list(Draft202012Validator(load_frozen_contract("result-set")).iter_errors(stored_result_set)), [])
        self.assertEqual(len(validate_result_set(stored_result_set, self.runtime.store)), stored_result_set["returned_count"])
        result_receipts = {
            item.get("receipt_digest"): item for item in self.runtime.store.list_json("receipt")
        }
        self.assertEqual(result_receipts[stored_result_set["query_receipt_digest"]]["receipt_kind"], "query")
        self.assertTrue(all(result_receipts[item["page_receipt_digest"]]["receipt_kind"] == "page" for item in stored_result_set["page_manifest"]))
        self.assertEqual(result_receipts[stored_result_set["freeze_receipt_digest"]]["receipt_kind"], "freeze")
        graph = self.runtime.get_evidence_graph(PRINCIPAL, completed["investigation_id"], 1)
        self.assertEqual(graph["graph_state"], "committed")
        graph_ref = next(item for item in self.runtime.store.list_json("investigation") if item.get("investigation_revision") == 3)["evidence_graph_refs"][-1]
        stored_graph = self.runtime.store.get_json("evidence-graph", graph_ref["object_digest"])
        self.assertEqual(list(Draft202012Validator(load_frozen_contract("evidence-graph")).iter_errors(stored_graph)), [])
        validate_evidence_references(stored_graph)
        self.runtime.graphs.validate_trusted_closure(stored_graph)
        export = self.runtime.create_export(
            PRINCIPAL,
            completed["investigation_id"],
            {
                **self.cas(completed, "export-0001"),
                "result_set_id": result_ref["result_set_id"],
                "result_set_revision": 1,
                "format": "csv",
            },
        )[0]
        artifact = self.runtime.get_export_artifact(PRINCIPAL, completed["investigation_id"], export["export"]["export_id"])
        self.assertTrue(artifact["content"].startswith(b"afi,"))
        with self.assertRaises(W5InvestigationError) as stale:
            self.runtime.rerun_node(
                PRINCIPAL, completed["investigation_id"], "tool07", self.cas(completed, "rerun-stale")
            )
        self.assertEqual(stale.exception.code, "compare_and_swap_conflict")
        current = export["investigation"]
        rerun = self.runtime.rerun_node(
            PRINCIPAL, current["investigation_id"], "tool07", self.cas(current, "rerun-0001")
        )[0]["investigation"]
        self.assertEqual(rerun["status"], "completed")
        self.assertEqual(rerun["evidence_graph_revision"], 2)
        self.assertEqual(next(item for item in rerun["nodes"] if item["node_id"] == "gate01")["state"], "reused")

    def test_tool11_exact_time_route_state_journey_commits_result_graph_and_export(self):
        goal = "执行 exact-time 路由状态下钻、证据闭包与导出"
        execution_recipe = [
            node("gate01", "GATE-01"),
            node("gate02", "GATE-02"),
            node("gate03", "GATE-03"),
            node(
                "tool11",
                "TOOL-11",
                ("gate01", "gate02", "gate03"),
                parameters={
                    "state_point_utc": "2026-02-27T00:05:00Z",
                    "prefix": "109.74.224.0/20",
                    "afi": 4,
                    "route_observation_key": "rrc25:vp-a:peer-a:109.74.224.0/20:ipv4",
                    "peer_asn_direction_id": "rrc25:64500",
                    "vp_id": "vp-a",
                    "peer_id": "peer-a",
                    "visibility": "visible",
                    "origin_asn": 58224,
                    "page_size": 1,
                },
            ),
            node("boundary", "BOUNDARY-01", ("tool11",), requiredness="boundary_only", mode="soft"),
        ]
        runtime = build_local_fixture_runtime(
            REPOSITORY_ROOT,
            Path(self.temporary.name) / "tool11",
            planning_grounding_port=TrustedFixturePlanningGroundingPort(
                fixture_catalog=trusted_projection_catalog("exact-time", goal, execution_recipe),
                fixture_id="exact-time",
            ),
        )
        created = runtime.create_investigation(
            PRINCIPAL,
            {
                "event_reference": EVENT_REFERENCE,
                "publication_id": "publication_fixture_ir_1",
                "revision": 1,
                "goal": goal,
                "idempotency_key": "create-tool11-exact-time",
            },
        )[0]["investigation"]
        completed = runtime.start_investigation(
            PRINCIPAL,
            created["investigation_id"],
            {
                "expected_investigation_revision": created["investigation_revision"],
                "expected_current_digest": created["current_digest"],
                "idempotency_key": "start-tool11-exact-time",
            },
        )[0]["investigation"]
        self.assertEqual(completed["status"], "completed")
        tool_node = next(item for item in completed["nodes"] if item["node_id"] == "tool11")
        self.assertEqual(tool_node["state"], "committed")
        tool_record = next(item for item in runtime.store.list_json("execution-record") if item["node_id"] == "tool11")
        self.assertEqual(tool_record["state"], "succeeded")
        self.assertIsInstance(tool_record["receipt_digest"], str)
        self.assertEqual(len(tool_node["result_set_refs"]), 1)
        result_ref = tool_node["result_set_refs"][0]
        result_set = runtime.store.get_json("result-set", result_ref["object_digest"])
        self.assertEqual(result_set["source_tool"]["tool_id"], "TOOL-11")
        self.assertEqual(result_set["member_identity"], "route_observation_key")
        members = validate_result_set(result_set, runtime.store)
        self.assertEqual([item["route_observation_key"] for item in members], result_set["preview_views"][0]["member_refs"])

        graph = runtime.store.list_json("evidence-graph")[-1]
        context = runtime.store.list_json("graph-validation-context")[-1]
        receipts = {
            item["receipt_digest"]: item
            for item in runtime.store.list_json("receipt")
            if isinstance(item.get("receipt_digest"), str)
        }
        admissions = {item["receipt_digest"]: item for item in runtime.store.list_json("plan-admission-receipt")}
        plan = runtime.store.list_json("design-investigation-plan")[-1]
        hook = self.design_hook()
        hook.validate_investigation_plan_instance(
            plan,
            schema=load_frozen_contract("investigation-plan"),
            trusted_registry_store=runtime.dispatcher.trusted_registry_store,
            trusted_admission_receipt_store=admissions,
            parameter_bindings=completed["plan"]["design_parameter_bindings"],
        )
        hook.validate_result_set_instance(
            result_set,
            schema=load_frozen_contract("result-set"),
            resolved_members=members,
            trusted_registry_store=runtime.dispatcher.trusted_registry_store,
            receipt_store=receipts,
        )
        hook.validate_evidence_graph_instance(
            graph,
            schema=load_frozen_contract("evidence-graph"),
            trusted_registry_store=runtime.dispatcher.trusted_registry_store,
            result_sets={(result_set["result_set_id"], 1): result_set},
            plan_definition=context["plan_definition"],
            investigation_snapshot=context["investigation_snapshot"],
            receipt_store=receipts,
            result_set_members={(result_set["result_set_id"], 1): {item[result_set["member_identity"]]: item for item in members}},
        )
        page = runtime.get_result_set(
            PRINCIPAL, completed["investigation_id"], result_set["result_set_id"], 1,
            {"page_size": 1, "page_token": None},
        )
        self.assertEqual(page["members"][0]["state_point_utc"], "2026-02-27T00:05:00Z")
        export = runtime.create_export(
            PRINCIPAL,
            completed["investigation_id"],
            {
                "expected_investigation_revision": completed["investigation_revision"],
                "expected_current_digest": completed["current_digest"],
                "idempotency_key": "export-tool11-exact-time",
                "result_set_id": result_set["result_set_id"],
                "result_set_revision": 1,
                "format": "json",
            },
        )[0]
        artifact = runtime.get_export_artifact(PRINCIPAL, completed["investigation_id"], export["export"]["export_id"])
        self.assertIn(b"route_observation_key", artifact["content"])

    def test_op29_to_op37_route_path_consistency_journey_commits_result_graph_and_export(self):
        raw_digest = lambda value: digest_prefixed(value).removeprefix("sha256:")
        evidence = lambda name: {"evidence_id": f"e-{name}", "source_digest": "b" * 64, "member_key": name}
        left_digest = raw_digest({"fact": "left"})
        right_digest = raw_digest({"fact": "right"})
        temporal_left = {
            "fact_type_id": "peak", "temporal_kind": "exact_point", "time_utc": "2026-02-27T00:00:00Z",
            "population_id": "country", "unit_id": "count", "fact_digest": left_digest,
            "evidence_refs": [evidence("temporal-left")],
        }
        temporal_right = {
            "fact_type_id": "peak", "temporal_kind": "exact_point", "time_utc": "2026-02-27T00:00:00Z",
            "population_id": "country", "unit_id": "count", "fact_digest": right_digest,
            "evidence_refs": [evidence("temporal-right")],
        }
        op29_inputs = {
            "left_fact": temporal_left,
            "right_fact": temporal_right,
            "comparability_profile": {
                "profile_id": "PROFILE-TEMPORAL-COMPARABILITY-1.0.0", "profile_digest": "a" * 64,
                "fact_type_pair": ["peak", "peak"], "population_compatible": True, "unit_compatible": True,
                "time_basis": "publication_state_point_grid", "granularity_seconds": 300, "tolerance_seconds": 300,
            },
            "left_digest": left_digest, "right_digest": right_digest,
        }
        typed_left = {
            "fact_type_id": "peak", "population_id": "country", "unit_id": "count",
            "predicate_id": "prefix_up", "truth_state": "true", "fact_digest": left_digest,
            "evidence_refs": [evidence("fact-left")],
        }
        typed_right = {
            "fact_type_id": "peak", "population_id": "country", "unit_id": "count",
            "predicate_id": "prefix_down", "truth_state": "true", "fact_digest": right_digest,
            "evidence_refs": [evidence("fact-right")],
        }
        placeholder_op29_receipt = {
            "identity": {}, "operator_id": "OP-29", "left_digest": left_digest, "right_digest": right_digest,
            "relation": "same_slot", "comparable": True, "profile_digest": "a" * 64,
            "output_digest": "0" * 64,
            "evidence_refs": [evidence("temporal-left"), evidence("temporal-right")],
        }
        op37_inputs = {
            "left_fact": typed_left, "right_fact": typed_right,
            "op29_temporal_receipt": placeholder_op29_receipt,
            "consistency_profile": {
                "profile_id": "PROFILE-EVIDENCE-CONSISTENCY-1.0.0", "profile_digest": "a" * 64,
                "assertion_relation": "mutually_exclusive",
                "mutually_exclusive_predicate_ids": ["prefix_down", "prefix_up"],
            },
            "left_digest": left_digest, "right_digest": right_digest,
        }
        envelope = lambda unit_id, profile, inputs: {
            "operator_id": unit_id, "operator_version": "1.0.0-design",
            "parameter_profile_id": profile, "parameter_profile_digest": "a" * 64 if profile else None,
            "input_completeness": "complete", "inputs": inputs, "input_digests": ["0" * 64],
        }
        common_vp = {
            "prefix": "109.74.224.0/20", "afi": 4, "state_point_utc": "2026-02-27T00:00:00Z",
            "expected_direction_set": ["rrc25:64500"], "direction_profile_digest": "a" * 64,
        }
        op30 = envelope("OP-30", "PROFILE-VP-CONSISTENCY-1.0.0", {
            **common_vp,
            "actual_visibility_rows": [{"direction_id": "rrc25:64500", "visibility": "visible", "evidence_ref": evidence("visibility")}],
        })
        op31 = envelope("OP-31", "PROFILE-VP-CONSISTENCY-1.0.0", {
            **common_vp,
            "actual_origin_rows": [{"direction_id": "rrc25:64500", "origin_state": "known", "origin_asns": [58224], "evidence_ref": evidence("origin")}],
        })
        op32 = envelope("OP-32", "PROFILE-VP-CONSISTENCY-1.0.0", {
            **{key: value for key, value in common_vp.items() if key != "direction_profile_digest"},
            "actual_path_rows": [{
                "direction_id": "rrc25:64500", "path_state": "known_ordered", "path_digest": "c" * 64,
                "evidence_ref": evidence("path"),
            }],
            "canonicalization_profile_digest": operators.PATH_PROFILE_DIGEST,
        })
        op33 = envelope("OP-33", None, {
            "new_prefix_state_rows": [], "route_state_rows": [],
            "left_digest": raw_digest([]), "right_digest": raw_digest([]),
        })
        goal = "执行 OP-29 到 OP-37 路径证据关系与一致性闭包"
        execution_recipe = [
            node("gate01", "GATE-01"), node("gate02", "GATE-02"), node("gate03", "GATE-03"),
            node("tool11", "TOOL-11", ("gate01", "gate02", "gate03"), parameters={
                "state_point_utc": "2026-02-27T00:05:00Z", "prefix": "109.74.224.0/20", "afi": 4,
                "route_observation_key": "rrc25:vp-a:peer-a:109.74.224.0/20:ipv4", "page_size": 1,
            }),
            node("op30", "OP-30", ("tool11",), parameters=op30),
            node("op31", "OP-31", ("tool11",), parameters=op31),
            node("op32", "OP-32", ("tool11",), parameters=op32),
            node("op33", "OP-33", ("tool11",), parameters=op33),
            node("op29", "OP-29", ("tool11",), parameters=envelope("OP-29", "PROFILE-TEMPORAL-COMPARABILITY-1.0.0", op29_inputs)),
            node("op37", "OP-37", ("op29",), parameters=envelope("OP-37", "PROFILE-EVIDENCE-CONSISTENCY-1.0.0", op37_inputs)),
            node("boundary", "BOUNDARY-01", ("op37",), requiredness="boundary_only", mode="soft"),
        ]
        runtime = build_local_fixture_runtime(
            REPOSITORY_ROOT, Path(self.temporary.name) / "op29-op37",
            planning_grounding_port=TrustedFixturePlanningGroundingPort(
                fixture_catalog=trusted_projection_catalog("op29-op37", goal, execution_recipe), fixture_id="op29-op37",
            ),
        )
        created = runtime.create_investigation(PRINCIPAL, {
            "event_reference": EVENT_REFERENCE, "publication_id": "publication_fixture_ir_1", "revision": 1,
            "goal": goal, "idempotency_key": "create-op29-op37",
        })[0]["investigation"]
        completed = runtime.start_investigation(
            PRINCIPAL, created["investigation_id"], {
                "expected_investigation_revision": created["investigation_revision"],
                "expected_current_digest": created["current_digest"], "idempotency_key": "start-op29-op37",
            },
        )[0]["investigation"]
        records = {item["node_id"]: item for item in runtime.store.list_json("execution-record")}
        self.assertEqual(completed["status"], "completed", {key: value.get("error") for key, value in records.items()})
        self.assertEqual(records["op29"]["state"], "succeeded")
        self.assertEqual(records["op29"]["result"]["result"]["relation"], "same_slot")
        self.assertEqual(records["op37"]["state"], "succeeded")
        self.assertEqual(records["op37"]["result"]["result"]["class"], "conflict")
        for node_id in ("op30", "op31", "op32", "op33"):
            self.assertEqual(records[node_id]["state"], "succeeded", records[node_id].get("error"))
        projections = [item for item in runtime.store.list_json("receipt") if item.get("receipt_kind") == "op29_to_op37_structural_projection"]
        self.assertEqual(len(projections), 1)
        self.assertEqual(projections[0]["source_operator_output_digest"], records["op29"]["result"]["output_digest"])
        graph = runtime.store.list_json("evidence-graph")[-1]
        self.assertEqual(
            {node["operator_id"] for node in (item["payload"] for item in graph["nodes"] if item["node_type"] == "derived_fact")},
            {"OP-29", "OP-30", "OP-31", "OP-32", "OP-33", "OP-37"},
        )
        self.assertTrue(any(edge["edge_type"] == "derived_from" for edge in graph["edges"]))
        result_set = runtime.store.list_json("result-set")[-1]
        export = runtime.create_export(PRINCIPAL, completed["investigation_id"], {
            "expected_investigation_revision": completed["investigation_revision"],
            "expected_current_digest": completed["current_digest"], "idempotency_key": "export-op29-op37",
            "result_set_id": result_set["result_set_id"], "result_set_revision": 1, "format": "markdown",
        })[0]
        artifact = runtime.get_export_artifact(PRINCIPAL, completed["investigation_id"], export["export"]["export_id"])
        self.assertIn(b"route_observation_key", artifact["content"])
        self._core_runtime = runtime
        self._core_completed = export["investigation"]

    def test_frozen_design_semantic_validators_replay_actual_runtime_artifacts(self):
        completed, plan, result_set, graph, context, receipts, admissions, members = self._actual_design_validation_inputs("design-pass")
        hook = self.design_hook()
        hook.validate_investigation_plan_instance(
            plan, schema=load_frozen_contract("investigation-plan"),
            trusted_registry_store=self.runtime.dispatcher.trusted_registry_store,
            trusted_admission_receipt_store=admissions,
            parameter_bindings=completed["plan"]["design_parameter_bindings"],
        )
        hook.validate_result_set_instance(
            result_set, schema=load_frozen_contract("result-set"), resolved_members=members,
            trusted_registry_store=self.runtime.dispatcher.trusted_registry_store, receipt_store=receipts,
        )
        hook.validate_evidence_graph_instance(
            graph, schema=load_frozen_contract("evidence-graph"),
            trusted_registry_store=self.runtime.dispatcher.trusted_registry_store,
            result_sets={(result_set["result_set_id"], result_set["result_set_revision"]): result_set},
            plan_definition=context["plan_definition"], investigation_snapshot=context["investigation_snapshot"],
            receipt_store=receipts,
            result_set_members={(result_set["result_set_id"], result_set["result_set_revision"]): {item[result_set["member_identity"]]: item for item in members}},
        )

    def test_frozen_design_semantic_validators_reject_named_attacks(self):
        completed, plan, result_set, graph, context, receipts, admissions, members = self._actual_design_validation_inputs("design-attacks")
        hook = self.design_hook()
        parameter_bindings = completed["plan"]["design_parameter_bindings"]
        bad_plan = copy.deepcopy(plan)
        bad_plan["plan_definition"]["admission_receipt_digest"] = "0" * 64
        with self.assertRaises(hook.AlignmentError) as plan_error:
            hook.validate_investigation_plan_instance(
                bad_plan, schema=load_frozen_contract("investigation-plan"), trusted_registry_store=self.runtime.dispatcher.trusted_registry_store,
                trusted_admission_receipt_store=admissions, parameter_bindings=parameter_bindings,
            )
        self.assertEqual(plan_error.exception.code, "plan_admission_receipt_unresolved")
        bad_result = copy.deepcopy(result_set)
        bad_result["stable_sort_digest"] = "0" * 64
        with self.assertRaises(hook.AlignmentError) as result_error:
            hook.validate_result_set_instance(
                bad_result, schema=load_frozen_contract("result-set"), resolved_members=members,
                trusted_registry_store=self.runtime.dispatcher.trusted_registry_store, receipt_store=receipts,
            )
        self.assertEqual(result_error.exception.code, "result_set_sort_digest_mismatch")
        bad_graph = copy.deepcopy(graph)
        bad_graph["plan_digest"] = "0" * 64
        with self.assertRaises(hook.AlignmentError) as graph_error:
            hook.validate_evidence_graph_instance(
                bad_graph, schema=load_frozen_contract("evidence-graph"), trusted_registry_store=self.runtime.dispatcher.trusted_registry_store,
                result_sets={(result_set["result_set_id"], 1): result_set}, plan_definition=context["plan_definition"],
                investigation_snapshot=context["investigation_snapshot"], receipt_store=receipts,
                result_set_members={(result_set["result_set_id"], 1): {item[result_set["member_identity"]]: item for item in members}},
            )
        self.assertEqual(graph_error.exception.code, "evidence_graph_plan_digest_mismatch")

    def test_runtime_artifact_admission_resolver_rejects_ghost_duplicate_tamper_and_cross_plan(self):
        created = self.create("runtime-admission-attacks")
        design_plan = self.runtime.store.get_json("design-investigation-plan", created["plan"]["source_design_plan_digest"])
        receipt_digest = created["plan"]["runtime_plan_admission_receipt_digest"]
        parameter_digest = digest_prefixed({"parameter_bindings": created["plan"]["design_parameter_bindings"]})
        resolved = self.runtime._resolve_runtime_artifact_admission(
            receipt_digest=receipt_digest, artifact_kind="InvestigationPlan", artifact=design_plan,
            parameter_bindings_digest=parameter_digest, authorizes_dispatcher_execution=True,
        )
        self.assertEqual(resolved["receipt_digest"], receipt_digest)
        with self.assertRaises(W5InvestigationError) as ghost:
            self.runtime._resolve_runtime_artifact_admission(
                receipt_digest="sha256:" + "0" * 64, artifact_kind="InvestigationPlan", artifact=design_plan,
                parameter_bindings_digest=parameter_digest, authorizes_dispatcher_execution=True,
            )
        self.assertEqual(ghost.exception.code, "runtime_artifact_admission_not_unique")
        original_list = self.runtime.store.list_json
        actual = [item for item in original_list("runtime-artifact-admission") if item.get("receipt_digest") == receipt_digest][0]
        with patch.object(self.runtime.store, "list_json", side_effect=lambda kind: [actual, copy.deepcopy(actual)] if kind == "runtime-artifact-admission" else original_list(kind)):
            with self.assertRaises(W5InvestigationError) as duplicate:
                self.runtime._resolve_runtime_artifact_admission(
                    receipt_digest=receipt_digest, artifact_kind="InvestigationPlan", artifact=design_plan,
                    parameter_bindings_digest=parameter_digest, authorizes_dispatcher_execution=True,
                )
        self.assertEqual(duplicate.exception.code, "runtime_artifact_admission_not_unique")
        tampered = {**actual, "registry_snapshot_digest": "sha256:" + "f" * 64}
        with patch.object(self.runtime.store, "list_json", side_effect=lambda kind: [tampered] if kind == "runtime-artifact-admission" else original_list(kind)):
            with self.assertRaises(W5InvestigationError) as tamper:
                self.runtime._resolve_runtime_artifact_admission(
                    receipt_digest=receipt_digest, artifact_kind="InvestigationPlan", artifact=design_plan,
                    parameter_bindings_digest=parameter_digest, authorizes_dispatcher_execution=True,
                )
        self.assertEqual(tamper.exception.code, "runtime_artifact_admission_digest_mismatch")
        cross_plan = copy.deepcopy(design_plan)
        cross_plan["plan_definition"]["goal_digest"] = "f" * 64
        with self.assertRaises(W5InvestigationError) as crossed:
            self.runtime._resolve_runtime_artifact_admission(
                receipt_digest=receipt_digest, artifact_kind="InvestigationPlan", artifact=cross_plan,
                parameter_bindings_digest=parameter_digest, authorizes_dispatcher_execution=True,
            )
        self.assertEqual(crossed.exception.code, "runtime_artifact_admission_binding_mismatch")

    def test_result_set_and_graph_admission_failures_leave_no_unadmitted_public_residue(self):
        def run_attack(artifact_kind, failure_layer, suffix):
            root = Path(self.temporary.name) / suffix
            runtime = build_local_fixture_runtime(
                REPOSITORY_ROOT,
                root,
                planning_grounding_port=TrustedFixturePlanningGroundingPort(
                    fixture_catalog=trusted_projection_catalog(
                        suffix, "执行国家事件全景、证据闭包与导出", self.plan
                    ),
                    fixture_id=suffix,
                ),
            )
            created = runtime.create_investigation(PRINCIPAL, {
                "event_reference": EVENT_REFERENCE,
                "publication_id": "publication_fixture_ir_1",
                "revision": 1,
                "goal": "执行国家事件全景、证据闭包与导出",
                "idempotency_key": f"create-{suffix}",
            })[0]["investigation"]
            public_before = {
                "result-set": len(runtime.store.list_json("result-set")),
                "evidence-graph": len(runtime.store.list_json("evidence-graph")),
                "graph-validation-context": len(runtime.store.list_json("graph-validation-context")),
            }
            graph_receipt_kinds = {"producer", "edge_producer", "graph_closure", "graph_commit"}
            graph_receipts_before = len([
                item for item in runtime.store.list_json("receipt")
                if item.get("receipt_kind") in graph_receipt_kinds
            ])
            original = (
                runtime._frozen_design_semantic_admission
                if failure_layer == "A"
                else runtime._runtime_artifact_admission
            )

            def fail_selected(*args, **kwargs):
                if kwargs.get("artifact_kind") == artifact_kind:
                    raise W5InvestigationError(
                        f"injected_{artifact_kind.lower()}_{failure_layer.lower()}_failure",
                        "注入准入失败",
                    )
                return original(*args, **kwargs)

            target = "_frozen_design_semantic_admission" if failure_layer == "A" else "_runtime_artifact_admission"
            with patch.object(runtime, target, side_effect=fail_selected):
                with self.assertRaises(W5InvestigationError) as failure:
                    runtime.start_investigation(
                        PRINCIPAL,
                        created["investigation_id"],
                        self.cas(created, f"start-{suffix}"),
                    )
            self.assertEqual(
                failure.exception.code,
                f"injected_{artifact_kind.lower()}_{failure_layer.lower()}_failure",
            )
            raw, pointer = runtime._load(created["investigation_id"])
            self.assertEqual(raw["status"], "failed")
            self.assertEqual(pointer["revision"], 3)
            self.assertFalse(any(item.get("status") == "running" for item in [raw]))
            self.assertEqual(len(runtime.store.list_json("evidence-graph")), public_before["evidence-graph"])
            self.assertEqual(len(runtime.store.list_json("graph-validation-context")), public_before["graph-validation-context"])
            self.assertEqual(len([
                item for item in runtime.store.list_json("receipt")
                if item.get("receipt_kind") in graph_receipt_kinds
            ]), graph_receipts_before)
            if artifact_kind == "ResultSet":
                self.assertEqual(len(runtime.store.list_json("result-set")), public_before["result-set"])
                self.assertEqual(raw["result_set_refs"], [])
            else:
                self.assertEqual(len(runtime.store.list_json("result-set")), public_before["result-set"] + 1)
                self.assertEqual(len(raw["result_set_refs"]), 1)
                self.assertEqual(raw["evidence_graph_refs"], [])
            failure_receipts = [
                item for item in runtime.store.list_json("receipt")
                if item.get("receipt_kind") == "transaction_failure"
            ]
            self.assertEqual(len(failure_receipts), 1)
            self.assertFalse(failure_receipts[0]["graph_published"])

            # 同一请求重放必须返回同一 failed current，而不是再次执行或卡在旧CAS。
            replay = runtime.start_investigation(
                PRINCIPAL,
                created["investigation_id"],
                self.cas(created, f"start-{suffix}"),
            )[0]
            self.assertTrue(replay["deduplicated"])
            self.assertEqual(replay["investigation"]["status"], "failed")

        for artifact_kind in ("ResultSet", "EvidenceGraph"):
            for failure_layer in ("A", "B"):
                with self.subTest(artifact_kind=artifact_kind, failure_layer=failure_layer):
                    run_attack(
                        artifact_kind,
                        failure_layer,
                        f"residue-{artifact_kind.lower()}-{failure_layer.lower()}",
                    )

    def test_local_failure_scope_and_p21_fail_closed(self):
        with self.assertRaises(W5InvestigationError) as denied:
            self.runtime.create_investigation(
                {"user_id": "w5-runtime-user", "authorization_scope": "country_outage_event_read:US"},
                {"event_reference": EVENT_REFERENCE, "publication_id": "publication_fixture_ir_1", "revision": 1, "goal": "x", "idempotency_key": "wrong-country"},
            )
        self.assertEqual(denied.exception.status_code, 403)
        with self.assertRaises(Exception) as p21:
            denied_runtime = build_local_fixture_runtime(
                REPOSITORY_ROOT,
                Path(self.temporary.name) / "p21",
                planning_grounding_port=TrustedFixturePlanningGroundingPort(
                    fixture_catalog=trusted_projection_catalog("p21", "执行国家事件全景、证据闭包与导出", [node("fanout", "PLAN-CAP-02")]), fixture_id="p21",
                ),
            )
            denied_runtime.create_investigation(PRINCIPAL, {
                "event_reference": EVENT_REFERENCE, "publication_id": "publication_fixture_ir_1", "revision": 1,
                "goal": "执行国家事件全景、证据闭包与导出", "idempotency_key": "p21-denied",
            })
        self.assertIn(getattr(p21.exception, "code", ""), {"p2_1_unit_forbidden", "execution_unit_not_admitted"})

    def test_running_revision_is_visible_and_cancel_wins_worker_cas(self):
        self.assertTrue(self._verify_running_cancel(self.runtime, self.create("cancel")))

    def _verify_running_cancel(self, runtime, created):
        entered = threading.Event()
        release = threading.Event()
        original = runtime._execute

        def blocked(snapshot, **kwargs):
            entered.set()
            release.wait(5)
            return original(snapshot, **kwargs)

        outcome = {}
        with patch.object(runtime, "_execute", side_effect=blocked):
            thread = threading.Thread(
                target=lambda: outcome.setdefault("value", runtime.start_investigation(PRINCIPAL, created["investigation_id"], self.cas(created, "start-cancel"))),
                daemon=True,
            )
            thread.start()
            self.assertTrue(entered.wait(5))
            running = runtime.get_investigation(PRINCIPAL, created["investigation_id"])["investigation"]
            self.assertEqual(running["status"], "running")
            cancelled = runtime.cancel_investigation(
                PRINCIPAL, created["investigation_id"], self.cas(running, "cancel-running")
            )[0]["investigation"]
            self.assertEqual(cancelled["status"], "cancelled")
            release.set()
            thread.join(10)
        self.assertEqual(outcome["value"][0]["investigation"]["status"], "cancelled")
        return True

    def test_store_tamper_symlink_and_registry_ghost_are_rejected(self):
        stored = self.runtime.store.put_json("attack", {"value": 1})
        path = Path(self.temporary.name) / stored["object_ref"]
        path.chmod(0o600)
        path.write_text('{"tampered":true}\n', encoding="utf-8")
        with self.assertRaises(W5StoreError):
            self.runtime.store.get_json("attack", stored["object_digest"])
        with self.assertRaises(W5RegistryError) as ghost:
            _implementation_digest_for_handler("python:backend.services.country_outage_p2_s1_delivery.ghost_handler")
        self.assertEqual(ghost.exception.code, "handler_member_missing")
        with tempfile.TemporaryDirectory() as parent:
            real = Path(parent) / "real"
            real.mkdir()
            alias = Path(parent) / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises(W5StoreError):
                ContentAddressedStore(alias / "store")

    def test_cas_journal_recovers_pointer_before_idempotency_crash(self):
        self.assertTrue(self._verify_cas_crash_recovery(Path(self.temporary.name) / "crash-store"))

    def _verify_cas_crash_recovery(self, root):
        store = ContentAddressedStore(root)
        stored = store.put_json("investigation", {"revision": 1})
        with patch.object(store, "_write_idempotency", side_effect=RuntimeError("injected crash")):
            with self.assertRaises(RuntimeError):
                store.compare_and_swap_pointer(
                    "investigation", "inv_crash", expected_current_digest=None, expected_revision=None,
                    new_object_digest=stored["object_digest"], new_revision=1,
                    idempotency_key="crash-key-0001", request_digest=digest_prefixed({"request": 1}),
                )
        rebuilt = ContentAddressedStore(root)
        replay = rebuilt.compare_and_swap_pointer(
            "investigation", "inv_crash", expected_current_digest=None, expected_revision=None,
            new_object_digest=stored["object_digest"], new_revision=1,
            idempotency_key="crash-key-0001", request_digest=digest_prefixed({"request": 1}),
        )
        self.assertTrue(replay["replayed"])
        self.assertIsNotNone(rebuilt.read_idempotency("investigation", "inv_crash", "crash-key-0001"))
        return True

    def test_plan_cap_scalar_and_committed_ancestor_binding_fail_closed(self):
        records = {
            "source": {
                "node_id": "source", "unit_id": "TOOL-07", "state": "succeeded",
                "result": {"peak": {"state_point_utc": "2026-02-27T00:05:00Z"}},
                "receipt_digest": "1" * 64, "record_digest": "sha256:" + "2" * 64, "result_digest": "2" * 64,
            }
        }
        scalar = self.runtime._plan_cap(
            {"source_node_id": "source", "source_path": "peak.state_point_utc", "target_name": "state_point_utc"}, records
        )
        self.assertEqual(scalar["value"], "2026-02-27T00:05:00Z")
        records["binding"] = {
            "node_id": "binding", "unit_id": "PLAN-CAP-01", "state": "succeeded",
            "result": scalar, "receipt_digest": "3" * 64, "record_digest": "sha256:" + "4" * 64, "result_digest": "4" * 64,
        }
        plan = [node("source", "TOOL-07"), node("binding", "PLAN-CAP-01", ("source",)), node("target", "TOOL-08", ("binding",))]
        binding = {
            "input_name": "state_point_utc", "source_kind": "node_result", "source_ref": "binding",
            "source_digest": __import__("hashlib").sha256(canonical_json({"input_name":"state_point_utc","source_kind":"node_result","source_ref":"binding","bound_parameter_value":scalar["value"]}).encode()).hexdigest(),
            "source_artifact_digest": "4" * 64,
        }
        request = self.runtime._bound_request({**plan[2], "parameters": {"page_size": 20, "state_point_utc": scalar["value"]}, "input_bindings": [binding]}, records, plan)
        self.assertEqual(request["state_point_utc"], scalar["value"])
        for mutation in (
            {**binding, "source_ref": "ghost"},
            {**binding, "source_artifact_digest": "5" * 64},
            {**binding, "source_digest": "6" * 64},
        ):
            with self.assertRaises(W5InvestigationError):
                self.runtime._bound_request({**plan[2], "parameters": {"state_point_utc": scalar["value"]}, "input_bindings": [mutation]}, records, plan)
        records["binding"]["result"] = {**scalar, "value": [scalar["value"]]}
        with self.assertRaises(W5InvestigationError):
            self.runtime._bound_request({**plan[2], "parameters": {"state_point_utc": scalar["value"]}, "input_bindings": [binding]}, records, plan)

    def test_z_runtime_execution_trace_is_derived_from_actual_spy_and_store(self):
        # 先执行事件全景与完整W4两条真实旅程；business人口只能来自各自store回执。
        panorama_created = self.create("trace-panorama")
        panorama_completed = self.runtime.start_investigation(
            PRINCIPAL, panorama_created["investigation_id"], self.cas(panorama_created, "start-trace-panorama")
        )[0]["investigation"]
        self.assertEqual(panorama_completed["status"], "completed")
        self.test_op29_to_op37_route_path_consistency_journey_commits_result_graph_and_export()
        runtime = self._core_runtime
        current = self._core_completed
        result_set = runtime.store.list_json("result-set")[-1]

        # 同一 frozen ResultSet 真实走齐三个 Renderer；每次Delivery均使用最新CAS。
        for index, format_name in enumerate(("csv", "json"), start=1):
            exported = runtime.create_export(PRINCIPAL, current["investigation_id"], {
                "expected_investigation_revision": current["investigation_revision"],
                "expected_current_digest": current["current_digest"],
                "idempotency_key": f"trace-export-{format_name}-{index}",
                "result_set_id": result_set["result_set_id"], "result_set_revision": 1, "format": format_name,
            })[0]
            runtime.get_export_artifact(PRINCIPAL, current["investigation_id"], exported["export"]["export_id"])
            current = exported["investigation"]

        # PLAN-CAP-01 由正式owner函数产生标量后，按同一admission/schema记录真实调用。
        source_record = {
            "node_id": "trace-source", "unit_id": "TOOL-11", "state": "succeeded",
            "record_digest": "sha256:" + "1" * 64,
            "result": {"peak": {"state_point_utc": "2026-02-27T00:00:00Z"}},
        }
        cap_input = {
            "parameters": {"source_node_id": "trace-source", "source_path": "peak.state_point_utc", "target_name": "state_point_utc"},
            "ancestor_record_digests": [source_record["record_digest"]],
        }
        cap_output = runtime._plan_cap(cap_input["parameters"], {"trace-source": source_record})
        runtime.dispatcher.record_control_execution("PLAN-CAP-01", cap_input, cap_output)

        # CAS冲突、running/cancel与crash recovery均实际执行，断言成功后才写入trace。
        cas_conflict_rejected = False
        try:
            runtime.rerun_node(
                PRINCIPAL, current["investigation_id"], "tool11",
                {
                    "expected_investigation_revision": self._core_completed["investigation_revision"],
                    "expected_current_digest": self._core_completed["current_digest"],
                    "idempotency_key": "trace-stale-cas",
                },
            )
        except W5InvestigationError as error:
            cas_conflict_rejected = error.code == "compare_and_swap_conflict"
        self.assertTrue(cas_conflict_rejected)
        cancel_goal = "执行国家事件全景、证据闭包与导出"
        cancel_runtime = build_local_fixture_runtime(
            REPOSITORY_ROOT, Path(self.temporary.name) / "trace-cancel-runtime",
            planning_grounding_port=TrustedFixturePlanningGroundingPort(
                fixture_catalog=trusted_projection_catalog("trace-cancel", cancel_goal, self.plan), fixture_id="trace-cancel",
            ),
        )
        cancel_created = cancel_runtime.create_investigation(PRINCIPAL, {
            "event_reference": EVENT_REFERENCE, "publication_id": "publication_fixture_ir_1", "revision": 1,
            "goal": cancel_goal, "idempotency_key": "create-trace-cancel",
        })[0]["investigation"]
        running_cancel_verified = self._verify_running_cancel(cancel_runtime, cancel_created)
        cas_crash_recovery_replayed_same_outcome = self._verify_cas_crash_recovery(
            Path(self.temporary.name) / "trace-crash-store"
        )

        root = REPOSITORY_ROOT
        hook = self.design_hook()
        plan = runtime.store.list_json("design-investigation-plan")[-1]
        graph = runtime.store.list_json("evidence-graph")[-1]
        graph_context = runtime.store.list_json("graph-validation-context")[-1]
        members = validate_result_set(result_set, runtime.store)
        receipts = {
            item["receipt_digest"]: item for item in runtime.store.list_json("receipt")
            if isinstance(item.get("receipt_digest"), str)
        }
        admissions = {item["receipt_digest"]: item for item in runtime.store.list_json("plan-admission-receipt")}
        hook.validate_investigation_plan_instance(
            plan, schema=load_frozen_contract("investigation-plan"),
            trusted_registry_store=runtime.dispatcher.trusted_registry_store,
            trusted_admission_receipt_store=admissions,
            parameter_bindings=current["plan"]["design_parameter_bindings"],
        )
        hook.validate_result_set_instance(
            result_set, schema=load_frozen_contract("result-set"), resolved_members=members,
            trusted_registry_store=runtime.dispatcher.trusted_registry_store, receipt_store=receipts,
        )
        hook.validate_evidence_graph_instance(
            graph, schema=load_frozen_contract("evidence-graph"),
            trusted_registry_store=runtime.dispatcher.trusted_registry_store,
            result_sets={(result_set["result_set_id"], 1): result_set},
            plan_definition=graph_context["plan_definition"],
            investigation_snapshot=graph_context["investigation_snapshot"], receipt_store=receipts,
            result_set_members={(result_set["result_set_id"], 1): {item[result_set["member_identity"]]: item for item in members}},
        )

        schema_paths = {
            "InvestigationPlan": "contracts/agent/country-outage-p2-s1-execution-unit-design/investigation-plan.schema.json",
            "ResultSet": "contracts/agent/country-outage-p2-s1-execution-unit-design/result-set.schema.json",
            "EvidenceGraph": "contracts/agent/country-outage-p2-s1-execution-unit-design/evidence-graph.schema.json",
        }
        artifacts = {"InvestigationPlan": plan, "ResultSet": result_set, "EvidenceGraph": graph}
        validator_ids = {
            "InvestigationPlan": "country_outage_p2_s1_w5_investigation_plan_semantic_validator",
            "ResultSet": "country_outage_p2_s1_w5_result_set_semantic_validator",
            "EvidenceGraph": "country_outage_p2_s1_w5_evidence_graph_semantic_validator",
        }
        entrypoints = {
            kind: [f".codex/hooks/country_outage_agent_p2_s1_design_alignment.py::{name}"]
            for kind, name in {
                "InvestigationPlan": "validate_investigation_plan_instance",
                "ResultSet": "validate_result_set_instance",
                "EvidenceGraph": "validate_evidence_graph_instance",
            }.items()
        }
        design_validator_path = root / ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py"
        design_validator_digest = "sha256:" + hashlib.sha256(design_validator_path.read_bytes()).hexdigest()
        artifact_bindings = []
        semantic_replays = []
        actual_design_validator_receipts = runtime.store.list_json("design-semantic-validator-receipt")
        actual_runtime_admissions = runtime.store.list_json("runtime-artifact-admission")
        for kind in ("InvestigationPlan", "ResultSet", "EvidenceGraph"):
            artifact = artifacts[kind]
            schema_path = schema_paths[kind]
            schema_sha = hashlib.sha256((root / schema_path).read_bytes()).hexdigest()
            design_digest = runtime.store.put_json(f"design-{kind.lower()}", artifact)["object_digest"]
            validator_receipt = next(
                item for item in actual_design_validator_receipts
                if item.get("artifact_kind") == kind and item.get("artifact_digest") == design_digest
            )
            runtime_receipt = next(
                item for item in actual_runtime_admissions
                if item.get("artifact_kind") == kind
                and item.get("design_artifact_digest") == design_digest
                and item.get("frozen_design_validator_receipt_digest") == validator_receipt["receipt_digest"]
            )
            artifact_bindings.append({
                "artifact_kind": kind, "validation_mode": "frozen_schema_valid",
                "frozen_schema_path": schema_path, "frozen_schema_sha256": schema_sha,
                "design_artifact_object_digest": design_digest,
                "runtime_object_digest": runtime_receipt["runtime_subject_digest"],
                "runtime_envelope_object_digest": None,
            })
            semantic_replays.append({
                "artifact_kind": kind, "artifact_digest": design_digest, "schema_path": schema_path,
                "schema_sha256": schema_sha, "validator_id": validator_ids[kind], "validator_version": "1.0.0",
                "validator_contract_digest": "sha256:" + schema_sha,
                "validator_implementation_digest": design_validator_digest, "trusted_store_resolved": True,
                "draft_schema_error_count": 0, "semantic_error_count": 0, "replay_disposition": "passed",
                "validator_receipt": validator_receipt, "runtime_admission_receipt": runtime_receipt,
            })

        all_receipts = {
            item["receipt_digest"]: item for item in runtime.store.list_json("receipt")
            if isinstance(item.get("receipt_digest"), str)
        }
        closure_fields = {
            field: result_set[field] for field in (
                "result_set_id", "result_set_revision", "manifest_digest", "content_digest", "returned_count",
                "total_count", "set_completeness", "source_population_id", "source_population_schema_digest", "source_dataset_digest",
            )
        }
        result_set_receipt_closure = {
            **closure_fields,
            "query_receipt": all_receipts[result_set["query_receipt_digest"]],
            "page_receipts": [all_receipts[item["page_receipt_digest"]] for item in result_set["page_manifest"]],
            "freeze_receipt": all_receipts[result_set["freeze_receipt_digest"]],
        }

        control_records = runtime.store.list_json("control-execution-call")
        control_counts = {
            unit_id: sum(1 for item in control_records if item["unit_id"] == unit_id)
            for unit_id in sorted({item["unit_id"] for item in control_records})
        }
        expected_control_units = {
            "PLAN-CAP-01", "GATE-01", "GATE-02", "GATE-03", "GATE-04", "GATE-05", "BOUNDARY-01",
            "RENDERER-01", "RENDERER-02", "RENDERER-03", "DELIVERY-01",
        }
        self.assertEqual(set(control_counts), expected_control_units)

        business_receipts = [
            item
            for source_runtime in (self.runtime, runtime)
            for item in source_runtime.store.list_json("receipt")
            if item.get("receipt_kind") == "business_runtime_schema_validation"
        ]
        business_units = sorted({item["unit_id"] for item in business_receipts})
        self.assertEqual(set(business_units), {"TOOL-07", "TOOL-11", "OP-29", "OP-30", "OP-31", "OP-32", "OP-33", "OP-37"})
        business_records = []
        for unit_id in business_units:
            calls = [item for item in business_receipts if item["unit_id"] == unit_id]
            entry = runtime.dispatcher.admission.entries[unit_id]
            first = calls[0]
            business_records.append({
                "unit_id": unit_id, "invocation_count": len(calls), "schema_validation_count": len(calls) * 2,
                "schema_validation_failure_count": 0, "registry_snapshot_digest": runtime.dispatcher.admission.snapshot_digest,
                "input_schema_ref": first["input_schema_ref"], "output_schema_ref": first["output_schema_ref"],
                "input_schema_digest": first["input_schema_digest"], "output_schema_digest": first["output_schema_digest"],
                "handler_id": entry["handler_id"], "implementation_digest": entry["implementation_digest"],
                "call_receipt_digests": [item["receipt_digest"] for item in calls],
            })
        registry_admission = runtime.store.list_json("registry-admission")[-1]
        plan_runtime_receipt = next(item["runtime_admission_receipt"] for item in semantic_replays if item["artifact_kind"] == "InvestigationPlan")
        event_contexts = [
            item for item in runtime.store.list_json("admission-event-context")
            if item.get("investigation_id") == current["investigation_id"]
        ]
        self.assertEqual(len(event_contexts), 1)
        event_context = event_contexts[0]
        admission_events = sorted(
            (
                item for item in runtime.store.list_json("admission-event")
                if item.get("execution_id") == event_context["execution_id"]
            ),
            key=lambda item: item["sequence"],
        )
        self.assertEqual([item["event_kind"] for item in admission_events], [
            "plan_design_validated", "plan_runtime_admitted", "running_committed", "first_dispatch",
            "result_set_built", "result_set_design_validated", "result_set_runtime_admitted", "result_set_published",
            "evidence_graph_built", "evidence_graph_design_validated", "evidence_graph_runtime_admitted",
            "evidence_graph_receipts_published", "evidence_graph_published", "final_investigation_cas_committed",
        ])
        event_chain_base = {
            "schema_version": "country_outage_p2_s1_w5_admission_event_chain_v1",
            "execution_id": event_context["execution_id"],
            "investigation_id": event_context["investigation_id"],
            "base_investigation_revision": event_context["base_investigation_revision"],
            "idempotency_key_digest": event_context["idempotency_key_digest"],
            "registry_snapshot_digest": event_context["registry_snapshot_digest"],
            "events": admission_events,
        }
        admission_event_chain = {
            **event_chain_base,
            "chain_digest": digest_prefixed(event_chain_base),
        }
        trace_base = {
            "schema_version": "country_outage_p2_s1_w5_execution_trace_v1",
            "trace_source": "runtime_execution_spy_and_content_addressed_store",
            "execution_admission_receipt_digest": registry_admission["receipt_digest"],
            "invoked_control_unit_ids": sorted(control_counts), "control_unit_call_counts": control_counts,
            "plan_node_unit_ids": sorted(
                {node["unit_id"] for node in current["plan"]["nodes"]}
                | {node["unit_id"] for node in panorama_completed["plan"]["nodes"]}
                | {"PLAN-CAP-01"}
            ),
            "control_unit_execution_records": control_records,
            "schema_validated_control_unit_ids": sorted(control_counts), "schema_validation_failure_count": 0,
            "persisted_artifact_schema_bindings": artifact_bindings,
            "runtime_artifact_schema_validation_failure_count": 0,
            "design_semantic_validator_replays": semantic_replays,
            "result_set_receipt_closure": result_set_receipt_closure,
            "admission_event_chain": admission_event_chain,
            "plan_admission_validator": {
                key: plan_runtime_receipt[key] for key in (
                    "receipt_digest", "validator_id", "validator_version", "validator_contract_digest",
                    "validator_implementation_digest", "trusted_store_resolved",
                )
            },
            "business_unit_invocation_ids": business_units,
            "schema_validated_business_unit_ids": business_units,
            "business_unit_execution_records": business_records,
            "business_unit_schema_validation_failure_count": 0,
            "monetary_limit_mode": "unlimited", "max_cost_amount_zero_present": False,
            "cas_crash_recovery_replayed_same_outcome": cas_crash_recovery_replayed_same_outcome,
            "planning_grounding_port": {
                "port_kind": "trusted_fixture_sol_planning_host_grounding", "request_plan_nodes_rejected": True,
                "constructor_plan_nodes_supported": False, "grounded_plan_committed": True,
                "grounded_execution_recipe_schema_version": "country_outage_p2_s1_w5_grounded_execution_recipe_v1",
                "recipe_digest_verified": True, "projection_recipe_digest_verified": True,
                "host_grounding_recipe_digest_verified": True,
            },
            "dynamic_fanout_count": 0, "arbitrary_callback_count": 0, "p2_1_unit_ids": [],
            "external_model_call_count": 0, "result_set_committed": True, "evidence_graph_committed": True,
            "export_committed": True, "cas_conflict_rejected": cas_conflict_rejected,
            "running_cancel_verified": running_cancel_verified, "local_fixture_only": True, "production_deployed": False,
        }
        trace = {**trace_base, "trace_digest": hashlib.sha256(canonical_json(trace_base).encode()).hexdigest()}
        print("P2_S1_W5_EXECUTION_TRACE=" + canonical_json(trace), flush=True)


if __name__ == "__main__":
    unittest.main()
