from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dev.tools import review_country_outage_p2_s0b_release_impact as reviewer


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = REPO_ROOT / reviewer.DEFAULT_POLICY
OUTPUT = REPO_ROOT / "evaluation/country-outage/p2-s0b-prod34-release/certification-impact-review.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fixture_root(destination: Path) -> Path:
    policy = load(POLICY)
    certification = load(REPO_ROOT / reviewer.BASE_CERTIFICATION)
    paths = {
        reviewer.DEFAULT_POLICY,
        reviewer.BASE_CERTIFICATION,
        reviewer.P2_ACCEPTANCE,
        reviewer.P2_PRODUCT_REVIEW,
        reviewer.REVIEWER_SOURCE,
    }
    paths.update(
        Path(item["path"])
        for item in certification["source_identity"]["files"]
    )
    paths.update(
        Path(item["path"])
        for item in policy["allowed_source_changes"]
    )
    for relative_path in paths:
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative_path, target)
    return destination


class P2S0BReleaseImpactReviewTest(unittest.TestCase):
    maxDiff = None

    def test_current_release_impact_is_pass(self) -> None:
        result = reviewer.review(REPO_ROOT, POLICY, OUTPUT)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["blocking_count"], 0)
        self.assertEqual(
            result["source_mismatch_paths"],
            ["agent-sidecar/src/chat/runtime-v2-semantic.ts"],
        )
        self.assertEqual(result["model_provider_calls"], 0)

    def test_second_certified_source_change_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = fixture_root(Path(temporary))
            target = root / "agent-sidecar/src/chat/pi-semantic-model.ts"
            target.write_text(target.read_text(encoding="utf-8") + "\n// tampered\n", encoding="utf-8")
            result = reviewer.review(root, root / reviewer.DEFAULT_POLICY, root / "review.json")
            self.assertEqual(result["status"], "BLOCK")
            self.assertIn("agent-sidecar/src/chat/pi-semantic-model.ts", result["source_mismatch_paths"])

    def test_allowed_runtime_source_tamper_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = fixture_root(Path(temporary))
            target = root / "agent-sidecar/src/chat/runtime-v2-semantic.ts"
            target.write_text(target.read_text(encoding="utf-8") + "\n// tampered\n", encoding="utf-8")
            result = reviewer.review(root, root / reviewer.DEFAULT_POLICY, root / "review.json")
            self.assertEqual(result["status"], "BLOCK")
            blocked = {item["check_id"] for item in result["blocking_items"]}
            self.assertIn("SOURCE-IMPACT-DIGESTS", blocked)

    def test_policy_cannot_claim_old_certification_as_new_runtime_certification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = fixture_root(Path(temporary))
            policy_path = root / reviewer.DEFAULT_POLICY
            policy = load(policy_path)
            policy["decision"]["base_model_certification_reused_as_new_runtime_certification"] = True
            policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result = reviewer.review(root, policy_path, root / "review.json")
            self.assertEqual(result["status"], "BLOCK")
            blocked = {item["check_id"] for item in result["blocking_items"]}
            self.assertIn("CERTIFICATION-RESPONSIBILITY-SPLIT", blocked)

    def test_p2_product_review_tamper_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = fixture_root(Path(temporary))
            review_path = root / reviewer.P2_PRODUCT_REVIEW
            review = load(review_path)
            review["status"] = "BLOCK"
            review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result = reviewer.review(root, root / reviewer.DEFAULT_POLICY, root / "review.json")
            self.assertEqual(result["status"], "BLOCK")
            blocked = {item["check_id"] for item in result["blocking_items"]}
            self.assertIn("P2-RUNTIME-EVIDENCE", blocked)


if __name__ == "__main__":
    unittest.main()
