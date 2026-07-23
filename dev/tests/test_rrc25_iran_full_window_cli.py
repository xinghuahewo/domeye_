from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from contextlib import nullcontext, redirect_stdout
import io
import signal

from backend.data_pipeline.route_event.artifacts import artifact_id_v1


ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = ROOT / "dev/data_quality/rrc25_iran_full_window.py"
SPEC = importlib.util.spec_from_file_location("rrc25_iran_full_window_cli", CLI_PATH)
assert SPEC is not None and SPEC.loader is not None
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def _artifact(index: int) -> dict:
    at = datetime(2026, 2, 27, 16, 0, tzinfo=timezone.utc) + timedelta(
        minutes=5 * index
    )
    file_sha = hashlib.sha256(f"artifact-{index}".encode()).hexdigest()
    return {
        "artifact_id": artifact_id_v1(file_sha),
        "artifact_type": "update",
        "artifact_time_utc": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "collector_id": "rrc25",
        "compression": "gz",
        "file_sha256": file_sha,
        "relative_path": f"rrc25/updates.{at:%Y%m%d.%H%M}.gz",
        "size_bytes": 100 + index,
    }


def _selection(count: int = 2) -> dict:
    start = datetime(2026, 2, 27, 16, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5 * count)
    updates = [_artifact(index) for index in range(count)]
    semantic = {
        "schema_version": cli.SELECTION_SCHEMA_VERSION,
        "study_id": "iran-rrc25-fixture",
        "collector_id": "rrc25",
        "country_code": "IR",
        "window": {
            "start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_exclusive_utc": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "interval_semantics": "half_open",
            "granularity_seconds": 300,
        },
        "parent_manifest_fingerprint_sha256": "a" * 64,
        "status": "complete",
        "roles": {
            "state_seed_rib": None,
            "baseline_reference_rib": None,
            "catch_up_updates": [],
            "analysis_updates": updates,
            "analysis_ribs": [],
        },
        "coverage": {
            "analysis_updates": {
                "expected_count": count,
                "observed_count": count,
                "missing_count": 0,
            },
            "analysis_ribs": {
                "expected_count": 0,
                "observed_count": 0,
                "missing_count": 0,
            },
            "baseline_reference_rib": {
                "expected_count": 1,
                "observed_count": 0,
            },
        },
        "selected_unique_artifact_count": count,
        "selected_unique_size_bytes": sum(row["size_bytes"] for row in updates),
        "failures": [],
    }
    fingerprint = hashlib.sha256(
        cli.canonical_json(semantic).encode("utf-8")
    ).hexdigest()
    selection_id = "rsel_v1_" + hashlib.sha256(
        cli.canonical_json(
            {"schema": cli.SELECTION_ID_SCHEMA, "selection": semantic}
        ).encode("utf-8")
    ).hexdigest()[:32]
    return {
        "selection_id": selection_id,
        **semantic,
        "semantic_fingerprint_sha256": fingerprint,
    }


def _bindings(selection: dict) -> dict:
    return {
        "profile_sha256": "1" * 64,
        "input_selection_sha256": selection["semantic_fingerprint_sha256"],
        "code_sha256": "2" * 64,
        "mapping_sha256": "3" * 64,
    }


def _head(index: int, total: int) -> SimpleNamespace:
    return SimpleNamespace(
        next_artifact_index=index,
        receipt={"total_artifacts": total},
        root=Path("/journal"),
        receipt_path=f"receipts/boundary-{index:04d}.json",
        receipt_sha256=f"{index + 5:064x}",
    )


def _native_contract() -> dict:
    semantic = {
        "schema_version": "rrc25-full-window-parser-attestation/v1",
        "backend": "native",
        "parser_name": cli.NATIVE_UPDATE_PARSER_NAME,
        "parser_version": cli.NATIVE_UPDATE_PARSER_VERSION,
        "binary_sha256": "8" * 64,
        "binary_execution_policy": cli.NATIVE_UPDATE_EXECUTION_POLICY,
        "adapter_source_sha256": "9" * 64,
    }
    return {
        **semantic,
        "semantic_fingerprint_sha256": hashlib.sha256(
            cli.canonical_json(semantic).encode("utf-8")
        ).hexdigest(),
    }


