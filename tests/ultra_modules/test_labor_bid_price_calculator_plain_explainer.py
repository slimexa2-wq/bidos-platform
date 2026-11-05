import unittest
import json
from ultra_modules.labor_bid_price_calculator_plain_explainer import LaborBidPricePlainExplainer

class TestLaborBidPricePlainExplainer(unittest.TestCase):

    def test_normal_case_profit(self):
        """Test with normal data resulting in profit."""
        data = {
            "base_salary": 4000.0,
            "social_security_base": 5000.0,
            "social_security_rate": 0.35,
            "disability_fund_rate": 0.015,
            "tax_rate": 0.06,
            "bid_price": 7000.0
        }
        explainer = LaborBidPricePlainExplainer(data)
        self.assertFalse(explainer.results["is_loss"])

        # 4000 + (5000*0.35) + (5000*0.015) + (7000*0.06) = 4000 + 1750 + 75 + 420 = 6245
        self.assertEqual(explainer.results["total_cost"], 6245.0)
        self.assertEqual(explainer.results["gross_profit"], 755.0)
        self.assertIn("✅ **盈利正常**", explainer.generate_markdown())
        self.assertIn("盈利正常", explainer.generate_html_card())

    def test_normal_case_loss(self):
        """Test with normal data resulting in a loss."""
        data = {
            "base_salary": 4000.0,
            "social_security_base": 5000.0,
            "social_security_rate": 0.35,
            "disability_fund_rate": 0.015,
            "tax_rate": 0.06,
            "bid_price": 6000.0
        }
        explainer = LaborBidPricePlainExplainer(data)
        self.assertTrue(explainer.results["is_loss"])

        # 4000 + (5000*0.35) + (5000*0.015) + (6000*0.06) = 4000 + 1750 + 75 + 360 = 6185
        self.assertEqual(explainer.results["total_cost"], 6185.0)
        self.assertEqual(explainer.results["gross_profit"], -185.0)
        self.assertIn("⚠️ **亏本警报**", explainer.generate_markdown())
        self.assertIn("亏损警告", explainer.generate_html_card())

    def test_missing_parameters_fallback(self):
        """Test with missing parameters to ensure sensible defaults are applied."""
        explainer = LaborBidPricePlainExplainer({})
        self.assertFalse(explainer.results["is_loss"])
        self.assertEqual(explainer.results["base_salary"], 3000.0)
        self.assertEqual(explainer.results["social_security_cost"], 1400.0) # 4000 * 0.35
        self.assertEqual(explainer.results["disability_fund_cost"], 60.0) # 4000 * 0.015
        self.assertEqual(explainer.results["tax_cost"], 300.0) # 5000 * 0.06
        self.assertEqual(explainer.results["total_cost"], 4760.0)
        self.assertEqual(explainer.results["gross_profit"], 240.0)

    def test_dirty_data_self_healing(self):
        """Test with dirty data (strings with commas, invalid strings)."""
        data = {
            "base_salary": "3,500.50",
            "social_security_base": "  4,500 ",
            "social_security_rate": "invalid", # should fallback to default 0.35
            "disability_fund_rate": "0.02",
            "tax_rate": None, # should fallback to default 0.06
            "bid_price": " 6,000"
        }
        explainer = LaborBidPricePlainExplainer(data)
        self.assertEqual(explainer.results["base_salary"], 3500.5)
        self.assertEqual(explainer.results["social_security_cost"], 1575.0) # 4500 * 0.35
        self.assertEqual(explainer.results["disability_fund_cost"], 90.0) # 4500 * 0.02
        self.assertEqual(explainer.results["tax_cost"], 360.0) # 6000 * 0.06
        self.assertEqual(explainer.results["total_cost"], 5525.5)
        self.assertEqual(explainer.results["gross_profit"], 474.5)

    def test_extreme_boundaries(self):
        """Test with zeroes to ensure no divide by zero errors."""
        data = {
            "base_salary": 0.0,
            "social_security_base": 0.0,
            "social_security_rate": 0.0,
            "disability_fund_rate": 0.0,
            "tax_rate": 0.0,
            "bid_price": 0.0
        }
        explainer = LaborBidPricePlainExplainer(data)
        self.assertEqual(explainer.results["total_cost"], 0.0)
        self.assertEqual(explainer.results["gross_profit"], 0.0)
        self.assertEqual(explainer.results["profit_margin_percent"], 0.0)
        self.assertFalse(explainer.results["is_loss"])

    def test_json_generation(self):
        """Test that JSON generation returns a valid JSON string."""
        explainer = LaborBidPricePlainExplainer({})
        json_str = explainer.generate_json()
        parsed_json = json.loads(json_str)
        self.assertIn("base_salary", parsed_json)
        self.assertIn("total_cost", parsed_json)
        self.assertEqual(parsed_json["base_salary"], 3000.0)

if __name__ == '__main__':
    unittest.main()
