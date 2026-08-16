from __future__ import annotations

import gzip
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.data_pipeline.route_event import (
    AsPathSegment,
    artifact_id_v1,
    route_event_id_v1,
    vp_id_v1,
)
from backend.data_pipeline.research.resource_gate import ResourceLimits
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    CONTINUOUS,
    RawRecordRef,
    ReplaySnapshot,
    ResearchRouteEvent,
    RouteLastChange,
    RouteReplayState,
    RouteStateKey,
)
from backend.data_pipeline.research.rrc25_country_outage.bounded_pilot_worker import (
    AmbiguityPopulation,
    BoundedPilotWorkerResult,
    SlotCount as WorkerSlotCount,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    MappingAssignment,
    build_country_mapping_view,
)
from backend.data_pipeline.research.rrc25_country_outage.mapped_compatible_projection import (
    build_mapped_compatible_projection,
)
from backend.data_pipeline.research.rrc25_country_outage.update_adapter import (
    RawRecordEvidence,
)
from dev.data_quality.rrc25_iran_bounded_pilot import (
    SparsePilotError,
    _assemble_once,
    _assert_research_write_targets,
    _build_slot_metadata,
    _evidence_rows,
    _execution_update_allowlist,
    _gzip_jsonl,
    _incident_source_fact,
    _load_code_identity,
    _preflight_output_directories,
    _publish_ab_with_runtime_gate,
    _require_cumulative_runtime_budget,
    _validated_parser_runtime_statistics,
    build_code_identity,
    build_parser,
)


ROOT = Path(__file__).resolve().parents[2]


