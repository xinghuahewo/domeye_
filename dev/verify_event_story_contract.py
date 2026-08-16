#!/usr/bin/env python3
"""验证伊朗事件详情响应是否覆盖产品合同十问和不可越界项。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


DEFAULT_REFERENCE = (
    "country_outage/2026-02-27%2009%3A12%3A32/IR/1/r"
)
DEFAULT_URL = (
    "http://127.0.0.1:28473/api/v1/events/story/" + DEFAULT_REFERENCE
)


def _require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def verify(payload: Any) -> list[str]:
    failures: list[str] = []
    root = _mapping(payload)
    event = _mapping(root.get("event"))
    observation = _mapping(root.get("observation"))
    cohort = _mapping(observation.get("cohort"))
    freshness = _mapping(observation.get("data_freshness"))
    baseline = _mapping(root.get("baseline"))
    detection = _mapping(root.get("detection"))
    impact = _mapping(root.get("impact"))
    lifecycle = _mapping(root.get("lifecycle"))
    precursor = _mapping(root.get("precursor"))
    evidence = _mapping(root.get("evidence"))
    contract_scope = _mapping(root.get("contract_scope"))
    claims = _list(root.get("claims"))
    unknowns = _list(root.get("unknowns"))
    actions = _list(root.get("actions"))
    series = _list(root.get("series"))

    _require(
        root.get("schema_version") == "event_detail_story_v1",
        "响应版本不是 event_detail_story_v1",
        failures,
    )

    # 十问 1：范围、时效和覆盖。
    _require(observation.get("collector_id") == "rrc25", "缺少 RRC25 范围", failures)
    _require(observation.get("observation_count") == 60, "状态点不是 60 个", failures)
    _require(cohort.get("baseline_origin_asn_count") == 563, "固定 ASN 人口不是 563", failures)
    _require(cohort.get("baseline_prefix_vp_count") == 384767, "固定 Prefix×VP 人口不符", failures)
    _require(freshness.get("quality_status") == "pass", "质量门未通过", failures)
    _require(bool(observation.get("coverage_statement")), "缺少覆盖说明", failures)

    # 十问 2：正常状态。
    _require(baseline.get("state") == "unknown", "正常带应保持 unknown", failures)
    _require(bool(baseline.get("reason")), "正常带未知缺少原因", failures)
    _require(bool(baseline.get("consequence")), "正常带未知缺少结论影响", failures)

    # 十问 3–4：变化与异常判定。
    _require(len(series) == 60, "变化序列不完整", failures)
    _require(bool(_mapping(detection.get("rule")).get("statement")), "缺少检测规则", failures)
    _require(_mapping(detection.get("rule")).get("threshold") == 0.03, "异常阈值不符", failures)
    _require(
        _mapping(detection.get("rule")).get("confirm_observation_count") == 2,
        "连续确认槽数不符",
        failures,
    )

    # 十问 5：时间语义。
    onset = _mapping(detection.get("onset"))
    detected = _mapping(detection.get("detected"))
    legacy_record = _mapping(detection.get("legacy_record"))
    _require(onset.get("precision") == "left_censored_at_window_start", "onset 未标记左删失", failures)
    _require(onset.get("at_local") != detected.get("at_local"), "onset 与 detected 被混用", failures)
    _require(legacy_record.get("not_event_onset") is True, "旧记录时间被当作 onset", failures)

    # 十问 6–7：影响规模与集中位置。
    peak = _mapping(impact.get("peak"))
    trough = _mapping(impact.get("trough"))
    window_end = _mapping(impact.get("window_end"))
    _require(peak.get("affected_asn_count") == 218, "峰值受影响 ASN 不是 218", failures)
    _require(peak.get("fully_invisible_asn_count") == 84, "峰值全不可见 ASN 不是 84", failures)
    _require(peak.get("partially_visible_asn_count") == 134, "峰值部分可见 ASN 不是 134", failures)
    _require(
        abs(float(trough.get("visible_prefix_vp_ratio", 0)) - 0.8231813019307789) < 1e-12,
        "Prefix×VP 谷值不符",
        failures,
    )
    _require(len(_list(impact.get("persistent_asns"))) >= 10, "持续受影响 ASN 不足", failures)
    _require(bool(trough.get("ipv4_visible_prefix_vp_ratio")), "缺少 IPv4 影响", failures)
    _require(bool(trough.get("ipv6_visible_prefix_vp_ratio")), "缺少 IPv6 影响", failures)

    # 十问 8：事件演化与恢复。
    _require(lifecycle.get("episode_count") == 1, "Episode 数量不符", failures)
    _require(lifecycle.get("wave_count") == 1, "Wave 数量不符", failures)
    _require(lifecycle.get("current_state") == "ongoing", "事件状态应为 ongoing", failures)
    _require(lifecycle.get("partial_recovery_at_local") is None, "错误确认部分恢复", failures)
    _require(lifecycle.get("full_recovery_at_local") is None, "错误确认完全恢复", failures)
    _require(window_end.get("affected_asn_count") == 161, "窗口末受影响 ASN 不符", failures)

    # 十问 9：证据、可信度和未知。
    _require(len(claims) >= 4, "关键结论不足", failures)
    for ordinal, claim_value in enumerate(claims, start=1):
        claim = _mapping(claim_value)
        _require(bool(claim.get("level")), f"结论 {ordinal} 缺少等级", failures)
        _require(bool(claim.get("confidence")), f"结论 {ordinal} 缺少可信度", failures)
        _require(bool(claim.get("scope")), f"结论 {ordinal} 缺少范围", failures)
        _require(bool(_list(claim.get("evidence_refs"))), f"结论 {ordinal} 缺少证据", failures)
    _require(len(unknowns) >= 5, "未知问题不足", failures)
    for ordinal, unknown_value in enumerate(unknowns, start=1):
        unknown = _mapping(unknown_value)
        for field, label in (
            ("question", "问题"),
            ("reason", "原因"),
            ("evidence_needed", "所需证据"),
            ("next_action", "下一步"),
        ):
            _require(bool(unknown.get(field)), f"未知项 {ordinal} 缺少{label}", failures)
    _require(evidence.get("consumed_deliverable_hashes_verified") is True, "消费文件未校验", failures)

    # 十问 10：下一步行动。
    _require(len(actions) >= 4, "下一步行动不足", failures)
    _require(all(bool(_mapping(item).get("reason")) for item in actions), "行动缺少理由", failures)

    # 不可越界项。
    _require(contract_scope.get("control_plane_only") is True, "未限定为控制面", failures)
    _require(contract_scope.get("causal_analysis_performed") is False, "错误声称已做因果分析", failures)
    _require(observation.get("left_censored") is True, "未表达左删失", failures)
    _require(observation.get("right_censored") is True, "未表达右删失", failures)
    _require(bool(event.get("service_impact_statement")), "缺少服务影响边界", failures)
    _require(precursor.get("causal_relation") == "not_assessed", "前兆被错误写成因果", failures)

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="事件叙事接口 URL")
    args = parser.parse_args()

    try:
        with urlopen(args.url, timeout=60) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        print(f"事件详情页产品合同验收失败：无法读取接口：{error}", file=sys.stderr)
        return 2

    failures = verify(payload)
    if failures:
        print("事件详情页产品合同验收失败：", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("事件详情页产品合同验收通过：十问、固定数据和不可越界项均满足。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
