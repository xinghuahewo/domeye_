import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (
    ResearchArtifactError,
    canonical_json,
)
from backend.data_pipeline.research.rrc25_country_outage.replay_persistence import (
    STATE_FINGERPRINT_SCHEMA,
    publish_replay_checkpoint,
    restore_replay_checkpoint,
    route_replay_state_from_payload,
    route_replay_state_to_payload,
)
from backend.data_pipeline.research.rrc25_country_outage.state_replay import (
    InputGap,
    apply_catch_up_updates,
    build_research_route_event,
    seed_state_from_rib,
)
from backend.data_pipeline.route_event import (
    AsPathSegment,
    ParsedRouteElement,
    artifact_id_v1,
)


HASHES = {
    name: hashlib.sha256(name.encode("ascii")).hexdigest()
    for name in ("profile", "selection", "code", "mapping")
}
RUN_ID = "research_run_v1_" + "1" * 24


def _event(label, *, action, event_time, record_ordinal=0, origin=64496):
    file_sha256 = hashlib.sha256(label.encode("ascii")).hexdigest()
    return build_research_route_event(
        artifact_id=artifact_id_v1(file_sha256),
        file_sha256=file_sha256,
        collector_id="rrc25",
        artifact_slot_utc=event_time[:16] + ":00Z",
        record_ordinal=record_ordinal,
        element_ordinal=0,
        element=ParsedRouteElement(
            event_time_utc=event_time,
            peer_ip="192.0.2.1",
            peer_asn=64500,
            action=action,
            prefix="203.0.113.0/24",
            afi_safi="ipv4_unicast",
            as_path=(AsPathSegment("as_sequence", (64500, origin)),)
            if action != "withdraw"
            else None,
            quality_flags=(),
        ),
    )


def _seed():
    return seed_state_from_rib(
        (_event("seed", action="rib_snapshot", event_time="2026-02-27T15:55:00Z"),)
    )


def _bindings():
    return {
        "profile_sha256": HASHES["profile"],
        "input_selection_sha256": HASHES["selection"],
        "code_sha256": HASHES["code"],
        "mapping_sha256": HASHES["mapping"],
    }


def _prepare_root(path):
    (path / "state").mkdir()
    (path / "checkpoints").mkdir()


def _publish(root, state, *, suffix="0001"):
    update_sha = hashlib.sha256(b"update-artifact").hexdigest()
    return publish_replay_checkpoint(
        root,
        state_relative_path=f"state/state-{suffix}.jsonl.gz",
        checkpoint_relative_path=f"checkpoints/checkpoint-{suffix}.json",
        state=state,
        run_id=RUN_ID,
        phase="window_updates",
        profile_sha256=HASHES["profile"],
        input_selection_sha256=HASHES["selection"],
        code_sha256=HASHES["code"],
        mapping_sha256=HASHES["mapping"],
        artifact_id=artifact_id_v1(update_sha),
        next_record_ordinal=2,
    )


def _resign_state(payload):
    semantic = copy.deepcopy(payload)
    semantic.pop("state_fingerprint_sha256", None)
    semantic["state_fingerprint_sha256"] = hashlib.sha256(
        canonical_json(
            {"schema": STATE_FINGERPRINT_SCHEMA, "state": semantic}
        ).encode("utf-8")
    ).hexdigest()
    return semantic


