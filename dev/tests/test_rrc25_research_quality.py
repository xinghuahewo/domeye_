from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unittest

from backend.data_pipeline.route_event import artifact_id_v1, route_event_id_v1
from backend.data_pipeline.research.rrc25_country_outage.research_quality import (
    CONTRACT_GATE_IDS,
    DiagnosticFact,
    DiagnosticViolation,
    GATE_ORDER,
    ResearchQualityInput,
    ResearchQualityInputError,
    evaluate_research_quality,
)


SHA = "1" * 64
SAMPLE_1 = "sample_v1_" + "1" * 24
SAMPLE_2 = "sample_v1_" + "2" * 24
SNAPSHOT_1 = "snapshot_v1_" + "1" * 24
SNAPSHOT_2 = "snapshot_v1_" + "2" * 24
EPISODE = "episode_v1_" + "3" * 24
WAVE = "wave_v1_" + "4" * 24


def raw_id(file_sha256: str, record: int, element: int) -> str:
    identity = {
        "schema": "raw_record_ref_id_v1",
        "file_sha256": file_sha256,
        "record_ordinal": record,
        "element_ordinal": element,
    }
    encoded = json.dumps(
        identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return "raw_v1_" + hashlib.sha256(encoded).hexdigest()[:32]


def measure(sample_id: str, snapshot_id: str, value: int = 1) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "snapshot_id": snapshot_id,
        "value": value,
        "value_state": "observed" if value else "observed_zero",
        "missing_reason": None,
    }


def ratio(sample_id: str, snapshot_id: str) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "snapshot_id": snapshot_id,
        "numerator": {
            "sample_id": sample_id,
            "snapshot_id": snapshot_id,
            "value": 1,
        },
        "denominator": {
            "sample_id": sample_id,
            "snapshot_id": snapshot_id,
            "value": 2,
        },
        "value": 0.5,
        "value_state": "observed",
        "missing_reason": None,
    }


def asn_set(sample_id: str, snapshot_id: str) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "snapshot_id": snapshot_id,
        "value": [1],
        "value_state": "observed",
        "missing_reason": None,
    }


def sample(sample_id: str, snapshot_id: str) -> dict[str, object]:
    metric_names = (
        "visible_asn_count",
        "damaged_asn_count",
        "baseline_asn_count",
        "visible_ipv4_prefix_count",
        "visible_ipv6_prefix_count",
        "visible_ipv4_address_union",
        "visible_ipv4_24_equivalent",
        "visible_ipv6_48_equivalent",
        "announce_count",
        "withdraw_count",
        "vp_expected_count",
        "vp_observed_count",
    )
    metrics = {name: measure(sample_id, snapshot_id) for name in metric_names}
    metrics["damaged_asn_ratio"] = ratio(sample_id, snapshot_id)
    return {
        "sample_id": sample_id,
        "snapshot_id": snapshot_id,
        "continuity_state": "continuous",
        "metrics": metrics,
        "asn_sets": {
            name: asn_set(sample_id, snapshot_id)
            for name in ("visible", "damaged", "baseline")
        },
    }


def explicit_pass_facts() -> tuple[DiagnosticFact, ...]:
    return tuple(
        DiagnosticFact(
            gate_id=gate,
            code="checked",
            passed=True,
            details_zh=f"已明确检查质量门 {gate}。",
        )
        for gate in GATE_ORDER
    )


