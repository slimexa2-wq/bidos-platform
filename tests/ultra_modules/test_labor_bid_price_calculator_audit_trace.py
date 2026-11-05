import unittest
from ultra_modules.labor_bid_price_calculator_audit_trace import LaborBidPriceCalculatorAuditTrace

class TestLaborBidPriceCalculatorAuditTrace(unittest.TestCase):
    def setUp(self):
        self.calculator = LaborBidPriceCalculatorAuditTrace()

    def test_normal_behavior(self):
        # Normal inputs
        total_ssf = self.calculator.calculate_social_security_provident_fund(6000.0, 0.25, 0.12)
        self.assertAlmostEqual(total_ssf, 6000.0 * 0.25 + 6000.0 * 0.12)

        total_df = self.calculator.calculate_disability_fund(10, 6000.0, 0.015)
        self.assertAlmostEqual(total_df, 10 * 6000.0 * 0.015)

        total_taxes = self.calculator.calculate_taxes(100000.0, 0.06)
        self.assertAlmostEqual(total_taxes, 100000.0 * 0.06)

        gross_profit = self.calculator.backward_calculate_gross_profit(100000.0, 80000.0)
        self.assertAlmostEqual(gross_profit, 20000.0)

        results = self.calculator.get_clean_result()
        self.assertIn("social_security_provident_fund", results)
        self.assertIn("disability_fund", results)
        self.assertIn("taxes", results)
        self.assertIn("gross_profit", results)

    def test_dirty_data_self_healing(self):
        # Strings and percentages
        total_ssf = self.calculator.calculate_social_security_provident_fund("6000", "25%", "12%")
        self.assertAlmostEqual(total_ssf, 6000.0 * 0.25 + 6000.0 * 0.12)

        # Invalid strings
        total_df = self.calculator.calculate_disability_fund("ten", "6000", "invalid%")
        # Should fallback to defaults: headcount=1.0, base=6000.0, rate=0.015
        self.assertAlmostEqual(total_df, 1.0 * 6000.0 * 0.015)

        # Unrecognized types
        total_taxes = self.calculator.calculate_taxes(object(), [0.06])
        # Should fallback to defaults: revenue=10000.0, rate=0.06
        self.assertAlmostEqual(total_taxes, 10000.0 * 0.06)

    def test_missing_parameter_fallbacks(self):
        # None values
        total_ssf = self.calculator.calculate_social_security_provident_fund(None, None, None)
        # Should fallback to defaults: base=5000.0, rate_social=0.2, rate_fund=0.05
        self.assertAlmostEqual(total_ssf, 5000.0 * 0.2 + 5000.0 * 0.05)

        total_df = self.calculator.calculate_disability_fund(None, None, None)
        # Should fallback to defaults: headcount=1.0, base=5000.0, rate=0.015
        self.assertAlmostEqual(total_df, 1.0 * 5000.0 * 0.015)

        total_taxes = self.calculator.calculate_taxes(None, None)
        # Should fallback to defaults: revenue=10000.0, rate=0.06
        self.assertAlmostEqual(total_taxes, 10000.0 * 0.06)

        gross_profit = self.calculator.backward_calculate_gross_profit(None, None)
        # Should fallback to defaults: target_revenue=10000.0, costs=8000.0
        self.assertAlmostEqual(gross_profit, 2000.0)

    def test_extreme_boundary_conditions(self):
        # Negative values should be floored to 0.0
        total_ssf = self.calculator.calculate_social_security_provident_fund(-1000, -0.1, -0.1)
        self.assertAlmostEqual(total_ssf, 0.0)

        total_df = self.calculator.calculate_disability_fund(-5, -1000, -0.01)
        self.assertAlmostEqual(total_df, 0.0)

        total_taxes = self.calculator.calculate_taxes(-10000, -0.05)
        self.assertAlmostEqual(total_taxes, 0.0)

        gross_profit = self.calculator.backward_calculate_gross_profit(-10000, -5000)
        self.assertAlmostEqual(gross_profit, 0.0)
        results = self.calculator.get_clean_result()
        self.assertAlmostEqual(results["gross_profit_margin"], 0.0)

    def test_audit_trace(self):
        self.calculator.calculate_social_security_provident_fund(None, "invalid%", -0.1)
        logs = self.calculator.get_audit_logs()

        self.assertTrue(len(logs) > 0)

        # Check that we have a log for sanitize_input for the None value
        sanitize_none_logs = [log for log in logs if log["step"] == "sanitize_input" and "Value is None" in log["details"]]
        self.assertTrue(len(sanitize_none_logs) > 0)

        # Check that we have a log for sanitize_input for the invalid percentage
        sanitize_invalid_logs = [log for log in logs if log["step"] == "sanitize_input" and "Failed to parse percentage string" in log["details"]]
        self.assertTrue(len(sanitize_invalid_logs) > 0)

        # Check that we have a log for calculate_social_security_provident_fund
        calc_logs = [log for log in logs if log["step"] == "calculate_social_security_provident_fund"]
        self.assertEqual(len(calc_logs), 1)
        self.assertIn("base", calc_logs[0]["details"])
        self.assertIn("rate_social", calc_logs[0]["details"])
        self.assertIn("rate_fund", calc_logs[0]["details"])

if __name__ == '__main__':
    unittest.main()
