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
    SlotResearchMetadata,
    assemble_derived_research,
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
from dev.tests.test_rrc25_research_evidence import _bundle_parameters, _incident


ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 2, 27, 16, 0, tzinfo=timezone.utc)
RUN_ID = "research_run_v1_" + "9" * 24
FILE_HASH = "a" * 64
ASN = 65001


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
        evidence_parameters = _bundle_parameters()
        evidence_parameters["route_event_refs"][0]["observed_at"] = withdraw.event_time_utc
        quality_facts = tuple(
            DiagnosticFact(
                gate_id=gate_id,
                code=f"synthetic.gate{index:02d}",
                passed=True,
                details_zh="合成贯通样本已显式完成该项检查。",
            )
            for index, gate_id in enumerate(GATE_ORDER, start=1)
        )
        result = assemble_derived_research(
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
            prefix_change_event_ids={
                (ASN, "ipv4", "10.0.1.0/24"): (withdraw.route_event_id,)
            },
            evidence_bundle_parameters=evidence_parameters,
            confirmed_onset_at="2026-02-27T22:00:00Z",
            limitations_zh=("本次仅用小型合成状态验证研究闭环。",),
        )

        self.assertTrue(result.baseline.resolved)
        self.assertEqual(len(result.samples), 74)
        self.assertEqual(len(result.episodes), 1)
        self.assertEqual(len(result.waves), 1)
        self.assertEqual(len(result.episode_as_records), 1)
        self.assertEqual(len(result.evidence_packages), 1)
        self.assertEqual(result.primary_episode_id, result.episodes[0]["episode_id"])
        self.assertEqual(result.quality["run_state"], "incomplete")
        self.assertEqual(result.quality["acceptance_state"], "not_accepted")
        self.assertEqual(result.package_manifest["acceptance_state"], "not_accepted")
        self.assertIn("不得外推为完整事件人口或生产验收结果", result.report_zh)
        self.assertEqual(len(result.reconciliation["claims"]), 11)
        self.assertTrue(
            result.evidence_packages[0]["sidecar"]["route_event_refs"]
        )
        self.assertTrue(
            result.episode_as_records[0]["evidence_links"]
        )
        for content in result.package_manifest["contents"]:
            payload = result.package_contents[content["path"]]
            self.assertEqual(content["size_bytes"], len(payload))
            self.assertEqual(
                content["sha256"],
                __import__("hashlib").sha256(payload).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
