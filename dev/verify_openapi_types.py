#!/usr/bin/env python3
"""验证提交的 TypeScript API 类型与 OpenAPI 契约一致。"""

from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "openapi.json"
GENERATED = ROOT / "frontend" / "src" / "types" / "openapi.generated.d.ts"


def main():
    with tempfile.TemporaryDirectory(prefix="domeye-openapi-") as temp_dir:
        candidate = Path(temp_dir) / GENERATED.name
        subprocess.run(
            [
                "npm", "exec", "--", "openapi-typescript",
                str(CONTRACT), "-o", str(candidate),
            ],
            cwd=str(ROOT / "frontend"),
            check=True,
        )
        if not GENERATED.exists() or candidate.read_bytes() != GENERATED.read_bytes():
            print("OpenAPI 生成类型已漂移，请执行 make api-types。", file=sys.stderr)
            raise SystemExit(1)
    print("OpenAPI 生成类型与契约一致。")


if __name__ == "__main__":
    main()
