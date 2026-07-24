#!/usr/bin/env python3
"""执行固定 RRC25 伊朗 18:05–23:00 有界状态重放。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data_pipeline.research.rrc25_country_outage.bounded_replay_v2 import (  # noqa: E402
    BoundedReplayExecutionError,
    run_fixed_replay,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="固定执行 RRC25 伊朗 18:05–23:00 状态重放；不写数据库。"
    )
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--compatible-mapping", required=True, type=Path)
    parser.add_argument("--revised-mapping", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--database-proxy", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = run_fixed_replay(
            raw_root=arguments.raw_root,
            selection_path=arguments.selection,
            compatible_mapping_path=arguments.compatible_mapping,
            revised_mapping_path=arguments.revised_mapping,
            output_directory=arguments.output,
            database_proxy_path=arguments.database_proxy,
            progress=lambda message: print(message, flush=True),
        )
    except (BoundedReplayExecutionError, OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
