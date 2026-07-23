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


if __name__ == "__main__":
    unittest.main()