def _generated_native_attestation() -> dict:
    semantic = {
        "schema_version": "parser_attestation_v1",
        "parser_name": cli.NATIVE_UPDATE_PARSER_NAME,
        "parser_version": cli.NATIVE_UPDATE_PARSER_VERSION,
        "parser_binary_sha256": "8" * 64,
        "adapter_name": "domeye_native_update_adapter",
        "adapter_version": cli.NATIVE_UPDATE_PARSER_VERSION,
        "adapter_source_sha256": "9" * 64,
        "binary_execution_policy": cli.NATIVE_UPDATE_EXECUTION_POLICY,
        "configuration": {"fixture": True},
        "configuration_sha256": hashlib.sha256(b"fixture").hexdigest(),
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
            ).encode("utf-8")
        ).hexdigest(),
    }


def _receipt_payload(schema: str, fingerprint_schema: str, semantic: dict) -> dict:
    normalized = {**semantic, "schema_version": schema}
    return {
        **normalized,
        "receipt_fingerprint_sha256": hashlib.sha256(
            cli.canonical_json(
                {"schema": fingerprint_schema, "receipt": normalized}
            ).encode("utf-8")
        ).hexdigest(),
    }


def _write_canonical(path: Path, payload: dict) -> None:
    path.write_text(cli.canonical_json(payload) + "\n", encoding="utf-8")


