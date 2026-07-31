#!/usr/bin/env python3
"""阶段结束时回检国家中断报告与追问 Agent 是否偏离最终效果合同。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_PATH = (
    REPOSITORY_ROOT / "docs" / "国家中断报告与追问Agent最终验收文档.md"
)
PLAN_PATH = REPOSITORY_ROOT / "docs" / "国家中断报告与追问Agent分阶段计划.md"
A0_BASELINE_PATH = REPOSITORY_ROOT / "docs" / "国家中断报告与追问AgentA0基线.md"
CORE_ACCEPTANCE_CONFIG_PATH = (
    REPOSITORY_ROOT / "config" / "country-outage-agent-core-acceptance-v3.json"
)
EXTERNAL_PACK_CONFIG_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "country-outage-external-evidence-pack-v1.json"
)
EXTERNAL_ENVELOPE_SCHEMA_PATH = (
    REPOSITORY_ROOT
    / "contracts"
    / "agent"
    / "country-outage-external-evidence-envelope-v1.schema.json"
)
CORE_MANIFEST_PATH = REPOSITORY_ROOT / "backend" / "core.sha256"

CORE_STAGE_IDS = tuple(f"A{index}" for index in range(6))
EXTERNAL_STAGE_IDS = tuple(f"E{index}" for index in range(3))
STAGE_IDS = CORE_STAGE_IDS + EXTERNAL_STAGE_IDS
PROFILE_IDS = ("core", "external-evidence")
FRONTEND_IDS = tuple(f"FE-{index:02d}" for index in range(1, 10))
REPORT_IDS = tuple(f"RG-{index:02d}" for index in range(1, 14))
SCENARIO_IDS = tuple(f"SCE-{index:02d}" for index in range(1, 11))
CORE_FRONTEND_IDS = (
    "FE-01",
    "FE-02",
    "FE-03",
    "FE-04",
    "FE-05",
    "FE-06",
    "FE-08",
    "FE-09",
)
CORE_REPORT_IDS = (
    "RG-01",
    "RG-02",
    "RG-03",
    "RG-04",
    "RG-05",
    "RG-06",
    "RG-07",
    "RG-08",
    "RG-10",
    "RG-11",
    "RG-12",
    "RG-13",
)
CORE_SCENARIO_IDS = (
    "SCE-01",
    "SCE-02",
    "SCE-03",
    "SCE-04",
    "SCE-07",
    "SCE-08",
    "SCE-09",
    "SCE-10",
)
EXTERNAL_FRONTEND_IDS = ("FE-07",)
EXTERNAL_REPORT_IDS = ("RG-09",)
EXTERNAL_SCENARIO_IDS = ("SCE-05", "SCE-06")
STAGE_NAMES = {
    "A0": "两条验收主线和量化基线冻结",
    "A1": "报告输入、快照和事实闭合",
    "A2": "基础报告生成逻辑闭合",
    "A3": "前端和 Domeye-only 追问闭合",
    "A4": "模型、安全和运行闭合（核心剖面）",
    "A5": "前端与报告生成逻辑联合验收",
    "E0": "外部证据能力包合同冻结",
    "E1": "Evidence Gateway 与应用编排闭合",
    "E2": "外部证据能力包真实公网认证",
}
STAGE_DUE_FRONTEND = {
    "A0": CORE_FRONTEND_IDS,
    "A1": (),
    "A2": (),
    "A3": (
        "FE-01",
        "FE-02",
        "FE-03",
        "FE-04",
        "FE-05",
        "FE-06",
        "FE-08",
        "FE-09",
    ),
    "A4": (),
    "A5": CORE_FRONTEND_IDS,
    "E0": EXTERNAL_FRONTEND_IDS,
    "E1": (),
    "E2": EXTERNAL_FRONTEND_IDS,
}
STAGE_DUE_REPORT = {
    "A0": CORE_REPORT_IDS,
    "A1": ("RG-01", "RG-02", "RG-03", "RG-04"),
    "A2": ("RG-05", "RG-06", "RG-08", "RG-12", "RG-13"),
    "A3": ("RG-07",),
    "A4": ("RG-10", "RG-11", "RG-13"),
    "A5": CORE_REPORT_IDS,
    "E0": EXTERNAL_REPORT_IDS,
    "E1": (),
    "E2": EXTERNAL_REPORT_IDS,
}
STAGE_DUE_SCENARIOS = {
    "A0": CORE_SCENARIO_IDS,
    "A1": ("SCE-02", "SCE-03"),
    "A2": ("SCE-01", "SCE-02", "SCE-03", "SCE-09"),
    "A3": ("SCE-04", "SCE-08", "SCE-09", "SCE-10"),
    "A4": ("SCE-07", "SCE-10"),
    "A5": CORE_SCENARIO_IDS,
    "E0": EXTERNAL_SCENARIO_IDS,
    "E1": (),
    "E2": EXTERNAL_SCENARIO_IDS,
}
STAGE_PLAN_SIGNATURES = {
    "A0": (
        "FE-01 至 FE-09、RG-01 至 RG-13 和 SCE-01 至 SCE-10",
        "SCE-05、SCE-06 保留为能力包独立到期项",
    ),
    "A1": (
        "RG-01 至 RG-04 通过",
        "SCE-02 和 SCE-03 的数据侧结果通过",
    ),
    "A2": (
        "RG-05、RG-06、RG-08、RG-12 和 RG-13 的基础报告部分通过",
        "SCE-01、SCE-02、SCE-03 和 SCE-09 的生成侧结果通过",
    ),
    "A3": (
        "FE-01 至 FE-06、FE-08、FE-09 和 RG-07 通过",
        "SCE-04、SCE-08、SCE-09 和 SCE-10 的前端与问答部分通过",
    ),
    "A4": (
        "RG-10、RG-11 的核心子句和 RG-13 的完整干净重放通过",
        "SCE-07 和 SCE-10 的核心安全隔离部分通过",
    ),
    "A5": (
        "FE-01 至 FE-06、FE-08、FE-09 全部通过",
        "RG-01 至 RG-08、RG-10 至 RG-13 全部通过",
        "SCE-01 至 SCE-04、SCE-07 至 SCE-10 全部在最终验收环境通过",
    ),
    "E0": (
        "ExternalEvidenceProvider.fetch -> EvidenceEnvelope",
        "不在策略文件实现站点 DOM、数据结构、标题或发布日期提取",
    ),
    "E1": (
        "managed-egress-v1",
        "不把 Provider 挂入 `CountryOutageCore` 或核心 Agent",
    ),
    "E2": (
        "FE-07、RG-09、SCE-05、SCE-06",
        "手工浏览器访问、`curl`、测试夹具和模拟 Envelope 不能替代真实产品路径",
    ),
}


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回检国家中断报告与追问 Agent 是否偏离最终验收文档。",
    )
    parser.add_argument(
        "--stage",
        choices=STAGE_IDS,
        help="显式阶段结束回检；省略时作为 Codex Stop Hook 运行。",
    )
    parser.add_argument(
        "--profile",
        choices=PROFILE_IDS,
        help=(
            "验收剖面；A0 至 A5 默认为 core，"
            "E0 至 E2 必须为 external-evidence。"
        ),
    )
    return parser.parse_args(argv)


def expected_profile_for_stage(stage: str) -> str:
    return "core" if stage in CORE_STAGE_IDS else "external-evidence"


def normalize_profile(stage: str, profile: str | None) -> str:
    expected = expected_profile_for_stage(stage)
    selected = expected if profile is None else profile
    if selected != expected:
        raise ValueError(
            f"{stage} 只能使用 {expected} 验收剖面，当前为 {selected}。"
        )
    return selected


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"无法读取文档：{path}：{error}") from error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise RuntimeError(f"无法读取摘要对象：{path}：{error}") from error
    return digest.hexdigest()


def validate_frozen_baseline() -> list[str]:
    """机械核对核心 A0 版本化配置和四个冻结摘要。"""
    errors: list[str] = []
    for path in (A0_BASELINE_PATH, CORE_ACCEPTANCE_CONFIG_PATH):
        if not path.is_file():
            errors.append(f"A0 冻结对象不存在：{path}")
    if errors:
        return errors

    try:
        baseline = read_text(A0_BASELINE_PATH)
        config = json.loads(read_text(CORE_ACCEPTANCE_CONFIG_PATH))
    except (RuntimeError, json.JSONDecodeError) as error:
        return [f"无法读取 A0 冻结基线：{error}"]
    if not isinstance(config, dict):
        return ["A0 量化验收配置必须是 JSON 对象。"]

    expected_config_values = {
        "schema_version": 3,
        "id": "country-outage-agent-core-acceptance-v3",
        "status": "frozen",
        "acceptance_profile": "core-v1",
        "validator_rules_version":
            "country_outage_report_validator_rules_v5",
    }
    for key, expected in expected_config_values.items():
        if config.get(key) != expected:
            errors.append(
                f"A0 量化验收配置 {key} 必须为 {expected!r}。"
            )
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        str(config.get("frozen_at", "")),
    ):
        errors.append("A0 核心配置 frozen_at 必须是有效的 UTC 冻结时间。")

    representative = config.get("representative_event")
    expected_representative = {
        "event_reference": "country_outage/2026-02-27 09:12:32/IR/1/r",
        "incident_id":
            "incident_go_v1_a1de26f854831330c616a72af21597eb",
        "publication_id":
            "publication_v1_38bddead083db3f49023c2e1",
        "revision": 1,
        "is_final": True,
        "data_through": "2026-02-28T15:00:00Z",
        "collector_id": "rrc25",
        "cohort_id":
            "cohort_go_v1_4ff75dc68f95249de99c11bec48391fb",
        "window_start_utc": "2026-02-28T10:05:00Z",
        "window_end_utc": "2026-02-28T15:00:00Z",
        "interval_seconds": 300,
        "expected_observation_count": 60,
    }
    if representative != expected_representative:
        errors.append("A0 代表性伊朗事件、快照、窗口或 60 槽基线发生漂移。")

    scope = config.get("scope")
    if not isinstance(scope, dict):
        errors.append("A0 核心 scope 配置缺失。")
    else:
        expected_scope = {
            "event_type": "country_outage",
            "collector_id": "rrc25",
            "trigger": "user_only",
            "data_access": "published_read_only",
            "public_network_access": "none",
            "external_evidence_pack_required_for_core_acceptance": False,
            "conversation_persistence": "ephemeral",
        }
        if scope != expected_scope:
            errors.append(
                "A0 核心 scope 必须固定为已有 country_outage、唯一 RRC25、"
                "用户触发、只读、公共网络访问 none、外部能力包非阻断和短期会话。"
            )

    forbidden_top_level = (
        "external_evidence",
        "provider",
        "gateway",
        "gateway_policy",
        "application_orchestration",
        "optional_capability_packs",
    )
    for key in forbidden_top_level:
        if key in config:
            errors.append(
                f"A0 核心配置不得包含外部能力实现字段：{key}。"
            )
    forbidden_nested = {
        "timeouts": ("external_evidence_run_ms",),
        "retention": ("temporary_external_evidence_seconds",),
        "authorization": ("external_evidence_scope",),
    }
    for section, keys in forbidden_nested.items():
        value = config.get(section)
        if not isinstance(value, dict):
            errors.append(f"A0 核心配置缺少 {section}。")
            continue
        for key in keys:
            if key in value:
                errors.append(
                    f"A0 核心配置 {section}.{key} 必须迁入外部能力包。"
                )

    browser = config.get("browser_acceptance")
    if not isinstance(browser, dict):
        errors.append("A0 候选浏览器验收身份缺失。")
    else:
        if browser.get("browser") != "Google Chrome":
            errors.append("A0 候选浏览器必须固定为 Google Chrome。")
        if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", str(browser.get("version", ""))):
            errors.append("A0 候选浏览器必须记录精确四段版本。")
        expected_browser_flags = {
            "keyboard_required": True,
            "semantic_tree_required": True,
            "reduced_motion_required": True,
            "specific_real_screen_reader_required": False,
        }
        for key, expected in expected_browser_flags.items():
            if browser.get(key) != expected:
                errors.append(
                    f"A0 候选浏览器配置 {key} 必须为 {expected!r}。"
                )

    digest_targets = {
        "验收文档 SHA-256": ACCEPTANCE_PATH,
        "分阶段计划 SHA-256": PLAN_PATH,
        "防偏离 Hook SHA-256": Path(__file__).resolve(),
        "核心量化验收配置 SHA-256": CORE_ACCEPTANCE_CONFIG_PATH,
    }
    for label, path in digest_targets.items():
        match = re.search(
            rf"{re.escape(label)}：\s*`([a-f0-9]{{64}})`",
            baseline,
        )
        if not match:
            errors.append(f"A0 基线缺少 {label}。")
            continue
        try:
            actual = sha256_file(path)
        except RuntimeError as error:
            errors.append(str(error))
            continue
        if match.group(1) != actual:
            errors.append(
                f"A0 基线 {label} 漂移：冻结 {match.group(1)}，当前 {actual}。"
            )
    return errors


def validate_external_pack_contract() -> list[str]:
    """机械核对外部能力包配置和 Envelope；不代表真实公网验收通过。"""
    errors: list[str] = []
    for path in (EXTERNAL_PACK_CONFIG_PATH, EXTERNAL_ENVELOPE_SCHEMA_PATH):
        if not path.is_file():
            errors.append(f"外部证据能力包冻结对象不存在：{path}")
    if errors:
        return errors

    try:
        config = json.loads(read_text(EXTERNAL_PACK_CONFIG_PATH))
        schema = json.loads(read_text(EXTERNAL_ENVELOPE_SCHEMA_PATH))
    except (RuntimeError, json.JSONDecodeError) as error:
        return [f"无法读取外部证据能力包合同：{error}"]
    if not isinstance(config, dict) or not isinstance(schema, dict):
        return ["外部证据能力包配置和 Envelope schema 必须是 JSON 对象。"]

    expected_top_level = {
        "schema_version": 1,
        "id": "country-outage-external-evidence-pack-v1",
        "status": "frozen",
        "capability_id": "external_evidence",
        "acceptance_profile": "external-evidence-pack-v1",
        "required_for_core_acceptance": False,
    }
    for key, expected in expected_top_level.items():
        if config.get(key) != expected:
            errors.append(
                f"外部能力包 {key} 必须为 {expected!r}。"
            )
    if config.get("status") == "frozen" and not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        str(config.get("frozen_at", "")),
    ):
        errors.append("外部能力包 frozen_at 必须在 E0 冻结时填写 UTC 时间。")

    orchestration = config.get("application_orchestration")
    expected_orchestration = {
        "country_outage_core_interface":
            "CountryOutageCore.generateReport->Rrc25Report",
        "provider_interface":
            "ExternalEvidenceProvider.fetch->EvidenceEnvelope",
        "annex_composer_interface":
            "AnnexComposer.compose(report,evidence?)->DownloadArtifact",
        "core_knows_provider_or_network_semantics": False,
        "external_evidence_may_modify_report_body": False,
    }
    if orchestration != expected_orchestration:
        errors.append(
            "外部能力包必须只在 Domeye 应用编排层通过 Core、Provider、"
            "AnnexComposer 三接口组合，核心不得知道网络语义，外部不得修改正文。"
        )

    provider = config.get("provider")
    if not isinstance(provider, dict):
        errors.append("外部能力包缺少 Provider 配置。")
    else:
        expected_provider = {
            "contract_version":
                "country_outage_external_evidence_provider_v1",
            "default_provider": "disabled",
            "managed_provider": "managed-egress-v1",
            "sidecar_direct_public_network_access": False,
            "envelope_schema_path":
                "contracts/agent/"
                "country-outage-external-evidence-envelope-v1.schema.json",
        }
        if provider != expected_provider:
            errors.append(
                "外部能力包 Provider 必须默认 disabled，正式实现必须为 "
                "managed-egress-v1，Sidecar 不得直接访问公开网络。"
            )

    readiness = config.get("readiness")
    expected_readiness = {
        "provider_api_states": [
            "ready",
            "not_configured",
            "self_check_failed",
        ],
        "browser_local_states": ["checking", "unknown"],
        "unconfigured_ui_text": "当前环境未配置公开来源旁证",
        "entry_enabled_only_when": "ready",
        "managed_egress_requires_self_test": True,
        "managed_egress_requires_valid_pack_certificate": True,
    }
    if readiness != expected_readiness:
        errors.append(
            "外部能力包 readiness 必须把 provider API 状态固定为 "
            "ready/not_configured/self_check_failed，并把 checking/unknown "
            "仅作为浏览器本地状态。"
        )

    request_policy = config.get("request_policy")
    expected_request_policy = {
        "minimum_urls": 1,
        "maximum_urls": 5,
        "authorization_validity_seconds": 300,
        "explicit_public_urls_required": True,
        "url_discovery_allowed": False,
    }
    if request_policy != expected_request_policy:
        errors.append("外部能力包显式 URL 请求策略发生漂移。")

    gateway_policy = config.get("gateway_policy")
    expected_gateway_policy = {
        "policy_version": "country-outage-external-v1",
        "allowed_host_boundaries": [
            "bgp.he.net",
            "radar.cloudflare.com",
        ],
        "maximum_pages": 5,
        "maximum_response_bytes_per_page": 2097152,
        "maximum_redirects": 3,
        "allowed_schemes": ["http", "https"],
        "allow_private_networks": False,
        "allow_authenticated_pages": False,
        "allow_file_uploads": False,
    }
    if gateway_policy != expected_gateway_policy:
        errors.append("外部能力包 Gateway 窄策略或 2.1 安全上限发生漂移。")

    gateway = config.get("gateway")
    layering = gateway.get("layering") if isinstance(gateway, dict) else None
    if not isinstance(layering, dict):
        errors.append("外部能力包缺少 Gateway 三层职责。")
    else:
        generic = layering.get("generic_safe_execution")
        policy = layering.get("country_outage_policy")
        adapters = layering.get("source_adapters")
        if (
            not isinstance(generic, dict)
            or generic.get("may_contain_source_dom_or_data_extraction") is not False
        ):
            errors.append("Gateway 通用安全执行层不得包含站点解析。")
        if (
            not isinstance(policy, dict)
            or policy.get("policy_version") != "country-outage-external-v1"
            or policy.get("may_contain_source_dom_or_data_extraction") is not False
        ):
            errors.append("Gateway 窄策略不得包含站点解析。")
        expected_adapters = {
            ("hurricane-electric-v1", "bgp.he.net"),
            ("cloudflare-radar-v1", "radar.cloudflare.com"),
        }
        actual_adapters: set[tuple[Any, Any]] = set()
        if isinstance(adapters, list):
            for adapter in adapters:
                if isinstance(adapter, dict):
                    actual_adapters.add(
                        (adapter.get("id"), adapter.get("host_boundary"))
                    )
                    if adapter.get("may_open_network_connections") is not False:
                        errors.append(
                            "来源适配器不得绕过 Gateway 通用安全执行层联网。"
                        )
        if actual_adapters != expected_adapters:
            errors.append(
                "Gateway 必须分别冻结 Hurricane Electric 与 Cloudflare Radar "
                "来源适配器。"
            )

    required_schema_fields = {
        "schema_version",
        "provider",
        "provider_version",
        "policy_version",
        "policy_sha256",
        "status",
        "evidence_status",
        "requested_at",
        "retrieved_at",
        "frozen_binding",
        "sources",
        "claims",
    }
    schema_required = schema.get("required")
    if (
        not isinstance(schema_required, list)
        or not required_schema_fields.issubset(set(schema_required))
    ):
        errors.append("Evidence Envelope schema 缺少必须审计字段。")
    source = (
        schema.get("$defs", {}).get("source")
        if isinstance(schema.get("$defs"), dict)
        else None
    )
    source_required = source.get("required") if isinstance(source, dict) else None
    required_source_fields = {
        "source_id",
        "adapter_id",
        "status",
        "evidence_status",
        "source_url",
        "final_url",
        "title",
        "publisher",
        "published_at",
        "retrieved_at",
        "summary",
        "content_sha256",
    }
    if (
        not isinstance(source_required, list)
        or not required_source_fields.issubset(set(source_required))
    ):
        errors.append("Evidence Envelope source 缺少读取、来源或摘要审计字段。")
    return errors


TYPESCRIPT_DEPENDENCY = re.compile(
    r"""(?:import|export)\s+(?:type\s+)?"""
    r"""(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]"""
)
TYPESCRIPT_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
TYPESCRIPT_FULL_LINE_COMMENT = re.compile(r"(?m)^[ \t]*//[^\n]*$")


def typescript_without_comments(text: str) -> str:
    """移除纯注释，避免把“禁止 Provider”之类的边界说明误判为实现。"""
    return TYPESCRIPT_FULL_LINE_COMMENT.sub(
        "",
        TYPESCRIPT_BLOCK_COMMENT.sub("", text),
    )


def resolve_typescript_dependency(current: Path, specifier: str) -> Path | None:
    if not specifier.startswith("."):
        return None
    candidate = (current.parent / specifier).resolve()
    candidates = [candidate]
    if candidate.suffix in (".js", ".mjs", ".cjs"):
        candidates.insert(0, candidate.with_suffix(".ts"))
    elif not candidate.suffix:
        candidates.extend((candidate.with_suffix(".ts"), candidate / "index.ts"))
    for value in candidates:
        if value.is_file():
            return value
    return None


def typescript_dependency_graph(entry: Path) -> set[Path]:
    """递归展开相对 TypeScript import/export；不解析第三方包。"""
    visited: set[Path] = set()
    pending = [entry.resolve()]
    while pending:
        current = pending.pop()
        if current in visited or not current.is_file():
            continue
        visited.add(current)
        text = read_text(current)
        for match in TYPESCRIPT_DEPENDENCY.finditer(text):
            dependency = resolve_typescript_dependency(current, match.group(1))
            if dependency is not None and dependency not in visited:
                pending.append(dependency)
    return visited


def validate_core_public_network_boundary() -> list[str]:
    """机械阻止核心重新吸收外部证据或公开网络执行语义。"""
    errors: list[str] = []
    sidecar_source = REPOSITORY_ROOT / "agent-sidecar" / "src"
    core_directories = (
        "core",
        "domain",
        "pi",
        "report",
        "qa",
        "runtime",
    )
    core_semantic_paths: set[Path] = set()
    core_root = sidecar_source / "core"
    if core_root.is_dir():
        core_semantic_paths.update(core_root.rglob("*.ts"))
    session_manager = (
        sidecar_source / "server" / "country-outage-session-manager.ts"
    )
    if session_manager.is_file():
        core_semantic_paths.add(session_manager)

    forbidden_import_fragments = (
        "from '../external/",
        'from "../external/',
        "from './external/",
        'from "./external/',
        "node:dns",
        "node:http",
        "node:https",
    )
    forbidden_core_semantics = (
        r"\bexternal_?evidence\b",
        r"Provider",
        r"Gateway",
        r"Annex",
        r"\bURLs?\b",
        r"\bexternal_?(?:run|phase|stage)\b",
        r"\bdomeye_plus_external\b",
        r"\bcollecting_external\b",
        r"\bexternal_appendix\b",
        r"\bExternalAppendix\b",
        r"\bexternal_urls\b",
        r"外部证据",
        r"外部阶段",
        r"外部附录",
    )
    scanned: set[Path] = set()
    for directory in core_directories:
        root = sidecar_source / directory
        if root.is_dir():
            scanned.update(root.rglob("*.ts"))
    scanned.update(core_semantic_paths)
    for path in sorted(scanned):
        try:
            text = read_text(path)
        except RuntimeError as error:
            errors.append(str(error))
            continue
        for fragment in forbidden_import_fragments:
            if fragment in text:
                errors.append(
                    "核心源码不得导入外部 Provider 或公开网络实现："
                    f"{path.relative_to(REPOSITORY_ROOT)} 包含 {fragment!r}。"
                )
        if path not in core_semantic_paths:
            continue
        executable_text = typescript_without_comments(text)
        for pattern in forbidden_core_semantics:
            if re.search(pattern, executable_text, re.IGNORECASE):
                errors.append(
                    "CountryOutageCore 与 SessionManager 不得包含外部能力语义："
                    f"{path.relative_to(REPOSITORY_ROOT)} 命中 {pattern!r}。"
                )

    report_port = core_root / "report-generation-port.ts"
    if not report_port.is_file():
        errors.append(
            "核心缺少 CountryOutageReportGenerationPort.generateReport() "
            "报告生成端口。"
        )
    else:
        try:
            port_text = typescript_without_comments(read_text(report_port))
        except RuntimeError as error:
            errors.append(str(error))
        else:
            port_contract = re.search(
                r"export\s+interface\s+CountryOutageReportGenerationPort"
                r"\s*\{.*?generateReport\s*\(.*?\)\s*:"
                r"\s*Promise<Rrc25Report>\s*\}",
                port_text,
                re.DOTALL,
            )
            if not port_contract:
                errors.append(
                    "核心报告生成端口必须真实声明 "
                    "CountryOutageReportGenerationPort.generateReport() "
                    "并返回 Promise<Rrc25Report>。"
                )
    if session_manager.is_file():
        try:
            manager_text = typescript_without_comments(
                read_text(session_manager)
            )
        except RuntimeError as error:
            errors.append(str(error))
        else:
            if (
                "CountryOutageReportGenerationPort" not in manager_text
                or not re.search(
                    r"this\.#reportGenerator\s*\.\s*generateReport\s*\(",
                    manager_text,
                )
            ):
                errors.append(
                    "SessionManager 必须通过 "
                    "CountryOutageReportGenerationPort.generateReport() "
                    "生成 RRC25 报告。"
                )

    formal_entry = sidecar_source / "cli" / "serve-formal.ts"
    if formal_entry.is_file():
        try:
            formal_graph = typescript_dependency_graph(formal_entry)
        except RuntimeError as error:
            errors.append(str(error))
        else:
            forbidden_formal_paths = (
                "safe-external-evidence-service",
                "safe-http-transport",
            )
            forbidden_formal_text = (
                "SafeCountryOutageExternalEvidenceService",
                "node:dns",
                "node:dns/promises",
                "node:https",
            )
            for path in sorted(formal_graph):
                relative = path.relative_to(REPOSITORY_ROOT)
                normalized = relative.as_posix()
                if any(value in normalized for value in forbidden_formal_paths):
                    errors.append(
                        "正式 Sidecar 依赖图不得包含外部公网执行实现："
                        f"{normalized}。"
                    )
                try:
                    text = read_text(path)
                except RuntimeError as error:
                    errors.append(str(error))
                    continue
                for fragment in forbidden_formal_text:
                    if fragment in text:
                        errors.append(
                            "正式 Sidecar 依赖图不得包含安全外部服务或 "
                            f"DNS/HTTPS 直连：{normalized} 命中 "
                            f"{fragment!r}。"
                        )
    return errors


def validate_documents(profile: str) -> list[str]:
    """只检查合同结构与关键边界，不判断阶段业务效果是否已经实现。"""
    errors: list[str] = []
    if not ACCEPTANCE_PATH.is_file():
        errors.append(f"最终验收文档不存在：{ACCEPTANCE_PATH}")
    if not PLAN_PATH.is_file():
        errors.append(f"分阶段计划文档不存在：{PLAN_PATH}")
    if errors:
        return errors

    try:
        acceptance = read_text(ACCEPTANCE_PATH)
        plan = read_text(PLAN_PATH)
    except RuntimeError as error:
        return [str(error)]

    found_frontend_ids = tuple(
        f"FE-{value}"
        for value in re.findall(r"^### FE-(\d{2})：", acceptance, re.MULTILINE)
    )
    if found_frontend_ids != FRONTEND_IDS:
        errors.append(
            "最终验收文档的前端编号必须且只能按 FE-01 至 FE-09 "
            "顺序出现；当前为："
            + (", ".join(found_frontend_ids) or "无")
        )

    found_report_ids = tuple(
        f"RG-{value}"
        for value in re.findall(r"^### RG-(\d{2})：", acceptance, re.MULTILINE)
    )
    if found_report_ids != REPORT_IDS:
        errors.append(
            "最终验收文档的报告逻辑编号必须且只能按 RG-01 至 RG-13 "
            "顺序出现；当前为："
            + (", ".join(found_report_ids) or "无")
        )

    found_scenario_ids = tuple(
        re.findall(r"^\| (SCE-\d{2}) \|", acceptance, re.MULTILINE)
    )
    if found_scenario_ids != SCENARIO_IDS:
        errors.append(
            "最终验收文档的场景编号必须且只能按 SCE-01 至 SCE-10 "
            "顺序出现；当前为："
            + (", ".join(found_scenario_ids) or "无")
        )

    found_stage_ids = tuple(
        re.findall(r"^### ([AE][0-9])：", plan, re.MULTILINE)
    )
    if found_stage_ids != STAGE_IDS:
        errors.append(
            "分阶段计划的阶段编号必须且只能按 A0 至 A5、E0 至 E2 顺序出现；"
            "当前为："
            + (", ".join(found_stage_ids) or "无")
        )

    for section in ("#### 入口", "#### 出口", "#### 边界"):
        if plan.count(section) != len(STAGE_IDS):
            errors.append(
                f"分阶段计划必须为每个阶段且只为每个阶段提供 `{section}`；"
                f"当前数量为 {plan.count(section)}。"
            )

    required_acceptance_phrases = (
        "本文只设计最终效果",
        "最终验收只有两个要点",
        "前端",
        "报告生成逻辑",
        "技术报告研读工作台",
        "只处理当前用户有权访问的已有合法 `country_outage` 事件",
        "从始至终只使用 RRC25",
        "一份报告固定绑定一个发布快照",
        "正式报告最低数据门槛",
        "数据观测",
        "报告与追问",
        "就此追问",
        "返回最新",
        "仅使用 Domeye 数据",
        "外部证据补充",
        "用户上传",
        "只提取所需事实、摘要和必要短引文，不复制整篇文章",
        "模型与 API 差异不改变正式结果",
        "country_outage_report_validator_rules_v5",
        "启动时实际加载的固定三文件 Skill",
        "front matter 使用确定性安全标量序列化",
        "1 至 5 个本次明确确认的 URL",
        "`bgp.he.net`、`radar.cloudflare.com`",
        "同一标准基础报告",
        "重新生成完整报告或完整回答",
        "短期会话",
        "PDF 和 Markdown",
        "空白页或缺字",
        "必须冻结的量化门槛",
        "干净环境重放",
        "FE-01 至 FE-09、RG-01 至 RG-13 和 SCE-01 至 SCE-10",
        "核心剖面 `core-v1`",
        "外部证据能力包 `external-evidence-pack-v1`",
        "CountryOutageCore.generateReport -> Rrc25Report",
        "ExternalEvidenceProvider.fetch   -> EvidenceEnvelope",
        "AnnexComposer.compose(report, evidence?) -> DownloadArtifact",
        "通用安全执行层",
        "Hurricane Electric",
        "Cloudflare Radar",
        "当前环境未配置公开来源旁证",
        "`ready`、`not_configured`",
        "`checking` 与 `unknown`",
        "测试夹具、手工浏览器访问",
        "不能替代该验收",
    )
    for phrase in required_acceptance_phrases:
        if phrase not in acceptance:
            errors.append(f"最终验收文档缺少防偏离语义：{phrase}")

    required_plan_phrases = (
        "只定义阶段入口、阶段出口和实施边界",
        "目标只包含前端最终效果和报告生成逻辑",
        "未达到当前剖面的本阶段出口时不得进入该剖面的下一阶段",
        "每个阶段结束必须调用国家中断报告与追问 Agent 最终验收防偏离 Hook",
        "不修改 `backend/core/` 业务逻辑",
        "从始至终只使用 RRC25",
        "不支持任意国家、任意时间、collector 选择、多 collector",
        "外部证据能力包只在用户显式授权后",
        "country_outage_report_validator_rules_v5",
        "`bgp.he.net`",
        "`radar.cloudflare.com`",
        "不建设永久会话历史",
        "已形成版本化验收配置",
        "“数据观测”和“报告与追问”",
        "桌面、平板、手机、键盘、语义树、滚动、焦点和减少动态效果",
        "不允许通用聊天外观、草稿流、强制滚动",
        "跨用户内容不可见",
        "PDF 与 Markdown 使用同一通过校验的报告制品",
        "FE-01 至 FE-06、FE-08、FE-09 全部通过",
        "RG-01 至 RG-08、RG-10 至 RG-13 全部通过",
        "SCE-01 至 SCE-04、SCE-07 至 SCE-10 全部在最终验收环境通过",
        "CountryOutageCore.generateReport -> Rrc25Report",
        "ExternalEvidenceProvider.fetch -> EvidenceEnvelope",
        "AnnexComposer.compose(report,evidence?) -> DownloadArtifact",
        "不把 Provider 挂入 `CountryOutageCore` 或核心 Agent",
        "Gateway 的通用安全执行层",
        "`ready`、`not_configured`",
        "`checking` 与 `unknown`",
        "手工浏览器访问、`curl`、测试夹具和模拟 Envelope "
        "不能替代真实产品路径",
    )
    for phrase in required_plan_phrases:
        if phrase not in plan:
            errors.append(f"分阶段计划缺少阶段封口语义：{phrase}")

    if ACCEPTANCE_PATH.name not in plan:
        errors.append("分阶段计划没有引用最终验收文档。")

    for stage, signatures in STAGE_PLAN_SIGNATURES.items():
        match = re.search(
            rf"^### {stage}：[^\n]+\n(?P<body>.*?)(?=^### [AE][0-9]：|^## 五、)",
            plan,
            re.MULTILINE | re.DOTALL,
        )
        if not match:
            errors.append(f"分阶段计划缺少 {stage} 完整正文。")
            continue
        for signature in signatures:
            if signature not in match.group("body"):
                errors.append(
                    f"分阶段计划 {stage} 缺少到期映射：{signature}"
                )

    if profile == "core":
        errors.extend(validate_frozen_baseline())
        errors.extend(validate_core_public_network_boundary())
    elif profile == "external-evidence":
        errors.extend(validate_external_pack_contract())
    else:
        errors.append(f"未知验收剖面：{profile}")
    return errors


def check_frozen_core() -> list[str]:
    """检查不可变核心边界；不把检查通过解释为阶段业务通过。"""
    warnings: list[str] = []
    if not CORE_MANIFEST_PATH.is_file():
        return [f"未找到核心哈希清单：{CORE_MANIFEST_PATH}"]

    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "--", "backend/core"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return [f"无法检查 `backend/core/` 工作树状态：{error}"]

    changed = [line for line in status_result.stdout.splitlines() if line.strip()]
    if changed:
        warnings.append(
            "`backend/core/` 存在工作树变化：\n  " + "\n  ".join(changed)
        )

    try:
        hash_result = subprocess.run(
            ["sha256sum", "-c", CORE_MANIFEST_PATH.name],
            cwd=CORE_MANIFEST_PATH.parent,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        warnings.append(f"无法执行 `core.sha256` 校验：{error}")
        return warnings

    if hash_result.returncode != 0:
        detail = (hash_result.stdout + hash_result.stderr).strip()
        warnings.append(
            "`core.sha256` 校验失败："
            + (f"\n  {detail}" if detail else "未返回错误详情")
        )
    return warnings


def review_reason(profile: str, stage: str) -> str:
    due_frontend = "、".join(STAGE_DUE_FRONTEND[stage]) or "无新增到期项"
    due_report = "、".join(STAGE_DUE_REPORT[stage]) or "无新增到期项"
    due_scenarios = (
        "、".join(STAGE_DUE_SCENARIOS[stage]) or "无新增到期项"
    )
    core_warnings = check_frozen_core()
    core_section = ""
    if core_warnings:
        core_section = (
            "\n\n机检发现的不可变核心偏离信号：\n- "
            + "\n- ".join(core_warnings)
        )

    profile_name = (
        "核心剖面 core-v1"
        if profile == "core"
        else "外部证据能力包 external-evidence-pack-v1"
    )
    profile_contract = (
        CORE_ACCEPTANCE_CONFIG_PATH
        if profile == "core"
        else EXTERNAL_PACK_CONFIG_PATH
    )
    if profile == "core":
        external_review = """7. 核心 `CountryOutageCore` 是否保持公共网络访问为 none，
   是否完全不知道 Provider、URL、DNS、站点和 Gateway；正式核心装配是否仍直接
   实例化外部读取服务或把外部能力包证书当作核心阻断项；能力包未配置时报告、追问
   和核心下载是否仍完成；"""
        isolation_review = """8. 代码级权限、缓存和用户隔离是否贯穿生成、追问和下载；
   提示注入、不可信 Markdown、危险 URL、Shell、文件、SQL 和异常容量是否受阻；
   生产身份与真实 ACL 不作为本轮阻塞，但不得据此声称已完成生产验收；"""
        quantitative_review = """10. A0 冻结的核心量化门槛是否仍按同一版本执行；外部
    能力包是否只记录为未配置/未验收而没有被误写成通过；尚未到期的核心 FE、RG 和
    SCE 是否仍可达，是否通过删除、降级或改写合同规避偏离。"""
    else:
        external_review = """7. 外部 URL 核验是否只由用户显式授权，Domeye 应用编排
   是否只通过 Core、Provider、AnnexComposer 三接口组合；Gateway 通用安全执行层、
   `country-outage-external-v1` 窄策略和两个来源适配器是否职责分离；是否独立分区、
   统一标识为直接旁证、逐项引用且只使用必要摘要；"""
        isolation_review = """8. 外部核验和附录下载是否继承事件权限并保持跨用户隔离；
   未授权、内网、登录页面、浏览器会话、上传、恶意 URL、提示注入、超时和异常容量
   是否受阻；来源适配器是否绕过通用层联网，外部内容是否修改 Domeye 正文；"""
        quantitative_review = """10. E0 冻结的能力包量化门槛、Envelope schema、
    readiness 和版本绑定是否仍按同一版本执行；E2 是否使用真实产品路径而非夹具、
    手工浏览器或 curl；尚未到期条款是否仍可达。"""

    return f"""国家中断报告与追问 Agent 阶段结束回检：{profile_name} {stage} \
{STAGE_NAMES[stage]}