def valid_quality_input(**overrides: object) -> ResearchQualityInput:
    artifact = artifact_id_v1(SHA)
    route = route_event_id_v1(SHA, 7, 2)
    raw = raw_id(SHA, 7, 2)
    values: dict[str, object] = {
        "facts": explicit_pass_facts(),
        "samples": (sample(SAMPLE_1, SNAPSHOT_1), sample(SAMPLE_2, SNAPSHOT_2)),
        "episodes": (
            {
                "episode_id": EPISODE,
                "supporting_sample_ids": [SAMPLE_1, SAMPLE_2],
                "wave_ids": [WAVE],
                "split_evidence": [],
                "incident_mappings": [
                    {"evidence_sample_ids": [SAMPLE_1]}
                ],
            },
        ),
        "waves": (
            {
                "wave_id": WAVE,
                "episode_id": EPISODE,
                "supporting_sample_ids": [SAMPLE_1, SAMPLE_2],
                "split_evidence": None,
            },
        ),
        "episode_as_records": (
            {
                "episode_as_id": "episode_as_v1_" + "5" * 24,
                "mapping_evidence": {"mapping_state": "mapped"},
                "evidence_links": [
                    {
                        "route_event_id": route,
                        "raw_record_ref_id": raw,
                        "artifact_id": artifact,
                        "artifact_sha256": SHA,
                        "record_ordinal": 7,
                        "element_ordinal": 2,
                    }
                ],
            },
        ),
        "route_events": (
            {
                "route_event_id": route,
                "raw_record_ref_id": raw,
                "artifact_id": artifact,
                "file_sha256": SHA,
                "record_ordinal": 7,
                "element_ordinal": 2,
                "raw_closure_state": "verified_raw_audit",
            },
        ),
        "raw_refs": (
            {
                "raw_record_ref_id": raw,
                "artifact_id": artifact,
                "file_sha256": SHA,
                "record_offset": 4096,
                "record_length": 128,
                "record_hash": "2" * 64,
                "record_ordinal": 7,
                "element_ordinal": 2,
                "verification_status": "verified",
                "raw_closure_state": "verified_raw_audit",
                "missing_reason_zh": None,
            },
        ),
        "artifacts": ({"artifact_id": artifact, "file_sha256": SHA},),
        "execution": {
            "database_write_operations": 0,
            "new_raw_bytes_read": 49_999_999_999,
            "peak_temporary_bytes": 4_999_999_999,
            "max_worker_seconds": 599.999,
        },
        "semantic_fingerprints": ("a" * 64, "a" * 64),
    }
    values.update(overrides)
    return ResearchQualityInput(**values)  # type: ignore[arg-type]


def gates_by_id(result: object) -> dict[str, object]:
    return {gate.gate_id: gate for gate in result.gates}  # type: ignore[attr-defined]


class ResearchQualityHappyPathTest(unittest.TestCase):
    def test_ten_gates_close_and_contract_projection_is_exact(self):
        result = evaluate_research_quality(valid_quality_input())

        self.assertEqual(result.run_state, "completed")
        self.assertEqual(result.acceptance_state, "accepted")
        self.assertEqual([gate.gate_id for gate in result.gates], list(GATE_ORDER))
        self.assertTrue(all(gate.status == "pass" for gate in result.gates))

        projected = result.to_research_run_fields()
        self.assertEqual(projected["run_state"], "completed")
        self.assertEqual(projected["acceptance_state"], "accepted")
        contract_gates = projected["quality_gates"]
        self.assertEqual(
            [item["gate_id"] for item in contract_gates],
            [CONTRACT_GATE_IDS[item] for item in GATE_ORDER],
        )
        self.assertTrue(all(item["status"] == "pass" for item in contract_gates))

    def test_result_is_deterministic_when_inputs_are_reordered(self):
        first = valid_quality_input()
        second = valid_quality_input(
            facts=tuple(reversed(first.facts)),
            samples=tuple(reversed(first.samples)),
            episodes=tuple(reversed(first.episodes)),
            waves=tuple(reversed(first.waves)),
        )

        self.assertEqual(
            evaluate_research_quality(first).to_dict(),
            evaluate_research_quality(second).to_dict(),
        )

    def test_nonblocking_warning_does_not_masquerade_as_failure(self):
        warning = DiagnosticViolation(
            gate_id="vp_coverage",
            code="vp_optional_context_sparse",
            details_zh="一个非必要观测点的上下文较少，仅作为警告保留。",
            severity="warn",
            blocking=False,
        )
        data = valid_quality_input(violations=(warning,))

        result = evaluate_research_quality(data)
        gate = gates_by_id(result)["vp_coverage"]
        self.assertEqual(gate.status, "warn")
        self.assertFalse(gate.blocking)
        self.assertEqual(result.acceptance_state, "accepted")
        contract = result.to_research_run_fields()["quality_gates"]
        projected = next(item for item in contract if item["gate_id"] == "vp_coverage")
        self.assertEqual(projected["status"], "pending")
        self.assertFalse(projected["blocking"])

    def test_missing_explicit_fact_blocks_acceptance(self):
        data = valid_quality_input(
            facts=tuple(
                fact
                for fact in explicit_pass_facts()
                if fact.gate_id != "input_completeness"
            )
        )
        result = evaluate_research_quality(data)

        gate = gates_by_id(result)["input_completeness"]
        self.assertEqual(gate.status, "fail")
        self.assertTrue(gate.blocking)
        self.assertEqual(result.run_state, "incomplete")
        self.assertEqual(result.acceptance_state, "not_accepted")


