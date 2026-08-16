from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from dev.data_quality import rrc25_iran_finalize as cli


def _workspace_product_args() -> dict[str, str]:
    return {
        "profile": "profile.json",
        "source_fact": "source-fact.json",
        "incident_policy": "incident-policy.json",
        "compatible_mapping": "compatible.json",
        "revised_mapping": "revised.json",
        "code_identity": "code-identity.json",
        "selection": "selection.json",
        "claim_inventory": "claims.json",
        "bindings": "bindings.json",
    }


class IranFinalizeCliTests(unittest.TestCase):
    def test_parser_exposes_pending_finalize_and_explicit_acceptance_receipt(self):
        parser = cli.build_parser()
        help_text = parser.format_help()
        self.assertIn("finalize", help_text)
        self.assertIn("reproduce", help_text)
        self.assertIn("verify-acceptance", help_text)
        self.assertIn("workspace-init", help_text)
        self.assertIn("workspace-start", help_text)
        self.assertIn("workspace-resume", help_text)
        self.assertIn("workspace-reconcile", help_text)
        self.assertIn("workspace-verify", help_text)
        self.assertIn("workspace-assemble", help_text)
        self.assertIn("workspace-reproduce", help_text)
        self.assertIn("workspace-reconcile-publication", help_text)
        self.assertIn("workspace-verify-package", help_text)
        self.assertEqual(
            parser.parse_args(["verify", "--workspace-root", "work"]).command,
            "verify",
        )

    def test_verify_only_runs_offline_package_and_optional_resource_verifier(self):
        args = Namespace(
            command="verify-only",
            package_root="/tmp/package",
            resource_receipt="/tmp/resource.json",
        )
        with (
            patch.object(cli, "verify_finalized_package", return_value={"verified": True}) as verify,
            patch.object(cli, "verify_finalization_resource_receipt") as resource,
        ):
            result = cli._run(args)
        verify.assert_called_once_with("/tmp/package")
        resource.assert_called_once_with("/tmp/package", "/tmp/resource.json")
        self.assertTrue(result["finalization_resource_receipt_verified"])

    def test_verify_only_is_inside_process_supervisor(self):
        args = Namespace(
            command="verify-only",
            package_root="/tmp/package",
            resource_receipt=None,
        )
        supervisor = MagicMock()
        supervisor.__enter__.return_value = supervisor
        with (
            patch.object(cli, "_Supervisor", return_value=supervisor) as factory,
            patch.object(cli, "verify_finalized_package", return_value={"verified": True}),
        ):
            cli._run(args)
        factory.assert_called_once_with()
        supervisor.__enter__.assert_called_once_with()
        supervisor.__exit__.assert_called_once()

    def test_verify_acceptance_keeps_v1_v2_dispatch_inside_supervisor(self):
        args = Namespace(
            command="verify-acceptance", acceptance_receipt="accepted.json"
        )
        receipt = {
            "acceptance_state": "accepted",
            "semantic_core_sha256": "a" * 64,
        }
        supervisor = MagicMock()
        supervisor.__enter__.return_value = supervisor
        with (
            patch.object(cli, "_Supervisor", return_value=supervisor),
            patch.object(
                cli,
                "verify_reproduction_acceptance_receipt",
                return_value=receipt,
            ) as verify,
        ):
            result = cli._run(args)
        verify.assert_called_once_with("accepted.json")
        supervisor.__enter__.assert_called_once_with()
        supervisor.__exit__.assert_called_once()
        self.assertEqual(result["acceptance_state"], "accepted")

    def test_finalize_never_reports_accepted_before_second_directory(self):
        args = Namespace(
            command="finalize",
            journal_root="journal",
            output_root="package",
            resource_receipt=None,
        )
        package = SimpleNamespace(
            root=Path("package"),
            manifest={"release_id": "release-v1"},
            semantic_core_sha256="a" * 64,
            resource_receipt_path=Path("package.finalization-resource-receipt.json"),
        )
        with (
            patch.object(cli, "_input_values", return_value={}),
            patch.object(cli, "finalize_full_window_package", return_value=package),
        ):
            result = cli._run(args)
        self.assertEqual(result["acceptance_state"], "not_accepted")
        self.assertEqual(result["reproduction_state"], "pending")

    def test_soft_stop_refuses_new_content_phase_but_not_atomic_publish_phase(self):
        supervisor = cli._Supervisor()
        supervisor.soft_crossed.set()
        with self.assertRaisesRegex(cli.FinalizeCliError, "540 秒"):
            supervisor.hook("after_content_publish", Path("staging"))
        supervisor.hook("before_atomic_directory_publish", Path("staging"))

    def test_workspace_child_builds_views_and_honors_420_second_boundary(self):
        args = Namespace(
            command="_workspace-child",
            workspace_root="workspace",
            compatible_mapping="compatible.json",
            revised_mapping="revised.json",
            max_slots=7,
        )
        run = SimpleNamespace(
            workspace_root=Path("workspace"),
            completed_slots=2,
            total_slots=10,
            segment_slots_committed=2,
            stop_reason="segment_slot_limit",
            sealed=False,
            terminal_path=None,
            deep_verification_path=None,
        )
        compatible = object()
        revised = object()
        with (
            patch.object(
                cli, "_workspace_mapping_views", return_value=(compatible, revised)
            ),
            patch.object(
                cli, "run_finalization_workspace_segment", return_value=run
            ) as execute,
        ):
            result = cli._run(args)
        execute.assert_called_once_with(
            "workspace",
            compatible_mapping=compatible,
            revised_mapping=revised,
            max_slots=7,
            planned_stop_seconds=420.0,
        )
        self.assertEqual(result["segment_slots_committed"], 2)
        self.assertFalse(result["sealed"])

    def test_workspace_start_uses_real_child_process_command(self):
        args = Namespace(
            command="workspace-start",
            workspace_root="workspace",
            compatible_mapping="compatible.json",
            revised_mapping="revised.json",
            max_slots=3,
        )
        child = MagicMock()
        child.pid = 12345
        child.returncode = 0
        child.wait.return_value = 0
        with (
            patch.object(cli.subprocess, "Popen", return_value=child) as popen,
            patch.object(
                cli,
                "_bounded_child_output",
                return_value=('{"completed_slots":3,"sealed":false}\n', ""),
            ),
        ):
            result = cli._run(args)
        command = popen.call_args.args[0]
        self.assertEqual(command[0], cli.sys.executable)
        self.assertEqual(command[2], "_workspace-child")
        self.assertIn("--max-slots", command)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        child.wait.assert_called_once_with(timeout=420.0)
        self.assertEqual(result["completed_slots"], 3)

    def test_workspace_parent_terms_at_540_kills_at_590_and_bounds_reap(self):
        args = Namespace(
            command="workspace-resume",
            workspace_root="workspace",
            compatible_mapping="compatible.json",
            revised_mapping="revised.json",
            max_slots=1,
        )
        child = MagicMock()
        child.pid = 12345
        child.wait.side_effect = (
            cli.subprocess.TimeoutExpired("child", 420.0),
            cli.subprocess.TimeoutExpired("child", 120.0),
            cli.subprocess.TimeoutExpired("child", 50.0),
            cli.subprocess.TimeoutExpired("child", 4.0),
        )
        with (
            patch.object(cli.subprocess, "Popen", return_value=child) as popen,
            patch.object(cli.os, "killpg") as killpg,
            self.assertRaisesRegex(cli.FinalizeHardTimeout, "590 秒 KILL"),
        ):
            cli._run(args)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(
            child.wait.call_args_list,
            [
                unittest.mock.call(timeout=420.0),
                unittest.mock.call(timeout=120.0),
                unittest.mock.call(timeout=50.0),
                unittest.mock.call(timeout=4.0),
            ],
        )
        self.assertEqual(
            killpg.call_args_list,
            [
                unittest.mock.call(12345, cli.signal.SIGTERM),
                unittest.mock.call(12345, cli.signal.SIGKILL),
            ],
        )

    def test_workspace_alias_resume_uses_same_parent_supervisor(self):
        args = Namespace(command="resume")
        with patch.object(
            cli, "_supervise_workspace_child", return_value={"verified": True}
        ) as supervise:
            result = cli._run(args)
        supervise.assert_called_once_with(args)
        self.assertTrue(result["verified"])

    def test_workspace_assemble_uses_real_bounded_child_process(self):
        args = Namespace(
            command="workspace-assemble",
            workspace_root="workspace",
            output_root="package",
            resource_receipt="resource.json",
            **_workspace_product_args(),
        )
        child = MagicMock()
        child.pid = 12345
        child.returncode = 0
        child.wait.return_value = 0
        with (
            patch.object(cli.subprocess, "Popen", return_value=child) as popen,
            patch.object(
                cli,
                "_bounded_child_output",
                return_value=('{"verified":true}\n', ""),
            ),
        ):
            result = cli._run(args)
        command = popen.call_args.args[0]
        self.assertEqual(command[2], "_workspace-assemble-child")
        self.assertIn("--resource-receipt", command)
        for value in _workspace_product_args().values():
            self.assertIn(value, command)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        child.wait.assert_called_once_with(timeout=420.0)
        self.assertTrue(result["verified"])

    def test_workspace_reproduce_uses_real_bounded_child_process(self):
        args = Namespace(
            command="workspace-reproduce",
            workspace_root="workspace",
            reference_output_root="reference",
            reproduction_output_root="reproduction",
            acceptance_receipt="accepted.json",
            **_workspace_product_args(),
        )
        child = MagicMock()
        child.pid = 12345
        child.returncode = 0
        child.wait.return_value = 0
        with (
            patch.object(cli.subprocess, "Popen", return_value=child) as popen,
            patch.object(
                cli,
                "_bounded_child_output",
                return_value=('{"acceptance_state":"accepted"}\n', ""),
            ),
        ):
            result = cli._run(args)
        command = popen.call_args.args[0]
        self.assertEqual(command[2], "_workspace-reproduce-child")
        self.assertIn("reference", command)
        self.assertIn("reproduction", command)
        for value in _workspace_product_args().values():
            self.assertIn(value, command)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        child.wait.assert_called_once_with(timeout=420.0)
        self.assertEqual(result["acceptance_state"], "accepted")

    def test_workspace_assembly_parent_uses_same_590_kill_and_bounded_reap(self):
        args = Namespace(
            command="workspace-assemble",
            workspace_root="workspace",
            output_root="package",
            resource_receipt=None,
            **_workspace_product_args(),
        )
        child = MagicMock()
        child.pid = 12345
        child.wait.side_effect = (
            cli.subprocess.TimeoutExpired("child", 420.0),
            cli.subprocess.TimeoutExpired("child", 120.0),
            cli.subprocess.TimeoutExpired("child", 50.0),
            cli.subprocess.TimeoutExpired("child", 4.0),
        )
        with (
            patch.object(cli.subprocess, "Popen", return_value=child) as popen,
            patch.object(cli.os, "killpg") as killpg,
            self.assertRaisesRegex(cli.FinalizeHardTimeout, "590 秒 KILL"),
        ):
            cli._run(args)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(
            child.wait.call_args_list,
            [
                unittest.mock.call(timeout=420.0),
                unittest.mock.call(timeout=120.0),
                unittest.mock.call(timeout=50.0),
                unittest.mock.call(timeout=4.0),
            ],
        )
        self.assertEqual(
            killpg.call_args_list,
            [
                unittest.mock.call(12345, cli.signal.SIGTERM),
                unittest.mock.call(12345, cli.signal.SIGKILL),
            ],
        )

    def test_hidden_assembly_child_uses_420_second_progress_guard(self):
        args = Namespace(
            command="_workspace-assemble-child",
            workspace_root="workspace",
            output_root="package",
            resource_receipt=None,
            **_workspace_product_args(),
        )
        product_values = {
            "profile": {},
            "source_fact_snapshot": {},
            "incident_policy": {},
            "compatible_mapping_snapshot": {},
            "revised_mapping_snapshot": {},
            "code_identity": {},
            "input_selection": {},
            "claim_inventory": {},
            "bindings": {},
        }
        with patch.object(
            cli,
            "assemble_finalized_package_from_workspace",
            return_value={"verified": True},
        ) as assemble, patch.object(
            cli, "_workspace_product_values", return_value=product_values
        ):
            result = cli._run(args)
        hook = assemble.call_args.kwargs["publication_hook"]
        self.assertTrue(callable(hook))
        for key, value in product_values.items():
            self.assertIs(assemble.call_args.kwargs[key], value)
        self.assertTrue(result["verified"])

        guard = cli._ChildPlannedStopGuard(
            monotonic=iter((0.0, 420.0)).__next__
        )
        with self.assertRaisesRegex(cli.FinalizeCliError, "420 秒"):
            guard.hook("before_assembly_copy", Path("segment"))

    def test_hidden_reproduction_child_passes_same_420_guard_to_both_assemblies(self):
        args = Namespace(
            command="_workspace-reproduce-child",
            workspace_root="workspace",
            reference_output_root="reference",
            reproduction_output_root="reproduction",
            acceptance_receipt="accepted.json",
            **_workspace_product_args(),
        )
        product_values = {
            "profile": {},
            "source_fact_snapshot": {},
            "incident_policy": {},
            "compatible_mapping_snapshot": {},
            "revised_mapping_snapshot": {},
            "code_identity": {},
            "input_selection": {},
            "claim_inventory": {},
            "bindings": {},
        }
        with patch.object(
            cli,
            "assemble_workspace_reproduction",
            return_value={"acceptance_state": "accepted"},
        ) as reproduce, patch.object(
            cli, "_workspace_product_values", return_value=product_values
        ):
            result = cli._run(args)
        self.assertTrue(
            callable(reproduce.call_args.kwargs["publication_hook"])
        )
        for key, value in product_values.items():
            self.assertIs(reproduce.call_args.kwargs[key], value)
        self.assertEqual(result["acceptance_state"], "accepted")

    def test_workspace_publication_reconcile_and_package_verify_dispatch(self):
        reconcile_args = Namespace(
            command="workspace-reconcile-publication",
            workspace_root="workspace",
            **_workspace_product_args(),
        )
        verify_args = Namespace(
            command="workspace-verify-package",
            package_root="package",
            resource_receipt="resource.json",
        )
        with (
            patch.object(
                cli,
                "reconcile_workspace_publication",
                return_value={"state": "clean"},
            ) as reconcile,
            patch.object(
                cli,
                "verify_workspace_assembled_package",
                return_value={"verified": True},
            ) as verify,
            patch.object(
                cli,
                "_workspace_product_values",
                return_value={
                    "profile": {},
                    "source_fact_snapshot": {},
                    "incident_policy": {},
                    "compatible_mapping_snapshot": {},
                    "revised_mapping_snapshot": {},
                    "code_identity": {},
                    "input_selection": {},
                    "claim_inventory": {},
                    "bindings": {},
                },
            ),
        ):
            reconciled = cli._run(reconcile_args)
            verified = cli._run(verify_args)
        self.assertEqual(reconcile.call_args.args, ("workspace",))
        self.assertEqual(
            set(reconcile.call_args.kwargs),
            {
                "profile",
                "source_fact_snapshot",
                "incident_policy",
                "compatible_mapping_snapshot",
                "revised_mapping_snapshot",
                "code_identity",
                "input_selection",
                "claim_inventory",
                "bindings",
            },
        )
        verify.assert_called_once_with(
            "package", resource_receipt_path="resource.json"
        )
        self.assertEqual(reconciled["state"], "clean")
        self.assertTrue(verified["verified"])


if __name__ == "__main__":
    unittest.main()
