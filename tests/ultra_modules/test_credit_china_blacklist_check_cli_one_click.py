import unittest
import os
import shutil
import tempfile
import sys
from unittest.mock import patch
import openpyxl
import json

# Adjust python path if needed to run the module directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from ultra_modules.credit_china_blacklist_check_cli_one_click import BlacklistChecklistGenerator, main

class TestCreditChinaBlacklistCheckCliOneClick(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_1_normal_operation(self):
        """Test Case 1: Normal operation with valid parameters"""
        generator = BlacklistChecklistGenerator(company_name="正常测试企业有限公司", output_dir=self.test_dir)
        results = generator.run_all()

        self.assertEqual(results["company_name"], "正常测试企业有限公司")
        self.assertTrue(os.path.exists(results["json_path"]))
        self.assertTrue(os.path.exists(results["excel_path"]))

        # Verify JSON
        with open(results["json_path"], 'r', encoding='utf-8') as f:
            data = json.load(f)
            self.assertEqual(data["company_name"], "正常测试企业有限公司")
            self.assertIn("未发现", data["conclusion"])

        # Verify Excel
        wb = openpyxl.load_workbook(results["excel_path"])
        ws = wb.active
        self.assertEqual(ws.title, "无重大违法记录声明核验单")
        self.assertEqual(ws.cell(row=1, column=2).value, "正常测试企业有限公司")

    def test_2_dirty_data_self_healing(self):
        """Test Case 2: Dirty data self-healing"""
        # Testing string with emojis, extra spaces, weird symbols
        dirty_name = "  🌟脏数据@企业!!!（北京）有限公司    "
        generator = BlacklistChecklistGenerator(company_name=dirty_name, output_dir=self.test_dir)

        # Assuming regex keeps letters, numbers, chinese characters and brackets
        # So "脏数据企业（北京）有限公司" is expected (note: '（' is kept if in regex)
        # Let's just check that it cleaned it up properly
        self.assertNotIn("🌟", generator.company_name)
        self.assertNotIn("@", generator.company_name)
        self.assertNotIn("!", generator.company_name)
        self.assertEqual(generator.company_name, "脏数据企业（北京）有限公司")

        results = generator.run_all()
        self.assertTrue(os.path.exists(results["json_path"]))
        self.assertTrue(os.path.exists(results["excel_path"]))

    def test_3_missing_parameters_fallback(self):
        """Test Case 3: Missing parameters fallback"""
        generator = BlacklistChecklistGenerator(output_dir=self.test_dir) # no company name

        self.assertEqual(generator.company_name, BlacklistChecklistGenerator.DEFAULT_COMPANY)

        results = generator.run_all()
        self.assertTrue(os.path.exists(results["json_path"]))
        self.assertTrue(os.path.exists(results["excel_path"]))

    def test_4_extreme_boundaries(self):
        """Test Case 4: Extreme boundaries"""
        # Case A: Very long string
        long_name = "测试" * 100
        generator = BlacklistChecklistGenerator(company_name=long_name, output_dir=self.test_dir)
        self.assertEqual(generator.company_name, long_name)

        # Case B: Completely empty string or spaces resulting in empty
        empty_generator = BlacklistChecklistGenerator(company_name="    !@#    ", output_dir=self.test_dir)
        # Assuming our clean logic strips all, it becomes empty, then defaults
        self.assertEqual(empty_generator.company_name, BlacklistChecklistGenerator.DEFAULT_COMPANY)

        # Case C: Bad output dir
        bad_dir_generator = BlacklistChecklistGenerator(company_name="测试企业", output_dir="/root/forbidden_dir/this/should/fail")
        bad_dir_generator.run_all() # Should not raise exception, falls back to "."
        # If it falls back, the file should be in "."
        expected_json = f"blacklist_check_{bad_dir_generator.company_name}.json"
        expected_excel = f"blacklist_check_{bad_dir_generator.company_name}.xlsx"
        self.assertTrue(os.path.exists(expected_json))
        self.assertTrue(os.path.exists(expected_excel))

        # Cleanup fallback files
        if os.path.exists(expected_json):
            os.remove(expected_json)
        if os.path.exists(expected_excel):
            os.remove(expected_excel)

    def test_5_cli_execution_mock(self):
        """Test Case 5: CLI execution mock"""
        test_args = ["credit_china_blacklist_check_cli_one_click.py", "--company", "CLI测试企业", "--outdir", self.test_dir]
        with patch.object(sys, 'argv', test_args):
            main() # Should run without raising exception

        json_path = os.path.join(self.test_dir, "blacklist_check_CLI测试企业.json")
        excel_path = os.path.join(self.test_dir, "blacklist_check_CLI测试企业.xlsx")

        self.assertTrue(os.path.exists(json_path))
        self.assertTrue(os.path.exists(excel_path))

if __name__ == "__main__":
    unittest.main()