请完整重新阅读：
1. {ACCEPTANCE_PATH}
2. {PLAN_PATH}
3. {profile_contract}

本阶段到期或需冻结的前端要求：{due_frontend}。
本阶段到期或需冻结的报告逻辑要求：{due_report}。
本阶段到期或需冻结的场景：{due_scenarios}。

结束本阶段前，必须根据实际结果逐项判断：
1. 本阶段入口是否真实成立，出口、到期 FE、RG 和场景是否有实际证据，是否越过
   本阶段或全局边界；
2. 前端是否仍是事件内的技术报告研读工作台；数据观测、报告、追问、证据模式、
   状态、短期会话、新 revision、下载、移动端、滚动、焦点、键盘、语义树和减少
   动态效果是否符合到期 FE；特定真实读屏器环境不作为本轮阻塞；
3. 报告逻辑是否仍只覆盖已有合法 country_outage、用户触发和 RRC25，是否扩展到
   任意国家时间、第二 collector、通用 RCA、归因、处置或写入；
4. 报告、追问、下载和审计是否固定在同一快照；能力包启用时外部附录是否绑定同一
   快照；最低数据门槛、缺槽、能力降级和身份冲突是否失败关闭；
5. 关键数字是否可追溯和重复计算，是否混淆 Prefix、Prefix×VP、ASN、UPDATE、
   等价资源、IP、用户，或把时间对应写成因果；
