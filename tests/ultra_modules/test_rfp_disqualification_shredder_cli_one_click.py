import unittest
import json
import os
import io
import sys
import tempfile
import csv
from unittest import mock

from ultra_modules.rfp_disqualification_shredder_cli_one_click import run_cli, get_default_sample_text, main


class TestRfpDisqualificationShredderCliOneClick(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_normal_operation_with_json_output(self):
        """Case 1: Normal operation with JSON output."""
        text = "★实质性要求：提供营业执照。\n▲扣分项：无ISO认证扣2分。"

        result = run_cli(input_text=text, output_format="json")

        data = json.loads(result)
        self.assertIn("substantive_items", data)
        self.assertIn("deduction_items", data)
        self.assertEqual(len(data["substantive_items"]), 1)
        self.assertEqual(len(data["deduction_items"]), 1)
        self.assertIn("★实质性要求：提供营业执照", data["substantive_items"][0])
        self.assertIn("▲扣分项：无ISO认证扣2分", data["deduction_items"][0])

    def test_dirty_data_self_healing(self):
        """Case 2: Dirty data self-healing (dict input, None input)."""
        # Dictionary input
        dict_input = {"key": "★必须提供资质"}
        result1 = run_cli(input_text=dict_input, output_format="json")
        data1 = json.loads(result1)
        self.assertEqual(len(data1["substantive_items"]), 1)
        self.assertIn("★必须提供资质", data1["substantive_items"][0])

        # None input -> absolute fallback
        result2 = run_cli(input_text=None, output_format="json")
        data2 = json.loads(result2)
        # Should fallback to default sample text which has both substantive and deduction
        self.assertTrue(len(data2["substantive_items"]) > 0)
        self.assertTrue(len(data2["deduction_items"]) > 0)

        # Integer input (dirty data)
        result3 = run_cli(input_text=12345, output_format="json")
        data3 = json.loads(result3)
        self.assertEqual(len(data3["substantive_items"]), 0)

    def test_excel_csv_export_fallback_zero_config(self):
        """Case 3: Excel/CSV dual-mode export fallback and zero-config file generation."""
        out_file = os.path.join(self.temp_dir.name, "output.csv")
        text = "★必须满足\n▲酌情加减"

        # Output to file
        result = run_cli(input_text=text, output_format="excel", output_file=out_file)

        # Verify JSON confirmation
        data = json.loads(result)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["file"], out_file)

        # Verify file contents
        self.assertTrue(os.path.exists(out_file))
        with open(out_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            rows = list(reader)
            self.assertEqual(rows[0], ["Type", "Clause"])
            self.assertEqual(rows[1], ["实质性废标项 (★)", "★必须满足"])
            self.assertEqual(rows[2], ["重要扣分项 (▲)", "▲酌情加减"])

    def test_extreme_boundaries(self):
        """Case 4: Extreme boundaries (empty strings, whitespace, gibberish)."""
        # Whitespace
        result_ws = run_cli(input_text="   \n  \t ", output_format="json")
        data_ws = json.loads(result_ws)
        # Empty input string triggers the fallback to default sample
        self.assertTrue(len(data_ws["substantive_items"]) > 0)

        # Gibberish without keywords
        result_gib = run_cli(input_text="asdf qwer zxcv 1234", output_format="json")
        data_gib = json.loads(result_gib)
        self.assertEqual(len(data_gib["substantive_items"]), 0)
        self.assertEqual(len(data_gib["deduction_items"]), 0)

    def test_cli_entry_point_simulation(self):
        """Case 5: CLI entry point full pipeline simulation."""
        # Mock sys.argv
        test_args = ["rfp_disqualification_shredder_cli_one_click.py", "★废标项1\n▲扣分项1", "--format", "json"]

        with mock.patch('sys.argv', test_args):
            # Capture stdout
            captured_out = io.StringIO()
            with mock.patch('sys.stdout', captured_out):
                main()

            output = captured_out.getvalue().strip()
            data = json.loads(output)
            self.assertIn("substantive_items", data)
            self.assertIn("deduction_items", data)
            self.assertEqual(len(data["substantive_items"]), 1)
            self.assertEqual(len(data["deduction_items"]), 1)

if __name__ == '__main__':
    unittest.main()
