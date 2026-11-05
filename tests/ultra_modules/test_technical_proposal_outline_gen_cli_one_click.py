import unittest
import sys
import os
import tempfile
import json
from unittest.mock import patch
from ultra_modules.technical_proposal_outline_gen_cli_one_click import run_cli

class TestTechnicalProposalOutlineGenCliOneClick(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.json_out = os.path.join(self.temp_dir.name, "out.json")
        self.excel_out = os.path.join(self.temp_dir.name, "out.xlsx")
        self.temp_in_file = os.path.join(self.temp_dir.name, "in.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_1_normal_case_valid_data(self):
        """1. 正常用例：有效评分标准JSON字符串与目标页数。"""
        valid_json_input = json.dumps([
            {"name": "Technical Architecture", "weight": 40},
            {"name": "Project Plan", "weight": 30},
            {"name": "Team Experience", "weight": 30}
        ])

        args = [
            "--input", valid_json_input,
            "--pages", "50",
            "--output-json", self.json_out,
            "--output-excel", self.excel_out,
            "--quiet"
        ]

        result = run_cli(args)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["items_generated"], 3)
        self.assertEqual(result["total_pages"], 50)

        # Verify JSON file creation
        self.assertTrue(os.path.exists(self.json_out))
        with open(self.json_out, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(len(data), 3)
            self.assertEqual(data[0]["section_name"], "Technical Architecture")

    def test_2_dirty_data_self_healing(self):
        """2. 脏数据自愈：无效的JSON字符串、负数页数。"""
        invalid_json_input = "this is not json"

        # We pass invalid json and a negative page number.
        # The core engine treats negative total_pages as invalid and falls back to default 100 pages.
        # The CLI handles invalid JSON by falling back to empty list, which core engine turns into default sections.
        args = [
            "--input", invalid_json_input,
            "--pages", "-50",
            "--output-json", self.json_out,
            "--output-excel", self.excel_out,
            "--quiet"
        ]

        result = run_cli(args)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["items_generated"], 5) # Default 5 sections
        self.assertEqual(result["total_pages"], 100) # Fallback pages

        self.assertTrue(os.path.exists(self.json_out))
        with open(self.json_out, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(len(data), 5)
            self.assertEqual(data[0]["section_name"], "1. 项目背景与目标")

    def test_3_missing_parameter_fallback(self):
        """3. 缺参默认兜底：完全不提供参数（一键运行）。"""
        args = [
            "--output-json", self.json_out,
            "--output-excel", self.excel_out,
            "--quiet"
        ]

        result = run_cli(args)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["items_generated"], 5) # Default 5 sections
        self.assertEqual(result["total_pages"], 100) # Default pages

        self.assertTrue(os.path.exists(self.json_out))
        with open(self.json_out, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertTrue(len(data) > 0)

    def test_4_extreme_boundary_conditions(self):
        """4. 极端边界条件：输入大量无效项的字典列表或文件不存在。"""
        # Testing with non-existent file
        args_no_file = [
            "--input", "/tmp/non_existent_file_12345.json",
            "--pages", "0",
            "--output-json", self.json_out,
            "--output-excel", self.excel_out,
            "--quiet"
        ]
        result_no_file = run_cli(args_no_file)
        self.assertEqual(result_no_file["status"], "success")
        self.assertEqual(result_no_file["total_pages"], 100) # page 0 is invalid, falls back to 100

        # Testing with empty valid JSON object without 'criteria' key
        empty_obj = "{}"
        args_empty_obj = [
            "--input", empty_obj,
            "--output-json", self.json_out,
            "--output-excel", self.excel_out,
            "--quiet"
        ]
        result_empty_obj = run_cli(args_empty_obj)
        self.assertEqual(result_empty_obj["status"], "success")
        self.assertEqual(result_empty_obj["items_generated"], 5)

        # Testing with valid JSON list containing invalid items
        invalid_list = json.dumps(["string", 123, {"wrong_key": 50}])
        args_invalid_list = [
            "--input", invalid_list,
            "--pages", "10",
            "--output-json", self.json_out,
            "--output-excel", self.excel_out,
            "--quiet"
        ]
        result_invalid_list = run_cli(args_invalid_list)
        self.assertEqual(result_invalid_list["status"], "success")
        self.assertEqual(result_invalid_list["items_generated"], 1) # one item extracted with 0 weight, defaulted to 100% weight
        self.assertEqual(result_invalid_list["total_pages"], 10)

    def test_5_output_format_generation(self):
        """5. 输出格式：验证JSON和Excel双模无感直出。"""
        valid_json_input = json.dumps([
            {"name": "Architecture", "weight": 50},
            {"name": "Plan", "weight": 50}
        ])

        args = [
            "--input", valid_json_input,
            "--pages", "20",
            "--output-json", self.json_out,
            "--output-excel", self.excel_out,
            "--quiet"
        ]

        result = run_cli(args)
        self.assertEqual(result["status"], "success")

        # Check JSON output
        self.assertTrue(os.path.exists(self.json_out))

        # Check Excel output if pandas is available
        from ultra_modules.technical_proposal_outline_gen_cli_one_click import HAS_PANDAS
        if HAS_PANDAS:
            self.assertTrue(os.path.exists(self.excel_out))
            self.assertTrue(result["excel_output"].startswith("Saved to"))
        else:
            self.assertIn("Skipped", result["excel_output"])

    def test_6_coverage_additional_cases(self):
        """6. Coverage testing: file input, dict json, exceptions."""
        valid_json_dict = json.dumps({
            "criteria": [
                {"name": "Architecture", "weight": 50},
                {"name": "Plan", "weight": 50}
            ]
        })

        # Test dict JSON string without quiet mode to hit print
        args_dict = [
            "--input", valid_json_dict,
            "--output-json", self.json_out,
            "--output-excel", self.excel_out
        ]
        result_dict = run_cli(args_dict)
        self.assertEqual(result_dict["items_generated"], 2)

        # Test dict JSON in file
        with open(self.temp_in_file, "w", encoding="utf-8") as f:
            f.write(valid_json_dict)

        args_file = [
            "--input", self.temp_in_file,
            "--output-json", self.json_out,
            "--output-excel", self.excel_out,
            "--quiet"
        ]
        result_file = run_cli(args_file)
        self.assertEqual(result_file["items_generated"], 2)

        # Test file reading exception handling
        # Since it's hard to force open to fail on an existing file without permissions,
        # we can mock json.load or open

        # Test writing JSON/Excel exceptions
        bad_out_path = "/invalid_dir_that_does_not_exist/out.json"
        bad_excel_path = "/invalid_dir_that_does_not_exist/out.xlsx"

        args_bad_out = [
            "--input", valid_json_dict,
            "--output-json", bad_out_path,
            "--output-excel", bad_excel_path
        ]
        result_bad = run_cli(args_bad_out)
        self.assertEqual(result_bad["status"], "success")

    def test_8_import_error_for_pandas(self):
        """8. Test import error for pandas via reload."""
        import sys
        import importlib

        # We temporarily mock import to fail for pandas
        real_import = __import__

        def mock_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 'pandas':
                raise ImportError("Mocked ImportError for pandas")
            return real_import(name, globals, locals, fromlist, level)

        with patch('builtins.__import__', side_effect=mock_import):
            # Reload module to trigger ImportError handling block (line 9-11)
            import ultra_modules.technical_proposal_outline_gen_cli_one_click as module_to_reload
            importlib.reload(module_to_reload)
            self.assertFalse(module_to_reload.HAS_PANDAS)

            valid_json_input = json.dumps([
                {"name": "Architecture", "weight": 50}
            ])
            args = [
                "--input", valid_json_input,
                "--output-json", self.json_out,
                "--output-excel", self.excel_out,
                "--quiet"
            ]

            result = module_to_reload.run_cli(args)
            self.assertIn("Skipped", result["excel_output"])

        # Restore module state
        import ultra_modules.technical_proposal_outline_gen_cli_one_click as module_to_reload
        importlib.reload(module_to_reload)

    def test_9_coverage_missing_lines(self):
        """9. Hit remaining missing lines: 50, 53, 73-80, 97."""
        # Line 51->60: JSON file read valid dict without criteria
        valid_json_dict_no_criteria = json.dumps({"wrong": 123})
        with open(self.temp_in_file, "w", encoding="utf-8") as f:
            f.write(valid_json_dict_no_criteria)

        args_file_dict_no_crit = [
            "--input", self.temp_in_file,
            "--output-json", self.json_out,
            "--output-excel", self.excel_out,
            "--quiet"
        ]
        run_cli(args_file_dict_no_crit)

        # Line 67->71: quiet true during json write exception
        args_bad_out_quiet = [
            "--input", valid_json_dict_no_criteria,
            "--output-json", "/invalid_dir_that_does_not_exist/out.json",
            "--output-excel", self.excel_out,
            "--quiet"
        ]
        run_cli(args_bad_out_quiet)

        # Line 50: list input via file
        valid_json_list = json.dumps([
            {"name": "Architecture", "weight": 50},
            {"name": "Plan", "weight": 50}
        ])
        with open(self.temp_in_file, "w", encoding="utf-8") as f:
            f.write(valid_json_list)

        args_file_list = [
            "--input", self.temp_in_file,
            "--output-json", self.json_out,
            "--output-excel", self.excel_out,
            "--quiet"
        ]
        result_file_list = run_cli(args_file_list)
        self.assertEqual(result_file_list["items_generated"], 2)

        # Line 53-54: exception while loading JSON from file
        # Mock json.load to raise Exception
        with patch('json.load', side_effect=Exception("Mocked error")):
            result_file_exc = run_cli(args_file_list)
            self.assertEqual(result_file_exc["items_generated"], 5) # Fallback default items

        # Lines 73-80: writing excel with HAS_PANDAS = True, and exception handling
        # Mock HAS_PANDAS to True and mock pandas.DataFrame
        with patch('ultra_modules.technical_proposal_outline_gen_cli_one_click.HAS_PANDAS', True):
            # Test success path for pandas
            import sys
            import types
            mock_pandas = types.ModuleType("pandas")

            class MockDataFrame:
                def __init__(self, data):
                    pass
                def to_excel(self, path, index=False):
                    pass
            mock_pandas.DataFrame = MockDataFrame

            with patch.dict(sys.modules, {'pandas': mock_pandas}):
                import ultra_modules.technical_proposal_outline_gen_cli_one_click
                # We need to set pd on the module directly if it's missing (e.g., failed to import initially)
                ultra_modules.technical_proposal_outline_gen_cli_one_click.pd = mock_pandas

                args_excel_success = [
                    "--input", valid_json_list,
                    "--output-json", self.json_out,
                    "--output-excel", self.excel_out,
                    "--quiet"
                ]
                result_excel_success = run_cli(args_excel_success)
                self.assertIn("Saved to", result_excel_success["excel_output"])

            # Test exception path for pandas
            class MockDataFrameException:
                def __init__(self, data):
                    pass
                def to_excel(self, path, index=False):
                    raise Exception("Excel error")
            mock_pandas.DataFrame = MockDataFrameException

            with patch.dict(sys.modules, {'pandas': mock_pandas}):
                import ultra_modules.technical_proposal_outline_gen_cli_one_click
                ultra_modules.technical_proposal_outline_gen_cli_one_click.pd = mock_pandas

                args_excel_fail = [
                    "--input", valid_json_list,
                    "--output-json", self.json_out,
                    "--output-excel", self.excel_out
                ]

                # patch print to avoid printing expected exception in stdout during test
                with patch('builtins.print'):
                    result_excel_fail = run_cli(args_excel_fail)
                self.assertIn("Failed", result_excel_fail["excel_output"])

                # Test exception with quiet
                args_excel_fail_quiet = [
                    "--input", valid_json_list,
                    "--output-json", self.json_out,
                    "--output-excel", self.excel_out,
                    "--quiet"
                ]
                run_cli(args_excel_fail_quiet)

        # Line 97: Hit __main__ block
        # Since coverage doesn't track subprocesses correctly without sitecustomize setup,
        # we can simulate __main__ execution.
        with patch('ultra_modules.technical_proposal_outline_gen_cli_one_click.__name__', '__main__'):
            with patch('sys.argv', ['ultra_modules/technical_proposal_outline_gen_cli_one_click.py', '--quiet']):
                import importlib
                import ultra_modules.technical_proposal_outline_gen_cli_one_click
                # re-evaluating the file to hit __main__ block in current process
                with open('ultra_modules/technical_proposal_outline_gen_cli_one_click.py', 'r') as f:
                    code = compile(f.read(), 'ultra_modules/technical_proposal_outline_gen_cli_one_click.py', 'exec')
                    exec(code, {'__name__': '__main__'})

    @patch("ultra_modules.technical_proposal_outline_gen_cli_one_click.HAS_PANDAS", False)
    def test_7_no_pandas_fallback(self):
        """7. Test behavior when pandas is not installed."""
        valid_json_input = json.dumps([
            {"name": "Architecture", "weight": 50}
        ])

        args = [
            "--input", valid_json_input,
            "--output-json", self.json_out,
            "--output-excel", self.excel_out,
            "--quiet"
        ]

        result = run_cli(args)
        self.assertIn("Skipped", result["excel_output"])

if __name__ == "__main__":
    unittest.main()
