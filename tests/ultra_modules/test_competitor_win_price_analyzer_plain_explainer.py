import unittest
import json
from ultra_modules.competitor_win_price_analyzer_plain_explainer import analyze_win_price_plain_explainer, CompetitorWinPriceAnalyzer

class TestCompetitorWinPriceAnalyzerPlainExplainer(unittest.TestCase):

    def test_normal_data(self):
        """Test with normal standard data input."""
        data = {
            "competitor": "中通服",
            "region": "广东",
            "budget": 1000000,
            "win_price": 850000
        }
        res = analyze_win_price_plain_explainer(data)

        self.assertEqual(res["competitor"], "中通服")
        self.assertEqual(res["region"], "广东")
        self.assertEqual(res["budget"], 1000000.0)
        self.assertEqual(res["win_price"], 850000.0)
        self.assertEqual(res["discount_rate"], 0.85)
        self.assertEqual(res["discount_percentage"], 85.0)
        self.assertIn("报价处于常规区间", res["plain_conclusion"])
        self.assertIn("中通服 (广东)", res["markdown"])
        self.assertIn("850,000", res["html_card"])

    def test_dirty_data_self_healing(self):
        """Test dirty data like strings for numbers, negative values, and missing fields."""
        data = {
            "budget": " ¥ 2,000,000 元 ",
            "win_price": -1900000, # negative should be absoluted
            # region and competitor missing
        }
        res = analyze_win_price_plain_explainer(data)

        self.assertEqual(res["competitor"], "未知竞争对手")
        self.assertEqual(res["region"], "未知区域")
        self.assertEqual(res["budget"], 2000000.0)
        self.assertEqual(res["win_price"], 1900000.0)
        self.assertEqual(res["discount_rate"], 0.95)
        self.assertEqual(res["discount_percentage"], 95.0)
        self.assertIn("报价极其保守", res["plain_conclusion"])

    def test_missing_parameter_default_fallback(self):
        """Test with completely empty data dictionary (fallback to 1 million and 90%)."""
        res = analyze_win_price_plain_explainer({})

        self.assertEqual(res["competitor"], "未知竞争对手")
        self.assertEqual(res["region"], "未知区域")
        self.assertEqual(res["budget"], 1000000.0)
        self.assertEqual(res["win_price"], 900000.0)
        self.assertEqual(res["discount_rate"], 0.9)
        self.assertEqual(res["discount_percentage"], 90.0)

    def test_extreme_edge_cases(self):
        """Test extreme edge cases such as budget being 0 or very large values, and win price > budget."""
        data = {
            "competitor": "恶意竞争者",
            "region": "火星",
            "budget": 0,
            "win_price": 50000
        }
        res = analyze_win_price_plain_explainer(data)

        # If budget is 0, it falls back to 1000000.0,
        # Then win_price is 50000, discount rate = 0.05
        self.assertEqual(res["budget"], 1000000.0)
        self.assertEqual(res["win_price"], 50000.0)
        self.assertEqual(res["discount_rate"], 0.05)
        self.assertIn("报价极其激进，属于恶意低价", res["plain_conclusion"])

        # Swap scenario
        data_swap = {
            "budget": 100000,
            "win_price": 200000
        }
        res_swap = analyze_win_price_plain_explainer(data_swap)
        # Should swap budget and win_price
        self.assertEqual(res_swap["budget"], 200000.0)
        self.assertEqual(res_swap["win_price"], 100000.0)
        self.assertEqual(res_swap["discount_rate"], 0.5)

    def test_missing_budget_missing_win_price(self):
        """Test None values explicitly."""
        data = {
            "budget": None,
            "win_price": None
        }
        res = analyze_win_price_plain_explainer(data)

        self.assertEqual(res["budget"], 1000000.0)
        self.assertEqual(res["win_price"], 900000.0)

    def test_wan_character(self):
        """Test '万' character parsing."""
        data = {
            "budget": "100万",
            "win_price": "80万"
        }
        res = analyze_win_price_plain_explainer(data)

        self.assertEqual(res["budget"], 1000000.0)
        self.assertEqual(res["win_price"], 800000.0)
        self.assertEqual(res["discount_rate"], 0.8)

if __name__ == '__main__':
    unittest.main()
