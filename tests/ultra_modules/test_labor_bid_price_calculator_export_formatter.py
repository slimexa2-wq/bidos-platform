import unittest
import math
from ultra_modules.labor_bid_price_calculator_export_formatter import format_export

class TestLaborBidPriceExportFormatter(unittest.TestCase):

    def test_normal_data_formatting(self):
        """Test Case 1: Normal data formats correctly with all required fields."""
        data = {
            'calculated_bid_price': 10500.0,
            'total_cost': 9000.0,
            'margin': 1500.0,
            'margin_rate': 0.1428,
            'some_extra_field': 123.45
        }

        result = format_export(data)

        self.assertEqual(result['comments'], 0)
        self.assertEqual(result['calculated_bid_price'], 10500.0)
        self.assertEqual(result['total_cost'], 9000.0)
        self.assertEqual(result['margin'], 1500.0)
        self.assertEqual(result['some_extra_field'], 123.45)

        expected_report = (
            "【测算简报】单人综合报价：¥10,500.00\n"
            "▶ 刚性成本：¥9,000.00 (含工资/社保/公积金/残保/税)\n"
            "▶ 预期毛利：¥1,500.00 (毛利率 14.3%)"
        )
        self.assertEqual(result['report_text'], expected_report)

    def test_dirty_data_self_healing(self):
        """Test Case 2: Dirty data is cleaned correctly."""
        data = {
            'calculated_bid_price': "10,500.0",
            'total_cost': "9000",
            'margin': "1,500.0",
            'margin_rate': "14.28%",
            'invalid_str': "not a number"
        }

        result = format_export(data)

        self.assertEqual(result['comments'], 0)
        self.assertEqual(result['calculated_bid_price'], 10500.0)
        self.assertEqual(result['total_cost'], 9000.0)
        self.assertEqual(result['margin'], 1500.0)
        self.assertEqual(result['margin_rate'], 14.28) # 14.28% becomes 14.28
        self.assertEqual(result['invalid_str'], 0.0)

    def test_missing_params_fallback(self):
        """Test Case 3: Missing parameters fallback to 0.0 correctly."""
        result = format_export(None)

        self.assertEqual(result['comments'], 0)
        self.assertEqual(result['calculated_bid_price'], 0.0)
        self.assertEqual(result['total_cost'], 0.0)
        self.assertEqual(result['margin'], 0.0)
        self.assertEqual(result['margin_rate'], 0.0)

        expected_report = (
            "【测算简报】单人综合报价：¥0.00\n"
            "▶ 刚性成本：¥0.00 (含工资/社保/公积金/残保/税)\n"
            "▶ 预期毛利：¥0.00 (毛利率 0.0%)"
        )
        self.assertEqual(result['report_text'], expected_report)

    def test_extreme_boundary_formatting(self):
        """Test Case 4: Extreme boundaries (e.g. very large numbers or zeros)."""
        data = {
            'calculated_bid_price': 9999999.99,
            'total_cost': 0.0,
            'margin': 9999999.99,
            'margin_rate': 1.0
        }

        result = format_export(data)

        self.assertEqual(result['comments'], 0)
        self.assertEqual(result['calculated_bid_price'], 9999999.99)
        self.assertEqual(result['total_cost'], 0.0)
        self.assertEqual(result['margin'], 9999999.99)
        self.assertEqual(result['margin_rate'], 1.0)

        expected_report = (
            "【测算简报】单人综合报价：¥9,999,999.99\n"
            "▶ 刚性成本：¥0.00 (含工资/社保/公积金/残保/税)\n"
            "▶ 预期毛利：¥9,999,999.99 (毛利率 100.0%)"
        )
        self.assertEqual(result['report_text'], expected_report)

    def test_nan_none_removal(self):
        """Test Case 5: NaN and None values are properly filtered/removed."""
        data = {
            'calculated_bid_price': math.nan,
            'total_cost': None,
            'margin': float('nan'),
            'margin_rate': None
        }

        result = format_export(data)

        self.assertEqual(result['comments'], 0)
        self.assertEqual(result['calculated_bid_price'], 0.0)
        self.assertEqual(result['total_cost'], 0.0)
        self.assertEqual(result['margin'], 0.0)
        self.assertEqual(result['margin_rate'], 0.0)

if __name__ == '__main__':
    unittest.main()
