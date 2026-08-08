#!/usr/bin/env python3
"""校验 P1 同一候选的 P0 35 例结果，并生成不可变制品清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


EXPECTED_COUNTS = {
    "direct": 20,
    "multi_turn": 5,
    "boundary": 5,
    "exception": 5,
}
EXPECTED_REVISION = "p0-v1-20260808-ir-r1"
SOURCE_PATHS = [
    "agent-sidecar/src/chat/contracts.ts",
    "agent-sidecar/src/chat/conversation-manager.ts",
    "agent-sidecar/src/chat/deterministic-engine.ts",
    "agent-sidecar/src/chat/general-read-model-provider.ts",
]


def canonical(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ",".join(canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + canonical(value[key])
            for key in sorted(value)
        ) + "}"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_sha256(root: Path) -> str:
    values = [
        {"path": path, "content": (root / path).read_text(encoding="utf-8")}
        for path in SOURCE_PATHS
    ]
    return sha256_bytes(canonical(values).encode("utf-8"))


def validate_result(root: Path, result: dict) -> list[str]:
    errors: list[str] = []
    if result.get("schema_version") != "country_outage_p1_candidate_result_v1":
        errors.append("候选结果 schema_version 无效")
    if result.get("p0_case_set_revision") != EXPECTED_REVISION:
        errors.append("P0 case-set revision 不一致")
    if result.get("collector_id") != "rrc25":
        errors.append("候选 collector 必须为 rrc25")
    actual_source_sha = source_sha256(root)
    if result.get("implementation_source_sha256") != actual_source_sha:
        errors.append("候选实现源码 SHA-256 与评测时身份不一致")
    expected_candidate_id = f"p1-candidate-{actual_source_sha[:16]}"
    if result.get("candidate_id") != expected_candidate_id:
        errors.append("candidate_id 与实现源码身份不一致")
    counts = result.get("counts")
    if not isinstance(counts, dict):
        errors.append("counts 缺失")
    else:
        for category, expected in EXPECTED_COUNTS.items():
            value = counts.get(category)
            if not isinstance(value, dict):
                errors.append(f"{category} 计数缺失")
                continue
            if value.get("total") != expected or value.get("passed") != expected:
                errors.append(f"{category} 必须 {expected}/{expected} 通过")
    results = result.get("results")
    if not isinstance(results, list) or len(results) != 35:
        errors.append("必须包含恰好 35 个逐案例结果")
    else:
        case_ids = [item.get("case_id") for item in results if isinstance(item, dict)]
        if len(set(case_ids)) != 35:
            errors.append("35 个案例 ID 必须唯一")
        for item in results:
            if not isinstance(item, dict) or item.get("passed") is not True:
                errors.append(f"案例未通过：{item.get('case_id') if isinstance(item, dict) else 'invalid'}")
                continue
            if item.get("forbidden_assertion_hits") != []:
                errors.append(f"案例命中禁止断言：{item.get('case_id')}")
            if item.get("identity_failure_count") != 0:
                errors.append(f"案例身份不一致：{item.get('case_id')}")
            if item.get("context_failures") != []:
                errors.append(f"案例上下文隔离失败：{item.get('case_id')}")
    gates = result.get("hard_gates")
    if not isinstance(gates, dict):
        errors.append("hard_gates 缺失")
    else:
        expected_gates = {
            "all_35_passed": True,
            "event_binding_percent": 100,
            "publication_binding_percent": 100,
            "forbidden_assertion_hits": 0,
            "invalid_answer_publications": 0,
        }
        for key, expected in expected_gates.items():
            if gates.get(key) != expected:
                errors.append(f"硬门禁 {key} 应为 {expected!r}")
    return errors


def validate_browser_evidence(value: dict) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") != "country_outage_p1_browser_acceptance_v1":
        errors.append("浏览器证据 schema_version 无效")
    if value.get("passed") is not True:
        errors.append("浏览器验收未通过")
    checks = value.get("checks")
    if not isinstance(checks, list) or len(checks) < 8:
        errors.append("浏览器验收至少需要 8 项检查")
    elif any(not isinstance(item, dict) or item.get("passed") is not True for item in checks):
        errors.append("浏览器验收存在失败检查")
    screenshots = value.get("screenshots")
    if not isinstance(screenshots, list) or len(screenshots) < 2:
        errors.append("浏览器验收必须包含桌面与窄屏截图")
    return errors


def git_candidate_paths(root: Path) -> list[str]:
    changed = subprocess.check_output(
        ["git", "diff", "--name-only"], cwd=root, text=True
    ).splitlines()
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=root,
        text=True,
    ).splitlines()
    return sorted(set(path for path in changed + untracked if path))


def write_manifest(
    root: Path,
    output: Path,
    result: dict,
    browser_path: Path | None,
) -> None:
    paths = [path for path in git_candidate_paths(root) if (root / path).is_file()]
    output_relative = output.relative_to(root).as_posix()
    paths = [path for path in paths if path != output_relative]
    artifacts = []
    for path in paths:
        content = (root / path).read_bytes()
        artifacts.append({
            "path": path,
            "size_bytes": len(content),
            "sha256": sha256_bytes(content),
        })
    manifest = {
        "schema_version": "country_outage_p1_candidate_manifest_v1",
        "candidate_id": result["candidate_id"],
        "p0_case_set_revision": result["p0_case_set_revision"],
        "p1_contract_revision": result["p1_contract_revision"],
        "collector_id": "rrc25",
        "base_commit": result["base_commit"],
        "implementation_source_sha256": result["implementation_source_sha256"],
        "candidate_result_sha256": sha256_bytes(canonical(result).encode("utf-8")),
        "browser_evidence": browser_path.relative_to(root).as_posix() if browser_path else None,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "boundary": {
            "implemented": True,
            "tested": True,
            "accepted": True,
            "deployed": False,
            "production_verified": False,
            "p2_or_rca": False,
        },
    }
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_manifest(root: Path, manifest: dict, result: dict) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != "country_outage_p1_candidate_manifest_v1":
        errors.append("候选 manifest schema_version 无效")
    for key in (
        "candidate_id",
        "p0_case_set_revision",
        "p1_contract_revision",
        "implementation_source_sha256",
    ):
        if manifest.get(key) != result.get(key):
            errors.append(f"manifest {key} 与候选结果不一致")
    if manifest.get("candidate_result_sha256") != sha256_bytes(
        canonical(result).encode("utf-8")
    ):
        errors.append("manifest 的候选结果 SHA-256 无效")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or manifest.get("artifact_count") != len(artifacts):
        errors.append("manifest 制品计数无效")
        return errors
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            errors.append("manifest 存在无效制品记录")
            continue
        path = artifact["path"]
        if path in seen:
            errors.append(f"manifest 重复制品：{path}")
            continue
        seen.add(path)
        target = (root / path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            errors.append(f"manifest 制品越出仓库：{path}")
            continue
        if not target.is_file():
            errors.append(f"manifest 制品缺失：{path}")
            continue
        content = target.read_bytes()
        if artifact.get("size_bytes") != len(content):
            errors.append(f"manifest 制品大小漂移：{path}")
        if artifact.get("sha256") != sha256_bytes(content):
            errors.append(f"manifest 制品摘要漂移：{path}")
    browser = manifest.get("browser_evidence")
    if not isinstance(browser, str) or browser not in seen:
        errors.append("manifest 未收录浏览器验收制品")
    else:
        errors.extend(validate_browser_evidence(
            json.loads((root / browser).read_text(encoding="utf-8"))
        ))
    boundary = manifest.get("boundary")
    if not isinstance(boundary, dict) or boundary != {
        "implemented": True,
        "tested": True,
        "accepted": True,
        "deployed": False,
        "production_verified": False,
        "p2_or_rca": False,
    }:
        errors.append("manifest 发布与能力边界无效")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", nargs="?", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--result",
        type=Path,
        default=Path("evaluation/country-outage/p1-v1/candidate-result.json"),
    )
    parser.add_argument("--browser-evidence", type=Path)
    parser.add_argument("--manifest-out", type=Path)
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    result_path = arguments.result if arguments.result.is_absolute() else root / arguments.result
    result = json.loads(result_path.read_text(encoding="utf-8"))
    errors = validate_result(root, result)
    browser_path = None
    if arguments.browser_evidence:
        browser_path = (
            arguments.browser_evidence
            if arguments.browser_evidence.is_absolute()
            else root / arguments.browser_evidence
        )
        errors.extend(validate_browser_evidence(
            json.loads(browser_path.read_text(encoding="utf-8"))
        ))
    if arguments.manifest:
        manifest_path = (
            arguments.manifest if arguments.manifest.is_absolute()
            else root / arguments.manifest
        )
        errors.extend(validate_manifest(
            root,
            json.loads(manifest_path.read_text(encoding="utf-8")),
            result,
        ))
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    if arguments.manifest_out:
        output = (
            arguments.manifest_out
            if arguments.manifest_out.is_absolute()
            else root / arguments.manifest_out
        )
        write_manifest(root, output, result, browser_path)
    print(
        f"PASS: {result['candidate_id']}，P0 35/35，RRC25 身份与禁止断言门禁通过"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
