"""为伊朗 RRC25 seed spool 构建并行 native prefilter sidecar。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from backend.data_pipeline.research.rrc25_country_outage.coordinator import (
    load_json_metadata,
)
from backend.data_pipeline.research.rrc25_country_outage.country_impact import (
    build_raw_retention_mapping_union,
    mapping_view_from_frozen_snapshot,
    mapping_view_from_revised_snapshot,
)
from backend.data_pipeline.research.rrc25_country_outage.file_artifacts import (
    write_canonical_json,
)
from backend.data_pipeline.research.rrc25_country_outage.rib_prefilter import (
    build_parallel_rib_prefilter,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="并行验证 seed V2 RIB origin 并冻结顺序 replay ordinal sidecar"
    )
    parser.add_argument("--spool", required=True)
    parser.add_argument("--spool-sha256", required=True)
    parser.add_argument("--spool-size-bytes", required=True, type=int)
    parser.add_argument("--seed-artifact-id", required=True)
    parser.add_argument("--seed-file-sha256", required=True)
    parser.add_argument("--artifact-slot-utc", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--revised-mapping", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--batch-records", type=int, default=4096)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    compatible_snapshot = load_json_metadata(
        args.mapping, maximum_bytes=64 * 1024 * 1024
    )
    revised_snapshot = load_json_metadata(
        args.revised_mapping, maximum_bytes=16 * 1024 * 1024
    )
    compatible = mapping_view_from_frozen_snapshot(compatible_snapshot)
    revised = mapping_view_from_revised_snapshot(
        revised_snapshot, compatible_snapshot
    )
    raw_retention = build_raw_retention_mapping_union(
        (compatible, revised)
    )
    receipt = build_parallel_rib_prefilter(
        args.spool,
        expected_spool_sha256=args.spool_sha256,
        expected_spool_size_bytes=args.spool_size_bytes,
        seed_artifact_id=args.seed_artifact_id,
        seed_file_sha256=args.seed_file_sha256,
        artifact_slot_utc=args.artifact_slot_utc,
        raw_retention_mapping=raw_retention,
        workers=args.workers,
        batch_records=args.batch_records,
    )
    published = write_canonical_json(
        Path(args.output),
        receipt,
        kind="rrc25_parallel_rib_prefilter",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(published.path),
                "sha256": published.sha256,
                "size_bytes": published.size_bytes,
                "receipt_fingerprint_sha256": receipt[
                    "receipt_fingerprint_sha256"
                ],
                "population": receipt["population"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
