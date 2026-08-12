from __future__ import annotations

import importlib.util
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = (
    REPO_ROOT / ".codex/hooks/country_outage_agent_p2_s1_design_alignment.py"
)
SPEC = importlib.util.spec_from_file_location("p2_s1_design_alignment", HOOK_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"无法加载 Hook：{HOOK_PATH}")
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)


class CountryOutageP2S1DesignAlignmentHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in (
            HOOK.TASK_SPEC,
            HOOK.PHASE_PLAN,
            HOOK.ALIGNMENT_HOOK,
            HOOK.ALIGNMENT_HOOK_TESTS,
        ):
            source = REPO_ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _replace(self, relative: Path, old: str, new: str) -> None:
        path = self.root / relative
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def _copy_stage_artifacts(self, stage: str) -> None:
        for relative in HOOK.ARTIFACTS_BY_STAGE[stage]:
            source = REPO_ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _copy_through_s1d4(self) -> None:
        for stage in ("S1D-1", "S1D-2", "S1D-3", "S1D-4"):
            self._copy_stage_artifacts(stage)

    def _mutate_json(self, relative: Path, mutate) -> None:
        path = self.root / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        mutate(payload)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _assert_error(self, code: str, stage: str = "S1D-0") -> None:
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.run_alignment(
                self.root,
                stage,
                require_prior_receipts=False,
            )
        self.assertEqual(code, captured.exception.code)

    @staticmethod
    def _hex(char: str = "a") -> str:
        return char * 64

    @staticmethod
    def _digest(payload: dict, *excluded: str) -> str:
        body = dict(payload)
        for field in excluded:
            body.pop(field, None)
        encoded = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _receipt(self, **fields) -> dict:
        receipt = dict(fields)
        receipt["receipt_digest"] = self._digest(receipt)
        return receipt

    def _minimal_schema_instance(self, schema: dict):
        if "const" in schema:
            return json.loads(json.dumps(schema["const"]))
        if "enum" in schema:
            return json.loads(json.dumps(schema["enum"][0]))
        if "oneOf" in schema:
            return self._minimal_schema_instance(schema["oneOf"][0])
        schema_type = schema.get("type")
        if schema_type == "object":
            properties = schema.get("properties", {})
            return {
                field: self._minimal_schema_instance(properties[field])
                for field in schema.get("required", [])
            }
        if schema_type == "array":
            return [
                self._minimal_schema_instance(schema.get("items", {}))
                for _ in range(schema.get("minItems", 0))
            ]
        if schema_type == "integer":
            return schema.get("minimum", 0)
        if schema_type == "number":
            return schema.get("minimum", 0)
        if schema_type == "boolean":
            return True
        if schema_type == "null":
            return None
        if schema_type == "string":
            pattern = schema.get("pattern", "")
            if "sha256:" in pattern:
                return "sha256:" + self._hex("d")
            if "[0-9a-f]{64}" in pattern:
                return self._hex("d")
            if schema.get("format") == "uri-reference":
                return "https://domeye.example/runtime/value"
            return "VALUE"
        return {}

    def _schema(self, name: str) -> dict:
        return json.loads(
            (REPO_ROOT / HOOK.CONTRACT_ROOT / name).read_text(encoding="utf-8")
        )

    def _operator_contract_schema(self) -> dict:
        return self._schema("operator-contract.schema.json")

    def _operator_entry(self, operator_id: str) -> dict:
        entry = self._registry_entry(operator_id, "operator")
        entry["unit_version"] = "1.0.0-design"
        entry["output_schema_refs"] = [
            f"operator-contract.schema.json#/$defs/op{operator_id.split('-')[1]}OutputEnvelope"
        ]
        return entry

    def _operator_identity(self, plan: dict) -> dict:
        identity = plan["plan_definition"]["identity"]
        return {
            field: (
                identity[field].removeprefix("sha256:")
                if field == "registry_snapshot_digest"
                else identity[field]
            )
            for field in (
                "incident_id",
                "publication_id",
                "publication_revision",
                "publication_digest",
                "collector_id",
                "cohort_id",
                "cohort_digest",
                "window_start_utc",
                "window_end_utc",
                "data_through_utc",
                "registry_snapshot_id",
                "registry_snapshot_digest",
                "binding_generation",
            )
        }

    def _operator_output(
        self,
        operator_id: str,
        *,
        plan: dict,
        result: dict,
        input_digests: list[str],
        parameter_profile_id=None,
        parameter_profile_digest=None,
    ) -> dict:
        output = {
            "identity": self._operator_identity(plan),
            "operator_id": operator_id,
            "operator_version": "1.0.0-design",
            "parameter_profile_id": parameter_profile_id,
            "parameter_profile_digest": parameter_profile_digest,
            "input_digests": input_digests,
            "input_completeness": "complete",
            "result_state": "computed",
            "completeness": "complete",
            "result": result,
            "evidence_refs": [
                {"evidence_id": "EV1", "source_digest": self._hex("e"), "member_key": "M1"}
            ],
            "fact_lineage": input_digests,
        }
        output["output_digest"] = self._digest(output)
        return output

    def _operator_artifact(self, operator_id: str, output: dict) -> dict:
        artifact = {
            "receipt_kind": "registered_operator_output",
            "operator_id": operator_id,
            "operator_version": output["operator_version"],
            "contract_digest": self._hex("1"),
            "output_schema_ref": (
                f"operator-contract.schema.json#/$defs/op{operator_id.split('-')[1]}OutputEnvelope"
            ),
            "operator_output": output,
            "output_digest": output["output_digest"],
            "disposition": "passed",
        }
        artifact["receipt_digest"] = self._digest(artifact)
        return artifact

    def _registry_entry(self, unit_id: str = "TOOL-07", unit_kind: str = "tool") -> dict:
        input_schema_ref = (
            f"https://domeye.example/registry-inputs/{unit_id.lower()}.schema.json"
        )
        output_schema_ref = "https://domeye.example/facts/as-prefix-membership.json"
        member_schema_ref = (
            "https://domeye.example/facts/as-prefix-membership-member.schema.json"
        )
        return {
            "unit_kind": unit_kind,
            "unit_version": "1.0.0",
            "contract_digest": "sha256:" + self._hex("1"),
            "implementation_digest": "sha256:" + self._hex("2"),
            "semantic_digest": "sha256:" + self._hex("3"),
            "atomic_capability_id": "CAP-P2-001",
            "atomic_capability_version": "1.0.0",
            "capability_contract_digest": "sha256:" + self._hex("4"),
            "lifecycle_state": "active",
            "p2_v1_admission": "allowed",
            "input_schema_ref": input_schema_ref,
            "input_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": input_schema_ref,
                "type": "object",
                "required": ["asn"],
                "properties": {"asn": {"type": "integer", "minimum": 0}},
                "additionalProperties": False,
            },
            "output_schema_refs": [output_schema_ref],
            "output_populations": [
                {
                    "population_id": "as_prefix_membership",
                    "member_schema_ref": member_schema_ref,
                    "member_schema": {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "$id": member_schema_ref,
                        "type": "object",
                        "required": ["id", "rank"],
                        "properties": {
                            "id": {"type": "string", "minLength": 1},
                            "rank": {"type": "integer", "minimum": 0},
                        },
                        "additionalProperties": False,
                    },
                }
            ],
            "monotonic_incomplete_input_allowed": False,
        }

    def _registry_view(self, entries: dict[str, dict]) -> dict:
        resolution_receipt = {
            "receipt_kind": "registry_snapshot_resolution",
            "resolver_id": "country_outage_p2_registry_resolver",
            "resolver_version": "1.0.0",
            "resolver_contract_digest": HOOK.REGISTRY_RESOLVER_CONTRACT_DIGEST,
            "resolver_implementation_digest": (
                HOOK.REGISTRY_RESOLVER_IMPLEMENTATION_DIGEST
            ),
            "registry_snapshot_id": "registry-snapshot-sha256:" + self._hex("5"),
            "registry_snapshot_digest": "sha256:" + self._hex("6"),
            "registry_snapshot_data_digest": self._hex("6"),
            "entries_digest": self._digest({"entries": entries}),
            "disposition": "passed",
        }
        resolution_receipt["receipt_digest"] = self._digest(resolution_receipt)
        view = {
            "view_contract_id": "country_outage_p2_registry_admission_view_v1",
            "trusted_snapshot_verified": True,
            "registry_snapshot_id": "registry-snapshot-sha256:" + self._hex("5"),
            "registry_snapshot_digest": "sha256:" + self._hex("6"),
            "registry_snapshot_data_digest": self._hex("6"),
            "entries": entries,
            "resolution_receipt": resolution_receipt,
            "resolution_receipt_digest": resolution_receipt["receipt_digest"],
        }
        view["view_digest"] = self._digest(view)
        return view

    @staticmethod
    def _registry_store(view: dict) -> dict:
        return {
            "store_contract_id": "country_outage_p2_trusted_registry_store_v1",
            "trust_origin": "host_authenticated_registry_store",
            "attestation_provider_id": "country_outage_p2_registry_store_host",
            "attestation_contract_digest": (
                HOOK.REGISTRY_STORE_ATTESTATION_CONTRACT_DIGEST
            ),
            "snapshot_views": {view["registry_snapshot_id"]: view},
        }

    def _node_result_receipt_store(
        self, plan: dict, *, node_id: str = "N1"
    ) -> dict:
        definition = plan["plan_definition"]
        executions = [
            item
            for item in plan["investigation_snapshot"]["node_execution_revisions"]
            if item["node_id"] == node_id
            and item["state"] in {"committed", "reused"}
        ]
        execution = max(executions, key=lambda item: item["execution_revision"])
        receipt = self._receipt(
            receipt_kind="committed_node_execution_result",
            validator_id=HOOK.NODE_RESULT_VALIDATOR_ID,
            validator_version=HOOK.NODE_RESULT_VALIDATOR_VERSION,
            validator_contract_digest=HOOK.NODE_RESULT_VALIDATOR_CONTRACT_DIGEST,
            validator_implementation_digest=(
                HOOK.NODE_RESULT_VALIDATOR_IMPLEMENTATION_DIGEST
            ),
            plan_id=definition["plan_id"],
            plan_revision=definition["plan_revision"],
            node_id=node_id,
            execution_revision=execution["execution_revision"],
            input_digest=execution["input_digest"],
            result_digest=execution["result_digest"],
            registry_snapshot_id=definition["registry_snapshot_id"],
            registry_snapshot_digest=definition["registry_snapshot_digest"],
            transaction_commit_digest=self._hex("7"),
            disposition=execution["state"],
        )
        execution["receipt_digest"] = receipt["receipt_digest"]
        plan["investigation_snapshot"]["snapshot_digest"] = self._digest(
            plan["investigation_snapshot"], "snapshot_digest"
        )
        return {
            "store_contract_id": "country_outage_p2_trusted_node_result_receipt_store_v1",
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "attestation_provider_id": "country_outage_p2_node_result_store_host",
            "attestation_contract_digest": (
                HOOK.NODE_RESULT_STORE_ATTESTATION_CONTRACT_DIGEST
            ),
            "receipts": {receipt["receipt_digest"]: receipt},
        }

    def _add_ancestor_result_consumer(
        self, plan: dict, *, source_artifact_digest: str
    ) -> None:
        definition = plan["plan_definition"]
        second = json.loads(json.dumps(definition["nodes"][0]))
        second["node_id"] = "N2"
        second["depends_on"] = ["N1"]
        second["wave"] = 1
        self.plan_parameters["N2"] = {"asn": 49666}
        binding = {
            "input_name": "asn",
            "source_kind": "node_result",
            "source_ref": "N1",
            "source_artifact_digest": source_artifact_digest,
        }
        binding["source_digest"] = self._digest(
            {
                "input_name": "asn",
                "source_kind": "node_result",
                "source_ref": "N1",
                "bound_parameter_value": 49666,
            }
        )
        second["input_bindings"] = [binding]
        second["parameters_digest"] = self._digest(
            {"parameters": self.plan_parameters["N2"]}
        )
        definition["nodes"].append(second)
        definition["dag_digest"] = self._digest({"nodes": definition["nodes"]})
        execution = json.loads(
            json.dumps(
                plan["investigation_snapshot"]["node_execution_revisions"][0]
            )
        )
        execution["node_id"] = "N2"
        plan["investigation_snapshot"]["node_execution_revisions"].append(
            execution
        )
        plan["investigation_snapshot"]["snapshot_digest"] = self._digest(
            plan["investigation_snapshot"], "snapshot_digest"
        )

    def _identity(self, registry_view: dict) -> dict:
        identity = {
            "incident_id": "INCIDENT-1",
            "publication_id": "PUBLICATION-1",
            "publication_revision": 1,
            "publication_digest": self._hex("8"),
            "collector_id": "rrc25",
            "cohort_id": "COHORT-1",
            "cohort_digest": self._hex("9"),
            "window_start_utc": "2026-02-27T00:10:00Z",
            "window_end_utc": "2026-03-11T00:00:00Z",
            "data_through_utc": "2026-03-11T00:00:00Z",
            "finality": "event_end_unknown",
            "registry_snapshot_id": registry_view["registry_snapshot_id"],
            "registry_snapshot_digest": registry_view["registry_snapshot_digest"],
            "binding_generation": 1,
        }
        identity["binding_digest"] = self._digest(identity)
        return identity

    @staticmethod
    def _design_boundary() -> dict:
        return {
            "design_only": True,
            "runtime_implemented": False,
            "production_deployed": False,
        }

    def _valid_plan_instance(self) -> dict:
        self.plan_schema = self._schema("investigation-plan.schema.json")
        entry = self._registry_entry()
        self.plan_registry_view = self._registry_view({"TOOL-07": entry})
        self.plan_registry = self._registry_store(self.plan_registry_view)
        identity = self._identity(self.plan_registry_view)
        budget = {
            "max_wall_ms": 1000,
            "max_rows": 100,
            "max_bytes": 100000,
            "max_cost_amount": 1,
            "cost_currency": "USD",
        }
        unit = {
            "unit_id": "TOOL-07",
            "unit_kind": "tool",
            "unit_version": entry["unit_version"],
            "contract_digest": entry["contract_digest"],
            "unit_implementation_digest": entry["implementation_digest"],
            "unit_semantic_digest": entry["semantic_digest"],
            "atomic_capability_id": entry["atomic_capability_id"],
            "atomic_capability_version": entry["atomic_capability_version"],
            "capability_contract_digest": entry["capability_contract_digest"],
        }
        self.plan_parameters = {"N1": {"asn": 49666}}
        asn_binding = {
            "input_name": "asn",
            "source_kind": "user_parameter",
            "source_ref": "user-parameter:asn",
            "source_artifact_digest": None,
        }
        asn_binding["source_digest"] = self._digest(
            {
                "input_name": asn_binding["input_name"],
                "source_kind": asn_binding["source_kind"],
                "source_ref": asn_binding["source_ref"],
                "bound_parameter_value": self.plan_parameters["N1"]["asn"],
            }
        )
        node = {
            "node_id": "N1",
            "execution_unit": unit,
            "depends_on": [],
            "dependency_mode": "hard",
            "requiredness": "required",
            "wave": 0,
            "input_bindings": [asn_binding],
            "parameters_digest": self._digest(
                {"parameters": self.plan_parameters["N1"]}
            ),
            "expected_output_schema_ref": entry["output_schema_refs"][0],
            "completeness_requirement": "complete",
            "incomplete_input_policy": "fail_closed",
            "budget_allocation": budget,
            "permission_scope_id": "country_outage:read",
            "cancellation_policy": "cooperative",
        }
        definition = {
            "plan_id": "PLAN-1",
            "plan_revision": 1,
            "parent_plan_revision": None,
            "plan_state": "admitted",
            "goal_digest": self._hex("b"),
            "identity": identity,
            "registry_snapshot_id": self.plan_registry_view["registry_snapshot_id"],
            "registry_snapshot_digest": self.plan_registry_view[
                "registry_snapshot_digest"
            ],
            "nodes": [node],
            "dag_digest": self._digest({"nodes": [node]}),
            "budget": budget,
            "answer_execution_policy": {
                "teacher_required": True,
                "mode": "sol_teacher_then_ds_student",
                "authorization_digest": None,
            },
            "permission_set_digest": self._hex("c"),
            "admission_receipt_digest": None,
        }
        admission_receipt = self._receipt(
            receipt_kind="plan_admission",
            validator_id=HOOK.PLAN_ADMISSION_VALIDATOR_ID,
            validator_version=HOOK.PLAN_ADMISSION_VALIDATOR_VERSION,
            validator_contract_digest=(
                HOOK.PLAN_ADMISSION_VALIDATOR_CONTRACT_DIGEST
            ),
            validator_implementation_digest=(
                HOOK.PLAN_ADMISSION_VALIDATOR_IMPLEMENTATION_DIGEST
            ),
            plan_id=definition["plan_id"],
            plan_revision=definition["plan_revision"],
            plan_subject_digest=self._digest(definition, "admission_receipt_digest"),
            identity_digest=identity["binding_digest"],
            dag_digest=definition["dag_digest"],
            registry_snapshot_id=definition["registry_snapshot_id"],
            registry_snapshot_digest=definition["registry_snapshot_digest"],
            parameter_bindings_digest=self._digest(
                {"parameter_bindings": self.plan_parameters}
            ),
            disposition="passed",
        )
        definition["admission_receipt_digest"] = admission_receipt["receipt_digest"]
        self.plan_receipts = {admission_receipt["receipt_digest"]: admission_receipt}
        snapshot = {
            "investigation_id": "INVESTIGATION-1",
            "investigation_revision": 1,
            "parent_investigation_revision": None,
            "plan_id": definition["plan_id"],
            "plan_revision": 1,
            "status": "completed",
            "node_execution_revisions": [
                {
                    "node_id": "N1",
                    "execution_revision": 1,
                    "parent_execution_revision": None,
                    "state": "committed",
                    "idempotency_key": "NODE-1-R1",
                    "input_digest": self._hex("e"),
                    "result_digest": self._hex("f"),
                    "receipt_digest": self._hex("0"),
                    "failure_code": None,
                }
            ],
            "evidence_graph_revision": 1,
        }
        snapshot["snapshot_digest"] = self._digest(snapshot)
        return {
            "schema_version": "country_outage_p2_investigation_plan_v1",
            "plan_definition": definition,
            "investigation_snapshot": snapshot,
            "design_boundary": self._design_boundary(),
        }

    def _valid_result_set_instance(self) -> tuple[dict, list[dict]]:
        self.result_schema = self._schema("result-set.schema.json")
        entry = self._registry_entry()
        self.result_registry_view = self._registry_view({"TOOL-07": entry})
        self.result_registry = self._registry_store(self.result_registry_view)
        identity = self._identity(self.result_registry_view)
        identity["registry_snapshot_digest"] = self.result_registry_view[
            "registry_snapshot_data_digest"
        ]
        identity["identity_digest"] = identity.pop("binding_digest")
        identity["identity_digest"] = self._digest(identity, "identity_digest")
        source_tool = {
            "tool_id": "TOOL-07",
            "tool_version": entry["unit_version"],
            "contract_digest": entry["contract_digest"].removeprefix("sha256:"),
            "tool_run_id": "TOOL-RUN-1",
        }
        query = {"asn": 49666}
        stable_sort = [
            {"field": "rank", "direction": "ASC", "nulls": "FORBIDDEN"}
        ]
        members = [{"id": "A", "rank": 1}]
        population = entry["output_populations"][0]
        population_schema_ref = population["member_schema_ref"]
        population_schema_digest = self._digest(population["member_schema"])
        query_digest = self._digest({"normalized_query": query})
        sort_digest = self._digest({"stable_sort": stable_sort})
        query_receipt = self._receipt(
            receipt_kind="query",
            query_digest=query_digest,
            identity_digest=identity["identity_digest"],
            tool_run_id=source_tool["tool_run_id"],
            source_population_id="as_prefix_membership",
            source_population_schema_digest=population_schema_digest,
            source_dataset_digest=self._hex("a"),
            disposition="passed",
        )
        segment = {
            "segment_ref": "SEGMENT-0",
            "page_index": 0,
            "member_count": 1,
            "segment_digest": self._digest({"members": members}),
        }
        page_content_digest = self._digest(
            {
                "page_index": 0,
                "member_segment_ref": segment["segment_ref"],
                "members": members,
            }
        )
        page_receipt = self._receipt(
            receipt_kind="page",
            page_index=0,
            page_content_digest=page_content_digest,
            identity_digest=identity["identity_digest"],
            source_population_id="as_prefix_membership",
            source_population_schema_digest=population_schema_digest,
            source_dataset_digest=self._hex("a"),
            disposition="passed",
        )
        page = {
            "page_index": 0,
            "token_in": None,
            "token_out": None,
            "identity_digest": identity["identity_digest"],
            "query_digest": query_digest,
            "stable_sort_digest": sort_digest,
            "source_population_id": "as_prefix_membership",
            "source_population_schema_digest": population_schema_digest,
            "source_dataset_digest": self._hex("a"),
            "first_sort_key": [1],
            "last_sort_key": [1],
            "member_count": 1,
            "member_segment_ref": segment["segment_ref"],
            "page_content_digest": page_content_digest,
            "page_receipt_digest": page_receipt["receipt_digest"],
            "evidence_refs": ["EV1"],
        }
        result_id = "result-set-sha256:" + self._digest(
            {
                "source_identity": identity,
                "source_tool": source_tool,
                "normalized_query": query,
                "stable_sort": stable_sort,
                "source_population_id": "as_prefix_membership",
                "source_population_schema_ref": population_schema_ref,
                "source_population_schema_digest": population_schema_digest,
                "source_dataset_digest": self._hex("a"),
            }
        )
        manifest_digest = self._digest(
            {"page_manifest": [page], "member_segments": [segment]}
        )
        content_digest = self._digest({"members": members})
        freeze_receipt = self._receipt(
            receipt_kind="freeze",
            result_set_id=result_id,
            manifest_digest=manifest_digest,
            content_digest=content_digest,
            returned_count=1,
            total_count=1,
            set_completeness="complete",
            source_population_id="as_prefix_membership",
            source_population_schema_digest=population_schema_digest,
            source_dataset_digest=self._hex("a"),
            disposition="passed",
        )
        self.result_receipts = {
            item["receipt_digest"]: item
            for item in (query_receipt, page_receipt, freeze_receipt)
        }
        payload = {
            "schema_version": "country_outage_p2_result_set_v1",
            "result_set_id": result_id,
            "result_set_revision": 1,
            "parent_result_set_revision": None,
            "state": "frozen",
            "source_identity": identity,
            "source_tool": source_tool,
            "normalized_query": query,
            "query_digest": query_digest,
            "stable_sort": stable_sort,
            "stable_sort_digest": sort_digest,
            "source_population_id": "as_prefix_membership",
            "source_population_schema_ref": population_schema_ref,
            "source_population_schema_digest": population_schema_digest,
            "source_dataset_digest": self._hex("a"),
            "member_identity": "id",
            "dedupe_key": ["id"],
            "page_manifest": [page],
            "returned_count": 1,
            "total_count": 1,
            "set_completeness": "complete",
            "resume_page_token": None,
            "member_segments": [segment],
            "query_receipt_digest": query_receipt["receipt_digest"],
            "source_completeness_receipt_digest": None,
            "evidence_refs": ["EV1"],
            "limitations": [],
            "manifest_digest": manifest_digest,
            "content_digest": content_digest,
            "freeze_receipt_digest": freeze_receipt["receipt_digest"],
            "preview_views": [],
            "generation_origin": "tool_pagination_without_llm_member_generation",
            "design_boundary": self._design_boundary(),
        }
        return payload, members

    def _attach_source_incomplete_receipt(self, payload: dict) -> None:
        receipt = self._receipt(
            receipt_kind="source_completeness",
            validator_id=HOOK.SOURCE_COMPLETENESS_VALIDATOR_ID,
            validator_version=HOOK.SOURCE_COMPLETENESS_VALIDATOR_VERSION,
            validator_contract_digest=(
                HOOK.SOURCE_COMPLETENESS_VALIDATOR_CONTRACT_DIGEST
            ),
            validator_implementation_digest=(
                HOOK.SOURCE_COMPLETENESS_VALIDATOR_IMPLEMENTATION_DIGEST
            ),
            tool_run_id=payload["source_tool"]["tool_run_id"],
            source_population_id=payload["source_population_id"],
            source_dataset_digest=payload["source_dataset_digest"],
            source_completeness="source_incomplete",
            limitations_digest=self._digest(
                {"limitations": payload["limitations"]}
            ),
            returned_count=payload["returned_count"],
            total_count=payload["total_count"],
            resume_page_token=payload["resume_page_token"],
            disposition="passed",
        )
        payload["source_completeness_receipt_digest"] = receipt["receipt_digest"]
        self.result_receipts[receipt["receipt_digest"]] = receipt

    def _valid_evidence_graph_instance(self) -> dict:
        self.evidence_schema = self._schema("evidence-graph.schema.json")
        plan = self._valid_plan_instance()
        entry = self._registry_entry()
        self.evidence_registry_view = self._registry_view({"TOOL-07": entry})
        self.evidence_registry = self._registry_store(self.evidence_registry_view)
        payload_body = {
            "payload_type": "observed_fact",
            "fact_id": "FACT-1",
            "fact_schema_ref": entry["output_schema_refs"][0],
            "fact_value_digest": self._hex("a"),
            "source_result_set_ref": None,
            "fact_value_projection": None,
        }
        payload_digest = self._digest({"payload": payload_body})
        producer_receipt = self._receipt(
            receipt_kind="producer",
            producer_id="TOOL-07",
            output_digest=payload_digest,
            disposition="committed",
        )
        graph_identity_digest = HOOK._plan_identity_data_projection_digest(
            plan["plan_definition"]["identity"]
        )
        node = {
            "node_id": "F1",
            "node_type": "observed_fact",
            "identity_digest": graph_identity_digest,
            "producer_ref": {
                "producer_kind": "tool",
                "producer_id": "TOOL-07",
                "producer_version": entry["unit_version"],
                "contract_digest": entry["contract_digest"].removeprefix("sha256:"),
                "run_receipt_digest": producer_receipt["receipt_digest"],
            },
            "provenance_node_ids": [],
            "evidence_refs": ["EV1"],
            "completeness": "complete",
            "payload": payload_body,
            "payload_digest": payload_digest,
            "committed": True,
        }
        graph = {
            "schema_version": "country_outage_p2_evidence_graph_v1",
            "graph_id": "GRAPH-1",
            "graph_revision": 1,
            "parent_graph_revision": None,
            "graph_state": "committed",
            "investigation_id": plan["investigation_snapshot"]["investigation_id"],
            "investigation_revision": plan["investigation_snapshot"]["investigation_revision"],
            "plan_id": plan["plan_definition"]["plan_id"],
            "plan_revision": plan["plan_definition"]["plan_revision"],
            "plan_digest": self._digest({"plan_definition": plan["plan_definition"]}),
            "identity_digest": node["identity_digest"],
            "registry_snapshot_id": self.evidence_registry_view[
                "registry_snapshot_id"
            ],
            "registry_snapshot_digest": self.evidence_registry_view[
                "registry_snapshot_data_digest"
            ],
            "nodes": [node],
            "edges": [],
            "root_node_ids": ["F1"],
            "closure_receipt_digest": None,
            "graph_digest": self._hex("b"),
            "commit_receipt_digest": None,
            "design_boundary": self._design_boundary(),
        }
        graph["graph_digest"] = self._digest(
            {
                key: graph[key]
                for key in (
                    "graph_id",
                    "graph_revision",
                    "parent_graph_revision",
                    "investigation_id",
                    "investigation_revision",
                    "plan_id",
                    "plan_revision",
                    "plan_digest",
                    "identity_digest",
                    "registry_snapshot_id",
                    "registry_snapshot_digest",
                    "nodes",
                    "edges",
                    "root_node_ids",
                )
            }
        )
        closure = self._receipt(
            receipt_kind="graph_closure",
            graph_digest=graph["graph_digest"],
            disposition="passed",
        )
        commit = self._receipt(
            receipt_kind="graph_commit",
            graph_digest=graph["graph_digest"],
            disposition="committed",
        )
        graph["closure_receipt_digest"] = closure["receipt_digest"]
        graph["commit_receipt_digest"] = commit["receipt_digest"]
        self.evidence_plan = plan
        self.evidence_result_sets = {}
        self.evidence_receipts = {
            item["receipt_digest"]: item
            for item in (producer_receipt, closure, commit)
        }
        return graph

    def _gate_receipt(
        self,
        *,
        subject_digest: str,
        binding_digest: str,
        passed: bool = True,
        validation_id: str = "VALIDATION-1",
    ) -> dict:
        gate_results = []
        for index in range(1, 6):
            gate = {"gate_id": f"GATE-0{index}", "passed": passed}
            gate["receipt_digest"] = self._digest(gate)
            gate_results.append(gate)
        receipt = {
            "validation_id": validation_id,
            "subject_digest": subject_digest,
            "shared_answer_binding_digest": binding_digest,
            "gate_results": gate_results,
            "all_gates_passed": passed,
        }
        receipt["receipt_digest"] = self._digest(receipt)
        return receipt

    def _model_identity(self, provider: str, model: str, version: str) -> dict:
        identity = {"provider": provider, "model": model, "version": version}
        identity["identity_digest"] = self._digest(identity)
        return identity

    @staticmethod
    def _run_cost() -> dict:
        return {
            "latency_ms": 10,
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_amount": 0.01,
            "cost_currency": "USD",
            "retry_count": 0,
        }

    def _valid_dual_model_instance(self) -> dict:
        self.dual_schema = self._schema("dual-model-answer-flow.schema.json")
        evidence = self._valid_evidence_graph_instance()
        graph_validation_context = {
            "schema": self.evidence_schema,
            "plan_definition": self.evidence_plan["plan_definition"],
            "investigation_snapshot": self.evidence_plan[
                "investigation_snapshot"
            ],
            "trusted_registry_store": self.evidence_registry,
            "result_set_records": [],
            "result_set_member_records": [],
            "receipt_store": self.evidence_receipts,
            "operator_contract_schema": self._operator_contract_schema(),
            "previous_graph": None,
        }
        graph_validation = self._receipt(
            receipt_kind="validated_committed_evidence_graph",
            validator_id=HOOK.COMMITTED_GRAPH_VALIDATOR_ID,
            validator_version=HOOK.COMMITTED_GRAPH_VALIDATOR_VERSION,
            validator_contract_digest=HOOK.COMMITTED_GRAPH_VALIDATOR_CONTRACT_DIGEST,
            validator_implementation_digest=(
                HOOK.COMMITTED_GRAPH_VALIDATOR_IMPLEMENTATION_DIGEST
            ),
            graph_id=evidence["graph_id"],
            graph_revision=evidence["graph_revision"],
            graph_digest=evidence["graph_digest"],
            graph_state=evidence["graph_state"],
            plan_revision=evidence["plan_revision"],
            plan_digest=evidence["plan_digest"],
            identity_digest=evidence["identity_digest"],
            registry_snapshot_id=evidence["registry_snapshot_id"],
            registry_snapshot_digest=evidence["registry_snapshot_digest"],
            validation_context_digest=self._digest(
                {"validation_context": graph_validation_context}
            ),
            disposition="passed",
        )
        self.dual_graph_store = {
            "store_contract_id": "country_outage_p2_trusted_committed_graph_store_v1",
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "attestation_provider_id": "country_outage_p2_committed_graph_store_host",
            "attestation_contract_digest": (
                HOOK.COMMITTED_GRAPH_STORE_ATTESTATION_CONTRACT_DIGEST
            ),
            "graphs": {
                evidence["graph_digest"]: {
                    "graph": evidence,
                    "validation_receipt": graph_validation,
                    "validation_context": graph_validation_context,
                }
            },
        }
        evidence_identity = self.evidence_plan["plan_definition"]["identity"]
        binding = {
            "question_id": "Q05",
            "question_digest": self._hex("1"),
            "goal_digest": self._hex("2"),
            "incident_id": "INCIDENT-1",
            "publication_id": "PUBLICATION-1",
            "publication_revision": 1,
            "publication_digest": evidence_identity["publication_digest"],
            "collector_id": "rrc25",
            "cohort_id": "COHORT-1",
            "cohort_digest": evidence_identity["cohort_digest"],
            "window_start_utc": "2026-02-27T00:10:00Z",
            "window_end_utc": "2026-03-11T00:00:00Z",
            "data_through_utc": "2026-03-11T00:00:00Z",
            "finality": "event_end_unknown",
            "binding_generation": 1,
            "grounding_plan_digest": self._hex("5"),
            "plan_id": evidence["plan_id"],
            "plan_revision": 1,
            "investigation_plan_digest": evidence["plan_digest"],
            "evidence_bundle_digest": self._hex("6"),
            "evidence_graph_revision": evidence["graph_revision"],
            "evidence_graph_digest": evidence["graph_digest"],
            "registry_snapshot_id": evidence["registry_snapshot_id"],
            "registry_snapshot_digest": evidence["registry_snapshot_digest"],
            "boundary_policy_digest": self._hex("7"),
            "world_knowledge_bundle_digest": None,
            "world_knowledge_policy": "explanation_and_hypothesis_only_not_event_evidence",
            "prompt_version": "prompt-v1",
            "prompt_digest": self._hex("8"),
            "policy_version": "policy-v1",
            "policy_digest": self._hex("9"),
        }
        binding_digest = self._digest(binding)
        teacher_identity = self._model_identity("openai", "gpt-5.6-sol", "2026-08-12")
        student_identity = self._model_identity(
            "deepseek", "deepseek-v4-flash", "deepseek-v4-flash"
        )
        teacher_reference = {
            "teacher_reference_id": "TEACHER-REFERENCE-1",
            "shared_answer_binding_digest": binding_digest,
            "required_fact_ids": ["FACT-1"],
            "evidence_refs": ["EV1"],
            "boundary_assertions": ["rrc25_control_plane_only"],
            "unknowns": ["event_end_unknown"],
            "answer_outline": ["关键发现", "证据边界"],
            "teacher_reference_is_ground_truth": False,
            "private_chain_of_thought_persisted": False,
        }
        teacher_reference["output_digest"] = self._digest(teacher_reference)
        teacher_validation = self._gate_receipt(
            subject_digest=teacher_reference["output_digest"],
            binding_digest=binding_digest,
            validation_id="TEACHER-VALIDATION-1",
        )
        teacher_run = {
            "run_id": "TEACHER-RUN-1",
            "role": "teacher",
            "exact_model_identity": teacher_identity,
            "shared_answer_binding_digest": binding_digest,
            "role_specific_input_digest": self._digest(
                {"role": "teacher", "shared_answer_binding_digest": binding_digest}
            ),
            "output_digest": teacher_reference["output_digest"],
            "validation_receipt_digest": teacher_validation["receipt_digest"],
            "cost": self._run_cost(),
            "disposition": "completed",
        }
        student_answer_digest = self._hex("a")
        student_validation = self._gate_receipt(
            subject_digest=student_answer_digest,
            binding_digest=binding_digest,
            validation_id="STUDENT-VALIDATION-1",
        )
        student_run_receipt = {
            "run_id": "STUDENT-RUN-1",
            "role": "student",
            "exact_model_identity": student_identity,
            "shared_answer_binding_digest": binding_digest,
            "role_specific_input_digest": self._digest(
                {
                    "role": "student",
                    "revision_ordinal": 0,
                    "shared_answer_binding_digest": binding_digest,
                    "teacher_reference_digest": teacher_reference["output_digest"],
                    "teacher_validation_receipt_digest": teacher_validation[
                        "receipt_digest"
                    ],
                    "structured_feedback_digest": None,
                }
            ),
            "output_digest": student_answer_digest,
            "validation_receipt_digest": student_validation["receipt_digest"],
            "cost": self._run_cost(),
            "disposition": "completed",
        }
        student_run = {
            "revision_ordinal": 0,
            "run_receipt": student_run_receipt,
            "teacher_reference_digest": teacher_reference["output_digest"],
            "teacher_validation_receipt_digest": teacher_validation["receipt_digest"],
            "student_answer_digest": student_answer_digest,
            "validation_receipt": student_validation,
            "may_call_tools": False,
            "may_add_event_facts": False,
        }
        alignment = {
            "alignment_run_id": "ALIGNMENT-1",
            "shared_answer_binding_digest": binding_digest,
            "teacher_reference_digest": teacher_reference["output_digest"],
            "student_answer_digest": student_answer_digest,
            "hard_gate_metrics": {
                "fact_precision": 1,
                "evidence_ref_precision": 1,
                "boundary_compliance": 1,
            },
            "advisory_text_similarity": 0.8,
            "hard_gates_passed": True,
            "disposition": "passed",
        }
        alignment["receipt_digest"] = self._digest(alignment)
        published = {
            "shared_answer_binding_digest": binding_digest,
            "claims_digest": self._hex("b"),
            "evidence_refs": ["EV1"],
            "limitations": ["single_rrc25_collector"],
            "unknowns": ["event_end_unknown"],
            "aligned_claim": True,
            "event_causality_claimed": False,
            "recovery_claimed": False,
        }
        published["answer_digest"] = self._digest(published)
        self.dual_publish_receipt = self._receipt(
            receipt_kind="answer_publish",
            answer_digest=published["answer_digest"],
            student_answer_digest=student_answer_digest,
            shared_answer_binding_digest=binding_digest,
            disposition="committed",
        )
        self.dual_evidence_graph = evidence
        return {
            "schema_version": "country_outage_p2_dual_model_answer_flow_v1",
            "flow_id": "FLOW-1",
            "flow_revision": 1,
            "parent_flow_revision": None,
            "flow_state": "published",
            "execution_order": [
                "gpt-5.6-sol",
                "teacher_reference_validator",
                "ds_student",
                "student_answer_validator",
                "alignment_evaluator",
            ],
            "default_teacher_required": True,
            "effective_teacher_required": True,
            "shared_answer_binding_digest": binding_digest,
            "shared_answer_binding": binding,
            "teacher_model_identity": teacher_identity,
            "student_model_identity": student_identity,
            "teacher_run_receipt": teacher_run,
            "teacher_reference": teacher_reference,
            "teacher_validation_receipt": teacher_validation,
            "student_runs": [student_run],
            "structured_feedback": None,
            "student_validation_receipt": student_validation,
            "alignment_run_receipt": alignment,
            "degraded_authorization": None,
            "published_answer": published,
            "publish_receipt_digest": self.dual_publish_receipt["receipt_digest"],
            "final_disposition": "aligned_published",
            "design_boundary": {
                "design_only": True,
                "model_calls_implemented": False,
                "runtime_integrated": False,
                "production_deployed": False,
            },
        }

    def _valid_degraded_dual_model_instance(self) -> dict:
        payload = self._valid_dual_model_instance()
        authorization = {
            "authorization_id": "AUTH-DEGRADED-1",
            "user_confirmed": True,
            "mode": "ds_unaligned_degraded",
            "parent_plan_revision": 1,
            "new_plan_revision": 2,
            "may_claim_sol_ds_alignment": False,
            "authorization_digest": None,
        }
        authorization["authorization_digest"] = self._digest(
            authorization, "authorization_digest"
        )

        degraded_plan = json.loads(json.dumps(self.evidence_plan))
        definition = degraded_plan["plan_definition"]
        definition["plan_revision"] = 2
        definition["parent_plan_revision"] = 1
        definition["answer_execution_policy"] = {
            "teacher_required": False,
            "mode": "ds_unaligned_explicitly_authorized",
            "authorization_digest": authorization["authorization_digest"],
        }
        definition["admission_receipt_digest"] = None
        degraded_admission = self._receipt(
            receipt_kind="plan_admission",
            validator_id=HOOK.PLAN_ADMISSION_VALIDATOR_ID,
            validator_version=HOOK.PLAN_ADMISSION_VALIDATOR_VERSION,
            validator_contract_digest=HOOK.PLAN_ADMISSION_VALIDATOR_CONTRACT_DIGEST,
            validator_implementation_digest=(
                HOOK.PLAN_ADMISSION_VALIDATOR_IMPLEMENTATION_DIGEST
            ),
            plan_id=definition["plan_id"],
            plan_revision=definition["plan_revision"],
            plan_subject_digest=self._digest(
                definition, "admission_receipt_digest"
            ),
            identity_digest=definition["identity"]["binding_digest"],
            dag_digest=definition["dag_digest"],
            registry_snapshot_id=definition["registry_snapshot_id"],
            registry_snapshot_digest=definition["registry_snapshot_digest"],
            parameter_bindings_digest=self._digest(
                {"parameter_bindings": self.plan_parameters}
            ),
            disposition="passed",
        )
        definition["admission_receipt_digest"] = degraded_admission[
            "receipt_digest"
        ]
        snapshot = degraded_plan["investigation_snapshot"]
        snapshot["investigation_revision"] = 2
        snapshot["parent_investigation_revision"] = 1
        snapshot["plan_revision"] = 2
        execution = json.loads(
            json.dumps(snapshot["node_execution_revisions"][0])
        )
        execution["execution_revision"] = 2
        execution["parent_execution_revision"] = 1
        execution["state"] = "reused"
        snapshot["node_execution_revisions"].append(execution)
        snapshot["evidence_graph_revision"] = 2
        snapshot["snapshot_digest"] = self._digest(snapshot, "snapshot_digest")
        plan_digest = self._digest({"plan_definition": definition})
        plan_validation_context = {
            "trusted_registry_store": self.plan_registry,
            "trusted_admission_receipt_store": {
                degraded_admission["receipt_digest"]: degraded_admission
            },
            "trusted_node_result_receipt_store": None,
            "parameter_bindings": self.plan_parameters,
            "previous_plan_definition": self.evidence_plan["plan_definition"],
            "previous_investigation_snapshot": self.evidence_plan[
                "investigation_snapshot"
            ],
        }
        plan_validation = self._receipt(
            receipt_kind="validated_investigation_plan",
            validator_id=HOOK.VALIDATED_PLAN_VALIDATOR_ID,
            validator_version=HOOK.VALIDATED_PLAN_VALIDATOR_VERSION,
            validator_contract_digest=HOOK.VALIDATED_PLAN_VALIDATOR_CONTRACT_DIGEST,
            validator_implementation_digest=(
                HOOK.VALIDATED_PLAN_VALIDATOR_IMPLEMENTATION_DIGEST
            ),
            plan_id=definition["plan_id"],
            plan_revision=definition["plan_revision"],
            plan_payload_digest=self._digest({"plan": degraded_plan}),
            plan_digest=plan_digest,
            admission_receipt_digest=definition["admission_receipt_digest"],
            effective_teacher_required=False,
            authorization_digest=authorization["authorization_digest"],
            validation_context_digest=self._digest(
                {"validation_context": plan_validation_context}
            ),
            disposition="passed",
        )
        self.dual_plan_store = {
            "store_contract_id": "country_outage_p2_trusted_validated_plan_store_v1",
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "attestation_provider_id": "country_outage_p2_validated_plan_store_host",
            "attestation_contract_digest": (
                HOOK.VALIDATED_PLAN_STORE_ATTESTATION_CONTRACT_DIGEST
            ),
            "plans": {
                f"{definition['plan_id']}@2": {
                    "plan": degraded_plan,
                    "validation_receipt": plan_validation,
                    "validation_context": plan_validation_context,
                }
            },
        }

        previous_graph = self.dual_evidence_graph
        graph = json.loads(json.dumps(previous_graph))
        graph["graph_revision"] = 2
        graph["parent_graph_revision"] = 1
        graph["investigation_revision"] = 2
        graph["plan_revision"] = 2
        graph["plan_digest"] = plan_digest
        graph["graph_digest"] = HOOK._evidence_graph_content_digest(graph)
        graph_closure = self._receipt(
            receipt_kind="graph_closure",
            graph_digest=graph["graph_digest"],
            disposition="passed",
        )
        graph_commit = self._receipt(
            receipt_kind="graph_commit",
            graph_digest=graph["graph_digest"],
            disposition="committed",
        )
        graph["closure_receipt_digest"] = graph_closure["receipt_digest"]
        graph["commit_receipt_digest"] = graph_commit["receipt_digest"]
        graph_receipt_store = dict(self.evidence_receipts)
        graph_receipt_store[graph_closure["receipt_digest"]] = graph_closure
        graph_receipt_store[graph_commit["receipt_digest"]] = graph_commit
        graph_validation_context = {
            "schema": self.evidence_schema,
            "plan_definition": definition,
            "investigation_snapshot": snapshot,
            "trusted_registry_store": self.evidence_registry,
            "result_set_records": [],
            "result_set_member_records": [],
            "receipt_store": graph_receipt_store,
            "operator_contract_schema": self._operator_contract_schema(),
            "previous_graph": previous_graph,
        }
        graph_validation = self._receipt(
            receipt_kind="validated_committed_evidence_graph",
            validator_id=HOOK.COMMITTED_GRAPH_VALIDATOR_ID,
            validator_version=HOOK.COMMITTED_GRAPH_VALIDATOR_VERSION,
            validator_contract_digest=HOOK.COMMITTED_GRAPH_VALIDATOR_CONTRACT_DIGEST,
            validator_implementation_digest=(
                HOOK.COMMITTED_GRAPH_VALIDATOR_IMPLEMENTATION_DIGEST
            ),
            graph_id=graph["graph_id"],
            graph_revision=graph["graph_revision"],
            graph_digest=graph["graph_digest"],
            graph_state=graph["graph_state"],
            plan_revision=graph["plan_revision"],
            plan_digest=graph["plan_digest"],
            identity_digest=graph["identity_digest"],
            registry_snapshot_id=graph["registry_snapshot_id"],
            registry_snapshot_digest=graph["registry_snapshot_digest"],
            validation_context_digest=self._digest(
                {"validation_context": graph_validation_context}
            ),
            disposition="passed",
        )
        self.dual_evidence_graph = graph
        self.dual_graph_store = {
            "store_contract_id": "country_outage_p2_trusted_committed_graph_store_v1",
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "attestation_provider_id": "country_outage_p2_committed_graph_store_host",
            "attestation_contract_digest": (
                HOOK.COMMITTED_GRAPH_STORE_ATTESTATION_CONTRACT_DIGEST
            ),
            "graphs": {
                graph["graph_digest"]: {
                    "graph": graph,
                    "validation_receipt": graph_validation,
                    "validation_context": graph_validation_context,
                }
            },
        }

        binding = payload["shared_answer_binding"]
        binding["plan_revision"] = 2
        binding["investigation_plan_digest"] = plan_digest
        binding["evidence_graph_revision"] = 2
        binding["evidence_graph_digest"] = graph["graph_digest"]
        root_digest = self._digest(binding)
        payload["shared_answer_binding_digest"] = root_digest
        payload["flow_state"] = "degraded_published"
        payload["effective_teacher_required"] = False
        payload["final_disposition"] = "ds_unaligned_degraded"
        payload["teacher_run_receipt"] = None
        payload["teacher_reference"] = None
        payload["teacher_validation_receipt"] = None
        payload["alignment_run_receipt"] = None
        payload["structured_feedback"] = None
        payload["degraded_authorization"] = authorization

        student = payload["student_runs"][0]
        student["teacher_reference_digest"] = None
        student["teacher_validation_receipt_digest"] = None
        student_run = student["run_receipt"]
        student_run["shared_answer_binding_digest"] = root_digest
        student_run["role_specific_input_digest"] = self._digest(
            {
                "role": "student",
                "revision_ordinal": 0,
                "shared_answer_binding_digest": root_digest,
                "teacher_reference_digest": None,
                "teacher_validation_receipt_digest": None,
                "structured_feedback_digest": None,
            }
        )
        validation = student["validation_receipt"]
        validation["shared_answer_binding_digest"] = root_digest
        validation["receipt_digest"] = self._digest(validation, "receipt_digest")
        student_run["validation_receipt_digest"] = validation["receipt_digest"]
        payload["student_validation_receipt"] = validation
        published = payload["published_answer"]
        published["shared_answer_binding_digest"] = root_digest
        published["aligned_claim"] = False
        published["answer_digest"] = self._digest(published, "answer_digest")
        self.dual_publish_receipt = self._receipt(
            receipt_kind="answer_publish",
            answer_digest=published["answer_digest"],
            student_answer_digest=student["student_answer_digest"],
            shared_answer_binding_digest=root_digest,
            disposition="committed",
        )
        payload["publish_receipt_digest"] = self.dual_publish_receipt[
            "receipt_digest"
        ]
        return payload

    def _valid_transaction_instance(
        self, consistency_kind: str = "node_result_commit_consistency"
    ) -> dict:
        self.transaction_contract = json.loads(
            (
                REPO_ROOT
                / HOOK.CONTRACT_ROOT
                / "runtime-commit-consistency-contract.json"
            ).read_text(encoding="utf-8")
        )
        self.transaction_schema = self.transaction_contract["transaction_record_schema"]
        boundary = next(
            item
            for item in self.transaction_contract["boundaries"]
            if item["id"] == consistency_kind
        )
        self.transaction_current_pointer = {
            "revision": 1,
            "digest": "sha256:" + self._hex("b"),
        }
        self.transaction_idempotency_components = {}
        for index, field in enumerate(boundary["idempotency_key_recipe"]):
            if field.endswith("_revision") or field == "binding_generation":
                value = index + 1
            elif field.endswith("_digest"):
                value = "sha256:" + self._hex(str(index % 10))
            elif field == "format":
                value = "json"
            else:
                value = f"VALUE-{index}"
            self.transaction_idempotency_components[field] = value
        self.transaction_request = {
            "request_schema_version": "country_outage_p2_transaction_request_v1",
            "consistency_kind": boundary["id"],
            "binding_digest": "sha256:" + self._hex("c"),
            "components": self.transaction_idempotency_components,
            "payload": dict(self.transaction_idempotency_components),
        }
        request_digest = "sha256:" + self._digest(
            {"request": self.transaction_request}
        )
        scope_digest = "sha256:" + self._digest(
            {
                "consistency_kind": boundary["id"],
                "binding_digest": self.transaction_request["binding_digest"],
                "components": self.transaction_idempotency_components,
            }
        )
        idempotency_key = boundary["id"] + ":" + self._digest(
            {
                "recipe": boundary["idempotency_key_recipe"],
                "components": self.transaction_idempotency_components,
            }
        )
        self.transaction_prepared_artifacts = {}
        for index, ref in enumerate(boundary["atomic_write_set"]):
            role_contract = self.transaction_contract[
                "prepared_artifact_role_contracts"
            ]["by_consistency_kind"][consistency_kind][ref]
            artifact_payload = self._minimal_schema_instance(
                role_contract["payload_schema"]
            )
            payload_digest = "sha256:" + self._digest(
                {"payload": artifact_payload}
            )
            prepare_receipt = {
                "receipt_kind": "artifact_prepare",
                "artifact_ref": ref,
                "payload_digest": payload_digest,
                "request_digest": request_digest,
                "scope_digest": scope_digest,
                "disposition": "staged",
            }
            prepare_receipt["receipt_digest"] = "sha256:" + self._digest(
                prepare_receipt
            )
            self.transaction_prepared_artifacts[ref] = {
                "artifact_contract_id": "country_outage_p2_prepared_artifact_v1",
                "artifact_role": ref,
                "artifact_ref": ref,
                "artifact_schema_ref": (
                    role_contract["artifact_schema_ref"]
                ),
                "artifact_revision": 1,
                "binding_digest": self.transaction_request["binding_digest"],
                "request_digest": request_digest,
                "scope_digest": scope_digest,
                "payload": artifact_payload,
                "payload_digest": payload_digest,
                "prepare_receipt": prepare_receipt,
                "visibility_state": "staged",
            }
        refs = list(boundary["atomic_write_set"])
        digests = [
            "sha256:"
            + self._digest({"artifact": self.transaction_prepared_artifacts[ref]})
            for ref in refs
        ]
        self.transaction_validation_receipts = {}
        validation_refs = []
        subject_bindings = [
            {
                "artifact_role": ref,
                "artifact_ref": ref,
                "artifact_digest": digest,
            }
            for ref, digest in zip(refs, digests)
        ]
        for gate_id in boundary["validation_gates"]:
            gate_entry = next(
                item
                for item in self.transaction_contract[
                    "trusted_gate_validator_registry"
                ]["entries"]
                if item["consistency_kind"] == boundary["id"]
                and item["gate_id"] == gate_id
            )
            gate_output = {
                "gate_id": gate_id,
                "subject_set_digest": "sha256:"
                + self._digest({"subject_bindings": subject_bindings}),
                "passed": True,
                "failure_code": None,
            }
            receipt = {
                "gate_id": gate_id,
                "gate_version": "1.0.0",
                "gate_contract_digest": gate_entry["gate_contract_digest"],
                "validator_id": gate_entry["validator_id"],
                "implementation_digest": gate_entry["implementation_digest"],
                "output_schema_ref": gate_entry["output_schema_ref"],
                "transaction_id": "TX-1",
                "request_digest": request_digest,
                "binding_digest": self.transaction_request["binding_digest"],
                "subject_bindings": subject_bindings,
                "gate_output": gate_output,
                "output_digest": "sha256:"
                + self._digest({"gate_output": gate_output}),
                "passed": True,
                "failure_code": None,
            }
            receipt["receipt_digest"] = "sha256:" + self._digest(receipt)
            self.transaction_validation_receipts[gate_id] = receipt
            validation_refs.append(receipt)
        self.transaction_request_store = {
            "store_contract_id": (
                "country_outage_p2_trusted_transaction_request_store_v1"
            ),
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "requests": {request_digest: self.transaction_request},
        }
        self.transaction_prepared_artifact_store = {
            "store_contract_id": (
                "country_outage_p2_trusted_prepared_artifact_store_v1"
            ),
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "artifacts": self.transaction_prepared_artifacts,
        }
        self.transaction_gate_receipt_store = {
            "store_contract_id": "country_outage_p2_trusted_gate_receipt_store_v1",
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "receipts": self.transaction_validation_receipts,
        }
        committed_digest = "sha256:" + self._digest(
            {
                "prepared_artifact_refs": refs,
                "prepared_artifact_digests": digests,
                "validation_receipts": validation_refs,
            }
        )
        commit_receipt = {
            "receipt_kind": "transaction_commit",
            "transaction_id": "TX-1",
            "consistency_kind": boundary["id"],
            "request_digest": request_digest,
            "binding_digest": self.transaction_request["binding_digest"],
            "scope_digest": scope_digest,
            "parent_revision": 1,
            "expected_current_digest": self.transaction_current_pointer["digest"],
            "committed_revision": 2,
            "artifact_set_digest": "sha256:" + self._digest(
                {
                    "prepared_artifact_refs": refs,
                    "prepared_artifact_digests": digests,
                }
            ),
            "gate_set_digest": "sha256:" + self._digest(
                {"validation_receipts": validation_refs}
            ),
            "commit_point": boundary["commit_point"],
            "committed_digest": committed_digest,
            "disposition": "committed",
        }
        commit_receipt["receipt_digest"] = "sha256:" + self._digest(commit_receipt)
        self.transaction_commit_receipt = commit_receipt
        self.transaction_recovery_receipt = None
        transaction = {
            "transaction_id": "TX-1",
            "consistency_kind": boundary["id"],
            "idempotency_key": idempotency_key,
            "request_digest": request_digest,
            "transaction_state": "committed",
            "parent_revision": 1,
            "parent_digest": self.transaction_current_pointer["digest"],
            "expected_current_digest": self.transaction_current_pointer["digest"],
            "prepared_artifact_refs": refs,
            "prepared_artifact_digests": digests,
            "validation_receipts": validation_refs,
            "commit_point": boundary["commit_point"],
            "commit_marker": boundary["commit_point"] + ":TX-1",
            "commit_receipt_digest": commit_receipt["receipt_digest"],
            "committed_revision": 2,
            "committed_digest": committed_digest,
            "recovery_action": "none",
            "recovery_receipt_digest": None,
            "conflict_kind": None,
            "disposition": "committed",
        }
        transaction["outcome_digest"] = "sha256:" + self._digest(
            {
                "transaction_id": transaction["transaction_id"],
                "transaction_state": transaction["transaction_state"],
                "disposition": transaction["disposition"],
                "committed_revision": transaction["committed_revision"],
                "committed_digest": transaction["committed_digest"],
                "recovery_action": transaction["recovery_action"],
                "conflict_kind": transaction["conflict_kind"],
            }
        )
        return transaction

    def test_s1d0_passes_with_exact_population_and_boundaries(self) -> None:
        receipt = HOOK.run_alignment(self.root, "S1D-0")
        self.assertEqual("alignment_passed", receipt["status"])
        self.assertEqual("S1D-0", receipt["stage"])
        self.assertTrue(receipt["design_only"])
        self.assertFalse(receipt["runtime_implemented"])
        self.assertFalse(receipt["production_deployed"])
        self.assertIn("question_population_28_of_28", receipt["checks"])
        self.assertIn("tool_operator_stage_separation", receipt["checks"])

    def test_missing_question_is_rejected(self) -> None:
        path = self.root / HOOK.TASK_SPEC
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        path.write_text(
            "".join(line for line in lines if not line.startswith("| Q33 |")),
            encoding="utf-8",
        )
        self._assert_error("question_coverage_mismatch")

    def test_duplicate_question_is_rejected(self) -> None:
        path = self.root / HOOK.TASK_SPEC
        text = path.read_text(encoding="utf-8")
        q01 = next(line for line in text.splitlines() if line.startswith("| Q01 |"))
        marker = "<!-- QUESTION_MAP_END -->"
        path.write_text(text.replace(marker, f"{q01}\n{marker}", 1), encoding="utf-8")
        self._assert_error("question_duplicate")

    def test_missing_function_atomicity_definition_is_rejected(self) -> None:
        self._replace(
            HOOK.TASK_SPEC,
            "execution_unit_function_atomicity",
            "execution-unit-function-atomicity",
        )
        self._assert_error("function_atomicity_marker_missing")

    def test_composite_tool_record_is_rejected(self) -> None:
        record = {
            "unit_id": "TOOL-07",
            "atomic_capability_id": "read.as_prefix_members",
            "single_responsibility": "查询成员并计算严重性排名",
            "output_population": "as_prefix_members",
            "composition_location": "investigation_plan",
            "embedded_capabilities": ["rank_as_severity"],
            "split_test": {"disposition": "atomic_as_designed"},
        }
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK._validate_atomic_unit_records(
                [record],
                expected_ids=("TOOL-07",),
                population_key="output_population",
                kind="tool",
            )
        self.assertEqual("composite_execution_unit_forbidden", captured.exception.code)

    def test_missing_evidence_before_teacher_rule_is_rejected(self) -> None:
        self._replace(
            HOOK.TASK_SPEC,
            "evidence_truth_precedes_teacher",
            "teacher_truth_precedes_evidence",
        )
        self._assert_error("dual_model_marker_missing")

    def test_ds_before_sol_is_rejected(self) -> None:
        self._replace(
            HOOK.TASK_SPEC,
            "→ gpt-5.6-sol 生成 TeacherSemanticPlan",
            "→ DS 生成 StudentSemanticPlan",
        )
        self._assert_error("model_execution_order_drift")

    def test_missing_plan_stage_is_rejected(self) -> None:
        self._replace(
            HOOK.PHASE_PLAN,
            "## 六、S1D-2：Tool 设计",
            "## 六、Tool 设计",
        )
        self._assert_error("plan_stage_missing")

    def test_tool_and_operator_combined_stage_is_rejected(self) -> None:
        self._replace(
            HOOK.PHASE_PLAN,
            "## 六、S1D-2：Tool 设计",
            "## 六、S1D-2：Tool 与 Operator 设计",
        )
        self._assert_error("tool_operator_stage_separation_missing")

    def test_design_only_boundary_drift_is_rejected(self) -> None:
        self._replace(
            HOOK.TASK_SPEC,
            "生产部署：禁止。远程写入：禁止。运行时实现：本任务不执行。",
            "生产部署：允许。远程写入：允许。运行时实现：本任务执行。",
        )
        self._assert_error("task_spec_marker_missing")

    def test_future_stage_without_artifacts_is_rejected(self) -> None:
        self._assert_error("artifact_missing", stage="S1D-1")

    def test_s1d1_passes_with_closed_question_capability_and_atomic_unit_maps(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        receipt = HOOK.run_alignment(
            self.root,
            "S1D-1",
            require_prior_receipts=False,
        )
        self.assertEqual("alignment_passed", receipt["status"])
        self.assertIn("execution_unit_atomic_decomposition", receipt["checks"])
        self.assertIn("shared_answer_binding_contract", receipt["checks"])

    def test_s1d1_rejects_q24_runtime_boundary_drift(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-1"][0]

        def mutate(payload) -> None:
            q24 = next(item for item in payload["questions"] if item["question_id"] == "Q24")
            q24["answerability"] = "new_tool_operator_required"

        self._mutate_json(relative, mutate)
        self._assert_error("deferred_boundary_missing", stage="S1D-1")

    def test_s1d1_rejects_atomic_decomposition_population_drift(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-1"][2]

        def mutate(payload) -> None:
            decision = next(
                item for item in payload["decisions"] if item["candidate_id"] == "set_relation"
            )
            decision["replacement_unit_ids"].remove("OP-28")

        self._mutate_json(relative, mutate)
        self._assert_error("atomic_decomposition_mismatch", stage="S1D-1")

    def test_s1d1_rejects_oracle_seed_scenario_gap(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-1"][1]

        def mutate(payload) -> None:
            del payload["questions"][0]["scenario_expectations"]["large_result"]

        self._mutate_json(relative, mutate)
        self._assert_error("oracle_seed_scenario_coverage_mismatch", stage="S1D-1")

    def test_s1d1_rejects_model_execution_order_drift(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-1"][3]

        def mutate(payload) -> None:
            payload["execution_order"] = ["ds_student", "gpt-5.6-sol"]

        self._mutate_json(relative, mutate)
        self._assert_error("model_execution_order_drift", stage="S1D-1")

    def test_s1d1_rejects_prior_receipt_bound_to_stale_documents(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        s1d0 = HOOK.run_alignment(self.root, "S1D-0")
        HOOK.write_receipt(
            self.root,
            HOOK.RECEIPT_ROOT / "S1D-0.json",
            s1d0,
        )
        task_spec = self.root / HOOK.TASK_SPEC
        task_spec.write_text(
            task_spec.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.run_alignment(self.root, "S1D-1")
        self.assertEqual("prior_receipt_stale", captured.exception.code)

    def test_prior_receipt_binds_stage_artifact_digests(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        s1d0 = HOOK.run_alignment(self.root, "S1D-0")
        HOOK.write_receipt(self.root, HOOK.RECEIPT_ROOT / "S1D-0.json", s1d0)
        s1d1 = HOOK.run_alignment(self.root, "S1D-1")
        HOOK.write_receipt(self.root, HOOK.RECEIPT_ROOT / "S1D-1.json", s1d1)
        capability_map = self.root / HOOK.ARTIFACTS_BY_STAGE["S1D-1"][0]
        capability_map.write_text(
            capability_map.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.run_alignment(self.root, "S1D-2")
        self.assertEqual("prior_receipt_stale", captured.exception.code)

    def test_prior_receipt_binds_alignment_hook_digest(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        s1d0 = HOOK.run_alignment(self.root, "S1D-0")
        HOOK.write_receipt(self.root, HOOK.RECEIPT_ROOT / "S1D-0.json", s1d0)
        hook_path = self.root / HOOK.ALIGNMENT_HOOK
        hook_path.write_text(
            hook_path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.run_alignment(self.root, "S1D-1")
        self.assertEqual("prior_receipt_stale", captured.exception.code)

    def test_prior_receipt_binds_alignment_hook_tests_digest(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        s1d0 = HOOK.run_alignment(self.root, "S1D-0")
        HOOK.write_receipt(self.root, HOOK.RECEIPT_ROOT / "S1D-0.json", s1d0)
        tests_path = self.root / HOOK.ALIGNMENT_HOOK_TESTS
        tests_path.write_text(
            tests_path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.run_alignment(self.root, "S1D-1")
        self.assertEqual("prior_receipt_stale", captured.exception.code)

    def test_s1d2_passes_with_typed_atomic_tool_contracts(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        receipt = HOOK.run_alignment(
            self.root,
            "S1D-2",
            require_prior_receipts=False,
        )
        self.assertEqual("alignment_passed", receipt["status"])
        self.assertIn("route_state_materialized_view_boundary", receipt["checks"])
        self.assertIn("window_path_association_semantics", receipt["checks"])

    def test_s1d2_rejects_runtime_ready_claim(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            payload["tools"][0]["runtime_ready_claim"] = True

        self._mutate_json(relative, mutate)
        self._assert_error("runtime_claim_forbidden", stage="S1D-2")

    def test_s1d2_rejects_duplicate_json_key(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]
        path = self.root / relative
        text = path.read_text(encoding="utf-8")
        needle = '"optional": [\n          "page_token"\n        ],'
        replacement = (
            '"optional": [\n          "page_token"\n        ],\n'
            '        "optional": [\n          "page_token"\n        ],'
        )
        self.assertIn(needle, text)
        path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
        self._assert_error("artifact_json_duplicate_key", stage="S1D-2")

    def test_s1d2_rejects_empty_ordered_path_contract(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            tool12 = next(item for item in payload["tools"] if item["unit_id"] == "TOOL-12")
            del tool12["output_field_schemas"]["path_segments"]["minItems"]

        self._mutate_json(relative, mutate)
        self._assert_error("empty_ordered_path_allowed", stage="S1D-2")

    def test_s1d2_rejects_missing_common_path_status(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            tool11 = next(item for item in payload["tools"] if item["unit_id"] == "TOOL-11")
            tool11["output_member_fields"].remove("common_path_status")

        self._mutate_json(relative, mutate)
        self._assert_error("common_path_status_missing", stage="S1D-2")

    def test_s1d2_rejects_empty_path_direction_population(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            tool12 = next(item for item in payload["tools"] if item["unit_id"] == "TOOL-12")
            del tool12["output_field_schemas"]["peer_asn_direction_ids"]["minItems"]

        self._mutate_json(relative, mutate)
        self._assert_error("path_direction_population_empty", stage="S1D-2")

    def test_s1d2_rejects_missing_native_path_digest(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            tool12 = next(item for item in payload["tools"] if item["unit_id"] == "TOOL-12")
            tool12["output_member_fields"].remove("path_digest")

        self._mutate_json(relative, mutate)
        self._assert_error("path_digest_source_missing", stage="S1D-2")

    def test_s1d2_rejects_unfrozen_path_profile_digest(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            payload["path_canonicalization_profile"]["profile_digest"] = "0" * 64

        self._mutate_json(relative, mutate)
        self._assert_error("path_digest_profile_unfrozen", stage="S1D-2")

    def test_s1d2_rejects_silent_path_profile_rewrite_with_matching_digest(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            profile = payload["path_canonicalization_profile"]
            profile["set_rule"] = "静默改写同一版本规则"
            body = dict(profile)
            body.pop("profile_digest")
            digest = hashlib.sha256(
                json.dumps(
                    body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            profile["profile_digest"] = digest
            for tool in payload["tools"]:
                if tool["unit_id"] in {"TOOL-11", "TOOL-12"}:
                    tool["output_field_schemas"][
                        "path_canonicalization_profile_digest"
                    ] = {"const": digest}

        self._mutate_json(relative, mutate)
        self._assert_error("path_digest_profile_unfrozen", stage="S1D-2")

    def test_s1d2_rejects_path_digest_schema_drift(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            tool12 = next(item for item in payload["tools"] if item["unit_id"] == "TOOL-12")
            tool12["output_field_schemas"]["path_digest"]["pattern"] = ".*"

        self._mutate_json(relative, mutate)
        self._assert_error("path_digest_schema_drift", stage="S1D-2")

    def test_s1d2_rejects_missing_known_path_digest_binding(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            tool11 = next(item for item in payload["tools"] if item["unit_id"] == "TOOL-11")
            tool11["output_row_constraints"]["allOf"] = [
                branch
                for branch in tool11["output_row_constraints"]["allOf"]
                if set(
                    branch.get("if", {})
                    .get("properties", {})
                    .get("path_status", {})
                    .get("enum", [])
                )
                != {"known_ordered", "known_unordered"}
            ]

        self._mutate_json(relative, mutate)
        self._assert_error("path_digest_known_binding_missing", stage="S1D-2")

    def test_s1d2_rejects_missing_null_path_digest_binding(self) -> None:
        self._copy_stage_artifacts("S1D-1")
        self._copy_stage_artifacts("S1D-2")
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-2"][0]

        def mutate(payload) -> None:
            tool11 = next(item for item in payload["tools"] if item["unit_id"] == "TOOL-11")
            tool11["output_row_constraints"]["allOf"] = [
                branch
                for branch in tool11["output_row_constraints"]["allOf"]
                if set(
                    branch.get("if", {})
                    .get("properties", {})
                    .get("path_status", {})
                    .get("enum", [])
                )
                != {"unknown", "not_applicable"}
            ]

        self._mutate_json(relative, mutate)
        self._assert_error("path_digest_null_binding_missing", stage="S1D-2")
    def test_s1d3_passes_with_closed_atomic_operator_contracts(self) -> None:
        for stage in ("S1D-1", "S1D-2", "S1D-3"):
            self._copy_stage_artifacts(stage)
        receipt = HOOK.run_alignment(
            self.root,
            "S1D-3",
            require_prior_receipts=False,
        )
        self.assertEqual("alignment_passed", receipt["status"])
        self.assertIn("operator_function_atomicity", receipt["checks"])
        self.assertIn("operator_typed_contract_closure", receipt["checks"])

    def test_s1d3_rejects_question_ref_drift(self) -> None:
        for stage in ("S1D-1", "S1D-2", "S1D-3"):
            self._copy_stage_artifacts(stage)
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-3"][0]

        def mutate(payload) -> None:
            op05 = next(item for item in payload["operators"] if item["unit_id"] == "OP-05")
            op05["question_refs"].append("Q33")

        self._mutate_json(relative, mutate)
        self._assert_error("operator_question_ref_drift", stage="S1D-3")

    def test_s1d3_rejects_open_payload_schema(self) -> None:
        for stage in ("S1D-1", "S1D-2", "S1D-3"):
            self._copy_stage_artifacts(stage)
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-3"][1]

        def mutate(payload) -> None:
            payload["$defs"]["op09InputPayload"]["additionalProperties"] = True

        self._mutate_json(relative, mutate)
        self._assert_error("operator_payload_schema_open", stage="S1D-3")

    def test_s1d3_rejects_profile_binding_drift(self) -> None:
        for stage in ("S1D-1", "S1D-2", "S1D-3"):
            self._copy_stage_artifacts(stage)
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-3"][0]

        def mutate(payload) -> None:
            payload["profile_binding_contract"]["profile_instance_input_paths"].pop("OP-36")

        self._mutate_json(relative, mutate)
        self._assert_error("operator_profile_binding_open", stage="S1D-3")

    def test_s1d3_rejects_output_profile_binding_drift(self) -> None:
        for stage in ("S1D-1", "S1D-2", "S1D-3"):
            self._copy_stage_artifacts(stage)
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-3"][0]

        def mutate(payload) -> None:
            payload["profile_binding_contract"]["profile_result_output_paths"].pop("OP-37")

        self._mutate_json(relative, mutate)
        self._assert_error("operator_profile_binding_open", stage="S1D-3")

    def test_s1d3_rejects_silent_profile_contract_rewrite(self) -> None:
        for stage in ("S1D-1", "S1D-2", "S1D-3"):
            self._copy_stage_artifacts(stage)
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-3"][0]

        def mutate(payload) -> None:
            profile = next(
                item
                for item in payload["parameter_profiles"]
                if item["profile_id"] == "PROFILE-FIRST-CROSSING-1.0.0"
            )
            profile["parameters"]["grid_step_seconds"] = 600

        self._mutate_json(relative, mutate)
        self._assert_error("operator_profile_unfrozen", stage="S1D-3")

    def test_s1d3_rejects_op15_pathless_schema_open(self) -> None:
        for stage in ("S1D-1", "S1D-2", "S1D-3"):
            self._copy_stage_artifacts(stage)
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-3"][1]

        def mutate(payload) -> None:
            payload["$defs"]["op15InputPayload"]["allOf"] = []

        self._mutate_json(relative, mutate)
        self._assert_error("draft202012_schema_invalid", stage="S1D-3")

    def test_s1d3_rejects_empty_output_evidence(self) -> None:
        for stage in ("S1D-1", "S1D-2", "S1D-3"):
            self._copy_stage_artifacts(stage)
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-3"][1]

        def mutate(payload) -> None:
            del payload["$defs"]["operatorOutputEnvelope"]["properties"]["evidence_refs"]["minItems"]

        self._mutate_json(relative, mutate)
        self._assert_error("operator_output_evidence_empty", stage="S1D-3")

    def test_s1d3_rejects_edge_views_that_contradict_core_results(self) -> None:
        schema = self._operator_contract_schema()

        def assert_invalid(definition_name: str, result: dict) -> None:
            with self.assertRaises(HOOK.AlignmentError) as captured:
                HOOK._validate_draft202012_subschema_instance(
                    result,
                    schema,
                    definition_name=definition_name,
                    subject=definition_name,
                )
            self.assertEqual(
                "instance_subschema_validation_failed", captured.exception.code
            )

        _, _, receipts, _, adjacency_output = self._valid_direct_adjacency_graph()
        position_output = next(
            item["operator_output"]
            for item in receipts.values()
            if isinstance(item, dict) and item.get("operator_id") == "OP-15"
        )
        bad_op15 = json.loads(json.dumps(position_output["result"]))
        bad_op15["outcome"] = "not_found"
        bad_op15["ordered_positions"] = []
        assert_invalid("op15ResultPayload", bad_op15)
        bad_op16 = json.loads(json.dumps(adjacency_output["result"]))
        bad_op16["outcome"] = "not_comparable"
        bad_op16["left_neighbors"] = []
        bad_op16["right_neighbors"] = []
        assert_invalid("op16ResultPayload", bad_op16)
        null_op16_endpoints = json.loads(json.dumps(adjacency_output["result"]))
        null_op16_endpoints["edge_projections"][0]["from_endpoint"][
            "typed_value"
        ] = None
        null_op16_endpoints["edge_projections"][0]["to_endpoint"][
            "typed_value"
        ] = None
        assert_invalid("op16ResultPayload", null_op16_endpoints)
        copied_field_drift = json.loads(json.dumps(adjacency_output["result"]))
        copied_field_drift["edge_projections"][0]["relation_projection"][
            "neighbor_position"
        ] = 99
        copied_field_drift["edge_projections"][0][
            "relation_projection_digest"
        ] = self._digest(
            {
                "projection": copied_field_drift["edge_projections"][0][
                    "relation_projection"
                ]
            }
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK._validate_operator_edge_projection_views(
                "OP-16", copied_field_drift
            )
        self.assertEqual(
            "operator_edge_projection_view_invalid", captured.exception.code
        )

        def edge(relation_type: str, body: dict, left="1", right="2") -> dict:
            return {
                "relation_type": relation_type,
                "from_endpoint": {
                    "domain_value_digest": self._hex(left),
                    "typed_value": None,
                },
                "to_endpoint": {
                    "domain_value_digest": self._hex(right),
                    "typed_value": None,
                },
                "relation_projection": body,
                "relation_projection_digest": self._digest(
                    {"projection": body}
                ),
                "publishable": True,
            }

        intersection_body = {
            "intersection_set_digest": self._hex("3"),
            "intersection_count": 1,
            "left_digest": self._hex("1"),
            "right_digest": self._hex("2"),
        }
        assert_invalid(
            "op25ResultPayload",
            {
                "members": [],
                "member_count": 0,
                "left_digest": self._hex("1"),
                "right_digest": self._hex("2"),
                "set_digest": self._hex("3"),
                "evidence_refs": [],
                "edge_projection": edge("set_intersects", intersection_body),
            },
        )
        contains_body = {
            "direction": "intersection_over_left",
            "intersection_count": 1,
            "denominator_count": 1,
            "ratio_exact": "1/1",
            "outcome": "computed",
            "left_digest": self._hex("1"),
            "right_digest": self._hex("2"),
        }
        assert_invalid(
            "op27ResultPayload",
            {
                "direction": "intersection_over_left",
                "intersection_count": 0,
                "denominator_count": 0,
                "ratio_exact": None,
                "outcome": "not_computable_empty_denominator",
                "left_digest": self._hex("1"),
                "right_digest": self._hex("2"),
                "evidence_refs": [],
                "edge_projection": edge(
                    "set_contains", contains_body, left="2", right="1"
                ),
            },
        )
        temporal_body = {
            "relation": "same_slot",
            "delta_seconds": 0,
            "comparable": True,
            "profile_digest": self._hex("4"),
            "left_digest": self._hex("1"),
            "right_digest": self._hex("2"),
        }
        assert_invalid(
            "op29ResultPayload",
            {
                "relation": "not_comparable",
                "delta_seconds": None,
                "comparable": False,
                "profile_digest": self._hex("4"),
                "left_digest": self._hex("1"),
                "right_digest": self._hex("2"),
                "evidence_refs": [],
                "edge_projection": edge("same_window", temporal_body),
            },
        )
        at_time_body = {
            "join_key": "4|192.0.2.0/24|2026-02-27T00:10:00Z",
            "new_prefix_state_digest": self._hex("1"),
            "route_state_digest": self._hex("2"),
            "left_population_digest": self._hex("3"),
            "right_population_digest": self._hex("4"),
        }
        assert_invalid(
            "op33ResultPayload",
            {
                "matched": [],
                "unmatched_left": [],
                "unmatched_right": [],
                "join_cardinality": {},
                "left_digest": self._hex("3"),
                "right_digest": self._hex("4"),
                "evidence_refs": [],
                "edge_projections": [
                    edge("at_time", at_time_body, left="1", right="2")
                ],
            },
        )
        conflict_body = {
            "class": "conflict",
            "basis_codes": ["MUTUALLY_EXCLUSIVE_SAME_SLOT"],
            "temporal_receipt_digest": self._hex("5"),
            "profile_digest": self._hex("6"),
            "left_digest": self._hex("1"),
            "right_digest": self._hex("2"),
        }
        assert_invalid(
            "op37ResultPayload",
            {
                "class": "not_comparable",
                "basis_codes": [],
                "temporal_receipt_digest": self._hex("5"),
                "profile_digest": self._hex("6"),
                "left_digest": self._hex("1"),
                "right_digest": self._hex("2"),
                "evidence_refs": [],
                "edge_projection": edge("conflicts_with", conflict_body),
            },
        )

    def test_s1d4_passes_with_plan_result_graph_model_and_commit_contracts(self) -> None:
        self._copy_through_s1d4()
        receipt = HOOK.run_alignment(
            self.root,
            "S1D-4",
            require_prior_receipts=False,
        )
        self.assertEqual("alignment_passed", receipt["status"])
        self.assertIn("one_plan_node_one_atomic_execution_unit", receipt["checks"])
        self.assertIn("result_set_page_and_preview_closure", receipt["checks"])
        self.assertIn("sol_teacher_validator_ds_student_shared_binding", receipt["checks"])
        self.assertIn("runtime_five_commit_consistency_boundaries", receipt["checks"])

    def test_s1d4_instance_validators_accept_closed_examples(self) -> None:
        plan = self._valid_plan_instance()
        HOOK.validate_investigation_plan_instance(
            plan,
            schema=self.plan_schema,
            trusted_registry_store=self.plan_registry,
            trusted_admission_receipt_store=self.plan_receipts,
            parameter_bindings=self.plan_parameters,
        )
        result_set, members = self._valid_result_set_instance()
        HOOK.validate_result_set_instance(
            result_set,
            schema=self.result_schema,
            resolved_members=members,
            trusted_registry_store=self.result_registry,
            receipt_store=self.result_receipts,
        )
        graph = self._valid_evidence_graph_instance()
        HOOK.validate_evidence_graph_instance(
            graph,
            schema=self.evidence_schema,
            trusted_registry_store=self.evidence_registry,
            result_sets=self.evidence_result_sets,
            plan_definition=self.evidence_plan["plan_definition"],
            investigation_snapshot=self.evidence_plan["investigation_snapshot"],
            receipt_store=self.evidence_receipts,
        )
        dual = self._valid_dual_model_instance()
        HOOK.validate_dual_model_flow_instance(
            dual,
            schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
        )
        transaction = self._valid_transaction_instance()
        HOOK.validate_transaction_record_instance(
            transaction,
            schema=self.transaction_schema,
            consistency_contract=self.transaction_contract,
            current_pointer=self.transaction_current_pointer,
            trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
            commit_receipt=self.transaction_commit_receipt,
            recovery_receipt=self.transaction_recovery_receipt,
        )

    def test_s1d4_plan_instance_rejects_cross_plan_snapshot(self) -> None:
        payload = self._valid_plan_instance()
        payload["investigation_snapshot"]["plan_id"] = "OTHER"
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store=self.plan_receipts,
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual("plan_snapshot_binding_mismatch", captured.exception.code)

    def test_s1d4_plan_instance_rejects_completed_node_without_result(self) -> None:
        payload = self._valid_plan_instance()
        payload["investigation_snapshot"]["node_execution_revisions"][0][
            "result_digest"
        ] = None
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store=self.plan_receipts,
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_plan_rejects_registry_output_schema_rewrite(self) -> None:
        payload = self._valid_plan_instance()
        definition = payload["plan_definition"]
        definition["nodes"][0]["expected_output_schema_ref"] = (
            "https://domeye.example/forged/customer-cone.json"
        )
        definition["dag_digest"] = self._digest({"nodes": definition["nodes"]})
        definition["admission_receipt_digest"] = None
        admission = self._receipt(
            receipt_kind="plan_admission",
            validator_id=HOOK.PLAN_ADMISSION_VALIDATOR_ID,
            validator_version=HOOK.PLAN_ADMISSION_VALIDATOR_VERSION,
            validator_contract_digest=HOOK.PLAN_ADMISSION_VALIDATOR_CONTRACT_DIGEST,
            validator_implementation_digest=(
                HOOK.PLAN_ADMISSION_VALIDATOR_IMPLEMENTATION_DIGEST
            ),
            plan_id=definition["plan_id"],
            plan_revision=definition["plan_revision"],
            plan_subject_digest=self._digest(definition, "admission_receipt_digest"),
            identity_digest=definition["identity"]["binding_digest"],
            dag_digest=definition["dag_digest"],
            registry_snapshot_id=definition["registry_snapshot_id"],
            registry_snapshot_digest=definition["registry_snapshot_digest"],
            parameter_bindings_digest=self._digest(
                {"parameter_bindings": self.plan_parameters}
            ),
            disposition="passed",
        )
        definition["admission_receipt_digest"] = admission["receipt_digest"]
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store={
                    admission["receipt_digest"]: admission
                },
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual(
            "execution_unit_output_schema_binding_mismatch", captured.exception.code
        )

    def test_s1d4_plan_rejects_direct_caller_registry_view(self) -> None:
        payload = self._valid_plan_instance()
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry_view,
                trusted_admission_receipt_store=self.plan_receipts,
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual("registry_store_invalid", captured.exception.code)

    def test_s1d4_plan_rejects_registry_governance_data_digest_drift(self) -> None:
        payload = self._valid_plan_instance()
        view = self.plan_registry_view
        view["registry_snapshot_data_digest"] = self._hex("7")
        resolution = view["resolution_receipt"]
        resolution["registry_snapshot_data_digest"] = self._hex("7")
        resolution["receipt_digest"] = self._digest(resolution, "receipt_digest")
        view["resolution_receipt_digest"] = resolution["receipt_digest"]
        view["view_digest"] = self._digest(view, "view_digest")
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store=self.plan_receipts,
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual(
            "registry_snapshot_digest_projection_mismatch", captured.exception.code
        )

    def test_s1d4_plan_rejects_input_binding_outside_registry_schema(self) -> None:
        payload = self._valid_plan_instance()
        definition = payload["plan_definition"]
        definition["plan_state"] = "draft"
        definition["admission_receipt_digest"] = None
        definition["nodes"][0]["input_bindings"] = [
            {
                "input_name": "ghost",
                "source_kind": "user_parameter",
                "source_ref": "ghost",
                "source_digest": self._hex("f"),
                "source_artifact_digest": None,
            }
        ]
        definition["dag_digest"] = self._digest({"nodes": definition["nodes"]})
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store={},
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual("plan_input_binding_schema_mismatch", captured.exception.code)

    def test_s1d4_plan_rejects_input_binding_digest_not_bound_to_value(self) -> None:
        payload = self._valid_plan_instance()
        definition = payload["plan_definition"]
        definition["plan_state"] = "draft"
        definition["admission_receipt_digest"] = None
        definition["nodes"][0]["input_bindings"] = [
            {
                "input_name": "asn",
                "source_kind": "user_parameter",
                "source_ref": "asn",
                "source_digest": self._hex("f"),
                "source_artifact_digest": None,
            }
        ]
        definition["dag_digest"] = self._digest({"nodes": definition["nodes"]})
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store={},
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual("plan_input_binding_digest_mismatch", captured.exception.code)

    def test_s1d4_result_instance_rejects_complete_count_mismatch(self) -> None:
        payload, members = self._valid_result_set_instance()
        payload["total_count"] = 999
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_result_set_instance(
                payload,
                schema=self.result_schema,
                resolved_members=members,
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
            )
        self.assertEqual("result_set_complete_mismatch", captured.exception.code)

    def test_s1d4_result_rejects_unregistered_customer_cone_population(self) -> None:
        payload, members = self._valid_result_set_instance()
        payload["source_population_id"] = "customer_cone_members"
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_result_set_instance(
                payload,
                schema=self.result_schema,
                resolved_members=members,
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
            )
        self.assertEqual("result_set_population_not_registered", captured.exception.code)

    def test_s1d4_result_rejects_member_outside_registered_schema(self) -> None:
        payload, _members = self._valid_result_set_instance()
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_result_set_instance(
                payload,
                schema=self.result_schema,
                resolved_members=[{"id": "A", "rank": "not-an-integer"}],
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_result_instance_rejects_cross_result_preview(self) -> None:
        payload, members = self._valid_result_set_instance()
        preview = {
            "view_id": "VIEW-1",
            "source_result_set_id": "OTHER",
            "source_result_set_revision": 1,
            "limit": 1,
            "returned_count": 1,
            "stable_sort_digest": payload["stable_sort_digest"],
            "member_refs": ["A"],
            "represents_complete_population": False,
        }
        preview["view_digest"] = self._digest(preview)
        payload["preview_views"] = [preview]
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_result_set_instance(
                payload,
                schema=self.result_schema,
                resolved_members=members,
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
            )
        self.assertEqual("preview_source_binding_mismatch", captured.exception.code)

    def test_s1d4_result_instance_rejects_page_receipt_binding_drift(self) -> None:
        payload, members = self._valid_result_set_instance()
        page_receipt_digest = payload["page_manifest"][0]["page_receipt_digest"]
        page_receipt = dict(self.result_receipts.pop(page_receipt_digest))
        page_receipt["page_content_digest"] = self._hex("f")
        page_receipt["receipt_digest"] = self._digest(page_receipt, "receipt_digest")
        payload["page_manifest"][0]["page_receipt_digest"] = page_receipt[
            "receipt_digest"
        ]
        self.result_receipts[page_receipt["receipt_digest"]] = page_receipt
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_result_set_instance(
                payload,
                schema=self.result_schema,
                resolved_members=members,
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
            )
        self.assertEqual("result_set_receipt_binding_mismatch", captured.exception.code)

    def test_s1d4_graph_instance_rejects_dangling_edge(self) -> None:
        payload = self._valid_evidence_graph_instance()
        producer = {
            "producer_kind": "tool",
            "producer_id": "TOOL-07",
            "producer_version": "1.0.0",
            "contract_digest": self._hex("1"),
            "run_receipt_digest": self._hex("a"),
        }
        edge = {
            "edge_id": "E1",
            "edge_type": "supports",
            "from_node_id": "F1",
            "to_node_id": "MISSING",
            "producer_ref": producer,
            "relation_receipt_ref": None,
        }
        digest_body = dict(edge)
        digest_body["producer_ref"] = dict(producer)
        digest_body["producer_ref"].pop("run_receipt_digest")
        edge["edge_digest"] = self._digest(digest_body)
        edge_receipt = self._receipt(
            receipt_kind="producer",
            producer_id="TOOL-07",
            output_digest=edge["edge_digest"],
            disposition="committed",
        )
        edge["producer_ref"]["run_receipt_digest"] = edge_receipt["receipt_digest"]
        payload["edges"] = [edge]
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_evidence_graph_instance(
                payload,
                schema=self.evidence_schema,
                trusted_registry_store=self.evidence_registry,
                result_sets=self.evidence_result_sets,
                plan_definition=self.evidence_plan["plan_definition"],
                investigation_snapshot=self.evidence_plan["investigation_snapshot"],
                receipt_store={**self.evidence_receipts, edge_receipt["receipt_digest"]: edge_receipt},
            )
        self.assertEqual("evidence_graph_dangling_ref", captured.exception.code)

    def test_s1d4_graph_instance_rejects_uncommitted_node(self) -> None:
        payload = self._valid_evidence_graph_instance()
        payload["nodes"][0]["committed"] = False
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_evidence_graph_instance(
                payload,
                schema=self.evidence_schema,
                trusted_registry_store=self.evidence_registry,
                result_sets=self.evidence_result_sets,
                plan_definition=self.evidence_plan["plan_definition"],
                investigation_snapshot=self.evidence_plan["investigation_snapshot"],
                receipt_store=self.evidence_receipts,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_dual_instance_rejects_teacher_role_swap(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["teacher_run_receipt"]["role"] = "student"
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_dual_instance_rejects_duplicate_gate(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["student_validation_receipt"]["gate_results"][1]["gate_id"] = "GATE-01"
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_dual_instance_rejects_shared_binding_drift(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["student_runs"][0]["run_receipt"]["shared_answer_binding_digest"] = self._hex("f")
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("dual_model_shared_binding_mismatch", captured.exception.code)

    def test_s1d4_dual_instance_rejects_revision_one_without_zero(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["student_runs"][0]["revision_ordinal"] = 1
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_dual_instance_rejects_zero_hard_metrics(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["alignment_run_receipt"]["hard_gate_metrics"]["fact_precision"] = 0
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_dual_instance_rejects_degrade_without_new_plan_revision(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["final_disposition"] = "ds_unaligned_degraded"
        payload["flow_state"] = "degraded_published"
        payload["effective_teacher_required"] = False
        payload["published_answer"]["aligned_claim"] = False
        payload["published_answer"]["answer_digest"] = self._digest(
            payload["published_answer"], "answer_digest"
        )
        payload["student_runs"][0]["teacher_reference_digest"] = None
        payload["student_runs"][0]["teacher_validation_receipt_digest"] = None
        payload["student_runs"][0]["run_receipt"]["role_specific_input_digest"] = self._digest(
            {
                "role": "student",
                "revision_ordinal": 0,
                "shared_answer_binding_digest": payload["shared_answer_binding_digest"],
                "teacher_reference_digest": None,
                "teacher_validation_receipt_digest": None,
                "structured_feedback_digest": None,
            }
        )
        payload["teacher_run_receipt"] = None
        payload["teacher_reference"] = None
        payload["teacher_validation_receipt"] = None
        payload["alignment_run_receipt"] = None
        payload["shared_answer_binding"]["plan_revision"] = 1
        payload["degraded_authorization"] = {
            "authorization_id": "AUTH-1",
            "user_confirmed": True,
            "mode": "ds_unaligned_degraded",
            "parent_plan_revision": 1,
            "new_plan_revision": 2,
            "may_claim_sol_ds_alignment": False,
            "authorization_digest": self._hex("f"),
        }
        payload["degraded_authorization"]["authorization_digest"] = self._digest(
            payload["degraded_authorization"], "authorization_digest"
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("degraded_plan_revision_invalid", captured.exception.code)

    def test_s1d4_dual_accepts_explicit_degrade_with_trusted_plan_and_graph(self) -> None:
        payload = self._valid_degraded_dual_model_instance()
        HOOK.validate_dual_model_flow_instance(
            payload,
            schema=self.dual_schema,
            evidence_graph=self.dual_evidence_graph,
            trusted_committed_graph_store=self.dual_graph_store,
            trusted_validated_plan_store=self.dual_plan_store,
            investigation_plan_schema=self.plan_schema,
            publish_receipt=self.dual_publish_receipt,
        )

    def test_s1d4_dual_rejects_resigned_degraded_authorization_drift(self) -> None:
        payload = self._valid_degraded_dual_model_instance()
        payload["degraded_authorization"]["authorization_id"] = "AUTH-TAMPERED"
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                trusted_validated_plan_store=self.dual_plan_store,
                investigation_plan_schema=self.plan_schema,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("degraded_authorization_digest_mismatch", captured.exception.code)

    def test_s1d4_dual_rejects_ghost_degraded_plan_store_record(self) -> None:
        payload = self._valid_degraded_dual_model_instance()
        record = self.dual_plan_store["plans"]["PLAN-1@2"]
        record["plan"]["plan_definition"]["answer_execution_policy"][
            "teacher_required"
        ] = True
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                trusted_validated_plan_store=self.dual_plan_store,
                investigation_plan_schema=self.plan_schema,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertIn(
            captured.exception.code,
            {
                "instance_schema_validation_failed",
                "validated_plan_receipt_invalid",
                "evidence_graph_plan_digest_mismatch",
            },
        )

    def test_s1d4_dual_rejects_caller_self_attested_committed_graph(self) -> None:
        payload = self._valid_dual_model_instance()
        self.dual_graph_store["trust_origin"] = "caller_supplied"
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("committed_graph_store_untrusted", captured.exception.code)

    def test_s1d4_transaction_instance_rejects_idempotency_conflict(self) -> None:
        payload = self._valid_transaction_instance()
        existing = json.loads(json.dumps(payload))
        existing["request_digest"] = "sha256:" + "f" * 64
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=self.transaction_contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
                commit_receipt=self.transaction_commit_receipt,
                recovery_receipt=self.transaction_recovery_receipt,
                existing_transaction=existing,
            )
        self.assertEqual("transaction_idempotency_conflict", captured.exception.code)

    def _as_compare_and_swap_conflict(self, payload: dict, *, actual_mismatch: bool) -> dict:
        payload["transaction_state"] = "aborted"
        payload["disposition"] = "rejected_conflict"
        payload["conflict_kind"] = "compare_and_swap"
        payload["recovery_action"] = "retry_compare_and_swap"
        payload["commit_marker"] = None
        payload["commit_receipt_digest"] = None
        payload["committed_revision"] = None
        payload["committed_digest"] = None
        if actual_mismatch:
            payload["parent_revision"] = 2
            payload["parent_digest"] = "sha256:" + self._hex("f")
            payload["expected_current_digest"] = "sha256:" + self._hex("f")
        recovery = {
            "receipt_kind": "transaction_recovery",
            "transaction_id": payload["transaction_id"],
            "action": "retry_compare_and_swap",
            "reason_code": "COMPARE_AND_SWAP_CONFLICT",
            "retry_of_transaction_id": payload["transaction_id"],
            "preserved_pointer_revision": self.transaction_current_pointer["revision"],
            "preserved_pointer_digest": self.transaction_current_pointer["digest"],
            "staging_disposition": "preserved_for_retry",
            "final_reference_preserved": True,
        }
        recovery["receipt_digest"] = "sha256:" + self._digest(recovery)
        payload["recovery_receipt_digest"] = recovery["receipt_digest"]
        payload["outcome_digest"] = "sha256:" + self._digest(
            {
                "transaction_id": payload["transaction_id"],
                "transaction_state": payload["transaction_state"],
                "disposition": payload["disposition"],
                "committed_revision": payload["committed_revision"],
                "committed_digest": payload["committed_digest"],
                "recovery_action": payload["recovery_action"],
                "conflict_kind": payload["conflict_kind"],
            }
        )
        return recovery

    def test_s1d4_transaction_accepts_real_compare_and_swap_conflict(self) -> None:
        payload = self._valid_transaction_instance()
        recovery = self._as_compare_and_swap_conflict(payload, actual_mismatch=True)
        HOOK.validate_transaction_record_instance(
            payload,
            schema=self.transaction_schema,
            consistency_contract=self.transaction_contract,
            current_pointer=self.transaction_current_pointer,
            trusted_transaction_request_store=self.transaction_request_store,
            trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
            trusted_gate_receipt_store=self.transaction_gate_receipt_store,
            commit_receipt=None,
            recovery_receipt=recovery,
        )

    def test_s1d4_transaction_rejects_fake_compare_and_swap_conflict_when_equal(self) -> None:
        payload = self._valid_transaction_instance()
        recovery = self._as_compare_and_swap_conflict(payload, actual_mismatch=False)
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=self.transaction_contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
                commit_receipt=None,
                recovery_receipt=recovery,
            )
        self.assertEqual("transaction_cas_conflict_not_observed", captured.exception.code)

    def test_s1d4_transaction_instance_rejects_half_commit(self) -> None:
        payload = self._valid_transaction_instance()
        payload["transaction_state"] = "prepared"
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=self.transaction_contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
                commit_receipt=self.transaction_commit_receipt,
                recovery_receipt=self.transaction_recovery_receipt,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_plan_instance_rejects_execution_revision_gap(self) -> None:
        payload = self._valid_plan_instance()
        revision_three = json.loads(
            json.dumps(payload["investigation_snapshot"]["node_execution_revisions"][0])
        )
        revision_three["execution_revision"] = 3
        revision_three["parent_execution_revision"] = 1
        payload["investigation_snapshot"]["node_execution_revisions"].append(
            revision_three
        )
        payload["investigation_snapshot"]["snapshot_digest"] = self._digest(
            payload["investigation_snapshot"], "snapshot_digest"
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store=self.plan_receipts,
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual("revision_chain_invalid", captured.exception.code)

    def test_s1d4_plan_instance_rejects_invalid_date_time_format(self) -> None:
        payload = self._valid_plan_instance()
        payload["plan_definition"]["identity"]["window_start_utc"] = "not-a-date"
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store=self.plan_receipts,
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_result_instance_rejects_ghost_preview_member(self) -> None:
        payload, members = self._valid_result_set_instance()
        preview = {
            "view_id": "VIEW-1",
            "source_result_set_id": payload["result_set_id"],
            "source_result_set_revision": payload["result_set_revision"],
            "limit": 1,
            "returned_count": 1,
            "stable_sort_digest": payload["stable_sort_digest"],
            "member_refs": ["GHOST"],
            "represents_complete_population": False,
        }
        preview["view_digest"] = self._digest(preview)
        payload["preview_views"] = [preview]
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_result_set_instance(
                payload,
                schema=self.result_schema,
                resolved_members=members,
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
            )
        self.assertEqual("preview_member_subset_invalid", captured.exception.code)

    def test_s1d4_result_schema_rejects_zero_page_partial_with_resume(self) -> None:
        payload, _ = self._valid_result_set_instance()
        payload["set_completeness"] = "partial_page"
        payload["returned_count"] = 0
        payload["total_count"] = None
        payload["resume_page_token"] = "NEXT"
        payload["page_manifest"] = []
        payload["member_segments"] = []
        payload["manifest_digest"] = self._digest(
            {"page_manifest": [], "member_segments": []}
        )
        payload["content_digest"] = self._digest({"members": []})
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_result_set_instance(
                payload,
                schema=self.result_schema,
                resolved_members=[],
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_complete_export_rejects_valid_partial_result_set(self) -> None:
        payload, members = self._valid_result_set_instance()
        payload["set_completeness"] = "partial_page"
        payload["total_count"] = None
        payload["resume_page_token"] = "NEXT"
        payload["page_manifest"][0]["token_out"] = "NEXT"
        payload["manifest_digest"] = self._digest(
            {
                "page_manifest": payload["page_manifest"],
                "member_segments": payload["member_segments"],
            }
        )
        freeze_receipt = self._receipt(
            receipt_kind="freeze",
            result_set_id=payload["result_set_id"],
            manifest_digest=payload["manifest_digest"],
            content_digest=payload["content_digest"],
            returned_count=payload["returned_count"],
            total_count=payload["total_count"],
            set_completeness=payload["set_completeness"],
            source_population_id=payload["source_population_id"],
            source_population_schema_digest=payload[
                "source_population_schema_digest"
            ],
            source_dataset_digest=payload["source_dataset_digest"],
            disposition="passed",
        )
        payload["freeze_receipt_digest"] = freeze_receipt["receipt_digest"]
        self.result_receipts[freeze_receipt["receipt_digest"]] = freeze_receipt
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_complete_export_eligibility(
                payload,
                schema=self.result_schema,
                resolved_members=members,
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
            )
        self.assertEqual("result_set_export_ineligible", captured.exception.code)

    def test_s1d4_graph_instance_rejects_unregistered_tool(self) -> None:
        payload = self._valid_evidence_graph_instance()
        node = payload["nodes"][0]
        receipt = self._receipt(
            receipt_kind="producer",
            producer_id="TOOL-99",
            output_digest=node["payload_digest"],
            disposition="committed",
        )
        node["producer_ref"]["producer_id"] = "TOOL-99"
        node["producer_ref"]["run_receipt_digest"] = receipt["receipt_digest"]
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_evidence_graph_instance(
                payload,
                schema=self.evidence_schema,
                trusted_registry_store=self.evidence_registry,
                result_sets=self.evidence_result_sets,
                plan_definition=self.evidence_plan["plan_definition"],
                investigation_snapshot=self.evidence_plan["investigation_snapshot"],
                receipt_store={**self.evidence_receipts, receipt["receipt_digest"]: receipt},
            )
        self.assertEqual("evidence_graph_producer_not_admitted", captured.exception.code)

    def test_s1d4_dual_instance_rejects_teacher_output_digest_drift(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["teacher_run_receipt"]["output_digest"] = self._hex("f")
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("teacher_output_binding_mismatch", captured.exception.code)

    def test_s1d4_dual_instance_rejects_teacher_rejected_all_gates_true(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["flow_state"] = "teacher_rejected"
        payload["final_disposition"] = "teacher_rejected"
        payload["student_runs"] = []
        payload["structured_feedback"] = None
        payload["student_validation_receipt"] = None
        payload["alignment_run_receipt"] = None
        payload["degraded_authorization"] = None
        payload["published_answer"] = None
        payload["publish_receipt_digest"] = None
        payload["teacher_validation_receipt"]["all_gates_passed"] = False
        payload["teacher_validation_receipt"]["receipt_digest"] = self._digest(
            payload["teacher_validation_receipt"], "receipt_digest"
        )
        payload["teacher_run_receipt"]["validation_receipt_digest"] = payload[
            "teacher_validation_receipt"
        ]["receipt_digest"]
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(payload, schema=self.dual_schema)
        self.assertEqual("validation_gate_summary_mismatch", captured.exception.code)

    def test_s1d4_dual_instance_rejects_nonterminal_publish(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["flow_state"] = "awaiting_teacher"
        payload["final_disposition"] = "none"
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_dual_schema_rejects_disposition_state_drift(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["flow_state"] = "failed"
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_dual_instance_rejects_ghost_feedback_for_one_run(self) -> None:
        payload = self._valid_dual_model_instance()
        feedback = {
            "feedback_round": 1,
            "source_student_answer_digest": payload["student_runs"][0][
                "student_answer_digest"
            ],
            "source_validation_receipt_digest": payload["student_runs"][0][
                "validation_receipt"
            ]["receipt_digest"],
            "difference_items": [
                {"kind": "structure_gap", "reference_id": "SECTION-1"}
            ],
            "may_add_event_facts": False,
            "may_change_evidence": False,
            "may_change_prompt_or_policy": False,
        }
        feedback["feedback_digest"] = self._digest(feedback)
        payload["structured_feedback"] = feedback
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("student_feedback_unexpected", captured.exception.code)

    def test_s1d4_transaction_instance_rejects_revision_rollback(self) -> None:
        payload = self._valid_transaction_instance()
        payload["committed_revision"] = 1
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=self.transaction_contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
                commit_receipt=self.transaction_commit_receipt,
                recovery_receipt=self.transaction_recovery_receipt,
            )
        self.assertEqual("transaction_revision_chain_invalid", captured.exception.code)

    def test_s1d4_transaction_instance_rejects_empty_write_set(self) -> None:
        payload = self._valid_transaction_instance()
        payload["prepared_artifact_refs"] = []
        payload["prepared_artifact_digests"] = []
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=self.transaction_contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store={
                    **self.transaction_prepared_artifact_store,
                    "artifacts": {},
                },
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
                commit_receipt=self.transaction_commit_receipt,
                recovery_receipt=self.transaction_recovery_receipt,
            )
        self.assertEqual(
            "transaction_consistency_kind_contract_invalid", captured.exception.code
        )

    def test_s1d4_plan_instance_rejects_reversed_identity_time_window(self) -> None:
        payload = self._valid_plan_instance()
        definition = payload["plan_definition"]
        definition["identity"]["window_start_utc"] = "2026-03-12T00:00:00Z"
        definition["identity"]["binding_digest"] = self._digest(
            definition["identity"], "binding_digest"
        )
        definition["admission_receipt_digest"] = None
        admission = self._receipt(
            receipt_kind="plan_admission",
            validator_id=HOOK.PLAN_ADMISSION_VALIDATOR_ID,
            validator_version=HOOK.PLAN_ADMISSION_VALIDATOR_VERSION,
            validator_contract_digest=HOOK.PLAN_ADMISSION_VALIDATOR_CONTRACT_DIGEST,
            validator_implementation_digest=(
                HOOK.PLAN_ADMISSION_VALIDATOR_IMPLEMENTATION_DIGEST
            ),
            plan_id=definition["plan_id"],
            plan_revision=definition["plan_revision"],
            plan_subject_digest=self._digest(definition, "admission_receipt_digest"),
            identity_digest=definition["identity"]["binding_digest"],
            dag_digest=definition["dag_digest"],
            registry_snapshot_id=definition["registry_snapshot_id"],
            registry_snapshot_digest=definition["registry_snapshot_digest"],
            parameter_bindings_digest=self._digest(
                {"parameter_bindings": self.plan_parameters}
            ),
            disposition="passed",
        )
        definition["admission_receipt_digest"] = admission["receipt_digest"]
        self.plan_receipts = {admission["receipt_digest"]: admission}
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store=self.plan_receipts,
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual("identity_time_order_invalid", captured.exception.code)

    def test_s1d4_partial_plan_requires_terminal_skip_for_hard_dependent(self) -> None:
        payload = self._valid_plan_instance()
        definition = payload["plan_definition"]
        dependent = json.loads(json.dumps(definition["nodes"][0]))
        dependent["node_id"] = "N2"
        dependent["depends_on"] = ["N1"]
        dependent["wave"] = 1
        definition["nodes"].append(dependent)
        self.plan_parameters["N2"] = dict(self.plan_parameters["N1"])
        definition["dag_digest"] = self._digest({"nodes": definition["nodes"]})
        definition["admission_receipt_digest"] = None
        admission = self._receipt(
            receipt_kind="plan_admission",
            validator_id=HOOK.PLAN_ADMISSION_VALIDATOR_ID,
            validator_version=HOOK.PLAN_ADMISSION_VALIDATOR_VERSION,
            validator_contract_digest=HOOK.PLAN_ADMISSION_VALIDATOR_CONTRACT_DIGEST,
            validator_implementation_digest=(
                HOOK.PLAN_ADMISSION_VALIDATOR_IMPLEMENTATION_DIGEST
            ),
            plan_id=definition["plan_id"],
            plan_revision=definition["plan_revision"],
            plan_subject_digest=self._digest(definition, "admission_receipt_digest"),
            identity_digest=definition["identity"]["binding_digest"],
            dag_digest=definition["dag_digest"],
            registry_snapshot_id=definition["registry_snapshot_id"],
            registry_snapshot_digest=definition["registry_snapshot_digest"],
            parameter_bindings_digest=self._digest(
                {"parameter_bindings": self.plan_parameters}
            ),
            disposition="passed",
        )
        definition["admission_receipt_digest"] = admission["receipt_digest"]
        self.plan_receipts = {admission["receipt_digest"]: admission}
        snapshot = payload["investigation_snapshot"]
        snapshot["status"] = "partially_completed"
        execution = snapshot["node_execution_revisions"][0]
        execution["state"] = "failed"
        execution["result_digest"] = None
        execution["failure_code"] = "TOOL_FAILED"
        snapshot["snapshot_digest"] = self._digest(snapshot, "snapshot_digest")
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store=self.plan_receipts,
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual(
            "investigation_partial_terminal_coverage_open", captured.exception.code
        )

    def test_s1d4_partial_result_rejects_returned_equal_known_total(self) -> None:
        payload, members = self._valid_result_set_instance()
        payload["set_completeness"] = "partial_page"
        payload["total_count"] = payload["returned_count"]
        payload["resume_page_token"] = "NEXT"
        payload["page_manifest"][0]["token_out"] = "NEXT"
        payload["manifest_digest"] = self._digest(
            {
                "page_manifest": payload["page_manifest"],
                "member_segments": payload["member_segments"],
            }
        )
        freeze = self._receipt(
            receipt_kind="freeze",
            result_set_id=payload["result_set_id"],
            manifest_digest=payload["manifest_digest"],
            content_digest=payload["content_digest"],
            returned_count=payload["returned_count"],
            total_count=payload["total_count"],
            set_completeness=payload["set_completeness"],
            disposition="passed",
        )
        payload["freeze_receipt_digest"] = freeze["receipt_digest"]
        self.result_receipts[freeze["receipt_digest"]] = freeze
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_result_set_instance(
                payload,
                schema=self.result_schema,
                resolved_members=members,
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
            )
        self.assertEqual("result_set_partial_page_open", captured.exception.code)

    def test_s1d4_source_incomplete_accepts_closed_known_partial_source(self) -> None:
        payload, members = self._valid_result_set_instance()
        payload["set_completeness"] = "source_incomplete"
        payload["limitations"] = [
            {"code": "SOURCE_INCOMPLETE", "scope": "source", "description": "源制品声明人口不完整"}
        ]
        self._attach_source_incomplete_receipt(payload)
        freeze = self._receipt(
            receipt_kind="freeze",
            result_set_id=payload["result_set_id"],
            manifest_digest=payload["manifest_digest"],
            content_digest=payload["content_digest"],
            returned_count=payload["returned_count"],
            total_count=payload["total_count"],
            set_completeness=payload["set_completeness"],
            source_population_id=payload["source_population_id"],
            source_population_schema_digest=payload[
                "source_population_schema_digest"
            ],
            source_dataset_digest=payload["source_dataset_digest"],
            disposition="passed",
        )
        payload["freeze_receipt_digest"] = freeze["receipt_digest"]
        self.result_receipts[freeze["receipt_digest"]] = freeze
        HOOK.validate_result_set_instance(
            payload,
            schema=self.result_schema,
            resolved_members=members,
            trusted_registry_store=self.result_registry,
            receipt_store=self.result_receipts,
        )

    def test_s1d4_source_incomplete_rejects_returned_over_known_total(self) -> None:
        payload, members = self._valid_result_set_instance()
        payload["set_completeness"] = "source_incomplete"
        payload["limitations"] = [
            {"code": "SOURCE_INCOMPLETE", "scope": "source", "description": "源制品声明人口不完整"}
        ]
        payload["total_count"] = 0
        self._attach_source_incomplete_receipt(payload)
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_result_set_instance(
                payload,
                schema=self.result_schema,
                resolved_members=members,
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
            )
        self.assertEqual("result_set_source_incomplete_open", captured.exception.code)

    def test_s1d4_source_incomplete_rejects_root_page_token_drift(self) -> None:
        payload, members = self._valid_result_set_instance()
        payload["set_completeness"] = "source_incomplete"
        payload["limitations"] = [
            {"code": "SOURCE_INCOMPLETE", "scope": "source", "description": "源制品声明人口不完整"}
        ]
        payload["resume_page_token"] = "GHOST-NEXT"
        self._attach_source_incomplete_receipt(payload)
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_result_set_instance(
                payload,
                schema=self.result_schema,
                resolved_members=members,
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
            )
        self.assertEqual("result_set_source_incomplete_open", captured.exception.code)

    def test_s1d4_tabular_export_nested_values_have_unique_canonical_bytes(self) -> None:
        left = [{"id": "A", "nested": {"b": 2, "a": 1}, "flag": True, "none": None}]
        right = [{"none": None, "flag": True, "nested": {"a": 1, "b": 2}, "id": "A"}]
        for export_format in ("csv", "markdown"):
            encoded = HOOK._canonical_export_bytes(left, export_format)
            self.assertEqual(encoded, HOOK._canonical_export_bytes(right, export_format))
            self.assertNotIn(b"{'a':", encoded)

    def test_s1d4_complete_export_artifact_binds_members_and_actual_bytes(self) -> None:
        payload, members = self._valid_result_set_instance()
        export_bytes = b'[{"id":"A","rank":1}]'
        authorization = self._receipt(
            receipt_kind="export_authorization",
            authorization_id="AUTH-EXPORT-1",
            result_set_id=payload["result_set_id"],
            result_set_revision=payload["result_set_revision"],
            allowed_formats=["json"],
            disposition="authorized",
        )
        manifest = {
            "export_id": "EXPORT-1",
            "authorization_id": authorization["authorization_id"],
            "source_result_set_id": payload["result_set_id"],
            "source_result_set_revision": payload["result_set_revision"],
            "source_manifest_digest": payload["manifest_digest"],
            "source_content_digest": payload["content_digest"],
            "format": "json",
            "member_count": 1,
            "ordered_member_digests": [
                self._digest({"member": members[0]})
            ],
            "temporary_artifact_ref": "staging/EXPORT-1.json",
            "export_bytes_sha256": hashlib.sha256(export_bytes).hexdigest(),
            "generation_origin": (
                "deterministic_serializer_without_llm_member_generation"
            ),
        }
        manifest["manifest_digest"] = self._digest(manifest)
        artifact = {
            "artifact_ref": manifest["temporary_artifact_ref"],
            "format": "json",
            "byte_length": len(export_bytes),
            "sha256": manifest["export_bytes_sha256"],
            "visibility_state": "staged",
            "manifest_digest": manifest["manifest_digest"],
        }
        HOOK.validate_complete_export_artifact(
            payload,
            schema=self.result_schema,
            resolved_members=members,
            trusted_registry_store=self.result_registry,
            receipt_store=self.result_receipts,
            export_artifact=artifact,
            export_manifest=manifest,
            authorization_receipt=authorization,
            export_bytes=export_bytes,
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_complete_export_artifact(
                payload,
                schema=self.result_schema,
                resolved_members=members,
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
                export_artifact=artifact,
                export_manifest=manifest,
                authorization_receipt=authorization,
                export_bytes=b"tampered",
            )
        self.assertEqual("export_bytes_not_canonical", captured.exception.code)

    def test_s1d4_export_rejects_resigned_noncanonical_bytes(self) -> None:
        payload, members = self._valid_result_set_instance()
        bad_bytes = b"THIS IS NOT THE RESULTSET"
        authorization = self._receipt(
            receipt_kind="export_authorization",
            authorization_id="AUTH-EXPORT-2",
            result_set_id=payload["result_set_id"],
            result_set_revision=payload["result_set_revision"],
            allowed_formats=["json"],
            disposition="authorized",
        )
        manifest = {
            "export_id": "EXPORT-2",
            "authorization_id": authorization["authorization_id"],
            "source_result_set_id": payload["result_set_id"],
            "source_result_set_revision": payload["result_set_revision"],
            "source_manifest_digest": payload["manifest_digest"],
            "source_content_digest": payload["content_digest"],
            "format": "json",
            "member_count": 1,
            "ordered_member_digests": [self._digest({"member": members[0]})],
            "temporary_artifact_ref": "staging/EXPORT-2.json",
            "export_bytes_sha256": hashlib.sha256(bad_bytes).hexdigest(),
            "generation_origin": (
                "deterministic_serializer_without_llm_member_generation"
            ),
        }
        manifest["manifest_digest"] = self._digest(manifest)
        artifact = {
            "artifact_ref": manifest["temporary_artifact_ref"],
            "format": "json",
            "byte_length": len(bad_bytes),
            "sha256": manifest["export_bytes_sha256"],
            "visibility_state": "staged",
            "manifest_digest": manifest["manifest_digest"],
        }
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_complete_export_artifact(
                payload,
                schema=self.result_schema,
                resolved_members=members,
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
                export_artifact=artifact,
                export_manifest=manifest,
                authorization_receipt=authorization,
                export_bytes=bad_bytes,
            )
        self.assertEqual("export_bytes_not_canonical", captured.exception.code)

    def test_s1d4_observed_fact_rejects_unresolved_result_set_member(self) -> None:
        payload = self._valid_evidence_graph_instance()
        node = payload["nodes"][0]
        node["payload"]["source_result_set_ref"] = {
            "result_set_id": "result-set-sha256:" + self._hex("f"),
            "result_set_revision": 1,
            "manifest_digest": self._hex("a"),
            "content_digest": self._hex("b"),
            "freeze_receipt_digest": self._hex("c"),
            "source_scope": "member",
            "member_ref": "GHOST",
            "member_digest": self._hex("d"),
            "projection_receipt_digest": None,
            "source_completeness": "complete",
        }
        node["payload_digest"] = self._digest({"payload": node["payload"]})
        producer_receipt = self._receipt(
            receipt_kind="producer",
            producer_id="TOOL-07",
            output_digest=node["payload_digest"],
            disposition="committed",
        )
        node["producer_ref"]["run_receipt_digest"] = producer_receipt[
            "receipt_digest"
        ]
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_evidence_graph_instance(
                payload,
                schema=self.evidence_schema,
                trusted_registry_store=self.evidence_registry,
                result_sets={},
                plan_definition=self.evidence_plan["plan_definition"],
                investigation_snapshot=self.evidence_plan["investigation_snapshot"],
                receipt_store={producer_receipt["receipt_digest"]: producer_receipt},
                result_set_members={},
            )
        self.assertEqual("evidence_graph_result_set_unresolved", captured.exception.code)

    def test_s1d4_trusted_graph_revalidation_accepts_json_safe_result_set_member_context(self) -> None:
        result_set, members = self._valid_result_set_instance()
        HOOK.validate_result_set_instance(
            result_set,
            schema=self.result_schema,
            resolved_members=members,
            trusted_registry_store=self.result_registry,
            receipt_store=self.result_receipts,
        )
        graph = self._valid_evidence_graph_instance()
        key = (result_set["result_set_id"], result_set["result_set_revision"])
        member_ref = "A"
        member = members[0]
        member_digest = self._digest({"member": member})
        node = graph["nodes"][0]
        node["payload"]["fact_value_digest"] = member_digest
        node["payload"]["source_result_set_ref"] = {
            "result_set_id": key[0],
            "result_set_revision": key[1],
            "manifest_digest": result_set["manifest_digest"],
            "content_digest": result_set["content_digest"],
            "freeze_receipt_digest": result_set["freeze_receipt_digest"],
            "source_scope": "member",
            "member_ref": member_ref,
            "member_digest": member_digest,
            "projection_receipt_digest": None,
            "source_completeness": "complete",
        }
        node["payload_digest"] = self._digest({"payload": node["payload"]})
        producer = self._receipt(
            receipt_kind="producer",
            producer_id="TOOL-07",
            output_digest=node["payload_digest"],
            disposition="committed",
        )
        node["producer_ref"]["run_receipt_digest"] = producer["receipt_digest"]
        graph["graph_digest"] = HOOK._evidence_graph_content_digest(graph)
        closure = self._receipt(
            receipt_kind="graph_closure",
            graph_digest=graph["graph_digest"],
            disposition="passed",
        )
        commit = self._receipt(
            receipt_kind="graph_commit",
            graph_digest=graph["graph_digest"],
            disposition="committed",
        )
        graph["closure_receipt_digest"] = closure["receipt_digest"]
        graph["commit_receipt_digest"] = commit["receipt_digest"]
        receipts = {
            **self.result_receipts,
            producer["receipt_digest"]: producer,
            closure["receipt_digest"]: closure,
            commit["receipt_digest"]: commit,
        }
        result_sets = {key: result_set}
        result_set_members = {key: {member_ref: member}}
        HOOK.validate_evidence_graph_instance(
            graph,
            schema=self.evidence_schema,
            trusted_registry_store=self.evidence_registry,
            result_sets=result_sets,
            plan_definition=self.evidence_plan["plan_definition"],
            investigation_snapshot=self.evidence_plan["investigation_snapshot"],
            receipt_store=receipts,
            result_set_members=result_set_members,
            operator_contract_schema=self._operator_contract_schema(),
        )
        context = {
            "schema": self.evidence_schema,
            "plan_definition": self.evidence_plan["plan_definition"],
            "investigation_snapshot": self.evidence_plan[
                "investigation_snapshot"
            ],
            "trusted_registry_store": self.evidence_registry,
            "result_set_records": [
                {
                    "result_set_id": key[0],
                    "result_set_revision": key[1],
                    "result_set": result_set,
                }
            ],
            "result_set_member_records": [
                {
                    "result_set_id": key[0],
                    "result_set_revision": key[1],
                    "members": [{"member_ref": member_ref, "member": member}],
                }
            ],
            "receipt_store": receipts,
            "operator_contract_schema": self._operator_contract_schema(),
            "previous_graph": None,
        }
        validation = self._receipt(
            receipt_kind="validated_committed_evidence_graph",
            validator_id=HOOK.COMMITTED_GRAPH_VALIDATOR_ID,
            validator_version=HOOK.COMMITTED_GRAPH_VALIDATOR_VERSION,
            validator_contract_digest=HOOK.COMMITTED_GRAPH_VALIDATOR_CONTRACT_DIGEST,
            validator_implementation_digest=(
                HOOK.COMMITTED_GRAPH_VALIDATOR_IMPLEMENTATION_DIGEST
            ),
            graph_id=graph["graph_id"],
            graph_revision=graph["graph_revision"],
            graph_digest=graph["graph_digest"],
            graph_state="committed",
            plan_revision=graph["plan_revision"],
            plan_digest=graph["plan_digest"],
            identity_digest=graph["identity_digest"],
            registry_snapshot_id=graph["registry_snapshot_id"],
            registry_snapshot_digest=graph["registry_snapshot_digest"],
            validation_context_digest=self._digest(
                {"validation_context": context}
            ),
            disposition="passed",
        )
        store = {
            "store_contract_id": "country_outage_p2_trusted_committed_graph_store_v1",
            "trust_origin": "host_authenticated_runtime_store",
            "caller_mutable": False,
            "attestation_provider_id": "country_outage_p2_committed_graph_store_host",
            "attestation_contract_digest": (
                HOOK.COMMITTED_GRAPH_STORE_ATTESTATION_CONTRACT_DIGEST
            ),
            "graphs": {
                graph["graph_digest"]: {
                    "graph": graph,
                    "validation_receipt": validation,
                    "validation_context": context,
                }
            },
        }
        self.assertEqual(
            graph,
            HOOK._resolve_trusted_committed_graph(
                store, graph_digest=graph["graph_digest"]
            ),
        )

        context["result_set_member_records"][0]["members"][0]["member"][
            "rank"
        ] = 999
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK._resolve_trusted_committed_graph(
                store, graph_digest=graph["graph_digest"]
            )
        self.assertIn(
            captured.exception.code,
            {
                "evidence_graph_observed_member_digest_mismatch",
                "committed_graph_validation_receipt_invalid",
            },
        )

    def _valid_direct_adjacency_graph(self):
        payload = self._valid_evidence_graph_instance()
        asn_schema_ref = "https://domeye.example/types/asn.json"
        first_value_digest = self._digest(
            {"value_schema_ref": asn_schema_ref, "value": 49666}
        )
        payload["nodes"][0]["payload"]["fact_value_digest"] = first_value_digest
        payload["nodes"][0]["payload"]["fact_value_projection"] = {
            "value_schema_ref": asn_schema_ref,
            "value": 49666,
            "value_digest": first_value_digest,
        }
        payload["nodes"][0]["payload_digest"] = self._digest(
            {"payload": payload["nodes"][0]["payload"]}
        )
        first_producer = self._receipt(
            receipt_kind="producer",
            producer_id="TOOL-07",
            output_digest=payload["nodes"][0]["payload_digest"],
            disposition="committed",
        )
        payload["nodes"][0]["producer_ref"]["run_receipt_digest"] = (
            first_producer["receipt_digest"]
        )
        second_node = json.loads(json.dumps(payload["nodes"][0]))
        second_node["node_id"] = "F2"
        second_node["payload"]["fact_id"] = "FACT-2"
        second_value_digest = self._digest(
            {"value_schema_ref": asn_schema_ref, "value": 48159}
        )
        second_node["payload"]["fact_value_digest"] = second_value_digest
        second_node["payload"]["fact_value_projection"] = {
            "value_schema_ref": asn_schema_ref,
            "value": 48159,
            "value_digest": second_value_digest,
        }
        second_node["payload_digest"] = self._digest(
            {"payload": second_node["payload"]}
        )
        second_producer = self._receipt(
            receipt_kind="producer",
            producer_id="TOOL-07",
            output_digest=second_node["payload_digest"],
            disposition="committed",
        )
        second_node["producer_ref"]["run_receipt_digest"] = second_producer[
            "receipt_digest"
        ]
        payload["nodes"].append(second_node)
        registry = self._registry_view(
            {
                "TOOL-07": self._registry_entry(),
                "OP-15": self._operator_entry("OP-15"),
                "OP-16": self._operator_entry("OP-16"),
            }
        )
        evidence_refs = [
            {"evidence_id": "EV1", "source_digest": self._hex("e"), "member_key": "M1"}
        ]
        position_result = {
            "outcome": "found",
            "target_asn": 49666,
            "ordered_positions": [2],
            "path_digest": self._hex("a"),
            "path_canonicalization_profile_id": "AS-PATH-CANONICALIZATION-1.0.0",
            "path_canonicalization_profile_digest": "eb4d2081ee69ab0254b7af461122cf315b6bcdf24551c22de7e8dccc6d965966",
            "input_digest": self._hex("d"),
            "evidence_refs": evidence_refs,
            "edge_projection": {
                "relation_type": "path_contains",
                "from_endpoint": {
                    "domain_value_digest": self._hex("a"),
                    "typed_value": None,
                },
                "to_endpoint": {
                    "domain_value_digest": first_value_digest,
                    "typed_value": 49666,
                },
                "relation_projection": {
                    "outcome": "found",
                    "target_asn": 49666,
                    "ordered_positions": [2],
                    "path_digest": self._hex("a"),
                    "path_canonicalization_profile_id": "AS-PATH-CANONICALIZATION-1.0.0",
                    "path_canonicalization_profile_digest": "eb4d2081ee69ab0254b7af461122cf315b6bcdf24551c22de7e8dccc6d965966",
                    "operator_input_digest": self._hex("d"),
                },
                "publishable": True,
            },
        }
        position_result["edge_projection"]["relation_projection_digest"] = self._digest(
            {"projection": position_result["edge_projection"]["relation_projection"]}
        )
        position_output = self._operator_output(
            "OP-15",
            plan=self.evidence_plan,
            result=position_result,
            input_digests=[self._hex("d")],
        )
        position = self._operator_artifact("OP-15", position_output)
        projection = {
            "target_asn": 49666,
            "neighbor_side": "right",
            "target_position": 2,
            "neighbor_position": 3,
            "neighbor_asn": 48159,
            "path_digest": self._hex("a"),
            "position_receipt_digest": position_output["output_digest"],
        }
        projection_digest = self._digest({"projection": projection})
        adjacency_result = {
            "outcome": "computed",
            "target_asn": 49666,
            "left_neighbors": [],
            "right_neighbors": [
                {"target_position": 2, "neighbor_position": 3, "neighbor_asn": 48159}
            ],
            "path_digest": self._hex("a"),
            "position_receipt_digest": position_output["output_digest"],
            "evidence_refs": evidence_refs,
            "edge_projections": [
                {
                    "relation_type": "directly_adjacent_in_path",
                    "from_endpoint": {
                        "domain_value_digest": first_value_digest,
                        "typed_value": 49666,
                    },
                    "to_endpoint": {
                        "domain_value_digest": second_value_digest,
                        "typed_value": 48159,
                    },
                    "relation_projection": json.loads(json.dumps(projection)),
                    "relation_projection_digest": projection_digest,
                    "publishable": True,
                }
            ],
        }
        adjacency_output = self._operator_output(
            "OP-16",
            plan=self.evidence_plan,
            result=adjacency_result,
            input_digests=[position_output["output_digest"], self._hex("a")],
        )
        operator_run = self._operator_artifact("OP-16", adjacency_output)
        relation = {
            "receipt_schema_version": "country_outage_p2_relation_receipt_v1",
            "receipt_kind": "evidence_graph_relation",
            "relation_type": "directly_adjacent_in_path",
            "from_node_binding": {
                "node_id": "F1",
                "node_payload_digest": payload["nodes"][0]["payload_digest"],
                "domain_value_digest": payload["nodes"][0]["payload"]["fact_value_digest"],
                "typed_value_schema_ref": asn_schema_ref,
                "typed_value": 49666,
            },
            "to_node_binding": {
                "node_id": "F2",
                "node_payload_digest": second_node["payload_digest"],
                "domain_value_digest": second_node["payload"]["fact_value_digest"],
                "typed_value_schema_ref": asn_schema_ref,
                "typed_value": 48159,
            },
            "identity_digest": payload["identity_digest"],
            "registry_snapshot_id": payload["registry_snapshot_id"],
            "registry_snapshot_digest": payload["registry_snapshot_digest"],
            "operator_binding": {
                "operator_id": "OP-16",
                "operator_version": "1.0.0-design",
                "contract_digest": self._hex("1"),
                "run_receipt_digest": operator_run["receipt_digest"],
                "output_schema_ref": operator_run["output_schema_ref"],
                "output_digest": operator_run["output_digest"],
            },
            "projection": projection,
            "projection_digest": projection_digest,
            "disposition": "passed",
        }
        relation["receipt_digest"] = self._digest(relation)
        producer_ref = {
            "producer_kind": "operator",
            "producer_id": "OP-16",
            "producer_version": "1.0.0-design",
            "contract_digest": self._hex("1"),
            "run_receipt_digest": operator_run["receipt_digest"],
        }
        edge = {
            "edge_id": "E-ADJ",
            "edge_type": "directly_adjacent_in_path",
            "from_node_id": "F1",
            "to_node_id": "F2",
            "producer_ref": producer_ref,
            "relation_receipt_ref": relation["receipt_digest"],
        }
        edge_body = json.loads(json.dumps(edge))
        edge_body["producer_ref"].pop("run_receipt_digest")
        edge["edge_digest"] = self._digest(edge_body)
        payload["edges"] = [edge]
        payload["graph_digest"] = self._digest(
            {
                key: payload[key]
                for key in (
                    "graph_id",
                    "graph_revision",
                    "parent_graph_revision",
                    "investigation_id",
                    "investigation_revision",
                    "plan_id",
                    "plan_revision",
                    "plan_digest",
                    "identity_digest",
                    "registry_snapshot_id",
                    "registry_snapshot_digest",
                    "nodes",
                    "edges",
                    "root_node_ids",
                )
            }
        )
        closure = self._receipt(
            receipt_kind="graph_closure",
            graph_digest=payload["graph_digest"],
            disposition="passed",
        )
        commit = self._receipt(
            receipt_kind="graph_commit",
            graph_digest=payload["graph_digest"],
            disposition="committed",
        )
        payload["closure_receipt_digest"] = closure["receipt_digest"]
        payload["commit_receipt_digest"] = commit["receipt_digest"]
        receipts = {
            **self.evidence_receipts,
            first_producer["receipt_digest"]: first_producer,
            second_producer["receipt_digest"]: second_producer,
            position["receipt_digest"]: position,
            operator_run["receipt_digest"]: operator_run,
            relation["receipt_digest"]: relation,
            closure["receipt_digest"]: closure,
            commit["receipt_digest"]: commit,
        }
        return payload, registry, receipts, relation, adjacency_output

    def test_s1d4_direct_adjacency_accepts_typed_op15_op16_witness(self) -> None:
        payload, registry, receipts, _, _ = self._valid_direct_adjacency_graph()
        HOOK.validate_evidence_graph_instance(
            payload,
            schema=self.evidence_schema,
            trusted_registry_store=self._registry_store(registry),
            result_sets=self.evidence_result_sets,
            plan_definition=self.evidence_plan["plan_definition"],
            investigation_snapshot=self.evidence_plan["investigation_snapshot"],
            receipt_store=receipts,
            operator_contract_schema=self._operator_contract_schema(),
        )

    def test_s1d4_direct_adjacency_rejects_self_edge_without_host_delta_recompute(self) -> None:
        payload, registry, receipts, relation, _ = self._valid_direct_adjacency_graph()
        edge = payload["edges"][0]
        edge["to_node_id"] = edge["from_node_id"]
        relation["to_node_binding"] = dict(relation["from_node_binding"])
        relation["receipt_digest"] = self._digest(relation, "receipt_digest")
        edge["relation_receipt_ref"] = relation["receipt_digest"]
        edge_body = json.loads(json.dumps(edge))
        edge_body["producer_ref"].pop("run_receipt_digest")
        edge["edge_digest"] = self._digest(edge_body, "edge_digest")
        receipts[relation["receipt_digest"]] = relation
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_evidence_graph_instance(
                payload,
                schema=self.evidence_schema,
                trusted_registry_store=self._registry_store(registry),
                result_sets=self.evidence_result_sets,
                plan_definition=self.evidence_plan["plan_definition"],
                investigation_snapshot=self.evidence_plan["investigation_snapshot"],
                receipt_store=receipts,
                operator_contract_schema=self._operator_contract_schema(),
            )
        self.assertEqual("evidence_graph_relation_endpoint_semantics_mismatch", captured.exception.code)

    def test_s1d4_direct_adjacency_rejects_projection_not_in_op16_members(self) -> None:
        payload, registry, receipts, relation, _ = self._valid_direct_adjacency_graph()
        relation["projection"]["neighbor_position"] = 99
        relation["projection_digest"] = self._digest({"projection": relation["projection"]})
        relation["receipt_digest"] = self._digest(relation, "receipt_digest")
        payload["edges"][0]["relation_receipt_ref"] = relation["receipt_digest"]
        edge_body = json.loads(json.dumps(payload["edges"][0]))
        edge_body.pop("edge_digest")
        edge_body["producer_ref"].pop("run_receipt_digest")
        payload["edges"][0]["edge_digest"] = self._digest(edge_body)
        receipts[relation["receipt_digest"]] = relation
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_evidence_graph_instance(
                payload,
                schema=self.evidence_schema,
                trusted_registry_store=self._registry_store(registry),
                result_sets=self.evidence_result_sets,
                plan_definition=self.evidence_plan["plan_definition"],
                investigation_snapshot=self.evidence_plan["investigation_snapshot"],
                receipt_store=receipts,
                operator_contract_schema=self._operator_contract_schema(),
            )
        self.assertEqual("evidence_graph_relation_endpoint_semantics_mismatch", captured.exception.code)

    def test_s1d4_dual_rejects_cross_evidence_incident_after_redigest(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["shared_answer_binding"]["incident_id"] = "INCIDENT-OTHER"
        payload["shared_answer_binding_digest"] = self._digest(
            payload["shared_answer_binding"]
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("dual_model_evidence_identity_mismatch", captured.exception.code)

    def test_s1d4_dual_rejects_plan_revision_newer_than_evidence_graph(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["shared_answer_binding"]["plan_revision"] = 2
        payload["shared_answer_binding_digest"] = self._digest(
            payload["shared_answer_binding"]
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("dual_model_evidence_identity_mismatch", captured.exception.code)

    def test_s1d4_degraded_schema_rejects_ghost_teacher_artifacts(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["flow_state"] = "degraded_published"
        payload["final_disposition"] = "ds_unaligned_degraded"
        payload["effective_teacher_required"] = False
        payload["alignment_run_receipt"] = None
        payload["published_answer"]["aligned_claim"] = False
        payload["published_answer"]["answer_digest"] = self._digest(
            payload["published_answer"], "answer_digest"
        )
        payload["degraded_authorization"] = {
            "authorization_id": "DEGRADE-1",
            "user_confirmed": True,
            "mode": "ds_unaligned_degraded",
            "parent_plan_revision": 1,
            "new_plan_revision": 2,
            "may_claim_sol_ds_alignment": False,
            "authorization_digest": self._hex("d"),
        }
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(payload, schema=self.dual_schema)
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_dual_rejects_completed_teacher_as_unavailable(self) -> None:
        payload = self._valid_dual_model_instance()
        payload["flow_state"] = "stopped_waiting_teacher"
        payload["final_disposition"] = "teacher_unavailable"
        payload["teacher_reference"] = None
        payload["teacher_validation_receipt"] = None
        payload["student_runs"] = []
        payload["student_validation_receipt"] = None
        payload["alignment_run_receipt"] = None
        payload["published_answer"] = None
        payload["publish_receipt_digest"] = None
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(payload, schema=self.dual_schema)
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_dual_rejects_teacher_reference_evidence_outside_graph(self) -> None:
        payload = self._valid_dual_model_instance()
        teacher_reference = payload["teacher_reference"]
        teacher_reference["evidence_refs"].append("EV-GHOST")
        teacher_reference["output_digest"] = self._digest(
            teacher_reference, "output_digest"
        )
        teacher_validation = payload["teacher_validation_receipt"]
        teacher_validation["subject_digest"] = teacher_reference["output_digest"]
        teacher_validation["receipt_digest"] = self._digest(
            teacher_validation, "receipt_digest"
        )
        teacher_run = payload["teacher_run_receipt"]
        teacher_run["output_digest"] = teacher_reference["output_digest"]
        teacher_run["validation_receipt_digest"] = teacher_validation[
            "receipt_digest"
        ]
        student = payload["student_runs"][0]
        student["teacher_reference_digest"] = teacher_reference["output_digest"]
        student["teacher_validation_receipt_digest"] = teacher_validation[
            "receipt_digest"
        ]
        student["run_receipt"]["role_specific_input_digest"] = self._digest(
            {
                "role": "student",
                "revision_ordinal": 0,
                "shared_answer_binding_digest": payload[
                    "shared_answer_binding_digest"
                ],
                "teacher_reference_digest": teacher_reference["output_digest"],
                "teacher_validation_receipt_digest": teacher_validation[
                    "receipt_digest"
                ],
                "structured_feedback_digest": None,
            }
        )
        alignment = payload["alignment_run_receipt"]
        alignment["teacher_reference_digest"] = teacher_reference["output_digest"]
        alignment["receipt_digest"] = self._digest(alignment, "receipt_digest")
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("teacher_reference_evidence_unclosed", captured.exception.code)

    def test_s1d4_dual_rejects_parent_from_other_flow(self) -> None:
        payload = self._valid_dual_model_instance()
        previous = json.loads(json.dumps(payload))
        previous["flow_id"] = "FLOW-OTHER"
        payload["flow_revision"] = 2
        payload["parent_flow_revision"] = 1
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                previous_flow=previous,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertEqual("revision_chain_identity_mismatch", captured.exception.code)

    def test_s1d4_transaction_rejects_untyped_artifact(self) -> None:
        payload = self._valid_transaction_instance()
        first_ref = payload["prepared_artifact_refs"][0]
        artifacts = dict(self.transaction_prepared_artifacts)
        artifacts[first_ref] = {"artifact_ref": first_ref, "value": 1}
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=self.transaction_contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store={
                    **self.transaction_prepared_artifact_store,
                    "artifacts": artifacts,
                },
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
                commit_receipt=self.transaction_commit_receipt,
                recovery_receipt=None,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_transaction_rejects_generic_placeholder_payload(self) -> None:
        payload = self._valid_transaction_instance()
        first_ref = payload["prepared_artifact_refs"][0]
        artifacts = json.loads(json.dumps(self.transaction_prepared_artifacts))
        artifacts[first_ref]["payload"] = {
            "subject_id": "SUBJECT-1",
            "subject_revision": 1,
            "value_digest": "sha256:" + self._hex("d"),
        }
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=self.transaction_contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store={
                    **self.transaction_prepared_artifact_store,
                    "artifacts": artifacts,
                },
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
                commit_receipt=self.transaction_commit_receipt,
                recovery_receipt=None,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_transaction_rejects_self_declared_gate_implementation(self) -> None:
        payload = self._valid_transaction_instance()
        gate_id = payload["validation_receipts"][0]["gate_id"]
        receipt = json.loads(
            json.dumps(self.transaction_validation_receipts[gate_id])
        )
        receipt["implementation_digest"] = "sha256:" + self._hex("f")
        receipt["receipt_digest"] = "sha256:" + self._digest(
            receipt, "receipt_digest"
        )
        payload["validation_receipts"][0] = receipt
        receipts = dict(self.transaction_validation_receipts)
        receipts[gate_id] = receipt
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=self.transaction_contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
                trusted_gate_receipt_store={
                    **self.transaction_gate_receipt_store,
                    "receipts": receipts,
                },
                commit_receipt=self.transaction_commit_receipt,
                recovery_receipt=None,
            )
        self.assertEqual(
            "transaction_validation_receipt_mismatch", captured.exception.code
        )

    def test_s1d4_transaction_rejects_resigned_gate_output_outside_registered_schema(self) -> None:
        payload = self._valid_transaction_instance()
        gate_id = payload["validation_receipts"][0]["gate_id"]
        receipt = json.loads(json.dumps(self.transaction_validation_receipts[gate_id]))
        receipt["gate_output"]["caller_claim"] = "passed"
        receipt["output_digest"] = "sha256:" + self._digest(
            {"gate_output": receipt["gate_output"]}
        )
        receipt["receipt_digest"] = "sha256:" + self._digest(
            receipt, "receipt_digest"
        )
        payload["validation_receipts"][0] = receipt
        receipts = dict(self.transaction_validation_receipts)
        receipts[gate_id] = receipt
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=self.transaction_contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
                trusted_gate_receipt_store={
                    **self.transaction_gate_receipt_store,
                    "receipts": receipts,
                },
                commit_receipt=self.transaction_commit_receipt,
                recovery_receipt=None,
            )
        self.assertEqual("instance_schema_validation_failed", captured.exception.code)

    def test_s1d4_all_five_transaction_kinds_accept_only_typed_closure(self) -> None:
        for kind in (
            "node_result_commit_consistency",
            "investigation_revision_commit_consistency",
            "evidence_graph_commit_consistency",
            "dialog_state_commit_consistency",
            "export_commit_consistency",
        ):
            with self.subTest(kind=kind):
                payload = self._valid_transaction_instance(kind)
                HOOK.validate_transaction_record_instance(
                    payload,
                    schema=self.transaction_schema,
                    consistency_contract=self.transaction_contract,
                    current_pointer=self.transaction_current_pointer,
                    trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
                    commit_receipt=self.transaction_commit_receipt,
                    recovery_receipt=None,
                )

    def test_s1d4_transaction_rejects_request_digest_not_bound_to_request(self) -> None:
        payload = self._valid_transaction_instance()
        request = json.loads(json.dumps(self.transaction_request))
        first_component = next(iter(request["payload"]))
        request["payload"][first_component] = "different_value"
        request["components"][first_component] = "different_value"
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=self.transaction_contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store={
                    **self.transaction_request_store,
                    "requests": {payload["request_digest"]: request},
                },
                trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
                commit_receipt=self.transaction_commit_receipt,
                recovery_receipt=None,
            )
        self.assertEqual(
            "transaction_idempotency_request_digest_mismatch", captured.exception.code
        )

    def test_s1d4_transaction_rejects_recovered_with_no_action(self) -> None:
        payload = self._valid_transaction_instance()
        payload.update(
            {
                "transaction_state": "aborted",
                "disposition": "recovered",
                "commit_marker": None,
                "commit_receipt_digest": None,
                "committed_revision": None,
                "committed_digest": None,
                "recovery_action": "none",
                "recovery_receipt_digest": None,
                "conflict_kind": None,
            }
        )
        payload["outcome_digest"] = "sha256:" + self._digest(
            {
                "transaction_id": payload["transaction_id"],
                "transaction_state": payload["transaction_state"],
                "disposition": payload["disposition"],
                "committed_revision": payload["committed_revision"],
                "committed_digest": payload["committed_digest"],
                "recovery_action": payload["recovery_action"],
                "conflict_kind": payload["conflict_kind"],
            }
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=self.transaction_contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
                commit_receipt=None,
                recovery_receipt=None,
            )
        self.assertEqual(
            "transaction_state_recovery_matrix_invalid", captured.exception.code
        )

    def test_s1d4_rejects_open_root_schema(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][0]

        def mutate(payload) -> None:
            payload["additionalProperties"] = True

        self._mutate_json(relative, mutate)
        self._assert_error("s1d4_schema_open", stage="S1D-4")

    def test_s1d4_rejects_missing_plan_revision_field(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][0]

        def mutate(payload) -> None:
            payload["$defs"]["planDefinition"]["required"].remove("plan_revision")

        self._mutate_json(relative, mutate)
        self._assert_error("plan_revision_contract_open", stage="S1D-4")

    def test_s1d4_rejects_untyped_plan_nodes(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][0]

        def mutate(payload) -> None:
            payload["$defs"]["planDefinition"]["properties"]["nodes"]["items"] = {}

        self._mutate_json(relative, mutate)
        self._assert_error("plan_typed_collection_open", stage="S1D-4")

    def test_s1d4_rejects_cross_stage_finality_drift(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][0]

        def mutate(payload) -> None:
            payload["$defs"]["identityBinding"]["properties"]["finality"]["enum"][1] = (
                "event_end_observed"
            )

        self._mutate_json(relative, mutate)
        self._assert_error("cross_stage_identity_contract_drift", stage="S1D-4")

    def test_s1d4_rejects_plan_node_fallback_units(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][0]

        def mutate(payload) -> None:
            payload["$defs"]["planNode"]["properties"]["fallback_units"] = {
                "type": "array"
            }

        self._mutate_json(relative, mutate)
        self._assert_error("composite_plan_node_forbidden", stage="S1D-4")

    def test_s1d4_rejects_same_parameter_rerun_plan_revision_drift(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][0]

        def mutate(payload) -> None:
            payload["x-revision-contract"][
                "same_parameter_rerun_changes_plan_revision"
            ] = True

        self._mutate_json(relative, mutate)
        self._assert_error("plan_revision_contract_open", stage="S1D-4")

    def test_s1d4_rejects_independent_branch_failure_propagation(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][0]

        def mutate(payload) -> None:
            payload["x-composition-contract"]["independent_branch_may_continue"] = False

        self._mutate_json(relative, mutate)
        self._assert_error("composite_plan_node_forbidden", stage="S1D-4")

    def test_s1d4_rejects_deferred_operator_admission(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][0]

        def mutate(payload) -> None:
            payload["x-composition-contract"]["p2_v1_deferred_units"] = ["TOOL-13"]

        self._mutate_json(relative, mutate)
        self._assert_error("deferred_unit_policy_open", stage="S1D-4")

    def test_s1d4_rejects_incomplete_input_default_not_fail_closed(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][0]

        def mutate(payload) -> None:
            payload["x-incomplete-input-contract"]["default"] = "lower_bound"

        self._mutate_json(relative, mutate)
        self._assert_error("incomplete_input_policy_open", stage="S1D-4")

    def test_s1d4_rejects_preview_as_completeness(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][1]

        def mutate(payload) -> None:
            payload["properties"]["set_completeness"]["enum"].append("preview")

        self._mutate_json(relative, mutate)
        self._assert_error("result_set_completeness_conflated", stage="S1D-4")

    def test_s1d4_rejects_open_page_token_chain(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][1]

        def mutate(payload) -> None:
            payload["x-result-set-closure-contract"]["token_chain_must_close"] = False

        self._mutate_json(relative, mutate)
        self._assert_error("result_set_page_closure_open", stage="S1D-4")

    def test_s1d4_rejects_untyped_page_manifest_items(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][1]

        def mutate(payload) -> None:
            payload["properties"]["page_manifest"]["items"] = {}

        self._mutate_json(relative, mutate)
        self._assert_error("result_set_typed_collection_open", stage="S1D-4")

    def test_s1d4_rejects_preview_population_overclaim(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][1]

        def mutate(payload) -> None:
            payload["$defs"]["previewView"]["properties"][
                "represents_complete_population"
            ]["const"] = True

        self._mutate_json(relative, mutate)
        self._assert_error("result_set_preview_overclaim", stage="S1D-4")

    def test_s1d4_rejects_export_from_incomplete_result_set(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][1]

        def mutate(payload) -> None:
            payload["x-export-contract"]["requires_frozen_complete_result_set"] = False

        self._mutate_json(relative, mutate)
        self._assert_error("result_set_export_boundary_open", stage="S1D-4")

    def test_s1d4_rejects_incomplete_population_jaccard(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][1]

        def mutate(payload) -> None:
            payload["x-incomplete-operator-contract"][
                "population_jaccard_coverage_and_total_relation_forbidden"
            ] = False

        self._mutate_json(relative, mutate)
        self._assert_error("incomplete_input_policy_open", stage="S1D-4")

    def test_s1d4_rejects_world_knowledge_fact_node_type(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][2]

        def mutate(payload) -> None:
            payload["$defs"]["graphNode"]["properties"]["node_type"]["enum"].append(
                "world_knowledge_fact"
            )

        self._mutate_json(relative, mutate)
        self._assert_error("evidence_node_population_drift", stage="S1D-4")

    def test_s1d4_rejects_unregistered_evidence_edge_type(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][2]

        def mutate(payload) -> None:
            payload["$defs"]["graphEdge"]["properties"]["edge_type"]["enum"].append(
                "causes"
            )

        self._mutate_json(relative, mutate)
        self._assert_error("evidence_edge_population_drift", stage="S1D-4")

    def test_s1d4_rejects_missing_forbidden_customer_relation(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][2]

        def mutate(payload) -> None:
            payload["x-forbidden-fact-relations"].remove("customer_of")

        self._mutate_json(relative, mutate)
        self._assert_error("forbidden_fact_relation_open", stage="S1D-4")

    def test_s1d4_rejects_dangling_graph_reference_policy(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][2]

        def mutate(payload) -> None:
            payload["x-graph-closure-contract"]["root_and_edge_endpoints_must_exist"] = False

        self._mutate_json(relative, mutate)
        self._assert_error("evidence_graph_closure_open", stage="S1D-4")

    def test_s1d4_rejects_path_contains_as_adjacency(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][2]

        def mutate(payload) -> None:
            payload["x-relation-semantic-contract"]["path_contains_is_not_adjacency"] = False

        self._mutate_json(relative, mutate)
        self._assert_error("path_relation_semantic_open", stage="S1D-4")

    def test_s1d4_rejects_39_and_1181_prefix_population_conflation(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][2]

        def mutate(payload) -> None:
            payload["x-relation-semantic-contract"][
                "as_owned_prefix_population_must_not_equal_path_association_prefix_population"
            ] = False

        self._mutate_json(relative, mutate)
        self._assert_error("path_relation_semantic_open", stage="S1D-4")

    def test_s1d4_rejects_world_knowledge_as_event_fact_source(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][2]

        def mutate(payload) -> None:
            payload["x-knowledge-boundary"]["world_knowledge_fact_node_forbidden"] = False

        self._mutate_json(relative, mutate)
        self._assert_error("world_knowledge_fact_boundary_open", stage="S1D-4")

    def test_s1d4_rejects_removed_evidence_payload_binding(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][2]

        def mutate(payload) -> None:
            payload["$defs"]["graphNode"]["allOf"] = []

        self._mutate_json(relative, mutate)
        self._assert_error("draft202012_schema_invalid", stage="S1D-4")

    def test_s1d4_rejects_ds_before_sol(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][3]

        def mutate(payload) -> None:
            payload["properties"]["execution_order"]["const"][0] = "ds_student"

        self._mutate_json(relative, mutate)
        self._assert_error("dual_model_order_drift", stage="S1D-4")

    def test_s1d4_rejects_different_student_evidence_binding(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][3]

        def mutate(payload) -> None:
            payload["x-shared-binding-equality-contract"][
                "teacher_and_student_must_share_grounding_plan_evidence_bundle_evidence_graph_registry_and_boundary_policy"
            ] = False

        self._mutate_json(relative, mutate)
        self._assert_error("shared_binding_contract_open", stage="S1D-4")

    def test_s1d4_rejects_teacher_as_ground_truth(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][3]

        def mutate(payload) -> None:
            payload["$defs"]["teacherReference"]["properties"][
                "teacher_reference_is_ground_truth"
            ]["const"] = True

        self._mutate_json(relative, mutate)
        self._assert_error("teacher_truth_conflation", stage="S1D-4")

    def test_s1d4_rejects_student_after_teacher_rejection(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][3]

        def mutate(payload) -> None:
            branch = next(
                item
                for item in payload["allOf"]
                if item.get("if", {}).get("properties", {}).get("flow_state", {}).get("const")
                == "teacher_rejected"
            )
            branch["then"]["properties"]["student_runs"]["maxItems"] = 1

        self._mutate_json(relative, mutate)
        self._assert_error("teacher_rejected_forwarding_open", stage="S1D-4")

    def test_s1d4_rejects_second_student_revision(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][3]

        def mutate(payload) -> None:
            payload["properties"]["student_runs"]["maxItems"] = 3

        self._mutate_json(relative, mutate)
        self._assert_error("student_revision_limit_open", stage="S1D-4")

    def test_s1d4_rejects_student_tool_calls(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][3]

        def mutate(payload) -> None:
            payload["$defs"]["studentRun"]["properties"]["may_call_tools"]["const"] = True

        self._mutate_json(relative, mutate)
        self._assert_error("shared_binding_contract_open", stage="S1D-4")

    def test_s1d4_rejects_untyped_student_run_items(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][3]

        def mutate(payload) -> None:
            payload["properties"]["student_runs"]["items"] = {}

        self._mutate_json(relative, mutate)
        self._assert_error("dual_model_role_binding_open", stage="S1D-4")

    def test_s1d4_rejects_text_similarity_over_hard_gates(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][3]

        def mutate(payload) -> None:
            payload["x-alignment-contract"]["text_similarity_may_override_hard_gate"] = True

        self._mutate_json(relative, mutate)
        self._assert_error("alignment_hard_gate_open", stage="S1D-4")

    def test_s1d4_rejects_silent_ds_degrade(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][3]

        def mutate(payload) -> None:
            payload["x-degraded-contract"]["silent_degrade_forbidden"] = False

        self._mutate_json(relative, mutate)
        self._assert_error("silent_degrade_open", stage="S1D-4")

    def test_s1d4_rejects_missing_commit_consistency_kind(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][4]

        def mutate(payload) -> None:
            payload["boundaries"].pop()

        self._mutate_json(relative, mutate)
        self._assert_error("runtime_commit_consistency_coverage_mismatch", stage="S1D-4")

    def test_s1d4_rejects_open_commit_envelope(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][4]

        def mutate(payload) -> None:
            payload["transaction_envelope"]["required_fields"].remove("idempotency_key")

        self._mutate_json(relative, mutate)
        self._assert_error("commit_envelope_open", stage="S1D-4")

    def test_s1d4_rejects_half_node_atomic_write_set(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][4]

        def mutate(payload) -> None:
            node = next(
                item
                for item in payload["boundaries"]
                if item["id"] == "node_result_commit_consistency"
            )
            node["atomic_write_set"].remove("node_evidence_fragment")

        self._mutate_json(relative, mutate)
        self._assert_error("commit_atomic_write_set_open", stage="S1D-4")

    def test_s1d4_rejects_dialog_advance_without_validated_answer(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][4]

        def mutate(payload) -> None:
            dialog = next(
                item
                for item in payload["boundaries"]
                if item["id"] == "dialog_state_commit_consistency"
            )
            dialog["preconditions"].remove("student_answer_validation_passed")

        self._mutate_json(relative, mutate)
        self._assert_error("commit_boundary_contract_incomplete", stage="S1D-4")

    def test_s1d4_rejects_export_replacing_final_on_failure(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][4]

        def mutate(payload) -> None:
            export = next(
                item
                for item in payload["boundaries"]
                if item["id"] == "export_commit_consistency"
            )
            export["forbidden_visibility"].remove(
                "failed_export_removes_previous_final_artifact"
            )

        self._mutate_json(relative, mutate)
        self._assert_error("commit_boundary_contract_incomplete", stage="S1D-4")

    def test_s1d4_rejects_global_commit_order_drift(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][4]

        def mutate(payload) -> None:
            payload["global_commit_order"].reverse()

        self._mutate_json(relative, mutate)
        self._assert_error("commit_order_or_failure_injection_drift", stage="S1D-4")

    def test_s1d4_rejects_missing_failure_injection(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][4]

        def mutate(payload) -> None:
            payload["required_failure_injections"].pop()

        self._mutate_json(relative, mutate)
        self._assert_error("commit_order_or_failure_injection_drift", stage="S1D-4")

    def test_s1d4_rejects_commit_point_semantic_drift(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][4]

        def mutate(payload) -> None:
            export = next(
                item
                for item in payload["boundaries"]
                if item["id"] == "export_commit_consistency"
            )
            export["commit_point"] = "replace_before_validation"

        self._mutate_json(relative, mutate)
        self._assert_error("commit_boundary_semantic_drift", stage="S1D-4")

    def test_s1d4_rejects_runtime_implementation_claim(self) -> None:
        self._copy_through_s1d4()
        relative = HOOK.ARTIFACTS_BY_STAGE["S1D-4"][4]

        def mutate(payload) -> None:
            payload["design_boundary"]["runtime_implemented"] = True

        self._mutate_json(relative, mutate)
        self._assert_error("runtime_claim_forbidden", stage="S1D-4")

    def test_s1d4_plan_rejects_missing_parameter_binding_coverage(self) -> None:
        payload = self._valid_plan_instance()
        definition = payload["plan_definition"]
        definition["plan_state"] = "draft"
        definition["admission_receipt_digest"] = None
        definition["nodes"][0]["input_bindings"] = []
        definition["dag_digest"] = self._digest({"nodes": definition["nodes"]})
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store={},
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual(
            "plan_input_binding_coverage_mismatch", captured.exception.code
        )

    def test_s1d4_plan_rejects_ancestor_artifact_digest_drift(self) -> None:
        payload = self._valid_plan_instance()
        definition = payload["plan_definition"]
        definition["plan_state"] = "draft"
        definition["admission_receipt_digest"] = None
        self._add_ancestor_result_consumer(
            payload, source_artifact_digest=self._hex("0")
        )
        trusted_results = self._node_result_receipt_store(payload)
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store={},
                trusted_node_result_receipt_store=trusted_results,
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual(
            "plan_input_binding_artifact_mismatch", captured.exception.code
        )

    def test_s1d4_plan_rejects_snapshot_self_reported_ancestor_without_trusted_receipt(self) -> None:
        payload = self._valid_plan_instance()
        definition = payload["plan_definition"]
        definition["plan_state"] = "draft"
        definition["admission_receipt_digest"] = None
        self._add_ancestor_result_consumer(
            payload, source_artifact_digest=self._hex("f")
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_investigation_plan_instance(
                payload,
                schema=self.plan_schema,
                trusted_registry_store=self.plan_registry,
                trusted_admission_receipt_store={},
                trusted_node_result_receipt_store=None,
                parameter_bindings=self.plan_parameters,
            )
        self.assertEqual(
            "plan_input_binding_artifact_mismatch", captured.exception.code
        )

    def test_s1d4_plan_accepts_ancestor_bound_to_trusted_committed_result_receipt(self) -> None:
        payload = self._valid_plan_instance()
        definition = payload["plan_definition"]
        definition["plan_state"] = "draft"
        definition["admission_receipt_digest"] = None
        self._add_ancestor_result_consumer(
            payload, source_artifact_digest=self._hex("f")
        )
        trusted_results = self._node_result_receipt_store(payload)
        HOOK.validate_investigation_plan_instance(
            payload,
            schema=self.plan_schema,
            trusted_registry_store=self.plan_registry,
            trusted_admission_receipt_store={},
            trusted_node_result_receipt_store=trusted_results,
            parameter_bindings=self.plan_parameters,
        )

    def test_s1d4_source_incomplete_rejects_unresolved_provenance_receipt(self) -> None:
        payload, members = self._valid_result_set_instance()
        payload["set_completeness"] = "source_incomplete"
        payload["limitations"] = [
            {
                "code": "SOURCE_INCOMPLETE",
                "scope": "source",
                "description": "源制品声明人口不完整",
            }
        ]
        payload["source_completeness_receipt_digest"] = self._hex("f")
        freeze = self._receipt(
            receipt_kind="freeze",
            result_set_id=payload["result_set_id"],
            manifest_digest=payload["manifest_digest"],
            content_digest=payload["content_digest"],
            returned_count=payload["returned_count"],
            total_count=payload["total_count"],
            set_completeness="source_incomplete",
            source_population_id=payload["source_population_id"],
            source_population_schema_digest=payload[
                "source_population_schema_digest"
            ],
            source_dataset_digest=payload["source_dataset_digest"],
            disposition="passed",
        )
        payload["freeze_receipt_digest"] = freeze["receipt_digest"]
        self.result_receipts[freeze["receipt_digest"]] = freeze
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_result_set_instance(
                payload,
                schema=self.result_schema,
                resolved_members=members,
                trusted_registry_store=self.result_registry,
                receipt_store=self.result_receipts,
            )
        self.assertEqual(
            "result_set_source_incomplete_provenance_unresolved",
            captured.exception.code,
        )

    def test_s1d4_export_rejects_non_finite_json_number(self) -> None:
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK._canonical_export_bytes([{"metric": float("nan")}], "json")
        self.assertEqual("export_non_finite_number", captured.exception.code)

    def test_s1d4_evidence_rejects_nonfrozen_operator_schema(self) -> None:
        payload, registry, receipts, _, _ = self._valid_direct_adjacency_graph()
        operator_schema = self._operator_contract_schema()
        operator_schema["title"] = "漂移的Operator Schema"
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_evidence_graph_instance(
                payload,
                schema=self.evidence_schema,
                trusted_registry_store=self._registry_store(registry),
                result_sets=self.evidence_result_sets,
                plan_definition=self.evidence_plan["plan_definition"],
                investigation_snapshot=self.evidence_plan[
                    "investigation_snapshot"
                ],
                receipt_store=receipts,
                operator_contract_schema=operator_schema,
            )
        self.assertEqual(
            "operator_contract_schema_identity_mismatch", captured.exception.code
        )

    def test_s1d4_evidence_rejects_unrelated_typed_asn_endpoint(self) -> None:
        payload, registry, receipts, relation, _ = (
            self._valid_direct_adjacency_graph()
        )
        second = next(node for node in payload["nodes"] if node["node_id"] == "F2")
        schema_ref = "https://domeye.example/types/asn.json"
        unrelated_digest = self._digest(
            {"value_schema_ref": schema_ref, "value": 58224}
        )
        second["payload"]["fact_value_digest"] = unrelated_digest
        second["payload"]["fact_value_projection"] = {
            "value_schema_ref": schema_ref,
            "value": 58224,
            "value_digest": unrelated_digest,
        }
        second["payload_digest"] = self._digest({"payload": second["payload"]})
        producer = self._receipt(
            receipt_kind="producer",
            producer_id="TOOL-07",
            output_digest=second["payload_digest"],
            disposition="committed",
        )
        second["producer_ref"]["run_receipt_digest"] = producer["receipt_digest"]
        receipts[producer["receipt_digest"]] = producer
        relation["to_node_binding"] = {
            "node_id": "F2",
            "node_payload_digest": second["payload_digest"],
            "domain_value_digest": unrelated_digest,
            "typed_value_schema_ref": schema_ref,
            "typed_value": 58224,
        }
        relation["receipt_digest"] = self._digest(relation, "receipt_digest")
        receipts[relation["receipt_digest"]] = relation
        edge = payload["edges"][0]
        edge["relation_receipt_ref"] = relation["receipt_digest"]
        edge_body = json.loads(json.dumps(edge))
        edge_body["producer_ref"].pop("run_receipt_digest")
        edge["edge_digest"] = self._digest(edge_body, "edge_digest")
        payload["graph_digest"] = HOOK._evidence_graph_content_digest(payload)
        closure = self._receipt(
            receipt_kind="graph_closure",
            graph_digest=payload["graph_digest"],
            disposition="passed",
        )
        commit = self._receipt(
            receipt_kind="graph_commit",
            graph_digest=payload["graph_digest"],
            disposition="committed",
        )
        payload["closure_receipt_digest"] = closure["receipt_digest"]
        payload["commit_receipt_digest"] = commit["receipt_digest"]
        receipts[closure["receipt_digest"]] = closure
        receipts[commit["receipt_digest"]] = commit
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_evidence_graph_instance(
                payload,
                schema=self.evidence_schema,
                trusted_registry_store=self._registry_store(registry),
                result_sets=self.evidence_result_sets,
                plan_definition=self.evidence_plan["plan_definition"],
                investigation_snapshot=self.evidence_plan[
                    "investigation_snapshot"
                ],
                receipt_store=receipts,
                operator_contract_schema=self._operator_contract_schema(),
            )
        self.assertEqual(
            "evidence_graph_relation_endpoint_semantics_mismatch",
            captured.exception.code,
        )

    def test_s1d4_dual_rejects_committed_graph_content_drift(self) -> None:
        payload = self._valid_dual_model_instance()
        graph_digest = payload["shared_answer_binding"]["evidence_graph_digest"]
        record = self.dual_graph_store["graphs"][graph_digest]
        record["graph"]["nodes"][0]["evidence_refs"].append("EV-TAMPER")
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_dual_model_flow_instance(
                payload,
                schema=self.dual_schema,
                evidence_graph=self.dual_evidence_graph,
                trusted_committed_graph_store=self.dual_graph_store,
                publish_receipt=self.dual_publish_receipt,
            )
        self.assertIn(
            captured.exception.code,
            {
                "evidence_graph_digest_mismatch",
                "committed_graph_payload_digest_mismatch",
            },
        )

    def test_s1d4_transaction_rejects_same_digest_conflict_even_if_revision_differs(self) -> None:
        payload = self._valid_transaction_instance()
        recovery = self._as_compare_and_swap_conflict(
            payload, actual_mismatch=False
        )
        payload["parent_revision"] = 2
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=self.transaction_contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
                commit_receipt=None,
                recovery_receipt=recovery,
            )
        self.assertEqual(
            "transaction_cas_conflict_not_observed", captured.exception.code
        )

    def test_s1d4_transaction_rejects_resigned_gate_registry_contract(self) -> None:
        payload = self._valid_transaction_instance()
        contract = json.loads(json.dumps(self.transaction_contract))
        entry = contract["trusted_gate_validator_registry"]["entries"][0]
        entry["implementation_artifact"]["deterministic_predicate_id"] = (
            "forged.predicate"
        )
        entry["implementation_digest"] = "sha256:" + self._digest(
            entry["implementation_artifact"]
        )
        registry = contract["trusted_gate_validator_registry"]
        registry["registry_content_digest"] = self._digest(
            registry, "registry_content_digest"
        )
        contract["contract_content_digest"] = self._digest(
            contract, "contract_content_digest"
        )
        with self.assertRaises(HOOK.AlignmentError) as captured:
            HOOK.validate_transaction_record_instance(
                payload,
                schema=self.transaction_schema,
                consistency_contract=contract,
                current_pointer=self.transaction_current_pointer,
                trusted_transaction_request_store=self.transaction_request_store,
                trusted_prepared_artifact_store=self.transaction_prepared_artifact_store,
                trusted_gate_receipt_store=self.transaction_gate_receipt_store,
                commit_receipt=self.transaction_commit_receipt,
                recovery_receipt=None,
            )
        self.assertEqual(
            "transaction_gate_registry_attestation_invalid",
            captured.exception.code,
        )

    def test_receipt_is_written_atomically_with_self_digest(self) -> None:
        receipt = HOOK.run_alignment(self.root, "S1D-0")
        output = HOOK.RECEIPT_ROOT / "S1D-0.json"
        target = HOOK.write_receipt(self.root, output, receipt)
        stored = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(stored["receipt_digest"], HOOK._canonical_digest(stored))
        leftovers = list(target.parent.glob(f".{target.name}.*.tmp"))
        self.assertEqual([], leftovers)


if __name__ == "__main__":
    unittest.main()
