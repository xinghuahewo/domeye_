#!/usr/bin/env python3
"""机器核对国家中断通用观测页 S6 同候选、生产身份与 GFA 全量证据。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/data/国家中断通用观测页S6最终验收证据.json"
ACCEPTANCE = ROOT / "docs/国家中断通用观测页S6最终验收记录.md"
REMOTE_HOST = "root@10.99.8.16"
STAGE_VERIFIERS = tuple(
    ROOT / f"dev/verify_country_outage_generalization_s{index}.py"
    for index in range(1, 6)
)
GFA_IDS = tuple(f"GFA-{index:02d}" for index in range(1, 17))
REMOTE_PROBE = r"""
set -Eeuo pipefail
release_id="$1"
commit="$2"
tag="$3"
unified="/home/bgpdata/Domeye-Core-runtime/unified-releases/${release_id}"
candidate="${unified}/CANDIDATE-MANIFEST.json"
deployment="${unified}/DEPLOYMENT.json"
verification="${unified}/PRODUCTION-VERIFICATION.json"
backend="$(readlink -f /home/bgpdata/Domeye-Core-runtime/current)"
frontend_path="$(jq -er '.components.frontend.path' "${candidate}")"
frontend_release="$(jq -er '.components.frontend.release_id' "${candidate}")"
source "${backend}/deploy/lib/artifact-common.sh"
source "${backend}/deploy/lib/frontend-common.sh"
pid="$(ss -H -lntp 'sport = :28473' | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -n 1)"
test "${pid}" != ""
process_release="$(tr '\0' '\n' < "/proc/${pid}/environ" | sed -n 's/^DOMEYE_P0_PRODUCTION_RELEASE_ID=//p')"
process_general_root="$(tr '\0' '\n' < "/proc/${pid}/environ" | sed -n 's/^DOMEYE_COUNTRY_OUTAGE_GENERAL_READ_MODEL=//p')"
rollback_json="$("${backend}/deploy/country-outage-general-page/rollback-runtime.sh" --check)"
jq -n \
    --arg release_id "${release_id}" \
    --arg commit "${commit}" \
    --arg tag "${tag}" \
    --arg main_commit "$(git -C /home/bgpdata/Domeye-Core rev-parse refs/heads/main)" \
    --arg tag_type "$(git -C /home/bgpdata/Domeye-Core cat-file -t "${tag}")" \
    --arg tag_target "$(git -C /home/bgpdata/Domeye-Core rev-parse "${tag}^{}")" \
    --arg backend "${backend}" \
    --arg binding_commit "$(jq -er '.source_commit' "${backend}/BACKEND-SOURCE-BINDING.json")" \
    --arg binding_tag "$(jq -er '.source_tag' "${backend}/BACKEND-SOURCE-BINDING.json")" \
    --arg process_cwd "$(readlink -f "/proc/${pid}/cwd")" \
    --arg process_release "${process_release}" \
    --arg process_general_root "${process_general_root}" \
    --arg frontend_release "$(< /home/bgpdata/Domeye-Core-runtime/web/state/frontend-current)" \
    --arg expected_frontend_release "${frontend_release}" \
    --arg frontend_tree "$(domeye_frontend_tree_sha256 /home/bgpdata/Domeye-Core-runtime/web/dist)" \
    --arg expected_frontend_tree "$(jq -er '.components.frontend.tree_sha256' "${candidate}")" \
    --arg general_manifest_sha "$(sha256sum "${backend}/general-read-model/manifest.json" | awk '{print $1}')" \
    --arg expected_general_manifest_sha "$(jq -er '.frozen_data.general_read_model_manifest_sha256' "${candidate}")" \
    --arg database_sha "$(sha256sum /home/bgpdata/Domeye-Core-dev-data/state.json | awk '{print $1}')" \
    --arg expected_database_sha "$(jq -er '.protected_runtime.database_state_sha256' "${candidate}")" \
    --arg nginx_main_sha "$(sha256sum /etc/nginx/nginx.conf | awk '{print $1}')" \
    --arg expected_nginx_main_sha "$(jq -er '.protected_runtime.nginx_main_sha256' "${candidate}")" \
    --arg nginx_site_sha "$(sha256sum /etc/nginx/conf.d/domeye-core.conf | awk '{print $1}')" \
    --arg expected_nginx_site_sha "$(jq -er '.protected_runtime.nginx_site_sha256' "${candidate}")" \
    --arg sidecar_release "$(jq -er '.release_id' /home/bgpdata/Domeye-Core-runtime/country-outage-agent/state/active.json)" \
    --arg expected_sidecar_release "$(jq -er '.protected_runtime.sidecar_release_id' "${candidate}")" \
    --arg candidate_status "$(jq -er '.status' "${candidate}")" \
    --arg deployment_status "$(jq -er '.status' "${deployment}")" \
    --arg verification_status "$(jq -er '.status' "${verification}")" \
    --arg health "$(curl -fsS http://127.0.0.1:28471/api/v1/healthz | jq -r '.status')" \
    --argjson rollback "${rollback_json}" \
    '{
      release_id:$release_id,
      source:{expected_commit:$commit,main_commit:$main_commit,tag:$tag,tag_type:$tag_type,tag_target:$tag_target},
      backend:{path:$backend,binding_commit:$binding_commit,binding_tag:$binding_tag,process_cwd:$process_cwd,process_release:$process_release,general_root:$process_general_root},
      frontend:{release_id:$frontend_release,expected_release_id:$expected_frontend_release,tree_sha256:$frontend_tree,expected_tree_sha256:$expected_frontend_tree,path:$frontend_path},
      data:{general_manifest_sha256:$general_manifest_sha,expected_general_manifest_sha256:$expected_general_manifest_sha},
      protected:{database_sha256:$database_sha,expected_database_sha256:$expected_database_sha,nginx_main_sha256:$nginx_main_sha,expected_nginx_main_sha256:$expected_nginx_main_sha,nginx_site_sha256:$nginx_site_sha,expected_nginx_site_sha256:$expected_nginx_site_sha,sidecar_release_id:$sidecar_release,expected_sidecar_release_id:$expected_sidecar_release},
      states:{candidate:$candidate_status,deployment:$deployment_status,verification:$verification_status,health:$health},
      rollback:$rollback
    }'
