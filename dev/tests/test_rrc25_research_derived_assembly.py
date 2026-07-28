from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from backend.data_pipeline.route_event import (
    AsPathSegment,
    ParsedRouteElement,
    artifact_id_v1,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    MAPPED,
    MappingAssignment,
    build_country_mapping_view,
    snapshot_id_v1,
)
from backend.data_pipeline.research.rrc25_country_outage.derived_assembly import (
    DerivedAssemblyError,
    SlotResearchMetadata,
    _automatic_prefix_change_event_ids,
    _quality_evidence_projection,
    _merge_prefix_change_event_ids,
    assemble_derived_research,
    validate_incident_episode_mapping_record,
)
from backend.data_pipeline.research.rrc25_country_outage.research_quality import (
    DiagnosticFact,
    GATE_ORDER,
)
from backend.data_pipeline.research.rrc25_country_outage.sample_builder import (
    SampleSourceRef,
    observed_slot_count,
)
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    CONTINUOUS,
    ReplaySnapshot,
    RouteReplayState,
    RouteStateEntry,
    build_research_route_event,
)
ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 2, 27, 16, 0, tzinfo=timezone.utc)
RUN_ID = "research_run_v1_" + "9" * 24
FILE_HASH = "a" * 64
ASN = 65001


def _phase():
    return {
        "source_field": "legacy_country_outage",
        "semantics": "route_observation_not_causal_trace",
        "supports_recovery": False,
        "status": "not_applicable",
        "missing_reason": "legacy_not_applicable",
        "observations": None,
    }


def _incident(index=1):
    incident_id = "inc_v1_{:024x}".format(index)
    detail = "country_outage/2026-02-27 09:12:{:02d}/IR/{}/r".format(
        31 + index, index
    )
    return {
        "schema_version": "p0_incident_normalization_v1",
        "incident_id": incident_id,
        "incident_id_schema": "incident_id_v1",
        "event_type": "country_outage",
        "source_code": "r",
        "source_table": "country_outage_202602",
        "source_primary_key": {
            "source": "r",
            "country": "IR",
            "outage_id": index,
        },
        "detail_reference": detail,
        "event_time_utc": "2026-02-27T01:12:{:02d}Z".format(31 + index),
        "end_time_utc": None,
        "duration_seconds": None,
        "risk_level": None,
        "affected_objects": [
            {
                "object_type": "country",
                "object_id": "IR",
                "role": "affected",
                "source_field": "detail_url.problem",
            }
        ],
        "collection_quality": [],
        "phase_coverage": {
            "before": _phase(),
            "during": _phase(),
            "after": _phase(),
        },
        "fact_link_status": "matched",
        "field_quality": [
            {
                "field": "detector_version",
                "status": "not_retained",
                "missing_reason": "legacy_field_not_retained",
            }
        ],
        "collision_group_id": None,
        "quarantine_id": None,
        "detector_version": None,
        "classification": "observation_only",
        "causal_conclusion": None,
    }


def _utc(value):
    return value.isoformat().replace("+00:00", "Z")


