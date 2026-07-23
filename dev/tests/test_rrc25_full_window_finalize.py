from __future__ import annotations

import copy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from backend.data_pipeline.route_event import artifact_id_v1
from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import canonical_json
from backend.data_pipeline.research.rrc25_country_outage import full_window_finalize as finalizer
from backend.data_pipeline.research.rrc25_country_outage import full_window_journal as journal
from backend.data_pipeline.research.rrc25_country_outage.full_window_worker import (
    artifact_descriptor_from_manifest,
    initialize_compact_state_from_seed,
    run_one_update_artifact,
)
from dev.data_quality.rrc25_iran_bounded_pilot import build_code_identity
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
from backend.data_pipeline.route_event import ParsedMrtRecord


SAMPLE_ID = "sample_v1_" + "1" * 24
SNAPSHOT_ID = "snapshot_v1_" + "2" * 24
RUN_ID = "research_run_v1_" + "3" * 24
PARTIAL = "observed_route_state_partial_vp_coverage"
PARTIAL_REASON = "peer_session_unavailable_route_state_carried_not_withdrawn"


def _metric(value, *, ratio=False):
    row = {
        "sample_id": SAMPLE_ID,
        "snapshot_id": SNAPSHOT_ID,
        "value": value,
        "value_state": PARTIAL,
        "missing_reason": PARTIAL_REASON,
    }
    if ratio:
        row["numerator"] = {"sample_id": SAMPLE_ID, "snapshot_id": SNAPSHOT_ID, "value": 1}
        row["denominator"] = {"sample_id": SAMPLE_ID, "snapshot_id": SNAPSHOT_ID, "value": 2}
    return row


def _observed_count(value):
    return {
        "sample_id": SAMPLE_ID,
        "snapshot_id": SNAPSHOT_ID,
        "value": value,
        "value_state": "observed_zero" if value == 0 else "observed",
        "missing_reason": None,
    }


def _internal_sample(view="compatible"):
    metrics = {
        "visible_asn_count": _metric(1),
        "damaged_asn_count": _metric(1),
        "baseline_asn_count": _metric(2),
        "visible_ipv4_prefix_count": _metric(1),
        "visible_ipv6_prefix_count": _metric(0),
        "visible_ipv4_address_union": _metric(256),
        "visible_ipv4_24_equivalent": _metric(1),
        "visible_ipv6_48_equivalent": _metric(0),
        "announce_count": _observed_count(1),
        "withdraw_count": _observed_count(0),
        "vp_expected_count": _observed_count(2),
        "vp_observed_count": _observed_count(1),
        "damaged_asn_ratio": _metric(0.5, ratio=True),
    }
    sets = {
        name: {
            "sample_id": SAMPLE_ID,
            "snapshot_id": SNAPSHOT_ID,
            "value": value,
            "value_state": PARTIAL,
            "missing_reason": PARTIAL_REASON,
        }
        for name, value in (("visible", [65001]), ("damaged", [65002]), ("baseline", [65001, 65002]))
    }
    return {
        "schema_version": finalizer.ANALYSIS_SAMPLE_SCHEMA_VERSION,
        "sample_id": SAMPLE_ID,
        "run_id": RUN_ID,
        "snapshot_id": SNAPSHOT_ID,
        "collector_id": "rrc25",
        "country_code": "IR",
        "cohort_view": view,
        "slot": {
            "start": "2026-02-27T16:00:00Z",
            "end": "2026-02-27T16:05:00Z",
            "boundary": "[start,end)",
            "granularity_seconds": 300,
        },
        "continuity_state": "continuous",
        "metrics": metrics,
        "asn_sets": sets,
        "source_refs": [{
            "ref_type": "immutable_package_state_shard",
            "ref_id": "journal-ancestry/shards/country_slots/slot-0000-a.jsonl.gz",
            "sha256": "a" * 64,
        }],
        "measurement_semantics": {
            "curve": "carried_route_state",
            "vp_coverage_state": "partial",
            "source_value_state": PARTIAL,
            "down_vp_route_semantics": "carried_state_not_implicit_withdrawal",
            "down_vp_ids": ["vp_v1_a"],
            "unknown_vp_ids": [],
            "implicit_withdrawal_from_peer_state_change": False,
            "algorithm_numeric_policy": "carried_state_value_used_with_partial_coverage_disclosed",
            "update_count_scope": "retained_tracked_prefix_set_not_country_intent",
            "retained_announce_count": 1,
            "retained_withdraw_count": 0,
            "collector_total_announce_count": 9,
            "collector_total_withdraw_count": 4,
        },
    }


