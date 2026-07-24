#!/usr/bin/env python3
"""在任务首次准备结束时，要求回检事件详情页产品合同。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "事件详情页产品合同.md"
)


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def main() -> int:
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        emit(
            {
                "continue": True,
                "systemMessage": "事件详情页产品合同回检 hook 未收到有效输入，请人工确认本次任务未偏离产品合同。",
            }
        )
        return 0

    if hook_input.get("hook_event_name") != "Stop":
        emit({})
        return 0

    # Codex 已因本 hook 继续过一次时直接放行，避免 Stop hook 自循环。
    if hook_input.get("stop_hook_active") is True:
        emit({})
        return 0

    if not CONTRACT_PATH.is_file():
        emit(
            {
                "decision": "block",
                "reason": (
                    "任务完成回检未通过：事件详情页产品合同文件不存在。"
                    f"预期位置：{CONTRACT_PATH}。请先恢复合同，再结束本次任务。"
                ),
            }
        )
        return 0

    reason = f"""任务完成前请执行一次“事件详情页产品合同回检”。

请重新阅读：{CONTRACT_PATH}

针对刚完成的任务逐项判断：
1. 是否继续帮助用户回答合同中的十个核心问题，而不是只展示字段、数量或证据编号；
2. 是否清楚区分观测范围、正常基线、异常发现、影响、演化、恢复、可信度与下一步行动；
3. 是否混淆了首次变化、达到阈值、系统检出、事实记录、峰值、谷值和恢复时间；
4. 是否把单 collector 的 BGP 控制面异常误写成全国用户服务中断，或把时间相关误写成因果关系；
5. 是否让关键结论能够说明证据范围、可信度和未知边界；
6. 是否把缺失或未知误写成 0、正常、未发生或已经恢复；
7. 是否为了实现便利削弱了产品合同，或把技术实现内容写进产品合同。

如果发现偏离：在本次授权范围内先修正，再结束任务；需要新增权限或扩大范围时，明确报告偏离点和阻塞原因。
如果任务与事件详情页无关：说明“对事件详情页产品合同无影响”即可，不要制造无关改动。

最终答复中加入一行简短结论：
“产品合同回检：一致 / 已修正 / 无影响 / 存在待处理偏离（说明原因）”。"""

    emit({"decision": "block", "reason": reason})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