class FullWindowCliTest(unittest.TestCase):
    def _retirement_case(self, parent: Path):
        checkpoint_directory = parent / "checkpoint"
        receipt_directory = parent / "retirement"
        raw_directory = parent / "raw"
        checkpoint_directory.mkdir()
        receipt_directory.mkdir()
        raw_directory.mkdir()
        checkpoint_path = checkpoint_directory / "full-seed.json.gz"
        checkpoint_path.write_bytes(b"checkpoint")
        raw_path = raw_directory / "seed.gz"
        raw_path.write_bytes(b"compressed-seed")
        raw_stat = raw_path.stat()
        raw_identity = {
            name: getattr(raw_stat, name) for name in cli._FILE_IDENTITY_FIELDS
        }
        checkpoint_fingerprint = "7" * 64
        checkpoint_sequence = 3
        prior_raw = 100
        seed_size = raw_path.stat().st_size
        checkpoint_cumulative = prior_raw + seed_size
        cumulative_after = checkpoint_cumulative + seed_size
        spool_path = checkpoint_directory / "retired-spool.mrt"
        checkpoint = {
            "path": str(checkpoint_path),
            "checkpoint_sequence": checkpoint_sequence,
            "checkpoint_fingerprint_sha256": checkpoint_fingerprint,
        }
        spool = {
            "path": str(spool_path),
            "sha256": "6" * 64,
            "size_bytes": 10,
            "stable_file_identity": {
                "st_dev": 1,
                "st_ino": 2,
                "st_size": 10,
                "st_mtime_ns": 3,
                "st_ctime_ns": 4,
            },
        }
        attempt_id = "fixture-attempt"
        attempt = _receipt_payload(
            cli.SEED_RETIREMENT_ATTEMPT_SCHEMA,
            cli.SEED_RETIREMENT_ATTEMPT_FINGERPRINT_SCHEMA,
            {
                "operation": "seed_spool_retirement_raw_verification_attempt",
                "attempt_id": attempt_id,
                "status": "raw_verification_reserved_outcome_unknown_until_success_receipt",
                "selection_id": "fixture-selection",
                "checkpoint": checkpoint,
                "spool": spool,
                "raw_accounting": {
                    "checkpoint_cumulative_new_raw_read_bytes": checkpoint_cumulative,
                    "full_artifact_reserved_bytes": seed_size,
                    "cumulative_new_raw_read_bytes_after_reservation": cumulative_after,
                },
            },
        )
        attempt_path = receipt_directory / "attempt.json"
        _write_canonical(attempt_path, attempt)
        receipt = _receipt_payload(
            cli.SEED_RETIREMENT_RECEIPT_SCHEMA,
            cli.SEED_RETIREMENT_RECEIPT_FINGERPRINT_SCHEMA,
            {
                "operation": "seed_spool_retirement",
                "checkpoint": checkpoint,
                "spool": spool,
                "compressed_raw": {
                    "path": str(raw_path),
                    "artifact_id": "art_v1_fixture",
                    "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                    "size_bytes": seed_size,
                    "hash_verified": True,
                    "stable_file_identity": raw_identity,
                },
                "raw_verification_attempt_receipt": {
                    "path": str(attempt_path),
                    "attempt_id": attempt_id,
                    "receipt_fingerprint_sha256": attempt[
                        "receipt_fingerprint_sha256"
                    ],
                    "status": attempt["status"],
                },
                "recoverable_by_rebuild_from_compressed_raw": True,
                "resource_accounting": {
                    "checkpoint_cumulative_new_raw_read_bytes": checkpoint_cumulative,
                    "retirement_verification_new_raw_read_bytes": seed_size,
                    "cumulative_new_raw_read_bytes_after_retirement_verification": cumulative_after,
                },
            },
        )
        receipt_path = receipt_directory / "retirement.json"
        _write_canonical(receipt_path, receipt)
        args = Namespace(
            seed_spool_retirement_receipt=str(receipt_path),
            journal_root=str(parent / "journal"),
            additional_pre_update_raw_read_bytes=seed_size,
        )
        bootstrap = SimpleNamespace(
            checkpoint_path=checkpoint_path,
            checkpoint_sequence=checkpoint_sequence,
            checkpoint_fingerprint_sha256=checkpoint_fingerprint,
            prior_raw_read_bytes=prior_raw,
            seed_artifact_read_bytes=seed_size,
            seed_artifact_ref={
                "artifact_id": "art_v1_fixture",
                "file_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                "size_bytes": seed_size,
            },
        )
        return args, bootstrap, receipt_path, spool_path

    def test_process_tree_helper_signals_new_session_group(self) -> None:
        process = SimpleNamespace(
            pid=43210,
            terminate=mock.Mock(),
            kill=mock.Mock(),
        )
        with mock.patch.object(cli.os, "killpg") as killpg:
            cli._signal_process_tree(process, signal.SIGTERM)
            cli._signal_process_tree(process, signal.SIGKILL)
        self.assertEqual(
            killpg.call_args_list,
            [mock.call(43210, signal.SIGTERM), mock.call(43210, signal.SIGKILL)],
        )
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    def test_public_init_main_only_calls_supervisor(self) -> None:
        argv = [
            "init",
            "--journal-root", "/tmp/full-window-fixture",
            "--bindings", "bindings.json",
            "--profile", "profile.json",
            "--selection", "selection.json",
            "--full-seed-checkpoint", "seed.json",
            "--compatible-mapping", "compatible.json",
            "--revised-mapping", "revised.json",
            "--seed-spool-attestation", "attestation.json",
            "--seed-spool-retirement-receipt", "retirement.json",
            "--window-end-exclusive", "2026-04-05T14:40:00Z",
            "--code-sha256", "2" * 64,
            "--parser-backend", "native",
            "--parser-attestation", "parser.json",
            "--probe-throughput-receipt", "throughput.json",
            "--probe-throughput-receipt-sha256", "4" * 64,
            "--full-flow-raw-projection", "projection.json",
            "--full-flow-raw-projection-sha256", "5" * 64,
            "--run-id", "research_run_v1_" + "a" * 24,
            "--additional-pre-update-raw-read-bytes", "0",
        ]
        with (
            mock.patch.object(
                cli, "_supervise_init", return_value={"ok": True, "command": "init"}
            ) as supervise,
            mock.patch.object(cli, "_run_init") as direct,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.main(argv), 0)
        supervise.assert_called_once()
        direct.assert_not_called()

    def test_init_requires_bound_throughput_and_has_no_bare_bootstrap_rate(self):
        choices = cli.build_parser()._subparsers._group_actions[0].choices
        actions = {action.dest for action in choices["init"]._actions}
        self.assertNotIn("bootstrap_bytes_per_second", actions)
        self.assertTrue(
            {
                "parser_backend",
                "parser_attestation",
                "probe_throughput_receipt",
                "probe_throughput_receipt_sha256",
                "full_flow_raw_projection",
                "full_flow_raw_projection_sha256",
            }.issubset(actions)
        )

    def test_hidden_workers_reject_direct_invocation_without_capability(self) -> None:
        for argv, target in (
            (["__init-worker"], "_run_init"),
            (
                [
                    "__existing-worker",
                    "verify",
                    "--journal-root",
                    "/tmp/journal",
                    "--bindings",
                    "bindings.json",
                ],
                "_run_verify",
            ),
        ):
            with self.subTest(argv=argv), mock.patch.dict(
                cli.os.environ, {}, clear=True
            ), mock.patch.object(cli, target) as direct, redirect_stdout(
                io.StringIO()
            ), mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(cli.main(argv), 2)
                direct.assert_not_called()

    def test_protected_old_project_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(cli.FullWindowCliError, "受保护"):
            cli._assert_journal_root_allowed(
                "/home/bgpdata/Domeye-Core/research/full-window"
            )

    def test_selection_updates_rejects_gap_instead_of_silent_reorder(self) -> None:
        selection = _selection(2)
        selection["roles"]["analysis_updates"][1]["artifact_time_utc"] = (
            "2026-02-27T16:10:00Z"
        )
        with self.assertRaisesRegex(cli.FullWindowCliError, "严格连续"):
            cli._selection_updates(selection)

    def test_selection_binding_recomputes_semantic_payload(self) -> None:
        selection = _selection(1)
        bindings = _bindings(selection)
        cli._verify_selection_binding(selection, bindings)
        selection["roles"]["analysis_updates"][0]["size_bytes"] += 1
        with self.assertRaisesRegex(cli.FullWindowCliError, "内容指纹"):
            cli._verify_selection_binding(selection, bindings)

    def test_init_hard_timeout_quarantines_even_a_current_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            args = Namespace(
                journal_root=str(root),
                bindings="bindings.json",
                profile="profile.json",
                selection="selection.json",
                full_seed_checkpoint="seed.json",
                compatible_mapping="compatible.json",
                revised_mapping="revised.json",
                seed_spool_attestation="attestation.json",
                seed_spool_retirement_receipt="retirement.json",
                window_end_exclusive="2026-04-05T14:40:00Z",
                code_sha256="2" * 64,
                parser_backend="native",
                parser_attestation="parser.json",
                probe_throughput_receipt="throughput.json",
                probe_throughput_receipt_sha256="4" * 64,
                full_flow_raw_projection="projection.json",
                full_flow_raw_projection_sha256="5" * 64,
                run_id="research_run_v1_" + "a" * 24,
                additional_pre_update_raw_read_bytes=0,
            )

            class HungProcess:
                returncode = None

                def __init__(self) -> None:
                    self.calls = 0
                    self.terminated = False
                    self.killed = False
                    root.mkdir()
                    (root / "CURRENT").write_text("看似成功但父进程未确认", encoding="utf-8")

                def communicate(self, timeout=None):
                    self.calls += 1
                    if self.calls <= 2:
                        raise subprocess.TimeoutExpired("fixture", timeout)
                    self.returncode = -9
                    return "", ""

                def terminate(self) -> None:
                    self.terminated = True

                def kill(self) -> None:
                    self.killed = True

            process_holder = []

            def start_process(*_args, **_kwargs):
                process = HungProcess()
                process_holder.append(process)
                return process

            with self.assertRaisesRegex(cli.FullWindowCliError, "硬杀"):
                cli._supervise_init(
                    args,
                    soft_timeout_seconds=0.01,
                    hard_timeout_seconds=0.02,
                    popen_factory=start_process,
                )
            process = process_holder[0]
            self.assertTrue(process.terminated)
            self.assertTrue(process.killed)
            self.assertFalse(root.exists())
            quarantined = list(Path(directory).glob("journal.timed-out-*"))
            self.assertEqual(len(quarantined), 1)
            self.assertTrue((quarantined[0] / "CURRENT").is_file())

    def test_run_timeout_preserves_committed_current_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "journal"
            root.mkdir()
            current = root / "CURRENT"
            current.write_text("已闭合边界", encoding="utf-8")

            class SlowProcess:
                returncode = None

                def __init__(self) -> None:
                    self.calls = 0

                def communicate(self, timeout=None):
                    self.calls += 1
                    if self.calls == 1:
                        raise subprocess.TimeoutExpired("fixture", timeout)
                    self.returncode = -15
                    return "", ""

                def terminate(self) -> None:
                    pass

                def kill(self) -> None:
                    raise AssertionError("软停后已退出，不应硬杀")

            with self.assertRaisesRegex(cli.FullWindowCliError, "可从 CURRENT 恢复"):
                cli._supervise_existing_command(
                    ("verify", "--journal-root", str(root), "--bindings", "b.json"),
                    soft_timeout_seconds=0.01,
                    hard_timeout_seconds=0.02,
                    popen_factory=lambda *_args, **_kwargs: SlowProcess(),
                )
            self.assertEqual(current.read_text(encoding="utf-8"), "已闭合边界")

    def test_leader_exit_does_not_skip_hard_kill_of_surviving_process_group(self) -> None:
        class LeaderExitedButChildSurvives:
            pid = 43211
            returncode = None

            def __init__(self) -> None:
                self.calls = 0

            def communicate(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired("fixture", timeout)
                self.returncode = -signal.SIGTERM
                return "", ""

        process = LeaderExitedButChildSurvives()
        now = [0.0]
        observed_signals: list[int] = []

        def fake_killpg(_pgid: int, requested_signal: int) -> None:
            observed_signals.append(requested_signal)

        def sleep(seconds: float) -> None:
            now[0] += seconds

        with mock.patch.object(cli.os, "killpg", side_effect=fake_killpg):
            with self.assertRaisesRegex(cli.FullWindowCliError, "硬杀"):
                cli._supervise_existing_command(
                    (
                        "verify",
                        "--journal-root",
                        "/tmp/journal",
                        "--bindings",
                        "bindings.json",
                    ),
                    soft_timeout_seconds=0.01,
                    hard_timeout_seconds=0.02,
                    popen_factory=lambda *_args, **_kwargs: process,
                    clock=lambda: now[0],
                    sleeper=sleep,
                )
        self.assertEqual(observed_signals[0], signal.SIGTERM)
        self.assertIn(0, observed_signals)
        self.assertEqual(observed_signals[-1], signal.SIGKILL)

    def test_native_factory_never_requires_bgpdump_identity(self) -> None:
        class NativeArgs:
            parser_backend = "native"
            raw_root = "/raw"
            max_spool_bytes = 1024
            native_max_frame_bytes = 64 * 1024 * 1024

            @property
            def bgpdump_path(self):
                raise AssertionError("native backend 不得读取 bgpdump path")

            @property
            def bgpdump_sha256(self):
                raise AssertionError("native backend 不得读取 bgpdump SHA")

        generated = _generated_native_attestation()
        factory = SimpleNamespace(parser_attestation=generated)
        with (
            mock.patch.object(
                cli,
                "make_native_update_record_stream_factory",
                return_value=factory,
            ) as native,
            mock.patch.object(cli, "make_bgpdump_record_stream_factory") as bgpdump,
        ):
            actual_factory, actual_attestation = cli._make_stream_factory(
                NativeArgs(),
                _artifact(0),
                parser_contract=_native_contract(),
            )
        self.assertIs(actual_factory, factory)
        self.assertEqual(actual_attestation, generated)
        native.assert_called_once()
        bgpdump.assert_not_called()

    def test_native_generated_attestation_mismatch_is_rejected(self) -> None:
        generated = _generated_native_attestation()
        generated["adapter_source_sha256"] = "a" * 64
        semantic = dict(generated)
        semantic.pop("attestation_fingerprint_sha256")
        generated["attestation_fingerprint_sha256"] = hashlib.sha256(
            cli.canonical_json(
                {
                    "schema": "parser_attestation_fingerprint_v1",
                    "attestation": semantic,
                }
            ).encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(cli.FullWindowCliError, "native source"):
            cli._validate_generated_parser_attestation(
                generated,
                contract=_native_contract(),
            )

    def test_seed_retirement_receipt_closes_init_raw_accounting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args, bootstrap, _receipt, _spool = self._retirement_case(
                Path(directory)
            )
            result = cli._validate_seed_retirement_for_init(
                args,
                selection={"selection_id": "fixture-selection"},
                bootstrap=bootstrap,
            )
        self.assertEqual(
            result["cumulative_after_retirement_raw_bytes"],
            bootstrap.prior_raw_read_bytes
            + 2 * bootstrap.seed_artifact_read_bytes,
        )
        self.assertGreater(result["retained_external_temporary_bytes"], 0)
        binding = result["seed_retirement_binding"]
        self.assertEqual(
            binding["schema_version"],
            "rrc25-seed-retirement-bootstrap-binding/v1",
        )
        self.assertTrue(binding["spool_absence_verified"])
        self.assertEqual(
            binding["success_receipt_file_sha256"],
            hashlib.sha256(
                (
                    cli.canonical_json(binding["success_receipt"]) + "\n"
                ).encode("utf-8")
            ).hexdigest(),
        )

    def test_seed_retirement_rejects_tamper_existing_spool_and_bad_delta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args, bootstrap, receipt_path, spool_path = self._retirement_case(
                Path(directory)
            )
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            payload["receipt_fingerprint_sha256"] = "0" * 64
            _write_canonical(receipt_path, payload)
            with self.assertRaisesRegex(cli.FullWindowCliError, "fingerprint"):
                cli._validate_seed_retirement_for_init(
                    args,
                    selection={"selection_id": "fixture-selection"},
                    bootstrap=bootstrap,
                )

        with tempfile.TemporaryDirectory() as directory:
            args, bootstrap, _receipt_path, spool_path = self._retirement_case(
                Path(directory)
            )
            spool_path.write_bytes(b"still-active")
            with self.assertRaisesRegex(cli.FullWindowCliError, "仍存在"):
                cli._validate_seed_retirement_for_init(
                    args,
                    selection={"selection_id": "fixture-selection"},
                    bootstrap=bootstrap,
                )

        with tempfile.TemporaryDirectory() as directory:
            args, bootstrap, _receipt_path, _spool_path = self._retirement_case(
                Path(directory)
            )
            args.additional_pre_update_raw_read_bytes += 1
            with self.assertRaisesRegex(cli.FullWindowCliError, "精确等于"):
                cli._validate_seed_retirement_for_init(
                    args,
                    selection={"selection_id": "fixture-selection"},
                    bootstrap=bootstrap,
                )

    def test_seed_retirement_external_temp_equal_5gb_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args, bootstrap, _receipt, _spool = self._retirement_case(
                Path(directory)
            )
            with mock.patch.object(
                cli,
                "_directory_regular_bytes",
                side_effect=(4_900_000_000, 100_000_000),
            ), self.assertRaisesRegex(cli.FullWindowCliError, "5GB"):
                cli._validate_seed_retirement_for_init(
                    args,
                    selection={"selection_id": "fixture-selection"},
                    bootstrap=bootstrap,
                )

    def test_run_bounded_advances_only_committed_artifact_boundaries(self) -> None:
        selection = _selection(2)
        bindings = _bindings(selection)
        args = Namespace(
            profile="profile.json",
            selection="selection.json",
            bindings="bindings.json",
            compatible_mapping="compatible.json",
            revised_mapping="revised.json",
            journal_root="journal",
            max_artifacts=2,
            global_soft_stop_seconds=540.0,
            max_spool_bytes=4_000_000_000,
            retained_seed_spool_bytes=0,
            admission_seconds=420.0,
            max_raw_bytes=50_000_000_000,
            runtime_check_interval_records=256,
            parser_backend="native",
            parser_attestation="parser.json",
        )
        initial = _head(0, 2)
        mapping = object()
        raw_union = SimpleNamespace(raw_retention_membership=lambda _asn: False)
        processed: list[int] = []

        def process(_args: Namespace, *, head: SimpleNamespace, row: dict, **_kw):
            processed.append(head.next_artifact_index)
            return SimpleNamespace(head=_head(head.next_artifact_index + 1, 2))

        ticks = iter((0.0, 0.0, 1.0))
        with (
            mock.patch.object(cli, "load_json_metadata", return_value=selection),
            mock.patch.object(cli, "_load_bindings", return_value=bindings),
            mock.patch.object(cli, "_verify_profile_selection_binding"),
            mock.patch.object(
                cli,
                "_load_mapping_context",
                return_value=(mapping, mapping, raw_union),
            ),
            mock.patch.object(cli, "load_full_window_head", return_value=initial),
            mock.patch.object(
                cli, "full_window_execution_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                cli,
                "reconcile_abandoned_active_attempt",
                return_value={"action": "none", "head": initial},
            ),
            mock.patch.object(
                cli,
                "_load_parser_attestation",
                return_value={"semantic_fingerprint_sha256": "4" * 64},
            ),
            mock.patch.object(cli, "_current_code_identity", return_value={}),
            mock.patch.object(cli, "_load_execution_contract", return_value={}),
            mock.patch.object(
                cli,
                "plan_artifact_admission",
                return_value=SimpleNamespace(
                    allowed=True, estimated_process_seconds=2.0, reason=None
                ),
            ),
            mock.patch.object(cli, "_process_one", side_effect=process),
            mock.patch.object(
                cli, "cumulative_reserved_raw_bytes", return_value=2048
            ),
        ):
            result = cli._run_updates(
                args, bounded=True, clock=lambda: next(ticks)
            )

        self.assertEqual(processed, [0, 1])
        self.assertEqual(result["completed_this_process"], 2)
        self.assertEqual(result["next_artifact_index"], 2)
        self.assertTrue(result["window_complete"])
        self.assertEqual(result["database_write_operations"], 0)

    def test_run_bounded_stops_before_reservation_when_global_time_is_short(self) -> None:
        selection = _selection(1)
        bindings = _bindings(selection)
        args = Namespace(
            profile="profile.json",
            selection="selection.json",
            bindings="bindings.json",
            compatible_mapping="compatible.json",
            revised_mapping="revised.json",
            journal_root="journal",
            max_artifacts=1,
            global_soft_stop_seconds=10.0,
            max_spool_bytes=4_000_000_000,
            retained_seed_spool_bytes=0,
            admission_seconds=420.0,
            max_raw_bytes=50_000_000_000,
            runtime_check_interval_records=256,
            parser_backend="native",
            parser_attestation="parser.json",
        )
        initial = _head(0, 1)
        with (
            mock.patch.object(cli, "load_json_metadata", return_value=selection),
            mock.patch.object(cli, "_load_bindings", return_value=bindings),
            mock.patch.object(cli, "_verify_profile_selection_binding"),
            mock.patch.object(
                cli,
                "_load_mapping_context",
                return_value=(object(), object(), object()),
            ),
            mock.patch.object(cli, "load_full_window_head", return_value=initial),
            mock.patch.object(
                cli, "full_window_execution_lock", return_value=nullcontext()
            ),
            mock.patch.object(
                cli,
                "reconcile_abandoned_active_attempt",
                return_value={"action": "none", "head": initial},
            ),
            mock.patch.object(
                cli,
                "_load_parser_attestation",
                return_value={"semantic_fingerprint_sha256": "4" * 64},
            ),
            mock.patch.object(cli, "_current_code_identity", return_value={}),
            mock.patch.object(cli, "_load_execution_contract", return_value={}),
            mock.patch.object(
                cli,
                "plan_artifact_admission",
                return_value=SimpleNamespace(
                    allowed=True, estimated_process_seconds=9.0, reason=None
                ),
            ),
            mock.patch.object(cli, "_process_one") as process,
            mock.patch.object(
                cli, "cumulative_reserved_raw_bytes", return_value=1024
            ),
        ):
            ticks = iter((0.0, 2.0))
            result = cli._run_updates(
                args, bounded=True, clock=lambda: next(ticks)
            )
        process.assert_not_called()
        self.assertEqual(result["completed_this_process"], 0)
        self.assertIn("软边界", result["stop_reason"])


if __name__ == "__main__":
    unittest.main()
