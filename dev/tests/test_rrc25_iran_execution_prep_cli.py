from __future__ import annotations

from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from backend.data_pipeline.route_event import artifact_id_v1
from backend.data_pipeline.research.rrc25_country_outage import bounded_pilot_worker


ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = ROOT / "dev/data_quality/rrc25_iran_execution_prep.py"
SPEC = importlib.util.spec_from_file_location("rrc25_iran_execution_prep_cli", CLI_PATH)
assert SPEC is not None and SPEC.loader is not None
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def artifact(index: int) -> dict:
    at = datetime(2026, 2, 27, 16, 0, tzinfo=timezone.utc) + timedelta(
        minutes=5 * index
    )
    file_sha = hashlib.sha256(f"probe-{index}".encode()).hexdigest()
    return {
        "artifact_id": artifact_id_v1(file_sha),
        "artifact_type": "update",
        "artifact_time_utc": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "collector_id": "rrc25",
        "compression": "gz",
        "file_sha256": file_sha,
        "relative_path": f"rrc25/updates.{at:%Y%m%d.%H%M}.gz",
        "size_bytes": 1000 + index,
    }


def generated_attestation() -> dict:
    semantic = {
        "schema_version": "parser_attestation_v1",
        "parser_name": cli.NATIVE_UPDATE_PARSER_NAME,
        "parser_version": cli.NATIVE_UPDATE_PARSER_VERSION,
        "parser_binary_sha256": "8" * 64,
        "adapter_name": "domeye_native_update_adapter",
        "adapter_version": cli.NATIVE_UPDATE_PARSER_VERSION,
        "adapter_source_sha256": "9" * 64,
        "binary_execution_policy": cli.NATIVE_UPDATE_EXECUTION_POLICY,
        "configuration": {},
        "configuration_sha256": hashlib.sha256(b"config").hexdigest(),
        "pilot_limits": {},
        "security_boundary": "fixture",
    }
    return {
        **semantic,
        "attestation_fingerprint_sha256": hashlib.sha256(
            cli.canonical_json(
                {
                    "schema": "parser_attestation_fingerprint_v1",
                    "attestation": semantic,
                }
            ).encode()
        ).hexdigest(),
    }


class FakeStream:
    def __init__(self, row: dict):
        self.row = row
        self.statistics = {
            "status": "not_started",
            "compressed_bytes_read_observed": None,
            "compressed_read_passes": 0,
        }

    def __iter__(self):
        self.statistics = {
            "status": "complete",
            "compressed_file_sha256": self.row["file_sha256"],
            "compressed_size_bytes": self.row["size_bytes"],
            "compressed_bytes_read_observed": self.row["size_bytes"],
            "compressed_read_passes": 1,
        }
        yield SimpleNamespace(elements=())


class FakeFactory:
    def __init__(self, rows, attestation, attempt_directory: Path):
        self.rows = {row["artifact_id"]: row for row in rows}
        self.parser_attestation = attestation
        self.attempt_directory = attempt_directory

    def __call__(self, row):
        if len(tuple(self.attempt_directory.glob("attempt-*.json"))) != 1:
            raise AssertionError("raw stream 在 durable ATTEMPT 前建立")
        return FakeStream(self.rows[row["artifact_id"]])