class Rrc25ReplayPersistenceTests(unittest.TestCase):
    def test_state_payload_round_trip_preserves_unknown_without_zero_fill(self):
        state = seed_state_from_rib(
            tuple(_seed().entries and (
                _event(
                    "gapped-seed",
                    action="rib_snapshot",
                    event_time="2026-02-27T15:55:00Z",
                ),
            )),
            input_gaps=(
                InputGap(
                    "2026-02-27T15:50:00Z",
                    "2026-02-27T15:55:00Z",
                    "seed_input_missing",
                ),
            ),
        )
        payload = route_replay_state_to_payload(state)
        restored = route_replay_state_from_payload(payload)

        self.assertEqual(restored, state)
        self.assertIsNone(restored.route_count)
        self.assertEqual(restored.missing_reasons, ("seed_input_missing",))

    def test_resigned_semantically_contradictory_state_fails_closed(self):
        payload = route_replay_state_to_payload(_seed())
        payload["continuity_state"] = "unknown_after_gap"
        with self.assertRaisesRegex(ResearchArtifactError, "连续性"):
            route_replay_state_from_payload(_resign_state(payload))

        payload = route_replay_state_to_payload(_seed())
        payload["entries"][0]["last_raw_ref"]["route_event_id"] = "rte_v1_" + "0" * 32
        with self.assertRaisesRegex(ResearchArtifactError, "原始坐标"):
            route_replay_state_from_payload(_resign_state(payload))

    def test_publish_and_restore_are_byte_deterministic_in_two_empty_roots(self):
        state = apply_catch_up_updates(
            _seed(),
            (_event("update-one", action="announce", event_time="2026-02-27T16:00:01Z"),),
        )
        with tempfile.TemporaryDirectory() as left_dir, tempfile.TemporaryDirectory() as right_dir:
            left_root = Path(left_dir)
            right_root = Path(right_dir)
            _prepare_root(left_root)
            _prepare_root(right_root)
            left = _publish(left_root, state)
            right = _publish(right_root, state)

            self.assertEqual(left.state_artifact.sha256, right.state_artifact.sha256)
            self.assertEqual(
                left.checkpoint_artifact.sha256, right.checkpoint_artifact.sha256
            )
            restored = restore_replay_checkpoint(
                left_root, "checkpoints/checkpoint-0001.json", expected_bindings=_bindings()
            )
            self.assertEqual(restored.state, state)
            self.assertEqual(restored.state_sha256, left.state_artifact.sha256)

    def test_interrupted_resume_matches_uninterrupted_replay(self):
        first = _event(
            "same-update-artifact",
            action="announce",
            event_time="2026-02-27T16:00:01Z",
            record_ordinal=0,
            origin=64497,
        )
        second = _event(
            "same-update-artifact",
            action="withdraw",
            event_time="2026-02-27T16:00:02Z",
            record_ordinal=1,
        )
        partial = apply_catch_up_updates(_seed(), (first,))
        uninterrupted = apply_catch_up_updates(_seed(), (second, first))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _prepare_root(root)
            _publish(root, partial)
            restored = restore_replay_checkpoint(
                root, "checkpoints/checkpoint-0001.json", expected_bindings=_bindings()
            )
            resumed = apply_catch_up_updates(restored.state, (second,))

        self.assertEqual(resumed, uninterrupted)
        self.assertEqual(resumed.route_count, 0)

    def test_existing_targets_binding_mismatch_and_state_tamper_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _prepare_root(root)
            _publish(root, _seed())
            with self.assertRaises(FileExistsError):
                _publish(root, _seed())

            wrong = _bindings()
            wrong["mapping_sha256"] = "0" * 64
            with self.assertRaisesRegex(ResearchArtifactError, "mapping_sha256"):
                restore_replay_checkpoint(
                    root, "checkpoints/checkpoint-0001.json", expected_bindings=wrong
                )

            state_path = root / "state/state-0001.jsonl.gz"
            with gzip.open(state_path, "rt", encoding="utf-8") as stream:
                payload = json.loads(stream.readline())
            payload["entries"][0]["peer_asn"] = 64499
            state_path.write_bytes(
                gzip.compress(
                    (canonical_json(_resign_state(payload)) + "\n").encode("utf-8"),
                    mtime=0,
                )
            )
            with self.assertRaisesRegex(ResearchArtifactError, "SHA256"):
                restore_replay_checkpoint(
                    root, "checkpoints/checkpoint-0001.json", expected_bindings=_bindings()
                )


if __name__ == "__main__":
    unittest.main()
