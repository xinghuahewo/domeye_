#!/usr/bin/env python3
"""校验 P1 页面能力语义覆盖 S0-S4 最终候选与证据闭包。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "country_outage_p1_page_coverage_manifest_v1"
S4_CANDIDATE = "p1-page-coverage-s4-live-rrc25-01"
S4_IDENTITY = "3317fbf78cf69b6a132bd17e2b10352b854e213579612dbb174050bdd491717f"
EXPECTED_QUESTIONS = ["IP地址变化情况", "IP地址变化趋势"]
REQUIRED_ROLES = {
    "s0_stage_receipt",
    "s1_stage_receipt",
    "s2_stage_receipt",
    "s3_stage_receipt",
    "s4_stage_receipt",
    "s4_same_candidate_manifest",
    "s4_browser_trace",
    "s4_semantic_review",
    "s4_unknowns",
    "final_acceptance_record",
    "p2_entry_receipt",
    "s4_capture_tool",
    "deterministic_validator",
    "regression_tests",
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"无法读取 JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON 顶层必须是对象：{path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_stringify_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{label} 必须是非空仓库相对路径")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"{label} 越出仓库：{value!r}")
    result = (ROOT / relative).resolve()
    try:
        result.relative_to(ROOT)
    except ValueError as error:
        raise RuntimeError(f"{label} 越出仓库：{value!r}") from error
    return result


def validate_stage_receipts(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    candidates = manifest.get("stage_candidates")
    if not isinstance(candidates, dict) or set(candidates) != {
        "S0", "S1", "S2", "S3", "S4"
    }:
        errors.append("stage_candidates 必须精确覆盖 S0-S4")
        return errors
    for stage in ("S0", "S1", "S2", "S3", "S4"):
        path = ROOT / (
            "evaluation/country-outage/p1-page-coverage/"
            f"{stage.lower()}/stage-receipt.json"
        )
        receipt = read_json(path)
        expected = candidates[stage]
        if receipt.get("stage") != stage or receipt.get("status") != "PASS":
            errors.append(f"{stage} 阶段回执未通过")
        if receipt.get("candidate_id") != expected:
            errors.append(f"{stage} 候选身份与 manifest 不一致")
        claims = receipt.get("prohibited_claims")
        if not isinstance(claims, dict) or any(value is not False for value in claims.values()):
            errors.append(f"{stage} 阶段回执存在越级声明")
        if receipt.get("unresolved_blockers") != []:
            errors.append(f"{stage} 阶段回执仍有阻断")
    if candidates.get("S4") != S4_CANDIDATE:
        errors.append("S4 candidate_id 漂移")
    return errors


def validate_s4_semantics() -> list[str]:
    errors: list[str] = []
    s4 = ROOT / "evaluation/country-outage/p1-page-coverage/s4"
    same = read_json(s4 / "same-candidate-manifest.json")
    review = read_json(s4 / "independent-semantic-review.json")
    trace = read_json(s4 / "browser-api-tool-evidence-state-trace.json")
    unknowns = read_json(s4 / "unclosed-unknowns.json")
    api = read_json(s4 / "raw/api-receipt.json")
    browser = read_json(s4 / "raw/browser-receipt.json")

    for label, value in {
        "same_candidate_manifest": same,
        "independent_semantic_review": review,
        "browser_trace": trace,
        "unknowns": unknowns,
    }.items():
        if value.get("candidate_id") != S4_CANDIDATE:
            errors.append(f"{label} candidate_id 漂移")
    if same.get("candidate_identity_sha256") != S4_IDENTITY:
        errors.append("S4 candidate_identity_sha256 漂移")
    if review.get("candidate_identity_sha256") != S4_IDENTITY:
        errors.append("独立 Reviewer 未绑定 S4 候选身份")
    if (
        review.get("verdict") != "PASS"
        or (review.get("review_summary") or {}).get("unresolved_blocker_count") != 0
    ):
        errors.append("独立产品语义审核未通过")
    reviewed_items = review.get("reviewed_items")
    if not isinstance(reviewed_items, list) or {
        item.get("case_id") for item in reviewed_items if isinstance(item, dict)
    } != {"S4-IP-001", "S4-IP-002", "S4-UX-001"}:
        errors.append("S4 Reviewer 未覆盖三个冻结案例")
    elif any(item.get("verdict") != "PASS" for item in reviewed_items):
        errors.append("S4 Reviewer 存在失败案例")

    if unknowns.get("blocking_count") != 0:
        errors.append("S4 仍有 blocking unknown")
    raw_unknowns = unknowns.get("unknowns")
    if not isinstance(raw_unknowns, list) or any(
        item.get("blocking") is not False
        for item in raw_unknowns
        if isinstance(item, dict)
    ):
        errors.append("S4 unknown 明细与 blocking_count 不一致")

    journeys = trace.get("journeys")
    if not isinstance(journeys, list) or len(journeys) != 1:
        errors.append("S4 必须有且只有一条冻结同候选浏览器旅程")
    else:
        journey = journeys[0]
        if journey.get("candidate_identity_sha256") != S4_IDENTITY:
            errors.append("S4 浏览器旅程未绑定候选身份")
        if journey.get("journey_id") != "S4-LIVE-IP-001":
            errors.append("S4 浏览器旅程身份漂移")

    conversation = (api.get("response") or {}).get("conversation")
    if not isinstance(conversation, dict):
        return errors + ["S4 API 回执缺少 conversation"]
    binding = conversation.get("binding")
    expected_binding = {
        "publication_id": (
            "country_outage_publication_v1_989f698fb6f6c32579eebe7bb2bc833f"
        ),
        "revision": 1,
        "collector_id": "rrc25",
        "data_through": "2026-03-11T00:00:00Z",
        "lifecycle_state": "event_end_unknown",
        "is_final_in_data_range": False,
    }
    if not isinstance(binding, dict):
        errors.append("S4 conversation 缺少绑定")
    else:
        for key, expected in expected_binding.items():
            if binding.get(key) != expected:
                errors.append(f"S4 binding.{key} 漂移")

    turns = conversation.get("turns")
    if not isinstance(turns, list) or [
        turn.get("question") for turn in turns if isinstance(turn, dict)
    ] != EXPECTED_QUESTIONS:
        return errors + ["S4 API 回执未精确包含两个冻结 IP 原问题"]

    for index, turn in enumerate(turns):
        label = f"turn[{index}]"
        answer = turn.get("answer")
        if not isinstance(answer, dict):
            errors.append(f"{label} 缺少回答")
            continue
        if answer.get("answerability") != "supported":
            errors.append(f"{label} 错误拒绝页面可回答 IP 目标")
        validation = answer.get("validation")
        if not isinstance(validation, dict) or validation.get("errors") != []:
            errors.append(f"{label} 回答验证未通过")
        receipt = answer.get("state_receipt")
        if not isinstance(receipt, dict) or receipt.get("status") != "committed":
            errors.append(f"{label} 未在验证后提交状态")
        semantic = answer.get("semantic_plan")
        goals = (((semantic or {}).get("user_goal_plan") or {}).get("goals"))
        decisions = (((semantic or {}).get("grounding_plan") or {}).get("decisions"))
        nodes = (((semantic or {}).get("grounding_plan") or {}).get("nodes"))
        if not isinstance(goals, list) or [
            goal.get("normalized_kind") for goal in goals
        ] != ["address_family_change", "new_prefix_resources"]:
            errors.append(f"{label} 未保留 fixed cohort 主答与新前缀独立补充")
        else:
            if any(
                goal.get("entities", {}).get("address_family") != "both"
                for goal in goals
            ):
                errors.append(f"{label} 静默缩窄了泛指 IP 地址族")
            expected_mode = "change_summary" if index == 0 else "event_window_trend"
            if goals[0].get("entities", {}).get("analysis_mode") != expected_mode:
                errors.append(f"{label} 改写了用户分析方式")
        if not isinstance(decisions, list) or len(decisions) != 2 or any(
            item.get("answerability") != "supported" for item in decisions
        ):
            errors.append(f"{label} 子目标裁决不完整")
        if not isinstance(nodes, list) or len(nodes) != 6:
            errors.append(f"{label} Grounding 节点数不是 6")

        evidence = answer.get("evidence")
        evidence_refs = {
            item.get("evidence_ref")
            for item in evidence
            if isinstance(item, dict)
        } if isinstance(evidence, list) else set()
        execution_nodes = ((answer.get("execution_trace") or {}).get("nodes"))
        if not isinstance(execution_nodes, list) or len(execution_nodes) != 6:
            errors.append(f"{label} 实际执行节点数不是 6")
        else:
            for node in execution_nodes:
                if json_stringify_sha256(node.get("output")) != node.get("output_sha256"):
                    errors.append(f"{label}.{node.get('node_id')} output SHA 不匹配")
                for ref in node.get("evidence_refs", []):
                    if ref not in evidence_refs:
                        errors.append(f"{label} Tool evidence ref 不可解析：{ref}")
        for result in answer.get("results", []):
            for ref in result.get("evidence_refs", []):
                if ref not in evidence_refs:
                    errors.append(f"{label} 回答 evidence ref 不可解析：{ref}")
        if any(item.get("source") == "model" for item in answer.get("evidence", [])):
            errors.append(f"{label} 出现模型生成事实")

    first_text = turns[0]["answer"].get("answer_text", "")
    second_text = turns[1]["answer"].get("answer_text", "")
    for fact in (
        "10,156,800", "10,069,760", "9,577,728", "267,292", "267,288",
        "窗口累计出现 700 条", "当前可见 111 条", "19,523",
        "窗口累计出现 1 条", "524,288",
        "单位不同", "最低点后的改善不等于恢复",
    ):
        if fact not in first_text:
            errors.append(f"IP 地址变化回答缺少必答事实或边界：{fact}")
    if "确定性时序趋势概括，不是正式历史趋势制品" not in second_text:
        errors.append("IP 地址趋势未区分事件内时序概括与正式历史趋势")

    assertions = browser.get("assertions")
    if not isinstance(assertions, dict) or any(value is not True for value in assertions.values()):
        errors.append("浏览器关键身份、计划、执行或状态断言不完整")
    screenshots = browser.get("screenshots")
    if not isinstance(screenshots, list) or {
        item.get("viewport") for item in screenshots if isinstance(item, dict)
    } != {"1440x1000", "390x844"}:
        errors.append("桌面与窄屏浏览器证据不完整")
    else:
        for item in screenshots:
            path = safe_path(item.get("path"), "browser screenshot")
            if not path.is_file() or sha256(path) != item.get("sha256"):
                errors.append(f"浏览器截图摘要不匹配：{item.get('path')}")
    return errors


def validate_manifest(path: Path) -> list[str]:
    manifest = read_json(path)
    errors: list[str] = []
    if manifest.get("schema_version") != SCHEMA:
        errors.append(f"manifest schema_version 必须为 {SCHEMA}")
    if manifest.get("candidate_id") != S4_CANDIDATE:
        errors.append("manifest candidate_id 漂移")
    if manifest.get("candidate_identity_sha256") != S4_IDENTITY:
        errors.append("manifest candidate_identity_sha256 漂移")
    if manifest.get("status") != "accepted_candidate":
        errors.append("manifest status 必须为 accepted_candidate")
    claims = manifest.get("claims")
    if not isinstance(claims, dict) or any(
        claims.get(key) is not False
        for key in ("merged", "deployed", "production_verified", "p2_complete", "rca_complete")
    ):
        errors.append("manifest 必须显式关闭合并、部署、生产、P2 和 RCA 声明")
    entry = manifest.get("p0_entry")
    if entry != {
        "revision": "p0-v1.3-20260809-ir-r1",
        "case_count": 35,
        "adopt": 17,
        "defer": 5,
        "reject": 4,
        "unknown_count": 8,
    }:
        errors.append("manifest P0 v1.3 入口计数漂移")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return errors + ["manifest 缺少 artifacts"]
    roles = [item.get("role") for item in artifacts if isinstance(item, dict)]
    paths = [item.get("path") for item in artifacts if isinstance(item, dict)]
    if len(roles) != len(set(roles)) or len(paths) != len(set(paths)):
        errors.append("manifest artifact role/path 必须唯一")
    missing = REQUIRED_ROLES - set(roles)
    if missing:
        errors.append(f"manifest 缺少制品角色：{sorted(missing)}")
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            errors.append(f"artifacts[{index}] 必须是对象")
            continue
        artifact_path = safe_path(artifact.get("path"), f"artifacts[{index}].path")
        if not artifact_path.is_file():
            errors.append(f"manifest 制品不存在：{artifact.get('path')}")
            continue
        if sha256(artifact_path) != artifact.get("sha256"):
            errors.append(f"manifest 制品摘要不匹配：{artifact.get('path')}")
        if artifact.get("size_bytes") != artifact_path.stat().st_size:
            errors.append(f"manifest 制品大小不匹配：{artifact.get('path')}")

    errors.extend(validate_stage_receipts(manifest))
    errors.extend(validate_s4_semantics())

    record = (ROOT / "docs/agent/P1-聊天问答/P1-页面能力覆盖-阶段与最终验收记录.md").read_text(
        encoding="utf-8"
    )
    handoff = (ROOT / "docs/agent/P1-聊天问答/P1-页面能力覆盖-P2入口回执.md").read_text(
        encoding="utf-8"
    )
    for phrase in (
        "P1 页面能力语义覆盖加固候选通过",
        "不表示已合并、已部署或生产验证",
        "不具备 P2 组合调查、P3 假设、P4 多源证据或 P5 RCA",
    ):
        if phrase not in record:
            errors.append(f"最终验收记录缺少边界：{phrase}")
    for phrase in (
        "P2 入口未开放",
        "RRC25-only",
        "不得直接复用 P1 的通过结论宣称 RCA",
    ):
        if phrase not in handoff:
            errors.append(f"P2 入口回执缺少边界：{phrase}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    args = parser.parse_args()
    try:
        path = safe_path(args.manifest, "manifest")
        errors = validate_manifest(path)
    except RuntimeError as error:
        errors = [str(error)]
    if errors:
        for error in errors:
            print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print(
        "P1 页面能力覆盖 manifest：PASS "
        f"candidate={S4_CANDIDATE} identity={S4_IDENTITY[:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
