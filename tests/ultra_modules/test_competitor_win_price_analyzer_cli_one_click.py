import unittest
import json
import os
import tempfile
import sys
from io import StringIO
from unittest.mock import patch

from ultra_modules.competitor_win_price_analyzer_cli_one_click import run_one_click, get_default_mock_data, main

class TestCompetitorWinPriceAnalyzerCliOneClick(unittest.TestCase):

    def test_normal_valid_data(self):
        data = [
            {"competitor": "CompA", "region": "North", "budget": 1000.0, "win_price": 900.0},
            {"competitor": "CompA", "region": "North", "budget": 2000.0, "win_price": 1600.0},
        ]
        result_json = run_one_click(raw_data=data, output_format='json')
        result_dict = json.loads(result_json)
        self.assertEqual(result_dict['record_count'], 2)
        # 1st discount: (1000-900)/1000 = 10%
        # 2nd discount: (2000-1600)/2000 = 20%
        # Average: (0.1 + 0.2) / 2 = 0.15
        self.assertAlmostEqual(result_dict['average_discount_rate'], 0.15)
        self.assertEqual(result_dict['distribution']['10-20%'], 1)
        self.assertEqual(result_dict['distribution']['20-30%'], 1)

    def test_dirty_data_self_healing(self):
        data = [
            {"competitor": "CompA", "region": "North", "budget": "invalid", "win_price": 800.0},
            {"competitor": "CompA", "region": "North", "budget": -500, "win_price": 800.0},
            {"competitor": None, "region": None, "budget": 1000.0, "win_price": 900.0},
            "totally invalid data"
        ]
        result_json = run_one_click(raw_data=data, output_format='json')
        result_dict = json.loads(result_json)
        self.assertEqual(result_dict['record_count'], 1) # Only one valid record since budget invalid defaults to 0 and we skip budget <= 0 in core engine
        self.assertEqual(result_dict['average_discount_rate'], 0.1)

    def test_missing_param_default_fallbacks(self):
        # Zero-parameter one click
        result_json = run_one_click()
        result_dict = json.loads(result_json)

        # Expecting the result from get_default_mock_data
        # budgets: 1000, 2000, 1500, "invalid"(0)
        # win_prices: 900, 1600, 1000, 800
        # Valid records: 3
        self.assertEqual(result_dict['record_count'], 3)

        # Test invalid output format fallbacks to json
        result_json_fallback = run_one_click(output_format='invalid_format')
        result_dict_fallback = json.loads(result_json_fallback)
        self.assertEqual(result_dict, result_dict_fallback)

    def test_extreme_boundary_conditions(self):
        # Empty list
        result_empty_json = run_one_click(raw_data=[])
        result_empty = json.loads(result_empty_json)
        self.assertEqual(result_empty['record_count'], 0)
        self.assertEqual(result_empty['average_discount_rate'], 0.0)

        # Extreme numbers
        data = [
            {"competitor": "CompA", "region": "North", "budget": 1e12, "win_price": 1e11},
            {"competitor": "CompA", "region": "North", "budget": 1e-5, "win_price": 0.5e-5},
        ]
        result_json = run_one_click(raw_data=data, output_format='json')
        result_dict = json.loads(result_json)
        self.assertEqual(result_dict['record_count'], 2)
        # 1st discount: (1e12 - 1e11) / 1e12 = 0.9 = 90%
        # 2nd discount: (1e-5 - 0.5e-5) / 1e-5 = 0.5 = 50%
        # Average: (0.9 + 0.5) / 2 = 0.70
        self.assertAlmostEqual(result_dict['average_discount_rate'], 0.70)

    def test_cli_output_modes_and_file_save(self):
        data = [{"competitor": "CompA", "region": "North", "budget": 1000.0, "win_price": 900.0}]

        # Test CSV direct return
        result_csv = run_one_click(raw_data=data, output_format='csv')
        self.assertIn("0.1", result_csv)
        self.assertIn("average_discount_rate", result_csv)

        # Test file output
        with tempfile.TemporaryDirectory() as tmpdirname:
            # json output
            json_file = os.path.join(tmpdirname, "out.json")
            msg = run_one_click(raw_data=data, output_format='json', output_file=json_file)
            self.assertEqual(msg, f"Saved to {json_file}")
            self.assertTrue(os.path.exists(json_file))
            with open(json_file, 'r') as f:
                saved_json = json.load(f)
                self.assertEqual(saved_json['record_count'], 1)

            # csv output
            csv_file = os.path.join(tmpdirname, "out.csv")
            msg_csv = run_one_click(raw_data=data, output_format='csv', output_file=csv_file)
            self.assertEqual(msg_csv, f"Saved to {csv_file}")
            self.assertTrue(os.path.exists(csv_file))
            with open(csv_file, 'r') as f:
                content = f.read()
                self.assertIn("average_discount_rate", content)

    @patch('sys.argv', ['competitor_win_price_analyzer_cli_one_click.py', '--format', 'json'])
    @patch('sys.stdout', new_callable=StringIO)
    def test_main_function(self, mock_stdout):
        main()
        output = mock_stdout.getvalue()
        self.assertIn("record_count", output)

    def test_run_main_block(self):
        import subprocess
        my_env = os.environ.copy()
        my_env["PYTHONPATH"] = "."
        result = subprocess.run([sys.executable, 'ultra_modules/competitor_win_price_analyzer_cli_one_click.py', '--format', 'json'], env=my_env, capture_output=True, text=True)
        self.assertIn("record_count", result.stdout)

if __name__ == '__main__':
    unittest.main()