6. 报告是否达到面向人的中文叙事，项目知识是否替代隐藏记忆；模型备用是否已经
   认证并完整重生成；干净环境是否不依赖 Codex 记忆；
{external_review}
{isolation_review}
9. PDF 与 Markdown 是否来自同一已校验制品，身份、摘要、中文字体、分页、表格、
   长链接和失败降级是否可核对；
{quantitative_review}{core_section}

判定规则：
- 一致：本阶段全部到期出口成立，未发现偏离；
- 已修正：偏离已在本阶段授权范围内完成修正；
- 无影响：本任务与国家中断报告 Agent 无关，且没有改变最终效果可达性；
- 存在待处理偏离：任一到期出口失败、阻塞或证据不足。必须列出 FE、RG 或 SCE 编号、
  偏离位置和原因，不得宣告阶段完成。

Hook 机检只覆盖合同结构和不可变核心状态，不代表数据与 Agent 工作面、报告、
追问、下载、外部证据、移动端、可访问性、模型、安全、短期重连、量化门槛、
干净环境重放、真实公网读取或生产效果已经通过。

最终答复必须包含一行：
“国家中断报告 Agent 最终验收回检：{profile_name} {stage} 一致 / 已修正 / 无影响 /
存在待处理偏离（FE、RG 或 SCE 编号与原因）”。"""


def run_explicit_stage_review(profile: str, stage: str) -> int:
    errors = validate_documents(profile)
    if errors:
        sys.stderr.write("国家中断报告 Agent 防偏离 Hook：结构检查失败\n")
        for error in errors:
            sys.stderr.write(f"- {error}\n")
        return 1

    sys.stdout.write(review_reason(profile, stage))
    sys.stdout.write("\n")
    return 0


def load_hook_input() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"未收到有效的 Codex Hook 输入：{error}") from error
    if not isinstance(value, dict):
        raise RuntimeError("Codex Hook 输入必须是 JSON 对象。")
    return value


def run_stop_hook() -> int:
    try:
        hook_input = load_hook_input()
    except RuntimeError as error:
        emit(
            {
                "continue": True,
                "systemMessage": (
                    f"国家中断报告 Agent 防偏离 Hook 无法执行：{error}。"
                    "请人工回读最终验收文档和分阶段计划。"
                ),
            }
        )
        return 0

    if hook_input.get("hook_event_name") != "Stop":
        emit({})
        return 0

    if hook_input.get("stop_hook_active") is True:
        emit({})
        return 0

    requested_stage = os.environ.get("DOMEYE_COUNTRY_OUTAGE_AGENT_STAGE")
    if requested_stage not in STAGE_IDS:
        # 只在调用方显式声明 Agent 阶段时介入，避免阻塞无关任务。
        emit({})
        return 0

    requested_profile = os.environ.get(
        "DOMEYE_COUNTRY_OUTAGE_AGENT_PROFILE"
    )
    try:
        profile = normalize_profile(requested_stage, requested_profile)
    except ValueError as error:
        emit({"decision": "block", "reason": str(error)})
        return 0

    errors = validate_documents(profile)
    if errors:
        emit(
            {
                "decision": "block",
                "reason": (
                    "国家中断报告 Agent 防偏离 Hook 的合同结构检查失败：\n- "
                    + "\n- ".join(errors)
                    + "\n请先修正文档结构，再结束任务。"
                ),
            }
        )
        return 0

    emit(
        {
            "decision": "block",
            "reason": review_reason(profile, requested_stage),
        }
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    if arguments.stage:
        try:
            profile = normalize_profile(arguments.stage, arguments.profile)
        except ValueError as error:
            sys.stderr.write(f"国家中断报告 Agent 防偏离 Hook：{error}\n")
            return 2
        return run_explicit_stage_review(profile, arguments.stage)
    if arguments.profile:
        sys.stderr.write("国家中断报告 Agent 防偏离 Hook：--profile 必须与 --stage 同用。\n")
        return 2
    return run_stop_hook()


if __name__ == "__main__":
    raise SystemExit(main())