def _write_rows(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(canonical_json(row) + "\n" for row in rows).encode("utf-8")
    path.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))


def _observation_ref(path: Path, rows):
    _write_rows(path, rows)
    payload = path.read_bytes()
    return {
        "kind": "record_observations",
        "path": path.name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "record_count": len(rows),
    }


def _observation_row(artifact, ordinal, *, kind="update"):
    event_time = "2026-02-27T16:00:01.123456Z"
    raw_sha = hashlib.sha256(f"record-{ordinal}".encode("ascii")).hexdigest()
    return {
        "schema_version": finalizer.RECORD_OBSERVATION_SHARD_SCHEMA_VERSION,
        "artifact_id": artifact["artifact_id"],
        "file_sha256": artifact["file_sha256"],
        "collector_id": artifact["collector_id"],
        "artifact_slot_utc": artifact["slot_start_utc"],
        "record_ordinal": ordinal,
        "record_offset": ordinal * 12,
        "record_length": 12,
        "raw_record_sha256": raw_sha,
        "event_time_utc": event_time,
        "record_kind": kind,
        "announce_count": 1 if kind == "update" else 0,
        "withdraw_count": 0,
        "update_peer_observations": (
            [{"vp_id": "vp_v1_fixture", "event_time_utc": event_time}]
            if kind == "update"
            else []
        ),
        "peer_session_observation": None,
        "semantics": (
            "complete_physical_record_observation_for_independent_slot_derivation"
        ),
    }


def _control_row(artifact, ordinal):
    return {
        "schema_version": finalizer.CONTROL_RECORD_SHARD_SCHEMA_VERSION,
        "record_kind": "keepalive",
        "artifact_id": artifact["artifact_id"],
        "file_sha256": artifact["file_sha256"],
        "collector_id": artifact["collector_id"],
        "artifact_slot_utc": artifact["slot_start_utc"],
        "record_ordinal": ordinal,
        "record_offset": ordinal * 12,
        "record_length": 12,
        "raw_record_sha256": hashlib.sha256(
            f"control-{ordinal}".encode("ascii")
        ).hexdigest(),
        "event_time_utc": "2026-02-27T16:00:01.123456Z",
        "mrt_type": 16,
        "mrt_subtype": 4,
        "route_event_ids": [],
        "peer_session_observation": None,
        "control_record_semantics": "control_record_not_route_element_evidence",
    }


