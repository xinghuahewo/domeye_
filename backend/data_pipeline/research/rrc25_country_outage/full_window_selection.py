"""完整窗口 selection 与研究 Profile 的共享严格闭合校验。

该模块不读取文件。它复用稀疏抽样入口已经使用的完整 selection 身份、制品、
角色、coverage 和 failure 重算，再增加 Profile 的动态窗口与期望数量绑定。
finalize、full-window runner 或其他外围入口都可复用同一函数，避免仅比较列表
长度而把伪造的 ``status=complete`` 当成完整输入。
"""

from __future__ import annotations

from typing import Any, Mapping

from .pilot_sampling import PilotSamplingError, _validated_full_selection
from .profile import validate_research_profile


class FullWindowSelectionError(ValueError):
    """selection 身份、覆盖或 Profile 绑定不闭合。"""


def validate_complete_selection_against_profile(
    selection: Mapping[str, Any], profile: Mapping[str, Any]
) -> Mapping[str, Any]:
    """返回规范 Profile；只接受完整、零缺口、半开窗口 selection。"""

    try:
        normalized_profile = validate_research_profile(profile)
        roles, _start, _end = _validated_full_selection(selection)
    except (TypeError, ValueError, PilotSamplingError) as error:
        raise FullWindowSelectionError("selection 身份、角色或 coverage 重算失败") from error
    window = selection.get("window")
    coverage = selection.get("coverage")
    if not isinstance(window, Mapping) or not isinstance(coverage, Mapping):
        raise FullWindowSelectionError("selection 缺少 window/coverage")
    update_coverage = coverage.get("analysis_updates")
    rib_coverage = coverage.get("analysis_ribs")
    baseline_coverage = coverage.get("baseline_reference_rib")
    expected_updates = normalized_profile["input_selection"]["analysis_updates"][
        "expected_slot_count"
    ]
    expected_ribs = normalized_profile["input_selection"]["analysis_ribs"][
        "expected_slot_count"
    ]
    if (
        selection.get("status") != "complete"
        or selection.get("failures") != []
        or selection.get("study_id") != normalized_profile["study_id"]
        or selection.get("collector_id") != normalized_profile["collector_id"]
        or selection.get("country_code") != normalized_profile["country_code"]
        or window.get("start_utc") != normalized_profile["window"]["start_utc"]
        or window.get("end_exclusive_utc")
        != normalized_profile["window"]["end_exclusive_utc"]
        or window.get("interval_semantics") != "half_open"
        or window.get("granularity_seconds") != 300
        or len(roles["analysis_updates"]) != expected_updates
        or len(roles["analysis_ribs"]) != expected_ribs
        or roles["baseline_reference_rib"] is None
        or not isinstance(update_coverage, Mapping)
        or update_coverage
        != {
            "expected_count": expected_updates,
            "observed_count": expected_updates,
            "missing_count": 0,
        }
        or not isinstance(rib_coverage, Mapping)
        or rib_coverage
        != {
            "expected_count": expected_ribs,
            "observed_count": expected_ribs,
            "missing_count": 0,
        }
        or baseline_coverage != {"expected_count": 1, "observed_count": 1}
    ):
        raise FullWindowSelectionError("selection 未与 Profile 动态期望数量和半开窗口精确闭合")
    return normalized_profile


__all__ = (
    "FullWindowSelectionError",
    "validate_complete_selection_against_profile",
)
