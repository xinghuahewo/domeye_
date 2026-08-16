import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from backend.data_pipeline.route_event import artifact_id_v1
from backend.data_pipeline.research.rrc25_country_outage import (
    full_window_journal as journal_module,
)
from backend.data_pipeline.research.rrc25_country_outage.full_window_journal import (
    ArtifactDescriptor,
    FullWindowJournalError,
    ShardInput,
    SimulatedJournalCrash,
    SinglePassProof,
    begin_artifact_attempt,
    commit_artifact_boundary,
    cumulative_reserved_raw_bytes,
    frozen_journal_head,
    full_window_execution_lock,
    initialize_full_window_journal,
    load_full_window_head,
    plan_artifact_admission,
    record_attempt_failure,
    reconcile_abandoned_active_attempt,
    scratch_payload_sha256,
)


RUN_ID = "research_run_v1_" + "1" * 24
BINDINGS = {
    "profile_sha256": hashlib.sha256(b"profile").hexdigest(),
    "input_selection_sha256": hashlib.sha256(b"selection").hexdigest(),
    "code_sha256": hashlib.sha256(b"code").hexdigest(),
    "mapping_sha256": hashlib.sha256(b"mapping").hexdigest(),
}
PRELIMINARY_READS = 1_280_393_043
SEED_READ = 426_797_681
ADDITIONAL_PRIOR_READ = 426_797_681


def artifact(index, *, size=1_000_000):
    file_sha = hashlib.sha256(f"update-{index}".encode("ascii")).hexdigest()
    minute = index * 5
    start_hour, start_minute = divmod(minute, 60)
    end_hour, end_minute = divmod(minute + 5, 60)
    return ArtifactDescriptor(
        index=index,
        artifact_id=artifact_id_v1(file_sha),
        file_sha256=file_sha,
        size_bytes=size,
        collector_id="rrc25",
        slot_start_utc=f"2026-02-27T{16 + start_hour:02d}:{start_minute:02d}:00Z",
        slot_end_exclusive_utc=f"2026-02-27T{16 + end_hour:02d}:{end_minute:02d}:00Z",
    )


def proof(value, *, seconds=2.0, status="complete", passes=1, database_writes=0):
    return SinglePassProof(
        status=status,
        compressed_file_sha256=value.file_sha256,
        compressed_size_bytes=value.size_bytes,
        compressed_bytes_read_observed=value.size_bytes,
        compressed_read_passes=passes,
        process_seconds=seconds,
        peak_temporary_bytes=1024,
        database_write_operations=database_writes,
    )


