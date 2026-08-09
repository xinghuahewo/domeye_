#!/usr/bin/env python3
"""P0 v1.3 阶段 Alignment Hook。"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


VALIDATOR = Path(__file__).with_name("validate_country_outage_p0_v1_3.py")


def load_validator():
    specification = importlib.util.spec_from_file_location(
        "validate_country_outage_p0_v1_3", VALIDATOR
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("无法加载 P0 v1.3 校验器")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=("S0", "S1", "S2", "S3"))
    arguments = parser.parse_args()
    validator = load_validator()
    try:
        errors = validator.validate_stage(arguments.stage)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"P0 v1.3 {arguments.stage} Hook 无法执行：{error}", file=sys.stderr)
        return 2
    if errors:
        print(f"P0 v1.3 {arguments.stage} 存在待处理偏离：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    meanings = {
        "S0": "系统表面、运行证据和 unknown 已显式闭合",
        "S1": "能力账本、候选处置和 Oracle seed 已显式闭合",
        "S2": "35 个案例与不可变参考真值及能力映射已闭合",
        "S3": "新 revision、回执和 manifest 已闭合",
    }
    print(
        f"P0 v1.3 {arguments.stage} Alignment Hook 通过：{meanings[arguments.stage]}。"
        "该结果不等于 P1 产品、生产发布或 RCA 验收。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
