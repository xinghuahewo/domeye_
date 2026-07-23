from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from backend.data_pipeline.route_event import artifact_id_v1
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    MAPPED,
    CountryMappingView,
    MappingAssignment,
)
from backend.data_pipeline.research.rrc25_country_outage.rib_adapter import (
    ObservedVpAccumulator,
    iter_rib_spool_artifact_records,
)
from backend.data_pipeline.research.rrc25_country_outage.rib_prefilter import (
    build_parallel_rib_prefilter,
    validate_rib_prefilter,
)
from dev.tests.test_rrc25_research_rib_adapter import (
    MRT_TIME,
    SLOT_TEXT,
    as_path,
    mrt_record,
    peer_index_payload,
    rib_payload,
)


def _mapping() -> CountryMappingView:
    return CountryMappingView(
        view="compatible",
        target_country="IR",
        assignments=(
            MappingAssignment(65001, ("IR",), MAPPED),
            MappingAssignment(65100, ("US",), MAPPED),
            MappingAssignment(65101, ("DE",), MAPPED),
        ),
        source_sha256="a" * 64,
        source_ref="synthetic-compatible",
    )


def _spool_bytes() -> bytes:
    peers = (
        ("192.0.2.10", 64510),
        ("192.0.2.20", 64520),
    )
    records = [mrt_record(peer_index_payload(*peers), subtype=1)]
    definitions = (
        ("198.51.100.0/24", (0, as_path((2, (64510, 65100))))),
        ("198.51.101.0/24", (1, as_path((2, (64520, 65001))))),
        ("198.51.102.0/24", (0, as_path())),
        ("198.51.103.0/24", (1, as_path((1, (65100, 65101))))),
    )
    for sequence, (prefix, entry) in enumerate(definitions, start=1):
        subtype, payload = rib_payload(prefix, entry, sequence=sequence)
        records.append(
            mrt_record(payload, subtype=subtype, timestamp=MRT_TIME)
        )
    return b"".join(records)


