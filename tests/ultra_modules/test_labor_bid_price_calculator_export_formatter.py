import unittest
import os
import csv
import math
from unittest.mock import patch, mock_open
from ultra_modules.labor_bid_price_calculator_export_formatter import (
    _clean_numeric,
    calculate_labor_bid,
    generate_brief_report,
    export_to_csv
)

class TestLaborBidPriceCalculatorExportFormatter(unittest.TestCase):
    def setUp(self):
        self.audit_log = []
        self.test_csv_path = 'test_labor_bid_export.csv'

    def tearDown(self):
        if os.path.exists(self.test_csv_path):
            os.remove(self.test_csv_path)

    def test_clean_numeric(self):
        # Normal
        self.assertEqual(_clean_numeric(10.5, 0.0, 'test', self.audit_log), 10.5)
        # None
        self.assertEqual(_clean_numeric(None, 1.0, 'test', self.audit_log), 1.0)
        # Empty string
        self.assertEqual(_clean_numeric("", 2.0, 'test', self.audit_log), 2.0)
        # NaN string
        self.assertEqual(_clean_numeric("NaN", 3.0, 'test', self.audit_log), 3.0)
        # Invalid string
        self.assertEqual(_clean_numeric("abc", 4.0, 'test', self.audit_log), 4.0)
        # Valid string
        self.assertEqual(_clean_numeric("5.5", 0.0, 'test', self.audit_log), 5.5)
        # Negative number
        self.assertEqual(_clean_numeric(-6.5, 0.0, 'test', self.audit_log), 6.5)
        # float NaN
        self.assertEqual(_clean_numeric(float('nan'), 7.0, 'test', self.audit_log), 7.0)
        # unsupported type
        self.assertEqual(_clean_numeric([], 8.0, 'test', self.audit_log), 8.0)

    def test_calculate_labor_bid_normal(self):
        params = {
            'target_total_price': 1060000,
            'headcount': 10,
            'months': 12,
            'social_security_per_person_month': 1500,
            'provident_fund_per_person_month': 500,
            'disability_fund_per_person_month': 100,
            'tax_rate': 0.06,
            'other_costs': 20000
        }
        result = calculate_labor_bid(params, self.audit_log)

        self.assertEqual(result['target_total_price'], 1060000)
        self.assertEqual(result['headcount'], 10)
        self.assertEqual(result['months'], 12.0)

        # 10 * 12 * (1500 + 500 + 100) = 120 * 2100 = 252000
        self.assertEqual(result['total_compliance_costs'], 252000.0)

        # Ex tax: 1060000 / 1.06 = 1000000
        # Taxes: 1060000 - 1000000 = 60000
        self.assertEqual(result['taxes'], 60000.0)

        # GP: 1000000 - 252000 - 20000 = 728000
        self.assertEqual(result['gross_profit'], 728000.0)
        self.assertEqual(result['gross_profit_margin'], 0.7280)

    def test_calculate_labor_bid_missing_and_dirty_data(self):
        # Missing critical params, NaN strings, negative values
        params = {
            'target_total_price': "NaN",
            'headcount': -5,  # will be converted to 5
            'months': 0,      # will default to 12.0
            'social_security_per_person_month': None,
            # provident fund missing entirely
            'disability_fund_per_person_month': -100, # converted to 100
            'tax_rate': "abc", # default 0.06
        }
        result = calculate_labor_bid(params, self.audit_log)

        self.assertEqual(result['target_total_price'], 0.0)
        self.assertEqual(result['headcount'], 5)
        self.assertEqual(result['months'], 12.0)
        self.assertEqual(result['total_social_security'], 0.0)
        self.assertEqual(result['total_provident_fund'], 0.0)
        # 5 * 12 * 100 = 6000
        self.assertEqual(result['total_disability_fund'], 6000.0)
        self.assertEqual(result['total_compliance_costs'], 6000.0)
        self.assertEqual(result['taxes'], 0.0)
        self.assertEqual(result['gross_profit'], -6000.0)
        self.assertEqual(result['gross_profit_margin'], 0.0) # revenue ex tax is 0

    def test_calculate_labor_bid_extreme_boundaries(self):
        # Zero headcount gets defaulted to 1, very large total price
        params = {
            'target_total_price': 1e12,
            'headcount': 0,
            'months': 12,
            'social_security_per_person_month': 0,
            'provident_fund_per_person_month': 0,
            'disability_fund_per_person_month': 0,
            'tax_rate': 0.0,
            'other_costs': 0
        }
        result = calculate_labor_bid(params, self.audit_log)

        self.assertEqual(result['headcount'], 1)
        self.assertEqual(result['total_compliance_costs'], 0.0)
        self.assertEqual(result['taxes'], 0.0)
        self.assertEqual(result['gross_profit'], 1e12)
        self.assertEqual(result['gross_profit_margin'], 1.0)

        # Provide no audit log array
        result2 = calculate_labor_bid(params)
        self.assertIsInstance(result2, dict)

    def test_generate_brief_report(self):
        result = {
            'target_total_price': 1060000.5,
            'gross_profit': 728000.0,
            'gross_profit_margin': 0.728,
            'total_compliance_costs': 252000.0,
            'taxes': 60000.0,
            'headcount': 10,
            'months': 12.0
        }
        report = generate_brief_report(result)
        self.assertIn("1,060,000.50", report)
        self.assertIn("728,000.00", report)
        self.assertIn("72.80%", report)
        self.assertIn("252,000.00", report)
        self.assertIn("60,000.00", report)
        self.assertIn("10人 × 12.0个月", report)

    def test_export_to_csv_success_and_sanitization(self):
        result = {
            'valid_param': 100,
            'none_param': None,
            'nan_param': float('nan'),
            'string_param': 'test'
        }
        success = export_to_csv(result, self.test_csv_path, self.audit_log)
        self.assertTrue(success)
        self.assertTrue(os.path.exists(self.test_csv_path))

        with open(self.test_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row['valid_param'], '100')
            self.assertEqual(row['none_param'], '0')
            self.assertEqual(row['nan_param'], '0')
            self.assertEqual(row['string_param'], 'test')
            self.assertEqual(row['comments'], '0') # Must force comments=0

    @patch('builtins.open', new_callable=mock_open)
    def test_export_to_csv_io_error(self, mock_file):
        mock_file.side_effect = PermissionError("Permission denied")
        result = {'test': 123}
        success = export_to_csv(result, self.test_csv_path, self.audit_log)

        self.assertFalse(success)
        self.assertTrue(any("Permission denied" in log for log in self.audit_log))

        # Test without passing audit_log explicitly
        success2 = export_to_csv(result, self.test_csv_path)
        self.assertFalse(success2)

if __name__ == '__main__':
    unittest.main()
