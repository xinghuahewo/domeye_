import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "governance" / "audit-server-credential-surface.py"
SPEC = importlib.util.spec_from_file_location("audit_server_credential_surface", SCRIPT)
S3 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(S3)


class ServerCredentialSurfaceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "Domeye-Core-runtime"
        self.data = self.root / "Domeye-Core-data"
        self.source = self.root / "Domeye-Core"
        self.governance = self.root / "Domeye-Core-governance"
        self.process_root = self.root / "proc"
        for directory in (self.runtime, self.data, self.source, self.governance, self.process_root):
            directory.mkdir(parents=True)
        self.releases = {
            "backend": self.create_release(self.runtime / "releases", "backend-active"),
            "legacy_agent_sidecar": self.create_release(self.runtime / "country-outage-agent" / "releases", "agent-active"),
            "interactive_agent_sidecar": self.create_release(
                self.runtime / "country-outage-interactive-agent" / "releases",
                "interactive-agent-active",
            ),
            "legacy_p1_chat_sidecar": self.create_release(
                self.runtime / "country-outage-p1-chat" / "releases",
                "legacy-p1-chat-active",
            ),
        }
        self.links = {
            "backend": self.link(self.runtime / "current", self.releases["backend"]),
            "legacy_agent_sidecar": self.link(self.runtime / "country-outage-agent" / "current", self.releases["legacy_agent_sidecar"]),
            "interactive_agent_sidecar": self.link(
                self.runtime / "country-outage-interactive-agent" / "current",
                self.releases["interactive_agent_sidecar"],
            ),
            "legacy_p1_chat_sidecar": self.link(
                self.runtime / "country-outage-p1-chat" / "current",
                self.releases["legacy_p1_chat_sidecar"],
            ),
        }
        self.config_paths = {
            "backend_database_env": self.data / "config" / "database.env",
            "country_outage_agent_env": self.runtime / "config" / "country-outage-agent.env",
            "country_outage_interactive_agent_env": self.runtime
            / "config"
            / "country-outage-interactive-agent.env",
            "country_outage_p1_chat_env": self.runtime
            / "config"
            / "country-outage-p1-chat.env",
            "country_outage_pi_auth": self.runtime / "config" / "country-outage-pi-auth.json",
        }
        for path in self.config_paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture-secret-must-not-appear\n", encoding="utf-8")
            path.chmod(0o600)
        self.add_process(101, self.releases["backend"], b"/usr/bin/python\x00run.py\x00", b"PATH=/bin\x00")
        self.add_process(102, self.releases["legacy_agent_sidecar"], b"/usr/bin/node\x00serve.js\x00", b"COUNTRY_OUTAGE_AGENT_SHARED_TOKEN=fixture-secret-must-not-appear\x00")
        self.add_process(103, self.root, b"", b"PATH=/bin\x00")
        legacy_p1_entry = str(
            self.releases["legacy_p1_chat_sidecar"].resolve()
            / "dist"
            / "legacy-p1-read-only-inventory.js"
        ).encode("utf-8")
        self.add_process(
            104,
            self.root,
            b"/usr/bin/node\x00" + legacy_p1_entry + b"\x00",
            b"PATH=/bin\x00",
        )
        self.policy = self.build_policy()
        self.stopped_ports: set[int] = set()
        self.original_run = S3.readonly_run
        S3.readonly_run = self.fake_run

    def tearDown(self):
        S3.readonly_run = self.original_run
        self.temporary.cleanup()

    def create_release(self, root, name):
        path = root / name
        path.mkdir(parents=True)
        return path

    def link(self, link, target):
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        return link

    def add_process(self, pid, cwd, command_line, environment):
        process = self.process_root / str(pid)
        process.mkdir()
        (process / "cwd").symlink_to(cwd)
        (process / "exe").symlink_to("/bin/sh")
        (process / "cmdline").write_bytes(command_line)
        (process / "environ").write_bytes(environment)
        (process / "comm").write_text("fixture-service\n", encoding="utf-8")

    def build_policy(self):
        uid = os.getuid()
        gid = os.getgid()
        return {
            "schemaVersion": S3.POLICY_SCHEMA,
            "expectedHost": socket.gethostname(),
            "processRoot": str(self.process_root),
            "managedRoots": [
                {"path": str(self.runtime)},
                {"path": str(self.data)},
                {"path": str(self.source)},
                {"path": str(self.governance)},
            ],
            "protectedRoots": [{"path": str(self.root / "old-domeye")}],
            "mutationPolicy": {
                "auditWritesServer": False,
                "deleteEnabled": False,
                "moveEnabled": False,
                "restartEnabled": False,
                "productionSwitchEnabled": False,
            },
            "credentialGovernance": {
                "schemaVersion": "domeye.server-s3-credential-governance/v1",
                "requiredConfigUid": uid,
                "requiredConfigGid": gid,
                "requiredConfigMode": "0600",
                "configFiles": [{"id": identifier, "path": str(path)} for identifier, path in self.config_paths.items()],
                "components": [
                    {
                        "name": "backend",
                        "activeLinkPath": str(self.links["backend"]),
                        "releaseRoot": str(self.runtime / "releases"),
                        "listenerPort": None,
                        "requiredIdentitySignals": ["cwd_or_executable"],
                        "configFileIds": ["backend_database_env"],
                    },
                    {
                        "name": "legacy_agent_sidecar",
                        "activeLinkPath": str(self.links["legacy_agent_sidecar"]),
                        "releaseRoot": str(self.runtime / "country-outage-agent" / "releases"),
                        "listenerPort": 28474,
                        "requiredIdentitySignals": ["cwd_or_executable", "listener_port"],
                        "configFileIds": ["country_outage_agent_env", "country_outage_pi_auth"],
                    },
                    {
                        "name": "interactive_agent_sidecar",
                        "activeLinkPath": str(self.links["interactive_agent_sidecar"]),
                        "releaseRoot": str(
                            self.runtime / "country-outage-interactive-agent" / "releases"
                        ),
                        "listenerPort": 28476,
                        "requiredIdentitySignals": ["listener_port", "active_release_argument"],
                        "configFileIds": [
                            "country_outage_interactive_agent_env",
                            "country_outage_pi_auth",
                        ],
                    },
                    {
                        "name": "legacy_p1_chat_sidecar",
                        "activeLinkPath": str(self.links["legacy_p1_chat_sidecar"]),
                        "releaseRoot": str(
                            self.runtime / "country-outage-p1-chat" / "releases"
                        ),
                        "listenerPort": 28475,
                        "requiredIdentitySignals": [
                            "listener_port",
                            "active_release_argument",
                        ],
                        "configFileIds": [
                            "country_outage_p1_chat_env",
                            "country_outage_pi_auth",
                        ],
                        "routingState": "retained_not_routed",
                        "governanceMode": "read_only",
                    },
                ],
            },
        }

    def fake_run(self, arguments):
        port = arguments[-1]
        numeric_port = int(port.rsplit(":", 1)[-1])
        pid = (
            102
            if port.endswith("28474")
            else 103
            if port.endswith("28476")
            else 104
            if port.endswith("28475")
            else None
        )
        if numeric_port in self.stopped_ports:
            pid = None
        stdout = f'LISTEN 0 1 127.0.0.1:* users:(("node",pid={pid},fd=24))\n' if pid else ""
        return subprocess.CompletedProcess(arguments, 0, stdout, "")

    def test_retired_components_require_preserved_release_and_no_process_or_listener(self):
        for index, pid, port in ((1, 102, 28474), (3, 104, 28475)):
            self.policy["credentialGovernance"]["components"][index][
                "runtimeState"
            ] = "retired_stopped"
            cwd = self.process_root / str(pid) / "cwd"
            cwd.unlink()
            cwd.symlink_to(self.root)
            self.stopped_ports.add(port)

        report = S3.build_report(self.policy)
        for index in (1, 3):
            component = report["components"][index]
            self.assertEqual(component["runtimeState"], "retired_stopped")
            self.assertEqual(component["identityState"], "verified_stopped")
            self.assertEqual(
                component["commandLineCredentialState"],
                "not_applicable_retired_stopped",
            )
            self.assertEqual(component["processes"], [])

        self.assertFalse(
            any("legacy_agent_sidecar:" in item for item in report["gate"]["reasons"])
        )
        self.assertFalse(
            any("legacy_p1_chat_sidecar:" in item for item in report["gate"]["reasons"])
        )

    def test_retired_component_fails_closed_when_listener_or_process_remains(self):
        component = self.policy["credentialGovernance"]["components"][1]
        component["runtimeState"] = "retired_stopped"

        report = S3.build_report(self.policy)

        self.assertIn(
            "legacy_agent_sidecar:retired_process_present",
            report["gate"]["reasons"],
        )
        self.assertIn(
            "legacy_agent_sidecar:retired_listener_present",
            report["gate"]["reasons"],
        )

    def test_reports_safe_surface_and_fails_closed_for_unbound_interactive_agent(self):
        report = S3.build_report(self.policy)
        encoded = json.dumps(report, ensure_ascii=False)

        self.assertEqual(report["schemaVersion"], S3.SCHEMA)
        self.assertFalse(report["serverWrites"])
        self.assertFalse(report["commandLineValuesEmitted"])
        self.assertFalse(report["environmentValuesEmitted"])
        self.assertFalse(report["configurationContentsRead"])
        self.assertNotIn("fixture-secret-must-not-appear", encoded)
        backend, agent, interactive_agent, legacy_p1_chat = report["components"]
        self.assertEqual(backend["identityState"], "verified")
        self.assertEqual(agent["identityState"], "verified")
        self.assertEqual(agent["commandLineCredentialState"], "verified_no_credential_like_arguments")
        self.assertTrue(agent["processes"][0]["credentialLikeEnvironmentKeyPresent"])
        self.assertEqual(interactive_agent["identityState"], "not_verified")
        self.assertEqual(interactive_agent["commandLineCredentialState"], "not_verified")
        self.assertEqual(legacy_p1_chat["identityState"], "verified")
        self.assertEqual(legacy_p1_chat["listener"]["port"], 28475)
        self.assertIn(
            "country_outage_p1_chat_env",
            legacy_p1_chat["referencedConfigFiles"],
        )
        legacy_policy = self.policy["credentialGovernance"]["components"][3]
        self.assertEqual(legacy_policy["routingState"], "retained_not_routed")
        self.assertEqual(legacy_policy["governanceMode"], "read_only")
        self.assertEqual(report["gate"]["decision"], "BLOCK_MUTATION")
        self.assertIn(
            "interactive_agent_sidecar:process_identity_not_verified",
            report["gate"]["reasons"],
        )

    def test_detects_credential_like_argument_without_emitting_value(self):
        (self.process_root / "101" / "cmdline").write_bytes(b"/usr/bin/python\x00--api-key=fixture-secret-must-not-appear\x00")

        report = S3.build_report(self.policy)
        backend = report["components"][0]
        encoded = json.dumps(report, ensure_ascii=False)

        self.assertEqual(backend["commandLineCredentialState"], "not_verified")
        self.assertIn("backend:credential_like_command_line_argument_detected", report["gate"]["reasons"])
        self.assertNotIn("fixture-secret-must-not-appear", encoded)

    def test_listener_tool_unavailable_fails_closed(self):
        def unavailable(arguments):
            raise FileNotFoundError("ss")

        S3.readonly_run = unavailable
        report = S3.build_report(self.policy)

        agent = report["components"][1]
        self.assertFalse(agent["listener"]["coverageComplete"])
        self.assertIn("legacy_agent_sidecar:listener_inspection_incomplete", report["gate"]["reasons"])

    def test_policy_rejects_mutation_capability(self):
        self.policy["mutationPolicy"]["restartEnabled"] = True

        with self.assertRaisesRegex(S3.CredentialSurfaceError, "restartEnabled=false"):
            S3.validate_policy(self.policy)


if __name__ == "__main__":
    unittest.main()