class RibPrefilterTests(unittest.TestCase):
    def test_parallel_prefilter_is_deterministic_and_retained_replay_equal(self):
        raw = _spool_bytes()
        spool_sha = hashlib.sha256(raw).hexdigest()
        seed_sha = hashlib.sha256(b"compressed-seed-artifact").hexdigest()
        artifact = {
            "artifact_id": artifact_id_v1(seed_sha),
            "file_sha256": seed_sha,
            "collector_id": "rrc25",
            "artifact_type": "rib",
            "artifact_time_utc": SLOT_TEXT,
            "relative_path": "rrc25/2026.02/bview.20260227.1600.gz",
            "compression": "gz",
            "size_bytes": 1024,
        }
        mapping = _mapping()
        predicate = lambda asn: mapping.target_membership(asn) is not False
        with tempfile.TemporaryDirectory() as directory:
            spool = Path(directory) / "seed.mrt"
            spool.write_bytes(raw)
            receipts = [
                build_parallel_rib_prefilter(
                    spool,
                    expected_spool_sha256=spool_sha,
                    expected_spool_size_bytes=len(raw),
                    seed_artifact_id=artifact["artifact_id"],
                    seed_file_sha256=artifact["file_sha256"],
                    artifact_slot_utc=SLOT_TEXT,
                    raw_retention_mapping=mapping,
                    workers=workers,
                    batch_records=64,
                )
                for workers in (1, 2)
            ]
            self.assertEqual(receipts[0], receipts[1])
            selected = validate_rib_prefilter(
                receipts[0],
                expected_spool_sha256=spool_sha,
                expected_spool_size_bytes=len(raw),
                seed_artifact_id=artifact["artifact_id"],
                seed_file_sha256=artifact["file_sha256"],
                artifact_slot_utc=SLOT_TEXT,
                raw_retention_mapping=mapping,
            )
            self.assertEqual(selected, frozenset({2, 3}))

            common = {
                "spool_path": spool,
                "expected_decompressed_sha256": spool_sha,
                "expected_decompressed_size_bytes": len(raw),
                "next_record_ordinal": 0,
                "next_record_offset": 0,
                "artifact": artifact,
                "origin_asn_predicate": predicate,
                "include_discarded_element_decisions": False,
            }
            legacy = tuple(iter_rib_spool_artifact_records(**common))
            accelerated = tuple(
                iter_rib_spool_artifact_records(
                    **common,
                    prefilter_materialize_rib_ordinals=selected,
                )
            )
            legacy_vps = ObservedVpAccumulator()
            sparse_vps = ObservedVpAccumulator()
            final_boundaries = []
            legacy_with_vps = tuple(
                iter_rib_spool_artifact_records(
                    **{
                        **common,
                        "vp_observer": legacy_vps.observe,
                    }
                )
            )
            sparse = tuple(
                iter_rib_spool_artifact_records(
                    **common,
                    vp_observer=sparse_vps.observe,
                    prefilter_materialize_rib_ordinals=selected,
                    sparse_physical_record_count=receipts[0][
                        "population"
                    ]["physical_record_count"],
                    checkpoint_observer=lambda boundary, _context: (
                        final_boundaries.append(boundary)
                    ),
                )
            )

        self.assertEqual(
            [row.route_events for row in accelerated],
            [row.route_events for row in legacy],
        )
        self.assertEqual(
            [
                (
                    row.source_element_count,
                    row.retained_element_count,
                    row.discarded_element_count,
                )
                for row in accelerated
            ],
            [
                (
                    row.source_element_count,
                    row.retained_element_count,
                    row.discarded_element_count,
                )
                for row in legacy
            ],
        )
        self.assertEqual(
            tuple(
                event
                for row in sparse
                for event in row.route_events
            ),
            tuple(
                event
                for row in legacy_with_vps
                for event in row.route_events
            ),
        )
        self.assertEqual(
            tuple(
                row.raw_record
                for row in sparse
                if row.route_events
            ),
            tuple(
                row.raw_record
                for row in legacy_with_vps
                if row.route_events
            ),
        )
        self.assertEqual(
            sparse_vps.observed_vp_ids,
            legacy_vps.observed_vp_ids,
        )
        self.assertEqual(final_boundaries[-1].record_ordinal, 4)
        self.assertEqual(
            final_boundaries[-1].record_offset
            + final_boundaries[-1].record_length,
            len(raw),
        )

    def test_sparse_replay_resumes_from_selected_boundary(self):
        raw = _spool_bytes()
        spool_sha = hashlib.sha256(raw).hexdigest()
        seed_sha = hashlib.sha256(b"compressed-seed-artifact").hexdigest()
        artifact = {
            "artifact_id": artifact_id_v1(seed_sha),
            "file_sha256": seed_sha,
            "collector_id": "rrc25",
            "artifact_type": "rib",
            "artifact_time_utc": SLOT_TEXT,
            "relative_path": "rrc25/2026.02/bview.20260227.1600.gz",
            "compression": "gz",
            "size_bytes": 1024,
        }
        mapping = _mapping()
        predicate = lambda asn: mapping.target_membership(asn) is not False
        with tempfile.TemporaryDirectory() as directory:
            spool = Path(directory) / "seed.mrt"
            spool.write_bytes(raw)
            receipt = build_parallel_rib_prefilter(
                spool,
                expected_spool_sha256=spool_sha,
                expected_spool_size_bytes=len(raw),
                seed_artifact_id=artifact["artifact_id"],
                seed_file_sha256=artifact["file_sha256"],
                artifact_slot_utc=SLOT_TEXT,
                raw_retention_mapping=mapping,
                workers=1,
                batch_records=64,
            )
            selected = validate_rib_prefilter(
                receipt,
                expected_spool_sha256=spool_sha,
                expected_spool_size_bytes=len(raw),
                seed_artifact_id=artifact["artifact_id"],
                seed_file_sha256=artifact["file_sha256"],
                artifact_slot_utc=SLOT_TEXT,
                raw_retention_mapping=mapping,
            )
            common = {
                "spool_path": spool,
                "expected_decompressed_sha256": spool_sha,
                "expected_decompressed_size_bytes": len(raw),
                "artifact": artifact,
                "origin_asn_predicate": predicate,
                "include_discarded_element_decisions": False,
                "prefilter_materialize_rib_ordinals": selected,
                "sparse_physical_record_count": receipt["population"][
                    "physical_record_count"
                ],
            }
            snapshots = []
            first = iter_rib_spool_artifact_records(
                **common,
                next_record_ordinal=0,
                next_record_offset=0,
                checkpoint_observer=lambda boundary, context: snapshots.append(
                    (boundary, context)
                ),
            )
            peer_row = next(first)
            selected_row = next(first)
            self.assertEqual(peer_row.raw_record.record_ordinal, 0)
            self.assertEqual(selected_row.raw_record.record_ordinal, 2)
            checkpoint_boundary, checkpoint_peer = snapshots[-1]
            first.close()

            resumed_boundaries = []
            resumed = tuple(
                iter_rib_spool_artifact_records(
                    **common,
                    next_record_ordinal=checkpoint_boundary.record_ordinal + 1,
                    next_record_offset=(
                        checkpoint_boundary.record_offset
                        + checkpoint_boundary.record_length
                    ),
                    previous_record_boundary=checkpoint_boundary,
                    peer_index_context=checkpoint_peer,
                    checkpoint_observer=lambda boundary, context: (
                        resumed_boundaries.append((boundary, context))
                    ),
                )
            )
            full = tuple(
                iter_rib_spool_artifact_records(
                    **common,
                    next_record_ordinal=0,
                    next_record_offset=0,
                )
            )

        self.assertEqual(
            tuple(
                event
                for row in (selected_row, *resumed)
                for event in row.route_events
            ),
            tuple(
                event
                for row in full
                for event in row.route_events
            ),
        )
        self.assertEqual(resumed_boundaries[-1][0].record_ordinal, 4)
        self.assertEqual(
            resumed_boundaries[-1][0].record_offset
            + resumed_boundaries[-1][0].record_length,
            len(raw),
        )


if __name__ == "__main__":
    unittest.main()