def prepared_probe_ledger(
    root: Path,
    *,
    selection: dict,
    bindings: dict,
    initial_lower: int,
    initial_upper: int,
) -> dict:
    ledger = root / "probe-ledger"
    attempts = ledger / "attempts"
    outcomes = ledger / "outcomes"
    seed_attempts = ledger / "seed-attempts"
    seed_outcomes = ledger / "seed-outcomes"
    attempts.mkdir(parents=True)
    outcomes.mkdir()
    seed_attempts.mkdir()
    seed_outcomes.mkdir()
    (ledger / "throughput").mkdir()
    (ledger / "LOCK").touch()
    (ledger / "SEED-EXECUTION.LOCK").touch()
    imported = cli._fingerprinted(
        cli.PREEXISTING_ACCOUNTING_SCHEMA_VERSION,
        cli.PREEXISTING_ACCOUNTING_FINGERPRINT_SCHEMA,
        {
            "accounting_state": "conservative_upper_bound",
            "observed_lower_bound_new_raw_bytes": initial_lower,
            "reserved_upper_bound_new_raw_bytes": initial_upper,
            "source_run_path": str(root),
            "source_metadata_refs": [
                {
                    "path": str(root / "source.json"),
                    "sha256": "a" * 64,
                    "size_bytes": 1,
                }
            ],
            "derivation": {"fixture_only": True},
            "codex_task_id": "fixture-task",
            "frozen_at_utc": "2026-07-22T00:00:00Z",
            "history_limitation_zh": "pre-ledger 历史读取不能逐次拆分。",
            "semantics": "conservative_no_refund_pre_goal_reads",
            "database_write_operations": 0,
        },
    )
    imported_artifact = cli.write_canonical_json(
        ledger / "PRIOR-ACCOUNTING-IMPORT.json",
        imported,
        kind="fixture",
    )
    imported_ref = cli._published_ref(root, imported_artifact)
    prior = {
        "kind": "imported_preexisting_accounting",
        "accounting_state": "conservative_upper_bound",
        "observed_lower_bound_new_raw_bytes": initial_lower,
        "reserved_upper_bound_new_raw_bytes": initial_upper,
        "cumulative_reserved_new_raw_bytes": initial_upper,
        "semantics": "conservative_no_refund_pre_goal_reads",
        "history_limitation_zh": "pre-ledger 历史读取不能逐次拆分。",
        "codex_task_id": "fixture-task",
        "frozen_at_utc": "2026-07-22T00:00:00Z",
        "source_receipt_original_path": str(ledger / "import-source.json"),
        "source_receipt_file_sha256": imported_artifact.sha256,
        "imported_receipt_ref": imported_ref,
    }
    ledger_id = "probe_ledger_v1_fixture"
    genesis = cli._fingerprinted(
        cli.PROBE_GENESIS_SCHEMA_VERSION,
        cli.PROBE_GENESIS_FINGERPRINT_SCHEMA,
        {
            "ledger_id": ledger_id,
            "prepared_bindings": bindings,
            "selection_id": selection["selection_id"],
            "prior_accounting": prior,
            "cumulative_reserved_new_raw_bytes": initial_upper,
            "cumulative_semantics": (
                "nonrefundable_reserved_upper_bound_not_measured_exact"
            ),
            "reservation_refund_policy": (
                "never_refund_even_on_failure_timeout_or_retry"
            ),
            "raw_open_authorized": False,
            "raw_mrt_files_opened": 0,
            "database_write_operations": 0,
        },
    )
    genesis_artifact = cli.write_canonical_json(
        ledger / "GENESIS.json", genesis, kind="fixture"
    )
    genesis_ref = cli._published_ref(root, genesis_artifact)
    receipt = {
        "probe_raw_ledger": {
            "ledger_id": ledger_id,
            "root_path": "probe-ledger",
            "genesis_ref": genesis_ref,
            "prior_accounting": prior,
            "initial_cumulative_reserved_new_raw_bytes": initial_upper,
            "initial_observed_lower_bound_new_raw_bytes": initial_lower,
            "reservation_refund_policy": (
                "never_refund_even_on_failure_timeout_or_retry"
            ),
        }
    }
    cli.write_canonical_json(root / "PREPARATION.json", receipt, kind="fixture")
    return {
        "root": root,
        "receipt": receipt,
        "full-selection.json": selection,
        "full-window-bindings.json": bindings,
        "native-parser-contract.json": cli._parser_contract(generated_attestation()),
    }


