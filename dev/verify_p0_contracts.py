#!/usr/bin/env python3
"""验证 P0 JSON Schema、fixture JSON 唯一键与正反例结果。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "contracts" / "data"
VALIDATOR = ROOT / "dev" / "data_quality" / "validate_p0_contracts.cjs"


class ContractVerificationError(RuntimeError):
    """P0 合同文件或验证结果不符合约定。"""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractVerificationError("JSON 对象字段重复：{}".format(key))
        result[key] = value
    return result


def _load_json_strict(path: Path) -> Any:
    """读取 JSON，并拒绝重复键、NaN 和 Infinity。"""

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ContractVerificationError("JSON 不允许非有限数值：{}".format(value))
            ),
        )
    except ContractVerificationError as error:
        raise ContractVerificationError("JSON 文件无效：{}：{}".format(path, error)) from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractVerificationError("JSON 文件无效：{}".format(path)) from error


def _verify_json_files() -> int:
    paths = sorted(CONTRACT_ROOT.rglob("*.json"))
    if not paths:
        raise ContractVerificationError("contracts/data 下没有 JSON 合同或 fixture")
    for path in paths:
        _load_json_strict(path)
    return len(paths)


def main() -> int:
    try:
        file_count = _verify_json_files()
        subprocess.run(["node", str(VALIDATOR)], cwd=str(ROOT), check=True)
    except (ContractVerificationError, OSError, subprocess.CalledProcessError) as error:
        print("P0 数据合同验证失败：{}".format(error), file=sys.stderr)
        return 1
    print("P0 合同 JSON 唯一键检查通过：{} 个文件。".format(file_count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