class ResearchQualityCrossStructureTest(unittest.TestCase):
    def test_measure_must_bind_parent_sample_and_snapshot(self):
        samples = list(deepcopy(valid_quality_input().samples))
        samples[0]["metrics"]["visible_asn_count"]["snapshot_id"] = SNAPSHOT_2
        result = evaluate_research_quality(
            valid_quality_input(samples=tuple(samples))
        )

        gate = gates_by_id(result)["stable_identity"]
        self.assertEqual(gate.status, "fail")
        self.assertIn(
            "measure_parent_identity_mismatch",
            {item.code for item in gate.diagnostics},
        )

    def test_unknown_value_cannot_be_zero_or_omit_reason(self):
        samples = list(deepcopy(valid_quality_input().samples))
        value = samples[0]["metrics"]["withdraw_count"]
        value.update(
            value=0,
            value_state="unknown_state_gap",
            missing_reason=None,
        )
        result = evaluate_research_quality(
            valid_quality_input(samples=tuple(samples))
        )

        gate = gates_by_id(result)["missing_semantics"]
        codes = {item.code for item in gate.diagnostics}
        self.assertEqual(gate.status, "fail")
        self.assertIn("unknown_has_non_null_value", codes)
        self.assertIn("unknown_missing_reason", codes)

    def test_prior_state_gap_allows_current_raw_counts_but_not_state_metrics(self):
        samples = list(deepcopy(valid_quality_input().samples))
        current = samples[0]
        current["continuity_state"] = "unknown_after_gap"
        unknown_fields = {
            "visible_asn_count",
            "damaged_asn_count",
            "baseline_asn_count",
            "visible_ipv4_prefix_count",
            "visible_ipv6_prefix_count",
            "visible_ipv4_address_union",
            "visible_ipv4_24_equivalent",
            "visible_ipv6_48_equivalent",
            "damaged_asn_ratio",
        }
        for name in unknown_fields:
            value = current["metrics"][name]
            value.update(
                value=None,
                value_state="unknown_state_gap",
                missing_reason="prior_state_gap",
            )
            if name == "damaged_asn_ratio":
                value.update(numerator=None, denominator=None)
        for value in current["asn_sets"].values():
            value.update(
                value=None,
                value_state="unknown_state_gap",
                missing_reason="prior_state_gap",
            )

        result = evaluate_research_quality(
            valid_quality_input(samples=tuple(samples))
        )
        state_gate = gates_by_id(result)["state_continuity"]
        codes = {item.code for item in state_gate.diagnostics}
        self.assertNotIn("gap_sample_has_observed_measure", codes)
        self.assertIn("state_continuity_unknown", codes)
        self.assertEqual(state_gate.status, "fail")
        # 当前槽独立可观测的消息计数仍保留真实值。
        self.assertEqual(current["metrics"]["announce_count"]["value"], 1)
        self.assertEqual(current["metrics"]["vp_observed_count"]["value"], 1)

    def test_unresolved_mapping_blocks_mapping_gate(self):
        records = list(deepcopy(valid_quality_input().episode_as_records))
        records[0]["mapping_evidence"]["mapping_state"] = "conflict"
        result = evaluate_research_quality(
            valid_quality_input(episode_as_records=tuple(records))
        )

        gate = gates_by_id(result)["mapping_coverage"]
        self.assertEqual(gate.status, "fail")
        self.assertIn(
            "episode_as_mapping_unresolved",
            {item.code for item in gate.diagnostics},
        )

    def test_episode_and_wave_sample_references_must_exist(self):
        episodes = list(deepcopy(valid_quality_input().episodes))
        waves = list(deepcopy(valid_quality_input().waves))
        missing = "sample_v1_" + "f" * 24
        episodes[0]["supporting_sample_ids"].append(missing)
        waves[0]["supporting_sample_ids"].append(missing)
        result = evaluate_research_quality(
            valid_quality_input(episodes=tuple(episodes), waves=tuple(waves))
        )

        gate = gates_by_id(result)["reference_closure"]
        codes = {item.code for item in gate.diagnostics}
        self.assertEqual(gate.status, "fail")
        self.assertIn("episode_sample_unresolved", codes)
        self.assertIn("wave_sample_unresolved", codes)

    def test_route_raw_artifact_chain_must_close(self):
        result = evaluate_research_quality(valid_quality_input(raw_refs=()))

        gate = gates_by_id(result)["reference_closure"]
        codes = {item.code for item in gate.diagnostics}
        self.assertEqual(gate.status, "fail")
        self.assertIn("route_raw_ref_unresolved", codes)
        self.assertIn("episode_as_evidence_unresolved", codes)

    def test_coordinate_only_raw_reference_blocks_reference_closure(self):
        routes = list(deepcopy(valid_quality_input().route_events))
        raw_refs = list(deepcopy(valid_quality_input().raw_refs))
        routes[0]["raw_closure_state"] = "derived_coordinate_only"
        raw_refs[0].update(
            record_offset=None,
            record_length=None,
            record_hash=None,
            verification_status="derived_coordinate_only",
            raw_closure_state="unverified",
            missing_reason_zh="仅由 RouteEvent 坐标推导，尚未执行正式 raw audit。",
        )

        result = evaluate_research_quality(
            valid_quality_input(route_events=tuple(routes), raw_refs=tuple(raw_refs))
        )
        gate = gates_by_id(result)["reference_closure"]
        self.assertEqual(gate.status, "fail")
        self.assertIn("raw_audit_unverified", {item.code for item in gate.diagnostics})
        self.assertEqual(result.acceptance_state, "not_accepted")

    def test_stable_route_id_must_match_raw_coordinates(self):
        routes = list(deepcopy(valid_quality_input().route_events))
        routes[0]["route_event_id"] = "rte_v1_" + "0" * 32
        result = evaluate_research_quality(
            valid_quality_input(route_events=tuple(routes))
        )

        gate = gates_by_id(result)["stable_identity"]
        self.assertEqual(gate.status, "fail")
        self.assertIn(
            "route_id_coordinate_mismatch",
            {item.code for item in gate.diagnostics},
        )


