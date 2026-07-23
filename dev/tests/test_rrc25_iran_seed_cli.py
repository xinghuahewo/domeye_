from __future__ import annotations

from argparse import Namespace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from backend.data_pipeline.route_event import artifact_id_v1
from backend.data_pipeline.research.rrc25_country_outage import (
    bounded_pilot_worker,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    MappingAssignment,
    build_country_mapping_view,
)
from backend.data_pipeline.research.rrc25_country_outage.country_mapping import (
    freeze_as_country_mapping,
)
from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (
    canonical_json,
)
from dev.data_quality import rrc25_iran_research as cli


CODE_SHA = "c" * 64


def _artifact(kind: str, at: str, suffix: str) -> dict[str, object]:
    file_sha256 = suffix * 64
    return {
        "artifact_id": artifact_id_v1(file_sha256),
        "artifact_type": kind,
        "artifact_time_utc": at,
        "collector_id": "rrc25",
        "relative_path": f"rrc25/{kind}s.{at}.{suffix}.gz",
        "file_sha256": file_sha256,
        "size_bytes": 10,
        "compression": "gz",
    }


def _manifest_bundle() -> tuple[dict[str, object], dict[str, object]]:
    rows = [
        _artifact("rib", "2026-02-27T08:00:00Z", "1"),
        _artifact("rib", "2026-02-27T16:00:00Z", "2"),
        _artifact("update", "2026-02-27T16:00:00Z", "3"),
        _artifact("update", "2026-02-27T16:05:00Z", "4"),
    ]
    fingerprint = "f" * 64
    return (
        {
            "schema_version": 1,
            "manifest_kind": "mrt_artifact_manifest",
            "manifest_fingerprint_sha256": fingerprint,
            "artifacts": rows,
        },
        {
            "verified": True,
            "manifest_fingerprint_sha256": fingerprint,
            "artifact_count": len(rows),
        },
    )


