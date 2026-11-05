import unittest
import json
from ultra_modules.one_page_go_nogo_card_plain_explainer import GoNoGoDecisionCard, generate_decision_card

class TestGoNoGoDecisionCard(unittest.TestCase):
    def test_normal_case_go(self):
        """测试正常情况下的'可投'判定"""
        raw_data = {
            "budget": 2000000,
            "tech_match": 85.5,
            "competitors": 2,
            "delivery_days": 60,
            "has_required_quals": True
        }
        card = GoNoGoDecisionCard(raw_data)
        result = card.evaluate()
        self.assertEqual(result["decision"], "可投")
        self.assertTrue("综合评估极佳" in result["reasons"][0])

    def test_dirty_data_self_healing(self):
        """测试脏数据自愈（带单位、逗号、奇怪字符的字符串）"""
        raw_data = {
            "budget": "约1,234,567.89元", # 应解析为 1234567.89
            "tech_match": "大概75.5%吧",  # 应解析为 75.5
            "competitors": "预测3家左右",  # 应解析为 3
            "delivery_days": "可能45天",    # 应解析为 45
            "has_required_quals": "是"      # 应解析为 True
        }
        card = GoNoGoDecisionCard(raw_data)
        result = card.evaluate()
        metrics = result["parsed_metrics"]
        self.assertEqual(metrics["budget"], 1234567.89)
        self.assertEqual(metrics["tech_match"], 75.5)
        self.assertEqual(metrics["competitors"], 3)
        self.assertEqual(metrics["delivery_days"], 45)
        self.assertTrue(metrics["has_required_quals"])
        self.assertEqual(result["decision"], "可投")

    def test_missing_params_fallback(self):
        """测试缺参时的默认兜底（一键直达、零配置容错）"""
        # 完全不传参数
        result = json.loads(generate_decision_card())
        metrics = result["parsed_metrics"]
        self.assertEqual(metrics["budget"], 0.0)
        self.assertEqual(metrics["tech_match"], 50.0) # 默认 50
        self.assertEqual(metrics["competitors"], 3)   # 默认 3
        self.assertEqual(metrics["delivery_days"], 30) # 默认 30
        self.assertTrue(metrics["has_required_quals"]) # 默认 True
        self.assertEqual(result["decision"], "禁投")

    def test_extreme_boundary_reject(self):
        """测试极端边界条件（禁投）"""
        # 预算太低
        card1 = GoNoGoDecisionCard({"budget": 100, "has_required_quals": True})
        self.assertEqual(card1.evaluate()["decision"], "禁投")

        # 工期太赶
        card2 = GoNoGoDecisionCard({"delivery_days": 3, "has_required_quals": True})
        self.assertEqual(card2.evaluate()["decision"], "禁投")

        # 资质不符
        card3 = GoNoGoDecisionCard({"has_required_quals": False})
        self.assertEqual(card3.evaluate()["decision"], "禁投")

    def test_export_formats(self):
        """测试前端大白话一页纸及各种导出格式"""
        raw_data = {
            "budget": "500万", # 5000000
            "tech_match": 90,
            "competitors": 1,
            "delivery_days": 90,
            "has_required_quals": "True"
        }

        json_output = generate_decision_card(raw_data, "json")
        self.assertTrue("可投" in json_output)

        md_output = generate_decision_card(raw_data, "markdown")
        self.assertTrue("# 投标决策卡: 可投" in md_output)
        self.assertTrue("5,000,000.00元" in md_output)

        html_output = generate_decision_card(raw_data, "html")
        self.assertTrue("投标决策: 可投" in html_output)
        self.assertTrue("5,000,000.00元" in html_output)

if __name__ == '__main__':
    unittest.main()
