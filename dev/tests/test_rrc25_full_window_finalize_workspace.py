from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.data_pipeline.route_event import ParsedMrtRecord
from backend.data_pipeline.research.rrc25_country_outage import full_window_journal as journal
from backend.data_pipeline.research.rrc25_country_outage import full_window_finalize as finalizer
from backend.data_pipeline.research.rrc25_country_outage import full_window_finalize_workspace as workspace
from backend.data_pipeline.research.rrc25_country_outage.full_window_worker import (
    artifact_descriptor_from_manifest,
    initialize_compact_state_from_seed,
    run_one_update_artifact,
)
from dev.tests.test_rrc25_full_window_worker import (
    FakeStream,
    advancing_clock,
    element,
    manifest as worker_manifest,
    mapping as worker_mapping,
    parser_attestation,
    path as as_path,
    revised_mapping as worker_revised_mapping,
    seed_state,
    update_frame,
)
from dev.tests.test_rrc25_full_window_segment_product import (
    _completed_fixture as _completed_product_fixture,
)


RUN_ID = "research_run_v1_" + "3" * 24


class SimulatedWorkspaceCrash(RuntimeError):
    pass


def _completed_journal(parent: Path, *, slots: int = 3):
    seed, seed_vp = seed_state()
    compatible = worker_mapping()
    revised = worker_revised_mapping()
    compact = initialize_compact_state_from_seed(
        seed,
        compatible_mapping=compatible,
        revised_mapping=revised,
        tracked_prefixes=("203.0.113.0/24",),
        expected_vp_ids=(seed_vp,),
        vp_population_source_sha256="7" * 64,
    )
    bindings = {
        "profile_sha256": "1" * 64,
        "input_selection_sha256": "2" * 64,
        "code_sha256": "3" * 64,
        "mapping_sha256": "4" * 64,
    }
    root = parent / "journal"
    head = journal.initialize_full_window_journal(
        root,
        run_id=RUN_ID,
        bindings=bindings,
        total_artifacts=slots,
        initial_compact_state=compact,
        preliminary_seed_read_bytes=1,
        seed_artifact_read_bytes=1,
        additional_pre_update_raw_read_bytes=0,
        bootstrap_bytes_per_second=1_000_000,
        genesis_shards=(
            journal.ShardInput(
                "seed_bootstrap_attestation", ({"initial_compact_state": compact},)
            ),
            journal.ShardInput("seed_route_events", ()),
            journal.ShardInput("seed_raw_record_refs", ()),
        ),
    )
    for index in range(slots):
        artifact = worker_manifest(index)
        descriptor = artifact_descriptor_from_manifest(index, artifact)
        token = journal.begin_artifact_attempt(head, descriptor)
        raw = update_frame(index, peer_ip="192.0.2.1")
        records = (
            ParsedMrtRecord(
                0,
                0,
                raw,
                (
                    element(
                        index,
                        peer_ip="192.0.2.1",
                        prefix="203.0.113.0/24",
                        as_path=as_path(64500, 65001),
                    ),
                ),
            ),
        )
        head = run_one_update_artifact(
            head,
            token,
            artifact_manifest_row=artifact,
            compatible_mapping=compatible,
            revised_mapping=revised,
            raw_retention_membership=lambda asn: asn in {65001, 65002},
            update_record_stream_factory=lambda _row, rows=records, item=artifact: FakeStream(
                rows, item
            ),
            parser_attestation=parser_attestation(),
            clock=advancing_clock(),
        ).head
    return root, compatible, revised, bindings


