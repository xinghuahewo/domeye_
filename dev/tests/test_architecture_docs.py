from __future__ import annotations

import unittest
from pathlib import Path

from dev import verify_architecture_docs as verifier


ROOT = Path(__file__).resolve().parents[2]


class ArchitectureDocsTest(unittest.TestCase):
    def test_repository_architecture_docs_pass(self) -> None:
        errors, summary = verifier.verify_repository(ROOT)
        self.assertEqual(errors, [])
        self.assertEqual(summary["profile"], "feb-mar-2026")
        self.assertGreaterEqual(summary["frontend_routes"], 9)
        self.assertGreaterEqual(summary["api_routes"], 30)
        self.assertGreaterEqual(summary["indexed_docs"], 40)

    def test_frontend_readme_routes_match_vue_router(self) -> None:
        self.assertEqual(
            verifier.parse_frontend_documented_routes(ROOT),
            verifier.parse_frontend_source_routes(ROOT),
        )

    def test_openapi_registered_and_documented_routes_match(self) -> None:
        registered = verifier.parse_backend_registered_routes(ROOT)
        self.assertEqual(verifier.parse_openapi_routes(ROOT), registered)
        self.assertEqual(verifier.parse_documented_api_routes(ROOT), registered)
        self.assertIn(
            ("POST", "/api/v2/country-outage/reports"),
            registered,
        )
        self.assertIn(
            (
                "GET",
                "/api/v2/country-outage/reports/{report_id}/events",
            ),
            registered,
        )

    def test_nginx_root_matches_frontend_install_target(self) -> None:
        self.assertEqual(
            verifier.nginx_root(ROOT),
            verifier.frontend_install_target(ROOT),
        )
        self.assertEqual(
            verifier.nginx_root(ROOT),
            "/home/bgpdata/Domeye-Core-runtime/web/dist",
        )

    def test_document_index_covers_every_markdown_file_once(self) -> None:
        errors, count = verifier.doc_index_errors(ROOT)
        expected = len(
            [
                path
                for path in (ROOT / "docs").glob("*.md")
                if path.name != "README.md"
            ]
        )
        self.assertEqual(errors, [])
        self.assertEqual(count, expected)

    def test_set_comparison_reports_missing_and_extra_values(self) -> None:
        errors = verifier.compare_sets(
            "示例",
            {"a", "b"},
            {"b", "c"},
        )
        self.assertEqual(len(errors), 2)
        self.assertIn("缺少", errors[0])
        self.assertIn("多出", errors[1])

    def test_route_normalization_handles_flask_converters(self) -> None:
        self.assertEqual(
            verifier.normalize_route_path(
                "/events/<event_type>/<int:event_id>/"
            ),
            "/events/{event_type}/{event_id}",
        )

    def test_marker_block_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            verifier.marker_block(
                "只有开始标记",
                "开始",
                "结束",
                "测试",
            )

    def test_current_docs_contain_no_stale_capability_claims(self) -> None:
        self.assertEqual(verifier.stale_claim_errors(ROOT), [])

    def test_current_doc_links_are_local_and_resolvable(self) -> None:
        errors, checked = verifier.local_link_errors(ROOT)
        self.assertEqual(errors, [])
        self.assertGreater(checked, 20)


if __name__ == "__main__":
    unittest.main()
