import unittest
import os
import json
from ultra_modules.labor_bid_price_calculator_cli_one_click import (
    LaborBidPriceCalculator,
    export_json,
    export_excel
)

class TestLaborBidPriceCalculatorCLI(unittest.TestCase):
    def setUp(self):
        self.csv_filename = 'test_labor_bid_price.csv'

    def tearDown(self):
        if os.path.exists(self.csv_filename):
            os.remove(self.csv_filename)

    def test_normal_execution(self):
        """Test with valid standard parameters."""
        calc = LaborBidPriceCalculator(
            base_salary=6000.0,
            social_security_rate=0.25,
            provident_fund_rate=0.10,
            disability_fund_rate=0.02,
            tax_rate=0.05,
            profit_margin=0.05
        )
        result = calc.calculate()

        # Expected total cost: 6000 + 1500 + 600 + 120 = 8220
        # Expected denominator: 1 - 0.05 - 0.05 = 0.90
        # Expected bid price: 8220 / 0.9 = 9133.33
        self.assertEqual(result['base_salary'], 6000.0)
        self.assertEqual(result['social_security_cost'], 1500.0)
        self.assertEqual(result['provident_fund_cost'], 600.0)
        self.assertEqual(result['disability_fund_cost'], 120.0)
        self.assertEqual(result['total_cost'], 8220.0)
        self.assertAlmostEqual(result['bid_price'], 9133.33, places=2)

    def test_dirty_data_self_healing(self):
        """Test with negative values and strings (should heal to positive floats or defaults)."""
        calc = LaborBidPriceCalculator(
            base_salary="-4000.5",  # String and negative
            social_security_rate="invalid",  # Invalid string, should use default
            tax_rate=-0.08  # Negative float
        )
        result = calc.calculate()
        self.assertEqual(result['base_salary'], 4000.5)
        self.assertEqual(result['social_security_cost'], round(4000.5 * LaborBidPriceCalculator.DEFAULT_SOCIAL_SECURITY_RATE, 2))
        self.assertEqual(result['tax_rate'], 0.08)

    def test_missing_parameter_fallback(self):
        """Test execution with no parameters (should use defaults)."""
        calc = LaborBidPriceCalculator()
        result = calc.calculate()

        self.assertEqual(result['base_salary'], 5000.0)
        self.assertEqual(result['tax_rate'], 0.06)
        self.assertEqual(result['profit_margin'], 0.05)

        expected_total_cost = 5000.0 + (5000 * 0.28) + (5000 * 0.12) + (5000 * 0.015)
        self.assertEqual(result['total_cost'], expected_total_cost)

    def test_extreme_boundaries(self):
        """Test with extreme boundaries like margins summing to >= 1.0 (should heal)."""
        calc = LaborBidPriceCalculator(tax_rate=0.5, profit_margin=0.6)
        result = calc.calculate()

        # Since 0.5 + 0.6 >= 0.99, it should fallback to defaults
        self.assertEqual(result['tax_rate'], LaborBidPriceCalculator.DEFAULT_TAX_RATE)
        self.assertEqual(result['profit_margin'], LaborBidPriceCalculator.DEFAULT_PROFIT_MARGIN)

    def test_export_adapters(self):
        """Test output to JSON and CSV/Excel."""
        calc = LaborBidPriceCalculator()
        result = calc.calculate()

        # Test JSON
        json_output = export_json(result)
        data = json.loads(json_output)
        self.assertEqual(data['base_salary'], 5000.0)

        # Test CSV Export
        exported_file = export_excel(result, filename=self.csv_filename)
        self.assertTrue(os.path.exists(exported_file))

        with open(exported_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            self.assertGreater(len(lines), 1)
            self.assertIn("base_salary,5000.0", "".join(lines))

if __name__ == '__main__':
    unittest.main()
