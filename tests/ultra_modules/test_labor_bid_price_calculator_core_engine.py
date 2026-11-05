import unittest
from ultra_modules.labor_bid_price_calculator_core_engine import calculate_labor_bid, safe_float

class TestLaborBidPriceCalculator(unittest.TestCase):

    def test_safe_float_dirty_data(self):
        self.assertEqual(safe_float("1,000.50"), 1000.50)
        self.assertEqual(safe_float("15%"), 15.0)
        self.assertEqual(safe_float(None, 10.0), 10.0)
        self.assertEqual(safe_float("invalid", 5.0), 5.0)
        self.assertEqual(safe_float(100.5), 100.5)

    def test_normal_forward_calculation(self):
        params = {
            'base_salary': 5000,
            'bonus': 1000,
            'allowance': 500,

            'social_security_base': 6500,
            'min_social_security_base': 3000,
            'social_security_rate': 20,

            'provident_fund_base': 6500,
            'min_provident_fund_base': 2500,
            'provident_fund_rate': 12,

            'disabled_fund_base': 6500,
            'disabled_fund_rate': 1.5,

            'management_fee': 200,
            'target_margin_rate': 10,
            'tax_rate': 5
        }
        result = calculate_labor_bid(params)

        self.assertEqual(result['total_salary'], 6500.0)
        self.assertEqual(result['actual_ss_base'], 6500.0)
        self.assertEqual(result['social_security_cost'], 1300.0) # 6500 * 0.2
        self.assertEqual(result['actual_pf_base'], 6500.0)
        self.assertEqual(result['provident_fund_cost'], 780.0) # 6500 * 0.12
        self.assertEqual(result['disabled_fund_cost'], 97.5) # 6500 * 0.015

        # total_cost_before_tax = 6500 + 1300 + 780 + 97.5 + 200 = 8877.5
        self.assertEqual(result['total_cost_before_tax'], 8877.5)

        # calculated_bid_price = 8877.5 / (1 - 0.10 - 0.05) = 8877.5 / 0.85 = 10444.1176...
        self.assertAlmostEqual(result['calculated_bid_price'], 10444.12, places=2)

        # tax = 10444.1176... * 0.05 = 522.2058...
        self.assertAlmostEqual(result['tax'], 522.21, places=2)

        # margin = 10444.1176... * 0.10 = 1044.4117...
        self.assertAlmostEqual(result['margin'], 1044.41, places=2)

    def test_compliance_baseline_adjustment(self):
        params = {
            'base_salary': 3000,

            'social_security_base': 3000,
            'min_social_security_base': 4000, # minimum is higher than salary
            'social_security_rate': 10,

            'provident_fund_base': 2000,
            'min_provident_fund_base': 3500, # minimum is higher than base
            'provident_fund_rate': 5,
        }
        result = calculate_labor_bid(params)

        self.assertEqual(result['actual_ss_base'], 4000.0)
        self.assertEqual(result['social_security_cost'], 400.0) # 4000 * 0.1

        self.assertEqual(result['actual_pf_base'], 3500.0)
        self.assertEqual(result['provident_fund_cost'], 175.0) # 3500 * 0.05

    def test_backward_margin_deduction(self):
        params = {
            'base_salary': 4000,
            'social_security_rate': 10, # SS base defaults to total salary (4000), cost = 400
            'provided_bid_price': 6000,
            'tax_rate': 5
        }
        result = calculate_labor_bid(params)

        # total_cost_before_tax = 4000 + 400 = 4400
        self.assertEqual(result['total_cost_before_tax'], 4400.0)

        # tax = 6000 * 0.05 = 300
        self.assertEqual(result['tax'], 300.0)
        self.assertEqual(result['total_cost'], 4700.0)

        # margin = 6000 - 4700 = 1300
        self.assertEqual(result['margin'], 1300.0)
        self.assertAlmostEqual(result['margin_rate'], 1300.0/6000.0, places=4)

    def test_missing_parameter_defaults(self):
        result = calculate_labor_bid(None)

        self.assertEqual(result['total_salary'], 0.0)
        self.assertEqual(result['total_cost_before_tax'], 0.0)
        self.assertEqual(result['calculated_bid_price'], 0.0)
        self.assertEqual(result['margin'], 0.0)

    def test_dirty_data_self_healing(self):
        params = {
            'base_salary': "5,000",
            'bonus': "N/A",
            'social_security_rate': "15%",
            'target_margin_rate': "10%",
            'tax_rate': "invalid data"
        }
        result = calculate_labor_bid(params)

        self.assertEqual(result['total_salary'], 5000.0)
        self.assertEqual(result['social_security_cost'], 750.0) # 5000 * 0.15
        self.assertEqual(result['tax'], 0.0)

        # cost before tax = 5000 + 750 = 5750
        # price = 5750 / (1 - 0.10) = 6388.888...
        self.assertAlmostEqual(result['calculated_bid_price'], 6388.89, places=2)

    def test_extreme_boundary(self):
        # target margin + tax >= 1.0 (impossible)
        params = {
            'base_salary': 1000,
            'target_margin_rate': 60,
            'tax_rate': 50
        }
        result = calculate_labor_bid(params)

        # Since denominator 1 - 1.1 = -0.1 <= 0, fallback price = cost
        self.assertEqual(result['calculated_bid_price'], 1000.0)
        self.assertEqual(result['tax'], 500.0)
        self.assertEqual(result['margin'], 600.0)

if __name__ == '__main__':
    unittest.main()