class IranBoundedPilotCliTests(unittest.TestCase):
    def test_cumulative_runtime_gate_keeps_soft_and_hard_limits_exclusive(self):
        limits = ResourceLimits()
        allowed = _require_cumulative_runtime_budget(
            stage="before_projection",
            process_started_at=0.0,
            clock=lambda: 539.999,
            limits=limits,
        )
        self.assertEqual(allowed["decision"], "allowed")
        with self.assertRaisesRegex(SparsePilotError, "decision=soft_stop"):
            _require_cumulative_runtime_budget(
                stage="before_assembly",
                process_started_at=0.0,
                clock=lambda: 540.0,
                limits=limits,
            )
        with self.assertRaisesRegex(SparsePilotError, "decision=approval_required"):
            _require_cumulative_runtime_budget(
                stage="before_publish",
                process_started_at=0.0,
                clock=lambda: 600.0,
                limits=limits,
            )

    def test_hard_runtime_limit_rejects_ab_publish_before_any_write(self):
        with patch(
            "dev.data_quality.rrc25_iran_bounded_pilot.publish_research_package"
        ) as publisher, self.assertRaisesRegex(
            SparsePilotError, "stage=before_publish_a.*approval_required"
        ):
            _publish_ab_with_runtime_gate(
                output_a="/tmp/research-a",
                contents_a={},
                manifest_a={},
                output_b="/tmp/research-b",
                contents_b={},
                manifest_b={},
                process_started_at=0.0,
                clock=lambda: 600.0,
                limits=ResourceLimits(),
                new_raw_read_bytes=0,
                peak_temporary_bytes=0,
            )
        publisher.assert_not_called()

    def test_incident_locator_reuses_normalizer_identity_without_claiming_fact_match(self):
        incident = _incident_source_fact(
            "country_outage/2026-02-27 09:12:32/IR/1/r"
        )

        self.assertEqual(
            incident["incident_id"],
            "inc_v1_ab52ddcad8926f8882fed33a",
        )
        self.assertEqual(incident["source_table"], "country_outage_202602")
        self.assertEqual(
            incident["source_primary_key"],
            {"source": "r", "country": "IR", "outage_id": 1},
        )
        self.assertEqual(incident["event_time_utc"], "2026-02-27T01:12:32Z")
        self.assertEqual(incident["fact_link_status"], "unresolved")
        self.assertIn(
            "legacy_fact_snapshot_not_supplied", incident["collection_quality"]
        )

    def test_incident_locator_rejects_noncanonical_detail_reference(self):
        with self.assertRaisesRegex(SparsePilotError, "五段式 detail URL"):
            _incident_source_fact(
                "country_outage/2026-02-27T09:12:32/IR/1/r"
            )

    def test_all_pilot_write_targets_reuse_coordinator_protected_path_gate(self):
        cases = (
            ("checkpoint_directory", Path("/home/bgpdata/Domeye/research/checkpoint")),
            ("output_a", Path("/home/bgpdata/Domeye-Core/research/a")),
            ("output_b", Path("/var/www/domeye/research/b")),
        )
        for label, path in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                SparsePilotError,
                rf"{label}:(?:protected|production)_write_target",
            ):
                _assert_research_write_targets({label: path})

        # coordinator 的词法目录边界允许独立的开发数据根，不把相邻前缀
        # 误判成生产目录。
        _assert_research_write_targets(
            {"output_a": Path("/home/bgpdata/Domeye-Core-dev-data/research/a")}
        )

    def test_preflight_applies_write_gate_before_any_output_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protected = root / "protected"
            checkpoint = protected / "checkpoint"
            coordinator = root / "coordinator"
            output_a = root / "output-a"
            output_b = root / "output-b"
            raw = root / "raw"
            for path in (
                protected,
                checkpoint,
                coordinator,
                output_a,
                output_b,
                raw,
            ):
                path.mkdir()
            inputs = []
            for name in ("seed.json", "code.json", "worker.json"):
                path = root / name
                path.write_text("{}", encoding="utf-8")
                inputs.append(path)
            args = SimpleNamespace(
                checkpoint_directory=checkpoint,
                coordinator_output_root=coordinator,
                output_a=output_a,
                output_b=output_b,
                raw_root=raw,
                seed_sample_checkpoint=inputs[0],
                seed_producer_code_identity=inputs[1],
                seed_producer_worker_plan=inputs[2],
            )

            with patch(
                "dev.data_quality.rrc25_iran_bounded_pilot.DEFAULT_PROTECTED_ROOTS",
                (str(protected),),
            ), self.assertRaisesRegex(
                SparsePilotError,
                "checkpoint_directory:protected_write_target",
            ):
                _preflight_output_directories(args)

            self.assertEqual(tuple(checkpoint.iterdir()), ())
            self.assertEqual(tuple(output_a.iterdir()), ())
            self.assertEqual(tuple(output_b.iterdir()), ())

    def test_research_cli_uses_explicit_large_but_bounded_stdout_queue(self):
        parser = build_parser()
        subparser_action = next(
            action for action in parser._actions if action.dest == "command"
        )
        run_parser = subparser_action.choices["run"]
        defaults = {action.dest: action.default for action in run_parser._actions}

        self.assertEqual(defaults["bgpdump_queue_capacity"], 4096)
        self.assertEqual(
            defaults["bgpdump_queue_source_bytes"], 8 * 1024 * 1024
        )

    def test_execution_allowlist_is_explicit_subset_of_sparse_selection(self):
        updates = [
            {
                "artifact_id": "art_v1_" + character * 32,
                "artifact_time_utc": slot,
            }
            for character, slot in (
                ("a", "2026-02-27T22:00:00Z"),
                ("b", "2026-02-28T08:10:00Z"),
            )
        ]
        selection = {
            "selection_id": "rsel_v1_" + "c" * 32,
            "semantic_fingerprint_sha256": "d" * 64,
            "roles": {"analysis_updates": updates},
        }
        allowlist = _execution_update_allowlist(
            selection, ("2026-02-28T08:10:00Z",)
        )
        self.assertEqual(allowlist["artifact_ids"], [updates[1]["artifact_id"]])
        self.assertEqual(allowlist["slots"], ["2026-02-28T08:10:00Z"])
        with self.assertRaisesRegex(ValueError, "不在已验证 selection"):
            _execution_update_allowlist(
                selection, ("2026-02-28T08:15:00Z",)
            )

    def test_parser_runtime_statistics_closes_control_population(self):
        artifact = {
            "artifact_id": "art_v1_" + "a" * 32,
            "file_sha256": "b" * 64,
            "size_bytes": 123,
        }

        class Factory:
            statistics_by_artifact = {
                artifact["artifact_id"]: {
                    "status": "complete",
                    "compressed_file_sha256": artifact["file_sha256"],
                    "compressed_size_bytes": artifact["size_bytes"],
                    "compressed_read_passes": 1,
                    "physical_record_count": 15,
                    "route_record_count": 8,
                    "state_change_record_count": 2,
                    "open_record_count": 1,
                    "notification_record_count": 1,
                    "keepalive_record_count": 3,
                    "route_element_count": 10,
                    "announce_count": 7,
                    "withdraw_count": 3,
                }
            }

        rows = _validated_parser_runtime_statistics(Factory(), (artifact,))
        self.assertEqual(rows[artifact["artifact_id"]]["open_record_count"], 1)
        Factory.statistics_by_artifact[artifact["artifact_id"]][
            "physical_record_count"
        ] = 16
        with self.assertRaisesRegex(ValueError, "分类人口"):
            _validated_parser_runtime_statistics(Factory(), (artifact,))

    def test_route_event_fans_out_verified_raw_element_reference(self):
        file_hash = "a" * 64
        artifact_id = artifact_id_v1(file_hash)
        route_id = route_event_id_v1(file_hash, 7, 2)
        vp_id = vp_id_v1("rrc25", "192.0.2.1", 64500)
        event = ResearchRouteEvent(
            artifact_id=artifact_id,
            file_sha256=file_hash,
            collector_id="rrc25",
            artifact_slot_utc="2026-02-28T08:10:00Z",
            record_ordinal=7,
            element_ordinal=2,
            route_event_id=route_id,
            event_time_utc="2026-02-28T08:14:00Z",
            peer_ip="192.0.2.1",
            peer_asn=64500,
            vp_id=vp_id,
            action="withdraw",
            afi_safi="ipv4_unicast",
            prefix="203.0.113.0/24",
            as_path=None,
            quality_flags=(),
        )
        audit = RawRecordEvidence(
            artifact_id=artifact_id,
            file_sha256=file_hash,
            collector_id="rrc25",
            artifact_slot_utc="2026-02-28T08:10:00Z",
            record_ordinal=7,
            record_offset=4096,
            record_length=128,
            raw_record_sha256="b" * 64,
            event_time_utc="2026-02-28T08:14:00Z",
            event_epoch_microseconds=0,
            mrt_type=16,
            mrt_subtype=4,
        )

        routes, raw, raw_only = _evidence_rows((event,), (audit,))

        self.assertEqual(len(routes), 1)
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw_only, [])
        self.assertEqual(routes[0]["route_event_id"], route_id)
        self.assertEqual(routes[0]["raw_record_ref_id"], raw[0]["raw_record_ref_id"])
        self.assertEqual(raw[0]["record_offset"], 4096)
        self.assertEqual(raw[0]["record_length"], 128)
        self.assertEqual(raw[0]["record_hash"], "b" * 64)
        self.assertEqual(raw[0]["element_ordinal"], 2)
        self.assertEqual(raw[0]["vp_id"], vp_id)

    def test_gzip_jsonl_is_deterministic_and_has_zero_mtime(self):
        first = _gzip_jsonl(({"b": 2, "a": 1}, {"z": "伊朗"}))
        second = _gzip_jsonl(({"b": 2, "a": 1}, {"z": "伊朗"}))
        self.assertEqual(first, second)
        self.assertEqual(first[4:8], b"\x00\x00\x00\x00")
        lines = gzip.decompress(first).decode("utf-8").splitlines()
        self.assertEqual(json.loads(lines[0]), {"a": 1, "b": 2})

    def test_code_identity_recomputes_and_checks_current_files(self):
        identity = build_code_identity()
        self.assertGreater(len(identity["files"]), 10)
        included = {row["path"] for row in identity["files"]}
        self.assertTrue(
            {
                "backend/data_pipeline/normalize/__init__.py",
                "backend/data_pipeline/normalize/facts.py",
                "dev/data_quality/rrc25_iran_bounded_pilot.py",
                "dev/data_quality/rrc25_iran_execution_prep.py",
                "dev/data_quality/rrc25_iran_full_window.py",
                "dev/data_quality/rrc25_iran_finalize.py",
            }
            <= included
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "code-identity.json"
            path.write_text(
                json.dumps(identity, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            loaded = _load_code_identity(path, identity["identity_sha256"])
        self.assertEqual(loaded, identity)

    def test_code_identity_closes_normalize_dependencies_deterministically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            required = (
                "backend/data_pipeline/__init__.py",
                "backend/data_pipeline/normalize/__init__.py",
                "backend/data_pipeline/normalize/facts.py",
                "dev/data_quality/rrc25_iran_research.py",
                "dev/data_quality/rrc25_iran_bounded_pilot.py",
                "dev/data_quality/rrc25_iran_execution_prep.py",
                "dev/data_quality/rrc25_iran_full_window.py",
                "dev/data_quality/rrc25_iran_finalize.py",
                "dev/data_quality/rrc25_iran_analysis_ribs.py",
                "dev/data_quality/rrc25_iran_acceptance.py",
                "dev/data_quality/validate_research_contracts.cjs",
                "dev/data_quality/validate_rrc25_full_window_package_contracts.cjs",
            )
            for relative in required:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative + "\n", encoding="utf-8")
            unrelated = root / "backend/data_pipeline/normalize/unrelated.py"
            unrelated.write_text("not imported by the pilot\n", encoding="utf-8")
            cache = root / "backend/data_pipeline/normalize/__pycache__/facts.pyc"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"cache")

            baseline = build_code_identity(root)
            repeated = build_code_identity(root)
            paths = [row["path"] for row in baseline["files"]]

            self.assertEqual(baseline, repeated)
            self.assertEqual(paths, sorted(paths))
            self.assertEqual(set(paths), set(required))
            self.assertNotIn(unrelated.relative_to(root).as_posix(), paths)
            self.assertFalse(any("__pycache__" in path for path in paths))

            for relative in (
                "backend/data_pipeline/normalize/__init__.py",
                "backend/data_pipeline/normalize/facts.py",
            ):
                with self.subTest(relative=relative):
                    dependency = root / relative
                    original = dependency.read_bytes()
                    dependency.write_bytes(original + b"# identity change\n")
                    changed = build_code_identity(root)
                    self.assertNotEqual(
                        changed["identity_sha256"], baseline["identity_sha256"]
                    )
                    dependency.write_bytes(original)
                    self.assertEqual(build_code_identity(root), baseline)

    def test_missing_raw_audit_fails_closed(self):
        file_hash = "c" * 64
        event = ResearchRouteEvent(
            artifact_id=artifact_id_v1(file_hash),
            file_sha256=file_hash,
            collector_id="rrc25",
            artifact_slot_utc="2026-02-28T08:10:00Z",
            record_ordinal=1,
            element_ordinal=0,
            route_event_id=route_event_id_v1(file_hash, 1, 0),
            event_time_utc="2026-02-28T08:10:01Z",
            peer_ip="192.0.2.1",
            peer_asn=64500,
            vp_id=vp_id_v1("rrc25", "192.0.2.1", 64500),
            action="announce",
            afi_safi="ipv4_unicast",
            prefix="203.0.113.0/24",
            as_path=(AsPathSegment("as_sequence", (64500, 65001)),),
            quality_flags=(),
        )
        with self.assertRaisesRegex(ValueError, "raw audit"):
            _evidence_rows((event,), ())

    def test_withdraw_link_survives_mapped_impact_projection(self):
        file_hash = "d" * 64
        route_id = route_event_id_v1(file_hash, 9, 0)
        key = RouteStateKey(
            collector_id="rrc25",
            vp_id=vp_id_v1("rrc25", "192.0.2.9", 64509),
            afi_safi="ipv4_unicast",
            prefix="203.0.113.0/24",
        )
        raw_ref = RawRecordRef(
            artifact_id=artifact_id_v1(file_hash),
            file_sha256=file_hash,
            collector_id="rrc25",
            artifact_slot_utc="2026-02-28T08:10:00Z",
            record_ordinal=9,
            element_ordinal=0,
            route_event_id=route_id,
        )
        snapshot = ReplaySnapshot(
            slot_start_utc="2026-02-28T08:10:00Z",
            slot_end_exclusive_utc="2026-02-28T08:15:00Z",
            boundary="[start,end)",
            continuity_state=CONTINUOUS,
            missing_reasons=(),
            route_count=0,
            entries=(),
            slot_changes=(
                RouteLastChange(
                    key=key,
                    action="withdraw",
                    event_time_utc="2026-02-28T08:14:00Z",
                    as_path=None,
                    quality_flags=(),
                    raw_ref=raw_ref,
                ),
            ),
        )
        mapping = build_country_mapping_view(
            (MappingAssignment(65001, ("IR",), "mapped"),),
            view="compatible",
            target_country="IR",
            source_sha256="e" * 64,
            source_ref="test-mapping",
        )
        projection = build_mapped_compatible_projection(snapshot, mapping)
        self.assertEqual(projection.projected.slot_changes, ())
        worker = BoundedPilotWorkerResult(
            schema_version="rrc25-bounded-pilot-worker-result/v1",
            selection_id="rsel_v1_" + "a" * 32,
            pilot_start_utc="2026-02-28T08:10:00Z",
            pilot_end_exclusive_utc="2026-02-28T08:15:00Z",
            status="complete",
            incomplete_reason=None,
            state=RouteReplayState((), (), CONTINUOUS, (), frozenset(), None),
            seed_state_at_window_start=None,
            snapshots=(snapshot,),
            route_events=(),
            raw_audits=(),
            slot_counts=(
                WorkerSlotCount(
                    slot_start_utc="2026-02-28T08:10:00Z",
                    slot_end_exclusive_utc="2026-02-28T08:15:00Z",
                    input_state="observed",
                    announce_count=0,
                    withdraw_count=1,
                    retained_announce_count=0,
                    retained_withdraw_count=1,
                    physical_record_count=1,
                    missing_reasons=(),
                ),
            ),
            observed_vp_ids=(),
            tracked_prefixes=(),
            pre_discovery_context_unknown=(),
            ambiguity=AmbiguityPopulation(0, (), (), (), "measurable", "measurable", ()),
            gaps=(),
            errors=(),
            resources={},
            checkpoint_path=None,
        )
        metadata = _build_slot_metadata(worker, (projection,))
        self.assertEqual(metadata[0].route_event_ids, (route_id,))


if __name__ == "__main__":
    unittest.main()
