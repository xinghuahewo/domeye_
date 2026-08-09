#!/usr/bin/env python3
"""P1 阶段结束 Alignment Hook。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from country_outage_agent_program_review import run_explicit_review


STAGES = ("S0", "S1", "S2", "S3", "S4")


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回检 P1 阶段是否偏离最终验收文档。",
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=STAGES,
        help="刚结束的 P1 阶段。",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    return run_explicit_review("P1", arguments.stage)


if __name__ == "__main__":
    raise SystemExit(main())
