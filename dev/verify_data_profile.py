#!/usr/bin/env python3
"""验证唯一数据档与必须携带窗口元数据的开发快照一致。"""

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dev.data_profile import DataProfileError, load_data_profile  # noqa: E402


FIXTURE_PATH = ROOT / "dev" / "fixtures" / "api-snapshot.json"
RUNTIME_FILES_WITHOUT_COPIED_DATES = (
    ROOT / "dev" / "run_local.py",
    ROOT / "deploy" / "lib" / "data-profile.sh",
    ROOT / "deploy" / "lib" / "artifact-common.sh",
    ROOT / "deploy" / "build-fixed-frontend.sh",
)
COPIED_DATE_LITERALS = ("2026-02-01", "2026-03-31", "2026-04-01")
STATEFUL_FIXED_WINDOW_FILES = {
    ROOT / "dev" / "backend" / "manage-dev-api.sh": ("start", "end_exclusive", "snapshot"),
    ROOT / "dev" / "backend" / "stage-dev-info.sh": ("start",),
    ROOT / "dev" / "database" / "manage-dev-database.sh": ("start", "end_exclusive"),
    ROOT / "dev" / "database" / "prune-feb-mar.sql": ("start", "end_exclusive"),
    ROOT / "dev" / "database" / "verify-feb-mar.sql": ("start", "end_exclusive"),
}


def verify():
    profile = load_data_profile()
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    expected_window = {
        "start_time": profile["local"]["start"],
        "end_time": profile["local"]["snapshot"],
        "timezone": profile["timezone"],
    }
    if fixture.get("data_window") != expected_window:
        raise DataProfileError("Mock 快照 data_window 与唯一数据档不一致")

    for path in RUNTIME_FILES_WITHOUT_COPIED_DATES:
        text = path.read_text(encoding="utf-8")
        copied = [literal for literal in COPIED_DATE_LITERALS if literal in text]
        if copied:
            raise DataProfileError(
                "运行文件复制了数据档日期常量：{} ({})".format(
                    path.relative_to(ROOT), ", ".join(copied)
                )
            )

    # 这些脚本属于现有候选数据库的不可变裁剪边界，本阶段不重写其 SQL。
    # 数据档是权威来源；任何窗口变化都必须先让这里失败，再进入严格数据库验收。
    for path, field_names in STATEFUL_FIXED_WINDOW_FILES.items():
        text = path.read_text(encoding="utf-8")
        for field_name in field_names:
            expected = profile["local"][field_name]
            if expected not in text:
                raise DataProfileError(
                    "有状态脚本与数据档不一致：{} 缺少 {}={}".format(
                        path.relative_to(ROOT), field_name, expected
                    )
                )
    return profile


def main():
    try:
        profile = verify()
    except (DataProfileError, OSError, json.JSONDecodeError) as error:
        raise SystemExit("数据档校验失败：{}".format(error))
    print(
        "数据档校验通过：{}，{} <= t < {}，快照 {}（{}）".format(
            profile["id"],
            profile["window_start"],
            profile["window_end_exclusive"],
            profile["snapshot_time"],
            profile["timezone"],
        )
    )


if __name__ == "__main__":
    main()
