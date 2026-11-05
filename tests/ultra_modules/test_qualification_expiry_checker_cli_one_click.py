import unittest
import datetime
import os
import json
from unittest.mock import patch
from ultra_modules.qualification_expiry_checker_cli_one_click import QualificationExpiryChecker, main, get_default_data

class TestQualificationExpiryChecker(unittest.TestCase):
    def setUp(self):
        # Fix the current date for testing
        self.check_date = datetime.date(2023, 10, 1)
        self.checker = QualificationExpiryChecker(check_date=self.check_date, warning_days=90)

    def test_normal_behavior(self):
        """Test valid dates and correct expiry calculations."""
        item1 = {"name": "Valid Qual", "expiry_date": "2024-10-01"}  # 366 days away
        item2 = {"name": "Expiring Soon", "expiry_date": "2023-11-01"}  # 31 days away
        item3 = {"name": "Expired Qual", "expiry_date": "2023-09-01"}  # -30 days

        res1 = self.checker.check_item(item1)
        self.assertEqual(res1['status'], 'valid')
        self.assertEqual(res1['days_remaining'], 366)

        res2 = self.checker.check_item(item2)
        self.assertEqual(res2['status'], 'expiring_soon')
        self.assertEqual(res2['days_remaining'], 31)

        res3 = self.checker.check_item(item3)
        self.assertEqual(res3['status'], 'expired')
        self.assertEqual(res3['days_remaining'], -30)

    def test_dirty_data_self_healing(self):
        """Test various date formats and unexpected strings."""
        item1 = {"name": "Qual 1", "expiry_date": "2024.01.01"}
        item2 = {"name": "Qual 2", "expiry_date": "2024年05月15日"}
        item3 = {"name": "Qual 3", "expiry_date": "   2024/02/01   "}
        item4 = {"name": "Invalid", "expiry_date": "Unknown Date"}

        res1 = self.checker.check_item(item1)
        self.assertEqual(res1['parsed_expiry_date'], "2024-01-01")

        res2 = self.checker.check_item(item2)
        self.assertEqual(res2['parsed_expiry_date'], "2024-05-15")

        res3 = self.checker.check_item(item3)
        self.assertEqual(res3['parsed_expiry_date'], "2024-02-01")

        res4 = self.checker.check_item(item4)
        self.assertEqual(res4['status'], 'unknown')
        self.assertIsNone(res4['parsed_expiry_date'])

    def test_extreme_boundaries(self):
        """Test extreme boundaries like 'expires today' or 'long term'."""
        item1 = {"name": "Expires Today", "expiry_date": "2023-10-01"}
        item2 = {"name": "Long Term", "expiry_date": "长期"}
        item3 = {"name": "Permanent", "expiry_date": "永久"}

        res1 = self.checker.check_item(item1)
        self.assertEqual(res1['status'], 'expiring_soon')
        self.assertEqual(res1['days_remaining'], 0)

        res2 = self.checker.check_item(item2)
        self.assertEqual(res2['status'], 'valid')
        self.assertEqual(res2['parsed_expiry_date'], "长期")
        self.assertEqual(res2['days_remaining'], float('inf'))

        res3 = self.checker.check_item(item3)
        self.assertEqual(res3['status'], 'valid')
        self.assertEqual(res3['parsed_expiry_date'], "长期")
        self.assertEqual(res3['days_remaining'], float('inf'))

    def test_missing_parameter_default_fallbacks(self):
        """Test when missing data fields."""
        item1 = {"name": "No Date"}
        item2 = {"expiry_date": "2024-10-01"}

        res1 = self.checker.check_item(item1)
        self.assertEqual(res1['name'], 'No Date')
        self.assertEqual(res1['status'], 'unknown')

        res2 = self.checker.check_item(item2)
        self.assertEqual(res2['name'], 'Unknown Qualification')
        self.assertEqual(res2['status'], 'valid')

    def test_output_adapters(self):
        """Test JSON output generation."""
        data = get_default_data()
        results = self.checker.process_data(data)

        json_path = 'test_output.json'

        # Test JSON
        self.checker.export_json(results, json_path)
        self.assertTrue(os.path.exists(json_path))

        with open(json_path, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)
            self.assertEqual(len(loaded_data), len(data))

        # Clean up
        if os.path.exists(json_path):
            os.remove(json_path)

    @patch('sys.argv', ['qualification_expiry_checker_cli_one_click.py'])
    def test_cli_execution_with_zero_params(self):
        """Test that main() can run without any arguments."""
        try:
            main()
            success = True
        except Exception as e:
            success = False

        self.assertTrue(success)

        if os.path.exists('qualifications.json'):
            os.remove('qualifications.json')
        if os.path.exists('qualifications.xlsx'):
            os.remove('qualifications.xlsx')

if __name__ == '__main__':
    unittest.main()
