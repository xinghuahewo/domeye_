from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "frontend/src/components/CountryOutageGeneralPage.vue"
ASN_SERVICE = ROOT / "backend/services/asn_service.py"


class CountryOutageGeneralizationS5Test(unittest.TestCase):
    def test_page_is_user_ordered_and_has_no_internal_release_copy(self) -> None:
        source = COMPONENT.read_text(encoding="utf-8")
        template = source[source.index("<template>"):source.index("</template>")]
        headings = [
            "前缀中断数量变化",
            "AS 中断数量变化",
            "IP 地址变化趋势",
            "哪些 AS 出现了路由不可见",
            "实际路径中关联了哪些网络",
        ]
        positions = [template.index(value) for value in headings]
        self.assertEqual(positions, sorted(positions))
        for forbidden in (
            "PRODUCT", "PUBLICATION", "REVISION", "DATA THROUGH",
            "Prefix×VP", "同一冻结制品", "incident_go_",
        ):
            self.assertNotIn(forbidden, template)

    def test_event_window_is_identity_bound_and_keeps_ordinary_24h_limit(self) -> None:
        source = ASN_SERVICE.read_text(encoding="utf-8")
        self.assertIn("days=45 if event_window else 1", source)
        self.assertIn("ASN 工作台最多支持 24 小时窗口", source)
        self.assertIn("country_outage_general_read_model().resolve(reference)", source)
        self.assertIn("请求范围与国家中断事件窗口不一致", source)
        self.assertIn("previous_start = start if event_window", source)

if __name__ == "__main__":
    unittest.main()
