from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (
    CHECKPOINT_FINGERPRINT_SCHEMA,
    ResearchArtifactError,
    build_checkpoint,
    verify_checkpoint,
    write_canonical_json,
    write_canonical_jsonl_gzip,
    canonical_json,
)


SHA = {
    name: hashlib.sha256(name.encode("ascii")).hexdigest()
    for name in ("profile", "selection", "code", "mapping", "state", "shard")
}


def checkpoint():
    return build_checkpoint(
        run_id="research_run_v1_" + "1" * 24,
        phase="analysis_updates",
        profile_sha256=SHA["profile"],
        input_selection_sha256=SHA["selection"],
        code_sha256=SHA["code"],
        mapping_sha256=SHA["mapping"],
        artifact_id="art_v1_" + "2" * 32,
        next_record_ordinal=42,
        state_ref={"path": "state/part-0001.jsonl.gz", "sha256": SHA["state"]},
        published_shards=(
            {
                "kind": "route_events",
                "path": "route-events/part-0001.jsonl.gz",
                "sha256": SHA["shard"],
                "record_count": 41,
            },
        ),
    )


def resign(payload):
    semantic = deepcopy(payload)
    semantic.pop("checkpoint_fingerprint_sha256", None)
    semantic["checkpoint_fingerprint_sha256"] = hashlib.sha256(
        canonical_json(
            {"schema": CHECKPOINT_FINGERPRINT_SCHEMA, "checkpoint": semantic}
        ).encode("utf-8")
    ).hexdigest()
    return semantic


class Rrc25ResearchFileArtifactsTest(unittest.TestCase):
    def test_jsonl_gzip_is_byte_deterministic_across_empty_directories(self):
        records = (
            {"z": 2, "a": "伊朗"},
            {"value": 0, "value_state": "observed_zero"},
        )
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            left = write_canonical_jsonl_gzip(
                Path(first) / "part.jsonl.gz", records, kind="samples"
            )
            right = write_canonical_jsonl_gzip(
                Path(second) / "part.jsonl.gz", records, kind="samples"
            )

            self.assertEqual(left.sha256, right.sha256)
            self.assertEqual(left.size_bytes, right.size_bytes)
            self.assertEqual(left.path.read_bytes(), right.path.read_bytes())
            self.assertEqual(left.record_count, 2)
            with gzip.open(left.path, "rt", encoding="utf-8") as stream:
                decoded = [json.loads(line) for line in stream]
            self.assertEqual(decoded, list(records))

    def test_json_and_jsonl_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "manifest.json"
            jsonl_path = Path(directory) / "part.jsonl.gz"
            write_canonical_json(json_path, {"ok": True}, kind="manifest")
            write_canonical_jsonl_gzip(jsonl_path, (), kind="samples")

            with self.assertRaises(FileExistsError):
                write_canonical_json(json_path, {"ok": False}, kind="manifest")
            with self.assertRaises(FileExistsError):
                write_canonical_jsonl_gzip(jsonl_path, (), kind="samples")

    def test_failed_record_serialization_does_not_publish_partial_shard(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "failed.jsonl.gz"
            with self.assertRaises(ResearchArtifactError):
                write_canonical_jsonl_gzip(
                    target,
                    ({"ok": 1}, {"bad": float("nan")}),
                    kind="samples",
                )
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_checkpoint_is_deterministic_and_bound_to_all_semantic_inputs(self):
        first = checkpoint()
        second = checkpoint()
        self.assertEqual(first, second)
        verified = verify_checkpoint(
            first,
            expected_bindings={
                "profile_sha256": SHA["profile"],
                "input_selection_sha256": SHA["selection"],
                "code_sha256": SHA["code"],
                "mapping_sha256": SHA["mapping"],
            },
        )
        self.assertEqual(verified, first)
        self.assertEqual(
            first["input_position"]["boundary"], "complete_physical_record"
        )

        wrong = dict(SHA)
        wrong["code"] = "0" * 64
        with self.assertRaisesRegex(ResearchArtifactError, "code_sha256"):
            verify_checkpoint(
                first,
                expected_bindings={
                    "profile_sha256": wrong["profile"],
                    "input_selection_sha256": wrong["selection"],
                    "code_sha256": wrong["code"],
                    "mapping_sha256": wrong["mapping"],
                },
            )

    def test_checkpoint_tamper_and_unsafe_paths_fail_closed(self):
        tampered = deepcopy(checkpoint())
        tampered["input_position"]["next_record_ordinal"] = 43
        with self.assertRaisesRegex(ResearchArtifactError, "指纹"):
            verify_checkpoint(
                tampered,
                expected_bindings={
                    "profile_sha256": SHA["profile"],
                    "input_selection_sha256": SHA["selection"],
                    "code_sha256": SHA["code"],
                    "mapping_sha256": SHA["mapping"],
                },
            )

        with self.assertRaisesRegex(ResearchArtifactError, "安全相对路径"):
            build_checkpoint(
                run_id="research_run_v1_" + "1" * 24,
                phase="analysis_updates",
                profile_sha256=SHA["profile"],
                input_selection_sha256=SHA["selection"],
                code_sha256=SHA["code"],
                mapping_sha256=SHA["mapping"],
                artifact_id="art_v1_" + "2" * 32,
                next_record_ordinal=0,
                state_ref={"path": "../state.json", "sha256": SHA["state"]},
                published_shards=(),
            )

    def test_resigned_malformed_checkpoint_still_fails_closed(self):
        expected_bindings = {
            "profile_sha256": SHA["profile"],
            "input_selection_sha256": SHA["selection"],
            "code_sha256": SHA["code"],
            "mapping_sha256": SHA["mapping"],
        }

        invalid_run = deepcopy(checkpoint())
        invalid_run["run_id"] = "research_run_v1_not-hex"
        with self.assertRaisesRegex(ResearchArtifactError, "run_id"):
            verify_checkpoint(resign(invalid_run), expected_bindings=expected_bindings)

        invalid_artifact = deepcopy(checkpoint())
        invalid_artifact["input_position"]["artifact_id"] = "art_v1_bad"
        with self.assertRaisesRegex(ResearchArtifactError, "artifact_id"):
            verify_checkpoint(
                resign(invalid_artifact), expected_bindings=expected_bindings
            )

        extra_field = deepcopy(checkpoint())
        extra_field["unexpected"] = True
        with self.assertRaisesRegex(ResearchArtifactError, "顶层字段"):
            verify_checkpoint(resign(extra_field), expected_bindings=expected_bindings)


if __name__ == "__main__":
    unittest.main()