class FullWindowJournalTests(unittest.TestCase):
    def initialize(self, parent, *, total=3, bootstrap=1_000_000.0):
        root = Path(parent) / "journal"
        head = initialize_full_window_journal(
            root,
            run_id=RUN_ID,
            bindings=BINDINGS,
            total_artifacts=total,
            initial_compact_state={"route_state": "seed", "tracked_prefixes": []},
            preliminary_seed_read_bytes=PRELIMINARY_READS,
            seed_artifact_read_bytes=SEED_READ,
            additional_pre_update_raw_read_bytes=ADDITIONAL_PRIOR_READ,
            bootstrap_bytes_per_second=bootstrap,
        )
        return root, head

    def commit(self, head, value, token=None, *, crash_hook=None):
        token = token or begin_artifact_attempt(head, value)
        return commit_artifact_boundary(
            head,
            token,
            proof=proof(value),
            compact_state={"route_state": f"after-{value.index}", "tracked_prefixes": ["192.0.2.0/24"]},
            shards=(
                ShardInput(
                    "route_events",
                    ({"artifact_id": value.artifact_id, "route_event_id": f"event-{value.index}"},),
                ),
                ShardInput(
                    "raw_audits",
                    ({"artifact_id": value.artifact_id, "record_ordinal": 0},),
                ),
                ShardInput(
                    "country_slots",
                    ({"slot_start_utc": value.slot_start_utc, "value_state": "observed"},),
                ),
            ),
            crash_hook=crash_hook,
        )

    def test_genesis_discloses_prior_seed_reads_and_frozen_head_excludes_current(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            self.assertEqual(
                cumulative_reserved_raw_bytes(root),
                PRELIMINARY_READS + SEED_READ + ADDITIONAL_PRIOR_READ,
            )
            frozen = frozen_journal_head(head)
            self.assertNotIn("CURRENT", str(frozen))
            self.assertFalse(frozen["scratch_is_evidence"])
            self.assertEqual(frozen["completed_artifact_count"], 0)
            self.assertEqual(len(list((root / "scratch").glob("state-*.jsonl.gz"))), 1)
            self.assertEqual(
                scratch_payload_sha256(head.scratch),
                head.receipt["state_ref"]["sha256"],
            )

    def test_seed_evidence_is_immutable_genesis_shard_and_tamper_blocks_load(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            head = initialize_full_window_journal(
                root,
                run_id=RUN_ID,
                bindings=BINDINGS,
                total_artifacts=1,
                initial_compact_state={"route_state": "seed"},
                preliminary_seed_read_bytes=PRELIMINARY_READS,
                seed_artifact_read_bytes=SEED_READ,
                additional_pre_update_raw_read_bytes=ADDITIONAL_PRIOR_READ,
                bootstrap_bytes_per_second=1_000_000.0,
                genesis_shards=(
                    ShardInput(
                        "seed_route_events",
                        ({"route_event_id": "rte_v1_seed", "raw_record_ref_id": "raw_v1_seed"},),
                    ),
                    ShardInput(
                        "seed_raw_record_refs",
                        ({"raw_record_ref_id": "raw_v1_seed", "record_ordinal": 7, "element_ordinal": 2},),
                    ),
                ),
            )
            self.assertEqual(len(head.receipt["shards"]), 2)
            self.assertNotEqual(head.shard_chain_sha256, journal_module._initial_chain())
            self.assertEqual(frozen_journal_head(head)["verified_receipt_count"], 1)
            target = root / head.receipt["shards"][0]["path"]
            target.write_bytes(target.read_bytes() + b"tamper")
            with self.assertRaisesRegex(FullWindowJournalError, "SHA256/size"):
                load_full_window_head(root, expected_bindings=BINDINGS)

    def test_attempt_start_is_durable_before_open_and_retry_never_refunds_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            value = artifact(0, size=2_000_000)
            first = begin_artifact_attempt(head, value)
            self.assertTrue((root / first.path).is_file())
            self.assertEqual(
                cumulative_reserved_raw_bytes(root),
                PRELIMINARY_READS + SEED_READ + ADDITIONAL_PRIOR_READ + value.size_bytes,
            )
            failure_ref = record_attempt_failure(
                root,
                first,
                reason="simulated_parser_crash",
                observed_compressed_bytes=1234,
            )
            failure = json.loads(
                (root / failure_ref["path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                failure["outcome"], "failed_before_complete_single_pass"
            )
            second = begin_artifact_attempt(head, value)
            self.assertNotEqual(first.attempt_id, second.attempt_id)
            self.assertEqual(
                cumulative_reserved_raw_bytes(root),
                PRELIMINARY_READS + SEED_READ + ADDITIONAL_PRIOR_READ + 2 * value.size_bytes,
            )

    def test_pre_open_admission_uses_bootstrap_and_420_second_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            _root, head = self.initialize(directory, bootstrap=50_000.0)
            decision = plan_artifact_admission(head, artifact(0, size=25_000_000))
            self.assertFalse(decision.allowed)
            self.assertEqual(
                decision.reason,
                "estimated_runtime_reaches_artifact_admission_boundary",
            )
            self.assertEqual(decision.estimated_process_seconds, 500.0)

    def test_success_publishes_content_addressed_shards_receipt_and_only_two_scratch_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            first = self.commit(head, artifact(0)).head
            second = self.commit(first, artifact(1)).head
            third = self.commit(second, artifact(2)).head

            self.assertEqual(third.next_artifact_index, 3)
            self.assertEqual(len(list((root / "scratch").glob("state-*.jsonl.gz"))), 2)
            self.assertEqual(len(list((root / "receipts").glob("boundary-*.json"))), 4)
            self.assertEqual(len(list((root / "shards/route_events").glob("*.jsonl.gz"))), 3)
            frozen = frozen_journal_head(third)
            self.assertEqual(frozen["terminal_receipt_ref"]["sha256"], third.receipt_sha256)
            self.assertEqual(frozen["shard_chain_sha256"], third.shard_chain_sha256)
            self.assertEqual(frozen["verified_receipt_count"], 4)
            self.assertEqual(
                frozen["cumulative_reserved_raw_bytes"],
                PRELIMINARY_READS + SEED_READ + ADDITIONAL_PRIOR_READ + 3_000_000,
            )

    def test_crash_after_scratch_leaves_orphan_scratch_but_does_not_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            value = artifact(0)
            token = begin_artifact_attempt(head, value)

            def crash(stage):
                if stage == "after_scratch_publish":
                    raise SimulatedJournalCrash(stage)

            with self.assertRaises(SimulatedJournalCrash):
                self.commit(head, value, token, crash_hook=crash)
            recovered = load_full_window_head(root, expected_bindings=BINDINGS)
            self.assertEqual(recovered.next_artifact_index, 0)
            self.assertEqual(recovered.scratch["compact_state"]["route_state"], "seed")
            self.assertTrue((root / "scratch/state-b.jsonl.gz").is_file())

    def test_supervised_precomplete_crash_is_reconciled_with_unknown_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            value = artifact(0)
            with full_window_execution_lock(root):
                abandoned = begin_artifact_attempt(
                    head, value, track_active_attempt=True
                )
            self.assertTrue((root / "raw-ledger/ACTIVE").is_file())

            with full_window_execution_lock(root):
                result = reconcile_abandoned_active_attempt(head)
                self.assertEqual(
                    result["action"],
                    "closed_precomplete_with_unknown_observed_interval",
                )
                retry = begin_artifact_attempt(
                    result["head"], value, track_active_attempt=True
                )
                committed = self.commit(result["head"], value, retry)

            self.assertFalse((root / "raw-ledger/ACTIVE").exists())
            terminal_path = next(
                (root / "raw-ledger/outcomes").glob(
                    f"terminal-outcome-{abandoned.attempt_id}-*.json"
                )
            )
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
            self.assertEqual(
                terminal["observed_compressed_bytes_state"],
                "unknown_after_process_termination",
            )
            self.assertIsNone(terminal["observed_compressed_bytes"])
            self.assertEqual(terminal["observed_compressed_bytes_lower_bound"], 0)
            self.assertEqual(
                terminal["observed_compressed_bytes_upper_bound"], value.size_bytes
            )
            self.assertEqual(committed.head.next_artifact_index, 1)

    def test_supervised_complete_parse_crash_links_parse_and_terminal_before_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            value = artifact(0)

            def crash(stage):
                if stage == "after_scratch_publish":
                    raise SimulatedJournalCrash(stage)

            with self.assertRaises(SimulatedJournalCrash):
                with full_window_execution_lock(root):
                    abandoned = begin_artifact_attempt(
                        head, value, track_active_attempt=True
                    )
                    self.commit(head, value, abandoned, crash_hook=crash)

            with full_window_execution_lock(root):
                result = reconcile_abandoned_active_attempt(head)
                self.assertEqual(
                    result["action"], "closed_complete_parse_without_receipt"
                )
                retry = begin_artifact_attempt(
                    result["head"], value, track_active_attempt=True
                )
                committed = self.commit(result["head"], value, retry)

            terminal_path = next(
                (root / "raw-ledger/outcomes").glob(
                    f"terminal-outcome-{abandoned.attempt_id}-*.json"
                )
            )
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
            self.assertEqual(
                terminal["outcome"],
                "publication_failed_after_complete_single_pass",
            )
            complete_ref = terminal["completed_parse_outcome_ref"]
            self.assertTrue((root / complete_ref["path"]).is_file())
            self.assertEqual(
                terminal["observed_compressed_bytes_lower_bound"], value.size_bytes
            )
            self.assertEqual(
                terminal["observed_compressed_bytes_upper_bound"], value.size_bytes
            )
            self.assertEqual(committed.head.next_artifact_index, 1)

    def test_crash_after_receipt_repairs_unique_committed_successor_without_reread(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            value = artifact(0)
            token = begin_artifact_attempt(head, value)

            def crash(stage):
                if stage == "after_receipt_publish":
                    raise SimulatedJournalCrash(stage)

            with self.assertRaises(SimulatedJournalCrash):
                self.commit(head, value, token, crash_hook=crash)
            recovered = load_full_window_head(root, expected_bindings=BINDINGS)
            self.assertEqual(recovered.next_artifact_index, 1)
            self.assertEqual(
                cumulative_reserved_raw_bytes(root),
                PRELIMINARY_READS + SEED_READ + ADDITIONAL_PRIOR_READ + value.size_bytes,
            )

    def test_read_only_verify_reports_recovery_required_after_receipt_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            value = artifact(0)
            token = begin_artifact_attempt(head, value)

            def crash(stage):
                if stage == "after_receipt_publish":
                    raise SimulatedJournalCrash(stage)

            with self.assertRaises(SimulatedJournalCrash):
                self.commit(head, value, token, crash_hook=crash)
            stale = load_full_window_head(
                root,
                expected_bindings=BINDINGS,
                recover_committed_successor=False,
            )
            with self.assertRaisesRegex(FullWindowJournalError, "recovery_required"):
                frozen_journal_head(stale)
            recovered = load_full_window_head(root, expected_bindings=BINDINGS)
            self.assertEqual(recovered.next_artifact_index, 1)

    def test_concurrent_commits_from_same_base_have_exactly_one_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            value = artifact(0)
            first = begin_artifact_attempt(head, value)
            second = begin_artifact_attempt(head, value)
            barrier = threading.Barrier(2)
            successes = []
            failures = []
            guard = threading.Lock()

            def run(token, marker):
                barrier.wait()
                try:
                    committed = commit_artifact_boundary(
                        head,
                        token,
                        proof=proof(value),
                        compact_state={"route_state": marker},
                        shards=(ShardInput("route_events", ({"marker": marker},)),),
                    )
                    with guard:
                        successes.append(committed)
                except FullWindowJournalError as error:
                    with guard:
                        failures.append(str(error))

            threads = (
                threading.Thread(target=run, args=(first, "first")),
                threading.Thread(target=run, args=(second, "second")),
            )
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
            self.assertEqual(len(successes), 1)
            self.assertEqual(len(failures), 1)
            self.assertIn("CURRENT 已变化", failures[0])
            committed = load_full_window_head(root, expected_bindings=BINDINGS)
            self.assertEqual(committed.next_artifact_index, 1)
            self.assertEqual(
                len(list((root / "receipts").glob("boundary-0001-*.json"))),
                1,
            )
            self.assertEqual(frozen_journal_head(committed)["verified_receipt_count"], 2)

    def test_verify_rejects_non_ancestry_sibling_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            committed = self.commit(head, artifact(0)).head
            source = root / committed.receipt_path
            sibling = source.with_name("boundary-0001-" + "e" * 64 + ".json")
            sibling.write_bytes(source.read_bytes())
            with self.assertRaisesRegex(FullWindowJournalError, "orphan/sibling"):
                frozen_journal_head(committed)

    def test_crash_after_current_is_already_committed(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            value = artifact(0)
            token = begin_artifact_attempt(head, value)

            def crash(stage):
                if stage == "after_current_publish":
                    raise SimulatedJournalCrash(stage)

            with self.assertRaises(SimulatedJournalCrash):
                self.commit(head, value, token, crash_hook=crash)
            recovered = load_full_window_head(root, expected_bindings=BINDINGS)
            self.assertEqual(recovered.next_artifact_index, 1)

    def test_incomplete_or_non_single_pass_proof_never_advances_current(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            value = artifact(0)
            token = begin_artifact_attempt(head, value)
            with self.assertRaisesRegex(FullWindowJournalError, "single pass"):
                commit_artifact_boundary(
                    head,
                    token,
                    proof=proof(value, passes=2),
                    compact_state={"route_state": "invalid"},
                    shards=(),
                )
            current = load_full_window_head(root, expected_bindings=BINDINGS)
            self.assertEqual(current.next_artifact_index, 0)

    def test_raw_limit_includes_prior_reads_and_all_failed_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            _root, head = self.initialize(directory)
            value = artifact(0, size=100)
            limit = PRELIMINARY_READS + SEED_READ + ADDITIONAL_PRIOR_READ + 99
            decision = plan_artifact_admission(head, value, max_raw_bytes=limit)
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason, "cumulative_raw_reservation_exceeds_limit")
            with self.assertRaisesRegex(FullWindowJournalError, "预检拒绝"):
                begin_artifact_attempt(head, value, max_raw_bytes=limit)

    def test_hot_raw_counter_is_constant_time_but_final_verify_recomputes_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            first = begin_artifact_attempt(head, artifact(0, size=100))
            record_attempt_failure(
                root,
                first,
                reason="fixture failure",
                observed_compressed_bytes=10,
            )
            expected = (
                PRELIMINARY_READS
                + SEED_READ
                + ADDITIONAL_PRIOR_READ
                + 100
            )
            with mock.patch.object(
                journal_module,
                "_attempt_receipts",
                side_effect=AssertionError("热路径不得全扫 attempt ledger"),
            ):
                self.assertEqual(cumulative_reserved_raw_bytes(root), expected)
                self.assertEqual(
                    plan_artifact_admission(head, artifact(0, size=100)).cumulative_reserved_before,
                    expected,
                )
            with mock.patch.object(
                journal_module,
                "_attempt_receipts",
                wraps=journal_module._attempt_receipts,
            ) as full_scan:
                frozen_journal_head(head)
                self.assertGreaterEqual(full_scan.call_count, 1)

    def test_raw_limit_equality_rejects_before_durable_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            value = artifact(0, size=100)
            exact_boundary = cumulative_reserved_raw_bytes(root) + value.size_bytes
            decision = plan_artifact_admission(
                head,
                value,
                max_raw_bytes=exact_boundary,
            )
            self.assertFalse(decision.allowed)
            self.assertEqual(
                decision.reason,
                "cumulative_raw_reservation_exceeds_limit",
            )
            with self.assertRaisesRegex(FullWindowJournalError, "预检拒绝"):
                begin_artifact_attempt(
                    head,
                    value,
                    max_raw_bytes=exact_boundary,
                )
            self.assertEqual(
                list((root / "raw-ledger/attempts").glob("attempt-start-*.json")),
                [],
            )

    def test_genesis_rejects_exact_50gb_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            with self.assertRaisesRegex(FullWindowJournalError, "50GB"):
                initialize_full_window_journal(
                    root,
                    run_id=RUN_ID,
                    bindings=BINDINGS,
                    total_artifacts=1,
                    initial_compact_state={"route_state": "seed"},
                    preliminary_seed_read_bytes=49_999_999_999,
                    seed_artifact_read_bytes=1,
                    additional_pre_update_raw_read_bytes=0,
                    bootstrap_bytes_per_second=1_000_000.0,
                )
            self.assertFalse(root.exists())

    def test_init_external_temp_budget_blocks_current_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            with self.assertRaisesRegex(FullWindowJournalError, "临时空间"):
                initialize_full_window_journal(
                    root,
                    run_id=RUN_ID,
                    bindings=BINDINGS,
                    total_artifacts=1,
                    initial_compact_state={"route_state": "seed"},
                    preliminary_seed_read_bytes=1,
                    seed_artifact_read_bytes=1,
                    additional_pre_update_raw_read_bytes=0,
                    bootstrap_bytes_per_second=1_000_000.0,
                    retained_external_temporary_bytes=99,
                    maximum_temporary_bytes=100,
                )
            self.assertTrue(root.is_dir())
            self.assertFalse((root / "CURRENT").exists())

    def test_missing_published_shard_blocks_head_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            committed = self.commit(head, artifact(0)).head
            shard_path = root / committed.receipt["shards"][0]["path"]
            shard_path.unlink()
            with self.assertRaisesRegex(FullWindowJournalError, "文件不可读"):
                load_full_window_head(root, expected_bindings=BINDINGS)

    def test_tampered_outcome_blocks_head_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            committed = self.commit(head, artifact(0)).head
            outcome_path = root / committed.receipt["outcome_ref"]["path"]
            payload = json.loads(outcome_path.read_text(encoding="utf-8"))
            payload["observed_compressed_bytes"] = 1
            outcome_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(FullWindowJournalError, "SHA256 不一致"):
                load_full_window_head(root, expected_bindings=BINDINGS)

    def test_wrong_successor_chain_is_not_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            value = artifact(0)
            token = begin_artifact_attempt(head, value)

            def crash(stage):
                if stage == "after_receipt_publish":
                    raise SimulatedJournalCrash(stage)

            with self.assertRaises(SimulatedJournalCrash):
                self.commit(head, value, token, crash_hook=crash)
            successor = next(
                path
                for path in (root / "receipts").glob("boundary-0001-*.json")
            )
            payload = json.loads(successor.read_text(encoding="utf-8"))
            semantic = dict(payload)
            semantic.pop("schema_version")
            semantic.pop("fingerprint_sha256")
            semantic["shard_chain_sha256"] = "0" * 64
            resigned = journal_module._fingerprinted(
                journal_module.BOUNDARY_RECEIPT_SCHEMA_VERSION,
                semantic,
            )
            successor.write_text(
                journal_module.canonical_json(resigned) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(FullWindowJournalError, "shard chain"):
                load_full_window_head(root, expected_bindings=BINDINGS)

    def test_multiple_closed_successor_receipts_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root, head = self.initialize(directory)
            value = artifact(0)
            token = begin_artifact_attempt(head, value)

            def crash(stage):
                if stage == "after_receipt_publish":
                    raise SimulatedJournalCrash(stage)

            with self.assertRaises(SimulatedJournalCrash):
                self.commit(head, value, token, crash_hook=crash)
            successor = next(
                path
                for path in (root / "receipts").glob("boundary-0001-*.json")
            )
            duplicate = successor.with_name(
                "boundary-0001-" + "f" * 64 + ".json"
            )
            duplicate.write_bytes(successor.read_bytes())
            with self.assertRaisesRegex(FullWindowJournalError, "多个已闭合后继"):
                load_full_window_head(root, expected_bindings=BINDINGS)


if __name__ == "__main__":
    unittest.main()
