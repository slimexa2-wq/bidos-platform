import unittest
import math
from ultra_modules.labor_bid_price_calculator_export_formatter import (
    sanitize_value,
    format_export_data,
    generate_export_csv_string,
    generate_minimal_report,
    export_and_report
)

class TestLaborBidPriceCalculatorExportFormatter(unittest.TestCase):

    def test_normal_case(self):
        # Setup typical parameters
        params = {
            'base_salary': 5000,
            'bonus': 1000,
            'social_security_rate': 20,
            'target_margin_rate': 10,
            'tax_rate': 5
        }
        result = export_and_report(params)
        clean_data = result['clean_data']
        self.assertEqual(clean_data['total_salary'], 6000.0)
        self.assertIn("【报价测算结果】投标总价", result['report'])
        self.assertIn("total_salary,actual_ss_base", result['csv_export'])

    def test_missing_params(self):
        # Test with None parameters (missing params fallback)
        result = export_and_report(None)
        clean_data = result['clean_data']
        self.assertEqual(clean_data['total_salary'], 0.0)
        self.assertEqual(clean_data['calculated_bid_price'], 0.0)

    def test_dirty_data_self_healing(self):
        # 1. Test None value
        self.assertEqual(sanitize_value(None), 0.0)

        # 2. Test NaN value (float)
        self.assertEqual(sanitize_value(float('nan')), 0.0)

        # 3. Test string with commas and percents
        self.assertEqual(sanitize_value("1,000.50"), 1000.50)
        self.assertEqual(sanitize_value("15%"), 15.0)

        # 4. Test invalid string fallback to 0.0
        self.assertEqual(sanitize_value("invalid_str"), 0.0)
        self.assertEqual(sanitize_value("nan"), 0.0)

        # 5. Test unsupported type fallback to 0.0
        self.assertEqual(sanitize_value([]), 0.0)

        # Test format_export_data with malformed raw_data
        clean = format_export_data("not a dict")
        self.assertEqual(clean['total_salary'], 0.0)

        # Test format_export_data with dict containing None and NaN
        raw = {'total_salary': None, 'margin': float('nan'), 'missing': 123}
        clean2 = format_export_data(raw)
        self.assertEqual(clean2['total_salary'], 0.0)
        self.assertEqual(clean2['margin'], 0.0)

    def test_extreme_boundaries(self):
        # Massive numbers and edge case zeros
        params = {
            'base_salary': 1e9,
            'target_margin_rate': 0,
            'tax_rate': 0
        }
        result = export_and_report(params)
        self.assertEqual(result['clean_data']['calculated_bid_price'], 1e9)
        self.assertIn("投标总价：1000000000.00元", result['report'])

    def test_report_and_csv_generation(self):
        clean_data = {
            'total_salary': 5000.0,
            'actual_ss_base': 5000.0,
            'social_security_cost': 1000.0,
            'actual_pf_base': 5000.0,
            'provident_fund_cost': 500.0,
            'disabled_fund_cost': 100.0,
            'total_cost_before_tax': 6600.0,
            'tax': 330.0,
            'total_cost': 6930.0,
            'margin': 693.0,
            'margin_rate': 0.10,
            'calculated_bid_price': 7623.0
        }

        report = generate_minimal_report(clean_data)
        lines = report.split('\n')
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "【报价测算结果】投标总价：7623.00元")
        self.assertEqual(lines[1], "【成本明细】总成本（含税费）：6930.00元")
        self.assertEqual(lines[2], "【收益预估】毛利：693.00元（毛利率：10.00%）")

        csv_str = generate_export_csv_string(clean_data)
        self.assertIn("total_salary,actual_ss_base,social_security_cost,actual_pf_base,provident_fund_cost,disabled_fund_cost,total_cost_before_tax,tax,total_cost,margin,margin_rate,calculated_bid_price", csv_str)
        self.assertIn("5000.0,5000.0,1000.0,5000.0,500.0,100.0,6600.0,330.0,6930.0,693.0,0.1,7623.0", csv_str)

if __name__ == '__main__':
    unittest.main()