"""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json(path: Path) -> dict[str, Any]:
    require(path.is_file(), f"缺少文件：{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON 顶层不是对象：{path}")
    return value


def command(arguments: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def run_stage_verifiers(candidate_commit: str) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for path in STAGE_VERIFIERS:
        result = command([sys.executable, str(path)], timeout=300)
        require(result.returncode == 0, f"前序阶段 verifier 失败：{path.name}：{result.stdout}{result.stderr}")
        payload = json.loads(result.stdout)
        require(payload.get("status") == "pass", f"前序阶段没有返回 pass：{path.name}")
        stage = str(payload.get("stage"))
        results[stage] = payload
    ancestry = command(["git", "merge-base", "--is-ancestor", candidate_commit, "HEAD"])
    require(ancestry.returncode == 0, "最终证据提交不包含生产候选提交")
    return results


def run_remote_probe(release_id: str, commit: str, tag: str) -> dict[str, Any]:
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", REMOTE_HOST, "bash", "-s", "--", release_id, commit, tag],
        input=REMOTE_PROBE,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    require(result.returncode == 0, f"生产身份探针失败：{result.stderr or result.stdout}")
    payload = json.loads(result.stdout)
    require(isinstance(payload, dict), "生产身份探针输出无效")
    return payload


def validate_remote(payload: dict[str, Any], release_id: str, commit: str, tag: str) -> None:
    source = payload["source"]
    require(payload["release_id"] == release_id, "生产 release-id 冲突")
    require(source == {
        "expected_commit": commit,
        "main_commit": commit,
        "tag": tag,
        "tag_type": "tag",
        "tag_target": commit,
    }, "生产主干或 annotated tag 身份冲突")
    backend = payload["backend"]
    require(backend["binding_commit"] == commit, "Backend 来源提交冲突")
    require(backend["binding_tag"] == tag, "Backend 来源 tag 冲突")
    require(backend["process_cwd"] == f'{backend["path"]}/backend', "Backend 实际进程目录冲突")
    require(backend["process_release"] == f"{release_id}-backend", "Backend 实际 release-id 冲突")
    require(backend["general_root"] == f'{backend["path"]}/general-read-model', "Backend 未选择同候选读模型")
    frontend = payload["frontend"]
    require(frontend["release_id"] == frontend["expected_release_id"], "Frontend release-id 冲突")
    require(frontend["tree_sha256"] == frontend["expected_tree_sha256"], "Frontend 线上字节冲突")
    require(payload["data"]["general_manifest_sha256"] == payload["data"]["expected_general_manifest_sha256"], "通用读模型摘要冲突")
    protected = payload["protected"]
    for name in ("database", "nginx_main", "nginx_site"):
        require(protected[f"{name}_sha256"] == protected[f"expected_{name}_sha256"], f"受保护状态发生变化：{name}")
    require(protected["sidecar_release_id"] == protected["expected_sidecar_release_id"], "Sidecar 身份发生变化")
    require(payload["states"] == {"candidate": "built", "deployment": "deployed", "verification": "passed", "health": "ok"}, "候选、部署、验证或健康状态不完整")
    require(payload["rollback"]["status"] == "ready", "回滚目标不可执行")


def main() -> int:
    evidence = load_json(EVIDENCE)
    acceptance = ACCEPTANCE.read_text(encoding="utf-8")
    require(evidence.get("schema_version") == "country_outage_general_page_s6_acceptance_v1", "S6 证据版本冲突")
    require(evidence.get("status") == "pass", "S6 证据没有通过")
    release_id = str(evidence["release_id"])
    candidate_commit = str(evidence["candidate_commit"])
    tag = str(evidence["annotated_tag"])
    require(re.fullmatch(r"[0-9a-f]{40}", candidate_commit) is not None, "生产候选提交无效")
    require(re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-z0-9._-]+", release_id) is not None, "生产 release-id 无效")
    require(tag == release_id, "本次发布 tag 必须等于 release-id")
    require(tuple(evidence.get("gfa", {}).keys()) == GFA_IDS, "GFA-01 至 GFA-16 身份或顺序冲突")
    for gfa_id in GFA_IDS:
        item = evidence["gfa"][gfa_id]
        require(item.get("status") == "pass", f"{gfa_id} 未通过")
        require(isinstance(item.get("evidence"), list) and item["evidence"], f"{gfa_id} 缺少证据")
    stage_results = run_stage_verifiers(candidate_commit)
    require(tuple(sorted(stage_results)) == ("S1", "S2", "S3", "S4", "S5"), "前序阶段 verifier 集合不完整")
    require(evidence["canary"]["status"] == "passed", "同制品 canary 未通过")
    require(evidence["production"]["status"] == "passed", "生产 API 验证未通过")
    require(evidence["production"]["repeat_order_concurrent_equal"], "重复、顺序或并发结果不一致")
    require(evidence["browser"]["status"] == "passed", "生产真实浏览器验收未通过")
    require(evidence["browser"]["javascript_errors"] == 0, "生产浏览器存在脚本错误")
    require(evidence["browser"]["forbidden_terms"] == [], "生产普通页面出现内部工程文字")
    require(evidence["boundaries"] == {
        "collector": "rrc25",
        "window": "[2026-02-24T00:00:00Z,2026-03-11T00:00:00Z)",
        "database_changed": False,
        "nginx_changed": False,
        "sidecar_changed": False,
        "paid_model_calls": 0,
        "raw_data_rerun": False,
    }, "S6 边界冲突")
    remote = run_remote_probe(release_id, candidate_commit, tag)
    validate_remote(remote, release_id, candidate_commit, tag)
    for phrase in (
        "GFA-01 至 GFA-16 全部通过",
        "同一不可变候选",
        "伊朗与马拉维",
        "prod21",
        "数据库、Nginx、Sidecar 和付费模型均未改变",
        "没有补提或重跑数据",
        "通用观测页最终验收回检：S6 已修正",
    ):
        require(phrase in acceptance, f"S6 验收记录缺少：{phrase}")
    print(json.dumps({
        "status": "pass",
        "stage": "S6",
        "release_id": release_id,
        "candidate_commit": candidate_commit,
        "gfa_passed": len(GFA_IDS),
        "previous_stages": sorted(stage_results),
        "production_identity": "equal",
        "rollback": "ready",
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "stage": "S6", "error": str(error)}, ensure_ascii=False, separators=(",", ":")))
        raise SystemExit(1)
