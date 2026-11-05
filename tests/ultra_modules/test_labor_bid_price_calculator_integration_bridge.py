import unittest
from ultra_modules.labor_bid_price_calculator_integration_bridge import calculate_bid_price

class TestLaborBidPriceCalculator(unittest.TestCase):

    def test_normal_case_standard_fields(self):
        input_data = {
            "base_salary": 4000.0,
            "social_security_rate": 0.25,
            "provident_fund_rate": 0.1,
            "disability_fund": 50.0,
            "tax_rate": 0.06,
            "profit_margin": 0.04
        }
        result = calculate_bid_price(input_data)

        # total_labor_cost = 4000 + 4000*0.25 + 4000*0.1 + 50 = 4000 + 1000 + 400 + 50 = 5450
        # denominator = 1 - 0.06 - 0.04 = 0.9
        # bid_price = 5450 / 0.9 = 6055.555...

        self.assertEqual(result["status"], "success")
        self.assertAlmostEqual(result["calculated_price"], 6055.56, places=2)
        self.assertEqual(result["breakdown"]["total_labor_cost"], 5450.0)
        self.assertEqual(len(result["audit_trace"]), 0) # No mapping, missing or dirty fields

    def test_normal_case_alias_mapping(self):
        input_data = {
            "salary_base": 5000.0,
            "ss_rate": 0.3,
            "housing_fund_ratio": 0.12,
            "canbaojin": 60.0,
            "shuifei_rate": 0.06,
            "target_margin": 0.05
        }
        result = calculate_bid_price(input_data)

        # labor_cost = 5000 + 1500 + 600 + 60 = 7160
        # den = 1 - 0.06 - 0.05 = 0.89
        # bid_price = 7160 / 0.89 = 8044.94
        self.assertEqual(result["status"], "success")
        self.assertAlmostEqual(result["calculated_price"], 8044.94, places=2)

        # Check audit trace for mapping
        self.assertTrue(any("Mapped field 'salary_base' to 'base_salary'" in msg for msg in result["audit_trace"]))

    def test_dirty_data_auto_healing(self):
        input_data = {
            "base_salary": "invalid_string", # Should fallback to default 3000
            "social_security_rate": -0.5,    # Negative value, should fallback to default 0.28
            "provident_fund_rate": 0.12,
            "disability_fund": 60.0,
            "tax_rate": 0.06,
            "profit_margin": 0.05
        }
        result = calculate_bid_price(input_data)

        # Defaults used: base_salary = 3000, social_security_rate = 0.28
        # labor_cost = 3000 + 3000*0.28 + 3000*0.12 + 60 = 3000 + 840 + 360 + 60 = 4260
        # den = 1 - 0.06 - 0.05 = 0.89
        # bid_price = 4260 / 0.89 = 4786.516...

        self.assertEqual(result["status"], "success")
        self.assertAlmostEqual(result["calculated_price"], 4786.52, places=2)

        # Check audit trace
        self.assertTrue(any("Dirty data found in field 'base_salary'" in msg for msg in result["audit_trace"]))
        self.assertTrue(any("Dirty data found in field 'social_security_rate'" in msg for msg in result["audit_trace"]))
        self.assertTrue(any("Defaulting to 3000.0" in msg for msg in result["audit_trace"]))

    def test_missing_parameters_fallback(self):
        input_data = {
            "base_salary": 4000.0
            # missing all other fields
        }
        result = calculate_bid_price(input_data)

        # Defaults: ss=0.28, pf=0.12, df=60, tax=0.06, profit=0.05
        # labor_cost = 4000 + 4000*0.28 + 4000*0.12 + 60 = 4000 + 1120 + 480 + 60 = 5660
        # den = 1 - 0.06 - 0.05 = 0.89
        # bid_price = 5660 / 0.89 = 6359.55...

        self.assertEqual(result["status"], "success")
        self.assertAlmostEqual(result["calculated_price"], 6359.55, places=2)

        # Check audit trace
        self.assertTrue(any("Missing parameter for 'tax_rate'" in msg for msg in result["audit_trace"]))

    def test_extreme_boundary(self):
        input_data = {
            "base_salary": 3000.0,
            "social_security_rate": 0.28,
            "provident_fund_rate": 0.12,
            "disability_fund": 60.0,
            "tax_rate": 0.06,
            "profit_margin": 1.2 # Extreme boundary
        }
        result = calculate_bid_price(input_data)

        # Expect adjustment: profit_margin = max(0.0, 0.9 - 0.06) = 0.84
        # den = 1 - 0.06 - 0.84 = 0.1
        # labor_cost = 3000 + 840 + 360 + 60 = 4260
        # bid_price = 4260 / 0.1 = 42600

        self.assertEqual(result["status"], "success")
        self.assertAlmostEqual(result["calculated_price"], 42600.0, places=2)

        # Check audit trace
        self.assertTrue(any("Extreme boundary detected" in msg for msg in result["audit_trace"]))

    def test_extreme_boundary_tax_invalid(self):
        input_data = {
            "base_salary": 3000.0,
            "social_security_rate": 0.28,
            "provident_fund_rate": 0.12,
            "disability_fund": 60.0,
            "tax_rate": 1.5,
            "profit_margin": 0.05
        }
        result = calculate_bid_price(input_data)

        # Expect adjustment: tax_rate = 0.06, profit_margin = 0.05
        # den = 1 - 0.06 - 0.05 = 0.89
        # labor_cost = 4260
        # bid_price = 4260 / 0.89 = 4786.516...

        self.assertEqual(result["status"], "success")
        self.assertAlmostEqual(result["calculated_price"], 4786.52, places=2)
        self.assertTrue(any("Tax rate completely invalid, reset to defaults" in msg for msg in result["audit_trace"]))

if __name__ == '__main__':
    unittest.main()