class FullWindowFinalizeContractTests(unittest.TestCase):
    def test_large_record_observation_shard_streams_without_journal_population(self):
        file_sha = "8" * 64
        artifact = {
            "artifact_id": artifact_id_v1(file_sha),
            "file_sha256": file_sha,
            "collector_id": "rrc25",
            "slot_start_utc": "2026-02-27T16:00:00Z",
            "slot_end_exclusive_utc": "2026-02-27T16:05:00Z",
        }
        rows = [_observation_row(artifact, ordinal) for ordinal in range(5000)]
        retained = rows[-1]
        route = {
            "record_ordinal": retained["record_ordinal"],
            "event_time_utc": retained["event_time_utc"],
        }
        raw = {
            "record_ordinal": retained["record_ordinal"],
            "record_offset": retained["record_offset"],
            "record_length": retained["record_length"],
            "raw_record_sha256": retained["raw_record_sha256"],
            "record_hash": retained["raw_record_sha256"],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = _observation_ref(root / "observations.jsonl.gz", rows)
            summary = finalizer._stream_record_observation_shard(
                root,
                ref,
                sequence=1,
                artifact=artifact,
                retained_route_rows=(route,),
                retained_raw_rows=(raw,),
            )
            self.assertEqual(summary["record_count"], 5000)
            self.assertRegex(summary["semantic_sha256"], r"^[0-9a-f]{64}$")
            self.assertNotIn(
                "record_observation_rows", finalizer._JournalData.__dataclass_fields__
            )
            self.assertIn(
                "record_observation_count", finalizer._JournalData.__dataclass_fields__
            )

            forged_raw = dict(raw)
            forged_raw["raw_record_sha256"] = "f" * 64
            forged_raw["record_hash"] = "f" * 64
            with self.assertRaisesRegex(
                finalizer.FullWindowFinalizeError,
                "physical record observation 坐标/哈希",
            ):
                finalizer._stream_record_observation_shard(
                    root,
                    ref,
                    sequence=1,
                    artifact=artifact,
                    retained_route_rows=(route,),
                    retained_raw_rows=(forged_raw,),
                )

    def test_retained_route_requires_update_observation_and_soft_stop(self):
        file_sha = "9" * 64
        artifact = {
            "artifact_id": artifact_id_v1(file_sha),
            "file_sha256": file_sha,
            "collector_id": "rrc25",
            "slot_start_utc": "2026-02-27T16:00:00Z",
            "slot_end_exclusive_utc": "2026-02-27T16:05:00Z",
        }
        row = _observation_row(artifact, 0, kind="keepalive")
        route = {"record_ordinal": 0, "event_time_utc": row["event_time_utc"]}
        raw = {
            "record_ordinal": 0,
            "record_offset": 0,
            "record_length": 12,
            "raw_record_sha256": row["raw_record_sha256"],
            "record_hash": row["raw_record_sha256"],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = _observation_ref(root / "observations.jsonl.gz", [row])
            with self.assertRaisesRegex(
                finalizer.FullWindowFinalizeError,
                "只能闭合到 UPDATE",
            ):
                finalizer._stream_record_observation_shard(
                    root,
                    ref,
                    sequence=1,
                    artifact=artifact,
                    retained_route_rows=(route,),
                    retained_raw_rows=(raw,),
                )
            with self.assertRaisesRegex(
                finalizer.FullWindowFinalizeError, "540 秒软停止门"
            ):
                finalizer._stream_record_observation_shard(
                    root,
                    ref,
                    sequence=1,
                    artifact=artifact,
                    retained_route_rows=(),
                    retained_raw_rows=(),
                    started_monotonic=0.0,
                    monotonic=lambda: 540.0,
                )

    def test_control_records_stream_without_global_journal_population(self):
        file_sha = "a" * 64
        artifact = {
            "artifact_id": artifact_id_v1(file_sha),
            "file_sha256": file_sha,
            "collector_id": "rrc25",
            "slot_start_utc": "2026-02-27T16:00:00Z",
            "slot_end_exclusive_utc": "2026-02-27T16:05:00Z",
        }
        rows = [_control_row(artifact, ordinal) for ordinal in range(1000)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = _observation_ref(root / "controls.jsonl.gz", rows)
            ref["kind"] = "control_records"
            summary = finalizer._stream_control_record_shard(
                root, ref, sequence=1, artifact=artifact
            )
            self.assertEqual(summary["record_count"], 1000)
            self.assertNotIn(
                "control_rows", finalizer._JournalData.__dataclass_fields__
            )
            self.assertIn(
                "control_record_count", finalizer._JournalData.__dataclass_fields__
            )

    def test_partial_vp_v1_projection_is_unknown_and_sidecar_retains_values(self):
        internal = _internal_sample()
        projected = finalizer._contract_sample(internal)
        sidecar = finalizer._sample_semantics_sidecar(internal)

        self.assertEqual(projected["schema_version"], "country-outage-sample/v1")
        self.assertEqual(projected["metrics"]["visible_ipv4_address_union"]["value_state"], "unknown_state_gap")
        self.assertIsNone(projected["metrics"]["visible_ipv4_address_union"]["value"])
        self.assertIsNone(projected["metrics"]["damaged_asn_ratio"]["numerator"])
        self.assertEqual(projected["asn_sets"]["visible"]["value_state"], "unknown_state_gap")
        self.assertIsNone(projected["asn_sets"]["visible"]["value"])
        self.assertEqual(projected["metrics"]["announce_count"]["value"], 1)
        self.assertEqual(sidecar["carried_metrics"]["visible_ipv4_address_union"]["value"], 256)
        self.assertEqual(sidecar["measurement_semantics"]["collector_total_announce_count"], 9)
        self.assertFalse(sidecar["measurement_semantics"]["implicit_withdrawal_from_peer_state_change"])

    def test_actual_ajv_package_contract_validation_passes_and_detects_false_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for view in ("compatible", "revised"):
                internal = _internal_sample(view)
                sample = finalizer._contract_sample(internal)
                sidecar = finalizer._sample_semantics_sidecar(internal)
                _write_rows(root / "data" / f"{view}-country-samples.jsonl.gz", (sample,))
                _write_rows(root / "data" / f"{view}-sample-measurement-semantics.jsonl.gz", (sidecar,))
                for name in ("episode-as", "episode-as-measurement-semantics", "prefix-impact"):
                    _write_rows(root / "data" / f"{view}-{name}.jsonl.gz", ())
            command = [
                "node",
                "dev/data_quality/validate_rrc25_full_window_package_contracts.cjs",
                "--package-root",
                str(root),
            ]
            passed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(passed.returncode, 0, passed.stderr)

            invalid = finalizer._contract_sample(_internal_sample("compatible"))
            invalid["metrics"]["visible_ipv4_address_union"] = _observed_count(256)
            _write_rows(root / "data/compatible-country-samples.jsonl.gz", (invalid,))
            failed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("伪装为 observed", failed.stderr)

    def test_attempt_outcome_group_counts_parse_and_publication_failure_once(self):
        artifact = {
            "index": 0,
            "artifact_id": artifact_id_v1("4" * 64),
            "file_sha256": "4" * 64,
            "size_bytes": 10,
            "collector_id": "rrc25",
            "slot_start_utc": "2026-02-27T16:00:00Z",
            "slot_end_exclusive_utc": "2026-02-27T16:05:00Z",
        }
        attempt = {"artifact": artifact, "reserved_raw_bytes": 10}
        complete = {
            "outcome": "complete_single_pass",
            "failure_reason": None,
            "observed_compressed_bytes": 10,
            "reservation_refunded_bytes": 0,
            "__ledger_ref": {
                "path": "raw-ledger/outcomes/parse-outcome-attempt.json",
                "sha256": "5" * 64,
            },
            "proof": {
                "status": "complete",
                "compressed_file_sha256": "4" * 64,
                "compressed_size_bytes": 10,
                "compressed_bytes_read_observed": 10,
                "compressed_read_passes": 1,
                "process_seconds": 1.0,
                "peak_temporary_bytes": 0,
                "database_write_operations": 0,
            },
        }
        publication_failed = {
            "outcome": "publication_failed_after_complete_single_pass",
            "failure_reason": "publication gate failed",
            "observed_compressed_bytes_state": "exact",
            "observed_compressed_bytes": 10,
            "observed_compressed_bytes_lower_bound": 10,
            "observed_compressed_bytes_upper_bound": 10,
            "completed_parse_outcome_ref": complete["__ledger_ref"],
            "reservation_refunded_bytes": 0,
        }
        self.assertEqual(
            finalizer._validate_attempt_outcome_group(attempt, (complete, publication_failed)),
            {
                "observed_compressed_bytes": 10,
                "observed_compressed_bytes_lower_bound": 10,
                "observed_compressed_bytes_upper_bound": 10,
                "has_complete": True,
                "has_terminal": True,
            },
        )
        with self.assertRaisesRegex(finalizer.FullWindowFinalizeError, "发布失败必须精确绑定"):
            finalizer._validate_attempt_outcome_group(attempt, (publication_failed,))

    def test_unknown_crash_outcome_is_preserved_as_interval_not_false_exact(self):
        artifact = {
            "index": 0,
            "artifact_id": artifact_id_v1("6" * 64),
            "file_sha256": "6" * 64,
            "size_bytes": 10,
            "collector_id": "rrc25",
            "slot_start_utc": "2026-02-27T16:00:00Z",
            "slot_end_exclusive_utc": "2026-02-27T16:05:00Z",
        }
        terminal = {
            "outcome": "failed_before_complete_single_pass",
            "failure_reason": "worker_hard_crash",
            "observed_compressed_bytes_state": "unknown_after_process_termination",
            "observed_compressed_bytes": None,
            "observed_compressed_bytes_lower_bound": 0,
            "observed_compressed_bytes_upper_bound": 10,
            "completed_parse_outcome_ref": None,
            "reservation_refunded_bytes": 0,
        }
        measured = finalizer._validate_attempt_outcome_group(
            {"artifact": artifact, "reserved_raw_bytes": 10}, (terminal,)
        )
        self.assertIsNone(measured["observed_compressed_bytes"])
        self.assertEqual(measured["observed_compressed_bytes_lower_bound"], 0)
        self.assertEqual(measured["observed_compressed_bytes_upper_bound"], 10)

    def test_embedded_seed_retirement_receipt_requires_own_fingerprint(self):
        semantic = {
            "schema_version": finalizer.SEED_RETIREMENT_RECEIPT_SCHEMA,
            "operation": "seed_spool_retirement",
        }
        receipt = {
            **semantic,
            "receipt_fingerprint_sha256": finalizer._canonical_hash(
                {
                    "schema": finalizer.SEED_RETIREMENT_RECEIPT_FINGERPRINT_SCHEMA,
                    "receipt": semantic,
                }
            ),
        }
        finalizer._validate_embedded_receipt_fingerprint(
            receipt,
            schema_version=finalizer.SEED_RETIREMENT_RECEIPT_SCHEMA,
            fingerprint_schema=finalizer.SEED_RETIREMENT_RECEIPT_FINGERPRINT_SCHEMA,
        )
        tampered = dict(receipt)
        tampered["operation"] = "forged"
        with self.assertRaisesRegex(
            finalizer.FullWindowFinalizeError, "内嵌 receipt fingerprint"
        ):
            finalizer._validate_embedded_receipt_fingerprint(
                tampered,
                schema_version=finalizer.SEED_RETIREMENT_RECEIPT_SCHEMA,
                fingerprint_schema=finalizer.SEED_RETIREMENT_RECEIPT_FINGERPRINT_SCHEMA,
            )

    def test_finalizer_rejects_active_attempt_before_reading_completed_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "raw-ledger").mkdir()
            (root / "raw-ledger/ACTIVE").write_text("active", encoding="utf-8")
            with self.assertRaisesRegex(
                finalizer.FullWindowFinalizeError, "ACTIVE attempt"
            ):
                finalizer._collect_journal_data(root, bindings={})

    def test_retained_staging_is_counted_and_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target = parent / "final-package"
            retained = parent / ".final-package.rrc25-finalize-staging-old"
            retained.mkdir()
            (retained / "payload").write_bytes(b"12345")
            total, paths = finalizer._retained_staging_inventory(target)
            self.assertEqual(total, 5)
            self.assertEqual(paths, (str(retained.resolve()),))
            other = parent / ".final-package.rrc25-finalize-staging-link"
            other.symlink_to(retained, target_is_directory=True)
            with self.assertRaisesRegex(finalizer.FullWindowFinalizeError, "历史 staging"):
                finalizer._retained_staging_inventory(target)

    def test_code_identity_is_recomputed_against_current_repository_files(self):
        identity = build_code_identity()
        self.assertEqual(
            finalizer._validate_code_identity(identity, identity["identity_sha256"]),
            identity,
        )
        changed = copy.deepcopy(identity)
        changed["files"][0]["sha256"] = "0" * 64
        changed["identity_sha256"] = finalizer._canonical_hash(
            {"schema_version": changed["schema_version"], "files": changed["files"]}
        )
        with self.assertRaisesRegex(finalizer.FullWindowFinalizeError, "当前代码与冻结身份"):
            finalizer._validate_code_identity(changed, changed["identity_sha256"])

    def test_two_slot_journal_is_independently_rederived_through_every_state_ref(self):
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
        seed_attestation = {"initial_compact_state": compact}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            head = journal.initialize_full_window_journal(
                root,
                run_id=RUN_ID,
                bindings=bindings,
                total_artifacts=2,
                initial_compact_state=compact,
                preliminary_seed_read_bytes=1,
                seed_artifact_read_bytes=1,
                additional_pre_update_raw_read_bytes=0,
                bootstrap_bytes_per_second=1_000_000,
                genesis_shards=(
                    journal.ShardInput(
                        "seed_bootstrap_attestation", (seed_attestation,)
                    ),
                    journal.ShardInput("seed_route_events", ()),
                    journal.ShardInput("seed_raw_record_refs", ()),
                ),
            )
            for index in range(2):
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
            frozen = journal.frozen_journal_head(head)
            collected = finalizer._collect_journal_data(
                root, bindings=bindings
            )
            self.assertEqual(collected.record_observation_count, 2)
            self.assertRegex(
                collected.record_observation_semantic_sha256,
                r"^[0-9a-f]{64}$",
            )
            self.assertFalse(hasattr(collected, "record_observation_rows"))
            self.assertEqual(collected.control_record_count, 0)
            self.assertFalse(hasattr(collected, "control_rows"))
            result = finalizer._validate_independent_artifact_derivations(
                journal_root=root,
                frozen_head=frozen,
                seed_bootstrap_attestation=seed_attestation,
                compatible_mapping=compatible,
                revised_mapping=revised,
                terminal_scratch=head.scratch,
            )
            self.assertEqual(result["verified_artifact_count"], 2)
            self.assertTrue(result["every_receipt_state_ref_recomputed"])


if __name__ == "__main__":
    unittest.main()