def _revised_delta(compatible: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "iran-revised-mapping-delta/v1",
        "target_country": "IR",
        "compatible_base_binding": {
            "snapshot_id": compatible["snapshot_id"],
            "source_file_sha256": compatible["source_file_sha256"],
            "semantic_fingerprint_sha256": compatible[
                "semantic_fingerprint_sha256"
            ],
        },
        "source": {
            "path": "/readonly/revised.csv",
            "sha256": "d" * 64,
            "size_bytes": 100,
            "source_kind": "legacy_derived_official_delegate_missing_list",
            "generated_on": "2026-04-07",
            "upstream_artifact_state": "not_retained_in_discovered_directory",
        },
        "temporal_policy": {
            "delegated_date_on_or_before": "20260227",
            "interval_role": "known_allocated_by_research_window_start_date",
            "excluded_after_cutoff_count": 0,
            "excluded_after_cutoff_asns": [],
        },
        "rows": [
            {
                "asn": 65001,
                "registry": "ripencc",
                "country_code": "IR",
                "resource_type": "asn",
                "delegated_date": "20260220",
                "status": "allocated",
                "range_start": 65001,
                "range_count": 1,
            }
        ],
        "summary": {
            "source_row_count": 1,
            "included_row_count": 1,
            "excluded_after_cutoff_count": 0,
            "present_in_compatible_snapshot_count": 1,
            "compatible_zz_count": 1,
            "compatible_explicit_other_country_count": 0,
        },
        "compatible_override_audit": {
            "policy": "revised_view_only_compatible_view_unchanged",
            "explicit_other_country_rows": [],
        },
        "limitations_zh": [
            "该增量文件由旧项目在2026-04-07生成。",
            "上游官方 delegated 原始文件未在发现目录中保留。",
        ],
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _attestation_for_seed(seed: dict[str, object]) -> dict[str, object]:
    semantic = {
        "schema_version": "rrc25-seed-spool-attestation/v1",
        "artifact_binding": {
            "artifact_id": seed["artifact_id"],
            "file_sha256": seed["file_sha256"],
            "compressed_size_bytes": seed["size_bytes"],
        },
        "decompressed": {"size_bytes": 100, "sha256": "a" * 64},
        "measurement": {
            "method": "full_streaming_gzip_decompression_sha256_v1",
            "measured_at_utc": "2026-07-22T10:11:14Z",
            "raw_read_pass_count": 1,
        },
    }
    return {
        **semantic,
        "semantic_fingerprint_sha256": hashlib.sha256(
            canonical_json(semantic).encode("utf-8")
        ).hexdigest(),
    }


class SeedCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw_root = self.root / "raw"
        self.checkpoints = self.root / "checkpoints"
        self.raw_root.mkdir()
        self.checkpoints.mkdir()
        seed_lock = self.root / "prepared" / "probe-ledger"
        seed_lock.mkdir(parents=True)
        (seed_lock / "SEED-EXECUTION.LOCK").touch()
        self.profile = json.loads(
            Path("config/research/iran-rrc25-202602.json").read_text(
                encoding="utf-8"
            )
        )
        manifest, verification = _manifest_bundle()
        mapping_source = self.root / "compatible.csv"
        mapping_source.write_text(
            "asn,as_country\n65001,ZZ\n65002,IR\n", encoding="utf-8"
        )
        compatible = freeze_as_country_mapping(mapping_source)
        values = {
            "profile": self.profile,
            "manifest": manifest,
            "verification": verification,
            "mapping": compatible,
            "revised": _revised_delta(compatible),
            "attestation": _attestation_for_seed(manifest["artifacts"][1]),
        }
        self.paths = {}
        for name, value in values.items():
            path = self.root / f"{name}.json"
            _write_json(path, value)
            self.paths[name] = path

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def args(self) -> Namespace:
        return Namespace(
            profile=str(self.paths["profile"]),
            manifest=str(self.paths["manifest"]),
            manifest_verification=str(self.paths["verification"]),
            mapping=str(self.paths["mapping"]),
            revised_mapping=str(self.paths["revised"]),
            code_identity=str(self.root / "code.json"),
            code_sha256=CODE_SHA,
            raw_root=str(self.raw_root),
            checkpoint_directory=str(self.checkpoints),
            pilot_end_exclusive="2026-02-27T16:10:00Z",
            planned_seed_checkpoint_seconds=420.0,
            prepared_directory=str(self.root / "prepared"),
            probe_ledger_terminal=str(
                self.root / "prepared/probe-ledger/GENESIS.json"
            ),
            resume_checkpoint=None,
            seed_spool_attestation=str(self.paths["attestation"]),
        )

    def probe_accounting(
        self,
        *,
        selection_id: str,
        selection_sha: str,
        prior: int,
        bindings: dict[str, str] | None = None,
    ) -> dict[str, object]:
        frozen_bindings = bindings or {
            "profile_sha256": "1" * 64,
            "input_selection_sha256": selection_sha,
            "code_sha256": CODE_SHA,
            "mapping_sha256": "2" * 64,
        }
        semantic = {
            "schema_version": "rrc25-native-probe-terminal-accounting/v1",
            "ledger_id": "probe_ledger_v1_fixture",
            "prepared_directory": str(self.root / "prepared"),
            "prepared_receipt_ref": {
                "path": "PREPARATION.json",
                "sha256": "3" * 64,
                "size_bytes": 1,
            },
            "prepared_bindings": frozen_bindings,
            "selection_id": selection_id,
            "terminal_receipt_ref": {
                "path": "probe-ledger/GENESIS.json",
                "sha256": "4" * 64,
                "size_bytes": 1,
            },
            "terminal_receipt_kind": (
                "zero_genesis" if prior == 0 else "imported_genesis"
            ),
            "attempt_count": 0,
            "outcome_count": 0,
            "prior_accounting": {"fixture": True},
            "initial_observed_lower_bound_new_raw_bytes": 0,
            "initial_reserved_upper_bound_new_raw_bytes": prior,
            "probe_observed_lower_bound_new_raw_bytes": 0,
            "probe_observed_upper_bound_new_raw_bytes": 0,
            "cumulative_reserved_new_raw_bytes": prior,
            "cumulative_semantics": "nonrefundable_reserved_upper_bound",
            "reservation_refund_policy": (
                "never_refund_even_on_failure_timeout_or_retry"
            ),
            "chain_refs_sha256": "5" * 64,
        }
        return {
            **semantic,
            "accounting_fingerprint_sha256": hashlib.sha256(
                canonical_json(
                    {
                        "schema": "rrc25_native_probe_terminal_accounting_v1",
                        "accounting": semantic,
                    }
                ).encode("utf-8")
            ).hexdigest(),
        }

    def seed_reservation(
        self,
        *,
        prior: int = 0,
        sequence: int = 1,
    ) -> dict[str, object]:
        return {
            "schema_version": "rrc25-seed-raw-reservation/v1",
            "attempt_id": f"seed_attempt_v1_{sequence:04d}",
            "sequence": sequence,
            "prior_cumulative_reserved_new_raw_bytes": prior,
            "reserved_new_raw_bytes": 10,
            "cumulative_reserved_new_raw_bytes": prior + 10,
            "reservation_fingerprint_sha256": f"{sequence:x}" * 64,
        }

    @staticmethod
    def seed_outcome() -> dict[str, object]:
        return {
            "schema_version": "rrc25-seed-raw-outcome/v1",
            "outcome": "fixture",
        }

    def segment_context(self, *, prior=None):
        compatible = build_country_mapping_view(
            (MappingAssignment(65001, ("IR",), "mapped"),),
            view="compatible",
            target_country="IR",
            source_sha256="a" * 64,
            source_ref="fixture-compatible",
        )
        revised = build_country_mapping_view(
            (MappingAssignment(65001, ("IR",), "mapped"),),
            view="revised",
            target_country="IR",
            source_sha256="b" * 64,
            source_ref="fixture-revised",
        )
        union = SimpleNamespace(
            semantics="raw_retention_only_not_a_statistical_mapping_view"
        )
        reservation = self.seed_reservation()
        return {
            "profile": self.profile,
            "selection": {
                "selection_id": "rsel_v1_fixture",
                "semantic_fingerprint_sha256": "9" * 64,
                "window": {
                    "start_utc": "2026-02-27T16:00:00Z",
                    "end_exclusive_utc": "2026-02-27T16:10:00Z",
                },
                "roles": {
                    "state_seed_rib": {
                        "artifact_id": "art_v1_fixture",
                        "artifact_time_utc": "2026-02-27T16:00:00Z",
                        "relative_path": "rrc25/bview.fixture.gz",
                        "file_sha256": "e" * 64,
                        "size_bytes": 10,
                    }
                },
            },
            "compatible_mapping": compatible,
            "revised_mapping": revised,
            "raw_retention_mapping": union,
            "code_identity": {"identity_sha256": CODE_SHA},
            "seed_spool_attestation": json.loads(
                self.paths["attestation"].read_text(encoding="utf-8")
            ),
            "raw_root": self.raw_root,
            "checkpoint_directory": self.checkpoints,
            "resume_checkpoint": (
                None if prior is None else self.checkpoints / "prior.json"
            ),
            "prior_checkpoint_verification": prior,
            "prior_new_raw_read_bytes": 0,
            "prior_raw_accounting": self.probe_accounting(
                selection_id="rsel_v1_fixture",
                selection_sha="9" * 64,
                prior=0,
            ),
            "seed_raw_reservation": (
                reservation if prior is not None else None
            ),
            "seed_raw_ledger": {
                "current_cumulative_reserved_new_raw_bytes": (
                    10 if prior is not None else 0
                ),
                "latest_reservation": (
                    reservation if prior is not None else None
                ),
            },
            "seed_reconciliation": None,
            "resource_gate": {"execution_allowed": True},
        }

    def full_seed_verification(
        self,
        *,
        sequence: int,
        ordinal: int,
        offset: int,
        phase: str = "seed_rib",
        spool_name: str = "seed-spool.fixture.mrt",
        spool_sha: str = "a" * 64,
        spool_size: int = 100,
    ) -> dict[str, object]:
        return {
            "verified": True,
            "bindings": {"selection_id": "rsel_v1_fixture"},
            "checkpoint_sequence": sequence,
            "checkpoint_fingerprint_sha256": f"{sequence:x}" * 64,
            "position": {"phase": phase},
            "seed_progress": {
                "next_record_ordinal": ordinal,
                "next_record_offset": offset,
                "seed_parse_complete": phase == "updates",
            },
            "seed_spool": {
                "schema_version": "rrc25-rib-decompressed-spool/v1",
                "file_name": spool_name,
                "sha256": spool_sha,
                "size_bytes": spool_size,
            },
            "resources": {
                "prior_new_raw_read_bytes": 1_280_393_043,
                "prior_raw_accounting": self.probe_accounting(
                    selection_id="rsel_v1_fixture",
                    selection_sha="9" * 64,
                    prior=1_280_393_043,
                ),
                "new_raw_read_bytes": 1_280_393_053,
            },
            "seed_spool_reclamation_eligibility": {
                "eligible": phase == "updates"
            },
        }

    def test_seed_dry_context_is_metadata_only_and_binds_dual_view(self):
        args = self.args()
        manifest = json.loads(self.paths["manifest"].read_text(encoding="utf-8"))
        verification = json.loads(
            self.paths["verification"].read_text(encoding="utf-8")
        )
        selection = cli.resolve_research_inputs(
            manifest,
            verification,
            cli._seed_resolver_profile(self.profile, args.pilot_end_exclusive),
        )
        compatible = json.loads(
            self.paths["mapping"].read_text(encoding="utf-8")
        )
        revised = json.loads(
            self.paths["revised"].read_text(encoding="utf-8")
        )
        bindings = {
            "profile_sha256": cli.profile_sha256(self.profile),
            "input_selection_sha256": selection[
                "semantic_fingerprint_sha256"
            ],
            "code_sha256": CODE_SHA,
            "mapping_sha256": cli.mapping_bundle_sha256(compatible, revised),
        }
        accounting = self.probe_accounting(
            selection_id=selection["selection_id"],
            selection_sha=selection["semantic_fingerprint_sha256"],
            prior=1_280_393_043,
            bindings=bindings,
        )
        with mock.patch.object(
            cli,
            "_load_bound_code_identity",
            return_value={"identity_sha256": CODE_SHA},
        ), mock.patch.object(
            cli,
            "verify_probe_raw_ledger_terminal",
            return_value=accounting,
        ), mock.patch.object(
            cli,
            "verify_seed_raw_ledger",
            return_value={
                "current_cumulative_reserved_new_raw_bytes": 1_280_393_043,
                "latest_reservation": None,
                "attempt_count": 0,
                "outcome_count": 0,
            },
        ), mock.patch.object(
            cli,
            "run_bounded_pilot_worker",
            side_effect=AssertionError("dry-run 不得打开 worker"),
        ):
            context = cli._seed_context(
                args,
                require_empty_checkpoint_directory=True,
                resume_checkpoint_path=None,
            )
            plan = cli._seed_public_plan(context)

        self.assertTrue(plan["ok"])
        self.assertFalse(plan["opens_raw_mrt"])
        self.assertEqual(plan["database_write_operations"], 0)
        self.assertEqual(plan["prior_new_raw_read_bytes"], 1_280_393_043)
        self.assertEqual(plan["mapping"]["revised_delta_asn_count"], 1)
        self.assertEqual(
            plan["mapping"]["raw_retention_semantics"],
            "raw_retention_only_not_a_statistical_mapping_view",
        )
        self.assertEqual(
            plan["seed_artifact"]["artifact_time_utc"],
            "2026-02-27T16:00:00Z",
        )

    def test_seed_resolver_accepts_full_profile_end(self):
        full_end = self.profile["window"]["end_exclusive_utc"]
        resolved = cli._seed_resolver_profile(self.profile, full_end)
        self.assertEqual(resolved["window"]["end_exclusive_utc"], full_end)

    def test_prior_raw_bytes_participates_in_cumulative_50gb_gate(self):
        attestation = json.loads(
            self.paths["attestation"].read_text(encoding="utf-8")
        )
        limit = self.profile["resource_limits"]["max_new_raw_read_bytes"]
        gate = cli._seed_write_gate(
            profile=self.profile,
            checkpoint_directory=self.checkpoints,
            seed_size_bytes=10,
            seed_spool_attestation=attestation,
            prior_raw_read_bytes=limit - 10,
            planned_seed_checkpoint_seconds=420,
            resume=False,
        )
        self.assertFalse(gate["execution_allowed"])
        self.assertIn(
            "new_raw_read_hard_limit_reached",
            {row["code"] for row in gate["findings"]},
        )
        with self.assertRaisesRegex(cli.SeedWorkflowError, "非负整数"):
            cli._seed_write_gate(
                profile=self.profile,
                checkpoint_directory=self.checkpoints,
                seed_size_bytes=10,
                seed_spool_attestation=attestation,
                prior_raw_read_bytes=-1,
                planned_seed_checkpoint_seconds=420,
                resume=False,
            )

    def test_seed_context_rejects_nonempty_start_directory(self):
        (self.checkpoints / "existing.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(
            cli,
            "_load_bound_code_identity",
            return_value={"identity_sha256": CODE_SHA},
        ), self.assertRaisesRegex(cli.SeedWorkflowError, "必须为空"):
            cli._seed_context(
                self.args(),
                require_empty_checkpoint_directory=True,
                resume_checkpoint_path=None,
            )

    def test_resource_estimate_resume_does_not_count_compressed_reread(self):
        attestation = json.loads(
            self.paths["attestation"].read_text(encoding="utf-8")
        )
        resume_gate = cli._seed_write_gate(
            profile=self.profile,
            checkpoint_directory=self.checkpoints,
            seed_size_bytes=10,
            seed_spool_attestation=attestation,
            prior_raw_read_bytes=49_999_999_990,
            planned_seed_checkpoint_seconds=420,
            resume=True,
        )
        self.assertTrue(resume_gate["execution_allowed"])
        self.assertEqual(
            resume_gate["temporary_projection"]["method"],
            "conservative_estimate_with_runtime_exact_gate",
        )
        self.assertFalse(
            resume_gate["temporary_projection"]["is_hard_upper_bound"]
        )
        start_gate = cli._seed_write_gate(
            profile=self.profile,
            checkpoint_directory=self.checkpoints,
            seed_size_bytes=10,
            seed_spool_attestation=attestation,
            prior_raw_read_bytes=49_999_999_990,
            planned_seed_checkpoint_seconds=420,
            resume=False,
        )
        self.assertFalse(start_gate["execution_allowed"])
        self.assertEqual(start_gate["decision"], "approval_required")
        self.assertIn(
            "new_raw_read_hard_limit_reached",
            {row["code"] for row in start_gate["findings"]},
        )

    def test_seed_segment_passes_union_and_never_opens_update(self):
        compatible = build_country_mapping_view(
            (MappingAssignment(65001, ("IR",), "mapped"),),
            view="compatible",
            target_country="IR",
            source_sha256="a" * 64,
            source_ref="fixture-compatible",
        )
        revised = build_country_mapping_view(
            (MappingAssignment(65001, ("IR",), "mapped"),),
            view="revised",
            target_country="IR",
            source_sha256="b" * 64,
            source_ref="fixture-revised",
        )
        union = SimpleNamespace(
            semantics="raw_retention_only_not_a_statistical_mapping_view"
        )
        selection = {
            "selection_id": "rsel_v1_fixture",
            "semantic_fingerprint_sha256": "9" * 64,
            "window": {
                "start_utc": "2026-02-27T16:00:00Z",
                "end_exclusive_utc": "2026-02-27T16:10:00Z",
            },
            "roles": {
                "state_seed_rib": {
                    "artifact_id": "art_v1_fixture",
                    "artifact_time_utc": "2026-02-27T16:00:00Z",
                    "relative_path": "rrc25/bview.fixture.gz",
                    "file_sha256": "e" * 64,
                    "size_bytes": 10,
                }
            },
        }
        context = {
            "profile": self.profile,
            "selection": selection,
            "compatible_mapping": compatible,
            "revised_mapping": revised,
            "raw_retention_mapping": union,
            "code_identity": {"identity_sha256": CODE_SHA},
            "seed_spool_attestation": json.loads(
                self.paths["attestation"].read_text(encoding="utf-8")
            ),
            "raw_root": self.raw_root,
            "checkpoint_directory": self.checkpoints,
            "resume_checkpoint": None,
            "prior_checkpoint_verification": None,
            "prior_new_raw_read_bytes": 0,
            "prior_raw_accounting": self.probe_accounting(
                selection_id="rsel_v1_fixture",
                selection_sha="9" * 64,
                prior=0,
            ),
            "resource_gate": {"execution_allowed": True},
        }
        worker_result = SimpleNamespace(
            checkpoint_path=str(self.checkpoints / "checkpoint.json"),
            incomplete_reason="planned_seed_checkpoint",
            status="incomplete",
            resources={"new_raw_read_bytes": 10, "database_writes": 0},
        )
        verification = {
            "position": {"phase": "seed_rib"},
            "seed_progress": {"next_record_ordinal": 1},
            "checkpoint_sequence": 1,
            "checkpoint_fingerprint_sha256": "f" * 64,
        }
        args = self.args()
        with mock.patch.object(cli, "_seed_context", return_value=context), mock.patch.object(
            cli,
            "reserve_seed_raw_attempt",
            return_value=self.seed_reservation(),
        ), mock.patch.object(
            cli,
            "close_seed_raw_attempt",
            return_value=self.seed_outcome(),
        ), mock.patch.object(
            cli, "run_bounded_pilot_worker", return_value=worker_result
        ) as worker, mock.patch.object(
            cli, "verify_full_seed_checkpoint", return_value=verification
        ):
            result, exit_code = cli._run_seed_segment(
                args, resume=False, clock=lambda: 0.0
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(result["ok"])
        self.assertFalse(result["opens_update_mrt"])
        self.assertEqual(result["database_write_operations"], 0)
        called = worker.call_args.kwargs
        self.assertIs(called["country_mapping"], compatible)
        self.assertIs(called["raw_retention_mapping"], union)
        self.assertTrue(called["stop_after_seed"])
        with self.assertRaisesRegex(cli.SeedWorkflowError, "禁止打开 UPDATE"):
            called["update_record_stream_factory"]({})

    def test_seed_start_reuses_spool_without_raw_reservation(self):
        context = self.segment_context()
        context["reuse_existing_seed_spool"] = True
        worker_result = SimpleNamespace(
            checkpoint_path=str(self.checkpoints / "checkpoint.json"),
            incomplete_reason="planned_seed_checkpoint",
            status="incomplete",
            errors=(),
            resources={"new_raw_read_bytes": 0, "database_writes": 0},
        )
        verification = {
            "position": {"phase": "seed_rib"},
            "seed_progress": {"next_record_ordinal": 1},
            "checkpoint_sequence": 1,
            "checkpoint_fingerprint_sha256": "f" * 64,
        }
        args = self.args()
        args.reuse_existing_seed_spool = True
        with mock.patch.object(
            cli, "_seed_context", return_value=context
        ), mock.patch.object(
            cli,
            "reserve_seed_raw_attempt",
            side_effect=AssertionError("复用 spool 不得预留压缩 raw"),
        ), mock.patch.object(
            cli,
            "close_seed_raw_attempt",
            side_effect=AssertionError("复用 spool 不得闭合不存在的 raw attempt"),
        ), mock.patch.object(
            cli, "run_bounded_pilot_worker", return_value=worker_result
        ) as worker, mock.patch.object(
            cli, "verify_full_seed_checkpoint", return_value=verification
        ):
            result, exit_code = cli._run_seed_segment(
                args, resume=False, clock=lambda: 0.0
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(result["ok"])
        self.assertFalse(result["opens_raw_mrt"])
        self.assertIsNone(result["seed_raw_reservation"])
        called = worker.call_args.kwargs
        self.assertTrue(called["reuse_existing_seed_spool"])
        self.assertIsNone(called["seed_raw_reservation"])
        self.assertEqual(
            called["seed_batch_max_route_events"], 1_048_576
        )
        self.assertEqual(called["seed_batch_max_records"], 65_536)

    def test_seed_segment_rejects_zero_progress_checkpoint(self):
        prior = {
            "checkpoint_fingerprint_sha256": "1" * 64,
            "position": {"phase": "seed_rib"},
            "seed_progress": {"next_record_ordinal": 5},
            "resources": {"new_raw_read_bytes": 10},
        }
        context = self.segment_context(prior=prior)
        worker_result = SimpleNamespace(
            checkpoint_path=str(self.checkpoints / "next.json"),
            incomplete_reason="planned_seed_checkpoint",
            status="incomplete",
            errors=(),
            resources={"new_raw_read_bytes": 10, "database_writes": 0},
        )
        verification = {
            "position": {"phase": "seed_rib"},
            "seed_progress": {"next_record_ordinal": 5},
        }
        with mock.patch.object(cli, "_seed_context", return_value=context), mock.patch.object(
            cli, "run_bounded_pilot_worker", return_value=worker_result
        ), mock.patch.object(
            cli, "verify_full_seed_checkpoint", return_value=verification
        ):
            result, exit_code = cli._run_seed_segment(
                self.args(), resume=True, clock=lambda: 0.0
            )
        self.assertEqual(exit_code, 4)
        self.assertFalse(result["ok"])
        self.assertFalse(result["meaningful_progress"])
        self.assertEqual(result["worker_reason"], "zero_progress_checkpoint_rejected")

    def test_seed_segment_preserves_parse_failure_without_verifier_masking(self):
        context = self.segment_context()
        worker_result = SimpleNamespace(
            checkpoint_path=str(self.checkpoints / "diagnostic.json"),
            incomplete_reason="seed_rib_parse_or_integrity_failure",
            status="incomplete",
            errors=(
                {
                    "phase": "seed_rib",
                    "reason": "spool_sha256_mismatch",
                    "message": "hash mismatch",
                },
            ),
            resources={"new_raw_read_bytes": 10, "database_writes": 0},
        )
        with mock.patch.object(cli, "_seed_context", return_value=context), mock.patch.object(
            cli,
            "reserve_seed_raw_attempt",
            return_value=self.seed_reservation(),
        ), mock.patch.object(
            cli,
            "close_seed_raw_attempt",
            return_value=self.seed_outcome(),
        ), mock.patch.object(
            cli, "run_bounded_pilot_worker", return_value=worker_result
        ), mock.patch.object(
            cli,
            "verify_full_seed_checkpoint",
            side_effect=AssertionError("失败 checkpoint 不得进入 full verifier"),
        ):
            result, exit_code = cli._run_seed_segment(
                self.args(), resume=False, clock=lambda: 0.0
            )
        self.assertEqual(exit_code, 4)
        self.assertEqual(result["worker_reason"], "seed_rib_parse_or_integrity_failure")
        self.assertEqual(result["errors"][0]["reason"], "spool_sha256_mismatch")
        self.assertIsNone(result["checkpoint_verification"])

    def test_seed_checkpoint_verify_failure_closes_reservation_unknown(self):
        context = self.segment_context()
        worker_result = SimpleNamespace(
            checkpoint_path=str(self.checkpoints / "checkpoint.json.gz"),
            incomplete_reason="planned_seed_checkpoint",
            status="incomplete",
            errors=(),
            resources={"new_raw_read_bytes": 10, "database_writes": 0},
        )
        with mock.patch.object(
            cli, "_seed_context", return_value=context
        ), mock.patch.object(
            cli,
            "reserve_seed_raw_attempt",
            return_value=self.seed_reservation(),
        ), mock.patch.object(
            cli, "run_bounded_pilot_worker", return_value=worker_result
        ), mock.patch.object(
            cli,
            "verify_full_seed_checkpoint",
            side_effect=cli.BoundedPilotWorkerError("fingerprint mismatch"),
        ), mock.patch.object(
            cli,
            "close_seed_raw_attempt",
            return_value=self.seed_outcome(),
        ) as close, self.assertRaisesRegex(
            cli.BoundedPilotWorkerError, "fingerprint mismatch"
        ):
            cli._run_seed_segment(self.args(), resume=False, clock=lambda: 0.0)
        self.assertFalse(close.call_args.kwargs["exact_seed_read"])
        self.assertEqual(
            close.call_args.kwargs["failure_type"], "BoundedPilotWorkerError"
        )

    def test_seed_segment_checks_postverify_soft_and_hard_runtime(self):
        context = self.segment_context()
        worker_result = SimpleNamespace(
            checkpoint_path=str(self.checkpoints / "checkpoint.json"),
            incomplete_reason="planned_seed_checkpoint",
            status="incomplete",
            errors=(),
            resources={"new_raw_read_bytes": 10, "database_writes": 0},
        )
        verification = {
            "position": {"phase": "seed_rib"},
            "seed_progress": {"next_record_ordinal": 1},
            "checkpoint_sequence": 1,
            "checkpoint_fingerprint_sha256": "f" * 64,
        }
        for boundary, expected_exit in ((540.0, 4), (600.0, 3)):
            values = iter((0.0, 0.0, 420.0, boundary))
            with self.subTest(boundary=boundary), mock.patch.object(
                cli, "_seed_context", return_value=context
            ), mock.patch.object(
                cli,
                "reserve_seed_raw_attempt",
                return_value=self.seed_reservation(),
            ), mock.patch.object(
                cli,
                "close_seed_raw_attempt",
                return_value=self.seed_outcome(),
            ), mock.patch.object(
                cli, "run_bounded_pilot_worker", return_value=worker_result
            ), mock.patch.object(
                cli, "verify_full_seed_checkpoint", return_value=verification
            ):
                result, exit_code = cli._run_seed_segment(
                    self.args(), resume=False, clock=lambda: next(values)
                )
            self.assertEqual(exit_code, expected_exit)
            self.assertFalse(result["ok"])
            self.assertTrue(result["process_runtime"]["planned_checkpoint_reached"])

    def test_seed_segment_refuses_worker_when_remaining_budget_is_exhausted(self):
        context = self.segment_context()
        for boundary, expected_exit in ((420.0, 4), (540.0, 4), (600.0, 3)):
            values = iter((0.0, boundary))
            with self.subTest(boundary=boundary), mock.patch.object(
                cli, "_seed_context", return_value=context
            ), mock.patch.object(
                cli,
                "run_bounded_pilot_worker",
                side_effect=AssertionError("预算耗尽不得启动 worker"),
            ):
                result, exit_code = cli._run_seed_segment(
                    self.args(), resume=False, clock=lambda: next(values)
                )
            self.assertEqual(exit_code, expected_exit)
            self.assertEqual(result["segment_state"], "worker_not_started")

    def test_seed_start_and_resume_fail_closed_under_concurrent_execution_lock(self):
        script = "\n".join(
            (
                "import sys",
                "from dev.data_quality.rrc25_iran_research import _seed_execution_lock",
                "with _seed_execution_lock(sys.argv[1]):",
                "    print('LOCKED', flush=True)",
                "    sys.stdin.read(1)",
            )
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(self.root / "prepared")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "LOCKED")
            for resume in (False, True):
                with self.subTest(resume=resume), mock.patch.object(
                    cli,
                    "_seed_context",
                    side_effect=AssertionError("锁冲突时不得加载执行上下文"),
                ), self.assertRaisesRegex(
                    cli.SeedWorkflowError,
                    "已有 seed start/resume/reconcile",
                ):
                    cli._run_seed_segment(
                        self.args(), resume=resume, clock=lambda: 0.0
                    )
            with mock.patch.object(
                cli,
                "_run_seed_spool_retirement_locked",
                side_effect=AssertionError("锁冲突时不得进入 spool 退役"),
            ), self.assertRaisesRegex(
                cli.SeedWorkflowError,
                "已有 seed start/resume/reconcile",
            ):
                cli._run_seed_spool_retirement(self.args(), clock=lambda: 0.0)
        finally:
            if process.stdin is not None:
                process.stdin.write("x")
                process.stdin.flush()
            process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0)

    def test_seed_workspace_reconcile_isolates_killed_partial_spool(self):
        context = self.segment_context()
        context["seed_reconciliation"] = {
            "reconciled": True,
            "outcome": "bounded_after_process_termination",
        }
        attestation = context["seed_spool_attestation"]
        spool_name = (
            "seed-spool."
            f"{attestation['semantic_fingerprint_sha256'][:16]}."
            f"{attestation['decompressed']['sha256'][:16]}.mrt"
        )
        partial = self.checkpoints / f".{spool_name}.tmp-123-deadbeef"
        partial.write_bytes(b"partial-spool")
        args = self.args()
        args.quarantine_directory = str(self.root / "seed-quarantine")
        with mock.patch.object(cli, "_seed_context", return_value=context):
            result = cli._run_seed_workspace_reconciliation(args)

        self.assertTrue(result["quarantine_performed"])
        self.assertEqual(result["recommended_action"], "seed-start")
        self.assertEqual(
            result["workspace_state"], "orphan_isolated_clean_start_ready"
        )
        self.assertFalse(partial.exists())
        quarantine = Path(result["quarantine_directory"])
        self.assertEqual((quarantine / partial.name).read_bytes(), b"partial-spool")
        self.assertTrue((quarantine / "ISOLATION.json").is_file())
        self.assertEqual(tuple(self.checkpoints.iterdir()), ())

    def test_seed_workspace_reconcile_preserves_checkpoint_and_routes_resume(self):
        context = self.segment_context()
        context["seed_reconciliation"] = {
            "reconciled": True,
            "outcome": "bounded_after_process_termination",
        }
        attestation = context["seed_spool_attestation"]
        spool_name = (
            "seed-spool."
            f"{attestation['semantic_fingerprint_sha256'][:16]}."
            f"{attestation['decompressed']['sha256'][:16]}.mrt"
        )
        spool = self.checkpoints / spool_name
        spool.write_bytes(b"verified-spool")
        checkpoint = self.checkpoints / (
            "rsel_v1_fixture.worker.0001.full-seed.ffffffffffffffff.json.gz"
        )
        checkpoint.write_bytes(b"verified-checkpoint")
        verification = {
            "checkpoint_sequence": 1,
            "checkpoint_fingerprint_sha256": "f" * 64,
            "seed_spool": {"file_name": spool_name},
        }
        args = self.args()
        args.quarantine_directory = str(self.root / "unused-quarantine")
        with mock.patch.object(
            cli, "_seed_context", return_value=context
        ), mock.patch.object(
            cli, "verify_full_seed_checkpoint", return_value=verification
        ):
            result = cli._run_seed_workspace_reconciliation(args)

        self.assertFalse(result["quarantine_performed"])
        self.assertEqual(result["recommended_action"], "seed-resume")
        self.assertEqual(result["resume_checkpoint"], str(checkpoint.resolve()))
        self.assertTrue(checkpoint.is_file())
        self.assertTrue(spool.is_file())
        self.assertFalse(Path(args.quarantine_directory).exists())

    def test_completed_seed_resume_is_verified_noop(self):
        prior = {
            "checkpoint_fingerprint_sha256": "1" * 64,
            "position": {"phase": "updates"},
            "seed_progress": {"next_record_ordinal": 9},
            "resources": {"new_raw_read_bytes": 10},
        }
        context = self.segment_context(prior=prior)
        with mock.patch.object(cli, "_seed_context", return_value=context), mock.patch.object(
            cli,
            "run_bounded_pilot_worker",
            side_effect=AssertionError("completed seed 不得重启 worker"),
        ):
            result, exit_code = cli._run_seed_segment(
                self.args(), resume=True, clock=lambda: 0.0
            )
        self.assertEqual(exit_code, 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["segment_state"], "already_complete_noop")
        self.assertFalse(result["opens_raw_mrt"])

    def test_seed_gate_counts_existing_checkpoint_bytes_at_atomic_boundary(self):
        attestation = json.loads(
            self.paths["attestation"].read_text(encoding="utf-8")
        )
        (self.checkpoints / "old.checkpoint").write_bytes(b"x" * 10)
        profile = json.loads(json.dumps(self.profile))
        profile["resource_limits"]["max_temporary_bytes"] = (
            cli.MAX_SEED_CHECKPOINT_BYTES + 100 + 10
        )
        gate = cli._seed_write_gate(
            profile=profile,
            checkpoint_directory=self.checkpoints,
            seed_size_bytes=10,
            seed_spool_attestation=attestation,
            prior_raw_read_bytes=0,
            planned_seed_checkpoint_seconds=420,
            resume=False,
        )
        self.assertEqual(gate["decision"], "approval_required")
        self.assertIn(
            "temporary_space_hard_limit_reached",
            {row["code"] for row in gate["findings"]},
        )

    def test_resume_estimate_allowed_but_runtime_exact_gate_rejects_growth(self):
        attestation = json.loads(
            self.paths["attestation"].read_text(encoding="utf-8")
        )
        prior = self.checkpoints / "prior.json"
        prior.write_bytes(b"x" * 100)
        projected = (
            100
            + 100
            + cli.SEED_RESUME_CHECKPOINT_GROWTH_RESERVE_BYTES
        )
        profile = json.loads(json.dumps(self.profile))
        profile["resource_limits"]["max_temporary_bytes"] = projected + 1
        gate = cli._seed_write_gate(
            profile=profile,
            checkpoint_directory=self.checkpoints,
            seed_size_bytes=10,
            seed_spool_attestation=attestation,
            prior_raw_read_bytes=10,
            planned_seed_checkpoint_seconds=420,
            resume=True,
            prior_checkpoint_size_bytes=100,
        )
        self.assertTrue(gate["execution_allowed"])
        context = {
            "resources": {"peak_temporary_bytes": 100},
            "large_payload": "x"
            * (cli.SEED_RESUME_CHECKPOINT_GROWTH_RESERVE_BYTES + 1024),
        }
        # 生产使用 level=1；这里固定 level=0 构造“压缩后仍超过预留”的
        # 确定性边界，证明最终门按真实落盘字节而不是未压缩估计放行。
        with mock.patch.object(
            bounded_pilot_worker, "_FULL_SEED_CHECKPOINT_GZIP_LEVEL", 0
        ), self.assertRaisesRegex(
            bounded_pilot_worker.BoundedPilotWorkerError,
            "写入瞬间总量",
        ):
            bounded_pilot_worker._publish_full_seed_checkpoint(
                self.checkpoints,
                selection_id="rsel_v1_fixture",
                sequence=2,
                context=context,
                maximum_temporary_bytes=projected + 1,
            )
        self.assertEqual(tuple(self.checkpoints.iterdir()), (prior,))

    def test_explicit_checkpoint_archive_copies_receipts_then_removes_old(self):
        history = self.root / "history"
        history.mkdir()
        old_path = self.checkpoints / "old.json"
        successor_path = self.checkpoints / "successor.json"
        old_path.write_bytes(b"old-checkpoint\n")
        successor_path.write_bytes(b"successor-checkpoint\n")
        old = self.full_seed_verification(sequence=1, ordinal=10, offset=100)
        successor = self.full_seed_verification(
            sequence=2, ordinal=20, offset=200
        )
        args = self.args()
        args.old_checkpoint = str(old_path)
        args.successor_checkpoint = str(successor_path)
        args.history_directory = str(history)
        args.dry_run = False
        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value={}
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            side_effect=(old, successor),
        ) as verifier:
            result = cli._run_seed_checkpoint_archive(args)

        self.assertEqual(verifier.call_count, 2)
        self.assertTrue(result["active_removed"])
        self.assertFalse(old_path.exists())
        self.assertEqual(Path(result["history_path"]).read_bytes(), b"old-checkpoint\n")
        self.assertTrue(Path(result["receipt_path"]).is_file())
        self.assertEqual(successor_path.read_bytes(), b"successor-checkpoint\n")
        self.assertEqual(result["released_active_temporary_bytes"], 15)

    def test_checkpoint_archive_failure_before_unlink_preserves_active_old(self):
        history = self.root / "history"
        history.mkdir()
        old_path = self.checkpoints / "old.json"
        successor_path = self.checkpoints / "successor.json"
        old_path.write_bytes(b"old")
        successor_path.write_bytes(b"new")
        args = self.args()
        args.old_checkpoint = str(old_path)
        args.successor_checkpoint = str(successor_path)
        args.history_directory = str(history)
        args.dry_run = False
        verifications = (
            self.full_seed_verification(sequence=1, ordinal=1, offset=10),
            self.full_seed_verification(sequence=2, ordinal=2, offset=20),
        )
        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value={}
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            side_effect=verifications,
        ), mock.patch.object(
            cli,
            "write_canonical_json",
            side_effect=OSError("receipt failure"),
        ), self.assertRaisesRegex(OSError, "receipt failure"):
            cli._run_seed_checkpoint_archive(args)
        self.assertEqual(old_path.read_bytes(), b"old")
        self.assertEqual(successor_path.read_bytes(), b"new")

    def test_checkpoint_archive_dry_run_reports_logical_release_without_writes(self):
        history = self.root / "history"
        history.mkdir()
        old_path = self.checkpoints / "old.json"
        successor_path = self.checkpoints / "successor.json"
        old_path.write_bytes(b"12345")
        successor_path.write_bytes(b"67890")
        args = self.args()
        args.old_checkpoint = str(old_path)
        args.successor_checkpoint = str(successor_path)
        args.history_directory = str(history)
        args.dry_run = True
        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value={}
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            side_effect=(
                self.full_seed_verification(sequence=1, ordinal=1, offset=10),
                self.full_seed_verification(sequence=2, ordinal=2, offset=20),
            ),
        ):
            result = cli._run_seed_checkpoint_archive(args)
        self.assertFalse(result["active_removed"])
        self.assertEqual(result["would_release_active_temporary_bytes"], 5)
        self.assertTrue(old_path.exists())
        self.assertTrue(successor_path.exists())
        self.assertEqual(tuple(history.iterdir()), ())

    def _spool_retirement_case(self, *, phase="updates", corrupt_spool=False):
        retirement = self.root / "retirement"
        retirement.mkdir()
        checkpoint = self.checkpoints / (
            "rsel_v1_fixture.worker.0002.full-seed.ffffffffffffffff.json"
        )
        checkpoint.write_bytes(b"checkpoint")
        spool_name = "seed-spool.fixture.mrt"
        spool_path = self.checkpoints / spool_name
        spool_bytes = b"bad-spool" if corrupt_spool else b"spool"
        spool_path.write_bytes(spool_bytes)
        expected_spool_sha = hashlib.sha256(b"spool").hexdigest()
        raw_bytes = b"compressed-raw"
        raw_path = self.raw_root / "seed.gz"
        raw_path.write_bytes(raw_bytes)
        verification = self.full_seed_verification(
            sequence=2,
            ordinal=20,
            offset=len(b"spool"),
            phase=phase,
            spool_name=spool_name,
            spool_sha=expected_spool_sha,
            spool_size=len(b"spool"),
        )
        context = {
            "profile": self.profile,
            "selection": {
                "selection_id": "rsel_v1_fixture",
                "roles": {
                    "state_seed_rib": {
                        "artifact_id": "art_v1_fixture",
                        "relative_path": "seed.gz",
                        "file_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                        "size_bytes": len(raw_bytes),
                    }
                },
            },
            "seed_spool_attestation": {
                "decompressed": {
                    "sha256": expected_spool_sha,
                    "size_bytes": len(b"spool"),
                }
            },
        }
        args = self.args()
        args.checkpoint = str(checkpoint)
        args.retirement_directory = str(retirement)
        args.dry_run = False
        return args, context, verification, checkpoint, spool_path, raw_path

    def test_seed_retire_spool_requires_complete_seed(self):
        args, context, verification, _checkpoint, spool, _raw = (
            self._spool_retirement_case(phase="seed_rib")
        )
        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value=context
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            return_value=verification,
        ), self.assertRaisesRegex(cli.SeedWorkflowError, "已完成 seed"):
            cli._run_seed_spool_retirement(args)
        self.assertTrue(spool.exists())

    def test_seed_retire_spool_rejects_hash_mismatch(self):
        args, context, verification, _checkpoint, spool, _raw = (
            self._spool_retirement_case(corrupt_spool=True)
        )
        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value=context
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            return_value=verification,
        ), self.assertRaisesRegex(cli.SeedWorkflowError, "spool SHA/size"):
            cli._run_seed_spool_retirement(args)
        self.assertTrue(spool.exists())

    def test_seed_retire_spool_rejects_missing_compressed_raw(self):
        args, context, verification, _checkpoint, spool, raw = (
            self._spool_retirement_case()
        )
        raw.unlink()
        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value=context
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            return_value=verification,
        ), self.assertRaisesRegex(cli.SeedWorkflowError, "原始制品不存在"):
            cli._run_seed_spool_retirement(args)
        self.assertTrue(spool.exists())

    def test_seed_retire_spool_rejects_non_latest_checkpoint(self):
        args, context, verification, _checkpoint, spool, _raw = (
            self._spool_retirement_case()
        )
        newer = self.checkpoints / (
            "rsel_v1_fixture.worker.0003.full-seed.eeeeeeeeeeeeeeee.json"
        )
        newer.write_bytes(b"newer")
        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value=context
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            return_value=verification,
        ), self.assertRaisesRegex(cli.SeedWorkflowError, "不是.*最新"):
            cli._run_seed_spool_retirement(args)
        self.assertTrue(spool.exists())

    def test_seed_retire_spool_publishes_receipt_before_unlink(self):
        args, context, verification, checkpoint, spool, raw = (
            self._spool_retirement_case()
        )
        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value=context
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            return_value=verification,
        ):
            result = cli._run_seed_spool_retirement(args)
        self.assertTrue(result["spool_removed"])
        self.assertTrue(result["recoverable_by_rebuild_from_compressed_raw"])
        self.assertEqual(
            result["resource_accounting"][
                "retirement_verification_new_raw_read_bytes"
            ],
            len(b"compressed-raw"),
        )
        self.assertFalse(spool.exists())
        self.assertTrue(checkpoint.exists())
        self.assertTrue(raw.exists())
        self.assertTrue(Path(result["receipt_path"]).is_file())

    def test_seed_retire_spool_receipt_failure_preserves_spool(self):
        args, context, verification, _checkpoint, spool, _raw = (
            self._spool_retirement_case()
        )
        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value=context
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            return_value=verification,
        ), mock.patch.object(
            cli,
            "write_canonical_json",
            side_effect=OSError("receipt failure"),
        ), self.assertRaisesRegex(OSError, "receipt failure"):
            cli._run_seed_spool_retirement(args)
        self.assertTrue(spool.exists())

    def test_seed_retire_spool_resumes_after_success_receipt_without_raw_reread(
        self,
    ):
        args, context, verification, _checkpoint, spool, raw = (
            self._spool_retirement_case()
        )
        original_boundary = cli._raise_if_seed_runtime_boundary

        def interrupt_after_success_receipt(**kwargs):
            if kwargs["phase"] == "seed_spool_retirement_before_spool_unlink":
                observation = cli._process_runtime_observation(
                    clock=lambda: 540.0,
                    process_started_at=0.0,
                    planned_seconds=420.0,
                    limits=kwargs["limits"],
                )
                raise cli._SeedRuntimeBoundaryError(
                    phase=kwargs["phase"],
                    bytes_read=kwargs["bytes_read"],
                    observation=observation,
                )
            return original_boundary(**kwargs)

        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value=context
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            return_value=verification,
        ), mock.patch.object(
            cli,
            "_raise_if_seed_runtime_boundary",
            side_effect=interrupt_after_success_receipt,
        ), self.assertRaises(cli.SeedSpoolRetirementAttemptError) as caught:
            cli._run_seed_spool_retirement(args, clock=lambda: 0.0)

        self.assertTrue(spool.exists())
        self.assertTrue(raw.exists())
        success_receipt = Path(caught.exception.result["success_receipt_path"])
        self.assertTrue(success_receipt.is_file())
        attempts_before = tuple(
            success_receipt.parent.glob(
                f"{spool.name}.raw-verification-attempt.*.json"
            )
        )
        self.assertEqual(len(attempts_before), 1)
        original_attempt = json.loads(
            attempts_before[0].read_text(encoding="utf-8")
        )
        extra_semantic = {
            key: value
            for key, value in original_attempt.items()
            if key not in {"schema_version", "receipt_fingerprint_sha256"}
        }
        extra_semantic["attempt_id"] = "99999999T999999Z-extra"
        extra_attempt = cli._spool_retirement_attempt_receipt_payload(
            extra_semantic
        )
        extra_path = success_receipt.parent / (
            f"{spool.name}.raw-verification-attempt."
            f"{extra_semantic['attempt_id']}.json"
        )
        extra_path.write_text(
            cli.canonical_json(extra_attempt) + "\n", encoding="utf-8"
        )
        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value=context
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            return_value=verification,
        ), self.assertRaisesRegex(cli.SeedWorkflowError, "额外 raw attempt"):
            cli._run_seed_spool_retirement(args, clock=lambda: 0.0)
        extra_path.unlink()

        original_hash = cli._hash_stable_regular_file_with_runtime_gate
        hashed_paths = []

        def record_hash(path, **kwargs):
            resolved = Path(path).resolve()
            hashed_paths.append(resolved)
            if resolved == raw.resolve():
                self.fail("恢复退役不得重读压缩 seed 原件")
            return original_hash(path, **kwargs)

        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value=context
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            return_value=verification,
        ), mock.patch.object(
            cli,
            "_hash_stable_regular_file_with_runtime_gate",
            side_effect=record_hash,
        ):
            resumed = cli._run_seed_spool_retirement(
                args,
                clock=lambda: 0.0,
            )

        self.assertTrue(resumed["spool_removed"])
        self.assertTrue(resumed["idempotent_finalize_from_success_receipt"])
        self.assertFalse(resumed["opens_compressed_raw_for_hash_verification"])
        self.assertEqual(
            resumed["resource_accounting"]["new_raw_read_bytes_this_invocation"],
            0,
        )
        self.assertIn(spool.resolve(), hashed_paths)
        self.assertNotIn(raw.resolve(), hashed_paths)
        self.assertFalse(spool.exists())
        self.assertTrue(raw.exists())
        self.assertEqual(
            tuple(
                success_receipt.parent.glob(
                    f"{spool.name}.raw-verification-attempt.*.json"
                )
            ),
            attempts_before,
        )

    def test_seed_retire_admission_binds_known_size_and_same_process_clock(self):
        limits = cli.effective_resource_limits(self.profile)
        base_runtime = {
            "elapsed_seconds": 0.0,
            "soft_stop_reached": False,
            "hard_limit_reached": False,
        }
        admission = cli._seed_spool_retirement_raw_admission(
            compressed_size_bytes=426_797_681,
            process_runtime=base_runtime,
            limits=limits,
        )
        self.assertTrue(admission["allowed"])
        self.assertLess(admission["estimated_hash_seconds"], 420.0)
        self.assertEqual(
            admission["conservative_bytes_per_second"], 2 * 1024 * 1024
        )

        late_runtime = {
            **base_runtime,
            "elapsed_seconds": 400.0,
        }
        rejected = cli._seed_spool_retirement_raw_admission(
            compressed_size_bytes=426_797_681,
            process_runtime=late_runtime,
            limits=limits,
        )
        self.assertFalse(rejected["allowed"])
        self.assertEqual(
            rejected["reason"],
            "projected_same_process_runtime_reaches_540_second_soft_boundary",
        )

        exact_boundary_throughput = 426_797_681 / 420.0
        exact_boundary = cli._seed_spool_retirement_raw_admission(
            compressed_size_bytes=426_797_681,
            process_runtime=base_runtime,
            limits=limits,
            conservative_bytes_per_second=exact_boundary_throughput,
        )
        self.assertFalse(exact_boundary["allowed"])
        self.assertFalse(exact_boundary["strictly_below_artifact_admission"])

    def test_retirement_hash_observes_soft_and_hard_boundaries_midstream(self):
        path = self.root / "runtime-gated.bin"
        path.write_bytes(b"x" * (2 * 1024 * 1024))
        limits = cli.effective_resource_limits(self.profile)
        for boundary, expected_exit in ((540.0, 4), (600.0, 3)):
            values = iter((0.0, 0.0, boundary))
            with self.subTest(boundary=boundary), self.assertRaises(
                cli._SeedRuntimeBoundaryError
            ) as caught:
                cli._hash_stable_regular_file_with_runtime_gate(
                    path,
                    clock=lambda: next(values),
                    process_started_at=0.0,
                    limits=limits,
                    phase="compressed_seed_raw_hash_verification",
                )
            self.assertEqual(caught.exception.exit_code, expected_exit)
            self.assertEqual(caught.exception.bytes_read, 1024 * 1024)
            self.assertEqual(
                caught.exception.phase,
                "compressed_seed_raw_hash_verification",
            )

    def test_seed_retire_runtime_failure_preserves_spool_and_counts_retry(self):
        args, context, verification, _checkpoint, spool, raw = (
            self._spool_retirement_case()
        )
        limits = cli.effective_resource_limits(self.profile)
        runtime_zero = cli._process_runtime_observation(
            clock=lambda: 0.0,
            process_started_at=0.0,
            planned_seconds=420.0,
            limits=limits,
        )
        runtime_soft = cli._process_runtime_observation(
            clock=lambda: 540.0,
            process_started_at=0.0,
            planned_seconds=420.0,
            limits=limits,
        )

        def gated_hash(path, **_kwargs):
            if Path(path) == spool.resolve():
                return (
                    hashlib.sha256(spool.read_bytes()).hexdigest(),
                    spool.stat().st_size,
                    cli._regular_file_identity(spool.resolve()),
                    runtime_zero,
                )
            self.assertEqual(Path(path), raw.resolve())
            raise cli._SeedRuntimeBoundaryError(
                phase="compressed_seed_raw_hash_verification",
                bytes_read=5,
                observation=runtime_soft,
            )

        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value=context
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            return_value=verification,
        ), mock.patch.object(
            cli,
            "_hash_stable_regular_file_with_runtime_gate",
            side_effect=gated_hash,
        ), self.assertRaises(cli.SeedSpoolRetirementAttemptError) as caught:
            cli._run_seed_spool_retirement(args, clock=lambda: 0.0)

        failure = caught.exception.result
        self.assertEqual(caught.exception.exit_code, 4)
        self.assertTrue(spool.exists())
        self.assertTrue(raw.exists())
        self.assertFalse(failure["spool_removed"])
        self.assertEqual(
            failure["failed_attempt_raw_accounting"][
                "observed_raw_bytes_read_this_attempt"
            ],
            5,
        )
        self.assertEqual(
            failure["failed_attempt_raw_accounting"][
                "accounted_raw_bytes_this_attempt"
            ],
            len(b"compressed-raw"),
        )
        attempt_path = Path(failure["attempt_receipt"]["path"])
        self.assertTrue(attempt_path.is_file())
        self.assertFalse(Path(failure["receipt_path"]).exists())

        args.dry_run = True
        with mock.patch.object(
            cli, "_load_seed_verification_context", return_value=context
        ), mock.patch.object(
            cli,
            "_verify_seed_checkpoint_with_context",
            return_value=verification,
        ):
            retry_plan = cli._run_seed_spool_retirement(
                args,
                clock=lambda: 0.0,
            )
        self.assertFalse(retry_plan["opens_compressed_raw_for_hash_verification"])
        self.assertEqual(
            retry_plan["resource_accounting"]["prior_retirement_attempt_count"],
            1,
        )
        self.assertEqual(
            retry_plan["resource_accounting"][
                "prior_failed_or_unknown_attempt_reserved_bytes"
            ],
            len(b"compressed-raw"),
        )
        self.assertEqual(
            retry_plan["resource_accounting"][
                "cumulative_new_raw_read_bytes_before_retirement_verification"
            ],
            verification["resources"]["new_raw_read_bytes"]
            + len(b"compressed-raw"),
        )

    def test_parser_exposes_seed_lifecycle(self):
        parser = cli.build_parser()
        choices = parser._subparsers._group_actions[0].choices
        self.assertTrue(
            {
                "seed-dry-run",
                "seed-start",
                "seed-resume",
                "seed-verify",
                "seed-archive-checkpoint",
                "seed-retire-spool",
            }.issubset(choices)
        )
        for command in (
            "seed-dry-run",
            "seed-start",
            "seed-resume",
            "seed-verify",
            "seed-archive-checkpoint",
            "seed-retire-spool",
        ):
            actions = {
                action.dest: action for action in choices[command]._actions
            }
            self.assertTrue(actions["seed_spool_attestation"].required)
        for command in ("seed-dry-run", "seed-start", "seed-resume"):
            actions = {action.dest: action for action in choices[command]._actions}
            self.assertNotIn("prior_new_raw_bytes", actions)
            self.assertTrue(actions["prepared_directory"].required)
            self.assertTrue(actions["probe_ledger_terminal"].required)


if __name__ == "__main__":
    unittest.main()