class FullWindowFinalizeWorkspaceTests(unittest.TestCase):
    def test_initialize_rejects_default_production_root_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            journal_root, _compatible, _revised, bindings = _completed_journal(
                parent, slots=1
            )
            production_root = parent / "production"
            production_root.mkdir()
            target = production_root / "finalize-workspace"
            with (
                patch.object(
                    workspace,
                    "_GLOBAL_MUTATION_PROTECTED_ROOTS",
                    (production_root.resolve(),),
                ),
                self.assertRaisesRegex(
                    workspace.FullWindowFinalizeWorkspaceError,
                    "生产根双向重叠",
                ),
            ):
                workspace.initialize_finalization_workspace(
                    target,
                    journal_root=journal_root,
                    bindings=bindings,
                )
            self.assertFalse(target.exists())

    def test_create_only_midflight_gate_rejects_exact_decimal_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "existing").write_bytes(b"1234567890")
            payload = b"next-payload"
            exact_boundary = workspace._tree_size(root) + len(payload)
            with self.assertRaisesRegex(
                workspace.FullWindowFinalizeWorkspaceError,
                "5GB 排他边界",
            ):
                workspace._create_bytes(
                    root / "published",
                    payload,
                    temporary_root=root,
                    maximum_temporary_bytes=exact_boundary,
                )
            self.assertFalse((root / "published").exists())
            self.assertEqual(
                list(root.glob(".published.publish-tmp-*")),
                [],
            )

    def test_each_slot_decompresses_control_and_observation_once(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            journal_root, compatible, revised, bindings = _completed_journal(
                parent, slots=2
            )
            target = parent / "finalize-workspace"
            workspace.initialize_finalization_workspace(
                target, journal_root=journal_root, bindings=bindings
            )
            observed = {"control_records": 0, "record_observations": 0}
            original = finalizer._iter_shard_rows

            def counted(root, ref):
                kind = ref.get("kind")
                if kind in observed:
                    observed[kind] += 1
                yield from original(root, ref)

            with patch.object(finalizer, "_iter_shard_rows", side_effect=counted):
                result = workspace.run_finalization_workspace_segment(
                    target,
                    compatible_mapping=compatible,
                    revised_mapping=revised,
                    max_slots=1,
                )
            self.assertEqual(result.completed_slots, 1)
            self.assertEqual(observed["control_records"], 1)
            self.assertEqual(observed["record_observations"], 1)

    def test_three_slots_kill_resume_seals_terminal_and_deep_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            journal_root, compatible, revised, bindings = _completed_journal(parent)
            target = parent / "finalize-workspace"
            workspace.initialize_finalization_workspace(
                target, journal_root=journal_root, bindings=bindings
            )

            def crash(phase: str, _root: Path) -> None:
                if phase == "after_segment_payload_publish":
                    raise SimulatedWorkspaceCrash(phase)

            with self.assertRaises(SimulatedWorkspaceCrash):
                workspace.run_finalization_workspace_segment(
                    target,
                    compatible_mapping=compatible,
                    revised_mapping=revised,
                    max_slots=2,
                    crash_hook=crash,
                )
            self.assertEqual(workspace._load_head(target)["sequence"], 0)
            self.assertTrue((target / "ACTIVE").is_file())

            first = workspace.run_finalization_workspace_segment(
                target,
                compatible_mapping=compatible,
                revised_mapping=revised,
                max_slots=1,
            )
            self.assertEqual(first.completed_slots, 1)
            self.assertFalse(first.sealed)
            self.assertFalse((target / "ACTIVE").exists())

            finished = workspace.run_finalization_workspace_segment(
                target,
                compatible_mapping=compatible,
                revised_mapping=revised,
                max_slots=3,
            )
            self.assertEqual(finished.completed_slots, 3)
            self.assertTrue(finished.sealed)
            verified = workspace.verify_finalization_workspace(target)
            self.assertEqual(verified["completed_slots"], 3)
            self.assertEqual(verified["record_observation_reread_count"], 0)
            self.assertEqual(
                verified["resource_accounting"]["database_write_operations"], 0
            )
            self.assertGreater(
                verified["resource_accounting"][
                    "cumulative_record_observation_bytes_read"
                ],
                0,
            )

    def test_receipt_published_before_kill_reconciles_without_observation_read(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            journal_root, compatible, revised, bindings = _completed_journal(
                parent, slots=2
            )
            target = parent / "finalize-workspace"
            workspace.initialize_finalization_workspace(
                target, journal_root=journal_root, bindings=bindings
            )

            def crash(phase: str, _root: Path) -> None:
                if phase == "after_segment_receipt_publish":
                    raise SimulatedWorkspaceCrash(phase)

            with self.assertRaises(SimulatedWorkspaceCrash):
                workspace.run_finalization_workspace_segment(
                    target,
                    compatible_mapping=compatible,
                    revised_mapping=revised,
                    crash_hook=crash,
                )
            with patch.object(
                finalizer,
                "_read_record_observation_shard_once",
                side_effect=AssertionError("reconcile 不得重读 observation"),
            ):
                result = workspace.reconcile_finalization_workspace(target)
            self.assertEqual(result["state"], "receipt_reconciled_to_head")
            self.assertEqual(result["head"]["sequence"], 1)

    def test_planned_stop_and_lock_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            journal_root, compatible, revised, bindings = _completed_journal(
                parent, slots=2
            )
            target = parent / "finalize-workspace"
            workspace.initialize_finalization_workspace(
                target, journal_root=journal_root, bindings=bindings
            )
            clock = iter((0.0, 421.0)).__next__
            stopped = workspace.run_finalization_workspace_segment(
                target,
                compatible_mapping=compatible,
                revised_mapping=revised,
                max_slots=2,
                monotonic=clock,
            )
            self.assertEqual(stopped.completed_slots, 0)
            self.assertEqual(
                stopped.stop_reason, "child_planned_stop_before_new_slot"
            )
            with workspace.finalization_workspace_lock(target):
                with self.assertRaises(workspace.FinalizationWorkspaceLocked):
                    workspace.reconcile_finalization_workspace(target)

    def test_tampered_terminal_segment_is_rejected_by_receipt_only_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            journal_root, compatible, revised, bindings = _completed_journal(
                parent, slots=2
            )
            target = parent / "finalize-workspace"
            workspace.initialize_finalization_workspace(
                target, journal_root=journal_root, bindings=bindings
            )
            workspace.run_finalization_workspace_segment(
                target,
                compatible_mapping=compatible,
                revised_mapping=revised,
                max_slots=2,
            )
            head = workspace._load_head(target)
            payload = target / head["segment_payload_refs"][-1]["path"]
            payload.write_bytes(payload.read_bytes() + b"tamper")
            with self.assertRaisesRegex(
                workspace.FullWindowFinalizeWorkspaceError, "hash/size|超过限制"
            ):
                workspace.verify_finalization_workspace(target)

    def test_nonterminal_segment_tamper_is_rejected_during_bounded_assembly_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target, _journal_root, frozen = _completed_product_fixture(parent)
            package = parent / "tampered-package"
            head = workspace._load_head(target)
            payload = target / head["segment_payload_refs"][0]["path"]
            payload.write_bytes(payload.read_bytes() + b"tamper")
            with self.assertRaisesRegex(
                workspace.FullWindowFinalizeWorkspaceError,
                "hash/size|超过限制",
            ):
                workspace.assemble_finalized_package_from_workspace(
                    target, package, **frozen
                )
            self.assertFalse(package.exists())

    def test_seal_workspace_and_package_verify_do_not_walk_full_segment_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            journal_root, compatible, revised, bindings = _completed_journal(
                parent, slots=3
            )
            target = parent / "finalize-workspace"
            workspace.initialize_finalization_workspace(
                target, journal_root=journal_root, bindings=bindings
            )
            workspace.run_finalization_workspace_segment(
                target,
                compatible_mapping=compatible,
                revised_mapping=revised,
                max_slots=2,
            )
            with patch.object(
                workspace,
                "_verify_segment_chain",
                side_effect=AssertionError("seal/verify 不得重扫 1..N segment"),
            ):
                finished = workspace.run_finalization_workspace_segment(
                    target,
                    compatible_mapping=compatible,
                    revised_mapping=revised,
                    max_slots=1,
                )
                self.assertTrue(finished.sealed)
                verified = workspace.verify_finalization_workspace(target)
                product_parent = parent / "product-fixture"
                product_parent.mkdir()
                product_target, _product_journal, frozen = (
                    _completed_product_fixture(product_parent)
                )
                package = parent / "segment-package"
                assembled = workspace.assemble_finalized_package_from_workspace(
                    product_target, package, **frozen
                )
                package_verified = workspace.verify_workspace_assembled_package(
                    package
                )
            self.assertEqual(verified["full_segment_chain_reread_count"], 0)
            self.assertEqual(assembled["full_segment_chain_reread_count"], 0)
            self.assertEqual(
                package_verified["full_segment_chain_reread_count"], 0
            )

    def test_seal_and_daily_workspace_verify_do_not_decompress_any_segment_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            journal_root, compatible, revised, bindings = _completed_journal(
                parent, slots=3
            )
            target = parent / "finalize-workspace"
            workspace.initialize_finalization_workspace(
                target, journal_root=journal_root, bindings=bindings
            )
            workspace.run_finalization_workspace_segment(
                target,
                compatible_mapping=compatible,
                revised_mapping=revised,
                max_slots=2,
            )
            def stop_after_last_head(phase: str, _path: Path) -> None:
                if phase == "after_head_publish":
                    raise SimulatedWorkspaceCrash("head-before-seal")

            with self.assertRaises(SimulatedWorkspaceCrash):
                workspace.run_finalization_workspace_segment(
                    target,
                    compatible_mapping=compatible,
                    revised_mapping=revised,
                    max_slots=1,
                    crash_hook=stop_after_last_head,
                )
            self.assertEqual(workspace._load_head(target)["sequence"], 3)
            with patch.object(
                workspace,
                "_load_segment_payload",
                side_effect=AssertionError(
                    "seal/daily verify 不得解压 segment payload"
                ),
            ):
                terminal, deep = workspace.seal_finalization_workspace(target)
                verified = workspace.verify_finalization_workspace(target)
            self.assertTrue(terminal.is_file())
            self.assertTrue(deep.is_file())
            self.assertEqual(
                verified["record_observation_reread_count"], 0
            )
            self.assertEqual(
                verified["full_segment_chain_reread_count"], 0
            )

    def test_strict_temporary_gate_rejects_equal_boundary_before_new_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            journal_root, compatible, revised, bindings = _completed_journal(
                parent, slots=1
            )
            target = parent / "finalize-workspace"
            workspace.initialize_finalization_workspace(
                target, journal_root=journal_root, bindings=bindings
            )
            occupied = workspace._tree_size(target)
            with self.assertRaisesRegex(
                workspace.FullWindowFinalizeWorkspaceError,
                "5GB 排他边界",
            ):
                workspace.run_finalization_workspace_segment(
                    target,
                    compatible_mapping=compatible,
                    revised_mapping=revised,
                    max_slots=1,
                    maximum_temporary_bytes=occupied,
                )
            self.assertFalse((target / "ACTIVE").exists())
            self.assertEqual(workspace._load_head(target)["sequence"], 0)

    def test_assembly_preflight_rejects_projected_five_gb_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target, _journal_root, frozen = _completed_product_fixture(parent)
            package = parent / "too-large-package"
            plan = workspace._build_complete_package_plan(
                target,
                maximum_projected_bytes=(
                    workspace.DEFAULT_MAX_TEMPORARY_BYTES
                ),
                **frozen,
            )
            with self.assertRaisesRegex(
                (workspace.FullWindowFinalizeWorkspaceError, ValueError),
                "5GB 排他边界|完整 v2 package",
            ):
                workspace.assemble_finalized_package_from_workspace(
                    target,
                    package,
                    maximum_temporary_bytes=plan.projected_regular_bytes,
                    **frozen,
                )
            self.assertFalse(package.exists())

    def test_torn_receipt_candidate_is_retired_for_safe_redo(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            journal_root, compatible, revised, bindings = _completed_journal(
                parent, slots=1
            )
            target = parent / "finalize-workspace"
            workspace.initialize_finalization_workspace(
                target, journal_root=journal_root, bindings=bindings
            )

            def crash(phase: str, _root: Path) -> None:
                if phase == "after_segment_payload_publish":
                    raise SimulatedWorkspaceCrash(phase)

            with self.assertRaises(SimulatedWorkspaceCrash):
                workspace.run_finalization_workspace_segment(
                    target,
                    compatible_mapping=compatible,
                    revised_mapping=revised,
                    crash_hook=crash,
                )
            torn = (
                target
                / "segments/receipts"
                / ("slot-0001-" + "0" * 64 + ".json")
            )
            torn.write_bytes(b'{"schema_version":')
            reconciled = workspace.reconcile_finalization_workspace(target)
            self.assertEqual(
                reconciled["state"],
                "torn_uncommitted_slot_retired_for_redo",
            )
            self.assertFalse(torn.exists())
            self.assertFalse((target / "ACTIVE").exists())
            resumed = workspace.run_finalization_workspace_segment(
                target,
                compatible_mapping=compatible,
                revised_mapping=revised,
            )
            self.assertTrue(resumed.sealed)

    def test_two_empty_directories_assemble_without_observation_reread(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target, _journal_root, frozen = _completed_product_fixture(parent)
            first_root = parent / "package-a"
            second_root = parent / "package-b"
            receipt_path = parent / "accepted-v2.json"
            with (
                patch.object(
                    finalizer,
                    "_read_record_observation_shard_once",
                    side_effect=AssertionError(
                        "assembly 不得读取 observation"
                    ),
                ),
                patch.object(
                    workspace,
                    "_build_complete_package_plan",
                    wraps=workspace._build_complete_package_plan,
                ) as build_plan,
            ):
                accepted = workspace.assemble_workspace_reproduction(
                    target,
                    reference_output_root=first_root,
                    reproduction_output_root=second_root,
                    acceptance_receipt_path=receipt_path,
                    **frozen,
                )
                verified = workspace.verify_workspace_reproduction_acceptance_receipt(
                    receipt_path
                )
                compatible_verified = finalizer.verify_reproduction_acceptance_receipt(
                    receipt_path
                )
            self.assertEqual(build_plan.call_count, 2)
            self.assertEqual(accepted["acceptance_state"], "accepted")
            self.assertEqual(
                accepted["reproduction_scope"],
                "independent_package_assembly_from_same_verified_finalization_segments",
            )
            self.assertEqual(
                accepted["semantic_core_sha256"], verified["semantic_core_sha256"]
            )
            self.assertTrue(
                accepted["checks"]["business_semantic_core_equal"]
            )
            self.assertTrue(
                accepted["checks"]["finalization_segment_core_equal"]
            )
            self.assertEqual(
                compatible_verified["schema_version"],
                "rrc25-full-window-reproduction-acceptance/v2",
            )
            first = workspace.verify_workspace_assembled_package(first_root)
            second = workspace.verify_workspace_assembled_package(second_root)
            required_paths = {
                "GENESIS",
                "TERMINAL",
                "DEEP-VERIFICATION",
                "segments/index.json",
                "metadata/finalization.json",
                "quality-and-accounting.json",
                "evidence/research-evidence-packages.jsonl.gz",
                "报告/RRC25伊朗国家路由中断事件复算与对账报告.md",
            }
            self.assertTrue(
                required_paths
                <= {
                    item["path"]
                    for item in first["manifest"]["contents"]
                }
            )
            self.assertFalse(
                any(
                    item["path"].startswith("journal-ancestry/")
                    for item in first["manifest"]["contents"]
                )
            )
            self.assertEqual(
                first["manifest"]["semantic_fingerprint_sha256"],
                second["manifest"]["semantic_fingerprint_sha256"],
            )
            self.assertEqual(first["record_observation_reread_count"], 0)
            self.assertEqual(second["record_observation_reread_count"], 0)

    def test_v2_acceptance_refuses_missing_complete_business_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target, _journal_root, frozen = _completed_product_fixture(parent)
            incomplete = dict(frozen)
            incomplete.pop("claim_inventory")
            receipt_path = parent / "must-not-exist.json"
            with self.assertRaises(TypeError):
                workspace.assemble_workspace_reproduction(
                    target,
                    reference_output_root=parent / "package-a",
                    reproduction_output_root=parent / "package-b",
                    acceptance_receipt_path=receipt_path,
                    **incomplete,
                )
            self.assertFalse(receipt_path.exists())
            self.assertFalse((parent / "package-a").exists())
            self.assertFalse((parent / "package-b").exists())

    def test_partial_assembly_checkpoint_resumes_after_420_style_stop_without_recopy(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target, _journal_root, frozen = _completed_product_fixture(parent)
            package = parent / "resumed-package"
            copy_starts = 0

            def stop_before_fourth_copy(phase: str, _path: Path) -> None:
                nonlocal copy_starts
                if phase != "before_assembly_copy":
                    return
                copy_starts += 1
                if copy_starts == 4:
                    raise SimulatedWorkspaceCrash("420-second-planned-stop")

            with self.assertRaises(SimulatedWorkspaceCrash):
                workspace.assemble_finalized_package_from_workspace(
                    target,
                    package,
                    publication_hook=stop_before_fourth_copy,
                    **frozen,
                )
            checkpoint = workspace._load_assembly_checkpoint(target)
            self.assertEqual(checkpoint["completed_copy_items"], 3)
            reconciled = workspace.reconcile_workspace_publication(
                target, **frozen
            )
            self.assertEqual(
                reconciled["state"],
                "partial_historical_staging_reusable",
            )
            copied_on_resume = []
            original_copy = workspace._materialize_complete_plan_item

            def counted_copy(*args, **kwargs):
                copied_on_resume.append(str(args[1].relative_path))
                return original_copy(*args, **kwargs)

            with patch.object(
                workspace,
                "_materialize_complete_plan_item",
                side_effect=counted_copy,
            ):
                result = workspace.assemble_finalized_package_from_workspace(
                    target, package, **frozen
                )
            self.assertTrue(result["resource_receipt_verified"])
            self.assertEqual(
                len(copied_on_resume),
                checkpoint["total_copy_items"] - 3,
            )
            self.assertFalse((target / "ASSEMBLY-CHECKPOINT").exists())
            self.assertFalse((target / "ASSEMBLY-ACTIVE").exists())

    def test_manifest_half_publish_and_acceptance_receipt_are_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target, _journal_root, frozen = _completed_product_fixture(parent)
            first_root = parent / "package-a"
            second_root = parent / "package-b"
            accepted_path = parent / "accepted-v2.json"
            original_create = workspace._create_bytes
            stopped = False

            def stop_before_sums(path, payload, **kwargs):
                nonlocal stopped
                if path.name == "package-manifest.json" and not stopped:
                    stopped = True
                    raise SimulatedWorkspaceCrash("manifest-only")
                return original_create(path, payload, **kwargs)

            with (
                patch.object(
                    workspace, "_create_bytes", side_effect=stop_before_sums
                ),
                self.assertRaises(SimulatedWorkspaceCrash),
            ):
                workspace.assemble_finalized_package_from_workspace(
                    target, first_root, **frozen
                )
            active = workspace._load_assembly_active(target)
            staging = Path(active["staging_root"])
            self.assertTrue((staging / "SHA256SUMS").is_file())
            self.assertFalse((staging / "package-manifest.json").exists())
            first = workspace.assemble_finalized_package_from_workspace(
                target, first_root, **frozen
            )
            self.assertTrue(first["resource_receipt_verified"])
            accepted = workspace.assemble_workspace_reproduction(
                target,
                reference_output_root=first_root,
                reproduction_output_root=second_root,
                acceptance_receipt_path=accepted_path,
                **frozen,
            )
            resumed = workspace.assemble_workspace_reproduction(
                target,
                reference_output_root=first_root,
                reproduction_output_root=second_root,
                acceptance_receipt_path=accepted_path,
                **frozen,
            )
            self.assertEqual(resumed, accepted)

    def test_daily_acceptance_verify_is_receipt_only_and_deep_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target, _journal_root, frozen = _completed_product_fixture(parent)
            first_root = parent / "package-a"
            second_root = parent / "package-b"
            accepted_path = parent / "accepted-v2.json"
            workspace.assemble_workspace_reproduction(
                target,
                reference_output_root=first_root,
                reproduction_output_root=second_root,
                acceptance_receipt_path=accepted_path,
                **frozen,
            )
            with (
                patch.object(
                    workspace,
                    "verify_published_package",
                    side_effect=AssertionError(
                        "日常 acceptance verify 不得深扫 package"
                    ),
                ),
                patch.object(
                    workspace,
                    "_load_segment_payload",
                    side_effect=AssertionError(
                        "日常 acceptance verify 不得解压 payload"
                    ),
                ),
            ):
                verified = (
                    workspace.verify_workspace_reproduction_acceptance_receipt(
                        accepted_path
                    )
                )
            self.assertEqual(verified["acceptance_state"], "accepted")
            with patch.object(
                workspace,
                "verify_workspace_assembled_package",
                wraps=workspace.verify_workspace_assembled_package,
            ) as deep:
                verified_deep = (
                    workspace.verify_workspace_reproduction_acceptance_receipt(
                        accepted_path, deep_content_walk=True
                    )
                )
            self.assertEqual(deep.call_count, 2)
            self.assertEqual(verified_deep["acceptance_state"], "accepted")

    def test_rename_crash_reconciles_missing_resource_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target, _journal_root, frozen = _completed_product_fixture(parent)
            package = parent / "package-after-crash"

            def crash(phase: str, _path: Path) -> None:
                if phase == "after_atomic_directory_publish":
                    raise SimulatedWorkspaceCrash(phase)

            with self.assertRaises(SimulatedWorkspaceCrash):
                workspace.assemble_finalized_package_from_workspace(
                    target, package, publication_hook=crash, **frozen
                )
            resource = workspace._package_resource_receipt_path(package)
            self.assertTrue(package.is_dir())
            self.assertFalse(resource.exists())
            torn_temporary = resource.parent / (
                f".{resource.name}.publish-tmp-deadbeef"
            )
            torn_temporary.write_bytes(b'{"schema_version":')
            reconciled = workspace.reconcile_workspace_publication(
                target, **frozen
            )
            self.assertEqual(
                reconciled["state"],
                "rename_after_publish_reconciled_resource_receipt",
            )
            self.assertTrue(resource.is_file())
            self.assertFalse(torn_temporary.exists())
            self.assertTrue(
                workspace.verify_workspace_assembled_package(package)["verified"]
            )

    def test_verified_historical_staging_is_reused_after_pre_rename_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target, _journal_root, frozen = _completed_product_fixture(parent)
            package = parent / "package-after-reuse"

            def stop(phase: str, _path: Path) -> None:
                if phase == "before_atomic_directory_publish":
                    raise SimulatedWorkspaceCrash(phase)

            with self.assertRaises(SimulatedWorkspaceCrash):
                workspace.assemble_finalized_package_from_workspace(
                    target, package, publication_hook=stop, **frozen
                )
            reconciled = workspace.reconcile_workspace_publication(
                target, **frozen
            )
            self.assertEqual(
                reconciled["state"], "verified_historical_staging_reusable"
            )
            with patch.object(
                workspace,
                "_build_assembly_staging",
                side_effect=AssertionError("已验 staging 应直接复用"),
            ):
                result = workspace.assemble_finalized_package_from_workspace(
                    target, package, **frozen
                )
            self.assertTrue(result["resource_receipt_verified"])
            self.assertTrue(package.is_dir())


if __name__ == "__main__":
    unittest.main()
