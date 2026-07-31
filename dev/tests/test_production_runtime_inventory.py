from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "inventory" / "collect-production-runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "collect_production_runtime",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ProductionRuntimeInventoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_envelope_sha_binds_canonical_inventory_only(self) -> None:
        inventory = {
            "中文": "值",
            "nested": {"b": 2, "a": 1},
        }
        envelope = self.module.inventory_envelope(inventory)
        expected = hashlib.sha256(
            json.dumps(
                inventory,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(envelope["inventory_sha256"], expected)
        self.assertEqual(envelope["inventory"], inventory)

    def test_stable_hash_rejects_symlink_and_sensitive_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regular = root / "runtime-metadata.json"
            regular.write_text("identity\n", encoding="utf-8")
            regular.chmod(0o600)
            identity = self.module.stable_file_sha256(regular)
            self.assertEqual(identity["sha256_status"], "verified")

            symlink = root / "runtime-link.json"
            symlink.symlink_to(regular)
            linked = self.module.stable_file_sha256(symlink)
            self.assertEqual(linked["sha256_status"], "not_regular_file")

            sensitive = root / "country-outage-pi-auth.json"
            sensitive.write_text("must-not-be-read\n", encoding="utf-8")
            sensitive.chmod(0)
            excluded = self.module.stable_file_sha256(sensitive)
            self.assertEqual(
                excluded["sha256_status"],
                "excluded_sensitive_path",
            )
            sensitive.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_nginx_parser_extracts_actual_root_and_proxy_without_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "domeye-core.conf"
            config.write_text(
                """
                server {
                    listen 28471;
                    root /srv/domeye/frontend/dist;
                    location /api/ {
                        proxy_pass http://127.0.0.1:28473;
                    }
                }
                """,
                encoding="utf-8",
            )
            config.chmod(0o600)
            parsed = self.module.parse_nginx_config(config)
            self.assertEqual(parsed["listen_ports"], [28471])
            self.assertEqual(parsed["roots"], ["/srv/domeye/frontend/dist"])
            self.assertEqual(
                parsed["proxy_pass"],
                ["http://127.0.0.1:28473"],
            )
            self.assertNotIn("body", parsed)

    def test_git_url_projection_removes_userinfo_query_and_fragment(self) -> None:
        self.assertEqual(
            self.module.sanitized_git_url(
                "https://user:token@example.test/repository?access_token=x#fragment"
            ),
            "https://<redacted>@example.test/repository",
        )
        self.assertEqual(
            self.module.sanitized_git_url(
                "root@example.test:/srv/domeye.git"
            ),
            "<redacted>@example.test:/srv/domeye.git",
        )

    def test_script_has_no_mutating_or_network_entrypoints(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        forbidden = (
            '["git", "fetch"',
            '["curl"',
            "requests.",
            "urllib.",
            "/environ",
            "write_text(",
            "write_bytes(",
            "mkdir(",
            "mkstemp",
            "NamedTemporaryFile",
            "unlink(",
            "rename(",
            "replace(",
            "chmod(",
            "chown(",
            "shutil.rmtree",
            "shell=True",
        )
        for fragment in forbidden:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, source)
        self.assertNotIn('Path("/home/bgpdata/.config', source)

    def test_frontend_tree_hash_matches_documented_stream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "assets").mkdir()
            (root / "index.html").write_text(
                "<script src='/assets/app.js'></script>\n",
                encoding="utf-8",
            )
            (root / "assets" / "app.js").write_text(
                "console.log('ok')\n",
                encoding="utf-8",
            )
            observed = self.module.frontend_tree_identity(root)
            digest = hashlib.sha256()
            for relative in ("assets/app.js", "index.html"):
                content = (root / relative).read_bytes()
                digest.update(relative.encode("utf-8"))
                digest.update(b"\0")
                digest.update(hashlib.sha256(content).hexdigest().encode("ascii"))
                digest.update(b"\0")
            self.assertEqual(observed["status"], "verified")
            self.assertEqual(observed["tree_sha256"], digest.hexdigest())
            self.assertEqual(observed["file_count"], 2)

    def test_fixed_screen_inventory_only_returns_approved_names(self) -> None:
        original = self.module.run_command
        try:
            self.module.run_command = lambda *args, **kwargs: self.module.CommandResult(
                True,
                0,
                "\n".join(
                    (
                        "123.domeye_country_outage_agent (Detached)",
                        "456.domeye_core_p0_canary (Detached)",
                        "789.unrelated_session (Detached)",
                    )
                ),
            )
            observed = self.module.fixed_screen_inventory()
        finally:
            self.module.run_command = original
        self.assertEqual(observed["status"], "observed")
        self.assertEqual(
            observed["sessions"],
            {
                "domeye_country_outage_agent": [
                    "123.domeye_country_outage_agent"
                ],
                "domeye_core_p0_canary": [
                    "456.domeye_core_p0_canary"
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
