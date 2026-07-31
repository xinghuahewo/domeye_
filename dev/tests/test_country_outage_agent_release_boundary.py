from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
OVERLAY_BUILDER = (
    ROOT / "deploy" / "country-outage-agent" / "build-approved-overlay.sh"
).read_text(encoding="utf-8")
NGINX_CONFIG = (ROOT / "deploy" / "nginx" / "domeye-core.conf").read_text(
    encoding="utf-8"
)


class CountryOutageAgentReleaseBoundaryTest(unittest.TestCase):
    def test_agent_overlay_never_contains_a_prebuilt_frontend(self):
        self.assertNotIn("copy_approved_tree frontend/dist", OVERLAY_BUILDER)
        self.assertIn("frontend/dist|frontend/tmp", OVERLAY_BUILDER)
        self.assertIn("frontend_dist_included: false", OVERLAY_BUILDER)
        self.assertIn("frontend_activation_authorized: false", OVERLAY_BUILDER)
        self.assertIn("frontend_build_required: true", OVERLAY_BUILDER)

    def test_overlay_requires_a_unified_frontend_build_and_events_gate(self):
        self.assertIn(
            "build_unified_frontend_from_bound_complete_source_and_data_profile",
            OVERLAY_BUILDER,
        )
        self.assertIn(
            "verify_events_fixed_window_product_path",
            OVERLAY_BUILDER,
        )

    def test_spa_html_is_not_cached_but_hashed_assets_are_immutable(self):
        self.assertIn("root /home/bgpdata/Domeye-Core-runtime/web/dist;", NGINX_CONFIG)
        self.assertIn("location = /index.html", NGINX_CONFIG)
        self.assertIn(
            'Cache-Control "no-cache, no-store, must-revalidate" always',
            NGINX_CONFIG,
        )
        self.assertIn(
            'Cache-Control "public, max-age=604800, immutable"',
            NGINX_CONFIG,
        )


if __name__ == "__main__":
    unittest.main()