def _route_event(ordinal, prefix, action, event_time):
    return build_research_route_event(
        artifact_id=artifact_id_v1(FILE_HASH),
        file_sha256=FILE_HASH,
        collector_id="rrc25",
        artifact_slot_utc=_utc(event_time.replace(minute=event_time.minute // 5 * 5)),
        record_ordinal=ordinal,
        element_ordinal=0,
        element=ParsedRouteElement(
            event_time_utc=_utc(event_time),
            peer_ip="192.0.2.1",
            peer_asn=64500,
            action=action,
            prefix=prefix,
            afi_safi="ipv4_unicast",
            as_path=(AsPathSegment("as_sequence", (64500, ASN)),)
            if action != "withdraw"
            else None,
            quality_flags=(),
        ),
    )


def _entry(event):
    assert event.as_path is not None
    return RouteStateEntry(
        key=event.key,
        peer_ip=event.peer_ip,
        peer_asn=event.peer_asn,
        as_path=event.as_path,
        quality_flags=event.quality_flags,
        last_action=event.action,
        last_event_time_utc=event.event_time_utc,
        last_raw_ref=event.raw_ref,
    )


def _snapshot(index, entries):
    start = START + timedelta(minutes=5 * index)
    end = start + timedelta(minutes=5)
    ordered = tuple(sorted(entries, key=lambda item: item.key))
    return ReplaySnapshot(
        slot_start_utc=_utc(start),
        slot_end_exclusive_utc=_utc(end),
        boundary="[start,end)",
        continuity_state=CONTINUOUS,
        missing_reasons=(),
        route_count=len(ordered),
        entries=ordered,
        slot_changes=(),
    )


def _assessments(claim_inventory):
    observation_types = {
        "report_event_time",
        "ipv4_decline",
        "recovery_state",
        "report_affected_asn_ratio",
        "report_visibility_class_counts",
        "database_affected_asn_ratio",
    }
    return {
        claim["claim_key"]: (
            {"comparison_outcome": "different"}
            if claim["claim_type"] in observation_types
            else {
                "comparison_outcome": "not_computable",
                "unknown_rating": "hypothesis_only",
            }
        )
        for claim in claim_inventory["claims"]
    }


class DerivedAssemblyFlowTests(unittest.TestCase):
    def test_coordinate_only_projection_is_explicitly_unverified(self):
        event = _route_event(91, "10.0.9.0/24", "announce", START)
        routes, raw_refs, artifacts = _quality_evidence_projection(
            {event.route_event_id: event},
            (event.route_event_id,),
        )

        self.assertEqual(routes[0]["raw_closure_state"], "derived_coordinate_only")
        self.assertEqual(raw_refs[0]["verification_status"], "derived_coordinate_only")
        self.assertEqual(raw_refs[0]["raw_closure_state"], "unverified")
        self.assertIsNone(raw_refs[0]["record_hash"])
        self.assertIn("正式 raw audit", raw_refs[0]["missing_reason_zh"])
        self.assertEqual(len(artifacts), 1)

    def _assemble_without_episode(self, incident):
        profile = json.loads(
            (ROOT / "config/research/iran-rrc25-202602.json").read_text("utf-8")
        )
        claims = json.loads(
            (ROOT / "config/research/iran-rrc25-report-claims.json").read_text(
                "utf-8"
            )
        )
        first = _route_event(
            1, "10.0.0.0/24", "announce", START - timedelta(minutes=1)
        )
        full_entries = (_entry(first),)
        seed = RouteReplayState(
            entries=full_entries,
            latest_changes=(),
            continuity_state=CONTINUOUS,
            missing_reasons=(),
            processed_route_event_ids=frozenset((first.route_event_id,)),
            last_order_key=None,
        )
        # 仅两个连续槽，故冻结六小时基线必然不足，不形成 Episode。
        snapshots = (_snapshot(0, full_entries), _snapshot(1, full_entries))
        source = SampleSourceRef(
            ref_type="input_artifact",
            ref_id=first.artifact_id,
            sha256=FILE_HASH,
        )
        slot_metadata = tuple(
            SlotResearchMetadata(
                snapshot_id=snapshot_id_v1(snapshot),
                announce_count=observed_slot_count(0),
                withdraw_count=observed_slot_count(0),
                vp_expected_count=observed_slot_count(1),
                vp_observed_count=observed_slot_count(1),
                source_refs=(source,),
                route_event_ids=(),
            )
            for snapshot in snapshots
        )
        mapping = build_country_mapping_view(
            (MappingAssignment(ASN, ("IR",), MAPPED),),
            view="compatible",
            target_country="IR",
            source_sha256="c" * 64,
            source_ref="synthetic-ir-mapping",
        )
        quality_facts = tuple(
            DiagnosticFact(
                gate_id=gate_id,
                code=f"synthetic.gate{index:02d}",
                passed=True,
                details_zh="合成零 Episode 样本已显式完成该项检查。",
            )
            for index, gate_id in enumerate(GATE_ORDER, start=1)
        )
        return assemble_derived_research(
            profile=profile,
            run_id=RUN_ID,
            execution_mode="bounded_pilot",
            baseline_snapshot=seed,
            snapshots=snapshots,
            mapping=mapping,
            slot_metadata=slot_metadata,
            incidents=(incident,),
            claim_inventory=claims,
            reconciliation_assessments="auto",
            quality_facts=quality_facts,
            execution={
                "database_write_operations": 0,
                "new_raw_bytes_read": 0,
                "peak_temporary_bytes": 0,
                "max_worker_seconds": 0.1,
            },
            semantic_fingerprints=("f" * 64, "f" * 64),
            input_selection={
                "selected_unique_artifact_count": 1,
                "selected_unique_size_bytes": 0,
                "coverage": {
                    "analysis_updates": {
                        "expected_count": 1928,
                        "observed_count": 2,
                        "missing_count": 1926,
                    },
                    "analysis_ribs": {
                        "expected_count": 21,
                        "observed_count": 1,
                        "missing_count": 20,
                    },
                },
            },
            reproduction_commands=(
                "python3 -m unittest dev.tests.test_rrc25_research_derived_assembly",
            ),
            limitations_zh=("本次只验证零 Episode 的事件模型闭环。",),
        )

    def test_small_synthetic_fixture_flows_through_all_derived_layers(self):
        profile = json.loads(
            (ROOT / "config/research/iran-rrc25-202602.json").read_text("utf-8")
        )
        claims = json.loads(
            (ROOT / "config/research/iran-rrc25-report-claims.json").read_text(
                "utf-8"
            )
        )
        first = _route_event(
            1, "10.0.0.0/24", "announce", START - timedelta(minutes=1)
        )
        second = _route_event(
            2, "10.0.1.0/24", "announce", START - timedelta(minutes=1)
        )
        withdraw = _route_event(
            42, "10.0.1.0/24", "withdraw", START + timedelta(hours=6)
        )
        full_entries = (_entry(first), _entry(second))
        degraded_entries = (_entry(first),)
        seed = RouteReplayState(
            entries=tuple(sorted(full_entries, key=lambda item: item.key)),
            latest_changes=(),
            continuity_state=CONTINUOUS,
            missing_reasons=(),
            processed_route_event_ids=frozenset(
                item.last_raw_ref.route_event_id for item in full_entries
            ),
            last_order_key=None,
        )
        # 72 个正常槽形成冻结六小时基线，随后两个槽触发一个持续 Episode。
        snapshots = tuple(
            _snapshot(index, full_entries if index < 72 else degraded_entries)
            for index in range(74)
        )
        source = SampleSourceRef(
            ref_type="input_artifact",
            ref_id=withdraw.artifact_id,
            sha256=FILE_HASH,
        )
        slot_metadata = tuple(
            SlotResearchMetadata(
                snapshot_id=snapshot_id_v1(snapshot),
                announce_count=observed_slot_count(0),
                withdraw_count=observed_slot_count(1 if index >= 72 else 0),
                vp_expected_count=observed_slot_count(1),
                vp_observed_count=observed_slot_count(1),
                source_refs=(source,),
                route_event_ids=(withdraw.route_event_id,) if index >= 72 else (),
            )
            for index, snapshot in enumerate(snapshots)
        )
        mapping = build_country_mapping_view(
            (MappingAssignment(ASN, ("IR",), MAPPED),),
            view="compatible",
            target_country="IR",
            source_sha256="c" * 64,
            source_ref="synthetic-ir-mapping",
        )
        quality_facts = tuple(
            DiagnosticFact(
                gate_id=gate_id,
                code=f"synthetic.gate{index:02d}",
                passed=True,
                details_zh="合成贯通样本已显式完成该项检查。",
            )
            for index, gate_id in enumerate(GATE_ORDER, start=1)
        )
        assembly_arguments = dict(
            profile=profile,
            run_id=RUN_ID,
            execution_mode="bounded_pilot",
            baseline_snapshot=seed,
            snapshots=snapshots,
            mapping=mapping,
            slot_metadata=slot_metadata,
            incidents=(_incident(),),
            claim_inventory=claims,
            reconciliation_assessments="auto",
            quality_facts=quality_facts,
            execution={
                "database_write_operations": 0,
                "new_raw_bytes_read": 1024,
                "peak_temporary_bytes": 2048,
                "max_worker_seconds": 1.0,
            },
            semantic_fingerprints=("f" * 64, "f" * 64),
            input_selection={
                "selected_unique_artifact_count": 1,
                "selected_unique_size_bytes": 1024,
                "coverage": {
                    "analysis_updates": {
                        "expected_count": 1928,
                        "observed_count": 74,
                        "missing_count": 1854,
                    },
                    "analysis_ribs": {
                        "expected_count": 21,
                        "observed_count": 1,
                        "missing_count": 20,
                    },
                },
            },
            reproduction_commands=("python3 -m unittest dev.tests.test_rrc25_research_derived_assembly",),
            route_events_by_id={withdraw.route_event_id: withdraw},
            limitations_zh=("本次仅用小型合成状态验证研究闭环。",),
        )
        with self.assertRaisesRegex(
            DerivedAssemblyError, "必须显式提供逐 Episode incident mapping"
        ):
            assemble_derived_research(**assembly_arguments)

        unmapped_probe = assemble_derived_research(
            **{
                **assembly_arguments,
                "incidents": (),
            }
        )
        probe_episode = unmapped_probe.episodes[0]
        assembly_arguments["incident_mappings_by_episode_id"] = {
            probe_episode["episode_id"]: (
                {
                    "incident_ref": _incident()["detail_reference"],
                    "relation": "possible_correspondence",
                    "causal": False,
                    "evidence_sample_ids": [
                        probe_episode["supporting_sample_ids"][0]
                    ],
                },
            )
        }
        result = assemble_derived_research(**assembly_arguments)

        self.assertTrue(result.baseline.resolved)
        self.assertEqual(len(result.samples), 74)
        self.assertEqual(len(result.episodes), 1)
        self.assertEqual(len(result.waves), 1)
        self.assertEqual(len(result.episode_as_records), 1)
        self.assertEqual(len(result.incident_episode_mappings), 1)
        self.assertEqual(
            result.incident_episode_mappings[0]["mapping_state"],
            "single_research_episode",
        )
        self.assertEqual(
            result.incident_episode_mappings[0]["episode_links"][0]["episode_id"],
            result.episodes[0]["episode_id"],
        )
        self.assertEqual(result.primary_episode_id, result.episodes[0]["episode_id"])
        self.assertEqual(result.quality["run_state"], "incomplete")
        self.assertEqual(result.quality["acceptance_state"], "not_accepted")
        self.assertEqual(result.package_manifest["acceptance_state"], "not_accepted")
        self.assertIn("不得外推为完整事件人口或生产验收结果", result.report_zh)
        self.assertEqual(len(result.reconciliation["claims"]), 11)
        self.assertEqual(result.episode_as_records[0]["evidence_links"], [])
        reference_gate = next(
            gate
            for gate in result.quality["gates"]
            if gate["gate_id"] == "reference_closure"
        )
        self.assertIn(
            "prefix_change.asn_unattributed",
            {diagnostic["code"] for diagnostic in reference_gate["diagnostics"]},
        )
        self.assertEqual(reference_gate["status"], "fail")

        second_incident = _incident(2)
        with self.assertRaisesRegex(
            DerivedAssemblyError, "精确覆盖全部 Incident"
        ):
            assemble_derived_research(
                **{
                    **assembly_arguments,
                    "incidents": (_incident(), second_incident),
                }
            )
        selective = assemble_derived_research(
            **{
                **assembly_arguments,
                "incidents": (_incident(), second_incident),
                "incident_mappings_by_episode_id": {
                    result.episodes[0]["episode_id"]: (
                        {
                            "incident_ref": _incident()["detail_reference"],
                            "relation": "possible_correspondence",
                            "causal": False,
                            "evidence_sample_ids": [
                                result.episodes[0]["supporting_sample_ids"][0]
                            ],
                        },
                        {
                            "incident_ref": second_incident["detail_reference"],
                            "relation": "no_correspondence",
                            "causal": False,
                            "evidence_sample_ids": [],
                        },
                    )
                },
            }
        )
        mapping_by_incident = {
            row["incident_id"]: row for row in selective.incident_episode_mappings
        }
        self.assertEqual(
            mapping_by_incident[_incident()["incident_id"]]["mapping_state"],
            "single_research_episode",
        )
        self.assertEqual(
            mapping_by_incident[second_incident["incident_id"]]["mapping_state"],
            "no_research_episode",
        )
        assert result.detection is not None
        detected_episode = result.detection.episodes[0]
        with self.assertRaisesRegex(
            DerivedAssemblyError, "唯一 resolved origin|ASN 级 raw proof"
        ):
            _merge_prefix_change_event_ids(
                {},
                {(ASN, "ipv4", "10.0.1.0/24"): (withdraw.route_event_id,)},
                episode=detected_episode,
                cohort=result.cohort,
                route_events_by_id={withdraw.route_event_id: withdraw},
            )

        announce = _route_event(
            43, "10.0.1.0/24", "announce", START + timedelta(hours=6, minutes=1)
        )
        conflict = build_research_route_event(
            artifact_id=artifact_id_v1(FILE_HASH),
            file_sha256=FILE_HASH,
            collector_id="rrc25",
            artifact_slot_utc=_utc(START + timedelta(hours=6)),
            record_ordinal=44,
            element_ordinal=0,
            element=ParsedRouteElement(
                event_time_utc=_utc(START + timedelta(hours=6, minutes=2)),
                peer_ip="192.0.2.1",
                peer_asn=64500,
                action="announce",
                prefix="10.0.1.0/24",
                afi_safi="ipv4_unicast",
                as_path=(AsPathSegment("as_set", (ASN, 65002)),),
                quality_flags=(),
            ),
        )
        automatic, unattributed = _automatic_prefix_change_event_ids(
            episode=detected_episode,
            cohort=result.cohort,
            route_events_by_id={
                announce.route_event_id: announce,
                conflict.route_event_id: conflict,
                withdraw.route_event_id: withdraw,
            },
        )
        self.assertEqual(
            automatic[(ASN, "ipv4", "10.0.1.0/24")],
            (announce.route_event_id,),
        )
        self.assertEqual(unattributed["withdraw_origin_unavailable"], 1)
        self.assertEqual(unattributed["origin_conflict"], 1)
        mapping_payload = json.loads(
            result.package_contents[
                "data/incident-episode-mappings.json"
            ].decode("utf-8")
        )
        self.assertEqual(mapping_payload, list(result.incident_episode_mappings))
        for content in result.package_manifest["contents"]:
            payload = result.package_contents[content["path"]]
            self.assertEqual(content["size_bytes"], len(payload))
            self.assertEqual(
                content["sha256"],
                __import__("hashlib").sha256(payload).hexdigest(),
            )

    def test_zero_episode_keeps_incident_mapping(self):
        result = self._assemble_without_episode(_incident())

        self.assertFalse(result.baseline.resolved)
        self.assertEqual(result.episodes, ())
        self.assertEqual(len(result.incident_episode_mappings), 1)
        mapping = result.incident_episode_mappings[0]
        self.assertEqual(mapping["mapping_state"], "no_research_episode")
        self.assertEqual(mapping["episode_links"], [])
        self.assertFalse(mapping["causal"])
        self.assertIn("未伪造事件边界", mapping["missing_reason_zh"])

    def test_unresolved_incident_without_episode_keeps_mapping(self):
        incident = _incident()
        incident["fact_link_status"] = "unresolved"
        result = self._assemble_without_episode(incident)

        self.assertEqual(result.episodes, ())
        self.assertEqual(
            result.incident_episode_mappings[0]["mapping_state"],
            "no_research_episode",
        )
    def test_mapping_validator_rejects_relation_and_sample_outside_episode(self):
        sample_id = "sample_v1_" + "1" * 24
        episode_id = "episode_v1_" + "2" * 24
        episode = {
            "episode_id": episode_id,
            "run_id": RUN_ID,
            "supporting_sample_ids": [sample_id],
            "incident_mappings": [
                {
                    "incident_ref": "country_outage/2026-02-27 09:12:32/IR/1/r",
                    "relation": "possible_correspondence",
                    "causal": False,
                    "evidence_sample_ids": [sample_id],
                }
            ],
        }

        def record(relation="possible_correspondence", samples=None):
            semantic = {
                "schema_version": "incident-episode-mapping/v1",
                "run_id": RUN_ID,
                "incident_id": "inc_v1_" + "3" * 24,
                "incident_ref": "country_outage/2026-02-27 09:12:32/IR/1/r",
                "source_fact_state": "matched",
                "mapping_state": "single_research_episode",
                "causal": False,
                "episode_links": [
                    {
                        "episode_id": episode_id,
                        "relation": relation,
                        "causal": False,
                        "evidence_sample_ids": samples or [sample_id],
                    }
                ],
                "missing_reason_zh": None,
            }
            digest = __import__("hashlib").sha256(
                json.dumps(
                    semantic,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24]
            return {**semantic, "mapping_id": "incident_episode_map_v1_" + digest}

        with self.assertRaisesRegex(DerivedAssemblyError, "relation"):
            validate_incident_episode_mapping_record(
                record(relation="causal_precursor"), episodes=(episode,)
            )
        with self.assertRaisesRegex(DerivedAssemblyError, "不属于目标 Episode"):
            validate_incident_episode_mapping_record(
                record(samples=["sample_v1_" + "f" * 24]), episodes=(episode,)
            )

        validate_incident_episode_mapping_record(record(), episodes=(episode,))
        wrong_run = {**episode, "run_id": "research_run_v1_" + "f" * 24}
        with self.assertRaisesRegex(DerivedAssemblyError, "run_id"):
            validate_incident_episode_mapping_record(record(), episodes=(wrong_run,))

        wrong_incident = {
            **episode,
            "incident_mappings": [
                {
                    **episode["incident_mappings"][0],
                    "incident_ref": "country_outage/OTHER",
                }
            ],
        }
        with self.assertRaisesRegex(DerivedAssemblyError, "反向内容不一致"):
            validate_incident_episode_mapping_record(
                record(), episodes=(wrong_incident,)
            )

        wrong_relation = {
            **episode,
            "incident_mappings": [
                {
                    **episode["incident_mappings"][0],
                    "relation": "legacy_reconciliation",
                }
            ],
        }
        with self.assertRaisesRegex(DerivedAssemblyError, "反向内容不一致"):
            validate_incident_episode_mapping_record(
                record(), episodes=(wrong_relation,)
            )


if __name__ == "__main__":
    unittest.main()
