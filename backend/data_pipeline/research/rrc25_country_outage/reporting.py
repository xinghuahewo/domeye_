"""生成 RRC25 伊朗国家中断研究的确定性中文报告。

报告生成器只消费已经结构化的研究结果，不读取数据库、文件或网络。它把
观测事实、外推/估算和未知项分栏表达；任何 ``value_state`` 非已观测值都
显示为“未知（原因）”，不会用零替代。
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any, Iterable, Mapping, Sequence


class ResearchReportInputError(ValueError):
    """报告输入不满足最小可审计结构。"""


_RATING_ZH = {
    "confirmed": "已确认",
    "revised": "已修订",
    "unverifiable": "不可验证",
    "hypothesis_only": "仅假设",
}
_RECOVERY_ZH = {
    "ongoing": "仍在持续",
    "recovering": "正在恢复",
    "partially_recovered": "部分恢复",
    "fully_recovered": "完全恢复",
    "unknown": "未知",
}
_OBSERVED_STATES = frozenset(
    {"observed", "observed_zero", "reported", "recomputed"}
)


def _required_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchReportInputError(f"{field} 必须是对象")
    return value


def _required_sequence(value: object, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ResearchReportInputError(f"{field} 必须是数组")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchReportInputError(f"{field} 必须是非空字符串")
    return value.strip()


def _utc(value: object, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    except ValueError as error:
        raise ResearchReportInputError(f"{field} 不是合法时间") from error
    if parsed.tzinfo is None:
        raise ResearchReportInputError(f"{field} 必须带时区")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResearchReportInputError(f"{field} 必须是数值")
    result = float(value)
    if not math.isfinite(result):
        raise ResearchReportInputError(f"{field} 必须是有限数")
    return result


def _json_value(value: object) -> str:
    if value is None:
        return "未知"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return format(value, ".6g")
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    return str(value).replace("|", "\\|").replace("\n", " ")


def _measure(measure: object) -> str:
    if not isinstance(measure, Mapping):
        return "未知（未提供结构化计量）"
    state = measure.get("value_state")
    if state in _OBSERVED_STATES:
        return _json_value(measure.get("value"))
    reason = measure.get("missing_reason")
    reason_text = str(reason).strip() if isinstance(reason, str) else "原因未记录"
    return f"未知（{reason_text}）"


def _duration(value: object) -> str:
    if not isinstance(value, Mapping):
        return "未知"
    state = value.get("duration_state")
    if state == "exact":
        return f"精确 {value.get('seconds')} 秒"
    if state == "lower_bound":
        return f"至少 {value.get('minimum_seconds')} 秒"
    if state == "interval":
        return (
            f"{value.get('minimum_seconds')}–{value.get('maximum_seconds')} 秒"
            "（连续性缺口区间）"
        )
    return "未知"


def _gate_summary(quality: Mapping[str, Any]) -> tuple[int, int, int]:
    passed = warned = failed = 0
    for gate in _required_sequence(quality.get("gates", ()), "quality.gates"):
        mapping = _required_mapping(gate, "quality.gates[]")
        status = mapping.get("status")
        if status == "pass":
            passed += 1
        elif status in {"warn", "pending"}:
            warned += 1
        elif status == "fail":
            failed += 1
        else:
            raise ResearchReportInputError("质量门包含未知状态")
    return passed, warned, failed


def build_research_report_zh(
    *,
    profile: Mapping[str, Any],
    run: Mapping[str, Any],
    input_selection: Mapping[str, Any],
    mapping_summary: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
    samples: Sequence[Mapping[str, Any]],
    episodes: Sequence[Mapping[str, Any]],
    waves: Sequence[Mapping[str, Any]],
    episode_as_records: Sequence[Mapping[str, Any]],
    reconciliation: Mapping[str, Any],
    quality: Mapping[str, Any],
    reproduction_commands: Sequence[str],
    source_temporal_evidence: Sequence[Mapping[str, Any]] = (),
) -> str:
    """返回可写入 Git 的确定性中文 Markdown 报告。"""

    profile = _required_mapping(profile, "profile")
    run = _required_mapping(run, "run")
    selection = _required_mapping(input_selection, "input_selection")
    mapping = _required_mapping(mapping_summary, "mapping_summary")
    reconciliation = _required_mapping(reconciliation, "reconciliation")
    quality = _required_mapping(quality, "quality")
    commands = [_text(item, "reproduction_commands[]") for item in reproduction_commands]
    if not commands:
        raise ResearchReportInputError("至少需要一条复现命令")

    study_id = _text(profile.get("study_id"), "profile.study_id")
    collector = _text(profile.get("collector_id"), "profile.collector_id")
    country = _text(profile.get("country_code"), "profile.country_code")
    window = _required_mapping(profile.get("window"), "profile.window")
    window_start = _utc(window.get("start_utc"), "profile.window.start_utc")
    window_end = _utc(
        window.get("end_exclusive_utc"), "profile.window.end_exclusive_utc"
    )
    run_id = _text(run.get("run_id"), "run.run_id")
    incident_ref = _text(run.get("incident_ref"), "run.incident_ref")
    acceptance = _text(
        quality.get("acceptance_state", run.get("acceptance_state")),
        "quality.acceptance_state",
    )
    mode = str(run.get("execution_mode", "full_profile"))
    pilot = mode != "full_profile"
    passed, warned, failed = _gate_summary(quality)

    coverage = _required_mapping(selection.get("coverage"), "selection.coverage")
    update_coverage = _required_mapping(
        coverage.get("analysis_updates"), "coverage.analysis_updates"
    )
    rib_coverage = _required_mapping(
        coverage.get("analysis_ribs"), "coverage.analysis_ribs"
    )
    execution = _required_mapping(run.get("execution"), "run.execution")

    temporal_lines: list[str] = []
    temporal_rows = _required_sequence(
        source_temporal_evidence, "source_temporal_evidence"
    )
    if temporal_rows:
        temporal_lines = [
            "### 2.1 旧事实双时间锚点",
            "",
            "| Incident | Locator 身份时间 | 旧文案候选时间 | 关系 | 前兆因果 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for index, raw in enumerate(temporal_rows):
            row = _required_mapping(raw, f"source_temporal_evidence[{index}]")
            locator = _required_mapping(
                row.get("locator_record_start"),
                f"source_temporal_evidence[{index}].locator_record_start",
            )
            candidate = _required_mapping(
                row.get("embedded_message_candidate"),
                f"source_temporal_evidence[{index}].embedded_message_candidate",
            )
            locator_utc = _utc(
                locator.get("utc"),
                f"source_temporal_evidence[{index}].locator_record_start.utc",
            )
            candidate_utc = _utc(
                candidate.get("utc"),
                f"source_temporal_evidence[{index}].embedded_message_candidate.utc",
            )
            if (
                locator.get("role") != "source_record_identity_only"
                or candidate.get("role")
                != "candidate_event_time_from_legacy_text"
                or row.get("relationship_state") != "unresolved_not_causal"
                or row.get("single_event_time_merge_allowed") is not False
                or row.get("precursor_causality_state") != "undetermined"
            ):
                raise ResearchReportInputError(
                    "旧事实双时间语义不得冒充确认事件时间或前兆因果"
                )
            temporal_lines.append(
                "| `{}` | `{}`（仅源记录身份） | `{}`（候选） | "
                "`unresolved_not_causal` | `undetermined` |".format(
                    _text(row.get("incident_id"), f"source_temporal_evidence[{index}].incident_id"),
                    locator_utc,
                    candidate_utc,
                )
            )
        temporal_lines.extend(
            [
                "",
                "Locator 时间不代表已确认事件起点；文案候选时间也未获确认。两者不得合并为单一事件时间，当前不能据此认定前兆或因果关系。",
                "",
            ]
        )

    lines = [
        "# RRC25 伊朗国家路由中断事件复算与对账报告",
        "",
        "## 1. 结论与证据等级",
        "",
        (
            f"本次运行 `{run_id}` 对 `{incident_ref}` 执行了"
            f"{'有界研究样本闭环' if pilot else '冻结 Profile 全窗口闭环'}。"
            f"研究验收状态为 **{acceptance}**；质量门通过 {passed} 项、"
            f"警告/待定 {warned} 项、失败 {failed} 项。"
        ),
        "",
    ]
    if pilot:
        lines.extend(
            [
                "> 本次是流程贯通用有界样本，不得外推为完整事件人口或生产验收结果。",
                "",
            ]
        )
    lines.extend(
        [
            "结论边界：RRC25 可以复算路由可见性、状态变化、波次与逐 ASN/前缀影响；"
            "不能单独证明物理断路、真实流量影响或政府意图。",
            "",
            "## 2. 冻结范围与资源",
            "",
            "| 项目 | 值 |",
            "| --- | --- |",
            f"| Study | `{study_id}` |",
            f"| Collector / 国家 | `{collector}` / `{country}` |",
            f"| Profile 半开窗口 | `[{window_start}, {window_end})` |",
            f"| 执行模式 | `{mode}` |",
            f"| 选中唯一制品 | {selection.get('selected_unique_artifact_count', '未知')} |",
            f"| 选中压缩字节 | {selection.get('selected_unique_size_bytes', '未知')} |",
            f"| 新增原始读取 | {execution.get('new_raw_bytes_read', '未知')} 字节 |",
            f"| 峰值临时空间 | {execution.get('peak_temporary_bytes', '未知')} 字节 |",
            f"| 最长 worker | {execution.get('max_worker_seconds', '未知')} 秒 |",
            f"| 数据库写操作 | {execution.get('database_write_operations', '未知')} |",
            "",
            *temporal_lines,
            "## 3. 输入完整性与映射",
            "",
            "| 输入 | 期望 | 可用 | 缺失 |",
            "| --- | ---: | ---: | ---: |",
            (
                "| 五分钟 UPDATE | "
                f"{update_coverage.get('expected_count', '未知')} | "
                f"{update_coverage.get('observed_count', '未知')} | "
                f"{update_coverage.get('missing_count', '未知')} |"
            ),
            (
                "| 八小时 RIB | "
                f"{rib_coverage.get('expected_count', '未知')} | "
                f"{rib_coverage.get('observed_count', '未知')} | "
                f"{rib_coverage.get('missing_count', '未知')} |"
            ),
            "",
            (
                "冻结国家映射共覆盖 "
                f"{mapping.get('unique_asn_count', '未知')} 个 ASN，其中 IR "
                f"{mapping.get('target_country_asn_count', '未知')} 个；冲突 ASN "
                f"{mapping.get('conflict_asn_count', '未知')} 个，国家缺失 "
                f"{mapping.get('missing_country_count', '未知')} 个。"
            ),
            "",
            "## 4. 基线、样本与事件时间线",
            "",
        ]
    )
    if baseline is None:
        lines.extend(["基线：未知（未形成稳定数值基线）。", ""])
    else:
        baseline = _required_mapping(baseline, "baseline")
        exclusion_boundary = _required_mapping(
            baseline.get("exclusion_boundary"), "baseline.exclusion_boundary"
        )
        boundary_at = _utc(
            exclusion_boundary.get("at_utc"),
            "baseline.exclusion_boundary.at_utc",
        )
        if (
            exclusion_boundary.get("role")
            != "user_supplied_earliest_possible_precursor_boundary"
            or exclusion_boundary.get("confirmation_state")
            != "candidate_not_confirmed"
            or exclusion_boundary.get("causal_claim_allowed") is not False
        ):
            raise ResearchReportInputError(
                "基线排除边界不得冒充确认 onset 或授权因果结论"
            )
        state = baseline.get("value_state", baseline.get("baseline_state"))
        if state in {"observed", "stable"}:
            lines.extend(
                [
                    (
                        f"基线中位数 `{_json_value(baseline.get('median'))}`，MAD "
                        f"`{_json_value(baseline.get('mad'))}`，实际窗口 "
                        f"`[{baseline.get('actual_start_utc')}, "
                        f"{baseline.get('actual_end_exclusive_utc')})`。"
                    ),
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    f"基线：未知（{baseline.get('missing_reason', '未记录原因')}）。",
                    "",
                ]
            )
        lines.extend(
            [
                (
                    f"基线扩展排除边界为 `{boundary_at}`，角色为 "
                    "`user_supplied_earliest_possible_precursor_boundary`，确认状态为 "
                    "`candidate_not_confirmed`，因果授权为 `false`。该边界不是 "
                    "Episode onset，也不构成前兆结论。"
                ),
                "",
            ]
        )
    lines.extend(
        [
            f"共生成 {len(samples)} 个五分钟样本、{len(episodes)} 个候选 Episode、"
            f"{len(waves)} 个 Wave、{len(episode_as_records)} 条逐 ASN 记录。",
            "",
            "| Episode | 起点 | 检出 | 低谷 | 恢复状态 | 持续时间 | Wave 数 |",
            "| --- | --- | --- | --- | --- | --- | ---: |",
        ]
    )
    if episodes:
        for episode in episodes:
            row = _required_mapping(episode, "episodes[]")
            lines.append(
                "| `{}` | {} | {} | {} | {} | {} | {} |".format(
                    row.get("episode_id", "未知"),
                    row.get("onset_at", "未知"),
                    row.get("detected_at", "未知"),
                    row.get("trough_at", "未知"),
                    _RECOVERY_ZH.get(str(row.get("recovery_state")), "未知"),
                    _duration(row.get("duration")),
                    len(row.get("wave_ids", ()))
                    if isinstance(row.get("wave_ids"), (list, tuple))
                    else "未知",
                )
            )
    else:
        lines.append("| 无已确认 Episode | — | — | — | 未达到判定条件 | 未知 | 0 |")

    lines.extend(
        [
            "",
            "## 5. 主张逐项对账",
            "",
            "| 主张类型 | 报告值 | 复算值 | 评级 | 依据 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    claims = _required_sequence(reconciliation.get("claims"), "reconciliation.claims")
    for claim in claims:
        item = _required_mapping(claim, "reconciliation.claims[]")
        original = _required_mapping(item.get("original_value"), "claim.original_value")
        recomputed = _required_mapping(
            item.get("recomputed_value"), "claim.recomputed_value"
        )
        lines.append(
            "| `{}` | {} | {} | {} | {} |".format(
                item.get("claim_type", "未知"),
                _measure(original),
                _measure(recomputed),
                _RATING_ZH.get(str(item.get("rating")), "未知"),
                _json_value(item.get("rationale_zh", "未记录")),
            )
        )

    lines.extend(
        [
            "",
            "## 6. 数据缺口与不可越界结论",
            "",
        ]
    )
    limitations = []
    for claim in claims:
        for limitation in claim.get("limitations_zh", ()):
            if isinstance(limitation, str) and limitation not in limitations:
                limitations.append(limitation)
    if selection.get("status") != "complete":
        limitations.insert(0, "输入 selection 不完整，事件数量、持续时间或人口结果不得作为确定值发布。")
    if not limitations:
        limitations.append("未记录额外限制；仍须遵守 RRC25 单观测源的因果边界。")
    lines.extend(f"- {item}" for item in limitations)

    lines.extend(
        [
            "",
            "## 7. 复现命令",
            "",
            "```bash",
            *commands,
            "```",
            "",
            "本报告正文排除运行时元数据；相同冻结输入、代码和映射应产生相同语义内容。",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = ("ResearchReportInputError", "build_research_report_zh")