class ResearchQualityBoundaryTest(unittest.TestCase):
    def test_each_exact_decimal_resource_boundary_is_rejected(self):
        cases = (
            ("new_raw_bytes_read", 50_000_000_000),
            ("peak_temporary_bytes", 5_000_000_000),
            ("max_worker_seconds", 600),
            ("database_write_operations", 1),
        )
        for field, value in cases:
            with self.subTest(field=field):
                execution = dict(valid_quality_input().execution or {})
                execution[field] = value
                result = evaluate_research_quality(
                    valid_quality_input(execution=execution)
                )
                gate = gates_by_id(result)["resource_usage"]
                self.assertEqual(gate.status, "fail")
                self.assertTrue(gate.blocking)
                self.assertEqual(result.run_state, "incomplete")
                self.assertEqual(result.acceptance_state, "not_accepted")

    def test_two_run_semantic_fingerprint_must_match(self):
        result = evaluate_research_quality(
            valid_quality_input(
                semantic_fingerprints=("a" * 64, "b" * 64)
            )
        )

        gate = gates_by_id(result)["reproducibility"]
        self.assertEqual(gate.status, "fail")
        self.assertIn(
            "semantic_fingerprint_mismatch",
            {item.code for item in gate.diagnostics},
        )

    def test_warning_cannot_be_declared_blocking(self):
        with self.assertRaisesRegex(ResearchQualityInputError, "blocking=false"):
            DiagnosticViolation(
                gate_id="vp_coverage",
                code="warning",
                details_zh="这是一条非阻断警告。",
                severity="warn",
                blocking=True,
            )


if __name__ == "__main__":
    unittest.main()
