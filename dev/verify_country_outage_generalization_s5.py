#!/usr/bin/env python3
"""机器核对国家中断通用观测页 S5 页面、同窗下钻与浏览器证据。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "frontend/src/components/CountryOutageGeneralPage.vue"
EVENT_PAGE = ROOT / "frontend/src/pages/EventDetailPage.vue"
ASN_PAGE = ROOT / "frontend/src/pages/AsnPage.vue"
EVENT_API = ROOT / "frontend/src/api/events.ts"
FEATURE_API = ROOT / "frontend/src/api/features.ts"
ASN_SERVICE = ROOT / "backend/services/asn_service.py"
ACCEPTANCE = ROOT / "docs/国家中断通用观测页S5验收记录.md"
EVIDENCE = ROOT / "docs/data/国家中断通用观测页S5浏览器证据.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read(path: Path) -> str:
    require(path.is_file(), f"缺少文件：{path}")
    return path.read_text(encoding="utf-8")


def main() -> int:
    component = read(COMPONENT)
    template = component[component.index("<template>"):component.index("</template>")]
    event_page = read(EVENT_PAGE)
    asn_page = read(ASN_PAGE)
    event_api = read(EVENT_API)
    feature_api = read(FEATURE_API)
    asn_service = read(ASN_SERVICE)
    acceptance = read(ACCEPTANCE)
    evidence = json.loads(read(EVIDENCE))
    candidate = evidence.get("候选提交", "")

    headings = (
        "前缀中断数量变化",
        "AS 中断数量变化",
        "IP 地址变化趋势",
        "哪些 AS 出现了路由不可见",
        "实际路径中关联了哪些网络",
    )
    positions = [template.index(value) for value in headings]
    require(positions == sorted(positions), "页面信息顺序偏离最终验收文档")
    for text in (
        "PRODUCT", "PUBLICATION", "REVISION", "DATA THROUGH", "Prefix×VP",
        "同一冻结制品", "incident_go_", "trend_product_", "observation_publication_",
    ):
        require(text not in template, f"普通页面出现内部工程文字：{text}")
    for text in (
        "不可见独立方向峰值", "同期中断前缀峰值", "同期 IPv4 地址量峰值",
        "同期 IPv6 /48 峰值", "查看关联路径",
    ):
        require(text in component, f"页面缺少用户任务字段：{text}")
    require("const asPageSize = 20" in component, "受影响 AS 分页大小偏离")
    require("const pathPageSize = 15" in component, "路径关联分页大小偏离")
    require("path_samples" in component and "slice" not in template, "路径样本展示异常")

    require("getCountryOutageGeneralPage" in event_page, "事件页没有通用入口")
    require("<CountryOutageGeneralPage" in event_page, "事件页没有通用页面组件")
    general_page_start = event_api.index(
        "export async function getCountryOutageGeneralPage"
    )
    general_page_end = event_api.index(
        "export interface CountryOutageGeneralAffectedAsQuery",
        general_page_start,
    )
    general_page_api = event_api[general_page_start:general_page_end]
    require("/audit" not in general_page_api, "通用普通页面 API 仍请求审计接口")
    require(
        "normalizeCountryOutageGeneralPage" in general_page_api,
        "页面没有组合身份校验",
    )

    for text in (
        "event_start", "event_end", "event_ref", "return_anchor",
        "eventContext.value?.reference", "cursor += 5 * 60 * 1000", "announce: null",
        "EVENT WINDOW DOSSIER",
    ):
        require(text in asn_page, f"AS 同窗或缺失语义缺少：{text}")
    require("event_reference" in feature_api, "AS 特征请求没有绑定事件引用")
    for text in (
        "事件窗口必须指定 ASN", "请求范围与国家中断事件窗口不一致",
        "country_outage_general_read_model().resolve(reference)",
        "previous_start = start if event_window", "days=45 if event_window else 1",
    ):
        require(text in asn_service, f"AS 服务端事件窗口门缺少：{text}")

    require(evidence["schema_version"] == "country_outage_general_page_s5_browser_evidence_v1", "浏览器证据版本冲突")
    require(
        isinstance(candidate, str)
        and candidate.startswith("git:")
        and len(candidate) == 44
        and all(value in "0123456789abcdef" for value in candidate[4:]),
        "浏览器证据没有绑定完整候选提交",
    )
    require(candidate in acceptance, "S5 验收记录没有绑定浏览器证据候选提交")
    require(evidence["伊朗"]["画布数"] == 4, "伊朗图表人口冲突")
    require(evidence["伊朗"]["受影响AS总数"] == 525, "伊朗 AS 人口冲突")
    require(evidence["伊朗"]["路径关联总数"] == 1956, "伊朗路径人口冲突")
    require(evidence["伊朗"]["普通页面内部术语命中"] == [], "伊朗页面出现内部文字")
    require(evidence["马拉维"]["画布数"] == 4, "马拉维图表人口冲突")
    require(evidence["马拉维"]["受影响AS总数"] == 8, "马拉维 AS 人口冲突")
    require(evidence["马拉维"]["路径关联总数"] == 18, "马拉维路径人口冲突")
    require(evidence["马拉维"]["移动端页面宽度"] == 390, "移动端页面宽度冲突")
    require(not evidence["马拉维"]["移动端水平溢出"], "移动端存在水平溢出")
    require(evidence["AS同窗下钻"]["页面请求状态"] == 200, "AS 同窗请求未成功")
    require(evidence["AS同窗下钻"]["五分钟时间槽数"] == 3431, "AS 同窗槽人口冲突")
    require(evidence["AS同窗下钻"]["缺失槽以空值断线"], "AS 缺失槽没有断线")
    require(evidence["AS同窗下钻"]["错误事件窗口状态"] == 400, "错误事件窗口没有失败关闭")
    require(evidence["失败关闭"]["浏览器脚本错误数"] == 0, "浏览器存在脚本错误")
    require(not evidence["失败关闭"]["中止趋势请求后回退旧页面"], "错误时回退了旧结果")
    for name, digest in evidence["截图摘要"].items():
        require(len(digest) == 64 and all(value in "0123456789abcdef" for value in digest), f"截图摘要无效：{name}")

    for phrase in (
        "候选不等于生产", "不读取窗口外等长对比基线", "3,431 个五分钟槽",
        "390 像素", "短暂停止约 37 秒", "不补提或重跑数据",
        "通用观测页最终验收回检：S5 已修正",
    ):
        require(phrase in acceptance, f"S5 验收记录缺少：{phrase}")

    payload = {
        "status": "pass",
        "stage": "S5",
        "checks": 6,
        "candidate": candidate,
        "browser_evidence_sha256": hashlib.sha256(EVIDENCE.read_bytes()).hexdigest(),
        "events": ["IR", "MW"],
        "as_window_asn": 48715,
    }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "stage": "S5", "error": str(error)}, ensure_ascii=False, separators=(",", ":")))
        raise SystemExit(1)