class ExecutionPrepCliTests(unittest.TestCase):
    def freeze_args(
        self,
        *,
        source_run: Path,
        source: Path,
        source_sha256: str,
        output: Path,
    ) -> Namespace:
        provenance_paths = []
        for index in range(1, 4):
            provenance = source_run / f"attempt-{index}.json"
            provenance.write_text(
                json.dumps(
                    {"attempt_id": f"prior-attempt-{index}"},
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            provenance_paths.append(provenance)
        resolved_source = source.resolve(strict=False)
        derivation = {
            "schema_version": cli.PRIOR_ACCOUNTING_DERIVATION_SCHEMA_VERSION,
            "observed_lower_bound": {
                "operation": "sum_source_json_integer_terms",
                "terms": [
                    {
                        "label": "已观察新 raw 下界",
                        "source_path": str(resolved_source),
                        "source_sha256": source_sha256,
                        "json_pointer": "/new_raw_read_bytes",
                        "multiplier": 1,
                    }
                ],
            },
            "reserved_upper_bound": {
                "operation": "sum_source_json_integer_terms",
                "conservative_attempt_count": 3,
                "terms": [
                    {
                        "label": f"保守 seed attempt {index}",
                        "source_path": str(resolved_source),
                        "source_sha256": source_sha256,
                        "json_pointer": "/seed/size_bytes",
                        "multiplier": 1,
                        "attempt_id": f"prior-attempt-{index}",
                        "artifact_id_json_pointer": "/seed/artifact_id",
                        "artifact_file_sha256_json_pointer": "/seed/file_sha256",
                        "provenance_path": str(provenance.resolve()),
                        "provenance_sha256": hashlib.sha256(
                            provenance.read_bytes()
                        ).hexdigest(),
                    }
                    for index, provenance in enumerate(provenance_paths, start=1)
                ],
            },
            "upper_bound_semantics": "conservative_no_refund_pre_goal_reads",
        }
        evidence = source_run / "derivation.json"
        evidence.write_text(
            cli.canonical_json(derivation) + "\n", encoding="utf-8"
        )
        return Namespace(
            source_run_path=str(source_run),
            source_metadata_ref=[
                str(source),
                *(str(path) for path in provenance_paths),
            ],
            source_metadata_ref_sha256=[
                source_sha256,
                *(
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in provenance_paths
                ),
            ],
            derivation_evidence=str(evidence),
            derivation_evidence_sha256=hashlib.sha256(
                evidence.read_bytes()
            ).hexdigest(),
            codex_task_id="fixture-codex-task",
            frozen_at_utc="2026-07-22T12:00:00Z",
            history_limitation_zh=(
                "pre-ledger 历史 raw 读取不能逐次拆，"
                "保守上界不能冒充精确实测。"
            ),
            output_directory=str(output.parent),
        )

    def test_freeze_prior_accounting_is_create_only_and_non_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_run = root / "historical-run"
            source_run.mkdir()
            source = source_run / "metadata.json"
            seed_sha = "b" * 64
            source.write_text(
                cli.canonical_json(
                    {
                        "new_raw_read_bytes": 27_117_963,
                        "seed": {
                            "artifact_id": artifact_id_v1(seed_sha),
                            "file_sha256": seed_sha,
                            "size_bytes": 426_797_681,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            output = root / "prior-accounting.json"
            args = self.freeze_args(
                source_run=source_run,
                source=source,
                source_sha256=digest,
                output=output,
            )
            result = cli._run_freeze_prior_accounting(args)
            output = Path(result["receipt_path"])
            self.assertEqual(result["accounting_state"], "conservative_upper_bound")
            self.assertEqual(
                result["reserved_upper_bound_new_raw_bytes"], 1_280_393_043
            )
            receipt = cli._load_receipt(
                output,
                schema=cli.PREEXISTING_ACCOUNTING_SCHEMA_VERSION,
                fingerprint_schema=cli.PREEXISTING_ACCOUNTING_FINGERPRINT_SCHEMA,
            )
            self.assertEqual(receipt["accounting_state"], "conservative_upper_bound")
            self.assertNotEqual(
                receipt["observed_lower_bound_new_raw_bytes"],
                receipt["reserved_upper_bound_new_raw_bytes"],
            )
            self.assertEqual(receipt["database_write_operations"], 0)
            self.assertIn(
                digest,
                {row["sha256"] for row in receipt["source_metadata_refs"]},
            )
            self.assertEqual(
                receipt["derivation"]["reserved_upper_bound_proof"][
                    "conservative_attempt_count"
                ],
                3,
            )
            self.assertIn(
                receipt["receipt_fingerprint_sha256"], output.name
            )
            with self.assertRaises(FileExistsError):
                cli._run_freeze_prior_accounting(args)

    def test_freeze_prior_rejects_symlink_and_source_sha_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_run = root / "historical-run"
            source_run.mkdir()
            source = source_run / "metadata.json"
            source.write_text('{"bytes":1}\n', encoding="utf-8")
            original_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            source.write_text('{"bytes":2}\n', encoding="utf-8")
            drift_output = root / "drift.json"
            with self.assertRaisesRegex(cli.ExecutionPrepError, "SHA256 漂移"):
                cli._run_freeze_prior_accounting(
                    self.freeze_args(
                        source_run=source_run,
                        source=source,
                        source_sha256=original_sha,
                        output=drift_output,
                    )
                )
            self.assertFalse(drift_output.exists())

            seed_sha = "c" * 64
            source.write_text(
                cli.canonical_json(
                    {
                        "new_raw_read_bytes": 1,
                        "seed": {
                            "artifact_id": artifact_id_v1(seed_sha),
                            "file_sha256": seed_sha,
                            "size_bytes": 10,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            current_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            source_link = root / "metadata-link.json"
            source_link.symlink_to(source)
            with self.assertRaisesRegex(cli.ExecutionPrepError, "非符号链接普通文件"):
                cli._run_freeze_prior_accounting(
                    self.freeze_args(
                        source_run=source_run,
                        source=source_link,
                        source_sha256=current_sha,
                        output=root / "from-source-link.json",
                    )
                )

            output_directory_link = root / "prior-output-link"
            output_directory_link.symlink_to(source_run, target_is_directory=True)
            with self.assertRaisesRegex(
                cli.ExecutionPrepError, "非符号链接目录"
            ):
                cli._run_freeze_prior_accounting(
                    self.freeze_args(
                        source_run=source_run,
                        source=source,
                        source_sha256=current_sha,
                        output=output_directory_link / "unused.json",
                    )
                )

    def test_default_probe_indices_cover_window_and_are_bounded(self):
        self.assertEqual(cli._probe_indices(1928, ()), (0, 482, 964, 1446, 1927))
        with self.assertRaises(cli.ExecutionPrepError):
            cli._probe_indices(10, (0, 1, 2, 3, 4, 5))
        with self.assertRaises(cli.ExecutionPrepError):
            cli._probe_indices(10, (1, 1))

    def test_full_flow_projection_recomputes_real_p0_population(self):
        count = 1928
        updates = [artifact(index) for index in range(count)]
        probe_indices = (0, 482, 964, 1446, 1927)
        probe_sizes = (4_000_000, 5_000_000, 5_000_000, 5_000_000, 5_041_823)
        for index, size in zip(probe_indices, probe_sizes):
            updates[index]["size_bytes"] = size
        remaining_indices = [
            index for index in range(count) if index not in probe_indices
        ]
        remaining_total = 9_748_654_669 - sum(probe_sizes)
        quotient, remainder = divmod(remaining_total, len(remaining_indices))
        for ordinal, index in enumerate(remaining_indices):
            updates[index]["size_bytes"] = quotient + (ordinal < remainder)

        def rib(label: str, size: int) -> dict:
            digest = hashlib.sha256(label.encode()).hexdigest()
            return {
                "artifact_id": artifact_id_v1(digest),
                "file_sha256": digest,
                "relative_path": f"rrc25/{label}.gz",
                "size_bytes": size,
            }

        seed = rib("seed", 426_797_681)
        analysis_sizes = [444_540_560]
        quotient, remainder = divmod(
            8_637_188_818 - analysis_sizes[0], 19
        )
        analysis_sizes.extend(
            quotient + (index < remainder) for index in range(19)
        )
        analysis = [
            rib(f"analysis-{index}", size)
            for index, size in enumerate(analysis_sizes)
        ]
        baseline = rib("baseline", 426_719_364)
        selection = {
            "schema_version": "rrc25-country-outage-input-selection/v1",
            "status": "complete",
            "failures": [],
            "selection_id": "rsel_v1_" + "a" * 32,
            "semantic_fingerprint_sha256": "b" * 64,
            "window": {
                "start_utc": "2026-02-27T16:00:00Z",
                "end_exclusive_utc": "2026-03-06T08:40:00Z",
                "interval_semantics": "half_open",
                "granularity_seconds": 300,
            },
            "roles": {
                "state_seed_rib": seed,
                "baseline_reference_rib": baseline,
                "analysis_ribs": [seed, *analysis],
                "analysis_updates": updates,
            },
            "coverage": {
                "analysis_updates": {
                    "expected_count": count,
                    "observed_count": count,
                    "missing_count": 0,
                }
            },
        }
        projection = cli._build_full_flow_raw_projection(
            selection,
            prior_accounting={
                "reserved_upper_bound_new_raw_bytes": 1_280_393_043,
                "source_receipt_file_sha256": "c" * 64,
                "semantics": "conservative_no_refund_pre_goal_reads",
            },
        )
        components = projection["components"]
        self.assertEqual(components["native_probe"]["artifact_count"], 5)
        self.assertEqual(components["native_probe"]["bytes"], 24_041_823)
        self.assertEqual(components["full_update_replay"]["artifact_count"], 1928)
        self.assertEqual(components["full_update_replay"]["bytes"], 9_748_654_669)
        self.assertEqual(
            components["analysis_rib_replay_excluding_seed"]["artifact_count"],
            20,
        )
        self.assertEqual(
            components["analysis_rib_replay_excluding_seed"]["bytes"],
            8_637_188_818,
        )
        self.assertEqual(
            components["minimum_failure_retry_margin"]["bytes"], 444_540_560
        )
        self.assertLess(
            projection["projected_cumulative_new_raw_read_bytes"],
            50_000_000_000,
        )

    def test_parser_contract_binds_runtime_source_and_policy(self):
        contract = cli._parser_contract(generated_attestation())
        self.assertEqual(contract["backend"], "native")
        self.assertEqual(contract["binary_sha256"], "8" * 64)
        self.assertEqual(contract["adapter_source_sha256"], "9" * 64)

    def test_preexisting_import_uses_conservative_upper_not_fake_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "historical-run"
            source.mkdir()
            metadata = source / "metadata.json"
            seed_sha = "d" * 64
            metadata.write_text(
                cli.canonical_json(
                    {
                        "new_raw_read_bytes": 27_117_963,
                        "seed": {
                            "artifact_id": artifact_id_v1(seed_sha),
                            "file_sha256": seed_sha,
                            "size_bytes": 426_797_681,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            frozen = cli._run_freeze_prior_accounting(
                self.freeze_args(
                    source_run=source,
                    source=metadata,
                    source_sha256=hashlib.sha256(
                        metadata.read_bytes()
                    ).hexdigest(),
                    output=root / "unused.json",
                )
            )
            receipt_path = Path(frozen["receipt_path"])
            receipt = cli._load_receipt(
                receipt_path,
                schema=cli.PREEXISTING_ACCOUNTING_SCHEMA_VERSION,
                fingerprint_schema=cli.PREEXISTING_ACCOUNTING_FINGERPRINT_SCHEMA,
            )
            receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            accounting, loaded = cli._prior_accounting_for_prepare(
                Namespace(
                    prior_accounting_receipt=str(receipt_path),
                    prior_accounting_receipt_sha256=receipt_sha,
                    new_task_zero_genesis=False,
                )
            )
            self.assertEqual(loaded, receipt)
            self.assertEqual(
                accounting["observed_lower_bound_new_raw_bytes"], 27_117_963
            )
            self.assertEqual(
                accounting["reserved_upper_bound_new_raw_bytes"], 1_280_393_043
            )
            self.assertEqual(
                accounting["cumulative_reserved_new_raw_bytes"], 1_280_393_043
            )
            self.assertEqual(accounting["accounting_state"], "conservative_upper_bound")

    def test_probe_cli_has_no_bare_prior_or_output_reset(self):
        choices = cli.build_parser()._subparsers._group_actions[0].choices
        self.assertIn("freeze-prior-accounting", choices)
        freeze_actions = {
            action.dest for action in choices["freeze-prior-accounting"]._actions
        }
        self.assertTrue(
            {
                "source_run_path",
                "source_metadata_ref",
                "source_metadata_ref_sha256",
                "derivation_evidence",
                "derivation_evidence_sha256",
                "codex_task_id",
                "frozen_at_utc",
                "output_directory",
            }.issubset(freeze_actions)
        )
        self.assertNotIn("observed_lower_bound_new_raw_bytes", freeze_actions)
        self.assertNotIn("reserved_upper_bound_new_raw_bytes", freeze_actions)
        probe_actions = {action.dest for action in choices["probe-native"]._actions}
        self.assertNotIn("prior_new_raw_bytes", probe_actions)
        self.assertNotIn("output_directory", probe_actions)
        prepare_actions = {action.dest for action in choices["prepare"]._actions}
        self.assertIn("prior_accounting_receipt", prepare_actions)
        self.assertIn("prior_accounting_receipt_sha256", prepare_actions)
        self.assertIn("new_task_zero_genesis", prepare_actions)

    def test_iran_study_cannot_reset_preexisting_reads_to_zero(self):
        with self.assertRaisesRegex(cli.ExecutionPrepError, "禁止 zero genesis"):
            cli._prior_accounting_for_prepare(
                Namespace(
                    prior_accounting_receipt=None,
                    prior_accounting_receipt_sha256=None,
                    new_task_zero_genesis=True,
                ),
                study_id="iran-rrc25-country-outage-202602-v1",
            )

    def test_probe_reserves_all_raw_before_stream_and_returns_seed_prior(self):
        rows = tuple(artifact(index) for index in range(5))
        selection = {
            "selection_id": "rsel_v1_" + "a" * 32,
            "window": {
                "start_utc": "2026-02-27T16:00:00Z",
                "end_exclusive_utc": "2026-02-27T16:25:00Z",
            },
        }
        contract = cli._parser_contract(generated_attestation())
        prepared = {
            "full-selection.json": selection,
            "full-window-bindings.json": {
                "profile_sha256": "1" * 64,
                "input_selection_sha256": "2" * 64,
                "code_sha256": "3" * 64,
                "mapping_sha256": "4" * 64,
            },
            "native-parser-contract.json": contract,
        }
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            raw_root = base / "raw"
            raw_root.mkdir()
            prepared_root = base / "prepared"
            prepared_root.mkdir()
            prepared = prepared_probe_ledger(
                prepared_root,
                selection=selection,
                bindings={
                    "profile_sha256": "1" * 64,
                    "input_selection_sha256": "2" * 64,
                    "code_sha256": "3" * 64,
                    "mapping_sha256": "4" * 64,
                },
                initial_lower=100,
                initial_upper=1234,
            )
            args = Namespace(
                raw_root=str(raw_root),
                prepared_directory=str(prepared_root),
                attempt_id="probe_v1_" + "f" * 32,
                artifact_index=[0, 4],
                max_spool_bytes=1024 * 1024,
                native_max_frame_bytes=1024 * 1024,
                soft_timeout_seconds=120.0,
            )
            factory = FakeFactory(
                (rows[0], rows[4]),
                generated_attestation(),
                prepared_root / "probe-ledger/attempts",
            )
            ticks = iter((0.0, 0.5))
            with (
                mock.patch.object(cli, "_load_prepared", return_value=prepared),
                mock.patch.object(cli, "_selection_updates", return_value=rows),
                mock.patch.object(
                    cli,
                    "_native_factory",
                    side_effect=lambda *_args, **_kwargs: factory,
                ),
                mock.patch.object(
                    cli, "_validate_generated_parser_attestation"
                ),
                mock.patch.object(cli.time, "monotonic", side_effect=lambda: next(ticks)),
            ):
                result = cli._run_probe_worker(args)
            expected = 1234 + rows[0]["size_bytes"] + rows[4]["size_bytes"]
            self.assertEqual(result["next_seed_prior_new_raw_bytes"], expected)
            attempt_path = next(
                (prepared_root / "probe-ledger/attempts").glob("attempt-*.json")
            )
            outcome_path = next(
                (prepared_root / "probe-ledger/outcomes").glob("outcome-*.json")
            )
            attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
            self.assertEqual(attempt["prior_new_raw_bytes"], 1234)
            self.assertEqual(attempt["cumulative_reserved_new_raw_bytes"], expected)
            self.assertEqual(outcome["outcome"], "complete_single_pass")
            self.assertEqual(outcome["observed_compressed_bytes_sum"], 2004)
            self.assertEqual(
                result["probe_terminal_accounting"][
                    "initial_observed_lower_bound_new_raw_bytes"
                ],
                100,
            )
            self.assertEqual(
                result["probe_terminal_accounting"][
                    "initial_reserved_upper_bound_new_raw_bytes"
                ],
                1234,
            )
            normalized = bounded_pilot_worker._verified_probe_terminal_accounting(
                result["probe_terminal_accounting"],
                expected_prior_raw_bytes=expected,
                selection_id=selection["selection_id"],
                selection_sha256="2" * 64,
                code_identity_sha256="3" * 64,
            )
            self.assertEqual(
                normalized["cumulative_reserved_new_raw_bytes"], expected
            )

    def test_reconcile_closes_abandoned_attempt_without_opening_raw(self):
        rows = (artifact(0),)
        selection = {
            "selection_id": "rsel_v1_" + "a" * 32,
            "window": {
                "start_utc": "2026-02-27T16:00:00Z",
                "end_exclusive_utc": "2026-02-27T16:05:00Z",
            },
        }
        bindings = {
            "profile_sha256": "1" * 64,
            "input_selection_sha256": "2" * 64,
            "code_sha256": "3" * 64,
            "mapping_sha256": "4" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            raw_root = base / "raw"
            raw_root.mkdir()
            prepared_root = base / "prepared"
            prepared_root.mkdir()
            prepared = prepared_probe_ledger(
                prepared_root,
                selection=selection,
                bindings=bindings,
                initial_lower=100,
                initial_upper=1234,
            )
            with mock.patch.object(
                cli, "_selection_updates", return_value=rows
            ):
                genesis = cli._probe_terminal_accounting(prepared_root, prepared)
            attempt_id = "probe_v1_" + "e" * 32
            cumulative = 1234 + rows[0]["size_bytes"]
            attempt = cli._fingerprinted(
                cli.PROBE_ATTEMPT_SCHEMA_VERSION,
                cli.PROBE_ATTEMPT_FINGERPRINT_SCHEMA,
                {
                    "ledger_id": genesis["ledger_id"],
                    "sequence": 1,
                    "attempt_id": attempt_id,
                    "prepared_bindings": bindings,
                    "selection_id": selection["selection_id"],
                    "previous_terminal_ref": genesis["terminal_receipt_ref"],
                    "artifact_indices": [0],
                    "artifacts": [rows[0]],
                    "prior_new_raw_bytes": 1234,
                    "reserved_new_raw_bytes": rows[0]["size_bytes"],
                    "cumulative_reserved_new_raw_bytes": cumulative,
                    "reservation_refund_policy": (
                        "never_refund_even_on_failure_timeout_or_retry"
                    ),
                    "raw_open_authorized_after_this_receipt": True,
                    "database_write_operations": 0,
                },
            )
            cli._write_probe_receipt(
                prepared_root,
                cli._PROBE_ATTEMPTS_RELATIVE
                / cli._probe_receipt_filename(
                    kind="attempt", sequence=1, attempt_id=attempt_id
                ),
                attempt,
            )
            args = Namespace(
                raw_root=str(raw_root),
                prepared_directory=str(prepared_root),
            )
            with (
                mock.patch.object(cli, "_load_prepared", return_value=prepared),
                mock.patch.object(cli, "_selection_updates", return_value=rows),
            ):
                result = cli._reconcile_probe_attempt(
                    args,
                    attempt_id=attempt_id,
                    failure_type="FixtureSupervisorCrash",
                    failure_message="durable attempt 后监督器退出",
                )
            self.assertEqual(
                result["action"],
                "closed_unknown_interval_reservation_not_refunded",
            )
            accounting = result["probe_terminal_accounting"]
            self.assertEqual(accounting["attempt_count"], 1)
            self.assertEqual(accounting["outcome_count"], 1)
            self.assertEqual(accounting["cumulative_reserved_new_raw_bytes"], cumulative)
            self.assertEqual(
                accounting["probe_observed_upper_bound_new_raw_bytes"],
                rows[0]["size_bytes"],
            )
            outcome_path = next(
                (prepared_root / "probe-ledger/outcomes").glob("outcome-*.json")
            )
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
            self.assertEqual(
                outcome["observed_compressed_bytes_state"],
                "bounded_after_process_termination",
            )
            self.assertEqual(outcome["observed_compressed_bytes_lower_bound_sum"], 0)

    def test_seed_kill_before_checkpoint_retry_keeps_both_reservations(self):
        rows = (artifact(0),)
        selection = {
            "selection_id": "rsel_v1_" + "a" * 32,
            "window": {
                "start_utc": "2026-02-27T16:00:00Z",
                "end_exclusive_utc": "2026-02-27T16:05:00Z",
            },
        }
        bindings = {
            "profile_sha256": "1" * 64,
            "input_selection_sha256": "2" * 64,
            "code_sha256": "3" * 64,
            "mapping_sha256": "4" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            raw_root = base / "raw"
            raw_root.mkdir()
            prepared_root = base / "prepared"
            prepared_root.mkdir()
            prepared = prepared_probe_ledger(
                prepared_root,
                selection=selection,
                bindings=bindings,
                initial_lower=100,
                initial_upper=1234,
            )
            terminal = prepared_root / "probe-ledger/GENESIS.json"
            with (
                mock.patch.object(cli, "_load_prepared", return_value=prepared),
                mock.patch.object(cli, "_selection_updates", return_value=rows),
            ):
                first = cli.reserve_seed_raw_attempt(
                    prepared_root,
                    terminal,
                    raw_root=raw_root,
                    seed_artifact=rows[0],
                    attempt_id="seed_v1_" + "a" * 32,
                )
                # 模拟 durable ATTEMPT 后立即 SIGKILL：没有 checkpoint/outcome。
                self.assertTrue(
                    Path(
                        prepared_root,
                        first["attempt_ref"]["path"],
                    ).is_file()
                )
                killed = cli.reconcile_abandoned_seed_raw_attempt(
                    prepared_root,
                    terminal,
                    raw_root=raw_root,
                    seed_artifact=rows[0],
                    failure_type="SIGKILL",
                    failure_message="checkpoint 前被杀",
                )
                second = cli.reserve_seed_raw_attempt(
                    prepared_root,
                    terminal,
                    raw_root=raw_root,
                    seed_artifact=rows[0],
                    attempt_id="seed_v1_" + "b" * 32,
                )
                self.assertEqual(
                    first["cumulative_reserved_new_raw_bytes"],
                    1234 + rows[0]["size_bytes"],
                )
                self.assertEqual(
                    second["prior_cumulative_reserved_new_raw_bytes"],
                    first["cumulative_reserved_new_raw_bytes"],
                )
                self.assertEqual(
                    second["cumulative_reserved_new_raw_bytes"],
                    1234 + 2 * rows[0]["size_bytes"],
                )
                self.assertEqual(
                    killed["current_cumulative_reserved_new_raw_bytes"],
                    first["cumulative_reserved_new_raw_bytes"],
                )
                with self.assertRaisesRegex(
                    cli.ExecutionPrepError, "checkpoint ref 非法"
                ):
                    cli.close_seed_raw_attempt(
                        prepared_root,
                        terminal,
                        raw_root=raw_root,
                        seed_artifact=rows[0],
                        reservation=second,
                        checkpoint_ref={
                            "path": "/checkpoint/full-seed.json.gz",
                            "checkpoint_sequence": True,
                            "checkpoint_fingerprint_sha256": "f" * 64,
                        },
                        exact_seed_read=True,
                    )
                closed = cli.close_seed_raw_attempt(
                    prepared_root,
                    terminal,
                    raw_root=raw_root,
                    seed_artifact=rows[0],
                    reservation=second,
                    checkpoint_ref={
                        "path": "/checkpoint/full-seed.json.gz",
                        "checkpoint_sequence": 1,
                        "checkpoint_fingerprint_sha256": "f" * 64,
                    },
                    exact_seed_read=True,
                )
                verified = cli.verify_seed_raw_ledger(
                    prepared_root,
                    terminal,
                    raw_root=raw_root,
                    seed_artifact=rows[0],
                    expected_reservation=second,
                )
            self.assertEqual(closed["attempt_count"], 2)
            self.assertEqual(closed["outcome_count"], 2)
            self.assertEqual(verified["latest_reservation"], second)
            outcomes = sorted(
                (prepared_root / "probe-ledger/seed-outcomes").glob("*.json")
            )
            first_outcome = json.loads(outcomes[0].read_text(encoding="utf-8"))
            self.assertEqual(
                first_outcome["observed_compressed_bytes_state"],
                "bounded_after_process_termination",
            )
            self.assertEqual(
                first_outcome["reservation_refund_policy"],
                "never_refund_even_on_failure_timeout_or_retry",
            )
            unexpected = (
                prepared_root / "probe-ledger/seed-attempts/UNCLASSIFIED"
            )
            unexpected.write_text("evidence", encoding="utf-8")
            with (
                mock.patch.object(cli, "_load_prepared", return_value=prepared),
                mock.patch.object(cli, "_selection_updates", return_value=rows),
                self.assertRaisesRegex(
                    cli.ExecutionPrepError, "未分类或非普通文件"
                ),
            ):
                cli.verify_seed_raw_ledger(
                    prepared_root,
                    terminal,
                    raw_root=raw_root,
                    seed_artifact=rows[0],
                )

    def test_hidden_probe_worker_rejects_direct_invocation(self):
        with (
            mock.patch.dict(cli.os.environ, {}, clear=True),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(cli.main(["__probe-worker"]), 2)


if __name__ == "__main__":
    unittest.main()
