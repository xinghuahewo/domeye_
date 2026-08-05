#!/usr/bin/env bash

set -Eeuo pipefail

readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"

python3 - "${REPOSITORY_ROOT}" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


root = Path(sys.argv[1])
documents = (
    root / "AGENTS.md",
    root / "README.md",
    root / "docs" / "主干开发与发布归一治理规范.md",
    root / "docs" / "Codex版本边界治理说明.md",
    root / "docs" / "开发与验收流水线.md",
    root / "deploy" / "README.md",
    root / "deploy" / "governance" / "README.md",
)
link_pattern = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
errors: list[str] = []
checked = 0

for document in documents:
    if not document.is_file():
        errors.append(f"缺少治理文档：{document.relative_to(root)}")
        continue
    text = document.read_text(encoding="utf-8")
    for match in link_pattern.finditer(text):
        raw_target = match.group(1).strip().strip("<>")
        parsed = urlsplit(raw_target)
        if parsed.scheme or raw_target.startswith(("#", "/")):
            continue
        relative_target = unquote(parsed.path)
        if not relative_target:
            continue
        checked += 1
        resolved = (document.parent / relative_target).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            errors.append(
                f"{document.relative_to(root)}：相对链接越出仓库：{raw_target}"
            )
            continue
        if not resolved.exists():
            errors.append(
                f"{document.relative_to(root)}：相对链接不存在：{raw_target}"
            )

if errors:
    print("治理文档相对链接检查失败：", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)

print(f"治理文档相对链接检查通过：{len(documents)} 份文档，{checked} 个相对链接。")
PY
