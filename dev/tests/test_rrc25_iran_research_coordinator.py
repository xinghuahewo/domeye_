from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import ast
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.data_pipeline.route_event import artifact_id_v1
from backend.data_pipeline.research import resource_gate
from backend.data_pipeline.research.rrc25_country_outage import coordinator as coordinator_module
from backend.data_pipeline.research.rrc25_country_outage.coordinator import (
    ExecutionRecord,
    ResearchCoordinatorError,
    build_worker_plan,
    execute_research,
    load_json_metadata,
    normalize_execution_mode,
    prepare_research_plan,
    resume_research,
    verify_worker_plan,
    verify_research_run,
)
from dev.data_quality.rrc25_iran_research import main as cli_main


UTC = timezone.utc
CODE_SHA = "c" * 64


def utc_text(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def small_profile() -> dict[str, object]:
    source = Path("config/research/iran-rrc25-202602.json")
    profile = json.loads(source.read_text(encoding="utf-8"))
    start = datetime(2026, 2, 27, 16, 0, tzinfo=UTC)
    end = start + timedelta(minutes=10)
    profile["window"]["start_utc"] = utc_text(start)
    profile["window"]["end_exclusive_utc"] = utc_text(end)
    profile["window"]["observation_end_utc"] = utc_text(end)
    numeric = profile["baseline"]["numeric"]
    numeric["initial_duration_seconds"] = 300
    numeric["extension_step_seconds"] = 300
    numeric["max_duration_seconds"] = 300
    numeric["exclusion_boundary"]["at_utc"] = utc_text(end)
    profile["input_selection"]["analysis_updates"]["expected_slot_count"] = 2
    profile["input_selection"]["analysis_ribs"]["expected_slot_count"] = 1
    profile["study_id"] = "iran-rrc25-coordinator-fixture-v1"
    return profile


def artifact(
    kind: str, at: str, suffix: str, *, size_bytes: int = 10
) -> dict[str, object]:
    file_hash = suffix * 64
    return {
        "artifact_id": artifact_id_v1(file_hash),
        "artifact_type": kind,
        "artifact_time_utc": at,
        "collector_id": "rrc25",
        "relative_path": f"rrc25/{kind}s.{at}.{suffix}.gz",
        "file_sha256": file_hash,
        "size_bytes": size_bytes,
        "compression": "gz",
    }


def manifest_bundle(
    *, size_override: dict[str, int] | None = None
) -> tuple[dict[str, object], dict[str, object]]:
    sizes = size_override or {}
    rows = [
        artifact("rib", "2026-02-27T08:00:00Z", "1", size_bytes=sizes.get("baseline", 10)),
        artifact("rib", "2026-02-27T16:00:00Z", "2", size_bytes=sizes.get("seed", 10)),
        artifact("update", "2026-02-27T16:00:00Z", "3", size_bytes=sizes.get("update0", 10)),
        artifact("update", "2026-02-27T16:05:00Z", "4", size_bytes=sizes.get("update1", 10)),
    ]
    fingerprint = "f" * 64
    manifest = {
        "schema_version": 1,
        "manifest_kind": "mrt_artifact_manifest",
        "manifest_fingerprint_sha256": fingerprint,
        "artifacts": rows,
    }
    verification = {
        "verified": True,
        "manifest_fingerprint_sha256": fingerprint,
        "artifact_count": len(rows),
    }
    return manifest, verification


def mapping() -> dict[str, object]:
    return {
        "snapshot_id": "asmap_v1_" + "a" * 32,
        "schema_version": "as-country-mapping-snapshot/v1",
        "semantic_fingerprint_sha256": "a" * 64,
        "rows": [{"asn": 1, "country_code": "IR"}],
    }


class MemoryExecutor:
    def __init__(
        self,
        records_by_artifact: dict[str, list[ExecutionRecord]],
    ):
        self.records_by_artifact = records_by_artifact

    def __call__(self, artifact_row, start_record_ordinal):
        for item in self.records_by_artifact.get(artifact_row["artifact_id"], []):
            if item.record_ordinal >= start_record_ordinal:
                yield item


def executor_for_plan(
    plan,
    *,
    raw_bytes: int = 1,
    temporary_bytes: int = 1,
    database_writes: int = 0,
) -> MemoryExecutor:
    rows: dict[str, list[ExecutionRecord]] = {}
    for _chunk, artifact_row in plan.flat_artifacts:
        artifact_id = artifact_row["artifact_id"]
        rows[artifact_id] = [
            ExecutionRecord(
                artifact_id=artifact_id,
                record_ordinal=0,
                output_record={
                    "artifact_id": artifact_id,
                    "record_ordinal": 0,
                    "kind": artifact_row["artifact_type"],
                },
                new_raw_bytes_read=raw_bytes,
                temporary_bytes=temporary_bytes,
                database_write_operations=database_writes,
            )
        ]
    return MemoryExecutor(rows)


class SequenceClock:
    def __init__(self, values):
        self.values = iter(values)
        self.last = 0.0

    def __call__(self):
        try:
            self.last = float(next(self.values))
        except StopIteration:
            pass
        return self.last


class MutableClock:
    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value


class CoordinatorTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.profile = small_profile()
        self.manifest, self.verification = manifest_bundle()
        self.mapping = mapping()

    def tearDown(self):
        self.temporary.cleanup()

    def plan(self, **overrides):
        values = {
            "profile": self.profile,
            "artifact_manifest": self.manifest,
            "manifest_verification": self.verification,
            "mapping_snapshot": self.mapping,
            "code_sha256": CODE_SHA,
            "output_root": self.root,
            "estimated_worker_seconds": 1,
            "estimated_temporary_bytes": 1,
        }
        values.update(overrides)
        return prepare_research_plan(**values)

    def test_dry_plan_is_metadata_only_and_chunked(self):
        for row in self.manifest["artifacts"]:
            row["relative_path"] = "rrc25/does-not-exist/" + row["relative_path"].split("/")[-1]

        plan = self.plan(maximum_artifacts_per_chunk=2)

        self.assertTrue(plan.ready)
        self.assertEqual(len(plan.chunks), 3)
        self.assertEqual(
            [
                [item["artifact_type"] for item in chunk.artifacts]
                for chunk in plan.chunks
            ],
            [["rib"], ["rib"], ["update", "update"]],
        )
        self.assertEqual(plan.to_dict()["dry_run_opens_raw_mrt"], False)
        self.assertEqual(plan.to_dict()["database_connections"], 0)

    def test_plan_requires_explicit_positive_resource_estimates(self):
        base = {
            "profile": self.profile,
            "artifact_manifest": self.manifest,
            "manifest_verification": self.verification,
            "mapping_snapshot": self.mapping,
            "code_sha256": CODE_SHA,
            "output_root": self.root,
        }
        with self.assertRaisesRegex(ResearchCoordinatorError, "必须显式提供"):
            prepare_research_plan(**base)
        for field in ("estimated_worker_seconds", "estimated_temporary_bytes"):
            values = {
                **base,
                "estimated_worker_seconds": 1,
                "estimated_temporary_bytes": 1,
                field: 0,
            }
            with self.subTest(field=field), self.assertRaisesRegex(
                ResearchCoordinatorError, "必须大于零"
            ):
                prepare_research_plan(**values)

    def test_profile_cannot_widen_global_approval_boundaries(self):
        self.profile["resource_limits"].update(
            {
                "max_new_raw_read_bytes": 100_000_000_000,
                "max_temporary_bytes": 10_000_000_000,
                "max_worker_runtime_seconds": 1_200,
                "worker_soft_stop_seconds": 1_000,
            }
        )
        plan = self.plan()

        self.assertEqual(plan.limits, resource_gate.ResourceLimits())

    def test_execute_rejects_profile_and_limits_drift_after_prepare(self):
        self.profile["resource_limits"].update(
            {
                "max_new_raw_read_bytes": 100,
                "max_temporary_bytes": 10,
                "max_worker_runtime_seconds": 100,
                "worker_soft_stop_seconds": 90,
            }
        )
        plan = self.plan()
        drifted_profile = deepcopy(plan.profile)
        drifted_profile["resource_limits"].update(
            {
                "max_new_raw_read_bytes": 50_000_000_000,
                "max_temporary_bytes": 5_000_000_000,
                "max_worker_runtime_seconds": 600,
                "worker_soft_stop_seconds": 540,
            }
        )

        with self.assertRaisesRegex(ResearchCoordinatorError, "plan.bindings"):
            execute_research(
                replace(
                    plan,
                    profile=drifted_profile,
                    limits=resource_gate.ResourceLimits(),
                ),
                executor_for_plan(plan),
            )
        self.assertFalse(plan.run_directory.exists())

    def test_worker_plan_is_portable_relative_and_content_addressed(self):
        plan = self.plan(maximum_artifacts_per_chunk=2)
        worker = build_worker_plan(plan)
        serialized = json.loads(json.dumps(worker))

        self.assertEqual(
            verify_worker_plan(serialized, expected_bindings=plan.bindings),
            serialized,
        )
        self.assertNotIn(str(self.root), json.dumps(worker))
        self.assertEqual(worker["execution_mode"], "full_profile")
        self.assertEqual(worker["resource_estimate"]["new_raw_read_bytes"], 40)
        for chunk in worker["chunks"]:
            self.assertLessEqual(len(chunk["artifacts"]), 5)
            for item in chunk["artifacts"]:
                self.assertFalse(Path(item["relative_path"]).is_absolute())
                self.assertTrue(item["relative_path"].startswith("rrc25/"))
                self.assertTrue(item["selection_roles"])

        with tempfile.TemporaryDirectory() as second_root:
            second = self.plan(
                output_root=Path(second_root), maximum_artifacts_per_chunk=2
            )
            self.assertEqual(build_worker_plan(second), worker)

    def test_worker_plan_rejects_path_or_hash_tampering(self):
        plan = self.plan()
        worker = build_worker_plan(plan)
        for field, value, message in (
            ("relative_path", "/raw/updates.gz", "relative_path"),
            ("file_sha256", "0" * 64, "artifact_id"),
        ):
            with self.subTest(field=field):
                tampered = deepcopy(worker)
                tampered["chunks"][0]["artifacts"][0][field] = value
                semantic = dict(tampered)
                semantic.pop("worker_plan_sha256")
                tampered["worker_plan_sha256"] = hashlib.sha256(
                    json.dumps(
                        semantic,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                with self.assertRaisesRegex(ResearchCoordinatorError, message):
                    verify_worker_plan(tampered)

    def test_worker_plan_rejects_rib_mixed_with_another_artifact(self):
        worker = deepcopy(build_worker_plan(self.plan()))
        rib_chunk = worker["chunks"][0]
        update_chunk = worker["chunks"][2]
        moved = update_chunk["artifacts"].pop(0)
        update_chunk["artifact_ids"].pop(0)
        update_chunk["artifact_count"] -= 1
        update_chunk["compressed_bytes"] -= moved["size_bytes"]
        rib_chunk["artifacts"].append(moved)
        rib_chunk["artifact_ids"].append(moved["artifact_id"])
        rib_chunk["artifact_count"] += 1
        rib_chunk["compressed_bytes"] += moved["size_bytes"]
        semantic = dict(worker)
        semantic.pop("worker_plan_sha256")
        worker["worker_plan_sha256"] = hashlib.sha256(
            json.dumps(
                semantic,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        with self.assertRaisesRegex(ResearchCoordinatorError, "RIB 必须独立"):
            verify_worker_plan(worker)

    def test_execute_and_verify_fixture_closed_loop(self):
        plan = self.plan(maximum_artifacts_per_chunk=2)
        result = execute_research(plan, executor_for_plan(plan))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.run_state["execution_mode"], "full_profile")
        self.assertEqual(result.run_state["acceptance_state"], "pending")
        verified = verify_research_run(
            result.run_directory, expected_bindings=plan.bindings
        )
        self.assertEqual(verified.status, "completed")
        self.assertEqual(verified.record_count, 4)
        self.assertEqual(verified.output_count, 3)

    def test_existing_run_is_never_overwritten(self):
        plan = self.plan()
        first = execute_research(plan, executor_for_plan(plan))
        before = sorted(path.name for path in first.run_directory.iterdir())

        repeated_plan = self.plan()
        self.assertFalse(repeated_plan.ready)
        with self.assertRaises(FileExistsError):
            execute_research(plan, executor_for_plan(plan))
        self.assertEqual(
            sorted(path.name for path in first.run_directory.iterdir()), before
        )

    def test_database_write_report_blocks_at_record_boundary(self):
        plan = self.plan()
        result = execute_research(
            plan, executor_for_plan(plan, database_writes=1)
        )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.run_state["research_run_state"], "incomplete")
        self.assertEqual(result.run_state["acceptance_state"], "not_accepted")
        self.assertEqual(
            result.run_state["execution"]["database_write_operations"], 1
        )
        self.assertEqual(result.run_state["outputs"], [])
        verified = verify_research_run(
            result.run_directory, expected_bindings=plan.bindings
        )
        self.assertEqual(verified.status, "blocked")

    def test_production_or_protected_output_is_rejected(self):
        cases = (
            ({"production_roots": (str(self.root),)}, "production_write_target"),
            ({"protected_roots": (str(self.root),)}, "protected_write_target"),
        )
        for override, expected in cases:
            with self.subTest(expected=expected):
                plan = self.plan(**override)
                self.assertFalse(plan.ready)
                codes = {
                    item["code"] for item in plan.resource_gate["findings"]
                }
                self.assertIn(expected, codes)

    def test_output_root_symlink_is_rejected_before_resolution(self):
        real_root = self.root / "real-output"
        real_root.mkdir()
        linked_root = self.root / "linked-output"
        linked_root.symlink_to(real_root, target_is_directory=True)

        with self.assertRaisesRegex(
            ResearchCoordinatorError, "output_root 必须是非符号链接目录"
        ):
            self.plan(output_root=linked_root)

    def test_json_metadata_symlink_is_rejected(self):
        metadata = self.root / "metadata.json"
        metadata.write_text("{}", encoding="utf-8")
        linked = self.root / "metadata-link.json"
        linked.symlink_to(metadata)

        with self.assertRaisesRegex(ResearchCoordinatorError, "无法只读打开元数据"):
            load_json_metadata(linked)

    def test_new_entrypoints_do_not_import_database_or_immutable_core(self):
        paths = (
            Path("backend/data_pipeline/research/rrc25_country_outage/coordinator.py"),
            Path("dev/data_quality/rrc25_iran_research.py"),
        )
        forbidden = (
            "backend.core",
            "pymysql",
            "psycopg",
            "sqlalchemy",
            "cassandra",
            "sqlite3",
        )
        imported = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
        self.assertFalse(
            [
                module
                for module in imported
                if module.startswith(forbidden)
            ]
        )

    def test_exact_estimated_raw_boundary_is_rejected(self):
        self.manifest, self.verification = manifest_bundle(
            size_override={
                "baseline": 49_999_999_970,
                "seed": 10,
                "update0": 10,
                "update1": 10,
            }
        )
        plan = self.plan()

        self.assertFalse(plan.ready)
        self.assertEqual(plan.resource_gate["decision"], "approval_required")

    def test_exact_estimated_temp_and_soft_runtime_are_not_executable(self):
        cases = (
            ({"estimated_temporary_bytes": 5_000_000_000}, "approval_required"),
            ({"estimated_worker_seconds": 540}, "soft_stop"),
        )
        for override, decision in cases:
            with self.subTest(decision=decision):
                plan = self.plan(**override)
                self.assertFalse(plan.ready)
                self.assertEqual(plan.resource_gate["decision"], decision)

    def test_exact_observed_raw_and_temporary_boundaries_block(self):
        cases = (
            {"raw_bytes": 50_000_000_000, "temporary_bytes": 1},
            {"raw_bytes": 1, "temporary_bytes": 5_000_000_000},
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as path:
                root = Path(path)
                plan = self.plan(output_root=root)
                result = execute_research(
                    plan, executor_for_plan(plan, **case)
                )
                self.assertEqual(result.status, "blocked")
                self.assertEqual(result.run_state["acceptance_state"], "not_accepted")

    def test_exact_worker_hard_boundary_blocks_not_soft_pauses(self):
        plan = self.plan()
        result = execute_research(
            plan,
            executor_for_plan(plan),
            clock=SequenceClock((0, 0, 600)),
        )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(
            result.run_state["resource_stop"]["decision"],
            "approval_required",
        )
        codes = {
            item["code"]
            for item in result.run_state["resource_stop"]["findings"]
        }
        self.assertIn("runtime_hard_limit_reached", codes)

    def test_runtime_gate_is_cumulative_across_chunks_in_one_process(self):
        plan = self.plan(maximum_artifacts_per_chunk=1)
        result = execute_research(
            plan,
            executor_for_plan(plan),
            clock=SequenceClock((0, 0, 100, 100, 300, 500, 500, 600)),
        )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.run_state["execution"]["max_worker_seconds"], 600)
        self.assertEqual(
            result.run_state["observed_resource_gate"]["decision"],
            "approval_required",
        )
        self.assertEqual(result.run_state["resource_limits"], plan.limits.to_dict())
        verify_research_run(
            result.run_directory, expected_bindings=plan.bindings
        )

    def test_run_state_freezes_stricter_profile_resource_limits(self):
        self.profile["resource_limits"].update(
            {
                "max_new_raw_read_bytes": 100,
                "max_temporary_bytes": 10,
                "max_worker_runtime_seconds": 100,
                "worker_soft_stop_seconds": 90,
            }
        )
        plan = self.plan()
        result = execute_research(
            plan,
            executor_for_plan(plan),
            clock=SequenceClock((0, 0, 100)),
        )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(
            result.run_state["resource_limits"],
            {
                "max_new_raw_read_bytes": 100,
                "max_temporary_bytes": 10,
                "max_worker_runtime_seconds": 100,
                "worker_soft_stop_seconds": 90,
                "database_writes": "forbidden",
                "output_storage": "filesystem_only",
            },
        )
        self.assertEqual(
            result.run_state["observed_resource_gate"]["limits"],
            result.run_state["resource_limits"],
        )
        verify_research_run(
            result.run_directory, expected_bindings=plan.bindings
        )

    def test_resume_rejects_any_replaced_or_profile_drifted_limits(self):
        self.profile["resource_limits"].update(
            {
                "max_new_raw_read_bytes": 100,
                "max_temporary_bytes": 10,
                "max_worker_runtime_seconds": 100,
                "worker_soft_stop_seconds": 90,
            }
        )
        plan = self.plan()
        paused = execute_research(
            plan,
            executor_for_plan(plan),
            clock=SequenceClock((0, 0, 91)),
        )
        self.assertEqual(paused.status, "paused")
        resume_plan = self.plan(allow_existing_run=True)

        replacements = (
            resource_gate.ResourceLimits(),
            resource_gate.ResourceLimits(
                max_new_raw_read_bytes=50,
                max_temporary_bytes=5,
                max_worker_runtime_seconds=80,
                worker_soft_stop_seconds=70,
            ),
        )
        for limits in replacements:
            with self.subTest(limits=limits), self.assertRaisesRegex(
                ResearchCoordinatorError, "plan.limits"
            ):
                resume_research(
                    replace(resume_plan, limits=limits),
                    executor_for_plan(resume_plan),
                )

        drifted_profile = deepcopy(resume_plan.profile)
        drifted_profile["resource_limits"].update(
            {
                "max_new_raw_read_bytes": 50_000_000_000,
                "max_temporary_bytes": 5_000_000_000,
                "max_worker_runtime_seconds": 600,
                "worker_soft_stop_seconds": 540,
            }
        )
        with self.assertRaisesRegex(
            ResearchCoordinatorError, "plan.bindings"
        ):
            resume_research(
                replace(
                    resume_plan,
                    profile=drifted_profile,
                    limits=resource_gate.ResourceLimits(),
                ),
                executor_for_plan(resume_plan),
            )

    def test_final_chunk_flush_time_is_gated_before_completed_state(self):
        plan = self.plan(maximum_artifacts_per_chunk=2)
        clock = MutableClock()
        original = coordinator_module.write_canonical_jsonl_gzip
        calls = 0

        def delayed_flush(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == len(plan.chunks):
                clock.value = 600
            return result

        with patch.object(
            coordinator_module,
            "write_canonical_jsonl_gzip",
            side_effect=delayed_flush,
        ):
            result = execute_research(
                plan, executor_for_plan(plan), clock=clock
            )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.run_state["execution"]["max_worker_seconds"], 600)
        self.assertEqual(
            result.run_state["observed_resource_gate"]["decision"],
            "approval_required",
        )

    def test_paused_run_is_upgraded_to_blocked_when_flush_reaches_hard_limit(self):
        plan = self.plan(maximum_artifacts_per_chunk=1)
        clock = MutableClock()
        base_executor = executor_for_plan(plan)
        original = coordinator_module.write_canonical_jsonl_gzip

        def soft_stopping_executor(artifact_row, start_record_ordinal):
            for record in base_executor(artifact_row, start_record_ordinal):
                clock.value = 541
                yield record

        def hard_limit_flush(*args, **kwargs):
            result = original(*args, **kwargs)
            clock.value = 600
            return result

        with patch.object(
            coordinator_module,
            "write_canonical_jsonl_gzip",
            side_effect=hard_limit_flush,
        ):
            result = execute_research(
                plan, soft_stopping_executor, clock=clock
            )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.run_state["execution"]["max_worker_seconds"], 600)
        self.assertEqual(result.run_state["runtime_evidence_kind"], "lower_bound")
        verify_research_run(
            result.run_directory, expected_bindings=plan.bindings
        )

    def test_completed_candidate_crossing_soft_limit_gets_eof_checkpoint(self):
        plan = self.plan()
        clock = MutableClock()
        original = coordinator_module._publish_run_state
        calls = 0

        def soft_limit_after_candidate(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 1:
                clock.value = 540
            return result

        with patch.object(
            coordinator_module,
            "_publish_run_state",
            side_effect=soft_limit_after_candidate,
        ):
            result = execute_research(
                plan, executor_for_plan(plan), clock=clock
            )

        self.assertEqual(result.status, "paused")
        self.assertEqual(result.run_state["state_sequence"], 2)
        self.assertIsNotNone(result.run_state["checkpoint_ref"])
        verify_research_run(
            result.run_directory, expected_bindings=plan.bindings
        )

        resume_plan = self.plan(allow_existing_run=True)
        completed = resume_research(
            resume_plan, executor_for_plan(resume_plan), clock=clock
        )
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.run_state["state_sequence"], 3)
        verify_research_run(
            completed.run_directory, expected_bindings=resume_plan.bindings
        )

    def test_run_state_write_crossing_hard_limit_appends_blocked_state(self):
        plan = self.plan()
        clock = MutableClock()
        original = coordinator_module._publish_run_state

        def delayed_state(*args, **kwargs):
            result = original(*args, **kwargs)
            clock.value = 600
            return result

        with patch.object(
            coordinator_module,
            "_publish_run_state",
            side_effect=delayed_state,
        ):
            result = execute_research(
                plan, executor_for_plan(plan), clock=clock
            )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.run_state["state_sequence"], 2)
        self.assertEqual(result.run_state["runtime_evidence_kind"], "lower_bound")
        self.assertTrue(plan.run_directory.exists())
        verify_research_run(
            result.run_directory, expected_bindings=plan.bindings
        )

        previous = result.run_state["previous_state_ref"]
        previous_path = result.run_directory / previous["path"]
        previous_path.write_bytes(previous_path.read_bytes() + b" ")
        with self.assertRaisesRegex(
            ResearchCoordinatorError, "前驱哈希不一致"
        ):
            verify_research_run(
                result.run_directory, expected_bindings=plan.bindings
            )

    def test_duplicate_run_state_sequence_is_rejected(self):
        plan = self.plan()
        result = execute_research(plan, executor_for_plan(plan))
        payload = b"{}\n"
        digest = hashlib.sha256(payload).hexdigest()
        duplicate = result.run_directory / f"run-state-000001-{digest}.json"
        duplicate.write_bytes(payload)

        with self.assertRaisesRegex(
            ResearchCoordinatorError, "重复 state_sequence"
        ):
            verify_research_run(
                result.run_directory, expected_bindings=plan.bindings
            )

    def test_soft_stop_resume_and_binding_mismatch(self):
        plan = self.plan()
        paused = execute_research(
            plan,
            executor_for_plan(plan),
            clock=SequenceClock((0, 0, 541)),
        )
        self.assertEqual(paused.status, "paused")
        self.assertIsNotNone(paused.run_state["checkpoint_ref"])

        resume_plan = self.plan(allow_existing_run=True)
        bad_plan = replace(
            resume_plan,
            bindings={**resume_plan.bindings, "code_sha256": "d" * 64},
        )
        with self.assertRaisesRegex(ResearchCoordinatorError, "bindings"):
            resume_research(bad_plan, executor_for_plan(resume_plan))

        completed = resume_research(
            resume_plan, executor_for_plan(resume_plan)
        )
        self.assertEqual(completed.status, "completed")
        verified = verify_research_run(
            completed.run_directory, expected_bindings=resume_plan.bindings
        )
        self.assertEqual(verified.record_count, 4)

    def test_resume_adopts_identical_orphan_after_state_publish_failure(self):
        plan = self.plan()
        paused = execute_research(
            plan,
            executor_for_plan(plan),
            clock=SequenceClock((0, 0, 541)),
        )
        self.assertEqual(paused.status, "paused")
        resume_plan = self.plan(allow_existing_run=True)

        with patch.object(
            coordinator_module,
            "_publish_run_state",
            side_effect=ResearchCoordinatorError("注入 state publish 失败"),
        ), self.assertRaisesRegex(ResearchCoordinatorError, "注入 state publish 失败"):
            resume_research(
                resume_plan,
                executor_for_plan(resume_plan),
                clock=MutableClock(),
            )

        still_paused = verify_research_run(
            paused.run_directory, expected_bindings=plan.bindings
        )
        self.assertEqual(still_paused.status, "paused")
        referenced = {row["path"] for row in paused.run_state["outputs"]}
        orphans = {
            path.relative_to(paused.run_directory).as_posix()
            for path in (paused.run_directory / "chunks").iterdir()
            if path.relative_to(paused.run_directory).as_posix() not in referenced
        }
        self.assertTrue(orphans)

        completed = resume_research(
            resume_plan,
            executor_for_plan(resume_plan),
            clock=MutableClock(),
        )
        self.assertEqual(completed.status, "completed")
        verified = verify_research_run(
            completed.run_directory, expected_bindings=resume_plan.bindings
        )
        self.assertEqual(verified.status, "completed")

    def test_legacy_full_window_name_normalizes_to_frozen_full_profile(self):
        self.assertEqual(normalize_execution_mode("full_window"), "full_profile")
        self.assertEqual(normalize_execution_mode("full_profile"), "full_profile")

    def test_bounded_pilot_is_completed_but_never_accepted_as_full_profile(self):
        original_profile = deepcopy(self.profile)
        plan = self.plan(pilot_end_exclusive="2026-02-27T16:05:00Z")

        self.assertTrue(plan.ready)
        self.assertEqual(plan.execution_mode, "bounded_pilot")
        self.assertEqual(len(plan.flat_artifacts), 3)
        dry_run = plan.to_dict()
        worker = dry_run["worker_plan"]
        verify_worker_plan(worker, expected_bindings=plan.bindings)
        self.assertEqual(worker["execution_mode"], "bounded_pilot")
        self.assertEqual(
            worker["remaining_profile_interval"]["start_utc"],
            "2026-02-27T16:05:00Z",
        )
        self.assertTrue(worker["blocking_incomplete_reasons_zh"])
        selected = [
            item
            for chunk in worker["chunks"]
            for item in chunk["artifacts"]
        ]
        self.assertFalse(
            [
                item
                for item in selected
                if set(item["selection_roles"])
                & {"analysis_updates", "analysis_ribs"}
                and item["artifact_time_utc"] >= "2026-02-27T16:05:00Z"
            ]
        )
        self.assertEqual(
            worker["resource_estimate"]["new_raw_read_bytes"],
            sum(item["size_bytes"] for item in selected),
        )
        self.assertEqual(
            plan.input_selection["coverage"]["analysis_updates"],
            {"expected_count": 1, "observed_count": 1, "missing_count": 0},
        )
        result = execute_research(plan, executor_for_plan(plan))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.run_state["execution_mode"], "bounded_pilot")
        self.assertEqual(result.run_state["research_run_state"], "incomplete")
        self.assertEqual(result.run_state["acceptance_state"], "not_accepted")
        self.assertEqual(
            result.run_state["unprocessed_profile_interval"]["start_utc"],
            "2026-02-27T16:05:00Z",
        )
        self.assertTrue(result.run_state["blocking_incomplete_reasons_zh"])
        verify_research_run(
            result.run_directory, expected_bindings=plan.bindings
        )
        self.assertEqual(self.profile, original_profile)

    def test_bounded_plan_cannot_be_relabelled_as_full_profile(self):
        plan = self.plan(pilot_end_exclusive="2026-02-27T16:05:00Z")
        spoofed = replace(
            plan,
            execution_mode="full_profile",
            pilot_end_exclusive=None,
            unprocessed_profile_interval=None,
            acceptance_blockers_zh=(),
        )

        with self.assertRaisesRegex(
            ResearchCoordinatorError, "执行范围或 acceptance blockers"
        ):
            execute_research(spoofed, executor_for_plan(spoofed))
        self.assertFalse(plan.run_directory.exists())

    def test_verify_detects_output_tampering(self):
        plan = self.plan()
        result = execute_research(plan, executor_for_plan(plan))
        output = result.run_state["outputs"][0]
        path = result.run_directory / output["path"]
        path.write_bytes(path.read_bytes() + b"tampered")

        with self.assertRaisesRegex(ResearchCoordinatorError, "输出哈希不一致"):
            verify_research_run(
                result.run_directory, expected_bindings=plan.bindings
            )

    def test_cli_dry_run_does_not_require_raw_files(self):
        metadata = self.root / "metadata"
        metadata.mkdir()
        paths = {}
        for name, value in (
            ("profile", self.profile),
            ("manifest", self.manifest),
            ("verification", self.verification),
            ("mapping", self.mapping),
        ):
            target = metadata / f"{name}.json"
            target.write_text(
                json.dumps(value, ensure_ascii=False), encoding="utf-8"
            )
            paths[name] = target
        output = io.StringIO()
        arguments = (
            "dry-run",
            "--profile", str(paths["profile"]),
            "--manifest", str(paths["manifest"]),
            "--manifest-verification", str(paths["verification"]),
            "--mapping", str(paths["mapping"]),
            "--code-sha256", CODE_SHA,
            "--output-root", str(self.root),
            "--estimated-worker-seconds", "1",
            "--estimated-temporary-bytes", "1",
        )
        with redirect_stdout(output):
            code = cli_main(arguments)
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ready"])
        self.assertFalse(payload["dry_run_opens_raw_mrt"])

        worker_output = io.StringIO()
        with redirect_stdout(worker_output):
            worker_code = cli_main(arguments + ("--worker-plan-only",))
        self.assertEqual(worker_code, 0)
        worker = json.loads(worker_output.getvalue())
        verify_worker_plan(worker)
        self.assertEqual(worker["execution_mode"], "full_profile")
        self.assertNotIn(str(self.root), worker_output.getvalue())


if __name__ == "__main__":
    unittest.main()
